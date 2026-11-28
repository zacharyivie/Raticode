require("./test-tmp.cjs");
// stream-page-menu.cjs works out what was right-clicked in a streamed Chrome or
// Edge tab. The page's answer is untrusted, so menuParams keeps only what the
// menu can use safely, and read() builds a script from the click position.

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { EDIT_COMMANDS, WATCH, menuParams, read } = require("../stream-page-menu.cjs");

const BASE = {
  prevented: false,
  x: 10,
  y: 20,
  linkURL: "https://example.com/page",
  srcURL: "http://example.com/image.png",
  mediaType: "image",
  selectionText: "chosen words",
  isEditable: false,
};

test("menuParams returns null with nothing to describe", () => {
  for (const seen of [undefined, null, "", 0, "https://example.com", 42, true]) {
    assert.equal(menuParams(seen), null, `for ${String(seen)}`);
  }
});

test("menuParams returns null for a right-click the page showed its own menu for", () => {
  assert.equal(menuParams({ ...BASE, prevented: true }), null);
  assert.notEqual(menuParams({ ...BASE, prevented: false }), null);
});

test("menuParams reports a plain right-click like Electron's context-menu event", () => {
  assert.deepEqual(menuParams(BASE), {
    x: 10,
    y: 20,
    linkURL: "https://example.com/page",
    srcURL: "http://example.com/image.png",
    mediaType: "image",
    selectionText: "chosen words",
    isEditable: false,
  });
});

test("only http and https links and sources pass", () => {
  for (const url of ["http://a.example/", "https://a.example/", "HTTPS://A.EXAMPLE/"]) {
    const params = menuParams({ ...BASE, linkURL: url, srcURL: url });
    assert.equal(params.linkURL, url);
    assert.equal(params.srcURL, url);
  }
  for (const url of [
    "javascript:alert(1)",
    "JavaScript:alert(document.cookie)",
    "file:///etc/passwd",
    "data:text/html,<script>alert(1)</script>",
    "blob:https://a.example/123",
    " https://a.example/",
    "httpx://a.example/",
    "",
    undefined,
    null,
    42,
    { toString: () => "javascript:alert(1)" },
  ]) {
    const params = menuParams({ ...BASE, linkURL: url, srcURL: url });
    assert.equal(params.linkURL, "", `linkURL for ${String(url)}`);
    assert.equal(params.srcURL, "", `srcURL for ${String(url)}`);
  }
});

test("a javascript: link comes back as an empty string", () => {
  assert.equal(menuParams({ ...BASE, linkURL: "javascript:void(fetch('//evil.example'))" }).linkURL, "");
});

test("URLs are cut to 4,096 characters and selected text to 20,000", () => {
  const url = `https://a.example/${"p".repeat(5000)}`;
  const params = menuParams({ ...BASE, linkURL: url, srcURL: url, selectionText: "s".repeat(25_000) });
  assert.equal(params.linkURL, url.slice(0, 4096));
  assert.equal(params.srcURL.length, 4096);
  assert.equal(params.selectionText, "s".repeat(20_000));
  const short = menuParams({ ...BASE, linkURL: url.slice(0, 4096), selectionText: "s".repeat(20_000) });
  assert.equal(short.linkURL.length, 4096);
  assert.equal(short.selectionText.length, 20_000);
});

test("selected text that isn't a string becomes text, and missing text is empty", () => {
  assert.equal(menuParams({ ...BASE, selectionText: 12 }).selectionText, "12");
  assert.equal(menuParams({ ...BASE, selectionText: undefined }).selectionText, "");
  assert.equal(menuParams({ ...BASE, selectionText: null }).selectionText, "");
});

test("an unknown media type becomes none", () => {
  for (const mediaType of ["image", "video", "audio"]) assert.equal(menuParams({ ...BASE, mediaType }).mediaType, mediaType);
  for (const mediaType of ["none", "canvas", "IMAGE", "", undefined, null, ["image"]]) {
    assert.equal(menuParams({ ...BASE, mediaType }).mediaType, "none", `for ${String(mediaType)}`);
  }
});

test("isEditable is true only for true", () => {
  assert.equal(menuParams({ ...BASE, isEditable: true }).isEditable, true);
  for (const isEditable of [false, "true", 1, {}, undefined]) {
    assert.equal(menuParams({ ...BASE, isEditable }).isEditable, false, `for ${String(isEditable)}`);
  }
});

