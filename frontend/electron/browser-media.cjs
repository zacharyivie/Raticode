// What each browser tab is playing, for the media bar at the top of the
// studio. One watcher per tab works for every engine: it reads the page through
// executeJavaScriptInIsolatedWorld, a script world the page cannot see.
//
// A page's previous and next buttons exist only as handlers it gave
// navigator.mediaSession, which no script world can read or call. So
// mediaPageHook runs in the page before its own scripts and remembers them.
// Raticode reaches it with events on window: a cancelled event means "yes" or
// "done", which needs no shared variable and leaves nothing in the page's DOM.
// A page that fakes the answers only misreports its own buttons.
const { heardSource } = require("./stream-sound.cjs");

const MEDIA_WORLD_ID = 1920;
const COMMANDS = new Set(["play-pause", "previous", "next"]);
// Short sounds, such as a notification, don't flash the bar. A page that
// names what it plays has declared itself a player and shows at once.
const SHOW_DELAY_MS = 1500;
// How long a paused tab keeps the bar, so its Play button can resume.
const PAUSE_GRACE_MS = 30000;
// Reads in a row that find nothing before the bar goes, to bridge track changes.
const MISSING_READS = 2;
const POLL_MS = 1000;
const READ_TIMEOUT_MS = 3000;
const MAX_TEXT = 300;
const MAX_URL = 2048;
const MAX_DATA_URL = 256 * 1024;

// Runs in the page's own world, before its scripts. Injected by source, so it
// can use nothing from this file. browser-preload.cjs holds an identical copy,
// because a sandboxed preload cannot load other files; a test keeps them equal.
/* eslint-disable no-undef */
function mediaPageHook() {
  const proto = globalThis.MediaSession?.prototype;
  const descriptor = proto && Object.getOwnPropertyDescriptor(proto, "setActionHandler");
  if (typeof descriptor?.value !== "function") return;
  const handlers = new Map();
  const wrapped = new Proxy(descriptor.value, {
    apply(target, session, args) {
      const result = Reflect.apply(target, session, args);
      if (typeof args[1] === "function") handlers.set(args[0], args[1]);
      else handlers.delete(args[0]);
      return result;
    },
  });
  Object.defineProperty(proto, "setActionHandler", { ...descriptor, value: wrapped });
  for (const action of ["play", "pause", "previoustrack", "nexttrack"]) {
    window.addEventListener(`raticode-media-has-${action}`, (event) => {
      if (handlers.has(action)) event.preventDefault();
    }, true);
    window.addEventListener(`raticode-media-run-${action}`, (event) => {
      const handler = handlers.get(action);
      if (!handler) return;
      event.preventDefault();
      handler({ action });
    }, true);
  }
}
/* eslint-enable no-undef */

// The element the bar speaks for: the longest one playing with sound,
// otherwise the longest one paused partway through.
function elementSource(tabMuted) {
  return `
  const heard = (media) => !media.ended && (${heardSource(tabMuted)});
  const weight = (media) => Number.isFinite(media.duration) ? media.duration : 1e9;
  const longest = (list) => list.sort((a, b) => weight(b) - weight(a))[0] || null;
  const elements = [...document.querySelectorAll("audio,video")].filter(heard);
  const element = longest(elements.filter((media) => !media.paused)) || longest(elements.filter((media) => media.paused && media.currentTime > 0));
  const session = navigator.mediaSession;`;
}

function mediaReadExpression(tabMuted) {
  return `(() => {${elementSource(tabMuted)}
  const has = (action) => !window.dispatchEvent(new Event("raticode-media-has-" + action, { cancelable: true }));
  const metadata = session && session.metadata;
  let artwork = "";
  try {
    const images = [...(metadata ? metadata.artwork : [])].map((item) => ({
      src: String(item.src || ""),
      size: Math.max(0, ...String(item.sizes || "").split(/\\s+/).map((size) => Number(size.split(/x/i)[0]) || 0)),
    })).filter((item) => item.src).sort((a, b) => a.size - b.size);
    artwork = (images.find((item) => item.size >= 96) || images.at(-1) || { src: "" }).src;
  } catch { /* The bar falls back to the video's poster or an icon. */ }
  return {
    hasElement: Boolean(element),
    playing: Boolean(element && !element.paused),
    playbackState: session ? String(session.playbackState) : "none",
    title: metadata ? String(metadata.title || "") : "",
    artist: metadata ? String(metadata.artist || "") : "",
    artwork,
    poster: element ? String(element.poster || "") : "",
    actions: { play: has("play"), pause: has("pause"), previous: has("previoustrack"), next: has("nexttrack") },
  };
})()`;
}

