import assert from "node:assert/strict";
import test from "node:test";

import { DictationText, joinWords } from "./dictation.js";
import { DOT_ROWS, DotMeter, DotMood, dotColor, drawVoiceDots, levelFromSamples } from "./voiceDots.js";

test("the dot meter rises with the voice, keeps a peak dot for a moment and settles in silence", () => {
  const meter = new DotMeter(41);
  for (let t = 0; t < 300; t += 16) meter.step(16, 1, t);
  const middle = meter.heights[20];
  assert.ok(middle > 2, `middle column rose to ${middle}`);
  assert.ok(middle <= DOT_ROWS);
  // Speech is fullest in the middle of the line.
  assert.ok(meter.shape(20) > meter.shape(0));
  assert.ok(meter.shape(20) > meter.shape(40));

  const peak = meter.peaks[20];
  meter.step(100, 0, 400);
  assert.ok(meter.heights[20] < middle, "the column falls back");
  assert.equal(meter.peaks[20], peak, "the peak dot hangs before dropping");
  for (let t = 500; t < 5000; t += 16) meter.step(16, 0, t);
  assert.ok(meter.heights[20] < 0.01);
  assert.ok(meter.peaks[20] < 0.01);
});

test("reduced motion keeps the line meaningful without the sway", () => {
  const meter = new DotMeter(11);
  for (let t = 0; t < 1000; t += 16) meter.step(16, 0.5, t, true);
  const settled = Array.from(meter.heights);
  meter.step(16, 0.5, 5000, true);
  settled.forEach((height, index) => assert.ok(Math.abs(meter.heights[index] - height) < 0.01));
});

test("microphone loudness maps silence to zero and speech toward full", () => {
  assert.equal(levelFromSamples(new Float32Array(512)), 0);
  assert.equal(levelFromSamples([]), 0);
  const quiet = levelFromSamples(Float32Array.from({ length: 512 }, (_, i) => Math.sin(i / 5) * 0.004));
  const loud = levelFromSamples(Float32Array.from({ length: 512 }, (_, i) => Math.sin(i / 5) * 0.4));
  assert.ok(quiet < 0.2, `quiet ${quiet}`);
  assert.ok(loud > 0.8, `loud ${loud}`);
  assert.ok(loud <= 1);
});

test("the line runs cyan to lilac to magenta, and paused leaves room for the microphone", () => {
  assert.deepEqual(dotColor(0), [0x3e, 0xe0, 0xff]);
  assert.deepEqual(dotColor(0.5), [0xa5, 0xa6, 0xff]);
  assert.deepEqual(dotColor(1), [0xff, 0x5a, 0xd6]);

  const dots = [];
  const context = {
    fillStyle: "",
    clearRect() {}, beginPath() {}, fill() {}, save() {}, restore() {}, translate() {}, scale() {},
    arc(x, y) { dots.push({ x, y, fill: this.fillStyle }); },
  };
  const meter = new DotMeter();
  drawVoiceDots(context, { width: 280, height: 56, meter, mood: DotMood.PAUSED, timeMs: 0 });
  assert.equal(meter.columns, 40);
  assert.ok(dots.length > 0);
  assert.equal(dots.some((dot) => Math.abs(dot.x - 140) < 22), false, "a gap for the microphone");
  assert.ok(dots.every((dot) => dot.fill.startsWith("rgba(94, 88, 120")), "paused dots rest in grey");

  dots.length = 0;
  drawVoiceDots(context, { width: 280, height: 56, meter, mood: DotMood.LISTENING, timeMs: 0 });
  assert.ok(dots.some((dot) => Math.abs(dot.x - 140) < 22));
});

test("dictation text keeps earlier sessions and a session cut short", () => {
  const words = new DictationText();
  words.partial("add a");
  assert.equal(words.text, "add a");
  words.result("add a review node");
  words.partial("and run");
  assert.equal(words.text, "add a review node and run");
  // A session with no final words keeps the words it heard.
  words.result("");
  assert.equal(words.text, "add a review node and run");
  words.partial("now please");
  words.interrupted();
  assert.equal(words.text, "add a review node and run now please");
  assert.equal(joinWords(" Note: ", "", " more "), "Note: more");
});
