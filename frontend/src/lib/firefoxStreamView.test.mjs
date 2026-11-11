import test from "node:test";
import assert from "node:assert/strict";
import { attachFirefoxView, firefoxKeyAction, safeCursor, wheelPixels } from "./firefoxStreamView.js";
import { DEFAULT_APP_SETTINGS, normalizeAppSettings } from "./settings.js";

test("keys in a Firefox view are forwarded, except paste and input-method composition", () => {
  assert.equal(firefoxKeyAction({ key: "a", ctrlKey: false }, "linux"), "forward");
  assert.equal(firefoxKeyAction({ key: "Enter" }, "linux"), "forward");
  assert.equal(firefoxKeyAction({ key: "v", ctrlKey: true }, "linux"), "paste");
  assert.equal(firefoxKeyAction({ key: "v", metaKey: true }, "darwin"), "paste");
  // Ctrl+V on a Mac is a plain key for the page.
  assert.equal(firefoxKeyAction({ key: "v", ctrlKey: true }, "darwin"), "forward");
  assert.equal(firefoxKeyAction({ key: "c", ctrlKey: true }, "win32"), "copy");
  assert.equal(firefoxKeyAction({ key: "X", ctrlKey: true, shiftKey: true }, "linux"), "cut");
  assert.equal(firefoxKeyAction({ key: "a", isComposing: true }, "linux"), "ignore");
  assert.equal(firefoxKeyAction({ key: "Process" }, "linux"), "ignore");
  assert.equal(firefoxKeyAction({ key: "Dead" }, "linux"), "ignore");
});

test("wheel deltas become pixels whatever the device reports", () => {
  assert.deepEqual(wheelPixels({ deltaMode: 0, deltaX: 3, deltaY: 120 }), { dx: 3, dy: 120 });
  assert.deepEqual(wheelPixels({ deltaMode: 1, deltaX: 0, deltaY: 3 }), { dx: 0, dy: 48 });
  assert.deepEqual(wheelPixels({ deltaMode: 2, deltaX: 0, deltaY: 1 }, 600), { dx: 0, dy: 600 });
});

test("only standard cursor names from the page reach the studio", () => {
  assert.equal(safeCursor("pointer"), "pointer");
  assert.equal(safeCursor(" TEXT "), "text");
  assert.equal(safeCursor("url(https://evil.example/c.png), auto"), "default");
  assert.equal(safeCursor(""), "default");
});

test("the web browser setting defaults to Raticode and accepts only known engines", () => {
  assert.equal(DEFAULT_APP_SETTINGS.browser.engine, "raticode");
  assert.equal(normalizeAppSettings({ browser: { engine: "firefox" } }).browser.engine, "firefox");
  assert.equal(normalizeAppSettings({ browser: { engine: "chrome" } }).browser.engine, "chrome");
  assert.equal(normalizeAppSettings({ browser: { engine: "netscape" } }).browser.engine, "raticode");
  assert.equal(normalizeAppSettings({}).browser.engine, "raticode");
});

// A minimal DOM: enough for the view to attach, listen and draw.
function fakeDom() {
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.style = {};
      this.dataset = {};
      this.children = [];
      this.listeners = {};
      this.attributes = {};
      this.clientWidth = 640;
      this.clientHeight = 480;
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
    appendChild(item) { this.append(item); return item; }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    dispatch(type, event = {}) {
      const full = { preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...event };
      for (const listener of this.listeners[type] || []) listener(full);
      return full;
    }
    remove() { this.parent && (this.parent.children = this.parent.children.filter((item) => item !== this)); }
    focus() { this.focused = true; }
    getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
    getContext() { return { drawImage: (...args) => { this.drawn = args; } }; }
    setPointerCapture() {}
    releasePointerCapture() {}
  }
  const document = {
    visibilityState: "visible",
    createElement: (tag) => new Element(tag),
    addEventListener() {},
    removeEventListener() {},
  };
  const window = {
    devicePixelRatio: 2,
    requestAnimationFrame: (callback) => { callback(); return 1; },
    cancelAnimationFrame() {},
    setTimeout,
    createImageBitmap: async () => ({ width: 1280, height: 960, close() {} }),
  };
  document.defaultView = window;
  return { document, window, container: Object.assign(new Element("div"), { ownerDocument: document }) };
}

