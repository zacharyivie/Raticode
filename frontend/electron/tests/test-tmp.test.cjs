const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const HELPER = path.join(__dirname, "test-tmp.cjs");
const linuxOnly = { skip: process.platform === "linux" ? false : "The run folder is only used on Linux" };

function fixture(t) {
  const base = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "test-tmp-fixture-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  return base;
}

function childEnv(base) {
  const env = { ...process.env, RATICODE_TEST_TMP_BASE: base };
  for (const name of ["RATICODE_TEST_TMP", "TMPDIR", "TEMP", "TMP"]) delete env[name];
  return env;
}

const runsIn = (base) => fs.readdirSync(path.join(base, `raticode-tests-${process.getuid()}`));

// The script writes to os.tmpdir(), the way a leaking test does, and prints the folder.
// It leaves that profile behind on purpose, so its stderr holds a temp_left_behind
// line; children that aren't checked for it ignore stderr so the line stays out of this run's output.
const WRITE = `require(${JSON.stringify(HELPER)});
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-profile-"));
fs.writeFileSync(path.join(profile, "places.sqlite"), "x".repeat(1024));
console.log(os.tmpdir());
`;

function run(base, body) {
  return spawnSync(process.execPath, ["-e", WRITE + body], { env: childEnv(base), encoding: "utf8" });
}

// Start a child, wait until it has written its folder, then give it the signal.
function runUntilSignal(base, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", `${WRITE}setInterval(() => {}, 1000);`], { env: childEnv(base), stdio: ["ignore", "pipe", "ignore"] });
    let out = "";
    child.stdout.on("data", (data) => {
      out += data;
      if (out.includes("\n")) child.kill(signal);
    });
    child.on("error", reject);
    child.on("exit", (code, exitSignal) => resolve({ dir: out.trim(), code, signal: exitSignal }));
  });
}

test("A run writes under its own folder and removes it on a normal exit", linuxOnly, (t) => {
  const base = fixture(t);
  const result = run(base, "");
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.trim().startsWith(path.join(base, `raticode-tests-${process.getuid()}`)));
  assert.deepEqual(runsIn(base), []);
});

test("A failing run still removes its folder", linuxOnly, (t) => {
  const base = fixture(t);
  assert.notEqual(run(base, 'throw new Error("test failed");').status, 0);
  assert.deepEqual(runsIn(base), []);
  assert.notEqual(run(base, "process.exit(3);").status, 0);
  assert.deepEqual(runsIn(base), []);
});

for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  test(`A run stopped by ${signal} removes its folder and still dies from that signal`, linuxOnly, async (t) => {
    const base = fixture(t);
    const result = await runUntilSignal(base, signal);
    assert.equal(result.signal, signal);
    assert.equal(fs.existsSync(result.dir), false);
    assert.deepEqual(runsIn(base), []);
  });
}

test("The next run clears a run that was killed outright, but not one still running", linuxOnly, async (t) => {
  const base = fixture(t);
  const killed = await runUntilSignal(base, "SIGKILL");
  assert.equal(fs.existsSync(path.join(killed.dir)), true);

  const live = spawn(process.execPath, ["-e", `${WRITE}setInterval(() => {}, 1000);`], { env: childEnv(base), stdio: ["ignore", "pipe", "ignore"] });
  t.after(() => live.kill("SIGKILL"));
  const liveDir = await new Promise((resolve) => live.stdout.once("data", (data) => resolve(String(data).trim())));

  assert.equal(run(base, "").status, 0);
  assert.equal(fs.existsSync(killed.dir), false);
  assert.equal(fs.existsSync(path.join(liveDir)), true);
});

// The count shows a test that stopped cleaning up, even though the folder still goes.
test("A run prints how many entries its tests left behind, and nothing when they left none", linuxOnly, (t) => {
  const base = fixture(t);
  const leaky = run(base, "");
  assert.equal(leaky.status, 0, leaky.stderr);
  assert.match(leaky.stderr, /^ℹ temp_left_behind 1 \(firefox-profile-\*\)$/m);
  assert.deepEqual(runsIn(base), []);

  const failed = run(base, 'throw new Error("test failed");');
  assert.notEqual(failed.status, 0);
  assert.match(failed.stderr, /^ℹ temp_left_behind 1 \(firefox-profile-\*\)$/m);

  const clean = run(base, "fs.rmSync(profile, { recursive: true });");
  assert.equal(clean.status, 0, clean.stderr);
  assert.doesNotMatch(clean.stderr, /temp_left_behind/);
  assert.deepEqual(runsIn(base), []);
});

