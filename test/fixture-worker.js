const { runWorker } = require("../src/scrape");

// Тестовая замена браузера; очередь, файлы и подтверждения — настоящий код.
runWorker({
  scrape: async (url) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    if (url.endsWith("/empty")) return [];
    return [
      { text: `Quote from ${url}`, author: "Test Author", tags: ["test"] },
    ];
  },
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
