// Run: node scripts/benchmark-chat-history.mjs
// This benchmark generates fixtures only. Storage latency and slow fsync are
// controlled simulations; archive bytes are written to an actual temp folder.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { performance } from "node:perf_hooks";
import { Worker } from "node:worker_threads";
import { saveConversationMessages, loadConversationMessages } from "../frontend/src/lib/conversationStorage.js";

const require = createRequire(import.meta.url);
const { createArchiveQueue } = require("../frontend/electron/archive-queue.cjs");
const messageCount = Number(process.env.CHAT_BENCH_MESSAGES || 1000);
const bodyBytes = Number(process.env.CHAT_BENCH_BODY_BYTES || 2048);
const updates = Number(process.env.CHAT_BENCH_UPDATES || 40);
const storageMiBPerSecond = 100;
const slowFsyncMs = 5;
const history = Array.from({ length: messageCount }, (_, id) => ({ id: String(id), role: "assistant", body: "x".repeat(bodyBytes) }));
const pauseArray = new Int32Array(new SharedArrayBuffer(4));
const key = "benchmark-chat";
const immediate = () => new Promise((resolve) => setImmediate(resolve));

async function measured(run) {
  let expected = performance.now() + 5;
  let maxDelayMs = 0;
  const timer = setInterval(() => {
    const now = performance.now();
    maxDelayMs = Math.max(maxDelayMs, now - expected);
    expected = now + 5;
  }, 5);
  await new Promise((resolve) => setTimeout(resolve, 10));
  const start = performance.now();
  const result = await run();
  const elapsedMs = performance.now() - start;
  await new Promise((resolve) => setTimeout(resolve, 10));
  clearInterval(timer);
  return { elapsedMs: +elapsedMs.toFixed(2), maxTimerDelayMs: +maxDelayMs.toFixed(2), ...result };
}

function storageFixture() {
  const values = new Map();
  return {
    bytesWritten: 0, writes: 0,
    get length() { return values.size; },
    key(index) { return [...values.keys()][index] ?? null; },
    getItem(item) { return values.get(item) ?? null; },
    setItem(item, value) {
      const bytes = Buffer.byteLength(value);
      this.bytesWritten += bytes;
      this.writes += 1;
      Atomics.wait(pauseArray, 0, 0, bytes / (1024 * 1024 * storageMiBPerSecond) * 1000);
      values.set(item, value);
    },
    removeItem(item) { values.delete(item); },
  };
}

async function storageBenchmark(incremental) {
  const storage = storageFixture();
  const save = incremental ? saveConversationMessages : (target, messages, store) => store.setItem(target, JSON.stringify(messages));
  save(key, history, storage);
  storage.bytesWritten = 0;
  storage.writes = 0;
  return measured(async () => {
    let current = history;
    for (let index = 0; index < updates; index += 1) {
      current = [...current.slice(0, -1), { ...current.at(-1), body: `${history.at(-1).body}\nUpdate ${index}` }];
      save(key, current, storage);
      await immediate();
    }
    assert.deepEqual(loadConversationMessages(key, storage), current);
    return { bytesWritten: storage.bytesWritten, writes: storage.writes };
  });
}

const archiveModule = require.resolve("../frontend/electron/conversation-archive.cjs");
const workerSource = `
const { parentPort, workerData } = require('node:worker_threads');
const fs = require('node:fs');
const originalWriteFile = fs.writeFileSync;
const originalWrite = fs.writeSync;
const originalFsync = fs.fsyncSync;
const pause = new Int32Array(new SharedArrayBuffer(4));
let bytesWritten = 0, writes = 0, jobs = 0, insideWriteFile = false;
fs.writeFileSync = function(file, value, ...args) {
  bytesWritten += Buffer.byteLength(value); writes++;
  insideWriteFile = true;
  try { return originalWriteFile.call(fs, file, value, ...args); }
  finally { insideWriteFile = false; }
};
fs.writeSync = function(...args) {
  const count = originalWrite.apply(fs, args);
  if (!insideWriteFile) { bytesWritten += count; writes++; }
  return count;
};
fs.fsyncSync = function(fd) {
  Atomics.wait(pause, 0, 0, workerData.delay);
  return originalFsync.call(fs, fd);
};
const { archiveConversation } = require(workerData.module);
parentPort.on('message', ({ id, root, thread, messages, options }) => {
  try {
    const result = archiveConversation(root, thread, messages, options);
    jobs++;
    parentPort.postMessage({ id, result: { ...result, benchmark: { bytesWritten, writes, jobs } } });
  } catch (error) { parentPort.postMessage({ id, error: error.message }); }
});
`;
async function archiveBenchmark() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-chat-bench-"));
  const queue = createArchiveQueue({ createWorker: () => new Worker(workerSource, {
    eval: true, workerData: { module: archiveModule, delay: slowFsyncMs },
  }) });
  const thread = { id: "benchmark", title: "Generated benchmark" };
  try {
    const initial = await queue.archive(root, thread, history);
    return await measured(async () => {
      const pending = [];
      let current = history;
      for (let index = 0; index < updates; index += 1) {
        current = [...current.slice(0, -1), { ...current.at(-1), body: `Update ${index}` }];
        pending.push(queue.archive(root, thread, current));
      }
      const final = (await Promise.all(pending)).at(-1);
      const snapshot = JSON.parse(fs.readFileSync(path.join(root, "threads", `${final.id}.json`), "utf8"));
      assert.deepEqual(snapshot.messages, current);
      return Object.fromEntries(Object.entries(final.benchmark).map(([name, value]) => [name, value - initial.benchmark[name]]));
    });
  } finally {
    await queue.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
}

console.log(JSON.stringify({
  environment: { node: process.version, platform: process.platform, arch: process.arch },
  fixture: { messageCount, bodyBytes, updates, storageMiBPerSecond, slowFsyncMs },
  caveat: "Synthetic localStorage throughput and fsync delay; generated history, real temporary archive writes. Timer delays are Node event-loop measurements, not a browser rendering benchmark. Full array joining/write volume is retained to preserve atomicity and quota capacity.",
  fullArrayStorage: await storageBenchmark(false),
  cachedMessageSerialization: await storageBenchmark(true),
  archiveWorkerBurst: await archiveBenchmark(),
}, null, 2));
