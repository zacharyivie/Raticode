// What a browser tab does when its page stops: its page process ended (the
// machine slept, the system freed memory, it crashed) or it stopped answering.
// The same rules as the phone's browsers (mobile/android TabRecovery.kt):
//
// - Nothing is shown. A tab whose page process ended reloads by itself once it's
//   on screen, keeping its address and back/forward list. A page process that
//   ended has lost what it held, so reloading loses nothing more, and downloads
//   run outside the page.
// - A page that keeps stopping gets MAX_RESTORES automatic reloads within
//   LOOP_WINDOW_MS, each later than the last, then a quiet Reload button in the
//   tab instead of a dialog.
// - A page that stops answering is left alone while it isn't on screen. On
//   screen, it gets HANG_GRACE_MS to answer again, then its page process is
//   ended and it reloads, unless it plays sound, which means it's still working.

export const MAX_RESTORES = 3;
export const LOOP_WINDOW_MS = 120_000;
export const BACKOFF_MS = [0, 2_000, 8_000];
export const HANG_GRACE_MS = 5_000;

export function createTabRecovery(now = () => Date.now()) {
  let restores = [];
  const recent = () => {
    const time = now();
    restores = restores.filter((at) => time - at <= LOOP_WINDOW_MS);
    return restores.length;
  };
  return {
    // The tab's page process ended: { giveUp: true }, or the delay before the
    // automatic reload once the tab is on screen.
    gone() {
      const count = recent();
      return count >= MAX_RESTORES ? { giveUp: true } : { delayMs: BACKOFF_MS[Math.min(count, BACKOFF_MS.length - 1)] };
    },
    restored() { restores.push(now()); },
    // The user reloaded, went somewhere else in the tab, or closed it.
    forget() { restores = []; },
    recentRestores: recent,
  };
}

// What to do with a page that stopped answering: "leave" it, or "end" it after
// HANG_GRACE_MS on screen.
export function hangAction({ onScreen, audible }) {
  if (!onScreen || audible) return "leave";
  return "end";
}
