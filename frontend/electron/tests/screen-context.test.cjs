// Show Rem your screen (screen-context.cjs): one screenshot, only when asked, for one message.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  MAX_BYTES,
  captureSize,
  captureSupport,
  cleanName,
  createScreenContext,
  createScreenShortcut,
  encode,
} = require("../screen-context.cjs");

const electronDir = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(electronDir, name), "utf8");

// A stand-in for Electron's NativeImage.
function picture({ width = 1920, height = 1080, png = 1000, jpeg = 500, empty = false } = {}) {
  return {
    isEmpty: () => empty,
    getSize: () => ({ width, height }),
    toPNG: () => Buffer.alloc(png, 1),
    toJPEG: (quality) => Buffer.alloc(typeof jpeg === "function" ? jpeg(quality) : jpeg, 2),
    toDataURL: () => "data:image/png;base64,AAAA",
    resize: ({ width: w, height: h }) => picture({ width: w, height: h, png, jpeg: 400 }),
  };
}

function fakeCapturer(sources) {
  const calls = [];
  return {
    calls,
    getSources: async (options) => {
      calls.push(options);
      const list = typeof sources === "function" ? sources(options) : sources;
      return list.filter((source) => options.types.includes(source.id.startsWith("screen:") ? "screen" : "window"));
    },
  };
}

const fakeScreen = {
  getAllDisplays: () => [{ id: 1, size: { width: 1920, height: 1080 }, scaleFactor: 1 }, { id: 2, size: { width: 2560, height: 1440 }, scaleFactor: 2 }],
  getCursorScreenPoint: () => ({ x: 3000, y: 10 }),
  getDisplayNearestPoint: () => ({ id: 2 }),
};

const SOURCES = [
  { id: "screen:1:0", name: "Screen 1", display_id: "1", thumbnail: picture() },
  { id: "screen:2:0", name: "Screen 2", display_id: "2", thumbnail: picture({ width: 2560, height: 1440 }) },
  { id: "window:11:0", name: "Terminal \u202e evil", thumbnail: picture() },
  { id: "window:12:0", name: "Raticode", thumbnail: picture() },
  { id: "window:13:0", name: "Minimized", thumbnail: picture({ empty: true }) },
];

function context(options = {}) {
  const capturer = options.capturer ?? fakeCapturer(SOURCES);
  const screenContext = createScreenContext({
    desktopCapturer: capturer,
    screen: fakeScreen,
    systemPreferences: options.systemPreferences ?? null,
    platform: options.platform ?? "linux",
    support: options.support ?? { kind: "list" },
    ownSourceIds: () => ["window:12:0"],
  });
  return { screenContext, capturer };
}

test("each desktop is described honestly: a list, the Wayland portal, X11 inside Wayland, or nothing", () => {
  assert.deepEqual(captureSupport({ platform: "win32", env: {} }), { kind: "list" });
  assert.deepEqual(captureSupport({ platform: "darwin", env: {} }), { kind: "list" });
  assert.deepEqual(captureSupport({ platform: "linux", env: { DISPLAY: ":0" } }), { kind: "list" });
  assert.deepEqual(captureSupport({ platform: "linux", env: { WAYLAND_DISPLAY: "wayland-0" } }), { kind: "portal" });
  assert.deepEqual(captureSupport({ platform: "linux", env: { XDG_SESSION_TYPE: "wayland" }, ozonePlatform: "x11" }), { kind: "xwayland" });
  assert.deepEqual(captureSupport({ platform: "freebsd", env: {} }), { kind: "unsupported" });
});

test("nothing is listed or captured until the user turns it on", async () => {
  const { screenContext, capturer } = context();
  assert.equal((await screenContext.list()).code, "off");
  assert.equal((await screenContext.capture({ sourceId: "screen:1:0" })).code, "off");
  assert.equal(capturer.calls.length, 0);
  screenContext.configure({ enabled: true });
  assert.equal((await screenContext.list()).ok, true);
  screenContext.configure({ enabled: false });
  assert.equal((await screenContext.capture({})).code, "off");
});

