// The media bar's decisions, without a browser. stream-tab-media.test.cjs runs
// the same scripts against real headless browsers.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  PAUSE_GRACE_MS,
  SHOW_DELAY_MS,
  describeMedia,
  mediaControlExpression,
  mediaPageHook,
  nextMediaState,
  safeArtwork,
  watchMedia,
} = require("../browser-media.cjs");

const playingElement = { hasElement: true, playing: true, title: "", actions: {} };
const pausedElement = { hasElement: true, playing: false, title: "", actions: {} };
const empty = { hasElement: false, playing: false, playbackState: "none", title: "", actions: {} };

test("the preload's copy of the page hook is identical to the shared one", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "browser-preload.cjs"), "utf8");
  const copy = /function mediaPageHook\(\) \{[\s\S]*?\n\}\n/.exec(source)?.[0].trim();
  assert.equal(copy, String(mediaPageHook));
});

function pageWithHook() {
  const context = vm.createContext({ Map, Proxy, Reflect, Object, globalThis: null, window: new EventTarget(), Event });
  context.globalThis = context;
  vm.runInContext("class MediaSession { setActionHandler(action, handler) { if (action === 'bad') throw new TypeError('bad action'); this.last = [action, handler]; } }; globalThis.MediaSession = MediaSession;", context);
  vm.runInContext(`(${mediaPageHook})()`, context);
  const ask = (name) => !context.window.dispatchEvent(new Event(`raticode-media-${name}`, { cancelable: true }));
  return { context, ask, session: vm.runInContext("new MediaSession()", context) };
}

test("the page hook remembers handlers, runs them on request and leaves the page's API working", () => {
  const { ask, session } = pageWithHook();
  assert.equal(ask("has-nexttrack"), false, "no handler has been set");
  assert.equal(ask("run-nexttrack"), false, "running with no handler reports nothing done");
  let details = null;
  const handler = (value) => { details = value; };
  session.setActionHandler("nexttrack", handler);
  assert.equal(session.last[1], handler, "the page's real call still happens");
  assert.equal(ask("has-nexttrack"), true);
  assert.equal(ask("run-nexttrack"), true);
  assert.deepEqual({ ...details }, { action: "nexttrack" });
  session.setActionHandler("nexttrack", null);
  assert.equal(ask("has-nexttrack"), false, "clearing the handler removes the button");
  assert.throws(() => session.setActionHandler("bad", handler), /bad action/);
  assert.equal(ask("has-play"), false);
});

test("the page hook does nothing where the page has no media session", () => {
  const context = vm.createContext({ Map, Proxy, Reflect, Object, globalThis: null, window: new EventTarget() });
  context.globalThis = context;
  assert.doesNotThrow(() => vm.runInContext(`(${mediaPageHook})()`, context));
});

test("a page's sound waits a moment before showing; a page that names its media shows at once", () => {
  const now = 1000000;
  const first = nextMediaState(null, playingElement, { now });
  assert.equal(first.shown, false);
  assert.equal(nextMediaState(first, playingElement, { now: now + SHOW_DELAY_MS - 1 }).shown, false);
  assert.equal(nextMediaState(first, playingElement, { now: now + SHOW_DELAY_MS }).shown, true);
  assert.equal(nextMediaState(null, { ...playingElement, title: "Song" }, { now }).shown, true);
});

test("a short sound never reaches the bar", () => {
  const now = 1000000;
  const started = nextMediaState(null, playingElement, { now });
  assert.equal(nextMediaState(started, empty, { now: now + 500 }), null);
  assert.equal(nextMediaState(started, pausedElement, { now: now + 500 }), null);
});

test("a paused element alone never opens the bar", () => {
  assert.equal(nextMediaState(null, pausedElement, { now: 1 }), null);
});

test("pausing keeps the bar for its Play button, then it goes", () => {
  const now = 1000000;
  const shown = nextMediaState(null, { ...playingElement, title: "Song" }, { now });
  const paused = nextMediaState(shown, pausedElement, { now: now + 10 });
  assert.equal(paused.playing, false);
  assert.equal(paused.shown, true);
  assert.equal(nextMediaState(paused, pausedElement, { now: now + 10 + PAUSE_GRACE_MS }).playing, false);
  assert.equal(nextMediaState(paused, pausedElement, { now: now + 11 + PAUSE_GRACE_MS }), null);
  const resumed = nextMediaState(paused, playingElement, { now: now + 2000 });
  assert.equal(resumed.playing, true);
  assert.equal(resumed.pausedAt, 0);
});

