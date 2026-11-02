// Keep real-Electron tests off the developer's desktop. On Linux each test runs on
// a private Xvfb display, so its windows never appear or take keyboard focus.
// Set RATICODE_TEST_VISIBLE=1 to watch the windows instead.
const { spawnSync } = require("node:child_process");

const MARKER = "RATICODE_TEST_BACKGROUND_DISPLAY";
const MISSING_XVFB = "Real-Electron tests run on a hidden Xvfb display so they don't take focus. Install Xvfb (xvfb-run), or set RATICODE_TEST_VISIBLE=1 to show the test windows.";

function needsBackgroundDisplay(env = process.env, platform = process.platform) {
  return platform === "linux" && env.RATICODE_TEST_VISIBLE !== "1" && env[MARKER] !== "1";
}

// Wrap an Electron launch so it opens on a new virtual display.
function backgroundLaunch(command, args, env = process.env, platform = process.platform) {
  if (!needsBackgroundDisplay(env, platform)) return { command, args, env };
  const childEnv = { ...env, [MARKER]: "1", XDG_SESSION_TYPE: "x11" };
  // Electron 38+ prefers Wayland when it is available, which would put windows
  // on the real desktop even inside xvfb-run.
  delete childEnv.WAYLAND_DISPLAY;
  return {
    command: "xvfb-run",
    args: ["-a", "-s", "-screen 0 1920x1080x24", command, "--ozone-platform=x11", ...args],
    env: childEnv,
  };
}

// Call first in an Electron test entry script. When the script was started on
// the real desktop, rerun it on a virtual display and exit with its result.
function runInBackgroundDisplay() {
  if (!needsBackgroundDisplay()) return;
  const launch = backgroundLaunch(process.execPath, process.argv.slice(1));
  const result = spawnSync(launch.command, launch.args, { env: launch.env, stdio: "inherit" });
  if (result.error) console.error(result.error.code === "ENOENT" ? MISSING_XVFB : result.error.message);
  process.exit(result.status ?? 1);
}

module.exports = { backgroundLaunch, needsBackgroundDisplay, runInBackgroundDisplay };