test("npm test's totals print the count once, even when it is 0", linuxOnly, (t) => {
  const base = fixture(t);
  const report = `const { reportLeftBehind } = require(${JSON.stringify(HELPER)});`;
  const leaky = run(base, `${report}\nreportLeftBehind((line) => process.stdout.write(line));`);
  assert.equal(leaky.status, 0, leaky.stderr);
  assert.match(leaky.stdout, /^ℹ temp_left_behind 1 \(firefox-profile-\*\)$/m);
  assert.doesNotMatch(leaky.stderr, /temp_left_behind/);

  const clean = run(base, `${report}\nfs.rmSync(profile, { recursive: true });\nprocess.stdout.write("count " + reportLeftBehind((line) => process.stdout.write(line)) + "\\n");`);
  assert.equal(clean.status, 0, clean.stderr);
  assert.match(clean.stdout, /^ℹ temp_left_behind 0$/m);
  assert.match(clean.stdout, /^count 0$/m);
  assert.deepEqual(runsIn(base), []);
});

// npm test stops its files on Ctrl+C and prints its totals after the folder is gone.
test("A stopped run prints the real count while removing its folder, and its totals don't print 0 after", linuxOnly, async (t) => {
  const base = fixture(t);
  const script = `process.on("SIGTERM", () => setTimeout(() => {
  process.stdout.write("totals " + reportLeftBehind((line) => process.stdout.write(line)) + "\\n");
  process.exit(1);
}, 10));
${WRITE}const { reportLeftBehind } = require(${JSON.stringify(HELPER)});
setInterval(() => {}, 1000);`;
  const result = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", script], { env: childEnv(base), stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (data) => {
      if (!stdout) child.kill("SIGTERM");
      stdout += data;
    });
    child.stderr.on("data", (data) => { stderr += data; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
  assert.equal(result.code, 1, result.stderr);
  assert.match(result.stderr, /^ℹ temp_left_behind 1 \(firefox-profile-\*\)$/m);
  assert.match(result.stdout, /^totals null$/m);
  assert.doesNotMatch(result.stdout, /temp_left_behind/);
  assert.deepEqual(runsIn(base), []);
});

test("A child sharing its parent's folder doesn't print a count; its parent does", linuxOnly, (t) => {
  const base = fixture(t);
  const child = `const { reportLeftBehind } = require(${JSON.stringify(HELPER)});
fs.writeFileSync(path.join(os.tmpdir(), "child-leftover"), "");
process.stdout.write("count " + reportLeftBehind() + "\\n");`;
  const parent = `const { spawnSync } = require("node:child_process");
const result = spawnSync(process.execPath, ["-e", ${JSON.stringify(WRITE)} + ${JSON.stringify(child)}], { encoding: "utf8" });
if (!/^count null$/m.test(result.stdout) || /temp_left_behind/.test(result.stderr)) throw new Error("child printed a count: " + result.stdout + result.stderr);
`;
  const result = run(base, parent);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /^ℹ temp_left_behind 3 \(firefox-profile-\* ×2, child-leftover\)$/m);
});

test("Leftovers are grouped by name without their random ending, most common first", () => {
  const { leftBehindLine } = require(HELPER);
  assert.equal(leftBehindLine([]), "ℹ temp_left_behind 0\n");
  assert.equal(
    leftBehindLine(["firefox-port-test-a1B2c3", "Downloads", "browser-sessions-Zz9y8x", "browser-sessions-q1w2e3"]),
    "ℹ temp_left_behind 4 (browser-sessions-* ×2, Downloads, firefox-port-test-*)\n",
  );
  const many = Array.from({ length: 10 }, (_, index) => `kind${index}-abcdef`);
  assert.match(leftBehindLine(many), /^ℹ temp_left_behind 10 \(kind0-\*, .*kind7-\*, 2 more kinds\)\n$/);
});

test("A child shares its parent's folder and leaves it in place", linuxOnly, (t) => {
  const base = fixture(t);
  const parent = `const { spawnSync } = require("node:child_process");
const child = spawnSync(process.execPath, ["-e", ${JSON.stringify(WRITE)}], { encoding: "utf8" });
if (child.stdout.trim() !== os.tmpdir()) throw new Error("child used " + child.stdout);
if (!fs.existsSync(os.tmpdir())) throw new Error("child removed the shared folder");
`;
  const result = run(base, parent);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(runsIn(base), []);
});

// The run folder's place in the normal temp folder, where a run goes when its base is refused.
const fallbackRuns = (temp) => path.join(temp, `raticode-tests-${process.getuid()}`);

