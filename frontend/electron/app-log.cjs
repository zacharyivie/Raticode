const fs = require("node:fs");
const path = require("node:path");

const LEVELS = ["debug", "info", "warn", "error"];
const LEVEL_ALIASES = { warning: "warn", critical: "error", fatal: "error", trace: "debug", verbose: "debug", log: "info" };

function redactLog(value) {
  // ANSI color sequences start with the ESC control character.
  // eslint-disable-next-line no-control-regex
  return String(value).replace(/\x1b\[[0-9;]*m/g, "")
    // HTTP token68 credentials can contain +, /, ~ and trailing = padding.
    .replace(/(bearer\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1[redacted]")
    .replace(/(authorization["']?\s*[:=]\s*["']?basic\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1[redacted]")
    // Consume the entire quoted value, including escaped quotes and spaces.
    .replace(/((?:api[_-]?key|token|password|secret|authorization)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*(?:"|\\?$)|'(?:\\.|[^'\\])*(?:'|\\?$)|[^\s,"'}]+)/gi, "$1[redacted]")
    // The last @ in the authority separates userinfo from the host. Passwords
    // can contain earlier @ characters, and URL schemes are case insensitive.
    .replace(/(https?:\/\/)[^\s/?#"'<>]*@/gi, "$1[redacted]@")
    // Bare tokens with a well-known prefix: Raticode agent tokens, GitHub, OpenAI and
    // Anthropic keys, Slack tokens and AWS access key IDs (same as app_log.py).
    .replace(/\b(?:rtk_agent_|gh[opusr]_|github_pat_|sk-|xox[abprs]-|AKIA)[A-Za-z0-9_-]{12,}/g, "[redacted]")
    // Pairing invitations carry one-time secrets.
    .replace(/(raticode:\/\/pair\?)[^\s"'<>]*/gi, "$1[redacted]")
    .slice(0, 32768);
}

function normalizeLevel(level) {
  const value = String(level || "").toLowerCase();
  if (LEVELS.includes(value)) return value;
  return LEVEL_ALIASES[value] || "info";
}

function parseLine(line) {
  try {
    const entry = JSON.parse(line);
    if (!entry || typeof entry !== "object" || typeof entry.message !== "string") return null;
    return {
      time: typeof entry.time === "string" ? entry.time : "",
      level: normalizeLevel(entry.level),
      source: typeof entry.source === "string" ? entry.source : "desktop",
      message: entry.message,
      ...(typeof entry.component === "string" && entry.component ? { component: entry.component } : {}),
    };
  } catch {
    return null;
  }
}

// The live view keeps recent entries in memory, bounded by count and bytes, so the
// Developer tab can follow the log without rereading files. The files stay the
// durable record: four rotated 5 MB files.
function createAppLog(directory, {
  maxBytes = 5 * 1024 * 1024, backups = 3, maxQueueBytes = 1024 * 1024,
  memoryEntries = 5000, memoryBytes = 4 * 1024 * 1024,
  perSourcePerSecond = 200, now = () => Date.now(),
} = {}) {
  const file = path.join(directory, "app.jsonl");
  const queue = [];
  const startedAt = new Date(now()).toISOString();
  let queuedBytes = 0, dropped = 0, pending, closed = false;
  // In-memory view of this session.
  const entries = [];
  let entryBytes = 0, sequence = 0, evicted = 0;
  const counts = { debug: 0, info: 0, warn: 0, error: 0 };
  // Flood guard: a runaway source must not push everything else out of the view.
  const rates = new Map();
  const emergency = (message) => {
    // Only fatal errors and writer failures use this small synchronous fallback.
    try { fs.writeSync(2, `${redactLog(message).slice(0, 2048)}\n`); } catch { /* stderr may be closed. */ }
  };
  function remember(entry) {
    sequence += 1;
    const stored = { seq: sequence, ...entry };
    entries.push(stored);
    counts[stored.level] += 1;
    entryBytes += stored.message.length + 64;
    while (entries.length > memoryEntries || (entryBytes > memoryBytes && entries.length > 1)) {
      const old = entries.shift();
      entryBytes -= old.message.length + 64;
      evicted += 1;
    }
    return stored;
  }
  async function ignoreMissing(operation) {
    try { await operation(); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  async function drain() {
    try {
      await fs.promises.mkdir(directory, { recursive: true });
      let size = 0;
      await ignoreMissing(async () => { size = (await fs.promises.stat(file)).size; });
      while (queue.length || dropped) {
        let line;
        if (queue.length) {
          line = queue.shift();
          queuedBytes -= Buffer.byteLength(line);
        } else {
          const entry = remember(build("warn", "desktop", `Dropped ${dropped} log messages from the log file because writing could not keep up. They still appear in this view until Raticode quits.`));
          line = encode(entry);
          dropped = 0;
        }
        const bytes = Buffer.byteLength(line);
        if (size && size + bytes > maxBytes) {
          if (backups <= 0) await ignoreMissing(() => fs.promises.unlink(file));
          else for (let i = backups; i >= 1; i -= 1) {
            const from = i === 1 ? file : `${file}.${i - 1}`;
            const to = `${file}.${i}`;
            await ignoreMissing(() => fs.promises.unlink(to));
            await ignoreMissing(() => fs.promises.rename(from, to));
          }
          size = 0;
        }
        await fs.promises.appendFile(file, line, { mode: 0o600 });
        size += bytes;
      }
    } catch (error) {
      queue.length = 0; queuedBytes = 0; dropped = 0;
      remember(build("error", "desktop", `Raticode could not write its log file (${error.code || "write failed"}). New entries appear here but are not saved.`));
      emergency(`Raticode could not persist its app log: ${error.code || "write failed"}`);
    }
  }
  function start() {
    if (!pending) pending = drain().finally(() => { pending = undefined; });
    return pending;
  }
  function build(level, source, message, component) {
    const entry = {
      time: new Date(now()).toISOString(), level: normalizeLevel(redactLog(level)),
      source: redactLog(source).slice(0, 64) || "desktop", message: redactLog(message),
    };
    if (component) entry.component = redactLog(component).slice(0, 128);
    return entry;
  }
  function encode(entry) {
    const stored = { time: entry.time, level: entry.level, source: entry.source, message: entry.message };
    if (entry.component) stored.component = entry.component;
    return JSON.stringify(stored) + "\n";
  }
  function persist(entry) {
    let line;
    try { line = encode(entry); }
    catch { emergency("Raticode could not format an app log message"); return false; }
    const bytes = Buffer.byteLength(line);
    if (queuedBytes + bytes > maxQueueBytes) { dropped++; void start(); return false; }
    queue.push(line); queuedBytes += bytes;
    void start();
    return true;
  }
  // Report sources that went over their per-second budget once their second ends.
  function settleRates(time) {
    for (const [source, rate] of rates) {
      if (time - rate.start < 1000) continue;
      rates.delete(source);
      if (rate.skipped) {
        const note = remember(build("warn", "desktop", `Skipped ${rate.skipped} ${source} log messages: more than ${perSourcePerSecond} arrived in one second. They are missing from the log file and this view.`));
        persist(note);
      }
    }
  }
  return {
    file,
    startedAt,
    emergency,
    write(level, source, message, component) {
      if (closed) return false;
      const time = now();
      settleRates(time);
      const key = String(source || "desktop");
      const rate = rates.get(key) || { start: time, count: 0, skipped: 0 };
      rates.set(key, rate);
      rate.count += 1;
      if (rate.count > perSourcePerSecond) { rate.skipped += 1; return false; }
      let entry;
      try { entry = build(level, source, message, component); }
      catch { emergency("Raticode could not format an app log message"); return false; }
      remember(entry);
      return persist(entry);
    },
    // Entries after `after`, oldest first. `missed` counts entries that left memory
    // before this reader saw them.
    read({ after = 0, limit = 1000 } = {}) {
      settleRates(now());
      const cursor = Math.max(0, Number(after) || 0);
      const first = entries.length ? entries[0].seq : sequence + 1;
      const missed = cursor && cursor < first - 1 ? first - 1 - cursor : 0;
      const start = entries.findIndex(entry => entry.seq > cursor);
      const max = Math.max(1, Math.min(2000, Number(limit) || 1000));
      const batch = start === -1 ? [] : entries.slice(start, start + max);
      return {
        entries: batch,
        cursor: batch.length ? batch.at(-1).seq : cursor,
        latest: sequence,
        more: start !== -1 && start + max < entries.length,
        missed,
        evicted,
        counts: { ...counts },
        startedAt,
        file,
      };
    },
    // The end of the log files from before this session, newest last.
    async history({ maxEntries = 2000, budget = 512 * 1024 } = {}) {
      const collected = [];
      let remaining = budget;
      let truncated = false;
      for (const name of [file, `${file}.1`]) {
        if (remaining <= 0 || collected.length >= maxEntries) break;
        let handle;
        try {
          handle = await fs.promises.open(name, "r");
          const { size } = await handle.stat();
          const length = Math.min(size, remaining);
          const buffer = Buffer.alloc(length);
          await handle.read(buffer, 0, length, size - length);
          remaining -= length;
          const lines = buffer.toString("utf8").split("\n");
          if (size > length) { lines.shift(); truncated = true; }
          const parsed = lines.map(parseLine).filter(entry => entry && entry.time && entry.time < startedAt);
          collected.unshift(...parsed);
        } catch (error) {
          if (error.code !== "ENOENT") truncated = true;
        } finally {
          await handle?.close().catch(() => {});
        }
      }
      if (collected.length > maxEntries) { collected.splice(0, collected.length - maxEntries); truncated = true; }
      return { entries: collected.map(entry => ({ ...entry, message: redactLog(entry.message) })), truncated };
    },
    async flush() { while (pending || queue.length || dropped) await (pending || start()); },
    close() { closed = true; return this.flush(); },
  };
}
module.exports = { createAppLog, redactLog, normalizeLevel, LEVELS };
