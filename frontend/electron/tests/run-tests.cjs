// Runs the frontend test suite (`npm test`) on several cores.
//
// The unit tests share one process (--experimental-test-isolation=none), as they always
// have. The files listed in SEPARATE drive real Firefox, Chrome and Edge tabs, the media
// bar and real git repositories, and take most of the run's time, so each one runs in its
// own process alongside the unit tests, a few at a time. Each process's output is printed
// in one piece when it finishes, followed by the totals for the whole run.
//
// RATICODE_TEST_JOBS sets how many separate files run at once. The default is 2 (1 on a
// two-core machine): with the unit process that fits the nightly's 4 GB memory cap even if
// each browser file needs about 1 GB. Inside a capped scope the run ends with its memory
// peak (ℹ memory_peak_mib), which shows whether more would fit. `npm run test:serial`
// runs everything in one process.
//
// Every process shares one temp folder for the run (test-tmp.cjs), removed when the run
// ends, even after a failure. The totals end with how many entries the tests left in it
// (ℹ temp_left_behind), so a test that stops cleaning up shows.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { dropInheritedSettings } = require("./test-env.cjs");

const FRONTEND = path.join(__dirname, "..", "..");

// Slowest first, so the longest file starts at once and the short ones fill the gaps.
// Times are from the 13 November 2026 nightly run, when all of these ran in one process.
// The agent guards took 84 s then, about 60 s of it a Firefox tab waiting for a frame that
// never loaded; since 16 November its Firefox part takes about 20 s.
const SEPARATE = [
  "electron/tests/chrome-signin.test.cjs", // 36 s
  "electron/git-efficiency.test.cjs", // git integration: 35 s
  "electron/tests/stream-tab-media.test.cjs", // media bar controls, three engines: 28 s
  "electron/tests/stream-agent-guards.test.cjs", // Firefox, Chrome and Edge tabs: about 26 s
  "electron/tests/stream-tab-sound.test.cjs", // media bar sound, three engines: 26 s
  "electron/tests/stream-agent-actions.test.cjs", // 17 s
  "electron/tests/firefox-browser.test.cjs", // 15 s
  "electron/tests/gecko-rem-control.test.cjs", // real windowed Firefox on its own Xvfb: 8 s
  "electron/tests/firefox-settings.test.cjs", // 14 s
  "electron/tests/branch-targets.test.cjs", // git: 9 s
  "electron/tests/firefox-signin.test.cjs", // 7 s
  "electron/tests/edge-browser.test.cjs", // 7 s
  "electron/tests/chrome-browser.test.cjs", // 6 s
  "electron/tests/git-watch.test.cjs", // 6 s
  "electron/tests/git-preview-security.test.cjs", // 3 s
  "electron/tests/terminal-find.test.cjs", // real Electron, xterm and search addon: 3 s
  "electron/tests/terminal-links.test.cjs", // real Electron and xterm, trusted mouse input: 3 s
];

const NODE_TEST_ARGS = ["--require", "./electron/tests/test-tmp.cjs", "--test", "--experimental-test-isolation=none", "--test-reporter=spec"];
const COUNTS = ["tests", "suites", "pass", "fail", "cancelled", "skipped", "todo"];

function testsIn(dir, pattern) {
  return fs.readdirSync(path.join(FRONTEND, dir)).filter((name) => pattern.test(name)).sort().map((name) => `${dir}/${name}`);
}

// The same files `npm run test:serial` runs.
function allTestFiles() {
  return [
    "src/pages/App.test.mjs",
    ...testsIn("src/lib", /\.test\.mjs$/),
    ...testsIn("electron/tests", /\.test\.cjs$/),
    "electron/git-efficiency.test.cjs",
  ];
}

function plan(files = allTestFiles()) {
  const missing = SEPARATE.filter((file) => !files.includes(file));
  if (missing.length) throw new Error(`Not a test file any more, update SEPARATE in run-tests.cjs: ${missing.join(", ")}`);
  return { unit: files.filter((file) => !SEPARATE.includes(file)), separate: [...SEPARATE] };
}

function defaultJobs(env = process.env) {
  const asked = Number.parseInt(env.RATICODE_TEST_JOBS || "", 10);
  if (asked > 0) return asked;
  return Math.max(1, Math.min(2, os.availableParallelism() - 1));
}

