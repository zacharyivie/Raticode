// Gives each test run one temporary folder on disk and removes it when the run ends.
//
// Tests create browser profiles and app builds of 100 MB or more, and /tmp is often
// RAM-backed, so leftovers can fill it and take memory from everything else. Requiring
// this file points TMPDIR at a folder for this run, so os.tmpdir(), child Electron and
// browser processes and Python test servers all write there. The folder is removed on
// exit, a crash or a termination signal. A run that was killed outright is cleared by
// the next run. Set RATICODE_TEST_TMP_BASE to use a different parent folder.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const RUN_DIR_ENV = "RATICODE_TEST_TMP";
const RUN_NAME = /^r(\d+)-/;

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

function clearDeadRuns(base) {
  for (const name of fs.readdirSync(base)) {
    const match = RUN_NAME.exec(name);
    if (match && !pidAlive(Number(match[1]))) fs.rmSync(path.join(base, name), { recursive: true, force: true });
  }
}

function installTestTmp(env = process.env) {
  if (process.platform !== "linux") return "";
  // A child of a test that already owns a folder shares it and leaves removal to its parent.
  if (env[RUN_DIR_ENV]) return env[RUN_DIR_ENV];
  let dir;
  try {
    const base = privateDir(path.join(env.RATICODE_TEST_TMP_BASE || "/var/tmp", `raticode-tests-${process.getuid()}`));
    clearDeadRuns(base);
    dir = fs.mkdtempSync(path.join(base, `r${process.pid}-`));
  } catch (error) {
    console.warn(`Tests will use ${os.tmpdir()}: ${error.message}`);
    return "";
  }
  Object.assign(env, { [RUN_DIR_ENV]: dir, TMPDIR: dir, TEMP: dir, TMP: dir });
  const remove = () => fs.rmSync(dir, { recursive: true, force: true });
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

module.exports = { installTestTmp };
