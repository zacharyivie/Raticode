import assert from "node:assert/strict";
import test from "node:test";

import { VoiceStage, atStage, describeError, reportQuietRecording, reportVoiceProblem, trackState } from "./voiceDiagnostics.js";

function capture() {
  const lines = [];
  return { lines, warn: (line) => lines.push(line) };
}

test("a failure is logged once with its stage and error name", () => {
  const { lines, warn } = capture();
  const error = atStage(Object.assign(new Error("Could not start audio source"), { name: "NotReadableError" }), VoiceStage.MICROPHONE);
  // A later stage doesn't overwrite where it really failed.
  atStage(error, VoiceStage.FINISH);
  reportVoiceProblem(error.voiceStage, error, { warn });
  assert.deepEqual(lines, ["Voice input: failed opening the microphone: NotReadableError: Could not start audio source"]);
  assert.equal(describeError(new Error("plain")), "plain");
  assert.equal(describeError(null), "unknown error");
  assert.equal(describeError({ message: "x".repeat(1000) }).length, 400);
});

test("a quiet recording says whether audio arrived, was all zeros, or was just quiet", () => {
  const { lines, warn } = capture();
  const base = { recorded: false, sampleRate: 48_000, contextState: "running", track: "track live", label: "MacBook Pro Microphone" };
  reportQuietRecording({ ...base, samples: 0, anySound: false, peak: 0, contextState: "suspended" }, { warn });
  reportQuietRecording({ ...base, samples: 96_000, anySound: false, peak: 0 }, { warn });
  reportQuietRecording({ ...base, samples: 48_000, anySound: true, peak: 0.0012 }, { warn });
  reportQuietRecording({ ...base, recorded: true }, { warn });
  assert.deepEqual(lines, [
    "Voice input: no audio reached the recorder (audio context suspended, track live).",
    'Voice input: "MacBook Pro Microphone" sent 2.0 s of exact zeros (track live, 48000 Hz).',
    'Voice input: "MacBook Pro Microphone" sent 1.0 s too quiet to transcribe (peak 0.0012, track live).',
  ]);
});

test("the track's state names what the system did to it", () => {
  const track = { readyState: "live", muted: true, enabled: true, getSettings: () => ({ sampleRate: 44_100, channelCount: 1 }) };
  assert.equal(trackState({ getAudioTracks: () => [track] }), "track live, muted by the system, 44100 Hz, 1 channel");
  assert.equal(trackState({ getAudioTracks: () => [] }), "no audio track");
  assert.equal(trackState(null), "no audio track");
});
