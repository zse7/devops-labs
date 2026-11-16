const fs = require("node:fs/promises");
const { createClient } = require("redis");

async function main() {
  await fs.access("/tmp/quotes-scraper.ready");
  const client = createClient({
    url: process.env.REDIS_URL || "redis://redis:6379",
    socket: { connectTimeout: 2000, reconnectStrategy: false },
  });
  client.on("error", () => {});
  try {
    await client.connect();
    if ((await client.ping()) !== "PONG") throw new Error("Redis not ready");
  } finally {
    if (client.isOpen) client.destroy();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
