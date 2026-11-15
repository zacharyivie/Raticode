const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { SEPARATE, allTestFiles, plan, defaultJobs, summaryOf } = require("./run-tests.cjs");

const frontend = path.join(__dirname, "..", "..");

test("npm test runs every test file exactly once, the slow ones in their own processes", () => {
  const files = allTestFiles();
  const { unit, separate } = plan(files);
  assert.deepEqual([...unit, ...separate].sort(), [...files].sort());
  assert.equal(new Set([...unit, ...separate]).size, files.length);
  assert.ok(unit.includes("src/pages/App.test.mjs"));
  assert.ok(unit.includes("electron/tests/run-tests.test.cjs"));
  for (const file of SEPARATE) assert.ok(fs.existsSync(path.join(frontend, file)), file);
});

test("a slow file that was renamed or removed stops the run instead of being skipped", () => {
  assert.throws(() => plan(allTestFiles().filter((file) => file !== SEPARATE[0])), /update SEPARATE/);
});

test("npm test uses the runner and test:serial keeps the one-process command for the same files", () => {
  const scripts = JSON.parse(fs.readFileSync(path.join(frontend, "package.json"), "utf8")).scripts;
  assert.equal(scripts.test, "node electron/tests/run-tests.cjs");
  assert.match(scripts["test:serial"], /--experimental-test-isolation=none/);
  for (const pattern of ["src/pages/App.test.mjs", "src/lib/*.test.mjs", "electron/tests/*.test.cjs", "electron/git-efficiency.test.cjs"]) {
    assert.ok(scripts["test:serial"].includes(pattern), pattern);
  }
});

test("RATICODE_TEST_JOBS sets how many slow files run at once", () => {
  assert.equal(defaultJobs({ RATICODE_TEST_JOBS: "5" }), 5);
  assert.ok(defaultJobs({}) >= 1 && defaultJobs({}) <= 2);
  assert.equal(defaultJobs({ RATICODE_TEST_JOBS: "0" }), defaultJobs({}));
});

test("the totals come from each process's closing summary", () => {
  const output = "✔ one (1ms)\nℹ tests 12\nℹ suites 0\nℹ pass 10\nℹ fail 1\nℹ cancelled 0\nℹ skipped 1\nℹ todo 0\nℹ duration_ms 5\n";
  assert.deepEqual(summaryOf(output), { tests: 12, suites: 0, pass: 10, fail: 1, cancelled: 0, skipped: 1, todo: 0 });
  assert.deepEqual(summaryOf("crashed before any summary"), {});
  // With FORCE_COLOR set, the reporter colors its summary lines.
  assert.deepEqual(summaryOf("\x1b[34mℹ tests 3\x1b[39m\n\x1b[34mℹ fail 0\x1b[39m\n"), { tests: 3, fail: 0 });
});
