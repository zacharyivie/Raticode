const { spawn } = require("node:child_process");
const os = require("node:os");
const path = require("node:path");
const electronPath = require("electron");
const { backgroundLaunch } = require("./tests/background-display.cjs");

const READY_MESSAGE = "GOFER_ELECTRON_READY";
const SMOKE_TIMEOUT_MS = 30000;

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
delete env.ELECTRON_NO_ATTACH_CONSOLE;
env.GOFER_ELECTRON_MODE = "production";
env.GOFER_ELECTRON_SMOKE_TEST = "1";
env.GOFER_DATA_DIR =
  env.GOFER_DATA_DIR || path.join(os.tmpdir(), "gofer-flow-electron-smoke");
env.LIBGL_ALWAYS_SOFTWARE = env.LIBGL_ALWAYS_SOFTWARE || "1";

const launch = backgroundLaunch(electronPath, [
  "--no-sandbox",
  "--disable-gpu",
  "--disable-gpu-compositing",
  "--disable-gpu-rasterization",
  "--disable-dev-shm-usage",
  ".",
], env);
const child = spawn(launch.command, launch.args, {
  env: launch.env,
  stdio: ["ignore", "pipe", "pipe"],
});

let ready = false;
let output = "";
const timeoutId = setTimeout(() => {
  fail(`Timed out waiting for ${READY_MESSAGE}.`);
}, SMOKE_TIMEOUT_MS);

child.stdout.on("data", (chunk) => {
  const text = chunk.toString();
  output += text;
  process.stdout.write(text);

  if (output.includes(READY_MESSAGE)) {
    ready = true;
  }
});

child.stderr.on("data", (chunk) => {
  const text = chunk.toString();
  output += text;
  process.stderr.write(text);
});

child.on("error", (error) => {
  fail(error.message);
});

child.on("exit", (code, signal) => {
  clearTimeout(timeoutId);
  if (ready && code === 0) {
    process.exit(0);
  }

  const reason = signal ? `signal ${signal}` : `code ${code}`;
  console.error(`Electron smoke test failed with ${reason}.`);
  if (output.trim()) {
    console.error(output.trim());
  }
  process.exit(code || 1);
});

function fail(message) {
  clearTimeout(timeoutId);
  console.error(message);
  child.kill("SIGTERM");
  setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
  }, 3000).unref();
}
