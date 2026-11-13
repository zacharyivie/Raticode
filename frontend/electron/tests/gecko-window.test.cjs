// Gecko's window handling, against a stand-in for the display and for Firefox.
// The real display path is exercised by hand on Xvfb and under KWin's Xwayland.
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { EVENT } = require("../x11-client.cjs");
const { readAuthority, parseDisplay } = require("../x11-client.cjs");
const { NEW_TAB_PAGE, PROFILE_PREFS, WINDOW_CLASS, createGeckoWindow, pageAddress, supportProblem } = require("../gecko-window.cjs");
const { X11_SWITCH, relaunchNeeded, setWantsX11, wantsX11 } = require("../gecko-display.cjs");

const ROOT = 1;
const HOST = 500;
const LINUX_X11 = { platform: "linux", env: { DISPLAY: ":0" } };

function event(type, write) {
  const data = Buffer.alloc(32);
  data[0] = type;
  write(data);
  return { type, data };
}
const created = (window, parent = ROOT, override = 0) => event(EVENT.CreateNotify, (d) => { d.writeUInt32LE(parent, 4); d.writeUInt32LE(window, 8); d[22] = override; });
const property = (window) => event(EVENT.PropertyNotify, (d) => d.writeUInt32LE(window, 4));
const mapped = (window) => event(EVENT.MapNotify, (d) => { d.writeUInt32LE(window, 4); d.writeUInt32LE(window, 8); });
const unmapped = (window) => event(EVENT.UnmapNotify, (d) => { d.writeUInt32LE(window, 4); d.writeUInt32LE(window, 8); });
const clicked = (window) => event(EVENT.ButtonPress, (d) => d.writeUInt32LE(window, 12));

class FakeClient extends EventEmitter {
  constructor() { super(); this.root = ROOT; this.calls = []; this.props = new Map(); }
  async connect() { return this; }
  record(name, ...args) { this.calls.push([name, ...args]); }
  named(name) { return this.calls.filter((call) => call[0] === name); }
  selectInput(...args) { this.record("selectInput", ...args); }
  reparentWindow(...args) { this.record("reparent", ...args); }
  grabClicks(...args) { this.record("grab", ...args); }
  configureWindow(...args) { this.record("configure", ...args); }
  mapWindow(...args) { this.record("map", ...args); }
  unmapWindow(...args) { this.record("unmap", ...args); }
  setInputFocus(...args) { this.record("focus", ...args); }
  allowClick() { this.record("allow"); }
  sendConfigureNotify(...args) { this.record("notify", ...args); }
  async translate() { return { x: 100, y: 50 }; }
  async extension(name) { return name === "SHAPE" ? this.shapeMajor ?? 129 : 0; }
  shapeWindow(...args) { this.record("shape", ...args); }
  unshapeWindow(...args) { this.record("unshape", ...args); }
  async text(window, name) { return this.props.get(`${window}:${name}`) || ""; }
  close() { this.record("close"); }
}

class FakeProcess extends EventEmitter {
  constructor() { super(); this.pid = 4242; this.signals = []; }
  kill(signal) { this.signals.push(signal); if (signal === "SIGTERM" && this.diesOnTerm !== false) setImmediate(() => this.emit("exit", 0)); }
  unref() {}
}

function setup(options = {}) {
  const dataDir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "raticode-gecko-test-"));
  const client = new FakeClient();
  const spawned = [];
  const processes = [];
  const window = Object.assign(new EventEmitter(), { isDestroyed: () => false, getNativeWindowHandle: () => { const b = Buffer.alloc(4); b.writeUInt32LE(HOST, 0); return b; } });
  const states = [];
  const gecko = createGeckoWindow({
    dataDir,
    getWindow: () => window,
    createClient: () => client,
    findExecutable: () => "/usr/bin/firefox",
    findProfiles: options.findProfiles || (() => []),
    createMediaWatch: options.createMediaWatch || (() => ({ start() {}, stop() {}, snapshot: () => null, control: async () => ({ handled: false }) })),
    spawn: (command, args, spawnOptions) => {
      spawned.push({ command, args, options: spawnOptions });
      const child = new FakeProcess();
      if (command !== "/bin/sh") processes.push(child);
      return child;
    },
    onState: (state) => states.push(state),
    ...LINUX_X11,
    windowTimeoutMs: options.windowTimeoutMs || 2000,
    stopTimeoutMs: 50,
    ask: options.ask,
    relaunch: options.relaunch,
  });
  return { client, dataDir, gecko, processes, spawned, states, window, done: () => { fs.rmSync(dataDir, { recursive: true, force: true }); } };
}

