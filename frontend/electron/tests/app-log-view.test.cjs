const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createAppLog, normalizeLevel, redactLog } = require("../app-log.cjs");
const { createBackendOutput } = require("../backend-log.cjs");

function tempDir(t, name) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `raticode-${name}-`));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test("levels from every source map onto debug, info, warn and error", () => {
  assert.deepEqual(["debug", "info", "warn", "error", "warning", "critical", "fatal", "trace", "verbose", "log", "", "odd"].map(normalizeLevel),
    ["debug", "info", "warn", "error", "warn", "error", "error", "debug", "debug", "info", "info", "info"]);
});

test("the live view reads new entries after a cursor and keeps the file format", async t => {
  const log = createAppLog(tempDir(t, "log-view"));
  log.write("info", "desktop", "Starting Raticode");
  log.write("warning", "devices", "Phone could not sign in", "gofer.devices.service");
  const first = log.read();
  assert.deepEqual(first.entries.map(entry => [entry.seq, entry.level, entry.source, entry.message, entry.component]), [
    [1, "info", "desktop", "Starting Raticode", undefined],
    [2, "warn", "devices", "Phone could not sign in", "gofer.devices.service"],
  ]);
  assert.equal(first.cursor, 2);
  assert.deepEqual(first.counts, { debug: 0, info: 1, warn: 1, error: 0 });
  log.write("error", "backend", "Backend stopped unexpectedly");
  const next = log.read({ after: first.cursor });
  assert.deepEqual(next.entries.map(entry => entry.message), ["Backend stopped unexpectedly"]);
  assert.equal(log.read({ after: next.cursor }).entries.length, 0);
  assert.equal(log.read({ after: next.cursor }).cursor, next.cursor);
  await log.close();
  const lines = fs.readFileSync(log.file, "utf8").trim().split("\n").map(line => JSON.parse(line));
  assert.deepEqual(Object.keys(lines[1]), ["time", "level", "source", "message", "component"]);
  assert.equal(lines.some(line => "seq" in line), false);
});

test("the live view is bounded and says how many entries a slow reader missed", async t => {
  const log = createAppLog(tempDir(t, "log-bounded"), { memoryEntries: 3 });
  for (let i = 1; i <= 6; i++) log.write("info", `source-${i}`, `message ${i}`);
  const view = log.read({ after: 1 });
  assert.deepEqual(view.entries.map(entry => entry.seq), [4, 5, 6]);
  assert.equal(view.missed, 2);
  assert.equal(view.evicted, 3);
  assert.equal(log.read().missed, 0, "a first read has nothing to miss");
  const paged = log.read({ after: 0, limit: 2 });
  assert.equal(paged.more, true);
  assert.equal(log.read({ after: paged.cursor, limit: 2 }).more, false);
  await log.close();

  const bytes = createAppLog(tempDir(t, "log-bytes"), { memoryBytes: 1000 });
  for (let i = 0; i < 20; i++) bytes.write("info", "backend", "x".repeat(200));
  assert.ok(bytes.read().entries.length <= 4);
  await bytes.close();
});

test("a flooding source is capped per second and the gap is reported", async t => {
  let clock = 1_000_000;
  const log = createAppLog(tempDir(t, "log-flood"), { perSourcePerSecond: 3, now: () => clock });
  for (let i = 0; i < 10; i++) log.write("debug", "backend", `noisy ${i}`);
  log.write("error", "devices", "still recorded");
  clock += 1000;
  log.write("info", "backend", "next second");
  const messages = log.read().entries.map(entry => entry.message);
  assert.deepEqual(messages, [
    "noisy 0", "noisy 1", "noisy 2", "still recorded",
    "Skipped 7 backend log messages: more than 3 arrived in one second. They are missing from the log file and this view.",
    "next second",
  ]);
  await log.close();
  assert.match(fs.readFileSync(log.file, "utf8"), /Skipped 7 backend log messages/);
});

