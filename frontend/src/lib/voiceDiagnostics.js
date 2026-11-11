// Voice input problems, for the app log. The desktop app copies the window's warnings to
// Settings > Developer, so these use console.warn. They carry states and error names
// only: never audio, never the words heard.

const PREFIX = "Voice input";

export const VoiceStage = Object.freeze({
  MICROPHONE: "opening the microphone",
  START: "starting transcription",
  RECORDER: "starting the recorder",
  STREAM: "transcribing",
  FINISH: "finishing transcription",
});

/** Marks where an error happened, so the one place that logs it can say. */
export function atStage(error, stage) {
  if (error && typeof error === "object" && !error.voiceStage) {
    try { error.voiceStage = stage; } catch { /* frozen errors keep no stage */ }
  }
  return error;
}

export function describeError(error) {
  if (!error) return "unknown error";
  const name = error.name && error.name !== "Error" ? `${error.name}: ` : "";
  const message = String(error.message || error).slice(0, 400);
  // getUserMedia's names say which: NotAllowedError (refused), NotFoundError (no input),
  // NotReadableError (in use or blocked by the OS), OverconstrainedError (input gone).
  return `${name}${message}`;
}

export function reportVoiceProblem(stage, error, { warn = console.warn } = {}) {
  const where = stage || error?.voiceStage || VoiceStage.START;
  const hint = error?.name === "NotAllowedError" && isMac()
    ? " On a Mac, check System Settings > Privacy & Security > Microphone."
    : "";
  warn(`${PREFIX}: failed ${where}: ${describeError(error)}${hint}`);
}

export function trackState(stream) {
  const track = stream?.getAudioTracks?.()[0];
  if (!track) return "no audio track";
  const settings = track.getSettings?.() || {};
  return [
    `track ${track.readyState || "unknown"}`,
    track.muted ? "muted by the system" : "",
    track.enabled === false ? "disabled" : "",
    settings.sampleRate ? `${settings.sampleRate} Hz` : "",
    settings.channelCount ? `${settings.channelCount} channel${settings.channelCount === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(", ");
}

/** A recording that heard nothing usable: what the recorder saw, to tell why. */
export function reportQuietRecording(stats, { warn = console.warn } = {}) {
  if (!stats || stats.recorded) return;
  const seconds = (stats.samples / Math.max(1, stats.sampleRate)).toFixed(1);
  const input = stats.label ? `"${stats.label}"` : "the microphone";
  let what;
  if (!stats.samples) {
    what = `no audio reached the recorder (audio context ${stats.contextState}, ${stats.track})`;
  } else if (!stats.anySound) {
    what = `${input} sent ${seconds} s of exact zeros (${stats.track}, ${stats.sampleRate} Hz)`;
    if (isMac()) what += ". macOS sends zeros when Raticode isn't allowed the microphone in Privacy & Security";
  } else {
    what = `${input} sent ${seconds} s too quiet to transcribe (peak ${stats.peak.toFixed(4)}, ${stats.track})`;
  }
  warn(`${PREFIX}: ${what}.`);
}

export function reportSilentInput(label, stream, { warn = console.warn } = {}) {
  const input = label ? `"${label}"` : "The microphone";
  const mac = isMac() ? " On a Mac this is also what a microphone turned off for Raticode in Privacy & Security sends." : "";
  warn(`${PREFIX}: ${input} is sending only zeros (${trackState(stream)}).${mac}`);
}

function isMac() {
  const platform = globalThis.navigator?.userAgentData?.platform || globalThis.navigator?.platform || "";
  return /mac/i.test(platform);
}
