require("./test-tmp.cjs");
// Links in terminal output, end to end: browser-tests/terminal-links.browser.cjs opens the
// real terminal pane with the real xterm in Electron, prints a web address, a file path
// and a missing path, and hovers and clicks them with trusted mouse input.
//
// It launches through backgroundLaunch() with Chromium's headless platform, like
// terminal-find.test.cjs, so it needs no display.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { backgroundLaunch } = require("./background-display.cjs");

const FRONTEND = path.join(__dirname, "..", "..");
const SCRIPT = path.join(FRONTEND, "browser-tests", "terminal-links.browser.cjs");

function electronBinary() {
  try {
    const binary = require("electron");
    return typeof binary === "string" && fs.existsSync(binary) ? binary : "";
  } catch {
    return "";
  }
}

const electron = electronBinary();

test("Ctrl+click opens terminal links in the real xterm and a plain click does not", { skip: electron ? false : "Electron isn't installed (run npm ci in frontend/)", timeout: 120000 }, () => {
  const env = { ...process.env, ELECTRON_DISABLE_SANDBOX: "1" };
  delete env.ELECTRON_RUN_AS_NODE;
  const launch = backgroundLaunch(electron, ["--ozone-platform=headless", SCRIPT], env);
  const result = spawnSync(launch.command, launch.args, { cwd: FRONTEND, encoding: "utf8", env: launch.env, timeout: 110000 });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  assert.equal(result.status, 0, output.split("\n").filter((line) => !/^\[\d+:|dconf|^\s*$|^\(process/.test(line)).slice(-40).join("\n"));
  assert.match(result.stdout, /Terminal links: .* passed\./);
});
