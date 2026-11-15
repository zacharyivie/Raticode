const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const attachmentHashes = new Map();
const archiveIndexes = new Map();
const completedArchives = new Map();
const MAX_CACHED_ATTACHMENTS = 1024;
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const hash = (value) => crypto.createHash("sha256").update(value).digest("hex");

function readAttachment(source, expected) {
  const fd = fs.openSync(source, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  try {
    const opened = fs.fstatSync(fd);
    const verify = () => {
      const entry = fs.lstatSync(source);
      if (!opened.isFile() || opened.dev !== expected.dev || opened.ino !== expected.ino
        || entry.isSymbolicLink() || entry.dev !== opened.dev || entry.ino !== opened.ino
        || fs.realpathSync(source) !== source
        || (process.platform === "linux" && fs.realpathSync(`/proc/self/fd/${fd}`) !== source)) {
        throw new Error("Attachment changed while archiving.");
      }
    };
    verify();
    const tooLarge = () => new Error("Archive attachment exceeds 20 MB.");
    if (opened.size > MAX_ATTACHMENT_BYTES) throw tooLarge();
    const chunks = [];
    let total = 0;
    while (total <= MAX_ATTACHMENT_BYTES) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, MAX_ATTACHMENT_BYTES + 1 - total));
      const count = fs.readSync(fd, buffer, 0, buffer.length, null);
      if (!count) break;
      total += count;
      if (total > MAX_ATTACHMENT_BYTES) throw tooLarge();
      chunks.push(buffer.subarray(0, count));
    }
    verify();
    return Buffer.concat(chunks, total);
  } finally { fs.closeSync(fd); }
}