test("A parent folder another name points at is refused, and the run uses a folder in the normal temp folder instead", linuxOnly, (t) => {
  const base = fixture(t);
  const elsewhere = fs.mkdtempSync(path.join(base, "elsewhere-"));
  fs.symlinkSync(elsewhere, path.join(base, `raticode-tests-${process.getuid()}`));
  const normalTemp = fs.mkdtempSync(path.join(base, "normal-temp-"));
  const result = spawnSync(process.execPath, ["-e", WRITE], { env: { ...childEnv(base), TMPDIR: normalTemp }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /not a private folder/);
  assert.deepEqual(fs.readdirSync(elsewhere), []);
  assert.equal(path.dirname(result.stdout.trim()), fallbackRuns(normalTemp));
  assert.deepEqual(fs.readdirSync(fallbackRuns(normalTemp)), []);
});

// Inside an agent's sandbox /var/tmp is read-only. The run used to fall back to the
// sandbox's RAM-backed temp folder with no cleanup, and its browser profiles piled up.
test("A run whose base can't be written still removes everything it wrote to the normal temp folder", linuxOnly, async (t) => {
  const base = fixture(t);
  const readOnly = fs.mkdtempSync(path.join(base, "read-only-"));
  fs.chmodSync(readOnly, 0o500);
  const normalTemp = fs.mkdtempSync(path.join(base, "normal-temp-"));
  const env = { ...childEnv(readOnly), TMPDIR: normalTemp };

  const result = spawnSync(process.execPath, ["-e", WRITE], { env, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(path.dirname(result.stdout.trim()), fallbackRuns(normalTemp));
  assert.deepEqual(fs.readdirSync(normalTemp), [`raticode-tests-${process.getuid()}`]);
  assert.deepEqual(fs.readdirSync(fallbackRuns(normalTemp)), []);

  const failed = spawnSync(process.execPath, ["-e", `${WRITE}throw new Error("test failed");`], { env, encoding: "utf8" });
  assert.notEqual(failed.status, 0);
  assert.deepEqual(fs.readdirSync(fallbackRuns(normalTemp)), []);

  const stopped = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", `${WRITE}setInterval(() => {}, 1000);`], { env, stdio: ["ignore", "pipe", "ignore"] });
    child.stdout.once("data", () => child.kill("SIGTERM"));
    child.on("error", reject);
    child.on("exit", (code, signal) => resolve(signal));
  });
  assert.equal(stopped, "SIGTERM");
  assert.deepEqual(fs.readdirSync(fallbackRuns(normalTemp)), []);
});

// A sandbox can number processes on its own, so a run only clears dead runs from its own namespace.
test("A dead-looking run from another process namespace is left for Raticode to clear", linuxOnly, (t) => {
  const base = fixture(t);
  const runs = path.join(base, `raticode-tests-${process.getuid()}`);
  fs.mkdirSync(runs, { mode: 0o700 });
  const namespace = Number(/\[(\d+)\]/.exec(fs.readlinkSync("/proc/self/ns/pid"))[1]).toString(36);
  const dead = spawnSync(process.execPath, ["-e", "process.stdout.write(String(process.pid))"], { encoding: "utf8" }).stdout;
  const other = path.join(runs, `nzzzzzz-r${dead}-abcdef`);
  const ours = path.join(runs, `n${namespace}-r${dead}-abcdef`);
  for (const dir of [other, ours]) fs.mkdirSync(dir);

  assert.equal(run(base, "").status, 0);
  assert.equal(fs.existsSync(other), true);
  assert.equal(fs.existsSync(ours), false);
});

// Every test that makes a temporary folder must either remove each one or load the
// shared run folder first, so new tests can't bring the leak back.
test("Tests that create temporary folders clean them up", () => {
  const frontend = path.join(__dirname, "..", "..");
  const files = [path.join(frontend, "browser-tests"), __dirname]
    .flatMap((dir) => fs.readdirSync(dir).filter((name) => name.endsWith(".cjs")).map((name) => path.join(dir, name)))
    .filter((file) => file !== __filename);
  const leaking = files.filter((file) => {
    const source = fs.readFileSync(file, "utf8");
    const created = (source.match(/mkdtemp(Sync)?\(/g) || []).length;
    const removed = (source.match(/\b(rmSync|rm)\(/g) || []).length;
    if (created <= removed) return false;
    const helper = source.search(/require\(["']\.[./a-z-]*\/(test-tmp|background-display)\.cjs["']\)/);
    return helper < 0 || helper > source.search(/mkdtemp(Sync)?\(/);
  });
  assert.deepEqual(leaking.map((file) => path.relative(frontend, file)), []);
});
