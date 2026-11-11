// What macOS allows Raticode's dictation to use, for the app log and the microphone gate.
//
// On macOS a microphone that the user (or an administrator) turned off for Raticode
// doesn't fail: it delivers silence, so dictation would sit there hearing nothing and
// the app would blame a muted microphone. Asking macOS first lets Raticode refuse the
// request instead, and the window then says where to turn the microphone back on.
//
// Only states and paths are logged, never audio or words.

const fs = require("node:fs");

function describeSpeechHelper(helperPath, fsImpl = fs) {
  if (!helperPath) return "no path";
  try {
    const stats = fsImpl.statSync(helperPath);
    if (!stats.isFile()) return `not a file at ${helperPath}`;
    fsImpl.accessSync(helperPath, fs.constants.X_OK);
    return `present at ${helperPath}`;
  } catch (error) {
    if (error?.code === "ENOENT") return `missing at ${helperPath}`;
    if (error?.code === "EACCES") return `not executable at ${helperPath}`;
    return `unreadable at ${helperPath} (${error?.code || error?.message || "error"})`;
  }
}

// Resolves to false only when macOS refuses Raticode the microphone. Returns null on
// systems without a per-app microphone permission.
function createSystemMicrophoneGate({ platform = process.platform, systemPreferences, helperPath, log, fsImpl = fs } = {}) {
  if (platform !== "darwin" || typeof systemPreferences?.getMediaAccessStatus !== "function") return null;
  let lastLine = "";
  return async function systemMicrophoneAllowed() {
    const before = systemPreferences.getMediaAccessStatus("microphone");
    let status = before;
    if (before === "not-determined" && typeof systemPreferences.askForMediaAccess === "function") {
      // macOS shows its dialog once; later answers come from System Settings.
      status = (await systemPreferences.askForMediaAccess("microphone")) ? "granted" : "denied";
    }
    const refused = status === "denied" || status === "restricted";
    const helper = typeof helperPath === "function" ? helperPath() : helperPath;
    const line = [
      `Dictation: macOS microphone access for Raticode is ${status}${status !== before ? ` (asked; it was ${before})` : ""}`,
      `speech helper ${describeSpeechHelper(helper, fsImpl)}`,
    ].join("; ");
    if (line !== lastLine) {
      lastLine = line;
      log?.(refused ? "warn" : "info", refused
        ? `${line}. The microphone was refused so the window can say so; allow Raticode in System Settings > Privacy & Security > Microphone, then restart Raticode.`
        : line);
    }
    return !refused;
  };
}

module.exports = { createSystemMicrophoneGate, describeSpeechHelper };