// Only ever built from COMMANDS. Nothing here is awaited: play() can stay
// pending for as long as the page buffers.
function mediaControlExpression(command, tabMuted) {
  if (!COMMANDS.has(command)) throw new Error(`Unknown media command: ${command}`);
  return `(() => {${elementSource(tabMuted)}
  const run = (action) => !window.dispatchEvent(new Event("raticode-media-run-" + action, { cancelable: true }));
  const command = ${JSON.stringify(command)};
  if (command === "previous") return run("previoustrack");
  if (command === "next") return run("nexttrack");
  if (element) {
    if (element.paused) element.play()?.catch?.(() => {});
    else element.pause();
    return true;
  }
  return run(session && session.playbackState === "playing" ? "pause" : "play");
})()`;
}

// The next state from one reading (null when the page could not be read).
// Returns null when the tab has nothing for the bar.
function nextMediaState(previous, reading, { audible = false, now = Date.now() } = {}) {
  const hasElement = reading?.hasElement === true;
  // An element's own state is exact. Without one (Web Audio, a player inside
  // another site's frame) only the tab's sound or the page's word is left.
  const playing = hasElement ? reading.playing === true : audible === true || reading?.playbackState === "playing";
  if (playing) {
    const startedAt = previous?.playing ? previous.startedAt : now;
    return {
      playing: true,
      shown: previous?.shown === true || Boolean(reading?.title) || now - startedAt >= SHOW_DELAY_MS,
      startedAt,
      pausedAt: 0,
      missing: 0,
      reading: reading || previous?.reading || null,
    };
  }
  if (!previous?.shown) return null;
  const pausedAt = previous.pausedAt || now;
  if (hasElement) return now - pausedAt > PAUSE_GRACE_MS ? null : { ...previous, playing: false, pausedAt, missing: 0, reading };
  const missing = previous.missing + 1;
  return missing >= MISSING_READS ? null : { ...previous, playing: false, pausedAt, missing };
}

function cleanText(value) {
  // eslint-disable-next-line no-control-regex
  return String(value ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").trim().slice(0, MAX_TEXT);
}

function hostnameOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch { return ""; }
}

// The address's host as a comparable name: "printer.local." is "printer.local".
function artworkHost(url) {
  return hostnameOf(url).replace(/\.+$/, "");
}

// The studio loads this image itself, so a page may only name a web address
// or an embedded bitmap, and not one of this computer's or its network's
// services unless the page is served from that same host. This goes by the
// address as written: nothing here looks up what a name resolves to.
function safeArtwork(value, pageUrl) {
  const url = String(value ?? "").trim();
  if (/^data:image\/(?:png|jpe?g|webp|gif);base64,/i.test(url)) return url.length <= MAX_DATA_URL ? url : "";
  if (!/^https?:/i.test(url) || url.length > MAX_URL) return "";
  const host = artworkHost(url);
  if (!host || (isPrivateHost(host) && host !== artworkHost(pageUrl))) return "";
  return url;
}

function isPrivateIpv4([a, b]) {
  return a === 0 || a === 10 || a === 127 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

// [host] comes from URL parsing, which writes every IPv4 form as dotted
// decimal and an IPv4-mapped IPv6 address as ::ffff: and two hex groups.
function isPrivateHost(host) {
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) return isPrivateIpv4(host.split(".").map(Number));
  const mapped = /^\[::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})\]$/i.exec(host);
  if (mapped) return isPrivateIpv4([Number.parseInt(mapped[1], 16) >> 8, Number.parseInt(mapped[1], 16) & 255]);
  return host === "[::]" || host === "[::1]" || /^\[(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):/i.test(host);
}

