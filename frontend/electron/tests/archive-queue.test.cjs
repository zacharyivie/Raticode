const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { createArchiveQueue } = require("../archive-queue.cjs");
const { archiveConversation } = require("../conversation-archive.cjs");

class FakeWorker extends EventEmitter {
  sent = [];
  postMessage(value) { this.sent.push(value); }
  ref() {}
  unref() {}
  terminate() { return Promise.resolve(); }
  complete(result = { saved: true }) { this.emit("message", { id: this.sent.at(-1).id, result }); }
}

test("slow archive work coalesces pending snapshots and preserves deletion acknowledgements", async () => {
  const worker = new FakeWorker();
  const queue = createArchiveQueue({ createWorker: () => worker });
  const first = queue.archive("/archive", { id: "a" }, [{ body: "one" }]);
  const second = queue.archive("/archive", { id: "a" }, [{ body: "two" }]);
  const third = queue.archive("/archive", { id: "a" }, [{ body: "three" }]);
  const other = queue.archive("/archive", { id: "b" }, []);
  let deletionAcknowledged = false;
  const deletion = queue.archive("/archive", { id: "a" }, [], { deleted: true }).then(() => { deletionAcknowledged = true; });
  assert.equal(worker.sent.length, 1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(deletionAcknowledged, false);
  worker.complete();
  await first;
  assert.equal(worker.sent[1].messages[0].body, "three");
  worker.complete();
  await Promise.all([second, third]);
  assert.equal(worker.sent[2].thread.id, "b");
  worker.complete();
  await other;
  assert.equal(worker.sent[3].options.deleted, true);
  assert.equal(deletionAcknowledged, false);
  worker.complete();
  await deletion;
  assert.equal(deletionAcknowledged, true);
  await queue.close();
});

test("worker failures reject active and queued writes and a later request starts a fresh worker", async () => {
  const workers = [];
  const queue = createArchiveQueue({ createWorker: () => { const worker = new FakeWorker(); workers.push(worker); return worker; } });
  const first = queue.archive("/archive", { id: "a" }, []);
  const second = queue.archive("/archive", { id: "b" }, []);
  const rejected = Promise.all([assert.rejects(first, /disk failed/), assert.rejects(second, /disk failed/)]);
  workers[0].emit("error", new Error("disk failed"));
  await rejected;
  const retry = queue.archive("/archive", { id: "a" }, []);
  workers[1].complete();
  await retry;
  await queue.close();
});

test("real worker preserves archive schema and no-op calls do not rewrite snapshots or index", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const queue = createArchiveQueue();
  t.after(() => queue.close());
  const thread = { id: "thread", title: "Thread" };
  const messages = [{ id: "message", body: "Hello" }];
  const result = await queue.archive(root, thread, messages);
  const files = [path.join(root, "threads", `${result.id}.json`), path.join(root, "threads", `${result.id}.jsonl`), path.join(root, "index.json")];
  const before = files.map((file) => fs.statSync(file).mtimeMs);
  await new Promise((resolve) => setTimeout(resolve, 25));
  await queue.archive(root, thread, messages);
  assert.deepEqual(files.map((file) => fs.statSync(file).mtimeMs), before);
  const snapshot = JSON.parse(fs.readFileSync(files[0]));
  assert.equal(snapshot.version, 1);
  assert.deepEqual(snapshot.messages, messages);
  await queue.archive(root, thread, messages, { deleted: true });
  assert.equal(JSON.parse(fs.readFileSync(files[0])).deleted, true);
});

test("attachment cache avoids rereading unchanged bytes and detects changed source", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-attachments-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const dataDir = path.join(root, "data");
  const storage = path.join(dataDir, "chat-attachments");
  const archive = path.join(root, "archive");
  fs.mkdirSync(storage, { recursive: true });
  fs.mkdirSync(archive);
  const source = path.join(storage, "fixture.txt");
  fs.writeFileSync(source, "fixture bytes");
  const thread = { id: "a" };
  const messages = [{ id: "m", attachments: [{ id: "attachment", path: source }] }];
  const saved = archiveConversation(archive, thread, messages, { dataDir });
  const originalRead = fs.readFileSync;
  let descriptorReads = 0;
  fs.readFileSync = (file, ...args) => { if (typeof file === "number") descriptorReads++; return originalRead(file, ...args); };
  try {
    archiveConversation(archive, thread, messages, { dataDir });
    assert.equal(descriptorReads, 0);
    fs.writeFileSync(source, "changed fixture bytes");
    archiveConversation(archive, thread, messages, { dataDir });
    assert.equal(descriptorReads, 1);
  } finally { fs.readFileSync = originalRead; }
  assert.equal(JSON.parse(fs.readFileSync(path.join(archive, "threads", `${saved.id}.json`))).messages.length, 1);
});

