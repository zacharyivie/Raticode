require("./test-tmp.cjs");
// terminal-editor.cjs is the Git editor a terminal's git runs (GIT_EDITOR). It
// sends the file git wants edited to Raticode over a local socket, as one JSON
// line, and exits with the status Raticode answers with. These tests run it
// against a local server on a temporary socket.

const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { editorTargetArg, runClient } = require("../terminal-editor.cjs");
const { tempFolder } = require("./temp-folder.cjs");

const SCRIPT = path.join(__dirname, "..", "terminal-editor.cjs");
const COULD_NOT_OPEN = "Raticode could not open the Git editor.\n";
const INVALID = "Raticode returned an invalid editor response.\n";

// A socket address whose length doesn't depend on the temp folder, so a long
// TMPDIR can't pass the OS limit (108 bytes on Linux, 104 on macOS). Linux gets
// an abstract name, Windows a named pipe; elsewhere it goes in a temp folder.
let addresses = 0;
function socketAddress(t) {
  addresses += 1;
  const name = `raticode-te-${process.pid}-${addresses}`;
  if (process.platform === "linux") return `\0${name}`;
  if (process.platform === "win32") return `\\\\.\\pipe\\${name}`;
  const socketPath = path.join(tempFolder("te-", t), "e.sock");
  const bytes = Buffer.byteLength(socketPath);
  if (bytes > 103) throw new Error(`The temp socket path is ${bytes} bytes, past the 104-byte limit: ${socketPath}`);
  return socketPath;
}

// The error listening on a socket gives in this run, or null when it works.
const listenError = new Promise((resolve) => {
  const server = net.createServer();
  server.once("error", resolve);
  server.listen(socketAddress(), () => server.close(() => resolve(null)));
});

// Some agent sandboxes refuse every Unix socket. Only that skips; any other
// error fails the test with its code.
const BLOCKED = new Set(["EPERM", "EACCES"]);

function socketTest(name, fn) {
  test(name, async (t) => {
    const error = await listenError;
    if (error && BLOCKED.has(error.code)) {
      t.skip(`this sandbox doesn't allow Unix sockets (${error.code})`);
      return;
    }
    if (error) throw new Error(`Couldn't listen on a test socket (${error.code}): ${error.message}`, { cause: error });
    await fn(t);
  });
}

// Runs fn with the current folder set to folder, for a socket path given
// relative to it. Binding and closing a socket happen before they return, so
// nothing else runs in that folder in between.
function inFolder(folder, fn) {
  const previous = process.cwd();
  process.chdir(folder);
  try {
    return fn();
  } finally {
    process.chdir(previous);
  }
}

function captureStderr() {
  const lines = [];
  return { lines, write: (text) => lines.push(String(text)), get text() { return lines.join(""); } };
}

// A server that reads the editor's request and answers with reply(socket, request).
// With ownFolder, it listens on a socket file named relative to a temp folder of
// its own (folder), for a child process started in that folder.
async function editorServer(t, reply, { ownFolder = false } = {}) {
  const folder = ownFolder ? fs.mkdtempSync(path.join(os.tmpdir(), "te-")) : "";
  const socketPath = folder ? "editor.sock" : socketAddress(t);
  const requests = [];
  let connections = 0;
  const server = net.createServer((socket) => {
    connections += 1;
    let received = "";
    socket.setEncoding("utf8");
    socket.on("error", () => {});
    socket.on("data", (chunk) => {
      received += chunk;
      if (!received.includes("\n")) return;
      requests.push(received);
      reply(socket, received);
    });
  });
  const here = (fn) => (folder ? inFolder(folder, fn) : fn());
  // After hooks run in the order they were added: the server closes, and removes
  // its socket file, while the folder is still there; then the folder goes.
  t.after(() => new Promise((resolve) => here(() => server.close(() => resolve()))));
  if (folder) t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const listening = new Promise((resolve, reject) => {
    server.once("listening", resolve);
    server.once("error", reject);
  });
  here(() => server.listen(socketPath));
  await listening;
  return { folder, socketPath, requests, get connections() { return connections; } };
}