function browserWindow(client, id = 77) {
  client.props.set(`${id}:WM_CLASS`, `firefox\0${WINDOW_CLASS}\0`);
  client.props.set(`${id}:WM_WINDOW_ROLE`, "browser");
}

async function started(context, url = "") {
  const opening = context.gecko.handle("open", { url });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  browserWindow(context.client);
  context.client.emit("event", created(77));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  await opening;
}

test("Firefox starts in Raticode's own profile, for X11, with no control port", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context, "https://example.org/");
  const launch = context.spawned[0];
  assert.equal(launch.command, "/usr/bin/firefox");
  assert.deepEqual(launch.args.slice(0, 3), ["--class", WINDOW_CLASS, "--new-instance"]);
  const profile = launch.args[launch.args.indexOf("--profile") + 1];
  assert.ok(profile.startsWith(context.dataDir));
  assert.equal(launch.args.at(-1), "https://example.org/");
  assert.ok(!launch.args.some((arg) => /remote-debugging|marionette/i.test(arg)));
  assert.equal(launch.options.env.GDK_BACKEND, "x11");
  assert.equal(launch.options.env.MOZ_ENABLE_WAYLAND, "0");
  assert.equal(launch.options.env.MOZ_HEADLESS, undefined);
  const prefs = fs.readFileSync(path.join(profile, "user.js"), "utf8");
  for (const name of Object.keys(PROFILE_PREFS)) assert.match(prefs, new RegExp(name.replaceAll(".", "\\.")));
  assert.ok(!/remote\./.test(prefs));
  assert.equal(fs.statSync(profile).mode & 0o777, 0o700);
  context.gecko.close();
});

test("only Firefox's browser window moves into Raticode, before it is shown", async (t) => {
  const context = setup();
  t.after(context.done);
  const opening = context.gecko.handle("open", {});
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  const { client } = context;
  // Firefox's hidden helper window has the class but no role.
  client.props.set(`70:WM_CLASS`, `firefox\0${WINDOW_CLASS}\0`);
  client.emit("event", created(70));
  // Someone else's window, and a menu, are left alone.
  client.props.set(`71:WM_CLASS`, "kate\0kate\0");
  client.props.set(`71:WM_WINDOW_ROLE`, "browser");
  client.emit("event", created(71));
  client.emit("event", created(72, ROOT, 1));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(client.named("reparent").length, 0);
  // The role can arrive after the window does.
  client.props.set(`70:WM_WINDOW_ROLE`, "browser");
  client.emit("event", property(70));
  await new Promise((resolve) => setImmediate(resolve));
  await opening;
  assert.deepEqual(client.named("reparent"), [["reparent", 70, HOST, 0, 0]]);
  assert.deepEqual(client.named("grab"), [["grab", 70]]);
  assert.equal(context.gecko.adoptedWindow, 70);
  // A second browser window, such as Ctrl+N, stays where it is.
  browserWindow(client, 80);
  client.emit("event", created(80));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(client.named("reparent").length, 1);
  context.gecko.close();
});

