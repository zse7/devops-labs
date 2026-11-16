const fs = require("node:fs/promises");
const path = require("node:path");

function validateJob(message) {
  const { job_id: jobId, url } = message;
  if (
    typeof jobId !== "string" ||
    !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(jobId)
  ) {
    throw new Error("Invalid job_id");
  }
  const target = new URL(url);
  if (!["http:", "https:"].includes(target.protocol)) {
    throw new Error("Job URL must use HTTP or HTTPS");
  }
  return { jobId, url: target.href };
}

function validateQuotes(quotes) {
  if (!Array.isArray(quotes) || quotes.length === 0) {
    throw new Error("Expected a nonempty array of quotes");
  }
  if (
    quotes.some(
      (q) =>
        !q ||
        typeof q.text !== "string" ||
        !q.text.trim() ||
        typeof q.author !== "string" ||
        !q.author.trim() ||
        !Array.isArray(q.tags) ||
        q.tags.some((tag) => typeof tag !== "string"),
    )
  ) {
    throw new Error("Invalid quote: expected text, author and string tags");
  }
  return quotes;
}

async function readResult(directory, jobId) {
  let content;
  try {
    content = await fs.readFile(path.join(directory, `${jobId}.json`), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  // Повреждённый существующий результат нельзя принять за выполненную работу.
  return validateQuotes(JSON.parse(content));
}

async function saveResult(directory, jobId, quotes) {
  validateQuotes(quotes);
  const temporary = path.join(directory, `.${jobId}.tmp`);
  const file = await fs.open(temporary, "wx", 0o644);
  try {
    await file.writeFile(JSON.stringify(quotes, null, 2) + "\n");
    await file.sync();
  } finally {
    await file.close();
  }
  // В лабораторной один последовательный worker, временный файл на том же диске.
  await fs.rename(temporary, path.join(directory, `${jobId}.json`));
  const parent = await fs.open(directory, "r");
  try {
    await parent.sync();
  } finally {
    await parent.close();
  }
}

async function clearTemporary(directory, jobId) {
  await fs.rm(path.join(directory, `.${jobId}.tmp`), { force: true });
}

module.exports = {
  validateJob,
  validateQuotes,
  readResult,
  saveResult,
  clearTemporary,
};
