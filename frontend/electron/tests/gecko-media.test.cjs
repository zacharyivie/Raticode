// Following Gecko's playback over MPRIS, with a stand-in for the session bus.
// gecko-media-bar.browser.cjs runs it against a real Firefox.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { PAUSE_GRACE_MS } = require("../browser-media.cjs");
const { MAX_ARTWORK_BYTES, readArtworkFile, readingFrom, watchGeckoMedia } = require("../gecko-media.cjs");

const GECKO_PID = 4242;
const GECKO_NAME = "org.mpris.MediaPlayer2.firefox.instance_1_77";
const EVERYDAY_NAME = "org.mpris.MediaPlayer2.firefox.instance_1_5";

function properties(overrides = {}) {
  const { status = "Playing", title = "Song", artist = ["Band"], art, next = true, previous = true, play = true, pause = true, url = "https://music.test/watch" } = overrides;
  return { type: "a{sv}", data: [{
    PlaybackStatus: { type: "s", data: status },
    Metadata: { type: "a{sv}", data: {
      "xesam:title": { type: "s", data: title },
      "xesam:artist": { type: "as", data: artist },
      "xesam:url": { type: "s", data: url },
      ...(art ? { "mpris:artUrl": { type: "s", data: art } } : {}),
    } },
    CanGoNext: { type: "b", data: next },
    CanGoPrevious: { type: "b", data: previous },
    CanPlay: { type: "b", data: play },
    CanPause: { type: "b", data: pause },
  }] };
}

function fakeBus() {
  const bus = {
    calls: [],
    names: [EVERYDAY_NAME, GECKO_NAME, "org.gnome.Shell", "org.mpris.MediaPlayer2.spotify"],
    owners: { [EVERYDAY_NAME]: 999, [GECKO_NAME]: GECKO_PID },
    player: properties(),
    failPlayer: false,
    async run(args) {
      bus.calls.push(args);
      if (args[3] === "ListNames") return { type: "as", data: [bus.names] };
      if (args[3] === "GetConnectionUnixProcessID") return { type: "u", data: [bus.owners[args[5]]] };
      if (args[3] === "GetAll") {
        if (bus.failPlayer) throw new Error("name has no owner");
        return bus.player;
      }
      return null;
    },
  };
  return bus;
}

// Timers run only when the test says so.
function setup(bus = fakeBus(), options = {}) {
  let clock = 1000000;
  const timers = new Map();
  let nextTimer = 1;
  let changes = 0;
  const watcher = watchGeckoMedia({
    pid: () => GECKO_PID,
    title: () => "Window title",
    onChange: () => { changes += 1; },
    run: bus.run,
    readArtwork: async () => "data:image/png;base64,AAAA",
    now: () => clock,
    setTimer: (callback, delay) => { timers.set(nextTimer, { callback, delay }); return nextTimer++; },
    clearTimer: (id) => timers.delete(id),
    ...options,
  });
  async function settle() { for (let turn = 0; turn < 8; turn += 1) await new Promise((resolve) => setImmediate(resolve)); }
  async function tick(ms = 0) {
    clock += ms;
    for (const [id, timer] of [...timers]) { timers.delete(id); timer.callback(); }
    await settle();
  }
  return { bus, watcher, tick, settle, changes: () => changes, timers, advance: (ms) => { clock += ms; } };
}

test("only a player held by Gecko's own process is followed, never the user's everyday Firefox", async () => {
  const { bus, watcher, settle } = setup();
  watcher.start();
  await settle();
  assert.equal(watcher.snapshot().title, "Song");
  const read = bus.calls.filter((args) => args[3] === "GetAll").map((args) => args[0]);
  assert.deepEqual([...new Set(read)], [GECKO_NAME]);
  watcher.stop();
});

test("with only the everyday Firefox playing, Gecko shows nothing", async () => {
  const bus = fakeBus();
  bus.names = [EVERYDAY_NAME];
  const { watcher, settle } = setup(bus);
  watcher.start();
  await settle();
  assert.equal(watcher.snapshot(), null);
  assert.equal(bus.calls.some((args) => args[3] === "GetAll"), false);
  watcher.stop();
});