test("a gap between tracks keeps the bar, and a stop removes it", () => {
  const now = 1000000;
  const shown = nextMediaState(null, { ...playingElement, title: "Song" }, { now });
  const gap = nextMediaState(shown, empty, { now: now + 1000 });
  assert.equal(gap.shown, true);
  assert.equal(nextMediaState(gap, playingElement, { now: now + 1500 }).playing, true);
  assert.equal(nextMediaState(gap, empty, { now: now + 2000 }), null);
});

test("without an element, only the tab's sound or the page's word counts as playing", () => {
  const now = 1000000;
  assert.equal(nextMediaState(null, empty, { now }), null);
  assert.equal(nextMediaState(null, empty, { now, audible: true }).playing, true);
  assert.equal(nextMediaState(null, { ...empty, playbackState: "playing" }, { now }).playing, true);
  assert.equal(nextMediaState(null, null, { now, audible: true }).playing, true, "an unreadable page that makes sound still plays");
  // An element's own state outranks the tab's sound, which lags after a pause.
  assert.equal(nextMediaState(null, pausedElement, { now, audible: true }), null);
});

test("controls say only what the page can do", () => {
  const state = { playing: true, startedAt: 5 };
  const read = (reading) => describeMedia({ ...state, reading }, { title: "Page", url: "https://music.test/watch" });
  assert.deepEqual(read({ hasElement: true, actions: {} }), { title: "Page", artist: "", site: "music.test", artwork: "", playing: true, canPlayPause: true, canPrevious: false, canNext: false, startedAt: 5 });
  const session = read({ hasElement: false, actions: { pause: true, next: true } });
  assert.deepEqual([session.canPlayPause, session.canPrevious, session.canNext], [true, false, true]);
  assert.equal(read({ hasElement: false, actions: {} }).canPlayPause, false, "sound the page gives no way to control");
  assert.equal(describeMedia({ ...state, playing: false, reading: { hasElement: false, actions: { pause: true } } }, {}).canPlayPause, false, "paused needs a play handler");
});

test("titles fall back to the page, the site, then a plain description, and stay short", () => {
  const state = { playing: true, startedAt: 5, reading: { hasElement: true, actions: {} } };
  assert.equal(describeMedia({ ...state, reading: { ...state.reading, title: "Song", artist: "Band" } }, { title: "Page" }).title, "Song");
  assert.equal(describeMedia(state, { title: "Page", url: "https://a.test/" }).title, "Page");
  assert.equal(describeMedia(state, { title: "", url: "https://a.test/" }).title, "a.test");
  assert.equal(describeMedia(state, {}).title, "Media in a browser tab");
  const long = describeMedia({ ...state, reading: { ...state.reading, title: `a\u0000b${"x".repeat(900)}` } }, {}).title;
  assert.equal(long.length, 300);
  assert.equal(long.slice(0, 3), "a b");
});

test("artwork is limited to web images and embedded bitmaps", () => {
  const page = "https://music.test/watch";
  assert.equal(safeArtwork("https://i.test/a.jpg", page), "https://i.test/a.jpg");
  assert.equal(safeArtwork("http://i.test/a.jpg", page), "http://i.test/a.jpg");
  assert.equal(safeArtwork("data:image/png;base64,AAAA", page), "data:image/png;base64,AAAA");
  for (const value of ["javascript:alert(1)", "file:///etc/passwd", "blob:https://music.test/x", "raticode-media://x", "data:image/svg+xml;base64,AAAA", "data:text/html,x", "", null]) {
    assert.equal(safeArtwork(value, page), "", String(value));
  }
  assert.equal(safeArtwork(`data:image/png;base64,${"A".repeat(300 * 1024)}`, page), "");
  assert.equal(safeArtwork(`https://i.test/${"a".repeat(3000)}`, page), "");
  for (const host of ["localhost", "127.0.0.1", "10.0.0.8", "192.168.1.1", "172.16.0.1", "169.254.169.254", "[::1]", "[fe80::1]", "printer.local", "app.localhost", "0.0.0.0"]) {
    assert.equal(safeArtwork(`http://${host}/a.jpg`, page), "", host);
  }
  assert.equal(safeArtwork("http://192.168.1.5/art.jpg", "http://192.168.1.5/player"), "http://192.168.1.5/art.jpg", "a player served from that host");
});

