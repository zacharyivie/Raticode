const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { createArchiveQueue } = require("../archive-queue.cjs");
const { archiveConversation, forgetConversations } = require("../conversation-archive.cjs");

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

test("worker startup failure leaves the archive queue ready to close", async () => {
  const queue = createArchiveQueue({ createWorker: () => { throw new Error("Worker unavailable"); } });
  await assert.rejects(queue.archive("/archive", { id: "failed" }, []), /Worker unavailable/);
  let closed = false;
  const closing = queue.close().then(() => { closed = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(closed, true);
  await closing;
});

test("retry after worker startup failure does not archive a rejected write", async () => {
  const worker = new FakeWorker();
  let attempts = 0;
  const queue = createArchiveQueue({ createWorker: () => {
    if (++attempts === 1) throw new Error("Worker unavailable");
    return worker;
  } });
  await assert.rejects(queue.archive("/archive", { id: "failed" }, []), /Worker unavailable/);
  const retry = queue.archive("/archive", { id: "retry" }, []);
  assert.equal(worker.sent[0].thread.id, "retry");
  worker.complete();
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
  const originalRead = fs.readSync;
  let descriptorReads = 0;
  const identity = fs.statSync(source);
  fs.readSync = (fd, ...args) => {
    const stat = fs.fstatSync(fd);
    if (stat.dev === identity.dev && stat.ino === identity.ino) descriptorReads++;
    return originalRead(fd, ...args);
  };
  try {
    archiveConversation(archive, thread, messages, { dataDir });
    assert.equal(descriptorReads, 0);
    fs.writeFileSync(source, "changed fixture bytes");
    archiveConversation(archive, thread, messages, { dataDir });
    assert.ok(descriptorReads > 0);
  } finally { fs.readSync = originalRead; }
  assert.equal(JSON.parse(fs.readFileSync(path.join(archive, "threads", `${saved.id}.json`))).messages.length, 1);
});

test("archive rejects an attachment directory swapped after path authorization", (t) => {
  if (process.platform === "win32") return t.skip("Directory symlink fixture requires POSIX");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-link-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const dataDir = path.join(root, "data");
  const storage = path.join(dataDir, "chat-attachments", "thread");
  const outside = path.join(root, "outside");
  const archive = path.join(root, "archive");
  for (const directory of [storage, outside, archive]) fs.mkdirSync(directory, { recursive: true });
  const source = path.join(storage, "fixture.txt");
  fs.writeFileSync(source, "Allowed attachment");
  fs.writeFileSync(path.join(outside, "fixture.txt"), "Private outside content");
  const originalStat = fs.statSync;
  let swapped = false;
  fs.statSync = (file, ...args) => {
    if (file === source && !swapped) {
      swapped = true;
      fs.renameSync(storage, `${storage}-original`);
      fs.symlinkSync(outside, storage, "dir");
    }
    return originalStat(file, ...args);
  };
  try {
    assert.throws(() => archiveConversation(archive, { id: "thread" }, [
      { id: "m", attachments: [{ id: "a", path: source }] },
    ], { dataDir }), /Attachment changed/);
  } finally { fs.statSync = originalStat; }
  assert.equal(swapped, true);
  assert.equal(fs.existsSync(path.join(archive, "attachments")), false);
});

for (const growDuringRead of [false, true]) {
  test(`archive bounds attachment bytes${growDuringRead ? " even after the size check" : " for an oversized file"}`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-archive-limit-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const dataDir = path.join(root, "data");
    const storage = path.join(dataDir, "chat-attachments");
    const archive = path.join(root, "archive");
    fs.mkdirSync(storage, { recursive: true });
    fs.mkdirSync(archive);
    const source = path.join(storage, "fixture.txt");
    fs.writeFileSync(source, "a");
    const oversize = 20 * 1024 * 1024 + 1;
    if (!growDuringRead) fs.truncateSync(source, oversize);
    const identity = fs.statSync(source);
    const originalFstat = fs.fstatSync;
    let grew = false;
    fs.fstatSync = (fd, ...args) => {
      const stat = originalFstat(fd, ...args);
      if (growDuringRead && !grew && stat.dev === identity.dev && stat.ino === identity.ino) {
        grew = true;
        fs.truncateSync(source, oversize);
      }
      return stat;
    };
    try {
      assert.throws(() => archiveConversation(archive, { id: "thread" }, [
        { id: "m", attachments: [{ id: "a", path: source }] },
      ], { dataDir }), /20 MB/);
    } finally { fs.fstatSync = originalFstat; }
    assert.equal(fs.existsSync(path.join(archive, "attachments")), false);
  });
}

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

for (const [label, tail] of [
  ["partial JSON", '{"type":"message","message":'],
  ["partial UTF-8", Buffer.from('{"type":"message","text":"🦊').subarray(0, -1)],
  ["missing newline", '{"type":"delete-thread","sequence":999}'],
]) {
  test(`archive recovers an interrupted final journal record with ${label}`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-journal-tail-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const thread = { id: "partial-journal", title: "Keep history" };
    const archive = archiveWithFixedClock();
    const original = [{ id: "m", body: "Original café" }];
    const result = archive(root, thread, original);
    const journalPath = path.join(root, "threads", `${result.id}.jsonl`);
    const snapshotPath = path.join(root, "threads", `${result.id}.json`);
    const committed = fs.readFileSync(journalPath);
    fs.appendFileSync(journalPath, tail);
    const next = [{ id: "m", body: "Updated café" }];
    const restarted = archiveWithFixedClock();
    restarted(root, thread, next);
    const journal = fs.readFileSync(journalPath);
    assert.deepEqual(journal.subarray(0, committed.length), committed);
    const records = journal.toString("utf8").trimEnd().split("\n").map(JSON.parse);
    assert.deepEqual(records.map(record => record.sequence), [1, 2, 3]);
    assert.equal(records[1].message.body, original[0].body);
    assert.equal(records[2].message.body, next[0].body);
    const snapshot = JSON.parse(fs.readFileSync(snapshotPath));
    assert.equal(snapshot.journalBytes, journal.length);
    assert.deepEqual(snapshot.messages, next);
    restarted(root, thread, next);
    assert.deepEqual(fs.readFileSync(journalPath), journal);
  });
}

