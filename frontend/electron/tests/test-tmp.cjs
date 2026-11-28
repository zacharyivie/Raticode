// Gives each test run one temporary folder and removes it when the run ends.
//
// Tests create browser profiles and app builds of 100 MB or more, and /tmp is often
// RAM-backed, so leftovers can fill it and take memory from everything else. Requiring
// this file points TMPDIR at a folder for this run, so os.tmpdir(), child Electron and
// browser processes and Python test servers all write there. The folder is removed on
// exit, a crash or a termination signal. A run that was killed outright is cleared by
// the next run. Set RATICODE_TEST_TMP_BASE to use a different parent folder.
//
// The folder goes in /var/tmp, which is on disk. Where /var/tmp can't be written, such
// as inside an agent's sandbox, it goes in the normal temp folder instead and is still
// removed at the end. Raticode itself clears what a killed run leaves there.
//
// Before removing the folder, the run counts what its tests left in it and prints
// "ℹ temp_left_behind N" with the most common names, so a test that stops cleaning
// up shows in the output. `npm test` prints the line with its totals, even when it is 0.
//
// It also drops the git and app settings tests would inherit from Raticode
// (test-env.cjs), so a test file that requires this gets a clean environment even
// when run on its own with `node --test`.
require("./test-env.cjs").dropInheritedSettings();
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const RUN_DIR_ENV = "RATICODE_TEST_TMP";
// n<pid namespace>-r<pid>-, or r<pid>- from runs before the namespace was recorded.
const RUN_NAME = /^(?:n([0-9a-z]+)-)?r(\d+)-/;

// Other accounts can create names in /var/tmp, so never trust one we didn't make.
function privateDir(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const info = fs.lstatSync(dir);
  if (!info.isDirectory() || info.uid !== process.getuid()) throw new Error(`${dir} is not a private folder`);
  fs.chmodSync(dir, 0o700);
  return dir;
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

// A sandbox can give each command its own process numbers, so a number that looks
// dead here may be a live run in another sandbox. Runs are named by namespace, and a
// run is only judged by a process in the same one.
function pidNamespace() {
  try {
    const match = /\[(\d+)\]/.exec(fs.readlinkSync("/proc/self/ns/pid"));
    return match ? Number(match[1]).toString(36) : "";
  } catch {
    return "";
  }
}

function clearDeadRuns(base, namespace) {
  for (const name of fs.readdirSync(base)) {
    const match = RUN_NAME.exec(name);
    if (!match || (match[1] !== undefined && (!namespace || match[1] !== namespace))) continue;
    if (!pidAlive(Number(match[2]))) fs.rmSync(path.join(base, name), { recursive: true, force: true });
  }
}

function runFolder(parent) {
  const base = privateDir(path.join(parent, `raticode-tests-${process.getuid()}`));
  const namespace = pidNamespace();
  clearDeadRuns(base, namespace);
  return fs.mkdtempSync(path.join(base, `${namespace ? `n${namespace}-` : ""}r${process.pid}-`));
}

// The run folder this process made, and whether its count was printed or it is gone.
let owned = null;

// "firefox-port-test-a1B2c3" and "firefox-port-test-Z9y8x7" are one kind of leftover.
function leftBehindLine(names) {
  const kinds = new Map();
  for (const name of names) {
    const kind = name.replace(/([-_.])[A-Za-z0-9]{6}$/, "$1*");
    kinds.set(kind, (kinds.get(kind) || 0) + 1);
  }
  const common = [...kinds].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const shown = common.slice(0, 8).map(([kind, count]) => (count > 1 ? `${kind} ×${count}` : kind));
  if (common.length > shown.length) shown.push(`${common.length - shown.length} more kinds`);
  return `ℹ temp_left_behind ${names.length}${shown.length ? ` (${shown.join(", ")})` : ""}\n`;
}

function leftBehind(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

// Prints the count for this process's run folder, once. Returns the count, or null
// when this process doesn't own a run folder or it was already removed, as after
// Ctrl+C, when removing it printed the count first.
function reportLeftBehind(write = (line) => fs.writeSync(2, line)) {
  if (!owned || owned.removed) return null;
  const names = leftBehind(owned.dir);
  owned.reported = true;
  try {
    write(leftBehindLine(names));
  } catch {
    // Output closed: the count is only a hint.
  }
  return names.length;
}

function installTestTmp(env = process.env) {
  if (process.platform !== "linux") return "";
  // A child of a test that already owns a folder shares it and leaves removal to its parent.
  if (env[RUN_DIR_ENV]) return env[RUN_DIR_ENV];
  const temp = env.TMPDIR || env.TMP || env.TEMP || os.tmpdir();
  let dir = "";
  for (const parent of [...new Set([env.RATICODE_TEST_TMP_BASE || "/var/tmp", temp])]) {
    try {
      dir = runFolder(parent);
      break;
    } catch (error) {
      console.warn(`Tests can't use ${parent} for their run folder: ${error.message}`);
    }
  }
  if (!dir) {
    console.warn(`Tests will use ${temp} without a run folder`);
    return "";
  }
  Object.assign(env, { [RUN_DIR_ENV]: dir, TMPDIR: dir, TEMP: dir, TMP: dir });
  owned = { dir, reported: false, removed: false };
  const remove = () => {
    if (owned?.dir === dir) {
      if (!owned.reported && leftBehind(dir).length) reportLeftBehind();
      owned.removed = true;
    }
    fs.rmSync(dir, { recursive: true, force: true });
  };
  process.once("exit", remove);
  if (process.type === "browser") {
    // Electron's main process skips the "exit" event when told to exit immediately.
    const electron = require("electron");
    const app = electron.app;
    const exit = app.exit.bind(app);
    app.exit = (code) => {
      remove();
      exit(code);
    };
    process.exit = app.exit;
  }
  for (const signal of ["SIGHUP", "SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      remove();
      // Leave the shutdown to any other handler; otherwise die from the same signal.
      if (process.listenerCount(signal) === 0) process.kill(process.pid, signal);
    });
  }
  return dir;
}

installTestTmp();

module.exports = { installTestTmp, leftBehindLine, reportLeftBehind };
