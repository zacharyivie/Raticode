// Run the same real Electron fixtures on each supported release runner.
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const electron = require("electron");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const release = path.resolve(__dirname, "../../release");
const platformPrefix = { linux: "linux", win32: "win", darwin: "mac" }[process.platform];
const archives = [];
function findArchives(directory, depth = 0) {
  if (depth > 5) return;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isFile() && entry.name === "app.asar") archives.push(candidate);
    else if (entry.isDirectory()) findArchives(candidate, depth + 1);
  }
}
if (fs.existsSync(release)) {
  for (const entry of fs.readdirSync(release, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name.startsWith(platformPrefix)) findArchives(path.join(release, entry.name));
  }
}
if (archives.length !== 1) throw new Error("Build exactly one native unpacked desktop package before test:platform.");
env.RATICODE_SMOKE_ASAR = archives[0];
for (const fixture of ["native-runtime.browser.cjs", "studio-policy.browser.cjs", "legacy-project-migration.browser.cjs", "conversation-storage.browser.cjs"]) {
  // Linux CI needs this before Electron starts; fixture JS runs after sandbox setup.
  const args = [electron, ...(process.platform === "linux" ? ["--no-sandbox"] : []), path.join(__dirname, fixture)];
  const result = process.platform === "linux"
    ? spawnSync("xvfb-run", ["-a", ...args], { env, stdio: "inherit", timeout: 60000 })
    : spawnSync(args[0], args.slice(1), { env, stdio: "inherit", timeout: 60000 });
  if (result.error || result.status !== 0) {
    console.error(`${fixture} failed: ${result.error?.message || result.signal || result.status}`);
    process.exit(1);
  }
}
