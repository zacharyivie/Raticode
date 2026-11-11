import assert from "node:assert/strict";
import test from "node:test";
import { controlledEngine, isStreamedEngine, webBrowserEngine } from "./browserEngines.js";
import { coverSamples, deviceBounds, isCovered, sameBounds } from "./geckoView.js";

test("Gecko is a web browser choice that Rem and previews never run on", () => {
  assert.equal(webBrowserEngine("gecko"), "gecko");
  assert.equal(isStreamedEngine("gecko"), false);
  assert.equal(controlledEngine("gecko"), "raticode");
  assert.equal(controlledEngine("chrome"), "chrome");
  assert.equal(controlledEngine("netscape"), "raticode");
});

test("window pixels follow display scaling and don't leave gaps", () => {
  assert.deepEqual(deviceBounds({ left: 10, top: 20, width: 100, height: 50 }), { x: 10, y: 20, width: 100, height: 50 });
  assert.deepEqual(deviceBounds({ left: 10.5, top: 20.25, width: 100.5, height: 50 }, 1.5), { x: 16, y: 30, width: 151, height: 75 });
  assert.deepEqual(deviceBounds({ left: 0, top: 0, width: 0, height: 0 }, 2), { x: 0, y: 0, width: 0, height: 0 });
});

test("samples stay inside the slot", () => {
  const rect = { left: 10, top: 20, width: 100, height: 40 };
  const points = coverSamples(rect);
  assert.equal(points.length, 25);
  assert.ok(points.every(({ x, y }) => x > 10 && x < 110 && y > 20 && y < 60));
});

test("the window steps aside only for something actually over the slot", () => {
  const slot = { name: "slot" };
  const inside = { name: "message" };
  const menu = { name: "menu" };
  const rect = { left: 0, top: 0, width: 100, height: 100 };
  const contains = (element) => element === slot || element === inside;
  assert.equal(isCovered(slot, rect, () => slot, contains), false);
  assert.equal(isCovered(slot, rect, () => inside, contains), false);
  assert.equal(isCovered(slot, rect, () => null, contains), false, "points off the window aren't covered");
  assert.equal(isCovered(slot, rect, (x) => (x > 90 ? menu : slot), contains), true);
});

test("an unchanged placement isn't sent again", () => {
  const a = { x: 1, y: 2, width: 3, height: 4, visible: true };
  assert.equal(sameBounds(a, { ...a }), true);
  assert.equal(sameBounds(a, { ...a, visible: false }), false);
  assert.equal(sameBounds(null, a), false);
});