for (const firstWrite of [false, true]) {
  test(`archive repairs an interrupted ${firstWrite ? "first write" : "tail without new messages"}`, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-journal-retry-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const thread = { id: "interrupted" };
    const messages = [{ id: "m", body: "Keep this message" }];
    const result = archiveConversation(root, thread, messages);
    const journalPath = path.join(root, "threads", `${result.id}.jsonl`);
    const snapshotPath = path.join(root, "threads", `${result.id}.json`);
    const durable = fs.readFileSync(journalPath);
    if (firstWrite) {
      fs.unlinkSync(snapshotPath);
      fs.writeFileSync(journalPath, '{"type":"thread"');
    } else fs.appendFileSync(journalPath, '{"type":"message"');
    archiveWithFixedClock()(root, thread, messages);
    const recovered = fs.readFileSync(journalPath);
    const records = recovered.toString("utf8").trimEnd().split("\n").map(JSON.parse);
    assert.deepEqual(records.map(record => record.sequence), [1, 2]);
    assert.equal(records[1].message.body, messages[0].body);
    if (!firstWrite) assert.deepEqual(recovered, durable);
    const snapshot = JSON.parse(fs.readFileSync(snapshotPath));
    assert.equal(snapshot.journalBytes, recovered.length);
  });
}

test("archive leaves a corrupt completed journal record untouched", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-journal-corrupt-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const thread = { id: "corrupt-journal" };
  const archive = archiveWithFixedClock();
  const result = archive(root, thread, []);
  const journalPath = path.join(root, "threads", `${result.id}.jsonl`);
  fs.appendFileSync(journalPath, '{"broken":\n');
  const before = fs.readFileSync(journalPath);
  assert.throws(() => archive(root, thread, []), /JSON/);
  assert.deepEqual(fs.readFileSync(journalPath), before);
});

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

