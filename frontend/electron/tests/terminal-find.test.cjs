require("./test-tmp.cjs");
// Find in the terminal, end to end: browser-tests/terminal-find.browser.cjs opens the real
// terminal pane with the real xterm and @xterm/addon-search in Electron, prints known
// lines, searches them from the find bar and checks what the addon selected.
//
// It launches through backgroundLaunch() with Chromium's headless platform: the test
// sends keys over the DevTools protocol, so it needs no display, and on Linux it runs
// without Xvfb and away from the desktop's session bus.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { backgroundLaunch } = require("./background-display.cjs");

const FRONTEND = path.join(__dirname, "..", "..");
const SCRIPT = path.join(FRONTEND, "browser-tests", "terminal-find.browser.cjs");

function electronBinary() {
  try {
    const binary = require("electron");
    return typeof binary === "string" && fs.existsSync(binary) ? binary : "";
  } catch {
    return "";
  }
}

const electron = electronBinary();

test("Find in the terminal selects what the real search addon found", { skip: electron ? false : "Electron isn't installed (run npm ci in frontend/)", timeout: 120000 }, () => {
  const env = { ...process.env, ELECTRON_DISABLE_SANDBOX: "1" };
  delete env.ELECTRON_RUN_AS_NODE;
  const launch = backgroundLaunch(electron, ["--ozone-platform=headless", SCRIPT], env);
  const result = spawnSync(launch.command, launch.args, { cwd: FRONTEND, encoding: "utf8", env: launch.env, timeout: 110000 });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  assert.equal(result.status, 0, output.split("\n").filter((line) => !/^\[\d+:|dconf|^\s*$|^\(process/.test(line)).slice(-40).join("\n"));
  assert.match(result.stdout, /Terminal Find: .* passed\./);
});