test("x and y are scaled by the zoom and rounded", () => {
  assert.deepEqual(pick(menuParams({ ...BASE, x: 10.4, y: 3.3 }, 1.5)), { x: 16, y: 5 });
  assert.deepEqual(pick(menuParams({ ...BASE, x: 101, y: 51 }, 0.5)), { x: 51, y: 26 });
  assert.deepEqual(pick(menuParams({ ...BASE, x: 7.6, y: 7.4 })), { x: 8, y: 7 });
  assert.deepEqual(pick(menuParams({ ...BASE, x: "12", y: "1e1" }, 2)), { x: 24, y: 20 });
  assert.deepEqual(pick(menuParams({ ...BASE, x: "left", y: undefined }, 2)), { x: 0, y: 0 });
  assert.deepEqual(pick(menuParams({ ...BASE, x: NaN, y: null }, 3)), { x: 0, y: 0 });
});

function pick({ x, y }) {
  return { x, y };
}

test("read() puts only numbers into the script", () => {
  assert.match(read(12, 34.5), /elementFromPoint\(12, 34\.5\), false, 12, 34\.5\)/);
  for (const [x, y] of [[NaN, NaN], ["left", "top"], ["1); alert(1); (", "0) || fetch('//x') || (0"], [undefined, null], [{}, []]]) {
    const script = read(x, y);
    assert.match(script, /elementFromPoint\(0, 0\), false, 0, 0\)/, `for ${String(x)}, ${String(y)}`);
    assert.doesNotMatch(script, /alert|fetch|left|top/);
  }
  // A number written as text is still the number it says.
  assert.match(read("8", "9"), /elementFromPoint\(8, 9\), false, 8, 9\)/);
});

// A page with nothing under the pointer, enough for the scripts to run in.
function page({ now = 1_000_000, under = null } = {}) {
  class Element {}
  const listeners = {};
  const context = {
    Date: { now: () => now },
    Element,
    getSelection: () => "",
    document: { elementFromPoint: (x, y) => { context.asked = [x, y]; return under; } },
    addEventListener: (type, listener) => { listeners[type] = listener; },
    listeners,
  };
  context.window = context;
  return vm.createContext(context);
}

test("read() runs in the page and describes what is under the pointer", () => {
  const context = page();
  const seen = vm.runInContext(read(5, 6), context);
  assert.deepEqual(context.asked, [5, 6]);
  assert.deepEqual(JSON.parse(JSON.stringify(seen)), {
    at: 1_000_000, prevented: false, x: 5, y: 6, linkURL: "", srcURL: "", mediaType: "none", selectionText: "", isEditable: false,
  });
});

test("read() takes a recent right-click the watcher saw, once", () => {
  const context = page();
  const watched = { at: 1_000_000 - 2_999, prevented: true, x: 1, y: 2 };
  context.__raticodeMenu = watched;
  assert.equal(vm.runInContext(read(5, 6), context), watched);
  assert.equal(context.__raticodeMenu, null);
  assert.equal(context.asked, undefined);
  context.__raticodeMenu = { at: 1_000_000 - 3_000, prevented: true };
  assert.equal(vm.runInContext(read(5, 6), context).prevented, false, "an old right-click is not used");
  assert.deepEqual(context.asked, [5, 6]);
});

test("WATCH installs one listener that remembers whether the page handled the right-click", () => {
  const context = page();
  assert.equal(vm.runInContext(WATCH, context), true);
  const listener = context.listeners.contextmenu;
  assert.equal(typeof listener, "function");
  assert.equal(vm.runInContext(WATCH, context), true);
  assert.equal(context.listeners.contextmenu, listener, "running it again adds nothing");
  listener({ target: null, defaultPrevented: true, clientX: 3, clientY: 4 });
  assert.equal(context.__raticodeMenu.prevented, true);
  assert.equal(menuParams(context.__raticodeMenu), null);
});

test("the menu's editing commands map to the browser's own command names", () => {
  assert.deepEqual(EDIT_COMMANDS, { cut: "delete", selectAll: "selectAll", undo: "undo", redo: "redo" });
});
