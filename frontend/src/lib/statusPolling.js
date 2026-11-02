// Polls one request at a time. The next request waits for the previous answer,
// and a slow or failing backend is asked less often, so status requests never
// pile up behind a busy service and crowd out the app's other requests.
export function pollWithoutOverlap(load, {
  interval = 2000, slow = 1000, max = 16000, signal, onError,
  schedule = (run, delay) => setTimeout(run, delay), cancel = timer => clearTimeout(timer), now = () => Date.now(),
} = {}) {
  let timer = null;
  let delay = interval;
  async function run() {
    const started = now();
    try {
      await load(signal);
      delay = now() - started > slow ? Math.min(delay * 2, max) : interval;
    } catch (error) {
      if (signal?.aborted) return;
      onError?.(error);
      delay = Math.min(delay * 2, max);
    }
    if (!signal?.aborted) timer = schedule(run, delay);
  }
  run();
  return () => cancel(timer);
}
