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

const filtered = child.stderr.pipe(createChromiumStderrFilter());
filtered.pipe(process.stderr, { end: false });

// If the terminal stops reading, keep draining Electron's stderr anyway: a full pipe
// would block Electron's next write instead of letting it notice and stop.
let terminalClosed = false;
function stopEchoing() {
  if (terminalClosed) return;
  terminalClosed = true;
  filtered.unpipe?.(process.stderr);
  filtered.resume?.();
}
process.stderr?.on?.("error", stopEchoing);
process.stdout?.on?.("error", () => {});

// Ctrl+C signals every process in the terminal's group, Electron included, and
// Electron quits on it by itself. Stay until it has, so its shutdown output still
// reaches the terminal and its stderr pipe isn't closed under it. Never pass the
// signal on: Electron handles only the first one and a second kills it outright.
// A second signal here quits at once, as the first one used to.
const SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];
let signalled = false;
function stopListening() {
  for (const name of SIGNALS) process.removeListener?.(name, onSignal);
}
function onSignal(signal) {
  if (!signalled) { signalled = true; return; }
  stopListening();
  process.kill(process.pid, signal);
}
for (const signal of SIGNALS) process.on?.(signal, onSignal);

// Wait for stderr to drain before exiting.
child.on("close", (code, signal) => {
  stopListening();
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});
