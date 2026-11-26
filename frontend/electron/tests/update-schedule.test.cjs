const assert = require("node:assert/strict");
const test = require("node:test");

const {
  CHECK_INTERVAL_MS,
  MAX_BACKOFF_MS,
  createUpdateScheduler,
  nextCheckDelay,
} = require("../update-schedule.cjs");

// A clock and timer list the test moves by hand.
function fakeClock(start = Date.UTC(2026, 10, 13, 12)) {
  let time = start;
  const timers = [];
  return {
    now: () => time,
    setTimer: (callback, delay) => {
      const timer = { at: time + delay, callback, delay };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      const index = timers.indexOf(timer);
      if (index >= 0) timers.splice(index, 1);
    },
    timers,
    async advance(ms) {
      const target = time + ms;
      for (;;) {
        const due = timers.filter((timer) => timer.at <= target).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        timers.splice(timers.indexOf(due), 1);
        time = due.at;
        due.callback();
        await settle();
      }
      time = target;
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("checks once on start and then every 10 minutes", async () => {
  const clock = fakeClock();
  let checks = 0;
  const scheduler = createUpdateScheduler({ ...clock, runCheck: async () => { checks += 1; return { available: false }; } });
  scheduler.start();
  await settle();
  assert.equal(checks, 1);
  assert.equal(clock.timers.length, 1);
  assert.equal(clock.timers[0].delay, CHECK_INTERVAL_MS);
  await clock.advance(CHECK_INTERVAL_MS - 1);
  assert.equal(checks, 1);
  await clock.advance(1);
  assert.equal(checks, 2);
  await clock.advance(CHECK_INTERVAL_MS * 3);
  assert.equal(checks, 5);
  // Starting again doesn't add a second schedule.
  scheduler.start();
  await settle();
  assert.equal(checks, 5);
  assert.equal(clock.timers.length, 1);
  scheduler.stop();
  assert.equal(clock.timers.length, 0);
  await clock.advance(CHECK_INTERVAL_MS * 5);
  assert.equal(checks, 5);
});

test("a manual check during a running one shares it instead of sending a second request", async () => {
  const clock = fakeClock();
  let checks = 0;
  let finish;
  const scheduler = createUpdateScheduler({
    ...clock,
    runCheck: () => { checks += 1; return new Promise((resolve) => { finish = resolve; }); },
  });
  scheduler.start();
  await settle();
  const manual = scheduler.checkNow();
  const another = scheduler.checkNow();
  assert.equal(checks, 1);
  assert.equal(scheduler.checking, true);
  // No timer runs while a check is out, so nothing can overlap it.
  assert.equal(clock.timers.length, 0);
  finish({ available: true, info: { version: "0.5.5" } });
  assert.deepEqual(await manual, { available: true, info: { version: "0.5.5" } });
  assert.equal(await another, await manual);
  assert.equal(clock.timers.length, 1);
  scheduler.stop();
});

test("a manual check restarts the 10-minute count", async () => {
  const clock = fakeClock();
  let checks = 0;
  const scheduler = createUpdateScheduler({ ...clock, runCheck: async () => { checks += 1; return {}; } });
  scheduler.start();
  await settle();
  await clock.advance(8 * 60 * 1000);
  await scheduler.checkNow();
  await settle();
  assert.equal(checks, 2);
  await clock.advance(5 * 60 * 1000);
  assert.equal(checks, 2, "the scheduled check moved to 10 minutes after the manual one");
  await clock.advance(5 * 60 * 1000);
  assert.equal(checks, 3);
  scheduler.stop();
});

test("failures back off and a success goes back to every 10 minutes", async () => {
  const clock = fakeClock();
  const answers = [{ error: "offline" }, { error: "offline" }, new Error("thrown"), { available: false }];
  const delays = [];
  const scheduler = createUpdateScheduler({
    ...clock,
    runCheck: async () => {
      const answer = answers.shift() || {};
      if (answer instanceof Error) throw answer;
      return answer;
    },
  });
  scheduler.start();
  for (let round = 0; round < 4; round += 1) {
    await settle();
    delays.push(clock.timers[0].delay);
    await clock.advance(clock.timers[0].delay);
  }
  assert.deepEqual(delays, [CHECK_INTERVAL_MS, 2 * CHECK_INTERVAL_MS, 4 * CHECK_INTERVAL_MS, CHECK_INTERVAL_MS]);
  assert.equal(scheduler.failures, 0);
  scheduler.stop();
});

test("backoff stops growing at two hours and waits for GitHub's rate-limit reset", () => {
  assert.equal(nextCheckDelay({ failures: 0 }), CHECK_INTERVAL_MS);
  assert.equal(nextCheckDelay({ failures: 1 }), CHECK_INTERVAL_MS);
  assert.equal(nextCheckDelay({ failures: 3 }), 4 * CHECK_INTERVAL_MS);
  assert.equal(nextCheckDelay({ failures: 50 }), MAX_BACKOFF_MS);
  const now = 1_000_000;
  assert.equal(nextCheckDelay({ failures: 1, retryAt: now + 45 * 60 * 1000, now }), 45 * 60 * 1000);
  // A reset in the past, or one shorter than the backoff, changes nothing.
  assert.equal(nextCheckDelay({ failures: 1, retryAt: now - 5, now }), CHECK_INTERVAL_MS);
  assert.equal(nextCheckDelay({ failures: 4, retryAt: now + 60 * 1000, now }), 8 * CHECK_INTERVAL_MS);
});

test("a rate-limited check waits until the reset before asking again", async () => {
  const clock = fakeClock();
  let checks = 0;
  const scheduler = createUpdateScheduler({
    ...clock,
    runCheck: async () => {
      checks += 1;
      return checks === 1 ? { error: "rate limited", retryAt: clock.now() + 50 * 60 * 1000 } : {};
    },
  });
  scheduler.start();
  await settle();
  await clock.advance(49 * 60 * 1000);
  assert.equal(checks, 1);
  await clock.advance(60 * 1000);
  assert.equal(checks, 2);
  scheduler.stop();
});

test("a download or a waiting restart skips the scheduled check", async () => {
  const clock = fakeClock();
  let checks = 0;
  let busy = false;
  const scheduler = createUpdateScheduler({ ...clock, isBusy: () => busy, runCheck: async () => { checks += 1; return {}; } });
  scheduler.start();
  await settle();
  busy = true;
  await clock.advance(CHECK_INTERVAL_MS * 3);
  assert.equal(checks, 1);
  busy = false;
  await clock.advance(CHECK_INTERVAL_MS);
  assert.equal(checks, 2);
  scheduler.stop();
});
