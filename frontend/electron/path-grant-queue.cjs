// The backend's DesktopPathGrantStore TTL is 15 minutes. Renew one minute early,
// measured from request start with a monotonic clock, not from acknowledgment.
const GRANT_CACHE_MS = 14 * 60 * 1000;

function createPathGrantQueue({ now = () => performance.now() } = {}) {
  const cached = new Map();
  const pending = new Map();
  const queue = [];
  const activeIds = new Set();
  let generation = 0;
  let active = 0;

  function drain() {
    while (active < 4) {
      const index = queue.findIndex(job => !activeIds.has(job.handle.grantId));
      if (index < 0) return;
      const job = queue.splice(index, 1)[0];
      active++;
      activeIds.add(job.handle.grantId);
      const started = now();
      Promise.resolve().then(() => {
        if (job.generation !== generation) throw new Error("Backend restarted. Retry folder access.");
        return job.register();
      }).then(value => {
        if (job.generation !== generation) throw new Error("Backend restarted. Retry folder access.");
        cached.set(job.handle.grantId, { path: job.handle.path, expires: started + GRANT_CACHE_MS });
        return value;
      }).catch(error => {
        if (job.generation === generation) cached.delete(job.handle.grantId);
        throw error;
      }).finally(() => {
        if (pending.get(job.key) === job.promise) pending.delete(job.key);
        active--;
        activeIds.delete(job.handle.grantId);
        drain();
      }).then(job.resolve, job.reject);
    }
  }

  return {
    reset() {
      generation++;
      cached.clear();
      pending.clear();
      for (const job of queue.splice(0)) job.reject(new Error("Backend restarted. Retry folder access."));
    },
    register(handle, register) {
      const key = JSON.stringify([handle.grantId, handle.path]);
      if (pending.has(key)) return pending.get(key);
      const ack = cached.get(handle.grantId);
      if (!activeIds.has(handle.grantId) && ack?.path === handle.path && ack.expires > now()) return Promise.resolve();
      cached.delete(handle.grantId);
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      pending.set(key, promise);
      queue.push({ key, handle: { ...handle }, register, resolve, reject, promise, generation });
      drain();
      return promise;
    },
  };
}

module.exports = { createPathGrantQueue, GRANT_CACHE_MS };
