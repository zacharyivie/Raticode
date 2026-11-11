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
    const child = spawn(process.execPath, ["-e", `${WRITE}setInterval(() => {}, 1000);`], { env: childEnv(base), stdio: ["ignore", "pipe", "inherit"] });
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

  const live = spawn(process.execPath, ["-e", `${WRITE}setInterval(() => {}, 1000);`], { env: childEnv(base), stdio: ["ignore", "pipe", "inherit"] });
  t.after(() => live.kill("SIGKILL"));
  const liveDir = await new Promise((resolve) => live.stdout.once("data", (data) => resolve(String(data).trim())));

  assert.equal(run(base, "").status, 0);
  assert.equal(fs.existsSync(killed.dir), false);
  assert.equal(fs.existsSync(path.join(liveDir)), true);
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

test("A parent folder another name points at is refused, and the run keeps the normal temp folder", linuxOnly, (t) => {
  const base = fixture(t);
  const elsewhere = fs.mkdtempSync(path.join(base, "elsewhere-"));
  fs.symlinkSync(elsewhere, path.join(base, `raticode-tests-${process.getuid()}`));
  const normalTemp = fs.mkdtempSync(path.join(base, "normal-temp-"));
  const result = spawnSync(process.execPath, ["-e", WRITE], { env: { ...childEnv(base), TMPDIR: normalTemp }, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /not a private folder/);
  assert.deepEqual(fs.readdirSync(elsewhere), []);
  assert.equal(result.stdout.trim(), normalTemp);
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