test("what Firefox publishes becomes the bar's title, artist, picture and controls", async () => {
  const bus = fakeBus();
  bus.player = properties({ art: "file:///x/firefox-mpris/1_0.png", next: false });
  const { watcher, settle } = setup(bus);
  watcher.start();
  await settle();
  const { startedAt, ...media } = watcher.snapshot();
  assert.ok(startedAt);
  assert.deepEqual(media, { title: "Song", artist: "Band", site: "music.test", artwork: "data:image/png;base64,AAAA", playing: true, canPlayPause: true, canPrevious: true, canNext: false, id: "gecko" });
  watcher.stop();
});

test("a title with no details falls back to Gecko's window, and page text stays plain data", async () => {
  const bus = fakeBus();
  bus.player = properties({ title: "", artist: [""], url: "" });
  const { watcher, settle, tick } = setup(bus);
  watcher.start();
  await settle();
  await tick(2000);
  assert.deepEqual([watcher.snapshot().title, watcher.snapshot().artist, watcher.snapshot().artwork], ["Window title", "", ""]);
  bus.player = properties({ title: `"quoted" \\ <b>${"x".repeat(900)}`, artist: ["A", "B"] });
  await tick(1000);
  assert.equal(watcher.snapshot().title.length, 300);
  assert.equal(watcher.snapshot().title.startsWith('"quoted" \\ <b>'), true);
  assert.equal(watcher.snapshot().artist, "A, B");
  watcher.stop();
});

test("a pause keeps the bar for its Play button, then it goes", async () => {
  const bus = fakeBus();
  const { watcher, settle, tick } = setup(bus);
  watcher.start();
  await settle();
  bus.player = properties({ status: "Paused" });
  await tick(1000);
  assert.equal(watcher.snapshot().playing, false);
  await tick(PAUSE_GRACE_MS);
  assert.equal(watcher.snapshot().playing, false);
  await tick(1000);
  assert.equal(watcher.snapshot(), null);
  watcher.stop();
});

test("when Firefox stops publishing the bar goes after a moment, and returns with the next player", async () => {
  const bus = fakeBus();
  const { watcher, settle, tick } = setup(bus);
  watcher.start();
  await settle();
  bus.failPlayer = true;
  bus.names = [];
  await tick(1000);
  assert.ok(watcher.snapshot(), "a track change doesn't flash the bar");
  await tick(1000);
  assert.equal(watcher.snapshot(), null);
  bus.failPlayer = false;
  bus.names = [GECKO_NAME];
  await tick(2000);
  assert.equal(watcher.snapshot().title, "Song");
  watcher.stop();
});

test("a stopped player counts as nothing playing", async () => {
  const bus = fakeBus();
  bus.player = properties({ status: "Stopped" });
  const { watcher, settle } = setup(bus);
  watcher.start();
  await settle();
  assert.equal(watcher.snapshot(), null);
  assert.equal(readingFrom(bus.player, ""), null);
  watcher.stop();
});

test("closing Firefox clears the bar at once and stops asking the bus", async () => {
  const { bus, watcher, settle, timers, changes } = setup();
  watcher.start();
  await settle();
  assert.equal(changes(), 1);
  watcher.stop();
  assert.equal(watcher.snapshot(), null);
  assert.equal(changes(), 2);
  assert.equal(timers.size, 0);
  const count = bus.calls.length;
  await settle();
  assert.equal(bus.calls.length, count);
});

test("an answer that arrives after Firefox closed is ignored", async () => {
  const bus = fakeBus();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const original = bus.run;
  bus.run = async (args) => { if (args[3] === "GetAll") await held; return original(args); };
  const { watcher, settle } = setup(bus);
  watcher.start();
  await settle();
  watcher.stop();
  release();
  await settle();
  assert.equal(watcher.snapshot(), null);
});