test("the chooser lists screens then windows, small, without Raticode's own or empty windows", async () => {
  const { screenContext, capturer } = context();
  screenContext.configure({ enabled: true });
  const result = await screenContext.list();
  assert.deepEqual(result.sources.map((source) => [source.kind, source.id]), [
    ["screen", "screen:1:0"], ["screen", "screen:2:0"], ["window", "window:11:0"],
  ]);
  // Hidden direction marks can't disguise a window's name.
  assert.equal(result.sources[2].name, "Terminal evil");
  assert.deepEqual(capturer.calls[0].thumbnailSize, { width: 320, height: 200 });
  assert.equal(capturer.calls[0].fetchWindowIcons, false);
});

test("a picked window is captured at full size, as bytes in memory", async () => {
  const { screenContext, capturer } = context();
  screenContext.configure({ enabled: true });
  const shot = await screenContext.capture({ sourceId: "window:11:0" });
  assert.equal(shot.ok, true);
  assert.equal(shot.kind, "window");
  assert.equal(shot.type, "image/png");
  assert.ok(shot.data instanceof Uint8Array);
  assert.equal(shot.data.byteLength, 1000);
  assert.deepEqual(capturer.calls[0].types, ["window"]);
  // The largest screen in real pixels, at most 2560 on its long side.
  assert.deepEqual(capturer.calls[0].thumbnailSize, { width: 2560, height: 1440 });
  assert.equal((await screenContext.capture({ sourceId: "window:99:0" })).code, "gone");
});

test("the shortcut takes the screen the pointer is on", async () => {
  const { screenContext } = context();
  screenContext.configure({ enabled: true });
  const shot = await screenContext.capture({ pointer: true });
  assert.equal(shot.ok, true);
  assert.equal(shot.name, "Screen 2");
});

test("on Wayland the desktop's dialog chooses, and closing it captures nothing", async () => {
  const chosen = fakeCapturer([SOURCES[2]]);
  const { screenContext } = context({ capturer: chosen, support: { kind: "portal" } });
  screenContext.configure({ enabled: true });
  assert.deepEqual(await screenContext.list(), { ok: true, portal: true, sources: [] });
  assert.equal(chosen.calls.length, 0);
  const shot = await screenContext.capture({});
  assert.equal(shot.ok, true);
  assert.deepEqual(chosen.calls[0].types, ["screen", "window"]);
  const closed = context({ capturer: fakeCapturer([]), support: { kind: "portal" } }).screenContext;
  closed.configure({ enabled: true });
  assert.equal((await closed.capture({})).code, "cancelled");
  const broken = context({ capturer: { getSources: async () => { throw new Error("portal missing: window titles"); } }, support: { kind: "portal" } }).screenContext;
  broken.configure({ enabled: true });
  const failed = await broken.capture({});
  assert.equal(failed.code, "failed");
  // The reason can name windows, so it isn't passed on.
  assert.doesNotMatch(failed.error, /window titles/);
  assert.match(failed.error, /xdg-desktop-portal/);
});

test("an empty picture is refused with the likely reason", async () => {
  const blank = fakeCapturer([{ id: "screen:1:0", name: "Screen", thumbnail: picture({ empty: true }) }]);
  const x11 = context({ capturer: blank, support: { kind: "xwayland" } }).screenContext;
  x11.configure({ enabled: true });
  assert.match((await x11.capture({ pointer: true })).error, /X11 app on a Wayland desktop/);
  const plain = context({ capturer: blank }).screenContext;
  plain.configure({ enabled: true });
  assert.equal((await plain.capture({ pointer: true })).code, "empty");
});

