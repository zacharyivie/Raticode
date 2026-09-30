const path = require("node:path");
const { Worker } = require("node:worker_threads");

// One worker serializes journal/index changes. Pending snapshots of a thread can
// replace each other, but a deletion is a barrier and its promise waits for disk.
function createArchiveQueue({ createWorker = () => new Worker(path.join(__dirname, "archive-worker.cjs")), maxPending = 128 } = {}) {
  let worker;
  let active;
  let nextId = 0;
  let closed = false;
  const pending = [];
  const drainWaiters = [];
  function drained() {
    if (active || pending.length) return;
    worker?.unref();
    for (const resolve of drainWaiters.splice(0)) resolve();
  }
  function fail(error) {
    const failedWorker = worker;
    worker = undefined;
    if (active) for (const waiter of active.waiters) waiter.reject(error);
    active = undefined;
    for (const job of pending.splice(0)) for (const waiter of job.waiters) waiter.reject(error);
    void failedWorker?.terminate();
    drained();
  }
  function start() {
    if (active || !pending.length) return drained();
    if (!worker) {
      try { worker = createWorker(); }
      catch (error) { fail(error); return; }
      worker.on("message", (message) => {
        if (!active || message.id !== active.id) return;
        const completed = active;
        active = undefined;
        for (const waiter of completed.waiters) {
          if (message.error) waiter.reject(new Error(message.error));
          else waiter.resolve(message.result);
        }
        start();
      });
      worker.on("error", fail);
      const currentWorker = worker;
      worker.on("exit", (code) => {
        if (worker === currentWorker) fail(new Error(`Conversation archive worker stopped (${code}).`));
      });
    }
    active = pending.shift();
    worker.ref();
    try {
      const { id, root, thread, messages, options } = active;
      worker.postMessage({ id, root, thread, messages, options });
    } catch (error) { fail(error); }
  }
  function archive(root, thread, messages, options = {}) {
    if (closed) return Promise.reject(new Error("Conversation archive is closing."));
    if (!thread || typeof thread.id !== "string") return Promise.reject(new Error("Invalid conversation archive payload."));
    return new Promise((resolve, reject) => {
      const lastForThread = pending.findLast((job) => job.root === root && job.thread.id === thread.id);
      if (lastForThread && !lastForThread.options.deleted && !options.deleted) {
        lastForThread.thread = thread;
        lastForThread.messages = messages;
        lastForThread.options = options;
        lastForThread.waiters.push({ resolve, reject });
      } else {
        if (pending.length >= maxPending) return reject(new Error("The archive is busy. Retry after pending conversations finish saving."));
        pending.push({ id: ++nextId, root, thread, messages, options, waiters: [{ resolve, reject }] });
      }
      start();
    });
  }
  async function close() {
    closed = true;
    if (active || pending.length) await new Promise((resolve) => drainWaiters.push(resolve));
    const finishedWorker = worker;
    worker = undefined;
    await finishedWorker?.terminate();
  }
  return { archive, close };
}
module.exports = { createArchiveQueue };