function editorEnv(socketPath, extra = {}) {
  return {
    RATICODE_EDITOR_ENDPOINT: socketPath,
    RATICODE_EDITOR_TOKEN: "editor-token",
    RATICODE_TERMINAL_ID: "terminal-7",
    RATICODE_EDITOR_CWD: "/work/repo",
    ...extra,
  };
}

// Only what node needs, plus the coverage folder when the run measures coverage.
function childEnv() {
  const env = { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR };
  if (process.env.NODE_V8_COVERAGE) env.NODE_V8_COVERAGE = process.env.NODE_V8_COVERAGE;
  return env;
}

const GIT_ARGV = ["node", SCRIPT, ".git/COMMIT_EDITMSG"];

test("editorTargetArg picks the last argument that isn't blank", () => {
  assert.equal(editorTargetArg(["--wait", ".git/COMMIT_EDITMSG"]), ".git/COMMIT_EDITMSG");
  assert.equal(editorTargetArg([".git/rebase-merge/git-rebase-todo", "", "   "]), ".git/rebase-merge/git-rebase-todo");
  assert.equal(editorTargetArg(["first", undefined, null, 7]), "first");
  assert.equal(editorTargetArg([]), "");
  assert.equal(editorTargetArg(["", " \t"]), "");
});

test("editorTargetArg leaves the arguments it was given in order", () => {
  const args = ["a", "b"];
  editorTargetArg(args);
  assert.deepEqual(args, ["a", "b"]);
});

for (const missing of ["RATICODE_EDITOR_ENDPOINT", "RATICODE_EDITOR_TOKEN", "RATICODE_TERMINAL_ID"]) {
  test(`without ${missing} the editor fails without connecting`, async (t) => {
    const connect = t.mock.method(net, "createConnection");
    const env = editorEnv(path.join(tempFolder("te-", t), "editor.sock"));
    delete env[missing];
    const stderr = captureStderr();
    assert.equal(await runClient({ argv: GIT_ARGV, env, stderr }), 1);
    assert.equal(stderr.text, COULD_NOT_OPEN);
    assert.equal(connect.mock.callCount(), 0);
  });
}

test("without a file to edit the editor fails without connecting", async (t) => {
  const connect = t.mock.method(net, "createConnection");
  const stderr = captureStderr();
  const env = editorEnv(path.join(tempFolder("te-", t), "editor.sock"));
  assert.equal(await runClient({ argv: ["node", SCRIPT, "  "], env, stderr }), 1);
  assert.equal(stderr.text, COULD_NOT_OPEN);
  assert.equal(connect.mock.callCount(), 0);
});

socketTest("the editor sends one JSON line with the file, terminal and token, and an ok answer exits 0", async (t) => {
  const server = await editorServer(t, (socket) => socket.end('{"ok":true}\n'));
  const stderr = captureStderr();
  const argv = ["node", SCRIPT, "--wait", ".git/COMMIT_EDITMSG", " "];
  assert.equal(await runClient({ argv, env: editorEnv(server.socketPath), stderr }), 0);
  assert.equal(stderr.text, "");
  assert.equal(server.requests.length, 1);
  const [request] = server.requests;
  assert.ok(request.endsWith("\n"));
  assert.equal(request.indexOf("\n"), request.length - 1, "the request is a single line");
  assert.deepEqual(JSON.parse(request), {
    targetPath: path.resolve("/work/repo", ".git/COMMIT_EDITMSG"),
    terminalId: "terminal-7",
    token: "editor-token",
  });
});

socketTest("an absolute file stays as it is and a missing RATICODE_EDITOR_CWD resolves from the current folder", async (t) => {
  const server = await editorServer(t, (socket) => socket.end('{"ok":true}\n'));
  const stderr = captureStderr();
  const absolute = path.join(path.sep, "elsewhere", "MERGE_MSG");
  assert.equal(await runClient({ argv: ["node", SCRIPT, absolute], env: editorEnv(server.socketPath), stderr }), 0);
  const env = editorEnv(server.socketPath);
  delete env.RATICODE_EDITOR_CWD;
  assert.equal(await runClient({ argv: GIT_ARGV, env, stderr }), 0);
  assert.deepEqual(server.requests.map((line) => JSON.parse(line).targetPath), [
    absolute,
    path.resolve(process.cwd(), ".git/COMMIT_EDITMSG"),
  ]);
});

