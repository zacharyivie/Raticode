const { spawn } = require("node:child_process");
const path = require("node:path");
const geckoDisplay = require("./gecko-display.cjs");
const os = require("node:os");
const fs = require("node:fs");
const electronPath = require("electron");
const { createChromiumStderrFilter } = require("./chromium-stderr.cjs");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
delete env.ELECTRON_NO_ATTACH_CONSOLE;
env.LIBGL_ALWAYS_SOFTWARE = env.LIBGL_ALWAYS_SOFTWARE || "1";

// Chromium does not reliably use XDG_CONFIG_HOME for its instance lock.
// Give development an explicit profile so an installed app cannot swallow launch.
const electronArgs = process.argv.slice(2);
if (!electronArgs.some(arg => arg === "--user-data-dir" || arg.startsWith("--user-data-dir="))) {
  electronArgs.push(`--user-data-dir=${path.resolve(env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), "raticode-dev")}`);
}

function profileDir(args) {
  for (let index = 0; index < args.length; index += 1) {
    if (args[index].startsWith("--user-data-dir=")) return args[index].slice("--user-data-dir=".length);
    if (args[index] === "--user-data-dir") return args[index + 1] || "";
  }
  return "";
}

// Chromium's SingletonLock links to "<host>-<pid>" while a window owns the profile.
function runningProfileOwner(directory) {
  try {
    const match = /^(.*)-(\d+)$/.exec(fs.readlinkSync(path.join(directory, "SingletonLock")));
    if (!match || match[1] !== os.hostname()) return 0;
    const pid = Number(match[2]);
    try {
      process.kill(pid, 0);
    } catch (error) {
      if (error.code !== "EPERM") return 0;
    }
    return pid;
  } catch {
    return 0;
  }
}

// A second Electron on a profile that is already open hands its launch to the
// running window, which then jumps to the front and takes keyboard focus. Refuse
// before starting Electron, so the running window is never contacted.
const userDataDir = profileDir(electronArgs);
if (geckoDisplay.relaunchNeeded({ userData: userDataDir, argv: electronArgs, env })) electronArgs.push(geckoDisplay.X11_SWITCH);
const owner = userDataDir ? runningProfileOwner(userDataDir) : 0;
if (owner) {
  process.stderr.write(
    `Raticode is already running with the profile ${userDataDir} (process ${owner}).\n`
    + "Not starting a second window, so the running one isn't brought to the front.\n"
    + "Close it first, or launch with a different XDG_CONFIG_HOME or --user-data-dir.\n",
  );
  process.exit(1);
}

const child = spawn(electronPath, [
  "--no-sandbox",
  "--disable-gpu",
  "--disable-gpu-compositing",
  "--disable-gpu-rasterization",
  "--disable-dev-shm-usage",
  ...electronArgs,
  ".",
], {
  env,
  stdio: ["inherit", "inherit", "pipe"],
});

child.stderr.pipe(createChromiumStderrFilter()).pipe(process.stderr, { end: false });

// Wait for stderr to drain before exiting.
child.on("close", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});
