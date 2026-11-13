const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  classifyMainError, createMainErrorHandler, createTerminalOutput, isBrokenPipe, rejectionLevel,
} = require("../main-errors.cjs");

const epipe = () => Object.assign(new Error("write EPIPE"), { code: "EPIPE", errno: -32, syscall: "write" });
const destroyed = () => new TypeError("Object has been destroyed");

test("a closed terminal is benign at any time; closing connections and windows only while quitting", () => {
  assert.equal(isBrokenPipe(epipe()), true);
  assert.equal(isBrokenPipe(Object.assign(new Error("write EIO"), { code: "EIO", syscall: "write" })), true);
  assert.equal(isBrokenPipe(Object.assign(new Error("read EIO"), { code: "EIO", syscall: "read" })), false);
  assert.equal(classifyMainError(epipe()), "benign");
  assert.equal(classifyMainError(Object.assign(new Error("write after end"), { code: "ERR_STREAM_WRITE_AFTER_END" })), "benign");
  const reset = Object.assign(new Error("read ECONNRESET"), { code: "ECONNRESET" });
  assert.equal(classifyMainError(reset), "fatal");
  assert.equal(classifyMainError(reset, { quitting: true }), "benign");
  assert.equal(classifyMainError(destroyed()), "fatal");
  assert.equal(classifyMainError(destroyed(), { quitting: true }), "benign");
  assert.equal(classifyMainError(new Error("boom"), { quitting: true }), "shutdown");
  assert.equal(classifyMainError(new Error("boom")), "fatal");
  assert.equal(classifyMainError("thrown string"), "fatal");
  assert.equal(rejectionLevel(epipe()), "info");
  assert.equal(rejectionLevel(new Error("boom"), { quitting: true }), "error");
});

test("real errors while running still show Electron's dialog; shutdown noise goes to the log only", () => {
  let quitting = false;
  const logged = [];
  const shown = [];
  const handle = createMainErrorHandler({ quitting: () => quitting, log: (...entry) => logged.push(entry), show: error => shown.push(error) });
  const genuine = new Error("genuine failure");
  handle(genuine);
  assert.deepEqual(shown, [genuine]);
  assert.equal(logged[0][0], "error");
  assert.match(logged[0][1], /^Uncaught exception: Error: genuine failure/);
  for (let index = 0; index < 100; index += 1) handle(epipe());
  assert.equal(shown.length, 1, "a broken pipe never opens a dialog");
  assert.ok(logged.slice(1).every(([level, message]) => level === "info" && /closed connection or terminal: Error: write EPIPE/.test(message)));
  quitting = true;
  handle(destroyed());
  handle(new Error("late failure"));
  assert.equal(shown.length, 1, "nothing opens a dialog while Raticode quits");
  assert.equal(logged.at(-2)[0], "info");
  assert.deepEqual([logged.at(-1)[0], /while Raticode was quitting \(not shown on screen\): Error: late failure/.test(logged.at(-1)[1])], ["error", true]);
});

test("a logger that throws can't turn the handler itself into a new uncaught error", () => {
  const shown = [];
  const handle = createMainErrorHandler({ log: () => { throw new Error("log closed"); }, show: error => shown.push(error) });
  assert.doesNotThrow(() => handle(epipe()));
  assert.doesNotThrow(() => handle(new Error("real")));
  assert.equal(shown.length, 1);
});

test("terminal output stops after the first broken write and logs it once", () => {
  const stderr = new EventEmitter();
  let writes = 0;
  stderr.write = () => { writes += 1; throw epipe(); };
  const stdout = new EventEmitter();
  stdout.write = () => true;
  const logged = [];
  const output = createTerminalOutput({ stdout, stderr, log: (...entry) => logged.push(entry) });
  assert.equal(output.stderr("one\n"), false);
  assert.equal(output.stderr("two\n"), false);
  stderr.emit("error", epipe());
  assert.equal(writes, 1);
  assert.equal(output.broken("stderr"), true);
  assert.equal(output.broken("stdout"), false);
  assert.equal(output.stdout("still open\n"), true);
  assert.deepEqual(logged, [["info", "The terminal stopped reading Raticode's stderr (EPIPE). Later output goes only to the app log."]]);
  // Any other stream failure is a warning, still without throwing.
  stdout.emit("error", Object.assign(new Error("bad fd"), { code: "EBADF" }));
  assert.deepEqual(logged.at(-1), ["warn", "The terminal stopped reading Raticode's stdout (EBADF). Later output goes only to the app log."]);
  assert.equal(output.stdout("after\n"), false);
  // A destroyed stream is skipped without a write.
  const closed = Object.assign(new EventEmitter(), { destroyed: true, write: () => assert.fail("wrote to a destroyed stream") });
  assert.equal(createTerminalOutput({ stderr: closed }).stderr("x\n"), false);
});

// A real Node process writing to a pipe whose reader has gone, the way Electron's
// stderr looks after Ctrl+C stops the dev runner.
test("a process whose terminal closed keeps running without an uncaught EPIPE", async t => {
  const results = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "raticode-epipe-"));
  t.after(() => fs.rmSync(results, { recursive: true, force: true }));
  const resultFile = path.join(results, "result.json");
  const script = `
    const fs = require("node:fs");
    const { createMainErrorHandler, createTerminalOutput } = require(${JSON.stringify(path.join(__dirname, "..", "main-errors.cjs"))});
    const log = [];
    const shown = [];
    const output = createTerminalOutput({ stdout: process.stdout, stderr: process.stderr, log: (...entry) => log.push(entry) });
    process.on("uncaughtException", createMainErrorHandler({ log: (...entry) => log.push(entry), show: error => shown.push(String(error)) }));
    process.stdin.once("data", () => {
      let attempts = 0;
      const timer = setInterval(() => {
        attempts += 1;
        output.stderr("[gofer-backend] INFO gofer.ui.server: still shutting down\\n");
        if (attempts < 200) return;
        clearInterval(timer);
        fs.writeFileSync(${JSON.stringify(resultFile)}, JSON.stringify({ log, shown, broken: output.broken("stderr") }));
        process.exit(0);
      }, 1);
    });
    process.stderr.write("ready\\n");
  `;
  const child = spawn(process.execPath, ["-e", script], { stdio: ["pipe", "ignore", "pipe"] });
  await new Promise(resolve => child.stderr.once("data", resolve));
  child.stderr.destroy();
  child.stdin.end("go\n");
  const [code, signal] = await new Promise(resolve => child.once("exit", (...result) => resolve(result)));
  assert.deepEqual([code, signal], [0, null]);
  const result = JSON.parse(fs.readFileSync(resultFile, "utf8"));
  assert.equal(result.broken, true);
  assert.deepEqual(result.shown, []);
  assert.equal(result.log.length, 1);
  assert.match(result.log[0][1], /stopped reading Raticode's stderr \(EPIPE\)/);
});
