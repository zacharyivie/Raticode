import assert from "node:assert/strict";
import test from "node:test";

import { SILENT_INPUT_MS, audioInputConstraints, createSilenceWatch, silentInputMessage } from "./audioDevices.js";

test("a microphone sending only zeros is reported after two seconds, and again live once sound returns", () => {
  const watch = createSilenceWatch();
  // 341 ms buffers, as 16384 samples at 48 kHz arrive.
  const buffer = (16384 * 1000) / 48_000;
  const seen = [];
  for (let elapsed = 0; elapsed < SILENT_INPUT_MS + buffer; elapsed += buffer) seen.push(watch(false, buffer));
  assert.deepEqual(seen.filter(Boolean), ["silent"]);
  assert.equal(watch(false, buffer), "", "it says so once, not on every buffer");
  assert.equal(watch(true, buffer), "live");
  assert.equal(watch(true, buffer), "");
});

test("any sound at all restarts the count, so a quiet room is never taken for a muted microphone", () => {
  const watch = createSilenceWatch(1000);
  assert.equal(watch(false, 900), "");
  assert.equal(watch(true, 10), "");
  assert.equal(watch(false, 900), "");
  assert.equal(watch(false, 100), "silent");
});

test("the no-sound message names the microphone, except Chromium's generic Default", () => {
  assert.match(silentInputMessage("Stealth 600P Gen 2 USB Analog Stereo"), /^Stealth 600P Gen 2 USB Analog Stereo is sending no sound\. .*muted on the device or in your system's sound settings, or switched off\. .*Settings > Devices\.$/);
  assert.match(silentInputMessage("Default"), /^Your microphone is sending no sound\./);
  assert.match(silentInputMessage(""), /^Your microphone is sending no sound\./);
  assert.match(silentInputMessage("Default - Headset Microphone (Jabra Evolve2)"), /^Headset Microphone \(Jabra Evolve2\) is sending no sound\./);
  assert.match(silentInputMessage("Defaulted USB mic"), /^Defaulted USB mic is sending no sound\./);
});

test("the system default microphone is opened without pinning a device", () => {
  assert.equal(audioInputConstraints("default").audio.deviceId, undefined);
  assert.deepEqual(audioInputConstraints("usb-mic").audio.deviceId, { exact: "usb-mic" });
});
