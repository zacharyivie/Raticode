import assert from "node:assert/strict";
import test from "node:test";
import { shareInFlight, startPolling } from "./refresh.js";
import { defaultSettingsSnapshot, DEFAULT_APP_SETTINGS, normalizeAppSettings } from "./settings.js";

function fakeTargets() {
  const timers = new Map();
  let sequence = 0;
  const target = new EventTarget();
  target.setTimeout = (callback, delay) => { const id = ++sequence; timers.set(id, { callback, delay }); return id; };
  target.clearTimeout = (id) => timers.delete(id);
  const documentTarget = new EventTarget();
  documentTarget.hidden = false;
  return { target, documentTarget, timers };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("polling never overlaps and resumes immediately on focus", async () => {
  const targets = fakeTargets();
  let calls = 0;
  let finish;
  const stop = startPolling(() => { calls += 1; return new Promise((resolve) => { finish = resolve; }); }, { ...targets, immediate: true });
  await tick();
  targets.target.dispatchEvent(new Event("focus"));
  assert.equal(calls, 1);
  assert.equal(targets.timers.size, 0);
  finish();
  await tick();
  assert.equal(targets.timers.size, 1);
  assert.ok([...targets.timers.values()][0].delay <= 2000);
  targets.target.dispatchEvent(new Event("focus"));
  await tick();
  assert.equal(calls, 2);
  stop();
  finish();
  await tick();
  assert.equal(targets.timers.size, 0);
});

test("hidden polling skips work and visibility restores an immediate refresh", async () => {
  const targets = fakeTargets();
  targets.documentTarget.hidden = true;
  let calls = 0;
  const stop = startPolling(() => { calls += 1; }, { ...targets, immediate: true });
  await tick();
  assert.equal(calls, 0);
  assert.equal([...targets.timers.values()][0].delay, 15000);
  targets.documentTarget.hidden = false;
  targets.documentTarget.dispatchEvent(new Event("visibilitychange"));
  await tick();
  assert.equal(calls, 1);
  stop();
});

test("resource requests share only pending work and recover after failure", async () => {
  let calls = 0;
  const task = async () => { calls += 1; return "result"; };
  assert.deepEqual(await Promise.all([shareInFlight("same", task), shareInFlight("same", task)]), ["result", "result"]);
  assert.equal(calls, 1);
  await shareInFlight("same", task);
  assert.equal(calls, 2);
  await assert.rejects(shareInFlight("same", async () => { throw new Error("fixture"); }));
  assert.equal(await shareInFlight("same", task), "result");
});

test("settings reset and normalization return independent default values", () => {
  const first = defaultSettingsSnapshot();
  const second = defaultSettingsSnapshot();
  assert.deepEqual(first, DEFAULT_APP_SETTINGS);
  assert.deepEqual(first, second);
  first.editor.fontSize += 1;
  assert.equal(second.editor.fontSize, DEFAULT_APP_SETTINGS.editor.fontSize);
  const normalized = normalizeAppSettings();
  normalized.keybindings = {};
  assert.notDeepEqual(normalized.keybindings, defaultSettingsSnapshot().keybindings);
});

test("shared cancellation keeps another subscriber alive and evicts abandoned requests", async () => {
  const { shareCancellable } = await import("./refresh.js");
  let resolve, calls = 0, sharedSignal;
  const task = signal => { calls++; sharedSignal = signal; return new Promise(done => { resolve = done; }); };
  const a = new AbortController(), b = new AbortController();
  const first = shareCancellable("discovery", task, a.signal);
  const second = shareCancellable("discovery", task, b.signal);
  await Promise.resolve();
  a.abort();
  await assert.rejects(first, { name: "AbortError" });
  assert.equal(sharedSignal.aborted, false);
  assert.equal(calls, 1);
  resolve("B");
  assert.equal(await second, "B");
  const abandoned = shareCancellable("discovery", task, b.signal);
  await Promise.resolve();
  b.abort();
  await assert.rejects(abandoned, { name: "AbortError" });
  assert.equal(sharedSignal.aborted, true);
  assert.equal(await shareCancellable("discovery", async () => "retry"), "retry");
  resolve("late");
});