// Totals from the spec reporter's closing lines, such as "ℹ pass 1669".
function summaryOf(output) {
  // eslint-disable-next-line no-control-regex
  const plain = output.replace(/\x1b\[[0-9;]*m/g, "");
  const counts = {};
  for (const name of COUNTS) {
    const match = new RegExp(`^ℹ ${name} (\\d+)$`, "m").exec(plain);
    if (match) counts[name] = Number(match[1]);
  }
  return counts;
}

// The capped scope's peak memory, when this run is inside one, such as the nightly's
// `systemd-run --scope -p MemoryMax=4G`.
function cgroupMemory() {
  try {
    const entry = fs.readFileSync("/proc/self/cgroup", "utf8").split("\n").find((line) => line.startsWith("0::"));
    const dir = path.join("/sys/fs/cgroup", entry.slice(3));
    const max = fs.readFileSync(path.join(dir, "memory.max"), "utf8").trim();
    if (max === "max") return null;
    return { peak: Number(fs.readFileSync(path.join(dir, "memory.peak"), "utf8")), max: Number(max) };
  } catch {
    return null;
  }
}

function main() {
  const started = Date.now();
  const { unit, separate } = plan();
  const jobs = defaultJobs();
  const running = new Set();
  const results = [];
  let stopping = false;

  // On Ctrl+C or a timeout, show what the unfinished files printed and stop them.
  const stop = (signal) => {
    if (stopping) return;
    stopping = true;
    process.stdout.write(`\nStopped by ${signal}. Still running: ${[...running].map((lane) => lane.name).join(", ") || "nothing"}.\n`);
    for (const lane of running) {
      process.stdout.write(`\n── ${lane.name} (unfinished) ──\n${lane.output}`);
      lane.child.kill("SIGTERM");
    }
    process.exitCode = 1;
  };
  process.on("SIGINT", () => stop("SIGINT"));
  process.on("SIGTERM", () => stop("SIGTERM"));
  // After the handlers above, so they run before it removes the run's folder. Every
  // test process shares that folder through RATICODE_TEST_TMP.
  const { reportLeftBehind } = require("./test-tmp.cjs");
  // Without Raticode's push block, commit hook and app settings (test-env.cjs).
  const env = dropInheritedSettings({ ...process.env });
  if (process.stdout.isTTY && !env.NO_COLOR && !env.FORCE_COLOR) env.FORCE_COLOR = "1";

  const report = (lane) => {
    const seconds = ((lane.ended - lane.started) / 1000).toFixed(1);
    process.stdout.write(`\n── ${lane.name} (${seconds} s, exit ${lane.code ?? lane.signal}) ──\n${lane.output}`);
  };

  const run = (name, files) => new Promise((resolve) => {
    const lane = { name, output: "", started: Date.now(), code: null, signal: null };
    const child = spawn(process.execPath, [...NODE_TEST_ARGS, ...files], { cwd: FRONTEND, env, stdio: ["ignore", "pipe", "pipe"] });
    lane.child = child;
    running.add(lane);
    child.stdout.on("data", (chunk) => { lane.output += chunk; });
    child.stderr.on("data", (chunk) => { lane.output += chunk; });
    child.on("error", (error) => { lane.output += `\n${error.stack || error}\n`; });
    child.on("close", (code, signal) => {
      Object.assign(lane, { code, signal, ended: Date.now() });
      running.delete(lane);
      results.push(lane);
      if (!stopping) report(lane);
      resolve(lane);
    });
  });


  const queue = [...separate];
  const worker = async () => {
    while (queue.length && !stopping) {
      const file = queue.shift();
      await run(file, [file]);
    }
  };
  process.stdout.write(`Running ${unit.length} unit test files in one process and ${separate.length} slow files in their own processes, ${jobs} at a time.\n`);
  return Promise.all([run("unit tests", unit), ...Array.from({ length: jobs }, worker)]).then(() => {
    const totals = Object.fromEntries(COUNTS.map((name) => [name, 0]));
    const failed = [];
    for (const lane of results) {
      const counts = summaryOf(lane.output);
      for (const name of COUNTS) totals[name] += counts[name] || 0;
      if (lane.code !== 0) failed.push(lane.name);
    }
    process.stdout.write("\n── All frontend tests ──\n");
    for (const name of COUNTS) process.stdout.write(`ℹ ${name} ${totals[name]}\n`);
    process.stdout.write(`ℹ duration_ms ${Date.now() - started}\n`);
    const memory = cgroupMemory();
    if (memory) process.stdout.write(`ℹ memory_peak_mib ${Math.round(memory.peak / 2 ** 20)} of ${Math.round(memory.max / 2 ** 20)}\n`);
    // What the tests left in the run's temp folder, which is removed right after.
    reportLeftBehind((line) => process.stdout.write(line));
    if (failed.length) process.stdout.write(`✖ Failed: ${failed.join(", ")}\n`);
    if (failed.length || stopping) process.exitCode = 1;
  });
}

if (require.main === module) main();

module.exports = { SEPARATE, allTestFiles, plan, defaultJobs, summaryOf };
