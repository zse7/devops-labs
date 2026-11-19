const fs = require("node:fs/promises");
const os = require("node:os");
const { randomUUID } = require("node:crypto");
const { createClient } = require("redis");
const { scrapeQuotes } = require("./browser");
const {
  validateJob,
  readResult,
  saveResult,
  clearTemporary,
} = require("./results");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function loadConfig() {
  const config = {
    redisUrl: process.env.REDIS_URL || "redis://redis:6379",
    resultsDir: process.env.RESULTS_DIR || "/results",
    stream: "jobs",
    group: "scrapers",
    consumer: `${os.hostname()}-${randomUUID()}`,
    claimIdleMs: Number(process.env.CLAIM_IDLE_MS || 15000),
    heartbeatMs: Number(process.env.HEARTBEAT_MS || 3000),
  };
  for (const key of ["claimIdleMs", "heartbeatMs"]) {
    if (!Number.isSafeInteger(config[key]) || config[key] <= 0) {
      throw new Error(`${key} must be a positive integer`);
    }
  }
  if (config.claimIdleMs < config.heartbeatMs * 3) {
    throw new Error("CLAIM_IDLE_MS must be at least three heartbeat intervals");
  }
  return config;
}

function fieldsToObject(fields) {
  const entries = [];
  for (let i = 0; i < fields.length; i += 2)
    entries.push([fields[i], fields[i + 1]]);
  return Object.fromEntries(entries);
}

async function faultCheckpoint(client, jobId, phase) {
  if (process.env.ENABLE_FAULT_TESTS !== "1") return;
  // Одноразовый маркер нужен для воспроизводимого теста падения.
  const shouldCrash = await client.sendCommand([
    "EVAL",
    "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",
    "1",
    `lab:fault:${jobId}`,
    phase,
  ]);
  if (shouldCrash === 1) {
    console.log(`FAULT ${phase} ${jobId}`);
    process.kill(process.pid, "SIGKILL");
  }
}

async function handleJob(client, config, entry, scrape, checkpoint) {
  const [messageId, fields] = entry;
  const { jobId, url } = validateJob(fieldsToObject(fields));
  console.log(`RECEIVED ${jobId} ${messageId}`);
  let heartbeatError;
  let heartbeatRunning = false;
  const heartbeat = setInterval(async () => {
    if (heartbeatRunning) return;
    heartbeatRunning = true;
    try {
      await client.sendCommand([
        "XCLAIM",
        config.stream,
        config.group,
        config.consumer,
        "0",
        messageId,
        "JUSTID",
      ]);
    } catch (error) {
      heartbeatError = error;
    } finally {
      heartbeatRunning = false;
    }
  }, config.heartbeatMs);
  try {
    await checkpoint(client, jobId, "after-receive");
    await clearTemporary(config.resultsDir, jobId);
    const existing = await readResult(config.resultsDir, jobId);
    if (!existing) {
      const quotes = await scrape(url);
      if (heartbeatError) throw heartbeatError;
      await saveResult(config.resultsDir, jobId, quotes);
      console.log(`SAVED ${jobId} ${quotes.length}`);
    } else {
      console.log(`REUSED ${jobId} ${existing.length}`);
    }
    await checkpoint(client, jobId, "after-save");
    if (heartbeatError) throw heartbeatError;
    // воркер подтверждает задание что запись обработана и рез сохранен
    await client.sendCommand(["XACK", config.stream, config.group, messageId]);
    console.log(`ACKED ${jobId} ${messageId}`);
  } finally {
    clearInterval(heartbeat);
    await clearTemporary(config.resultsDir, jobId);
  }
}

async function runWorker({
  config = loadConfig(),
  scrape = scrapeQuotes,
  checkpoint = faultCheckpoint,
} = {}) {
  await fs.mkdir(config.resultsDir, { recursive: true });
  await fs.rm("/tmp/quotes-scraper.ready", { force: true });
  const client = createClient({
    url: config.redisUrl,
    disableOfflineQueue: true,
    socket: {
      reconnectStrategy: (retries) =>
        retries < 10
          ? Math.min(100 * 2 ** retries, 2000)
          : new Error("Redis unavailable"),
    },
  });
  const reader = client.duplicate();
  client.on("error", (error) => console.error("Redis:", error.message));
  reader.on("error", (error) => console.error("Redis reader:", error.message));
  let stopping = false;
  const requestStop = () => {
    stopping = true;
  };
  process.on("SIGTERM", requestStop);
  process.on("SIGINT", requestStop);
  try {
    await client.connect();
    await reader.connect();
    try {
      await client.sendCommand([
        "XGROUP",
        "CREATE",
        config.stream,
        config.group,
        "0",
        "MKSTREAM",
      ]);
    } catch (error) {
      if (!error.message.startsWith("BUSYGROUP")) throw error;
    }
    await fs.writeFile("/tmp/quotes-scraper.ready", config.consumer);
    console.log(`READY ${config.consumer}`);
    let cursor = "0-0";
    // воркер ждет задания
    while (!stopping) {
      try {
        const claimed = await reader.sendCommand([
          "XAUTOCLAIM",
          config.stream,
          config.group,
          config.consumer,
          String(config.claimIdleMs),
          cursor,
          "COUNT",
          "1",
        ]);
        cursor = claimed[0];
        let entry = claimed[1][0];
        if (!entry) {
          const streams = await reader.sendCommand([
            "XREADGROUP",
            "GROUP",
            config.group,
            config.consumer,
            "COUNT",
            "1",
            "BLOCK",
            "1000",
            "STREAMS",
            config.stream,
            ">",
          ]);
          entry = streams?.[0]?.[1]?.[0];
        }
        // получил запись - вызови обработку задания
        if (entry) await handleJob(client, config, entry, scrape, checkpoint);
      } catch (error) {
        if (!reader.isOpen || !client.isOpen) throw error;
        console.error("Job not acknowledged:", error.message);
        await sleep(1000);
      }
    }
  } finally {
    process.off("SIGTERM", requestStop);
    process.off("SIGINT", requestStop);
    if (reader.isOpen) reader.destroy();
    if (client.isOpen) client.destroy();
    await fs.rm("/tmp/quotes-scraper.ready", { force: true });
  }
}

if (require.main === module) {
  runWorker().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = { loadConfig, handleJob, runWorker };
