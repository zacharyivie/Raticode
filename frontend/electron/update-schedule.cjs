// When Raticode looks for a new version, and when it reminds the user that one is waiting.
// Kept free of Electron so the timing and the once-a-day rule can be tested with a fake clock.

const CHECK_INTERVAL_MS = 10 * 60 * 1000;
// A failing check waits longer each time: 10, 20, 40, 80 minutes, then every 2 hours.
const MAX_BACKOFF_MS = 2 * 60 * 60 * 1000;
// Someone who touched the keyboard or mouse within this long is using the computer.
const ACTIVE_IDLE_SECONDS = 5 * 60;

/** How long to wait before the next automatic check. */
function nextCheckDelay({ failures = 0, retryAt = 0, now = Date.now(), intervalMs = CHECK_INTERVAL_MS } = {}) {
  const backoff = failures > 0 ? Math.min(intervalMs * 2 ** Math.min(failures - 1, 10), MAX_BACKOFF_MS) : intervalMs;
  // GitHub said when it would answer again; asking earlier only fails again.
  const untilRetry = Number(retryAt) > now ? Number(retryAt) - now : 0;
  return Math.max(backoff, Math.min(untilRetry, 6 * MAX_BACKOFF_MS));
}

/**
 * Runs `runCheck` on start and then every 10 minutes, never two at once. A manual check that
 * arrives while one is running gets the running one's answer instead of starting another.
 * `runCheck` resolves to the updater state; `state.error` marks a failure and
 * `state.retryAt` (epoch ms) is when GitHub allows another request.
 */
function createUpdateScheduler({
  runCheck,
  isBusy = () => false,
  intervalMs = CHECK_INTERVAL_MS,
  now = () => Date.now(),
  setTimer = (callback, delay) => setTimeout(callback, delay),
  clearTimer = (handle) => clearTimeout(handle),
  onSettled = () => {},
} = {}) {
  let timer = null;
  let running = null;
  let started = false;
  let failures = 0;
  let retryAt = 0;
  let lastCheckAt = 0;

  function schedule(delay) {
    if (timer) clearTimer(timer);
    timer = null;
    if (!started) return;
    timer = setTimer(() => {
      timer = null;
      void tick();
    }, delay);
    timer?.unref?.();
  }

  async function tick() {
    // A download, a signature check or a waiting restart owns the updater; checking again would reset it.
    if (isBusy()) {
      schedule(intervalMs);
      return;
    }
    await check({ manual: false });
  }

  function check({ manual = false } = {}) {
    if (running) return running;
    lastCheckAt = now();
    running = (async () => {
      let state;
      try {
        state = await runCheck({ manual });
      } catch (error) {
        state = { error: error instanceof Error ? error.message : String(error || "Update check failed") };
      }
      if (state?.error) {
        failures += 1;
        retryAt = Number(state.retryAt) || 0;
      } else {
        failures = 0;
        retryAt = 0;
      }
      return state;
    })().finally(() => {
      running = null;
      schedule(nextCheckDelay({ failures, retryAt, now: now(), intervalMs }));
      try { onSettled(); } catch { /* A reminder failing must not stop the schedule. */ }
    });
    return running;
  }

  return {
    start() {
      if (started) return;
      started = true;
      void tick();
    },
    stop() {
      started = false;
      if (timer) clearTimer(timer);
      timer = null;
    },
    checkNow: (options) => check({ manual: true, ...options }),
    get started() { return started; },
    get checking() { return Boolean(running); },
    get failures() { return failures; },
    get lastCheckAt() { return lastCheckAt; },
    get scheduled() { return Boolean(timer); },
  };
}

/** The local calendar day, such as 2026-11-13. */
function localDay(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * Whether to show the once-a-day "update Raticode" reminder now. One reminder per calendar day,
 * the first time the user is at Raticode that day. Days away add nothing: the record holds the
 * last day shown, not a count, so coming back after a week shows one reminder for the newest version.
 */
function reminderDecision({ available, latestVersion, record, today, userActive }) {
  const last = record && typeof record === "object" ? record : {};
  if (!available || !latestVersion || !userActive) return { show: false, record: last };
  if (last.day === today) return { show: false, record: last };
  return { show: true, record: { day: today, version: String(latestVersion) } };
}

function readReminderRecord(fs, file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!value || typeof value !== "object") return {};
    return {
      day: /^\d{4}-\d{2}-\d{2}$/.test(String(value.day || "")) ? String(value.day) : "",
      version: typeof value.version === "string" ? value.version.slice(0, 64) : "",
    };
  } catch {
    return {};
  }
}

function writeReminderRecord(fs, file, record) {
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify({ day: record.day, version: record.version })}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

module.exports = {
  ACTIVE_IDLE_SECONDS,
  CHECK_INTERVAL_MS,
  MAX_BACKOFF_MS,
  createUpdateScheduler,
  localDay,
  nextCheckDelay,
  readReminderRecord,
  reminderDecision,
  writeReminderRecord,
};
