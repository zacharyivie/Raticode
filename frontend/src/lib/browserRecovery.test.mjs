import assert from "node:assert/strict";
import test from "node:test";
import { BACKOFF_MS, LOOP_WINDOW_MS, MAX_RESTORES, createTabRecovery, hangAction } from "./browserRecovery.js";

test("a page whose process ended reloads at once, then waits longer each time, then offers Reload", () => {
  let now = 1_000_000;
  const recovery = createTabRecovery(() => now);
  for (let round = 0; round < MAX_RESTORES; round += 1) {
    assert.deepEqual(recovery.gone(), { delayMs: BACKOFF_MS[round] });
    recovery.restored();
  }
  assert.deepEqual(recovery.gone(), { giveUp: true });
  // Long-ago reloads don't count, and the user reloading starts counting again.
  now += LOOP_WINDOW_MS + 1;
  assert.deepEqual(recovery.gone(), { delayMs: 0 });
  for (let round = 0; round < MAX_RESTORES; round += 1) recovery.restored();
  recovery.forget();
  assert.deepEqual(recovery.gone(), { delayMs: 0 });
});

test("a stuck page is ended only while it's on screen and not playing sound", () => {
  assert.equal(hangAction({ onScreen: false, audible: false }), "leave");
  assert.equal(hangAction({ onScreen: true, audible: true }), "leave");
  assert.equal(hangAction({ onScreen: true, audible: false }), "end");
});