test("the window follows the slot, hides when asked, and reports its screen position", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { client, gecko } = context;
  client.emit("event", mapped(77));
  await gecko.handle("place", { x: 10.4, y: 20, width: 800, height: 600, visible: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(client.named("configure").at(-1), ["configure", 77, { x: 10, y: 20, width: 800, height: 600 }]);
  assert.deepEqual(client.named("notify").at(-1), ["notify", 77, { x: 110, y: 70, width: 800, height: 600 }]);
  await gecko.handle("place", { visible: false });
  assert.deepEqual(client.named("unmap").at(-1), ["unmap", 77]);
  client.emit("event", unmapped(77));
  // Hidden stays hidden even if Firefox maps itself again.
  const hides = client.named("unmap").length;
  client.emit("event", mapped(77));
  assert.equal(client.named("unmap").length, hides + 1);
  client.emit("event", unmapped(77));
  await gecko.handle("place", { x: 0, y: 0, width: 400, height: 300, visible: true });
  assert.deepEqual(client.named("map").at(-1), ["map", 77]);
  gecko.close();
});

test("a menu over the page cuts a hole in Firefox's window instead of hiding it", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { client, gecko } = context;
  client.emit("event", mapped(77));
  const hides = client.named("unmap").length;
  await gecko.handle("place", { x: 0, y: 40, width: 800, height: 600, visible: true, cutouts: [{ x: 500, y: -10, width: 400, height: 200 }, { x: 900, y: 0, width: 5, height: 5 }, "junk"] });
  // Kept inside the window; anything outside it or malformed is dropped.
  assert.deepEqual(client.named("shape").at(-1), ["shape", 129, 77, 800, 600, [{ x: 500, y: 0, width: 300, height: 190 }]]);
  assert.equal(client.named("unmap").length, hides, "the page stays on screen");
  await gecko.handle("place", { x: 0, y: 40, width: 800, height: 600, visible: true, cutouts: [] });
  assert.deepEqual(client.named("unshape").at(-1), ["unshape", 129, 77]);
  const many = Array.from({ length: 40 }, (_, index) => ({ x: index, y: 0, width: 1, height: 1 }));
  await gecko.handle("place", { x: 0, y: 40, width: 800, height: 600, visible: true, cutouts: many });
  assert.equal(client.named("shape").at(-1)[5].length, 16);
  // A click in the page is counted, so Raticode can close a menu open over it.
  const before = gecko.status().pageClicks;
  client.emit("event", clicked(77));
  assert.equal(gecko.status().pageClicks, before + 1);
  assert.equal(context.states.at(-1).pageClicks, before + 1);
  gecko.close();
});

test("without the SHAPE extension, a menu over the page hides the window as before", async (t) => {
  const context = setup();
  t.after(context.done);
  context.client.shapeMajor = 0;
  await started(context);
  const { client, gecko } = context;
  client.emit("event", mapped(77));
  await gecko.handle("place", { x: 0, y: 40, width: 800, height: 600, visible: true, cutouts: [{ x: 10, y: 10, width: 50, height: 50 }] });
  assert.deepEqual(client.named("unmap").at(-1), ["unmap", 77]);
  assert.equal(client.named("shape").length, 0);
  gecko.close();
});

test("clicks give Firefox the keyboard; Raticode's interface and window focus take it back correctly", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { client, gecko, window } = context;
  client.emit("event", mapped(77));
  await gecko.handle("place", { x: 0, y: 0, width: 400, height: 300, visible: true });
  assert.deepEqual(await gecko.handle("focus-app"), { focused: false });
  assert.equal(client.named("focus").length, 0);
  client.emit("event", clicked(77));
  assert.deepEqual(client.named("focus"), [["focus", 77]]);
  assert.equal(client.named("allow").length, 1);
  window.emit("focus");
  assert.deepEqual(client.named("focus").at(-1), ["focus", 77]);
  assert.deepEqual(await gecko.handle("focus-app"), { focused: true });
  assert.deepEqual(client.named("focus").at(-1), ["focus", HOST]);
  const count = client.named("focus").length;
  window.emit("focus");
  assert.equal(client.named("focus").length, count, "the page doesn't take focus after the user clicked Raticode");
  gecko.close();
});

test("a running Gecko opens addresses as new tabs, and only web addresses", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { spawned, gecko } = context;
  await gecko.handle("open", { url: "https://example.com/a" });
  const helper = spawned.at(-1);
  assert.deepEqual(helper.args.slice(-2), ["--new-tab", "https://example.com/a"]);
  assert.ok(!helper.args.includes("--new-instance"));
  const before = spawned.length;
  await gecko.handle("open", { url: "--headless" });
  await gecko.handle("open", { url: "file:///etc/passwd" });
  await gecko.handle("open", { url: "javascript:alert(1)" });
  assert.equal(spawned.length, before);
  gecko.close();
});

test("the tab bar's + opens an empty Firefox tab, with a fixed address the renderer can't change", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { spawned, gecko } = context;
  const result = await gecko.handle("new-tab", { url: "javascript:alert(1)", args: ["--headless"] });
  assert.equal(result.newTab, true);
  const helper = spawned.at(-1);
  assert.deepEqual(helper.args.slice(-2), ["--new-tab", NEW_TAB_PAGE]);
  assert.equal(NEW_TAB_PAGE, "about:newtab");
  assert.ok(!helper.args.includes("--new-instance"));
  assert.ok(!helper.args.some((arg) => /javascript|headless/.test(arg)));
  gecko.close();
});