test("history returns earlier launches only, redacted, from the current and previous file", async t => {
  const root = tempDir(t, "log-history");
  const file = path.join(root, "app.jsonl");
  const old = (time, message, level = "info") => JSON.stringify({ time, level, source: "backend", message }) + "\n";
  fs.writeFileSync(`${file}.1`, old("2026-11-04T10:00:00.000Z", "from the rotated file"));
  fs.writeFileSync(file, old("2026-11-05T09:00:00.000Z", "token=old-secret in an old line", "warning") + "not json\n" + old("2026-11-05T09:30:00.000Z", "last line before launch"));
  const log = createAppLog(root, { now: () => Date.parse("2026-11-05T10:00:00.000Z") });
  log.write("info", "desktop", "Starting Raticode");
  await log.flush();
  const history = await log.history();
  assert.deepEqual(history.entries.map(entry => [entry.level, entry.message]), [
    ["info", "from the rotated file"],
    ["warn", "token=[redacted] in an old line"],
    ["info", "last line before launch"],
  ]);
  assert.equal(history.truncated, false);
  const small = await log.history({ maxEntries: 1 });
  assert.deepEqual(small.entries.map(entry => entry.message), ["last line before launch"]);
  assert.equal(small.truncated, true);
  await log.close();
});

test("pairing invitations are redacted with the other secrets", () => {
  assert.equal(redactLog("scan raticode://pair?v=2&data=c2VjcmV0 now"), "scan raticode://pair?[redacted] now");
});

test("backend output keeps each structured record's level and groups other stderr", () => {
  const written = [];
  const echoed = [];
  const output = createBackendOutput({ write: (...args) => written.push(args), echo: line => echoed.push(line) });
  const record = (level, logger, message) => `GOFER_LOG ${JSON.stringify({ level, logger, message })}\n`;
  output.stderr(record("debug", "gofer.devices.service", "A connection closed early") + record("warn", "gofer.ui.server", "Skipping invalid schedule"));
  // Split across reads, as pipes deliver it.
  const traceback = record("error", "gofer.crash", "Uncaught error\nTraceback (most recent call last):\nValueError: bad");
  output.stderr(traceback.slice(0, 30));
  output.stderr(traceback.slice(30));
  output.stderr("Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: native crash\n");
  output.stderr("plain library notice\n");
  output.stderr("GOFER_LOG {not json}\n");
  output.stderr("GOFER_UI_STOPPED\n");
  output.stdoutLine("printed by the backend");
  output.stderr("no newline at exit");
  output.end();
  assert.deepEqual(written, [
    ["debug", "devices", "A connection closed early", "gofer.devices.service"],
    ["warn", "backend", "Skipping invalid schedule", "gofer.ui.server"],
    ["error", "backend", "Uncaught error\nTraceback (most recent call last):\nValueError: bad", "gofer.crash"],
    ["error", "backend", "Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: native crash", "stderr"],
    ["warn", "backend", "plain library notice", "stderr"],
    ["warn", "backend", "GOFER_LOG {not json}", "stderr"],
    ["info", "backend", "Backend stopped its server"],
    ["info", "backend", "printed by the backend"],
    ["warn", "backend", "no newline at exit", "stderr"],
  ]);
  assert.equal(echoed[0], "DEBUG gofer.devices.service: A connection closed early");
  assert.equal(echoed[2], "ERROR gofer.crash: Uncaught error\nTraceback (most recent call last):\nValueError: bad", "errors keep their traceback for the crash window");
});

test("main.js hands backend output to the parser and asks for desktop-format logs", () => {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  assert.match(source, /GOFER_LOG_FORMAT: "desktop"/);
  assert.match(source, /child\.stderr\.on\("data", \(text\) => backendOutput\.stderr\(text\)\)/);
  assert.match(source, /developerInfo,\n\s+developerAction,\n\s+developerLog,/, "main.js provides the handler");
  const handlers = fs.readFileSync(path.join(__dirname, "../ipc-handlers.cjs"), "utf8");
  assert.match(handlers, /\["gofer:developer-log", "developerLog"\]/);
  const preload = fs.readFileSync(path.join(__dirname, "../preload.cjs"), "utf8");
  assert.match(preload, /entries: \(options = \{\}\) => invokeDesktop\("gofer:developer-log", options\)/);
});
