// Keeps routine shutdown noise out of Electron's "A JavaScript error occurred in
// the main process" dialog.
//
// Ctrl+C in the terminal running `npm run electron:dev` signals every process in
// the group at once: the dev runner that reads this process's stderr, the backend
// and Electron. The reader goes first, so the backend's last log lines are echoed
// into a closed pipe. Node reports each failed write as an `error` event on
// process.stderr, and with no listener every one became an uncaught exception and
// its own dialog. Those errors belong in the app log, not on screen.

// A reader that went away. EIO only counts for writes: a closed terminal reports it.
const BROKEN_PIPE_CODES = new Set(["EPIPE", "ERR_STREAM_DESTROYED", "ERR_STREAM_WRITE_AFTER_END"]);
// Connections and windows that close under us while Raticode quits.
const SHUTDOWN_CODES = new Set([
  "ECONNRESET", "ECONNREFUSED", "ECONNABORTED", "ENOTCONN", "ERR_IPC_CHANNEL_CLOSED", "ERR_SOCKET_CLOSED",
]);
const SHUTDOWN_MESSAGES = /Object has been destroyed|Render frame was disposed|WebContents was destroyed|Frame was disposed/;

function isBrokenPipe(error) {
  const code = error?.code;
  return BROKEN_PIPE_CODES.has(code) || (code === "EIO" && error?.syscall === "write");
}

function describeError(error) {
  if (error instanceof Error) return error.stack || `${error.name}: ${error.message}`;
  return String(error);
}

// "benign": a broken pipe at any time, or a connection or window closing while
// Raticode quits. "shutdown": anything else while quitting. "fatal": anything else.
function classifyMainError(error, { quitting = false } = {}) {
  if (isBrokenPipe(error)) return "benign";
  if (!quitting) return "fatal";
  if (SHUTDOWN_CODES.has(error?.code) || SHUTDOWN_MESSAGES.test(String(error?.message || error))) return "benign";
  return "shutdown";
}

// Writes to the terminal that stop quietly, after one log entry, once a stream breaks.
function createTerminalOutput({ stdout, stderr, log = () => {} }) {
  const streams = { stdout, stderr };
  const broken = new Set();
  function markBroken(name, error) {
    if (broken.has(name)) return;
    broken.add(name);
    const reason = error?.code || error?.message || "closed";
    try {
      log(isBrokenPipe(error) || !error ? "info" : "warn",
        `The terminal stopped reading Raticode's ${name} (${reason}). Later output goes only to the app log.`);
    } catch { /* logging must never throw. */ }
  }
  for (const [name, stream] of Object.entries(streams)) stream?.on?.("error", error => markBroken(name, error));
  function write(name, text) {
    if (broken.has(name)) return false;
    const stream = streams[name];
    if (!stream) return false;
    if (stream.destroyed || stream.writableEnded) { markBroken(name); return false; }
    try {
      stream.write(text);
      return true;
    } catch (error) {
      markBroken(name, error);
      return false;
    }
  }
  return {
    stdout: text => write("stdout", text),
    stderr: text => write("stderr", text),
    broken: name => broken.has(name),
  };
}

// The main process's uncaughtException listener. Electron shows its own dialog only
// when nothing else listens, so `show` repeats that dialog for real errors.
function createMainErrorHandler({ quitting = () => false, log, show }) {
  return (error) => {
    const kind = classifyMainError(error, { quitting: quitting() });
    const text = describeError(error);
    try {
      if (kind === "benign") log("info", `Ignored an error from a closed connection or terminal: ${text}`);
      else if (kind === "shutdown") log("error", `Uncaught exception while Raticode was quitting (not shown on screen): ${text}`);
      else log("error", `Uncaught exception: ${text}`);
    } catch { /* logging must never throw. */ }
    if (kind === "fatal") show(error);
  };
}

function rejectionLevel(error, { quitting = false } = {}) {
  return classifyMainError(error, { quitting }) === "benign" ? "info" : "error";
}

module.exports = {
  classifyMainError, createMainErrorHandler, createTerminalOutput, describeError, isBrokenPipe, rejectionLevel,
};