test("macOS asks for Screen Recording once and captures nothing until it's allowed", async () => {
  let state = "not-determined";
  const preferences = { getMediaAccessStatus: (kind) => (kind === "screen" ? state : "granted") };
  const { screenContext, capturer } = context({ platform: "darwin", systemPreferences: preferences });
  screenContext.configure({ enabled: true });
  assert.equal(screenContext.status().permission, "not-determined");
  const asked = await screenContext.capture({ pointer: true });
  assert.equal(asked.code, "permission");
  assert.match(asked.error, /Screen & System Audio Recording/);
  // The one request is what makes macOS show its prompt; it's 1x1 and thrown away.
  assert.deepEqual(capturer.calls.map((call) => call.thumbnailSize), [{ width: 1, height: 1 }]);
  state = "denied";
  assert.equal((await screenContext.list()).code, "permission");
  assert.equal(capturer.calls.length, 1);
  state = "granted";
  assert.equal((await screenContext.capture({ pointer: true })).ok, true);
});

test("pictures stay under Rem's 5 MB limit: PNG, then JPEG, then a smaller JPEG", () => {
  assert.equal(encode(picture({ png: 1000 })).type, "image/png");
  assert.equal(encode(picture({ png: MAX_BYTES + 1, jpeg: 1000 })).type, "image/jpeg");
  const shrunk = encode(picture({ width: 5120, height: 2880, png: MAX_BYTES + 1, jpeg: MAX_BYTES + 1 }));
  assert.equal(shrunk.type, "image/jpeg");
  assert.equal(shrunk.data.length, 400);
  assert.equal(encode({ ...picture({ png: MAX_BYTES + 1, jpeg: MAX_BYTES + 1 }), resize: () => picture({ png: MAX_BYTES + 1, jpeg: MAX_BYTES + 1 }) }), null);
  assert.deepEqual(captureSize({ getAllDisplays: () => [{ size: { width: 3840, height: 2160 }, scaleFactor: 1 }] }), { width: 2560, height: 1440 });
  assert.equal(cleanName("", "Screen"), "Screen");
  assert.equal(cleanName("a".repeat(200), "x").length, 80);
});

test("one screenshot at a time", async () => {
  let release;
  const slow = { getSources: (options) => new Promise((resolve) => { release = () => resolve(SOURCES.filter((source) => options.types.includes(source.id.startsWith("screen:") ? "screen" : "window"))); }) };
  const { screenContext } = context({ capturer: slow });
  screenContext.configure({ enabled: true });
  const first = screenContext.capture({ pointer: true });
  assert.equal((await screenContext.capture({ pointer: true })).code, "busy");
  release();
  assert.equal((await first).ok, true);
});

test("the Ask Rem about your screen shortcut registers only when on, and reports conflicts", () => {
  const registered = new Map();
  const shortcuts = {
    register: (accelerator, callback) => { if (accelerator === "Control+Alt+Shift+F1") return false; registered.set(accelerator, callback); return true; },
    unregister: (accelerator) => registered.delete(accelerator),
  };
  let pressed = 0;
  const shortcut = createScreenShortcut({ globalShortcut: shortcuts, onPress: () => { pressed += 1; }, now: () => 42 });
  assert.equal(shortcut.configure({ enabled: false, binding: "Ctrl+Alt+Shift+Space" }).registered, false);
  assert.equal(registered.size, 0);
  const on = shortcut.configure({ enabled: true, binding: "Ctrl+Alt+Shift+Space" });
  assert.equal(on.registered, true);
  assert.equal(on.accelerator, "Control+Alt+Shift+Space");
  registered.get("Control+Alt+Shift+Space")();
  assert.equal(pressed, 1);
  assert.equal(shortcut.status().lastPressedAt, 42);
  assert.match(shortcut.configure({ enabled: true, binding: "Ctrl+Alt+Shift+F1" }).error, /Another app already uses/);
  assert.equal(registered.size, 0);
  assert.match(shortcut.configure({ enabled: true, binding: "Shift+KeyA" }).error, /Add Ctrl, Alt/);
  shortcut.configure({ enabled: true, binding: "Ctrl+Alt+Shift+Space" });
  shortcut.dispose();
  assert.equal(registered.size, 0);
});

