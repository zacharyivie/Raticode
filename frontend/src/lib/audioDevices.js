export function audioInputConstraints(deviceId = "default") {
  const selectedDevice = String(deviceId || "default");
  return {
    audio: {
      autoGainControl: true,
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      ...(selectedDevice === "default" ? {} : { deviceId: { exact: selectedDevice } }),
    },
  };
}

// A microphone muted on the device (a headset's flip-up boom), muted in the system's
// sound settings, or switched off with its receiver still plugged in opens normally but
// sends nothing but exact zeros. A live microphone never does, even in a silent room.
export const SILENT_INPUT_MS = 2000;

/**
 * Watches for that. Feed it each stretch of audio as (hasSound, durationMs); it returns
 * "silent" once the input has sent only zeros for SILENT_INPUT_MS, "live" when sound
 * comes back after that, and "" otherwise.
 */
export function createSilenceWatch(limitMs = SILENT_INPUT_MS) {
  let quietMs = 0;
  let silent = false;
  return (hasSound, durationMs) => {
    if (hasSound) {
      quietMs = 0;
      if (!silent) return "";
      silent = false;
      return "live";
    }
    quietMs += durationMs;
    if (silent || quietMs < limitMs) return "";
    silent = true;
    return "silent";
  };
}

/** What to tell someone whose microphone is sending no sound. */
export function silentInputMessage(label = "") {
  // Chromium calls the system default "Default" on Linux and "Default - <name>" on Windows.
  const named = String(label || "").trim().replace(/^default(\s*-\s*|$)/i, "").trim();
  const name = named || "Your microphone";
  return `${name} is sending no sound. It may be muted on the device or in your system's sound settings, or switched off. Unmute it, or choose another microphone in Settings > Devices.`;
}

export async function listAudioInputDevices(mediaDevices = globalThis.navigator?.mediaDevices) {
  if (!mediaDevices?.enumerateDevices) return [];
  const devices = await mediaDevices.enumerateDevices();
  let fallbackIndex = 0;
  return devices
    .filter((device) => device.kind === "audioinput")
    .map((device) => {
      fallbackIndex += 1;
      return {
        id: device.deviceId,
        label: device.label || `Microphone ${fallbackIndex}`,
      };
    });
}
