const { spawn } = require("node:child_process");
const path = require("node:path");
const os = require("node:os");
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