test("a Firefox view reports its size, forwards input and acknowledges frames", async () => {
  const { document, container } = fakeDom();
  const calls = [];
  let frameListener = null;
  let firefoxListener = null;
  const bridge = {
    platform: "linux",
    firefoxView: async (id, view) => calls.push(["view", id, view]),
    firefoxInput: async (id, events) => calls.push(["input", id, events]),
    frameAck: async (id, seq) => calls.push(["ack", id, seq]),
    viewDetached: async (id) => calls.push(["detached", id]),
    adopt: async () => ({ engine: "firefox", ready: true }),
    onFrame: (listener) => { frameListener = listener; return () => { frameListener = null; }; },
    onFirefox: (listener) => { firefoxListener = listener; return () => { firefoxListener = null; }; },
  };
  const previousDocument = globalThis.document;
  const previousBlob = globalThis.Blob;
  globalThis.document = document;
  globalThis.Blob = class { constructor(parts, options) { this.parts = parts; this.type = options.type; } };
  try {
    const viewRef = { current: null };
    const updates = [];
    attachFirefoxView(bridge, "tab-1", container, viewRef, { clientId: "client", onUpdate: (state) => updates.push(state) });
    const wrapper = viewRef.current;
    assert.equal(wrapper.dataset.engine, "firefox");
    assert.deepEqual(calls.find(([kind]) => kind === "view"), ["view", "tab-1", { width: 640, height: 480, scale: 2, visible: true }]);
    const [canvas, keyboard] = wrapper.children;

    canvas.dispatch("pointerdown", { clientX: 12.4, clientY: 30, button: 0, pointerId: 1 });
    assert.equal(keyboard.focused, true);
    assert.deepEqual(calls.at(-1), ["input", "tab-1", [{ t: "down", x: 12, y: 30, button: 0 }]]);
    const plain = keyboard.dispatch("keydown", { key: "a", code: "KeyA" });
    assert.equal(plain.defaultPrevented, true);
    assert.deepEqual(calls.at(-1)[2], [{ t: "keydown", key: "a" }]);
    keyboard.dispatch("keyup", { key: "A", code: "KeyA" });
    assert.deepEqual(calls.at(-1)[2], [{ t: "keyup", key: "a" }]);
    const paste = keyboard.dispatch("keydown", { key: "v", code: "KeyV", ctrlKey: true });
    assert.equal(paste.defaultPrevented, undefined);
    keyboard.dispatch("paste", { clipboardData: { getData: () => "pasted text" } });
    assert.deepEqual(calls.at(-1)[2], [{ t: "text", text: "pasted text" }]);
    keyboard.dispatch("keydown", { key: "c", code: "KeyC", ctrlKey: true });
    assert.deepEqual(calls.at(-1)[2], [{ t: "copy" }, { t: "keydown", key: "c" }]);
    canvas.dispatch("wheel", { clientX: 5, clientY: 6, deltaMode: 1, deltaX: 0, deltaY: 2 });
    assert.deepEqual(calls.at(-1)[2], [{ t: "wheel", x: 5, y: 6, dx: 0, dy: 32 }]);

    await frameListener({ id: "tab-1", seq: 4, data: new Uint8Array([0xff, 0xd8]), width: 640, height: 480 });
    assert.equal(canvas.width, 1280);
    assert.equal(canvas.style.width, "640px");
    assert.deepEqual(calls.at(-1), ["ack", "tab-1", 4]);
    await frameListener({ id: "another-tab", seq: 9, data: new Uint8Array([1]), width: 1, height: 1 });
    assert.notDeepEqual(calls.at(-1), ["ack", "another-tab", 9]);

    firefoxListener({ id: "tab-1", kind: "cursor", cursor: "pointer" });
    assert.equal(canvas.style.cursor, "pointer");
    firefoxListener({ id: "tab-1", kind: "select", rect: { x: 10, y: 20, width: 100, height: 30 }, options: [{ index: 0, label: "one" }, { index: 1, label: "two", selected: true }] });
    const menu = wrapper.children.find((item) => item.attributes.role === "listbox");
    assert.ok(menu);
    menu.children[1].dispatch("click");
    assert.deepEqual(calls.at(-1)[2], [{ t: "select-option", index: 1 }]);

    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(updates, [{ engine: "firefox", ready: true }]);
    wrapper.remove();
    assert.deepEqual(calls.at(-1), ["detached", "tab-1"]);
    assert.equal(frameListener, null);
    assert.equal(firefoxListener, null);
  } finally {
    globalThis.document = previousDocument;
    globalThis.Blob = previousBlob;
  }
});