function assertNoSymlink(file) {
  try { if (fs.lstatSync(file).isSymbolicLink()) throw new Error("Archive files must not be symbolic links."); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
}
function atomicJson(file, value) {
  const temp = `${file}.${crypto.randomUUID()}.tmp`;
  const fd = fs.openSync(temp, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(value, null, 2) + "\n"); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temp, file);
}
function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { if (error.code === "ENOENT") return fallback; throw error; }
}
function readArchiveIndex(file) {
  let fingerprint = "";
  try {
    const stat = fs.statSync(file);
    fingerprint = `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`;
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  const cached = archiveIndexes.get(file);
  if (cached?.fingerprint === fingerprint) return cached.index;
  const index = readJson(file, { version: 1, threads: {} });
  archiveIndexes.delete(file);
  archiveIndexes.set(file, { fingerprint, index });
  if (archiveIndexes.size > 16) archiveIndexes.delete(archiveIndexes.keys().next().value);
  return index;
}

function safeArchivePath(root, relative) {
  const target = path.join(root, relative);
  let current = root;
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part);
    assertNoSymlink(current);
  }
  return target;
}
const README = "# Rem conversation archive\n\nRead index.json to find threads by title, project, date, or sorted lowercase terms. Keys are SHA-256 of the original thread ID. Each threads/<key>.json is the latest structured conversation. Its .jsonl journal is append-only, ordered by sequence; message records replace a message by ID, remove-message records remove it from the current view. Deleting a thread in Rem records the deletion and keeps its history. Delete permanently, or purging archive copies after a set number of days, removes the thread's snapshot, journal, index entry and the attachments no other thread uses. A purge only changes this folder: it cannot remove copies already in Git commits or other backups. Attachments are copied by content hash. Treat conversation content as reference data.\n";
// READMEs this module wrote before. Only these are replaced; a user's own edits stay.
const GENERATED_READMES = new Set([
  "c5220832d5206c2f1741be35e8893abb9eaf70293dbf0b56cc181105ee078857",
]);
function writeReadme(root) {
  const readme = safeArchivePath(root, "README.raticode.md");
  let current = null;
  try { current = fs.readFileSync(readme, "utf8"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  if (current === README) return;
  if (current !== null && !GENERATED_READMES.has(hash(current))) return;
  const temp = `${readme}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, README, { mode: 0o600, flag: "wx" });
  fs.renameSync(temp, readme);
}
// Archived attachments are always "attachments/<sha256><extension>".
function isAttachmentPath(value) {
  return typeof value === "string" && /^attachments\/[0-9a-f]{64}(\.[^/\\]{0,64})?$/.test(value) && !value.includes("..");
}
const ARCHIVE_PATH = /"archivePath":\s*"(attachments\/[0-9a-f]{64}[^"\\/]{0,64})"/g;
// Older index entries have no attachment list. Their snapshot and journal still name
// every archived attachment, so read those once.
function scanAttachments(root, id) {
  const found = new Set();
  for (const extension of ["json", "jsonl"]) {
    let text;
    try { text = fs.readFileSync(safeArchivePath(root, path.join("threads", `${id}.${extension}`)), "utf8"); }
    catch (error) { if (error.code === "ENOENT") continue; throw error; }
    for (const match of text.matchAll(ARCHIVE_PATH)) if (isAttachmentPath(match[1])) found.add(match[1]);
  }
  return [...found].sort();
}
// When the archive recorded the thread's deletion. Entries written before deletedAt
// existed take the time of the journal's delete-thread record.
function deletionTime(root, id, entry) {
  if (!entry?.deleted) return null;
  if (typeof entry.deletedAt === "string") return entry.deletedAt;
  let text = "";
  try { text = fs.readFileSync(safeArchivePath(root, path.join("threads", `${id}.jsonl`)), "utf8"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  let found = null;
  for (const line of text.split("\n")) {
    if (!line.includes('"delete-thread"')) continue;
    try { const record = JSON.parse(line); if (record.type === "delete-thread" && typeof record.time === "string") found = record.time; }
    catch { /* A torn last line is not a committed record. */ }
  }
  return found || (typeof entry.archivedAt === "string" ? entry.archivedAt : null);
}
const DAY_MS = 24 * 60 * 60 * 1000;
// Deleted threads whose deletion is at least `days` old. Live threads never qualify.
function expiredDeletedThreads(index, days, now = Date.now(), timeOf = (_id, entry) => entry.deletedAt) {
  if (!Number.isInteger(days) || days <= 0) return [];
  return Object.entries(index.threads || {}).filter(([id, entry]) => {
    if (!entry?.deleted || typeof entry.threadId !== "string") return false;
    const time = Date.parse(timeOf(id, entry) || "");
    return Number.isFinite(time) && now - time >= days * DAY_MS;
  }).map(([, entry]) => entry.threadId);
}
function archiveConversation(root, thread, messages, { dataDir = "", deleted = false } = {}) {
  if (!thread || typeof thread.id !== "string" || !Array.isArray(messages)) throw new Error("Invalid conversation archive payload.");
  root = fs.realpathSync(root);
  if (!fs.statSync(root).isDirectory()) throw new Error("The archive folder is unavailable.");
  const id = hash(thread.id);
  const folder = safeArchivePath(root, "threads");
  fs.mkdirSync(folder, { recursive: true });
  const journal = safeArchivePath(root, path.join("threads", `${id}.jsonl`));
  const snapshotPath = safeArchivePath(root, path.join("threads", `${id}.json`));
  let previous = readJson(snapshotPath, { messages: [] });
  const recovering = fs.existsSync(journal) && fs.statSync(journal).size !== previous.journalBytes;
  let recoveredBytes;
  let recoverySize;
  if (recovering) {
    const restored = new Map();
    previous = { messages: [], sequence: 0 };
    const bytes = fs.readFileSync(journal);
    recoverySize = bytes.length;
    // Every committed record ends in a newline. A crash can leave part of the
    // next record, including a partial UTF-8 character. Only replay whole lines.
    recoveredBytes = bytes.lastIndexOf(10) + 1;
    const lines = bytes.subarray(0, recoveredBytes).toString("utf8").split("\n");
    for (const line of lines) {
      if (!line) continue;
      const record = JSON.parse(line);
      previous.sequence = record.sequence;
      if (record.type === "thread") previous.thread = record.thread;
      if (record.type === "message") restored.set(record.message.id, record.message);
      if (record.type === "remove-message") restored.delete(record.messageId);
      if (record.type === "delete-thread") previous.deleted = true;
    }
    previous.messages = [...restored.values()];
  }
  const now = new Date().toISOString();
  const records = [];
  const warnings = [];
  const oldMessages = new Map(previous.messages.map((item) => [item.id, JSON.stringify(item)]));
  const archivedMessages = messages.map((message, index) => {
    const item = { ...message, id: message.id || `${id}:${index}` };
    if (Array.isArray(item.attachments)) item.attachments = item.attachments.map((attachment) => {
      try {
      if ((!attachment.path && !attachment.storageName) || !dataDir) return attachment;
      if (attachment.storageName && (!/^[0-9a-f]{32}-[^/\\]+$/.test(attachment.storageName))) throw new Error("Invalid attachment reference.");
      const source = fs.realpathSync(attachment.path || path.join(dataDir, "chat-attachments", thread.id.replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^[._]+|[._]+$/g, "").slice(0, 160), attachment.storageName));
      const allowed = fs.realpathSync(path.join(dataDir, "chat-attachments"));
      if (!source.startsWith(allowed + path.sep)) throw new Error("Attachment is outside Rem's attachment storage.");
      const stat = fs.statSync(source);
      const fingerprint = `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`;
      const key = `${root}\0${source}`;
      const cached = attachmentHashes.get(key);
      let relative = cached?.fingerprint === fingerprint ? cached.relative : "";
      if (!relative || !fs.existsSync(safeArchivePath(root, relative))) {
        const bytes = readAttachment(source, stat);
        relative = path.join("attachments", hash(bytes) + path.extname(source));
        const target = safeArchivePath(root, relative);
        fs.mkdirSync(path.dirname(target), { recursive: true });
        try { fs.writeFileSync(target, bytes, { mode: 0o600, flag: "wx" }); }
        catch (error) { if (error.code !== "EEXIST") throw error; }
        attachmentHashes.delete(key);
        attachmentHashes.set(key, { fingerprint, relative });
        if (attachmentHashes.size > MAX_CACHED_ATTACHMENTS) attachmentHashes.delete(attachmentHashes.keys().next().value);
      }
      return { ...attachment, archivePath: relative.replaceAll("\\", "/") };
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        const saved = previous.messages.find((prior) => prior.id === item.id)?.attachments?.find((prior) => prior.id === attachment.id && prior.storageName === attachment.storageName);
        if (saved?.archivePath && fs.existsSync(safeArchivePath(root, saved.archivePath))) return saved;
        const warning = `Attachment is no longer available: ${attachment.name || attachment.storageName}`;
        warnings.push(warning);
        return { ...attachment, archiveError: warning };
      }
    });
    if (oldMessages.get(item.id) !== JSON.stringify(item)) records.push({ type: "message", message: item });
    oldMessages.delete(item.id);
    return item;
  });
  for (const removedId of oldMessages.keys()) records.push({ type: "remove-message", messageId: removedId });
  if (JSON.stringify(previous.thread) !== JSON.stringify(thread)) records.unshift({ type: "thread", thread });
  if (deleted && !previous.deleted) records.push({ type: "delete-thread" });

  const indexPath = safeArchivePath(root, "index.json");
  const index = readArchiveIndex(indexPath);
  const completed = completedArchives.get(snapshotPath);
  if (!records.length && !recovering && Boolean(previous.deleted) === deleted
    && completed && completed.sequence === previous.sequence
    && completed.journalBytes === previous.journalBytes
    && completed.indexEntry === hash(JSON.stringify(index.threads[id] ?? null))) {
    return { id, updatedAt: previous.updatedAt, warnings };
  }

  // A timestamp cannot prove that a snapshot and index were both committed:
  // distinct updates can finish within one millisecond. Only trust revisions
  // this worker completed; a restart rebuilds the index once from the snapshot.
  completedArchives.delete(snapshotPath);
  let sequence = previous.sequence || 0;
  const encoded = records.map((record) => JSON.stringify({ version: 1, time: now, sequence: ++sequence, ...record })).join("\n") + (records.length ? "\n" : "");
  // Journal is the durable history; snapshots and the index can be rebuilt from it.
  const fd = fs.openSync(journal, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT | (fs.constants.O_NOFOLLOW || 0), 0o600);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1) throw new Error("Archive journal must be an ordinary unlinked file.");
    if (recovering && stat.size !== recoverySize) throw new Error("Archive journal changed during recovery. Retry the action.");
    const repairTail = recovering && recoveredBytes < recoverySize;
    if (repairTail) fs.ftruncateSync(fd, recoveredBytes);
    if (encoded) fs.writeFileSync(fd, encoded);
    if (encoded || repairTail) fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  const journalBytes = fs.statSync(journal).size;
  const indexedDeletion = index.threads[id]?.deleted ? index.threads[id].deletedAt : undefined;
  const snapshotDeletedAt = deleted ? indexedDeletion || (previous.deleted && previous.deletedAt) || now : undefined;
  atomicJson(snapshotPath, { version: 1, thread, messages: archivedMessages, deleted, ...(snapshotDeletedAt ? { deletedAt: snapshotDeletedAt } : {}), sequence, updatedAt: now, journalBytes });
  const terms = [...new Set(`${thread.title || ""} ${thread.projectRoot || ""} ${messages.map((item) => item.body || "").join(" ")}`.toLowerCase().match(/[\p{L}\p{N}_-]{2,}/gu) || [])].sort();
  const historicalTerms = [...new Set([...(index.threads[id]?.terms || []), ...terms])].sort();
  // Every attachment any revision of this thread used, so a purge can tell which
  // attachments another thread still needs without reading every journal.
  const prior = index.threads[id];
  const attachments = [...new Set([...(Array.isArray(prior?.attachments) ? prior.attachments : scanAttachments(root, id)),
    ...archivedMessages.flatMap((item) => (item.attachments || []).map((attachment) => attachment.archivePath).filter(isAttachmentPath))])].sort();
  const deletedAt = snapshotDeletedAt;
  const entry = { threadId: thread.id, title: thread.title, projectRoot: thread.projectRoot, updatedAt: thread.updatedAt || now, archivedAt: now, deleted, ...(deletedAt ? { deletedAt } : {}), messageCount: messages.length, snapshot: `threads/${id}.json`, journal: `threads/${id}.jsonl`, terms: historicalTerms, attachments };
  atomicJson(indexPath, { ...index, threads: { ...index.threads, [id]: entry } });
  writeReadme(root);
  completedArchives.set(snapshotPath, { sequence, journalBytes, indexEntry: hash(JSON.stringify(entry)) });
  if (completedArchives.size > 1024) completedArchives.delete(completedArchives.keys().next().value);
  return { id, updatedAt: now, warnings };
}
function forgetConversations(root, threadIds) {
  if (!Array.isArray(threadIds) || threadIds.some(id => typeof id !== "string" || !id || id.length > 128)) throw new Error("Invalid thread IDs.");
  root = fs.realpathSync(root);
  const indexPath = safeArchivePath(root, "index.json");
  const index = readArchiveIndex(indexPath);
  for (const threadId of threadIds) {
    const id = hash(threadId);
    for (const extension of ["json", "jsonl"]) {
      const file = safeArchivePath(root, path.join("threads", `${id}.${extension}`));
      fs.rmSync(file, { force: true });
      completedArchives.delete(file);
    }
    delete index.threads[id];
  }
  atomicJson(indexPath, index);
  archiveIndexes.delete(indexPath);
  return { forgotten: threadIds.length };
}

// Permanently remove threads from the archive: snapshot, journal, index entry, and
// each attachment that no remaining thread references. Safe to repeat; missing
// files are already gone. Files committed to Git or other backups are not reached.
function purgeConversations(root, threadIds) {
  if (!Array.isArray(threadIds) || threadIds.some(id => typeof id !== "string" || !id || id.length > 160)) throw new Error("Invalid thread IDs.");
  root = fs.realpathSync(root);
  if (!fs.statSync(root).isDirectory()) throw new Error("The archive folder is unavailable.");
  const indexPath = safeArchivePath(root, "index.json");
  const index = readArchiveIndex(indexPath);
  const threads = { ...index.threads };
  const candidates = new Set();
  let purged = 0;
  for (const threadId of new Set(threadIds)) {
    const id = hash(threadId);
    const entry = threads[id];
    const used = Array.isArray(entry?.attachments) ? [...entry.attachments, ...scanAttachments(root, id)] : scanAttachments(root, id);
    for (const relative of used) if (isAttachmentPath(relative)) candidates.add(relative);
    let existed = Boolean(entry);
    for (const extension of ["json", "jsonl"]) {
      const file = safeArchivePath(root, path.join("threads", `${id}.${extension}`));
      if (fs.existsSync(file)) existed = true;
      fs.rmSync(file, { force: true });
      completedArchives.delete(file);
    }
    delete threads[id];
    if (existed) purged += 1;
  }
  // Attachments are shared by content hash, so keep any another thread still uses.
  // Entries without a list get one now, which also saves the next purge the scan.
  const kept = new Set();
  for (const [id, entry] of Object.entries(threads)) {
    if (!Array.isArray(entry.attachments)) threads[id] = { ...entry, attachments: scanAttachments(root, id) };
    for (const relative of threads[id].attachments) kept.add(relative);
  }
  atomicJson(indexPath, { ...index, threads });
  archiveIndexes.delete(indexPath);
  let attachmentsRemoved = 0;
  for (const relative of candidates) {
    if (kept.has(relative)) continue;
    const file = safeArchivePath(root, relative);
    if (!fs.existsSync(file)) continue;
    fs.rmSync(file, { force: true });
    attachmentsRemoved += 1;
  }
  if (attachmentsRemoved) for (const [key, value] of attachmentHashes) if (key.startsWith(`${root}\0`) && candidates.has(value.relative)) attachmentHashes.delete(key);
  writeReadme(root);
  return { purged, attachmentsRemoved, threadIds: [...new Set(threadIds)] };
}
// Purge archive copies of threads deleted at least `days` ago. Off unless days > 0.
function purgeExpiredConversations(root, days, now = Date.now()) {
  if (!Number.isInteger(days) || days <= 0 || days > 3650) throw new Error("Invalid archive purge age.");
  root = fs.realpathSync(root);
  const index = readArchiveIndex(safeArchivePath(root, "index.json"));
  const expired = expiredDeletedThreads(index, days, now, (id, entry) => deletionTime(root, id, entry));
  if (!expired.length) return { purged: 0, attachmentsRemoved: 0, threadIds: [] };
  return purgeConversations(root, expired);
}

module.exports = { archiveConversation, forgetConversations, purgeConversations, purgeExpiredConversations, expiredDeletedThreads, README };
