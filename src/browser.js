const { chromium } = require("playwright");

async function scrapeQuotes(url) {
  const proxyServer = process.env.SCRAPER_PROXY_SERVER;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    proxy: proxyServer ? { server: proxyServer } : undefined,
    timeout: 30000,
  });
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForSelector(".quote", { timeout: 15000 });
    let previousCount = 0;
    for (let round = 0; round < 30; round += 1) {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(800);
      const count = await page.locator(".quote").count();
      if (count > 0 && count === previousCount) break;
      previousCount = count;
    }
    return await page.$$eval(".quote", (elements) =>
      elements.map((el) => ({
        text: el.querySelector(".text")?.textContent?.trim() ?? "",
        author: el.querySelector(".author")?.textContent?.trim() ?? "",
        tags: Array.from(el.querySelectorAll(".tag"), (tag) =>
          tag.textContent.trim(),
        ),
      })),
    );
  } finally {
    await browser.close();
  }
}

module.exports = { scrapeQuotes };