test("artwork can't reach private addresses through other ways of writing them", () => {
  const page = "https://music.test/watch";
  const blocked = [
    "localhost.:8765", "localhost..", "printer.local.", "app.localhost.", "127.0.0.1.",
    "[::ffff:127.0.0.1]:8765", "[::ffff:7f00:1]", "[::ffff:192.168.1.1]", "[::ffff:10.1.2.3]", "[::ffff:172.31.0.1]", "[::ffff:169.254.169.254]", "[::ffff:100.64.0.1]", "[::ffff:0.1.2.3]",
    "[::]", "[::1]", "[0:0:0:0:0:0:0:1]", "[fc00::1]", "[fd12::1]", "[fe80::1]", "[febf::1]",
    "0.1.2.3", "0.0.0.0", "100.64.0.0", "100.100.1.1", "100.127.255.255",
    "2130706433", "0x7f.1", "017700000001",
  ];
  for (const host of blocked) assert.equal(safeArtwork(`http://${host}/a.jpg`, page), "", host);
  const allowed = ["1.2.3.4", "100.63.255.255", "100.128.0.1", "172.32.0.1", "[::ffff:8.8.8.8]", "[2606:4700::1]", "10.example.com", "127.example.com", "music.test."];
  for (const host of allowed) assert.equal(safeArtwork(`http://${host}/a.jpg`, page), `http://${host}/a.jpg`, host);
  assert.equal(safeArtwork("http://192.168.1.5./art.jpg", "http://192.168.1.5/player"), "http://192.168.1.5./art.jpg", "the page's own host, however it is written");
  assert.equal(safeArtwork("http://printer.local./art.jpg", "http://printer.local/player"), "http://printer.local./art.jpg");
  assert.equal(safeArtwork("http://[::ffff:192.168.1.5]/art.jpg", "http://192.168.1.6/player"), "", "another host on the page's network");
});

test("media commands are limited to the three the bar has", () => {
  for (const command of ["play-pause", "previous", "next"]) assert.match(mediaControlExpression(command, false), new RegExp(`"${command}"`));
  assert.throws(() => mediaControlExpression("eval(1)", false), /Unknown media command/);
});

function fakeTab(reading) {
  const tab = new EventEmitter();
  Object.assign(tab, {
    audible: false,
    closed: false,
    reading,
    runs: [],
    isDestroyed: () => tab.closed,
    getTitle: () => "Page",
    getURL: () => "https://music.test/",
    async executeJavaScriptInIsolatedWorld(world, sources, gesture) {
      tab.runs.push({ world, code: sources[0].code, gesture });
      if (tab.held) await tab.held;
      return sources[0].code.includes("raticode-media-run") ? tab.commandResult : tab.reading;
    },
  });
  return tab;
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const named = { hasElement: true, playing: true, title: "Song", actions: { next: true } };

function watch(tab, changes = []) {
  return watchMedia(tab, { audible: () => tab.audible, muted: () => false, onChange: () => changes.push(tab.closed) });
}

test("a tab that starts playing appears, and leaving the page removes it at once", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  tab.emit("media-started-playing");
  await tick();
  assert.equal(watcher.snapshot().title, "Song");
  assert.equal(watcher.snapshot().canNext, true);
  tab.emit("did-navigate", {}, "https://other.test/");
  assert.equal(watcher.snapshot(), null);
  watcher.stop();
});

test("a same-page change keeps the bar and updates what it says", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  tab.emit("media-started-playing");
  await tick();
  tab.reading = { ...named, title: "Next song" };
  tab.emit("did-navigate-in-page", {}, "https://music.test/next", true);
  await tick();
  assert.equal(watcher.snapshot().title, "Next song");
  watcher.stop();
});

test("a reading from the page before a navigation is thrown away", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  let release;
  tab.held = new Promise((resolve) => { release = resolve; });
  tab.emit("media-started-playing");
  await tick();
  tab.emit("did-navigate", {}, "https://other.test/");
  release();
  tab.held = null;
  await tick();
  await tick();
  assert.equal(watcher.snapshot(), null);
  watcher.stop();
});

test("a tab's lingering sound doesn't start a bar on the page it moved to", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  tab.audible = true;
  tab.emit("media-started-playing");
  await tick();
  tab.reading = { ...empty };
  tab.emit("did-navigate", {}, "https://other.test/");
  tab.emit("did-stop-loading");
  await tick();
  assert.equal(watcher.snapshot(), null);
  // The tab's next report that it is making sound counts again.
  tab.reading = { ...empty, title: "Web Audio" };
  tab.emit("audio-state-changed", { audible: true });
  await tick();
  assert.equal(watcher.snapshot().title, "Web Audio");
  watcher.stop();
});

