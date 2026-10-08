const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const TARGET_URL = "https://quotes.toscrape.com/scroll";
const OUT_DIR = path.join(process.cwd(), "out");
const OUT_FILE = path.join(OUT_DIR, "result.json");
const MAX_SCROLL_ROUNDS = 30;
const SCROLL_PAUSE_MS = 800;

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

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await page.goto(TARGET_URL, { waitUntil: "networkidle" });
    await page.waitForSelector(".quote", { timeout: 15000 });
    await loadAllQuotes(page);

    const quotes = await extractQuotes(page);
    if (!quotes.length) {
      throw new Error("No quotes found — page structure may have changed");
    }

    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(OUT_FILE, JSON.stringify(quotes, null, 2), "utf8");
    console.log(`Saved ${quotes.length} quotes to ${OUT_FILE}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
