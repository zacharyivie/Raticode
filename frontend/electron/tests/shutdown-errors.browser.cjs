// The Electron side of run-shutdown-errors.cjs. Wires the main process the way
// main.js does, then keeps echoing backend lines while the launcher closes its
// stderr and presses Ctrl+C. Opens no windows. Electron's error dialog is replaced
// with a recorder, so a dialog that would have appeared is counted instead.
require("./background-display.cjs").runInBackgroundDisplay();
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, dialog } = require("electron");
const { createMainErrorHandler, createTerminalOutput, describeError } = require("../main-errors.cjs");

const resultFile = process.env.RATICODE_SHUTDOWN_RESULT;
if (!resultFile) throw new Error("Start this through run-shutdown-errors.cjs");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-shutdown-errors-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");

const result = { dialogs: [], log: [], nodeSignals: [], beforeQuit: 0, echoed: 0, attempts: 0 };
dialog.showErrorBox = (title, content) => result.dialogs.push({ title, content });
const log = (level, message) => result.log.push([level, message]);
let quitting = false;
const output = createTerminalOutput({ stdout: process.stdout, stderr: process.stderr, log });
process.on("uncaughtException", createMainErrorHandler({
  quitting: () => quitting,
  log,
  show: error => dialog.showErrorBox("A JavaScript error occurred in the main process", `Uncaught Exception:\n${describeError(error)}`),
}));
// Electron turns SIGINT into app.quit() itself; these listeners should never run.
// The launcher checks that, since main.js relies on before-quit instead.
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => result.nodeSignals.push(signal));

const timer = setTimeout(() => { finish(); app.exit(3); }, 30000);
function finish() {
  clearTimeout(timer);
  fs.writeFileSync(resultFile, JSON.stringify(result));
  fs.rmSync(profile, { recursive: true, force: true });
}

let echo;
app.on("before-quit", event => {
  // main.js sets quitPending here.
  quitting = true;
  result.beforeQuit += 1;
  if (result.beforeQuit > 1) return;
  // Hold the quit briefly, as main.js does while archives and logs drain, so late
  // output and a window that is already gone happen during shutdown.
  event.preventDefault();
  setImmediate(() => { throw new TypeError("Object has been destroyed"); });
  setTimeout(() => app.quit(), 300);
});
app.on("will-quit", () => { clearInterval(echo); finish(); });

app.whenReady().then(() => {
  // A real error during normal running still shows the dialog.
  setImmediate(() => { throw new Error("genuine failure while running"); });
  setTimeout(() => {
    echo = setInterval(() => {
      result.attempts += 1;
      if (output.stderr(`[gofer-backend] INFO gofer.ui.server: line ${result.attempts}\n`)) result.echoed += 1;
      // Code that writes to stderr directly is covered by the same listener.
      if (result.attempts % 25 === 0) process.stderr.write("direct write\n");
    }, 2);
    fs.writeSync(1, `READY ${process.pid}\n`);
  }, 100);
});
