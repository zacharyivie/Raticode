const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createChromiumStderrFilter } = require("../chromium-stderr.cjs");
const { createAppLog, redactLog } = require("../app-log.cjs");
const os = require("node:os");

test("log redaction covers token fields in JSON, environments and URL queries", async t => {
  const messages = [
    JSON.stringify({ token: "private-credential", refresh_token: "private-refresh" }),
    'api_token="private-credential with spaces" status=failed',
    "GOFER_UI_API_TOKEN=private-credential",
    "RATICODE_REPORT_PDF_TOKEN=private-credential",
    "request failed https://example.test/callback?token=private-credential&state=ready",
  ];
  for (const message of messages) {
    assert.ok(!redactLog(message).includes("private-"), redactLog(message));
  }
  assert.equal(redactLog("input_tokens=123 output_tokens=456"), "input_tokens=123 output_tokens=456");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-log-tokens-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const log = createAppLog(root);
  for (const message of messages) log.write("warn", "backend", message);
  await log.close();
  assert.ok(!fs.readFileSync(log.file, "utf8").includes("private-"));
});

test("log redaction removes complete HTTP credentials and quoted secrets", async () => {
  const messages = [
    "Authorization: Basic dXNlcjpwYXNz+/==",
    "Proxy-Authorization: Basic dXNlcjpwYXNz+/==",
    "Authorization: Bearer token/with+symbols~==",
    "Bearer token/with+symbols~==",
    "Authorization: Bearer\n token/with+symbols~==",
    JSON.stringify({ authorization: "Basic dXNlcjpwYXNz+/==" }),
    JSON.stringify({ password: 'words with "escaped quotes" and spaces' }),
    "secret='words with spaces' status=failed",
    'password="words with spaces',
    "secret='words with spaces",
    'password="words with spaces\\',
  ];
  for (const message of messages) {
    const redacted = redactLog(message);
    assert.ok(!/dXNlcj|pwYXNz|token|with|symbols|words|escaped|quotes|spaces/.test(redacted), redacted);
    assert.ok(redacted.includes("[redacted]"));
  }
  assert.equal(redactLog("basic operation failed status=503"), "basic operation failed status=503");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-log-credentials-"));
  try {
    const log = createAppLog(root);
    for (const message of messages) log.write("warn", "backend", message);
    await log.close();
    const entries = fs.readFileSync(log.file, "utf8").trim().split("\n").map(line => JSON.parse(line));
    assert.deepEqual(entries.map(entry => entry.message), messages.map(redactLog));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("log redaction removes the full URL userinfo through the last authority separator", async t => {
  const messages = [
    "request failed https://private-user:private-first@private-rest@example.test/report",
    "request failed HTTPS://private-user:private-password@example.test/report",
    "request failed https://private-user@example.test/report",
    "request failed https://private-user:private%40password@[::1]:8765/report",
  ];
  const expected = [
    "request failed https://[redacted]@example.test/report",
    "request failed HTTPS://[redacted]@example.test/report",
    "request failed https://[redacted]@example.test/report",
    "request failed https://[redacted]@[::1]:8765/report",
  ];
  assert.deepEqual(messages.map(redactLog), expected);
  for (const url of [
    "https://example.test/users/contact@example.test",
    "https://example.test/?email=contact@example.test",
    "https://example.test/#contact@example.test",
  ]) assert.equal(redactLog(url), url);
  const separateFields = JSON.stringify({ url: "https://example.test", email: "contact@example.test" });
  assert.equal(redactLog(separateFields), separateFields);

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-log-userinfo-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const log = createAppLog(root);
  for (const message of messages) log.write("warn", "backend", message);
  await log.close();
  const entries = fs.readFileSync(log.file, "utf8").trim().split("\n").map(line => JSON.parse(line));
  assert.deepEqual(entries.map(entry => entry.message), expected);
});

const prefix = "[552746:0910/164107.771391:ERROR:components/viz/service/display/display.cc:298] ";
const noise = `${prefix}Frame latency is negative: -0.011 ms\n`;

async function filter(chunks) {
  const stream = createChromiumStderrFilter();
  let output = "";
  stream.setEncoding("utf8");
  const done = (async () => { for await (const chunk of stream) output += chunk; })();
  for (const chunk of chunks) stream.write(chunk);
  stream.end();
  await done;
  return output;
}

test("native stderr filters only tiny negative frame timing samples across chunks", async () => {
  const other = `${prefix}GPU process crashed\n`;
  const large = `${prefix}Frame latency is negative: -1 ms\n`;
  const unrelated = "Application error: Frame latency is negative: -0.011 ms\n";
  assert.equal(await filter([other + noise.slice(0, 60), noise.slice(60) + large + unrelated]), other + large + unrelated);
  assert.equal(await filter([noise.trimEnd()]), "");
  assert.equal(await filter([noise.replace("\n", "\r\n")]), "");
});

test("native stderr preserves UTF-8, incomplete lines and long output", async () => {
  const bytes = Buffer.from("warning: café");
  assert.equal(await filter([bytes.subarray(0, bytes.length - 1), bytes.subarray(bytes.length - 1)]), "warning: café");
  const long = "x".repeat(70000);
  assert.equal(await filter([long, "tail\n"]), long + "tail\n");
});

test("console handler uses one event argument and records studio warnings/errors", () => {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const start = source.indexOf('  app.on("web-contents-created",');
  const end = source.indexOf('  app.on("render-process-gone",', start);
  let register;
  const writes = [];
  const mainContents = {};
  const errorContents = {};
  vm.runInNewContext(source.slice(start, end), {
    app: { on: (_name, listener) => { register = listener; } },
    mainWindow: { webContents: mainContents },
    backendErrorWindow: { webContents: errorContents },
    applicationLog: { write: (...args) => writes.push(args) },
  });
  for (const contents of [mainContents, errorContents, {}]) {
    let handler;
    contents.on = (_name, listener) => { handler = listener; };
    register({}, contents);
    assert.equal(handler.length, 1, "Electron detects legacy listeners by argument count");
    for (const level of ["info", "warning", "error"]) handler({ level, message: level });
  }
  assert.deepEqual(writes, [
    ["warn", "renderer", "warning"], ["error", "renderer", "error"],
    ["warn", "renderer", "warning"], ["error", "renderer", "error"],
  ]);
});