test("archive repairs an index left stale by a crash after snapshot replacement", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-recovery-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const thread = { id: "recovery" };
  const messages = [{ id: "message", body: "Durable message" }];
  const result = archiveConversation(root, thread, messages);
  const journal = path.join(root, "threads", `${result.id}.jsonl`);
  const durable = fs.readFileSync(journal, "utf8");
  fs.writeFileSync(path.join(root, "index.json"), JSON.stringify({ version: 1, threads: {} }));
  archiveConversation(root, thread, messages);
  assert.equal(fs.readFileSync(journal, "utf8"), durable);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads[result.id].messageCount, 1);
});

test("a failed index write remains retryable even when its previous index is cached", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-index-failure-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const thread = { id: "retry" };
  const first = [{ id: "message", body: "First" }];
  const next = [...first, { id: "next", body: "Next" }];
  const result = archiveConversation(root, thread, first);
  const originalRename = fs.renameSync;
  fs.renameSync = (source, target) => {
    if (target === path.join(root, "index.json")) throw new Error("Disk unavailable");
    return originalRename(source, target);
  };
  try { assert.throws(() => archiveConversation(root, thread, next), /Disk unavailable/); }
  finally { fs.renameSync = originalRename; }
  archiveConversation(root, thread, next);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads[result.id].messageCount, 2);
});

function archiveWithFixedClock() {
  const vm = require("node:vm");
  const module = { exports: {} };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : ["2026-09-10T12:00:00.000Z"])); }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../conversation-archive.cjs"), "utf8"), {
    module, require, Date: FixedDate,
  });
  return module.exports.archiveConversation;
}

for (const restart of [false, true]) {
  test(`same-millisecond same-count body edits recover a failed index write${restart ? " after worker restart" : " on retry"}`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-index-clock-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    let archive = archiveWithFixedClock();
    const thread = { id: "same-clock", title: "Same timestamp" };
    const result = archive(root, thread, [{ id: "message", body: "originalterm" }]);
    const snapshotPath = path.join(root, "threads", `${result.id}.json`);
    const indexPath = path.join(root, "index.json");
    const journalPath = path.join(root, "threads", `${result.id}.jsonl`);
    for (let iteration = 0; iteration < 5; iteration++) {
      const term = `changedterm${iteration}`;
      const messages = [{ id: "message", body: term }];
      const originalRename = fs.renameSync;
      fs.renameSync = (source, target) => {
        if (target === indexPath) throw new Error("Index disk failure");
        return originalRename(source, target);
      };
      try { assert.throws(() => archive(root, thread, messages), /Index disk failure/); }
      finally { fs.renameSync = originalRename; }
      const failedIndex = JSON.parse(fs.readFileSync(indexPath));
      const committedSnapshot = JSON.parse(fs.readFileSync(snapshotPath));
      assert.equal(failedIndex.threads[result.id].archivedAt, committedSnapshot.updatedAt);
      assert.equal(failedIndex.threads[result.id].messageCount, committedSnapshot.messages.length);
      assert.equal(failedIndex.threads[result.id].terms.includes(term), false);
      const durableJournal = fs.readFileSync(journalPath, "utf8");
      if (restart) archive = archiveWithFixedClock();
      archive(root, thread, messages);
      assert.equal(JSON.parse(fs.readFileSync(indexPath)).threads[result.id].terms.includes(term), true);
      assert.equal(fs.readFileSync(journalPath, "utf8"), durableJournal);
      const indexIdentity = fs.statSync(indexPath).ino;
      archive(root, thread, messages);
      assert.equal(fs.statSync(indexPath).ino, indexIdentity, "a verified no-op must not replace the index");
    }
  });
}