socketTest("an answer that isn't ok exits 1 and prints its error", async (t) => {
  const server = await editorServer(t, (socket) => socket.end('{"ok":false,"error":"The terminal was closed."}\n'));
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr }), 1);
  assert.equal(stderr.text, "The terminal was closed.\n");
});

socketTest("only ok: true counts as success", async (t) => {
  const server = await editorServer(t, (socket) => socket.end('{"ok":"true"}\n'));
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr }), 1);
  assert.equal(stderr.text, "");
});

socketTest("an answer split across writes is read once its line is complete", async (t) => {
  const server = await editorServer(t, (socket) => {
    socket.write('{"ok":');
    setTimeout(() => socket.end("true}\n"), 20);
  });
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr: captureStderr() }), 0);
});

socketTest("more than 16 KB without a newline exits 1 as an invalid answer", async (t) => {
  // The server keeps the connection open, so only the size limit can end the wait.
  const server = await editorServer(t, (socket) => socket.write("x".repeat(16 * 1024 + 1)));
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr }), 1);
  assert.equal(stderr.text, INVALID);
});

socketTest("an answer of exactly 16 KB is still read", async (t) => {
  const line = JSON.stringify({ ok: true, pad: "" });
  const padded = `${line.slice(0, -2)}${"x".repeat(16 * 1024 - line.length - 1)}"}\n`;
  assert.equal(padded.length, 16 * 1024);
  const server = await editorServer(t, (socket) => socket.end(padded));
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr: captureStderr() }), 0);
});

socketTest("an answer that isn't JSON exits 1 as an invalid answer", async (t) => {
  const server = await editorServer(t, (socket) => socket.end("not json\n"));
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr }), 1);
  assert.equal(stderr.text, INVALID);
});

socketTest("a connection closed before an answer exits 1", async (t) => {
  const server = await editorServer(t, (socket) => socket.end());
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(server.socketPath), stderr }), 1);
  assert.equal(stderr.text, "Raticode closed the Git editor before it finished.\n");
});

socketTest("a connection that fails exits 1 with the reason", async (t) => {
  const stderr = captureStderr();
  assert.equal(await runClient({ argv: GIT_ARGV, env: editorEnv(socketAddress(t)), stderr }), 1);
  assert.equal(stderr.lines.length, 1, "only the first failure is printed");
  // An abstract name nobody listens on is refused; a missing socket file isn't found.
  assert.match(stderr.text, /^Raticode could not open the Git editor: connect (?:ECONNREFUSED|ENOENT) .*raticode-te-\d+-\d+\n$/);
});

socketTest("run as git's editor, the script exits with the answer's status", async (t) => {
  // An environment variable can't hold an abstract socket name, so this one is a
  // socket file named relative to the folder the server and git's editor run in.
  const ownFolder = process.platform !== "win32";
  const server = await editorServer(t, (socket) => socket.end('{"ok":false,"error":"No editor tab."}\n'), { ownFolder });
  const { folder } = server;
  if (folder) assert.ok(fs.statSync(path.join(folder, server.socketPath)).isSocket());
  const run = (env) => new Promise((resolve) => {
    const child = spawn(process.execPath, [SCRIPT, ".git/COMMIT_EDITMSG"], {
      cwd: folder || undefined,
      env: { ...childEnv(), ...env },
      stdio: ["ignore", "ignore", "pipe"],
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => resolve({ status, stderr }));
  });
  assert.deepEqual(await run(editorEnv(server.socketPath)), { status: 1, stderr: "No editor tab.\n" });
  assert.equal(server.requests.length, 1);
});

test("run as git's editor without its settings, the script exits 1", () => {
  const missing = spawnSync(process.execPath, [SCRIPT, "file"], { env: childEnv(), encoding: "utf8" });
  assert.equal(missing.status, 1);
  assert.equal(missing.stderr, COULD_NOT_OPEN);
});