test("Firefox restarting while the bus is still answering keeps the watcher following", async () => {
  const bus = fakeBus();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const original = bus.run;
  bus.run = async (args) => { if (args[3] === "GetAll") await held; return original(args); };
  const { watcher, settle, timers } = setup(bus);
  watcher.start();
  await settle();
  watcher.stop();
  watcher.start();
  release();
  await settle();
  assert.equal(watcher.snapshot()?.title, "Song", "the new Firefox was never read");
  assert.equal(timers.size, 1, "polling did not continue");
  watcher.stop();
});

test("controls call the player's own method after checking it is still Gecko's", async () => {
  const { bus, watcher, settle } = setup();
  watcher.start();
  await settle();
  for (const [command, method] of [["next", "Next"], ["previous", "Previous"], ["play-pause", "PlayPause"]]) {
    bus.calls.length = 0;
    assert.deepEqual(await watcher.control(command), { handled: true });
    assert.equal(bus.calls[0][3], "GetConnectionUnixProcessID");
    assert.deepEqual(bus.calls[1], [GECKO_NAME, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", method]);
  }
  await assert.rejects(watcher.control("Quit"), /Unknown media command/);
  watcher.stop();
});

test("controls Firefox says it can't do, or a player now held by someone else, do nothing", async () => {
  const bus = fakeBus();
  bus.player = properties({ next: false, previous: false, pause: false });
  const { watcher, settle } = setup(bus);
  assert.deepEqual(await watcher.control("next"), { handled: false }, "nothing is showing yet");
  watcher.start();
  await settle();
  assert.equal(watcher.snapshot().canPlayPause, false);
  for (const command of ["next", "previous", "play-pause"]) assert.deepEqual(await watcher.control(command), { handled: false });
  bus.player = properties();
  watcher.stop();
  const second = setup(bus);
  second.watcher.start();
  await second.settle();
  bus.owners[GECKO_NAME] = 999;
  bus.calls.length = 0;
  assert.deepEqual(await second.watcher.control("next"), { handled: false });
  assert.equal(bus.calls.some((args) => args[3] === "Next"), false);
  second.watcher.stop();
});

test("a missing busctl leaves the bar empty and says so once", async () => {
  const logs = [];
  const bus = { calls: [], run: async () => { throw Object.assign(new Error("not found"), { code: "ENOENT" }); } };
  const { watcher, settle, tick } = setup(bus, { log: (level, message) => logs.push([level, message]) });
  watcher.start();
  await settle();
  await tick(2000);
  assert.equal(watcher.snapshot(), null);
  assert.equal(logs.length, 1);
  assert.match(logs[0][1], /busctl/);
  watcher.stop();
});

test("artwork is read only from Firefox's own folder, as a small real image file", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gecko-art-test-"));
  try {
    const folder = path.join(root, "firefox-mpris");
    fs.mkdirSync(folder);
    const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(16)]);
    const write = (name, bytes) => { const file = path.join(folder, name); fs.writeFileSync(file, bytes); return pathToFileURL(file).href; };
    assert.equal(await readArtworkFile(write("1_0.png", png)), `data:image/png;base64,${png.toString("base64")}`);
    assert.match(await readArtworkFile(write("1_1.jpg", Buffer.from([0xff, 0xd8, 0xff, 0xe0]))), /^data:image\/jpeg;base64,/);
    assert.equal(await readArtworkFile(write("1_2.png", "<svg onload=alert(1)>")), "", "not an image by its contents");
    assert.equal(await readArtworkFile(write("1_3.png", Buffer.concat([png, Buffer.alloc(MAX_ARTWORK_BYTES)]))), "", "too large");
    const outside = path.join(root, "secret.png");
    fs.writeFileSync(outside, png);
    assert.equal(await readArtworkFile(pathToFileURL(outside).href), "", "outside Firefox's folder");
    const link = path.join(folder, "1_4.png");
    fs.symlinkSync(outside, link);
    assert.equal(await readArtworkFile(pathToFileURL(link).href), "", "a link out of the folder");
    assert.equal(await readArtworkFile(pathToFileURL(path.join(folder, "gone.png")).href), "");
    assert.equal(await readArtworkFile("not a url"), "");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
