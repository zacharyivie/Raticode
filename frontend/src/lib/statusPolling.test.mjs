import test from "node:test";
import assert from "node:assert/strict";
import { pollWithoutOverlap } from "./statusPolling.js";

function harness() {
  const scheduled = [];
  let clock = 0;
  return {
    scheduled,
    advance: ms => { clock += ms; },
    options: { schedule: (run, delay) => { scheduled.push({ run, delay }); return scheduled.length; }, cancel: () => {}, now: () => clock },
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test("a status poll never starts while the previous one is still waiting", async () => {
  const { scheduled, options } = harness();
  let inFlight = 0, most = 0, finish;
  const load = () => { inFlight++; most = Math.max(most, inFlight); return new Promise(resolve => { finish = () => { inFlight--; resolve(); }; }); };
  pollWithoutOverlap(load, options);
  await settle();
  assert.equal(scheduled.length, 0, "no next poll is scheduled before the answer");
  finish();
  await settle();
  assert.equal(scheduled.length, 1);
  scheduled[0].run();
  await settle();
  finish();
  await settle();
  assert.equal(most, 1);
});

test("slow or failing answers back off and a fast answer restores the interval", async () => {
  const { scheduled, advance, options } = harness();
  const outcomes = ["slow", "slow", "fail", "fast"];
  const errors = [];
  pollWithoutOverlap(async () => {
    const outcome = outcomes.shift();
    if (outcome === "slow") advance(5000);
    if (outcome === "fail") throw new Error("busy");
  }, { ...options, interval: 2000, slow: 1000, max: 10000, onError: error => errors.push(error.message) });
  for (let index = 0; index < 4; index++) {
    await settle();
    if (index < 3) scheduled.at(-1).run();
  }
  await settle();
  assert.deepEqual(scheduled.map(item => item.delay), [4000, 8000, 10000, 2000]);
  assert.deepEqual(errors, ["busy"]);
});

test("stopping the poll ends it after an aborted request", async () => {
  const { scheduled, options } = harness();
  const controller = new AbortController();
  pollWithoutOverlap(async () => { controller.abort(); throw new Error("aborted"); }, { ...options, signal: controller.signal, onError: () => assert.fail("no error after stop") });
  await settle();
  assert.equal(scheduled.length, 0);
});