test("+ on a Gecko tab whose Firefox isn't running starts Firefox instead of a second helper", async (t) => {
  const context = setup();
  t.after(context.done);
  const opening = context.gecko.handle("new-tab");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  browserWindow(context.client);
  context.client.emit("event", created(77));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  await opening;
  const firefox = context.spawned.filter((item) => item.command === "/usr/bin/firefox");
  assert.equal(firefox.length, 1);
  assert.ok(firefox[0].args.includes("--new-instance"));
  assert.ok(!firefox[0].args.includes("--new-tab"));
  context.gecko.close();
});

test("a window that never appears, or a Firefox that exits, is reported", async (t) => {
  const slow = setup({ windowTimeoutMs: 20 });
  t.after(slow.done);
  await assert.rejects(slow.gecko.handle("open", {}), /window never appeared/);
  assert.deepEqual(slow.processes[0].signals, ["SIGTERM"]);

  const dead = setup();
  t.after(dead.done);
  const opening = dead.gecko.handle("open", {});
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  dead.processes[0].emit("exit", 1);
  await assert.rejects(opening, /closed before its window appeared/);
  assert.equal(dead.gecko.status().running, false);
});

test("profiles are separate folders and switching closes Firefox first", async (t) => {
  const context = setup();
  t.after(context.done);
  await started(context);
  const { gecko, processes, spawned } = context;
  let state = await gecko.handle("profile-create", { name: "Work Stuff!" });
  assert.deepEqual(state.profiles.map((item) => item.id), ["default", "work-stuff"]);
  state = await gecko.handle("profile-create", { name: "work stuff" });
  assert.deepEqual(state.profiles.map((item) => item.id), ["default", "work-stuff", "work-stuff-2"]);
  await assert.rejects(gecko.handle("profile-choose", { id: "../../etc" }), /doesn't exist/);
  state = await gecko.handle("profile-choose", { id: "work-stuff" });
  assert.deepEqual(processes[0].signals, ["SIGTERM"]);
  assert.equal(state.profile, "work-stuff");
  assert.equal(state.running, false);
  await started(context);
  const launch = spawned.filter((item) => item.args.includes("--new-instance")).at(-1);
  assert.match(launch.args[launch.args.indexOf("--profile") + 1], /profiles.work-stuff$/);
  gecko.close();
});

test("import only reads profiles Firefox lists, asks first, and leaves the source alone", async (t) => {
  const source = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "raticode-gecko-source-"));
  t.after(() => fs.rmSync(source, { recursive: true, force: true }));
  fs.mkdirSync(path.join(source, "extensions"));
  fs.writeFileSync(path.join(source, "extensions", "a@example.xpi"), "package");
  fs.writeFileSync(path.join(source, "prefs.js"), 'user_pref("general.smoothScroll", false);\nuser_pref("identity.fxaccounts.account.device.name", "secret");\n');
  fs.writeFileSync(path.join(source, "times.json"), "{}");
  fs.writeFileSync(path.join(source, "extensions.json"), JSON.stringify({ addons: [
    { id: "a@example", type: "extension", location: "app-profile", active: true, version: "1", defaultLocale: { name: "A" } },
    { id: "b@example", type: "extension", location: "app-profile", active: true, version: "1", defaultLocale: { name: "B" } },
  ] }));
  fs.writeFileSync(path.join(source, "extensions", "b@example.xpi"), "other");
  const before = fs.readFileSync(path.join(source, "prefs.js"), "utf8");
  let answer = false;
  const asked = [];
  const context = setup({ findProfiles: () => [{ name: "default-release", path: source, isDefault: true }], ask: async (question) => { asked.push(question); return answer; } });
  t.after(context.done);
  const { gecko, dataDir } = context;
  await assert.rejects(gecko.handle("import-inspect", { path: "/etc" }), /wasn't found/);
  await assert.rejects(gecko.handle("import", { path: "/home/someone", items: { settings: true } }), /wasn't found/);
  await assert.rejects(gecko.handle("import", { path: source, items: {} }), /Choose something/);
  const inspected = await gecko.handle("import-inspect", { path: source });
  assert.deepEqual(inspected.extensions.map((item) => item.id), ["a@example", "b@example"]);
  const request = { path: source, items: { settings: true, extensions: true }, extensionIds: ["a@example"] };
  assert.equal((await gecko.handle("import", request)).cancelled, true);
  assert.equal(fs.existsSync(path.join(dataDir, "gecko", "profiles", "default", "prefs.js")), false);
  answer = true;
  const result = await gecko.handle("import", request);
  assert.match(asked[0].detail, /only read/);
  assert.deepEqual(result.imported.extensions, [{ id: "a@example", name: "A" }]);
  const target = path.join(dataDir, "gecko", "profiles", "default");
  assert.equal(fs.readFileSync(path.join(target, "extensions", "a@example.xpi"), "utf8"), "package");
  assert.equal(fs.existsSync(path.join(target, "extensions", "b@example.xpi")), false);
  const prefs = fs.readFileSync(path.join(target, "prefs.js"), "utf8");
  assert.match(prefs, /general\.smoothScroll/);
  assert.doesNotMatch(prefs, /fxaccounts/);
  assert.equal(fs.readFileSync(path.join(source, "prefs.js"), "utf8"), before);
});

test("the choice of display system: what each platform can do", () => {
  assert.equal(supportProblem({ platform: "linux", env: { DISPLAY: ":0" }, x11Switch: false }), "");
  assert.equal(supportProblem({ platform: "linux", env: { DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-1" }, x11Switch: false }), "restart");
  assert.equal(supportProblem({ platform: "linux", env: { DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-1" }, x11Switch: true }), "");
  assert.match(supportProblem({ platform: "linux", env: { WAYLAND_DISPLAY: "wayland-1" }, x11Switch: false }), /XWayland/);
  assert.match(supportProblem({ platform: "darwin", env: {}, x11Switch: false }), /macOS/);
  assert.match(supportProblem({ platform: "win32", env: {}, x11Switch: false }), /Windows/);
});

test("Raticode restarts on X11 only when Gecko is chosen on a Wayland desktop", (t) => {
  const userData = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "raticode-gecko-display-"));
  t.after(() => fs.rmSync(userData, { recursive: true, force: true }));
  const wayland = { WAYLAND_DISPLAY: "wayland-1", DISPLAY: ":0", XDG_SESSION_TYPE: "wayland" };
  const needs = (argv, env = wayland, platform = "linux") => relaunchNeeded({ userData, argv, env, platform });
  assert.equal(needs(["electron", "."]), false);
  setWantsX11(userData, true);
  assert.equal(wantsX11(userData), true);
  assert.equal(needs(["electron", "."]), true);
  assert.equal(needs(["electron", ".", X11_SWITCH]), false, "no restart loop");
  assert.equal(needs(["electron", "."], { DISPLAY: ":0", XDG_SESSION_TYPE: "x11" }), false);
  assert.equal(needs(["electron", "."], { WAYLAND_DISPLAY: "wayland-1" }), false, "no XWayland, nothing to restart into");
  assert.equal(needs(["electron", "."], wayland, "darwin"), false);
  setWantsX11(userData, false);
  assert.equal(needs(["electron", "."]), false);
});

test("addresses and display names are read strictly", () => {
  assert.equal(pageAddress("https://example.org"), "https://example.org/");
  assert.equal(pageAddress("about:config"), "");
  assert.equal(pageAddress("-P evil"), "");
  assert.deepEqual(parseDisplay(":1.0"), { number: "1", socket: "/tmp/.X11-unix/X1" });
  assert.deepEqual(parseDisplay("unix:2"), { number: "2", socket: "/tmp/.X11-unix/X2" });
  assert.equal(parseDisplay("evil.example:0"), null);
  assert.equal(parseDisplay(""), null);
});

test("the display's cookie is read for the right display", () => {
  const entry = (family, address, display, name, secret) => {
    const field = (value) => { const b = Buffer.alloc(2 + value.length); b.writeUInt16BE(value.length, 0); Buffer.from(value).copy(b, 2); return b; };
    const head = Buffer.alloc(2);
    head.writeUInt16BE(family, 0);
    return Buffer.concat([head, field(address), field(display), field(name), field(secret)]);
  };
  const data = Buffer.concat([
    entry(256, "other", "1", "MIT-MAGIC-COOKIE-1", "wrong"),
    entry(256, "myhost", "0", "MIT-MAGIC-COOKIE-1", "right"),
  ]);
  const read = () => data;
  assert.equal(readAuthority("0", { env: { XAUTHORITY: "/x" }, hostname: "myhost", read }).data.toString(), "right");
  assert.equal(readAuthority("9", { env: { XAUTHORITY: "/x" }, hostname: "myhost", read }), null);
  assert.equal(readAuthority("0", { env: { XAUTHORITY: "/x" }, hostname: "h", read: () => { throw new Error("missing"); } }), null);
});
