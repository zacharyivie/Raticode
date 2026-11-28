require("./test-tmp.cjs");
// The per-test temporary folder helper: removed when the test ends, pass or fail,
// with any Firefox engine using it stopped first.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { stopBeforeRemoving, tempFolder } = require("./temp-folder.cjs");

const HELPER = path.join(__dirname, "temp-folder.cjs");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A stand-in for a Firefox engine that writes its profile while it shuts down.
function shutdownWriter(folder, order, name = "engine") {
  return {
    async stopAndWait() {
      await delay(20);
      assert.equal(fs.existsSync(folder), true, `${name} stopped after its folder was removed`);
      fs.writeFileSync(path.join(folder, `${name}-sessionstore.jsonlz4`), "x");
      order.push(name);
    },
  };
}

test("A folder made with the test's context is gone once that test ends", async (t) => {
  let folder = "";
  await t.test("makes a folder", (inner) => {
    folder = tempFolder("temp-folder-context-", inner);
    fs.writeFileSync(path.join(folder, "profile.sqlite"), "x".repeat(1024));
    assert.equal(path.dirname(folder), os.tmpdir());
  });
  assert.equal(fs.existsSync(folder), false);
});

test("A folder a helper makes without the test's context is gone when the calling test ends, not at the end of the file", async (t) => {
  let folder = "";
  const harness = () => ({ directory: tempFolder("temp-folder-harness-") });
  await t.test("calls a harness", async () => {
    await delay(5);
    folder = harness().directory;
    assert.equal(fs.existsSync(folder), true);
  });
  assert.equal(fs.existsSync(folder), false);
});

test("Engines stop, last started first, and finish writing before their folder is removed", async (t) => {
  let folder = "";
  const order = [];
  await t.test("starts two engines", (inner) => {
    folder = tempFolder("temp-folder-engines-", inner);
    stopBeforeRemoving(folder, shutdownWriter(folder, order, "first"));
    const second = shutdownWriter(folder, order, "second");
    assert.equal(stopBeforeRemoving(folder, second), second);
  });
  assert.deepEqual(order, ["second", "first"]);
  assert.equal(fs.existsSync(folder), false);
});

test("An engine with only close() or stop() is stopped too", async (t) => {
  const calls = [];
  let folder = "";
  await t.test("uses both kinds", (inner) => {
    folder = tempFolder("temp-folder-kinds-", inner);
    stopBeforeRemoving(folder, { close: () => calls.push("close") });
    stopBeforeRemoving(folder, { stop: () => calls.push("stop") });
  });
  assert.deepEqual(calls, ["stop", "close"]);
  assert.equal(fs.existsSync(folder), false);
});

test("A folder the helper didn't make, or already removed, can't take an engine", async (t) => {
  assert.throws(() => stopBeforeRemoving(os.tmpdir(), { stop() {} }), /not a folder from tempFolder/);
  let folder = "";
  await t.test("makes a folder", (inner) => { folder = tempFolder("temp-folder-done-", inner); });
  assert.throws(() => stopBeforeRemoving(folder, { stop() {} }), /not a folder from tempFolder/);
});

// A failing test, a test whose engine fails to stop and a folder made at the top of a
// file all run in a child, since each of them fails or outlives a test here.
const CHILD = `const test = require("node:test");
const fs = require("node:fs");
const { stopBeforeRemoving, tempFolder } = require(${JSON.stringify(HELPER)});
const top = tempFolder("temp-folder-top-");
console.log("MADE " + top);
test("fails", (t) => {
  const folder = tempFolder("temp-folder-failed-", t);
  console.log("MADE " + folder);
  fs.writeFileSync(folder + "/cookies.sqlite", "x");
  throw new Error("the test failed");
});
test("engine fails to stop", () => {
  const folder = tempFolder("temp-folder-stuck-");
  console.log("MADE " + folder);
  stopBeforeRemoving(folder, { async stopAndWait() { throw new Error("Firefox did not stop"); } });
});
test("top folder is still there", () => {
  if (!fs.existsSync(top)) throw new Error("the file's folder went before its tests ended");
});
`;

test("Folders go after a failed test, an engine that fails to stop, and at the end of the file, and the failures still show", (t) => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "temp-folder-child-"));
  t.after(() => fs.rmSync(scratch, { recursive: true, force: true }));
  const file = path.join(scratch, "child.test.cjs");
  fs.writeFileSync(file, CHILD);
  // A child that sees this run's test context would skip its own files.
  const env = { ...process.env, NO_COLOR: "1" };
  for (const name of ["NODE_TEST_CONTEXT", "FORCE_COLOR"]) delete env[name];
  const result = spawnSync(process.execPath, ["--test", "--test-reporter=tap", file], { encoding: "utf8", env });
  const output = result.stdout + result.stderr;
  const folders = [...output.matchAll(/MADE (\S+)/g)].map((match) => match[1]);
  assert.equal(new Set(folders).size, 3, output);
  for (const folder of folders) assert.equal(fs.existsSync(folder), false, `${folder} was left behind\n${output}`);
  assert.notEqual(result.status, 0, output);
  assert.match(output, /the test failed/);
  assert.match(output, /Firefox did not stop/);
  assert.match(output, /# pass 1\b/, output);
  assert.match(output, /# fail 2\b/, output);
});
