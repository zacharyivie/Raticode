// Real Electron, hidden: Ctrl+C after the terminal stopped reading stderr.
// Run with `npm run test:shutdown-errors-browser`.
//
// Electron must quit normally on SIGINT, record the closed terminal and the
// "Object has been destroyed" error in its log, and open only one error dialog:
// the one for a real error while it was running.
require("./test-tmp.cjs");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const electronPath = require("electron");
const { backgroundLaunch } = require("./background-display.cjs");

async function main() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-shutdown-run-"));
  const resultFile = path.join(folder, "result.json");
  try {
    const launch = backgroundLaunch(electronPath, ["--no-sandbox", path.join(__dirname, "shutdown-errors.browser.cjs")], {
      ...process.env, RATICODE_SHUTDOWN_RESULT: resultFile, ELECTRON_ENABLE_LOGGING: "",
    });
    const child = spawn(launch.command, launch.args, { env: launch.env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    const pid = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Electron never became ready:\n${stdout}`)), 60000);
      child.stdout.on("data", chunk => {
        stdout += chunk;
        const match = /READY (\d+)/.exec(stdout);
        if (match) { clearTimeout(timer); resolve(Number(match[1])); }
      });
      child.once("exit", code => { clearTimeout(timer); reject(new Error(`Electron exited early (${code}):\n${stdout}`)); });
    });
    // Ctrl+C stops the dev runner first: its end of Electron's stderr goes away.
    child.stderr.destroy();
    await new Promise(resolve => setTimeout(resolve, 200));
    process.kill(pid, "SIGINT");
    const code = await new Promise(resolve => child.once("exit", resolve));
    const result = JSON.parse(fs.readFileSync(resultFile, "utf8"));
    assert.equal(code, 0, `Electron exit code ${code}: ${JSON.stringify(result)}`);
    // Electron quit through before-quit on its own; main.js depends on that.
    assert.deepEqual(result.nodeSignals, []);
    assert.equal(result.beforeQuit, 2);
    assert.equal(result.dialogs.length, 1, JSON.stringify(result.dialogs));
    assert.match(result.dialogs[0].content, /genuine failure while running/);
    const brokenPipe = result.log.filter(([, message]) => /stopped reading Raticode's stderr \(EPIPE\)/.test(message));
    assert.equal(brokenPipe.length, 1, JSON.stringify(result.log));
    assert.equal(brokenPipe[0][0], "info");
    assert.ok(result.log.some(([level, message]) => level === "info" && /Object has been destroyed/.test(message)), JSON.stringify(result.log));
    assert.ok(result.attempts > result.echoed + 20, `echo kept writing: ${result.echoed} of ${result.attempts}`);
    console.log(`Shutdown errors: Electron quit cleanly; ${result.attempts - result.echoed} lines went only to the log; 1 dialog for the real error.`);
  } finally {
    fs.rmSync(folder, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
