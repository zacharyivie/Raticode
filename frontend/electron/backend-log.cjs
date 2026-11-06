// Turns the backend's stdout and stderr into app log entries.
//
// The backend writes its Python log records to stderr as `GOFER_LOG {json}` lines
// (see src/gofer/utils/logging.py), so each keeps its level and logger name. Any
// other stderr output, such as a crash before logging started or a library writing
// directly, is kept too: consecutive lines from one read become one entry, filed as
// an error when it looks like one and as a warning otherwise.
const PREFIX = "GOFER_LOG ";
const MAX_LINE = 65536;
const ERROR_TEXT = /\b(Traceback|Error|Exception|CRITICAL|FATAL|panic|Segmentation fault)\b/;

function sourceFor(logger) {
  return /^gofer\.devices(\.|$)/.test(logger) ? "devices" : "backend";
}

function createBackendOutput({ write, echo = () => {} }) {
  const pending = { stdout: "", stderr: "" };

  function structured(line) {
    try {
      const record = JSON.parse(line.slice(PREFIX.length));
      if (!record || typeof record.message !== "string") return false;
      const logger = typeof record.logger === "string" ? record.logger : "backend";
      write(record.level, sourceFor(logger), record.message, logger);
      const level = String(record.level || "info");
      // Warnings and errors keep their traceback; the crash window shows this tail.
      const text = ["warn", "error"].includes(level) ? record.message : record.message.split("\n", 1)[0];
      echo(`${level.toUpperCase()} ${logger}: ${text}`);
      return true;
    } catch {
      return false;
    }
  }

  function flushGroup(stream, group) {
    if (!group.length) return;
    const text = group.join("\n");
    group.length = 0;
    if (!text.trim()) return;
    if (stream === "stdout") { write("info", "backend", text); return; }
    if (/^GOFER_UI_STOPPED\s*$/.test(text)) { write("info", "backend", "Backend stopped its server"); return; }
    write(ERROR_TEXT.test(text) ? "error" : "warn", "backend", text, "stderr");
  }

  function take(stream, text) {
    pending[stream] += text;
    const lines = pending[stream].split(/\r?\n/);
    pending[stream] = lines.pop() ?? "";
    if (pending[stream].length > MAX_LINE) {
      lines.push(pending[stream]);
      pending[stream] = "";
    }
    const group = [];
    for (const line of lines) {
      if (stream === "stderr" && line.startsWith(PREFIX)) {
        flushGroup(stream, group);
        if (structured(line)) continue;
      }
      if (stream === "stderr") echo(line);
      group.push(line);
    }
    flushGroup(stream, group);
  }

  return {
    stdout(text) { take("stdout", String(text)); },
    stderr(text) { take("stderr", String(text)); },
    // Lines the main process already split, such as stdout before the ready marker.
    stdoutLine(line) { if (String(line).trim()) write("info", "backend", String(line)); },
    end() {
      for (const stream of ["stdout", "stderr"]) {
        const rest = pending[stream];
        pending[stream] = "";
        if (rest) take(stream, `${rest}\n`);
      }
    },
  };
}

module.exports = { createBackendOutput, sourceFor };