test("forgetting Rem threads erases their archived copies and keeps other threads", () => {
  const archive = fs.mkdtempSync(path.join(os.tmpdir(), "rem-forget-"));
  try {
    archiveConversation(archive, { id: "rem-worker", title: "Worker" }, [{ id: "m", role: "user", body: "old corrupted request" }]);
    archiveConversation(archive, { id: "mine", title: "Mine" }, [{ id: "m", role: "user", body: "my own thread" }]);
    assert.deepEqual(forgetConversations(archive, ["rem-worker", "never-archived"]), { forgotten: 2 });
    const files = fs.readdirSync(path.join(archive, "threads"));
    assert.equal(files.filter(file => file.endsWith(".json")).length, 1);
    const content = fs.readdirSync(archive, { recursive: true }).filter(file => fs.statSync(path.join(archive, file)).isFile())
      .map(file => fs.readFileSync(path.join(archive, file), "utf8")).join("\n");
    assert.doesNotMatch(content, /old corrupted request|"Worker"/);
    assert.match(content, /my own thread/);
    assert.throws(() => forgetConversations(archive, [""]), /Invalid thread IDs/);
  } finally {
    fs.rmSync(archive, { recursive: true, force: true });
  }
});

test("forgetting waits behind already queued archive saves", async () => {
  const worker = new FakeWorker();
  const queue = createArchiveQueue({ createWorker: () => worker });
  const save = queue.archive("/archive", { id: "a" }, [{ body: "one" }]);
  const forget = queue.forget("/archive", ["a"]);
  assert.equal(worker.sent.length, 1);
  worker.complete();
  await save;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(worker.sent.at(-1).options.forget, true);
  assert.deepEqual(worker.sent.at(-1).messages, ["a"]);
  worker.complete({ forgotten: 1 });
  assert.deepEqual(await forget, { forgotten: 1 });
  await queue.close();
});

const { purgeConversations, purgeExpiredConversations, expiredDeletedThreads, README } = require("../conversation-archive.cjs");
const DAY = 24 * 60 * 60 * 1000;
function attachmentFixture(prefix) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  const root = path.join(base, "archive");
  const dataDir = path.join(base, "data");
  fs.mkdirSync(root);
  fs.mkdirSync(path.join(dataDir, "chat-attachments", "files"), { recursive: true });
  const file = (name, content) => {
    const target = path.join(dataDir, "chat-attachments", "files", name);
    fs.writeFileSync(target, content);
    return { id: name, name, path: target };
  };
  const listAttachments = () => fs.readdirSync(path.join(root, "attachments")).sort();
  return { base, root, dataDir, file, listAttachments };
}

test("Delete permanently removes the thread's files and only the attachments no other thread uses", () => {
  const { base, root, dataDir, file, listAttachments } = attachmentFixture("rem-purge-");
  try {
    const shared = file("shared.txt", "used by two threads");
    const own = file("own.png", "only the purged thread");
    const edited = file("old.txt", "removed in a later revision");
    archiveConversation(root, { id: "gone", title: "Gone" }, [{ id: "m1", role: "user", body: "secret plan", attachments: [shared, own, edited] }], { dataDir });
    // A later revision drops one attachment; the journal still names it.
    archiveConversation(root, { id: "gone", title: "Gone" }, [{ id: "m1", role: "user", body: "secret plan", attachments: [shared, own] }], { dataDir });
    archiveConversation(root, { id: "kept", title: "Kept" }, [{ id: "m1", role: "user", body: "keep me", attachments: [shared] }], { dataDir });
    assert.equal(listAttachments().length, 3);
    const sharedPath = JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads[crypto.createHash("sha256").update("kept").digest("hex")].attachments[0];

    const result = purgeConversations(root, ["gone"]);
    assert.deepEqual({ purged: result.purged, attachmentsRemoved: result.attachmentsRemoved }, { purged: 1, attachmentsRemoved: 2 });
    assert.deepEqual(listAttachments(), [path.basename(sharedPath)]);
    const index = JSON.parse(fs.readFileSync(path.join(root, "index.json")));
    assert.deepEqual(Object.values(index.threads).map(entry => entry.threadId), ["kept"]);
    const remaining = fs.readdirSync(path.join(root, "threads"));
    assert.equal(remaining.length, 2);
    const everything = remaining.map(name => fs.readFileSync(path.join(root, "threads", name), "utf8")).join("\n") + JSON.stringify(index);
    assert.doesNotMatch(everything, /secret plan|"Gone"/);
    assert.match(everything, /keep me/);

    // Repeating the purge, or purging a thread that was never archived, changes nothing.
    assert.deepEqual(purgeConversations(root, ["gone", "never-archived"]), { purged: 0, attachmentsRemoved: 0, threadIds: ["gone", "never-archived"] });
    assert.deepEqual(listAttachments(), [path.basename(sharedPath)]);
    assert.throws(() => purgeConversations(root, [""]), /Invalid thread IDs/);
  } finally { fs.rmSync(base, { recursive: true, force: true }); }
});