function describeMedia(state, { title = "", url = "" } = {}) {
  const reading = state.reading || {};
  const actions = reading.actions || {};
  const site = hostnameOf(url);
  return {
    title: cleanText(reading.title) || cleanText(title) || site || "Media in a browser tab",
    artist: cleanText(reading.artist),
    site,
    artwork: safeArtwork(reading.artwork, url) || safeArtwork(reading.poster, url),
    playing: state.playing === true,
    canPlayPause: reading.hasElement === true || Boolean(state.playing ? actions.pause : actions.play),
    canPrevious: actions.previous === true,
    canNext: actions.next === true,
    startedAt: state.startedAt,
  };
}

// Follows one tab. Reads when the tab reports a change, and every second while
// it has something to show.
function watchMedia(contents, { audible, muted, onChange = () => {}, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let state = null;
  let timer = null;
  let epoch = 0;
  let reading = false;
  let readAgain = false;
  let stopped = false;
  // Firefox and Chrome tabs learn the sound stopped only at their next check,
  // so a tab's sound doesn't count on a new page until it next changes.
  let staleSound = false;
  const followUps = new Set();
  const live = () => !stopped && !contents.isDestroyed();
  const sounding = () => audible() && !staleSound;

  function run(code, userGesture) {
    let timeout;
    return Promise.race([
      Promise.resolve().then(() => contents.executeJavaScriptInIsolatedWorld(MEDIA_WORLD_ID, [{ code }], userGesture)),
      new Promise((_resolve, reject) => { timeout = setTimer(() => reject(new Error("The page did not answer.")), READ_TIMEOUT_MS); }),
    ]).finally(() => clearTimer(timeout));
  }

  function apply(next) {
    state = next;
    clearTimer(timer);
    timer = null;
    if (live() && (state || sounding())) {
      timer = setTimer(read, POLL_MS);
      timer.unref?.();
    }
    onChange();
  }

  async function read() {
    if (!live()) return;
    if (reading) { readAgain = true; return; }
    reading = true;
    const started = epoch;
    let result = null;
    try {
      result = await run(mediaReadExpression(muted()), false);
    } catch { /* Loading, closing or refused; treated as an empty reading. */ }
    reading = false;
    if (!live()) return;
    if (started === epoch) apply(nextMediaState(state, result && typeof result === "object" ? result : null, { audible: sounding() }));
    if (readAgain) {
      readAgain = false;
      void read();
    }
  }

  // A new document has none of the old one's media, whatever is still in flight.
  function clear() {
    epoch += 1;
    staleSound = audible();
    if (state) apply(null);
  }

  contents.on("audio-state-changed", () => { staleSound = false; });
  for (const event of ["audio-state-changed", "media-started-playing", "media-paused", "did-stop-loading", "did-navigate-in-page", "page-title-updated"]) {
    contents.on(event, () => {
      if (event === "page-title-updated" && !state) return;
      void read();
    });
  }
  contents.on("did-navigate", clear);
  contents.on("render-process-gone", clear);

  return {
    snapshot() {
      if (!state?.shown || !live()) return null;
      return describeMedia(state, { title: contents.getTitle(), url: contents.getURL() });
    },
    async control(command) {
      if (!COMMANDS.has(command)) throw new Error(`Unknown media command: ${command}`);
      if (!state?.shown || !live()) return { handled: false };
      let handled = false;
      try {
        handled = (await run(mediaControlExpression(command, muted()), true)) === true;
      } catch { /* The bar reports that the control did nothing. */ }
      // The page takes a moment to act; show the result as soon as it has.
      for (const delay of [100, 500]) {
        const followUp = setTimer(() => { followUps.delete(followUp); void read(); }, delay);
        followUps.add(followUp);
      }
      return { handled };
    },
    stop() {
      stopped = true;
      clearTimer(timer);
      for (const followUp of followUps) clearTimer(followUp);
      followUps.clear();
    },
  };
}

module.exports = {
  COMMANDS,
  MEDIA_WORLD_ID,
  PAUSE_GRACE_MS,
  SHOW_DELAY_MS,
  describeMedia,
  mediaControlExpression,
  mediaPageHook,
  mediaReadExpression,
  nextMediaState,
  safeArtwork,
  watchMedia,
};
