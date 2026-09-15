const fs = require("node:fs");
const path = require("node:path");

function redactLog(value) {
  // ANSI color sequences start with the ESC control character.
  // eslint-disable-next-line no-control-regex
  return String(value).replace(/\x1b\[[0-9;]*m/g, "")
    .replace(/(bearer\s+)[\w.-]+/gi, "$1[redacted]")
    .replace(/((?:api[_-]?key|apiToken|access[_-]?token|password|secret|authorization)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi, "$1[redacted]")
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/g, "$1[redacted]@")
    .slice(0, 32768);
}

function createAppLog(directory, { maxBytes = 5 * 1024 * 1024, backups = 3, maxQueueBytes = 1024 * 1024 } = {}) {
  const file = path.join(directory, "app.jsonl");
  const queue = [];
  let queuedBytes = 0, dropped = 0, pending, closed = false;
  const emergency = (message) => {
    // Only fatal errors and writer failures use this small synchronous fallback.
    try { fs.writeSync(2, `${redactLog(message).slice(0, 2048)}\n`); } catch { /* stderr may be closed. */ }
  };
  const encode = (level, source, message) => JSON.stringify({
    time: new Date().toISOString(), level: redactLog(level), source: redactLog(source), message: redactLog(message),
  }) + "\n";
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
          line = encode("warn", "desktop", `Dropped ${dropped} log messages because the log queue was full.`);
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
      emergency(`Raticode could not persist its app log: ${error.code || "write failed"}`);
    }
  }
  function start() {
    if (!pending) pending = drain().finally(() => { pending = undefined; });
    return pending;
  }
  return {
    file,
    emergency,
    write(level, source, message) {
      if (closed) return false;
      let line;
      try { line = encode(level, source, message); }
      catch { emergency("Raticode could not format an app log message"); return false; }
      const bytes = Buffer.byteLength(line);
      if (queuedBytes + bytes > maxQueueBytes) { dropped++; void start(); return false; }
      queue.push(line); queuedBytes += bytes;
      void start();
      return true;
    },
    async flush() { while (pending || queue.length || dropped) await (pending || start()); },
    close() { closed = true; return this.flush(); },
  };
}
module.exports = { createAppLog, redactLog };