test("a crashed tab loses the bar, and a closed one stops being read", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  tab.emit("media-started-playing");
  await tick();
  tab.emit("render-process-gone", {}, { reason: "crashed" });
  assert.equal(watcher.snapshot(), null);
  tab.emit("media-started-playing");
  await tick();
  tab.closed = true;
  assert.equal(watcher.snapshot(), null);
  const runs = tab.runs.length;
  tab.emit("media-started-playing");
  await tick();
  assert.equal(tab.runs.length, runs);
  watcher.stop();
});

test("a control counts as the user's gesture, reports the page's answer and is refused when there's no bar", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  assert.deepEqual(await watcher.control("next"), { handled: false }, "nothing is showing");
  tab.emit("media-started-playing");
  await tick();
  tab.commandResult = true;
  assert.deepEqual(await watcher.control("next"), { handled: true });
  const command = tab.runs.at(-1);
  assert.equal(command.gesture, true);
  assert.equal(command.world, 1920);
  tab.commandResult = false;
  assert.deepEqual(await watcher.control("previous"), { handled: false });
  await assert.rejects(watcher.control("reload"), /Unknown media command/);
  watcher.stop();
});

test("a control on a page that refuses scripts reports that it did nothing", async () => {
  const tab = fakeTab(named);
  const watcher = watch(tab);
  tab.emit("media-started-playing");
  await tick();
  tab.executeJavaScriptInIsolatedWorld = async () => { throw new Error("Rem can't act on it"); };
  assert.deepEqual(await watcher.control("play-pause"), { handled: false });
  watcher.stop();
});

test("the bar is the first thing in the main content, so every area has it", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "..", "src", "pages", "App.jsx"), "utf8");
  const section = app.indexOf('<section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-x border-line');
  assert.ok(section > 0);
  const bar = app.indexOf("<BrowserMediaBar viewedTabs={[...viewedCodePaths, ...viewedBrowserMedia(viewedBrowserPaths, browserTabs)]} />", section);
  assert.ok(bar > section);
  const areas = ["<OrganizationHome", "<SecondBrainWorkspace", "<RemSlot", "<SwarmWorkspace", "<CodeWorkspace"].map((name) => app.indexOf(name, section));
  for (const area of areas) assert.ok(area > bar, "an area renders above the bar");
  assert.equal(app.indexOf("<BrowserMediaBar", bar + 1), -1);
  // It's told which file and web tabs are on screen, so it skips the tab that plays,
  // and Gecko's player while the Raticode (gecko) tab is in view.
  assert.match(app, /onViewedPathsChange=\{setViewedCodePaths\}/);
  assert.match(app, /onViewedPathsChange=\{setViewedBrowserPaths\}/);
  assert.equal(app.slice(section, bar).match(/>/g).length, 1, "something renders above the bar");
});

function stoppedLoading(contents) {
  const calls = { mute: 0, check: 0 };
  Object.assign(contents, {
    muted: false,
    url: "https://music.test/",
    destroyed: false,
    applyMute: async () => { calls.mute += 1; },
    sound: { check: async () => { calls.check += 1; } },
    evaluateIn: async () => null,
    refreshHistory: async () => {},
    refreshState: async () => {},
    settleWaiters() {},
    markActive() {},
    commitUrl() {},
    emit() {},
  });
  return calls;
}

test("a new page in a Firefox, Chrome or Edge tab keeps a site's muted video muted unless the tab is muted", () => {
  const { ChromiumContents } = require("../chromium-contents.cjs");
  const { FirefoxContents } = require("../firefox-contents.cjs");
  const finish = {
    chromium: (tab) => ChromiumContents.prototype.finishLoading.call(tab),
    firefox: (tab) => FirefoxContents.prototype.handleEvent.call(tab, "browsingContext.load", { url: "https://music.test/" }),
  };
  for (const load of Object.values(finish)) {
    const tab = {};
    const calls = stoppedLoading(tab);
    load(tab);
    assert.deepEqual(calls, { mute: 0, check: 1 });
    tab.muted = true;
    load(tab);
    assert.deepEqual(calls, { mute: 1, check: 1 });
  }
});
