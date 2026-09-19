const inFlight = new Map();

export function shareInFlight(key, task) {
  const pending = inFlight.get(key);
  if (pending) return pending;
  const promise = Promise.resolve().then(task).finally(() => {
    if (inFlight.get(key) === promise) inFlight.delete(key);
  });
  inFlight.set(key, promise);
  return promise;
}

export function startPolling(refresh, { interval = 2000, immediate = false, target = window, documentTarget = globalThis.document } = {}) {
  let stopped = false;
  let timer;
  let pending;
  const schedule = (delay) => {
    target.clearTimeout(timer);
    if (!stopped) timer = target.setTimeout(run, delay);
  };
  const run = () => {
    if (stopped) return Promise.resolve();
    if (pending) return pending;
    target.clearTimeout(timer);
    if (documentTarget?.hidden) {
      schedule(15000);
      return Promise.resolve();
    }
    const started = Date.now();
    pending = Promise.resolve().then(refresh).catch(() => {}).finally(() => {
      pending = undefined;
      // Preserve the visible two-second cadence while avoiding overlapping work.
      schedule(Math.max(0, interval - (Date.now() - started)));
    });
    return pending;
  };
  const wake = () => {
    if (!documentTarget?.hidden) void run();
  };
  target.addEventListener("focus", wake);
  documentTarget?.addEventListener("visibilitychange", wake);
  if (immediate) void run();
  else schedule(interval);
  return () => {
    stopped = true;
    target.clearTimeout(timer);
    target.removeEventListener("focus", wake);
    documentTarget?.removeEventListener("visibilitychange", wake);
  };
}

const cancellableRequests = new Map();

// Each subscriber owns its cancellation. Only the last departure aborts shared work.
export function shareCancellable(key, task, signal) {
  signal?.throwIfAborted();
  let entry = cancellableRequests.get(key);
  if (!entry) {
    const controller = new AbortController();
    entry = { controller, consumers: 0 };
    entry.promise = Promise.resolve().then(() => task(controller.signal)).finally(() => {
      if (cancellableRequests.get(key) === entry) cancellableRequests.delete(key);
    });
    cancellableRequests.set(key, entry);
  }
  entry.consumers++;
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", cancel);
      if (--entry.consumers === 0) {
        if (cancellableRequests.get(key) === entry) cancellableRequests.delete(key);
        entry.controller.abort();
      }
      callback(value);
    };
    const cancel = () => finish(reject, new DOMException("Discovery cancelled", "AbortError"));
    signal?.addEventListener("abort", cancel, { once: true });
    entry.promise.then(value => finish(resolve, value), error => finish(reject, error));
  });
}
