const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { createClient } = require("redis");
const { validateJob, validateQuotes } = require("../src/results");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, message) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await sleep(50);
  }
  throw new Error(`Timed out: ${message}`);
}

test("reject unsafe job paths and invalid results", () => {
  for (const jobId of ["../escape", "/tmp/escape", "", "a/b"]) {
    assert.throws(() =>
      validateJob({ job_id: jobId, url: "https://example.com" }),
    );
  }
  assert.throws(() =>
    validateJob({ job_id: "abc", url: "file:///etc/passwd" }),
  );
  assert.throws(() => validateQuotes([]));
  assert.throws(() => validateQuotes([{ text: "x", author: "", tags: [] }]));
});

test(
  "Redis delivery, crashes and duplicate jobs",
  { timeout: 60000 },
  async (t) => {
    const directory = await fs.mkdtemp(
      path.join(os.tmpdir(), "quotes-worker-test-"),
    );
    const client = createClient({ url: process.env.REDIS_URL });
    client.on("error", (error) => console.error("Test Redis:", error.message));
    await client.connect();
    const children = new Set();
    t.after(async () => {
      for (const child of children) {
        if (child.exitCode === null && child.signalCode === null) {
          const exited = once(child, "exit");
          child.kill("SIGKILL");
          await exited;
        }
      }
      client.destroy();
      await fs.rm(directory, { recursive: true, force: true });
    });

    async function start() {
      const child = spawn(
        process.execPath,
        [path.join(__dirname, "fixture-worker.js")],
        {
          env: {
            ...process.env,
            RESULTS_DIR: directory,
            CLAIM_IDLE_MS: "600",
            HEARTBEAT_MS: "100",
            ENABLE_FAULT_TESTS: "1",
          },
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      children.add(child);
      let output = "";
      child.stdout.on("data", (data) => {
        output += data;
      });
      child.stderr.on("data", (data) => {
        output += data;
      });
      await waitFor(() => {
        if (child.exitCode !== null || child.signalCode !== null) {
          throw new Error(`Worker exited before READY: ${output}`);
        }
        return output.includes("READY ");
      }, "worker ready");
      return { child, output: () => output };
    }
    async function stop(worker) {
      const exited = once(worker.child, "exit");
      worker.child.kill("SIGTERM");
      const deadline = setTimeout(() => worker.child.kill("SIGKILL"), 4000);
      try {
        await exited;
      } finally {
        clearTimeout(deadline);
      }
    }
    async function enqueue(jobId, url = "https://example.com/quotes") {
      return client.sendCommand([
        "XADD",
        "jobs",
        "*",
        "job_id",
        jobId,
        "url",
        url,
      ]);
    }
    async function pendingCount() {
      return (await client.sendCommand(["XPENDING", "jobs", "scrapers"]))[0];
    }
    async function waitForAck(worker, jobId) {
      await waitFor(
        () => worker.output().includes(`ACKED ${jobId} `),
        `ack ${jobId}`,
      );
      await waitFor(async () => (await pendingCount()) === 0, "empty PEL");
    }
    async function fingerprint(jobId) {
      const filename = path.join(directory, `${jobId}.json`);
      return {
        content: await fs.readFile(filename, "utf8"),
        mtime: (await fs.stat(filename)).mtimeMs,
      };
    }

    let worker = await start();
    await t.test(
      "normal delivery and same job_id under a new Redis ID",
      async () => {
        await enqueue("normal");
        await waitForAck(worker, "normal");
        const before = await fingerprint("normal");
        validateQuotes(JSON.parse(before.content));
        await enqueue("normal");
        await waitFor(
          () => worker.output().includes("REUSED normal "),
          "duplicate reused",
        );
        await waitFor(
          async () => (await pendingCount()) === 0,
          "duplicate acknowledged",
        );
        assert.deepEqual(await fingerprint("normal"), before);
        assert.equal(
          (await fs.readdir(directory)).filter((name) => name === "normal.json")
            .length,
          1,
        );
        await stop(worker);
      },
    );

    for (const phase of ["after-receive", "after-save"]) {
      await t.test(
        `SIGKILL ${phase}, pending recovery by a new consumer`,
        async () => {
          worker = await start();
          const jobId = `crash-${phase}`;
          await client.sendCommand(["SET", `lab:fault:${jobId}`, phase]);
          const exited = once(worker.child, "exit");
          await enqueue(jobId);
          const [, signal] = await exited;
          assert.equal(signal, "SIGKILL");
          assert.equal(await pendingCount(), 1);
          let before;
          if (phase === "after-save") before = await fingerprint(jobId);
          else
            await assert.rejects(
              fs.access(path.join(directory, `${jobId}.json`)),
            );
          // Имитируем файл, оставшийся при прерывании записи до rename.
          await fs.writeFile(
            path.join(directory, `.${jobId}.tmp`),
            '{"partial":',
          );
          worker = await start();
          await waitForAck(worker, jobId);
          const result = await fingerprint(jobId);
          validateQuotes(JSON.parse(result.content));
          if (before) assert.deepEqual(result, before);
          await assert.rejects(
            fs.access(path.join(directory, `.${jobId}.tmp`)),
          );
          await stop(worker);
        },
      );
    }

    await t.test("an empty scrape is not acknowledged", async () => {
      worker = await start();
      await enqueue("empty", "https://example.com/empty");
      await waitFor(
        () => worker.output().includes("Job not acknowledged:"),
        "empty result rejected",
      );
      assert.equal(await pendingCount(), 1);
      await assert.rejects(fs.access(path.join(directory, "empty.json")));
      await stop(worker);
    });
  },
);
