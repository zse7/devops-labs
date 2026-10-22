const { chromium } = require("playwright");
const { S3Client, PutObjectCommand } = require("@aws-sdk/client-s3");
const {
  SQSClient,
  ReceiveMessageCommand,
  DeleteMessageCommand,
} = require("@aws-sdk/client-sqs");

const TARGET_URL = "https://quotes.toscrape.com/scroll";
const OBJECT_KEY = "result.json";
const MAX_SCROLL_ROUNDS = 30;
const SCROLL_PAUSE_MS = 800;
const RECEIVE_WAIT_SECONDS = 10;
const VISIBILITY_TIMEOUT_SECONDS = 180;

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Нет переменной окружения ${name}. Адрес облака, ключи, имя шкафа и адрес очереди приходят снаружи.`
    );
  }
  return value;
}

function loadConfig() {
  return {
    endpoint: requireEnv("AWS_ENDPOINT_URL"),
    region: requireEnv("AWS_REGION"),
    accessKeyId: requireEnv("AWS_ACCESS_KEY_ID"),
    secretAccessKey: requireEnv("AWS_SECRET_ACCESS_KEY"),
    bucket: requireEnv("BUCKET_NAME"),
    queueUrl: requireEnv("QUEUE_URL"),
  };
}

function clientOptions(config) {
  return {
    region: config.region,
    endpoint: config.endpoint,
    credentials: {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    },
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadAllQuotes(page) {
  let previousCount = 0;

  for (let round = 0; round < MAX_SCROLL_ROUNDS; round += 1) {
    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
    });
    await sleep(SCROLL_PAUSE_MS);

    const count = await page.locator(".quote").count();
    if (count > 0 && count === previousCount) {
      break;
    }
    previousCount = count;
  }
}

async function extractQuotes(page) {
  return page.$$eval(".quote", (elements) =>
    elements.map((el) => {
      const text = el.querySelector(".text")?.textContent?.trim() ?? "";
      const author = el.querySelector(".author")?.textContent?.trim() ?? "";
      const tags = Array.from(el.querySelectorAll(".tag")).map((tag) =>
        tag.textContent.trim()
      );
      return { text, author, tags };
    })
  );
}

async function scrapeQuotes() {
  const browser = await chromium.launch({
    headless: true,
    // системный Chromium из apt (/usr/bin/chromium), не браузер Playwright
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  const page = await browser.newPage();

  try {
    await page.goto(TARGET_URL, { waitUntil: "networkidle" });
    await page.waitForSelector(".quote", { timeout: 15000 });
    await loadAllQuotes(page);

    const quotes = await extractQuotes(page);
    if (!quotes.length) {
      throw new Error("No quotes found — page structure may have changed");
    }
    return quotes;
  } finally {
    await browser.close();
  }
}

async function saveToBucket(s3, bucket, quotes) {
  await s3.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: OBJECT_KEY,
      Body: JSON.stringify(quotes, null, 2),
      ContentType: "application/json",
    })
  );
}

async function receiveJob(sqs, queueUrl) {
  const response = await sqs.send(
    new ReceiveMessageCommand({
      QueueUrl: queueUrl,
      MaxNumberOfMessages: 1,
      WaitTimeSeconds: RECEIVE_WAIT_SECONDS,
      VisibilityTimeout: VISIBILITY_TIMEOUT_SECONDS,
    })
  );
  return response.Messages?.[0];
}

async function deleteJob(sqs, queueUrl, receiptHandle) {
  await sqs.send(
    new DeleteMessageCommand({
      QueueUrl: queueUrl,
      ReceiptHandle: receiptHandle,
    })
  );
}

async function handleJob(config, s3, sqs, message) {
  const quotes = await scrapeQuotes();
  await saveToBucket(s3, config.bucket, quotes);
  await deleteJob(sqs, config.queueUrl, message.ReceiptHandle);
  console.log(
    `Saved ${quotes.length} quotes to s3://${config.bucket}/${OBJECT_KEY} and deleted the message`
  );
}

async function main() {
  const config = loadConfig();
  const s3 = new S3Client({
    ...clientOptions(config),
    forcePathStyle: true,
    // Floci принимает обычный PutObject. Новый AWS SDK иначе шлёт лишние checksum-заголовки.
    requestChecksumCalculation: "WHEN_REQUIRED",
    responseChecksumValidation: "WHEN_REQUIRED",
  });
  const sqs = new SQSClient(clientOptions(config));

  let stopping = false;
  const requestStop = () => {
    stopping = true;
  };
  process.on("SIGTERM", requestStop);
  process.on("SIGINT", requestStop);

  console.log(`Waiting for a job on ${config.queueUrl}`);

  while (!stopping) {
    const message = await receiveJob(sqs, config.queueUrl);
    if (!message) {
      continue;
    }

    try {
      await handleJob(config, s3, sqs, message);
    } catch (error) {
      console.error("Job failed, message stays in the queue");
      console.error(error);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