test("on a Wayland desktop the screen shortcut is asked of the desktop with the dictation one, and says why it can't work", async () => {
  const updates = [];
  let answer = { route: "kde", state: "registered", trigger: "Ctrl+Alt+S", message: "" };
  const desktop = { update: (id, entry) => { updates.push([id, entry]); return Promise.resolve(entry ? answer : { state: "off" }); } };
  const grabbed = [];
  let pressed = 0;
  const shortcut = createScreenShortcut({ globalShortcut: { register: (key) => grabbed.push(key), unregister() {} }, desktopShortcuts: desktop, onPress: () => { pressed += 1; }, now: () => 7 });
  const on = await shortcut.configure({ enabled: true, binding: "Ctrl+Alt+KeyS" });
  assert.deepEqual(updates, [["ask-rem-about-screen", { description: "Ask Rem about your screen", accelerator: "Control+Alt+S" }]]);
  assert.equal(on.registered, true);
  assert.equal(on.trigger, "Ctrl+Alt+S");
  assert.deepEqual(grabbed, []);
  shortcut.press();
  assert.equal(pressed, 1);
  assert.equal(shortcut.status().lastPressedAt, 7);
  answer = { route: "manual", state: "manual", trigger: "", message: "" };
  assert.match((await shortcut.configure({ enabled: true, binding: "Ctrl+Alt+KeyD" })).error, /doesn't let apps add shortcuts/);
  assert.equal(shortcut.desktopChanged({ shortcuts: { "ask-rem-about-screen": { state: "registered", trigger: "Meta+S" } } }).trigger, "Meta+S");
  const off = await shortcut.configure({ enabled: false, binding: "Ctrl+Alt+KeyD" });
  assert.equal(off.registered, false);
  assert.deepEqual(updates.at(-1), ["ask-rem-about-screen", null]);
});

test("main.js wires screenshots to the main window only, on request, without the overlay in the picture", () => {
  const main = read("main.js");
  assert.match(main, /const screenContext = createScreenContext\(\{/);
  assert.match(main, /listScreenSources: \(\) => screenContext\.list\(\)/);
  assert.match(main, /captureScreen: \(_event, \{ sourceId \} = \{\}\) => screenContext\.capture/);
  // The shortcut needs Dictate to Rem from any app on too, and captures before the panel shows.
  const ask = main.slice(main.indexOf("async function askAboutScreen"), main.indexOf("const screenShortcut = createScreenShortcut"));
  assert.match(ask, /!globalDictation\.status\(\)\.enabled/);
  assert.ok(ask.indexOf("screenContext.capture") < ask.indexOf("globalDictation.setNote"));
  assert.match(ask, /mainWindow\.webContents\.send\("gofer:global-dictation", \{ action: "screen"/);
  assert.match(main, /app\.on\("will-quit", \(\) => screenShortcut\.dispose\(\)\)/);
  const ipc = read("ipc-handlers.cjs");
  for (const channel of ["gofer:screen-context-configure", "gofer:screen-context-sources", "gofer:screen-context-capture", "gofer:screen-context-privacy"]) {
    assert.ok(ipc.includes(channel), channel);
  }
  // The picture's bytes skip the path-grant rewrite, which would turn them into an object.
  assert.match(read("preload.cjs"), /capture: \(sourceId = ""\) => ipcRenderer\.invoke\("gofer:screen-context-capture"/);
});

test("the screenshot code never logs, saves or keeps a picture", () => {
  const source = read("screen-context.cjs").split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
  assert.doesNotMatch(source, /console\.|applicationLog|\.write\(|writeFile|require\("node:fs"\)|require\("fs"\)|setInterval|setTimeout/);
});