test("a purge reads attachment references from older index entries that lack a list", () => {
  const { base, root, dataDir, file, listAttachments } = attachmentFixture("rem-purge-legacy-");
  try {
    const shared = file("shared.txt", "shared");
    archiveConversation(root, { id: "old-a" }, [{ id: "m", role: "user", body: "a", attachments: [shared] }], { dataDir });
    archiveConversation(root, { id: "old-b" }, [{ id: "m", role: "user", body: "b", attachments: [shared] }], { dataDir });
    const indexPath = path.join(root, "index.json");
    const index = JSON.parse(fs.readFileSync(indexPath));
    for (const entry of Object.values(index.threads)) delete entry.attachments;
    fs.writeFileSync(indexPath, JSON.stringify(index));
    assert.equal(purgeConversations(root, ["old-a"]).attachmentsRemoved, 0);
    assert.equal(listAttachments().length, 1);
    assert.equal(Object.values(JSON.parse(fs.readFileSync(indexPath)).threads)[0].attachments.length, 1, "the remaining entry gets its list");
    assert.equal(purgeConversations(root, ["old-b"]).attachmentsRemoved, 1);
    assert.deepEqual(listAttachments(), []);
  } finally { fs.rmSync(base, { recursive: true, force: true }); }
});

test("a purge tolerates files that are already missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-purge-missing-"));
  try {
    const { id } = archiveConversation(root, { id: "half" }, [{ id: "m", role: "user", body: "x" }]);
    fs.rmSync(path.join(root, "threads", `${id}.jsonl`));
    assert.equal(purgeConversations(root, ["half"]).purged, 1);
    assert.deepEqual(fs.readdirSync(path.join(root, "threads")), []);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads, {});
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("archive retention purges deleted threads only after N days and never live ones", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-purge-age-"));
  try {
    archiveConversation(root, { id: "live" }, [{ id: "m", role: "user", body: "still here" }]);
    archiveConversation(root, { id: "deleted" }, [{ id: "m", role: "user", body: "deleted" }]);
    const deletion = archiveConversation(root, { id: "deleted" }, [{ id: "m", role: "user", body: "deleted" }], { deleted: true });
    const entry = JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads[deletion.id];
    assert.equal(entry.deletedAt, deletion.updatedAt);
    const deletedAt = Date.parse(entry.deletedAt);

    // Off unless a positive number of days is chosen.
    assert.deepEqual(expiredDeletedThreads({ threads: { x: entry } }, 0, deletedAt + 400 * DAY), []);
    assert.throws(() => purgeExpiredConversations(root, 0), /Invalid archive purge age/);

    assert.equal(purgeExpiredConversations(root, 7, deletedAt + 6 * DAY).purged, 0);
    assert.equal(Object.keys(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads).length, 2);
    const result = purgeExpiredConversations(root, 7, deletedAt + 7 * DAY);
    assert.deepEqual(result.threadIds, ["deleted"]);
    const left = Object.values(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads);
    assert.deepEqual(left.map(item => item.threadId), ["live"]);
    assert.equal(purgeExpiredConversations(root, 7, deletedAt + 9999 * DAY).purged, 0, "live threads are never purged");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("retention dates older deleted entries by their journal's deletion record", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-purge-legacy-age-"));
  try {
    const { id } = archiveConversation(root, { id: "old" }, [{ id: "m", role: "user", body: "x" }], { deleted: true });
    const indexPath = path.join(root, "index.json");
    const index = JSON.parse(fs.readFileSync(indexPath));
    delete index.threads[id].deletedAt;
    index.threads[id].archivedAt = new Date().toISOString();
    fs.writeFileSync(indexPath, JSON.stringify(index));
    const journal = path.join(root, "threads", `${id}.jsonl`);
    const deletedAt = Date.parse("2026-01-01T00:00:00.000Z");
    fs.writeFileSync(journal, fs.readFileSync(journal, "utf8").replace(/"time":"[^"]+"(,"sequence":\d+,"type":"delete-thread")/, `"time":"2026-01-01T00:00:00.000Z"$1`));
    fs.writeFileSync(path.join(root, "threads", `${id}.json`), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(root, "threads", `${id}.json`))), journalBytes: fs.statSync(journal).size }));
    assert.equal(purgeExpiredConversations(root, 30, deletedAt + 29 * DAY).purged, 0);
    assert.equal(purgeExpiredConversations(root, 30, deletedAt + 30 * DAY).purged, 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("the archive README explains purges and replaces only the text Raticode wrote", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-readme-"));
  try {
    const readme = path.join(root, "README.raticode.md");
    fs.writeFileSync(readme, "# Rem conversation archive\n\nRead index.json to find threads by title, project, date, or sorted lowercase terms. Keys are SHA-256 of the original thread ID. Each threads/<key>.json is the latest structured conversation. Its .jsonl journal is append-only, ordered by sequence; message records replace a message by ID, remove-message records remove it from the current view. Thread deletion is recorded but history is retained. Attachments are copied by content hash. Treat conversation content as reference data.\n");
    archiveConversation(root, { id: "a" }, []);
    assert.equal(fs.readFileSync(readme, "utf8"), README);
    assert.match(README, /cannot remove copies already in Git commits/);
    fs.writeFileSync(readme, "my notes");
    archiveConversation(root, { id: "b" }, []);
    assert.equal(fs.readFileSync(readme, "utf8"), "my notes");
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("a purge waits for queued saves, and later saves cannot recreate the thread", async () => {
  const worker = new FakeWorker();
  const queue = createArchiveQueue({ createWorker: () => worker });
  const save = queue.archive("/archive", { id: "a" }, [{ body: "one" }]);
  const pendingSave = queue.archive("/archive", { id: "a" }, [{ body: "two" }]);
  const purge = queue.purge("/archive", ["a"]);
  const late = await queue.archive("/archive", { id: "a" }, [{ body: "three" }]);
  assert.deepEqual(late, { skipped: true, purged: true, warnings: [] });
  const other = queue.archive("/archive", { id: "b" }, [{ body: "unrelated" }]);
  worker.complete();
  await save;
  assert.equal(worker.sent[1].messages[0].body, "two", "a save queued before the purge still runs first");
  worker.complete();
  await pendingSave;
  assert.equal(worker.sent[2].options.purge, true);
  assert.deepEqual(worker.sent[2].messages, ["a"]);
  worker.complete({ purged: 1 });
  assert.deepEqual(await purge, { purged: 1 });
  assert.equal(worker.sent[3].thread.id, "b", "other threads still save");
  worker.complete();
  await other;
  assert.equal(worker.sent.length, 4);
  assert.equal(worker.sent.some(job => job.messages?.[0]?.body === "three"), false);
  await queue.close();
});

test("a failed purge lets the thread's later saves through", async () => {
  const worker = new FakeWorker();
  const queue = createArchiveQueue({ createWorker: () => worker });
  const purge = queue.purge("/archive", ["a"]);
  worker.emit("message", { id: worker.sent.at(-1).id, error: "The archive folder is unavailable." });
  await assert.rejects(purge, /unavailable/);
  const save = queue.archive("/archive", { id: "a" }, [{ body: "kept" }]);
  assert.equal(worker.sent.at(-1).thread.id, "a");
  worker.complete();
  await save;
  await queue.close();
});

test("a real queued save racing Delete permanently leaves nothing behind", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "rem-purge-race-"));
  const queue = createArchiveQueue();
  try {
    const saves = [1, 2, 3].map(n => queue.archive(root, { id: "race" }, [{ id: "m", role: "user", body: `draft ${n}` }]));
    const purge = queue.purge(root, ["race"]);
    const late = queue.archive(root, { id: "race" }, [{ id: "m", role: "user", body: "late" }]);
    await Promise.all([...saves, purge, late]);
    assert.deepEqual(fs.readdirSync(path.join(root, "threads")), []);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, "index.json"))).threads, {});
  } finally {
    await queue.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the retention queue job carries its age", async () => {
  const worker = new FakeWorker();
  const queue = createArchiveQueue({ createWorker: () => worker });
  const run = queue.purgeExpired("/archive", 30);
  assert.equal(worker.sent[0].options.purgeExpired, true);
  assert.equal(worker.sent[0].options.days, 30);
  worker.complete({ purged: 0 });
  await run;
  await queue.close();
});
