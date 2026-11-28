// The Browser's own window: where it opens, what it may do, and how closing it
// hands the tabs back first. electron/tests/browser-window.browser.cjs moves
// real tabs, including a real Firefox window, through it.
require("./test-tmp.cjs");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FRAME_NAME, MIN_SIZE, createBrowserWindowManager, placeOnScreen } = require("../browser-window.cjs");
const { tempFolder } = require("./temp-folder.cjs");

const LEFT = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
const RIGHT = { id: 2, workArea: { x: 1920, y: 0, width: 2560, height: 1400 } };

test("the window opens where it was left while that screen is connected", () => {
  assert.deepEqual(placeOnScreen({ x: 2100, y: 100, width: 1000, height: 800 }, [LEFT, RIGHT], LEFT), { x: 2100, y: 100, width: 1000, height: 800 });
});

test("a window left on a screen that is gone opens centred on Raticode's screen", () => {
  assert.deepEqual(placeOnScreen({ x: 2100, y: 100, width: 1000, height: 800 }, [LEFT], LEFT), { x: 460, y: 120, width: 1000, height: 800 });
  // Only a sliver of the title bar on a screen isn't enough to grab it.
  assert.equal(placeOnScreen({ x: 1900, y: 100, width: 800, height: 600 }, [LEFT], LEFT).x, 560);
});

test("first use, bad saved values and tiny screens get a usable size", () => {
  assert.deepEqual(placeOnScreen(null, [LEFT], LEFT), { x: 360, y: 90, width: 1200, height: 860 });
  assert.deepEqual(placeOnScreen({ x: "a", width: 10, height: -5 }, [LEFT], LEFT), { x: 360, y: 90, width: 1200, height: 860 });
  const small = { workArea: { x: 0, y: 0, width: 800, height: 600 } };
  const placed = placeOnScreen(null, [small], small);
  assert.ok(placed.width <= 800 && placed.height <= 600 && placed.width >= MIN_SIZE.width && placed.height >= MIN_SIZE.height);
  // A saved window bigger than its screen shrinks to fit.
  assert.deepEqual(placeOnScreen({ x: 0, y: 0, width: 3000, height: 2000 }, [LEFT], LEFT), { x: 0, y: 0, width: 1920, height: 1040 });
});

class FakeWindow extends EventEmitter {
  constructor(bounds = { x: 0, y: 0, width: 1200, height: 860 }) {
    super();
    this.bounds = bounds;
    this.destroyed = false;
    this.contents = Object.assign(new EventEmitter(), { sent: [], openHandler: null, isDestroyed: () => false, isCrashed: () => false, send(channel, payload) { this.sent.push([channel, payload]); }, setWindowOpenHandler(handler) { this.openHandler = handler; } });
    this.webContents = this.contents;
  }
  isDestroyed() { return this.destroyed; }
  getBounds() { return { ...this.bounds }; }
  getNormalBounds() { return { ...this.bounds }; }
  setBounds(next) { this.bounds = { ...next }; }
  isMaximized() { return false; }
  isMinimized() { return false; }
  setMenu() {}
  show() { this.shown = true; }
  focus() { this.focused = true; }
  restore() {}
  maximize() { this.maximized = true; }
  close() {
    const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    this.emit("close", event);
    if (!event.defaultPrevented) this.destroy();
  }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.emit("closed");
  }
}

function setup({ displays = [LEFT, RIGHT], saved, beforeClose, closeTimeoutMs } = {}) {
  // Removed when the test that called setup() ends.
  const dataDir = tempFolder("raticode-browser-window-");
  if (saved) fs.writeFileSync(path.join(dataDir, "browser-window.json"), JSON.stringify(saved));
  const main = new FakeWindow({ x: 0, y: 0, width: 1440, height: 960 });
  const screen = Object.assign(new EventEmitter(), {
    displays,
    getAllDisplays() { return this.displays; },
    getPrimaryDisplay() { return this.displays[0]; },
    getDisplayMatching() { return this.displays[0]; },
  });
  const order = [];
  const manager = createBrowserWindowManager({
    dataDir,
    getMainWindow: () => main,
    screen,
    beforeClose: beforeClose || (async (window) => { order.push(window ? "views and firefox back" : "firefox back"); }),
    ...(closeTimeoutMs ? { closeTimeoutMs } : {}),
  });
  manager.watchScreens();
  return { dataDir, main, screen, manager, order };
}

