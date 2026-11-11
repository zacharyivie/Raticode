// Keep real-Electron tests off the developer's desktop. On Linux each test runs on
// a private Xvfb display and a private, empty D-Bus session, so its windows never
// appear or take keyboard focus, and it can't add a tray icon, show a notification,
// or start the desktop's portal or wallet services.
// Set RATICODE_TEST_VISIBLE=1 to watch the windows instead.
require("./test-tmp.cjs");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const MARKER = "RATICODE_TEST_BACKGROUND_DISPLAY";
const MISSING_XVFB = "Real-Electron tests run on a hidden Xvfb display so they don't take focus. Install Xvfb (xvfb-run), or set RATICODE_TEST_VISIBLE=1 to show the test windows.";
const BUS_CONFIG = path.join(__dirname, "test-session-bus.conf");
// A socket that never exists: without dbus-run-session, D-Bus calls fail instead
// of falling back to the desktop's bus in $XDG_RUNTIME_DIR.
const NO_SESSION_BUS = "unix:path=/nonexistent/raticode-test-session-bus";

function needsBackgroundDisplay(env = process.env, platform = process.platform) {
  return platform === "linux" && env.RATICODE_TEST_VISIBLE !== "1" && env[MARKER] !== "1";
}

function findExecutable(name, env = process.env) {
  for (const dir of String(env.PATH || "").split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // Keep looking.
    }
  }
  return "";
}

// Wrap an Electron launch so it opens on a new virtual display and session bus.
function backgroundLaunch(command, args, env = process.env, platform = process.platform, which = findExecutable) {
  if (!needsBackgroundDisplay(env, platform)) return { command, args, env };
  const childEnv = { ...env, [MARKER]: "1", XDG_SESSION_TYPE: "x11", NO_AT_BRIDGE: "1", DBUS_SESSION_BUS_ADDRESS: NO_SESSION_BUS };
  // Electron 38+ prefers Wayland when it is available, which would put windows
  // on the real desktop even inside xvfb-run.
  delete childEnv.WAYLAND_DISPLAY;
  // Even on Xvfb, Electron reaches the desktop over the session bus: the app's
  // tray icon lands in the real panel and Chromium asks the real wallet for keys.
  delete childEnv.DBUS_SESSION_BUS_PID;
  const xvfb = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", command, "--ozone-platform=x11", "--password-store=basic", ...args];
  // Without xvfb-run, launch it bare so the caller gets ENOENT and MISSING_XVFB.
  if (which("xvfb-run", env) && which("dbus-run-session", env)) {
    return { command: "dbus-run-session", args: [`--config-file=${BUS_CONFIG}`, "--", ...xvfb], env: childEnv };
  }
  return { command: xvfb[0], args: xvfb.slice(1), env: childEnv };
}

// Call first in an Electron test entry script. When the script was started on
// the real desktop, rerun it on a virtual display and exit with its result.
function runInBackgroundDisplay() {
  if (process.platform !== "linux" && process.env.RATICODE_TEST_VISIBLE !== "1" && !process.env.CI) {
    console.error(`No hidden display on ${process.platform}: this test's windows will be visible and may take focus.`);
  }
  if (!needsBackgroundDisplay()) return;
  const launch = backgroundLaunch(process.execPath, process.argv.slice(1));
  const result = spawnSync(launch.command, launch.args, { env: launch.env, stdio: "inherit" });
  if (result.error) console.error(result.error.code === "ENOENT" ? MISSING_XVFB : result.error.message);
  process.exit(result.status ?? 1);
}

module.exports = { BUS_CONFIG, NO_SESSION_BUS, backgroundLaunch, findExecutable, needsBackgroundDisplay, runInBackgroundDisplay };
