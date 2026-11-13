import assert from "node:assert/strict";
import test from "node:test";
import { controlledEngine, isStreamedEngine, webBrowserEngine } from "./browserEngines.js";
import { coverCutouts, coverSamples, floatingRoot, deviceBounds, isCovered, sameBounds } from "./geckoView.js";

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

// A tiny element tree: parentElement links, contains, and a CSS position.
function node(name, position = "static", parent = null) {
  const element = { name, position, parentElement: parent, contains(other) { for (let at = other; at; at = at.parentElement) if (at === this) return true; return false; } };
  return element;
}

test("a menu over the Gecko tab becomes a hole in Firefox's window, not a hidden page", () => {
  const body = node("body");
  const slot = node("slot", "relative", body);
  const strip = node("strip", "relative", body);
  const menu = node("menu", "absolute", strip);
  const item = node("item", "static", menu);
  const rect = { left: 0, top: 40, width: 800, height: 600 };
  const boxes = new Map([[menu, { left: 600, top: 30, width: 180, height: 120 }]]);
  // The menu covers the top right of the slot; everything else is the slot.
  const elementAt = (x, y) => (x >= 600 && x <= 780 && y <= 150 ? item : slot);
  const options = { elementAt, positionOf: (element) => element.position, rectOf: (element) => boxes.get(element) };
  assert.equal(floatingRoot(item, slot, options.positionOf), menu, "the whole menu, not one item");
  assert.deepEqual(coverCutouts(slot, rect, options), [{ x: 596, y: 0, width: 188, height: 114 }]);
  // Display scaling.
  assert.deepEqual(coverCutouts(slot, rect, { ...options, ratio: 2 }), [{ x: 1192, y: 0, width: 376, height: 228 }]);
  // Nothing over the slot: no holes.
  assert.deepEqual(coverCutouts(slot, rect, { ...options, elementAt: () => slot }), []);
  // A small open menu between the sample points still gets its hole.
  const small = node("small", "fixed", body);
  boxes.set(small, { left: 100, top: 300, width: 20, height: 20 });
  assert.deepEqual(coverCutouts(slot, rect, { ...options, elementAt: () => slot, extra: [small] }), [{ x: 96, y: 256, width: 28, height: 28 }]);
  // A dialog's backdrop covers most of the slot: the window steps aside.
  const backdrop = node("backdrop", "fixed", body);
  boxes.set(backdrop, { left: 0, top: 0, width: 1000, height: 800 });
  assert.equal(coverCutouts(slot, rect, { ...options, elementAt: () => backdrop }), null);
  // Something over the slot that isn't floating (can't be measured as one piece): step aside.
  const panel = node("panel", "static", body);
  assert.equal(coverCutouts(slot, rect, { ...options, elementAt: (x) => (x > 700 ? panel : slot) }), null);
});

test("holes are part of an unchanged placement", () => {
  const base = { x: 0, y: 0, width: 10, height: 10, visible: true };
  assert.equal(sameBounds({ ...base, cutouts: [] }, { ...base }), true);
  assert.equal(sameBounds({ ...base, cutouts: [{ x: 1, y: 1, width: 2, height: 2 }] }, { ...base, cutouts: [] }), false);
});