test("only the studio's own blank Browser window may open", () => {
  const { manager } = setup({ saved: { x: 2000, y: 50, width: 900, height: 700 } });
  assert.equal(manager.windowOpenRequest({ url: "https://example.com/", frameName: "" }), null, "other pop-ups keep the old rule");
  assert.deepEqual(manager.windowOpenRequest({ url: "https://evil.example/", frameName: FRAME_NAME }), { action: "deny" });
  const allowed = manager.windowOpenRequest({ url: "about:blank", frameName: FRAME_NAME });
  assert.equal(allowed.action, "allow");
  assert.equal(allowed.outlivesOpener, false);
  const options = allowed.overrideBrowserWindowOptions;
  assert.deepEqual([options.x, options.y, options.width, options.height], [2000, 50, 900, 700]);
  // No preload, no Node, no views of its own.
  assert.deepEqual(options.webPreferences, { contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: false, backgroundThrottling: false });
  assert.equal("preload" in options.webPreferences, false);
});

test("a second Pop out shows the open window instead of opening another", () => {
  const { manager } = setup();
  const child = new FakeWindow();
  assert.equal(manager.adopt(child, { frameName: FRAME_NAME }), true);
  assert.deepEqual(manager.windowOpenRequest({ url: "about:blank", frameName: FRAME_NAME }), { action: "deny" });
  assert.equal(child.focused, true);
  const extra = new FakeWindow();
  assert.equal(manager.adopt(extra, { frameName: FRAME_NAME }), false);
  assert.equal(extra.destroyed, true);
});

test("the window can't navigate, open windows or host views of its own", () => {
  const { manager } = setup();
  const child = new FakeWindow();
  manager.adopt(child, { frameName: FRAME_NAME });
  for (const name of ["will-navigate", "will-redirect", "will-attach-webview"]) {
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    child.contents.emit(name, event);
    assert.equal(event.prevented, true, name);
  }
  assert.deepEqual(child.contents.openHandler({ url: "https://example.com/" }), { action: "deny" });
});

test("closing the window asks the studio to take the tabs back, then closes after Firefox leaves it", async () => {
  const { manager, main, order } = setup();
  const child = new FakeWindow();
  manager.adopt(child, { frameName: FRAME_NAME });
  assert.equal(manager.window, child);
  child.close();
  assert.equal(child.destroyed, false, "the window waits for the studio");
  assert.deepEqual(main.contents.sent.at(-1), ["gofer:browser-window", { open: true, closeRequested: true }]);
  // The studio moved the tabs back and asks to close.
  const closing = manager.handle("close");
  assert.equal(manager.window, null, "a closing window no longer hosts Firefox");
  await closing;
  assert.deepEqual(order, ["views and firefox back"]);
  assert.equal(child.destroyed, true);
  assert.deepEqual(main.contents.sent.at(-1), ["gofer:browser-window", { open: false }]);
  assert.equal(manager.state().open, false);
});

test("the window closes by itself when the studio is gone", async () => {
  const { manager, main, order } = setup();
  const child = new FakeWindow();
  manager.adopt(child, { frameName: FRAME_NAME });
  main.contents.isCrashed = () => true;
  child.close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(child.destroyed, true);
  assert.deepEqual(order, ["views and firefox back"]);
});

test("a studio that stops answering can't keep the window open: it closes after a while, its pages handed back first", async () => {
  const { manager, order } = setup({ closeTimeoutMs: 30 });
  const child = new FakeWindow();
  manager.adopt(child, { frameName: FRAME_NAME });
  child.close();
  assert.equal(child.destroyed, false, "the studio gets its chance first");
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(child.destroyed, true);
  assert.deepEqual(order, ["views and firefox back"]);
});

test("its place is saved, and a screen that goes away brings it back onto one that is left", async () => {
  const { manager, screen, dataDir } = setup();
  const child = new FakeWindow({ x: 2100, y: 100, width: 1000, height: 800 });
  manager.adopt(child, { frameName: FRAME_NAME });
  child.emit("move");
  await new Promise((resolve) => setTimeout(resolve, 500));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dataDir, "browser-window.json"), "utf8")), { x: 2100, y: 100, width: 1000, height: 800, maximized: false });
  screen.displays = [LEFT];
  screen.emit("display-removed");
  assert.deepEqual(child.getBounds(), { x: 460, y: 120, width: 1000, height: 800 });
  // Still on a screen: left alone.
  child.setBounds({ x: 100, y: 100, width: 800, height: 600 });
  screen.emit("display-metrics-changed");
  assert.deepEqual(child.getBounds(), { x: 100, y: 100, width: 800, height: 600 });
});

test("quitting closes the window at once", () => {
  const { manager } = setup();
  const child = new FakeWindow();
  manager.adopt(child, { frameName: FRAME_NAME });
  manager.destroy();
  assert.equal(child.destroyed, true);
});
