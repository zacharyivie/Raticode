// Runs the Firefox studio test on a hidden display, then checks that no
// Firefox started from the test's profile is still running.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const electronPath = require("electron");
const { backgroundLaunch } = require("../electron/tests/background-display.cjs");

const env = { ...process.env, ELECTRON_DISABLE_SANDBOX: "1", LIBGL_ALWAYS_SOFTWARE: process.env.LIBGL_ALWAYS_SOFTWARE || "1" };
delete env.ELECTRON_RUN_AS_NODE;

function leftover(profile) {
  if (!profile || process.platform !== "linux") return [];
  return fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
    try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(path.join(profile, "firefox-profile")); } catch { return false; }
  });
}

async function main() {
  const launch = backgroundLaunch(electronPath, [path.join(__dirname, "../electron/tests/firefox-studio.browser.cjs")], env);
  const child = spawn(launch.command, launch.args, { env: launch.env, stdio: ["ignore", "pipe", "inherit"] });
  let profile = "";
  child.stdout.on("data", (data) => {
    process.stdout.write(data);
    profile ||= /RATICODE_TEST_PROFILE=(\S+)/.exec(String(data))?.[1] || "";
  });
  const code = await new Promise((resolve) => child.on("exit", (exitCode, signal) => resolve(signal ? 1 : exitCode)));
  if (code !== 0) throw new Error(`Firefox studio test exited with ${code}.`);
  const deadline = Date.now() + 15000;
  while (leftover(profile).length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 200));
  const left = leftover(profile);
  if (left.length) throw new Error(`Firefox kept running after Raticode quit (pids ${left.join(", ")}).`);
  console.log("No Firefox left running after quit.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
