import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { BROWSER_KEYS } from "../../electron/browser-keys.js";
import { browserChromeShortcutAction } from "./browserShortcuts.js";

const require = createRequire(import.meta.url);
const { browserShortcutAction } = require("../../electron/browser-utils.cjs");

const PLATFORMS = ["linux", "win32", "darwin"];
const SHARED_ACTIONS = [
  "focus-location", "find", "reload", "back", "forward", "reload-hard", "toggle-devtools", "print",
  "bookmark", "history", "reopen-closed-tab", "new-private-tab", "zoom-in", "zoom-out", "zoom-reset",
];

function pressesFor(binding, platform) {
  const mac = platform === "darwin";
  const held = new Set(binding.modifiers.map((name) => (name === "Mod" ? (mac ? "Meta" : "Ctrl") : name)));
  return binding.keys.map((key) => ({
    key,
    ctrl: held.has("Ctrl"),
    meta: held.has("Meta"),
    alt: held.has("Alt"),
    shift: held.has("Shift"),
  }));
}

function pageInput(press) {
  return { type: "keyDown", key: press.key, control: press.ctrl, meta: press.meta, alt: press.alt, shift: press.shift };
}

function toolbarEvent(press) {
  return { key: press.key, ctrlKey: press.ctrl, metaKey: press.meta, altKey: press.alt, shiftKey: press.shift };
}

test("Alt+Left and Alt+Right go back and forward in a focused web page", () => {
  // Electron's input.key is KeyboardEvent.key, so arrows arrive as "ArrowLeft", not "left".
  assert.equal(browserShortcutAction({ alt: true, key: "ArrowLeft", type: "keyDown" }, "linux"), "back");
  assert.equal(browserShortcutAction({ alt: true, key: "ArrowRight", type: "keyDown" }, "linux"), "forward");
  assert.equal(browserShortcutAction({ alt: true, key: "ArrowLeft", type: "keyDown" }, "darwin"), "back");
  assert.equal(browserShortcutAction({ meta: true, key: "[", type: "keyDown" }, "darwin"), "back");
  assert.equal(browserShortcutAction({ meta: true, key: "]", type: "keyDown" }, "darwin"), "forward");
  assert.equal(browserShortcutAction({ control: true, key: "[", type: "keyDown" }, "linux"), "");
  assert.equal(browserShortcutAction({ alt: true, key: "ArrowLeft", type: "keyDown", isAutoRepeat: true }, "linux"), "");
});

test("the page and the toolbar give the same action for every shared browser key", () => {
  assert.deepEqual([...new Set(BROWSER_KEYS.map((binding) => binding.action))].sort(), [...SHARED_ACTIONS].sort());
  for (const platform of PLATFORMS) {
    for (const binding of BROWSER_KEYS) {
      if (binding.mac !== undefined && binding.mac !== (platform === "darwin")) continue;
      for (const press of pressesFor(binding, platform)) {
        const label = `${platform} ${JSON.stringify(press)}`;
        assert.equal(browserShortcutAction(pageInput(press), platform), binding.action, label);
        assert.equal(browserChromeShortcutAction(toolbarEvent(press), platform, { scoped: true }), binding.action, label);
        assert.equal(
          browserChromeShortcutAction(toolbarEvent(press), platform, { scoped: false }),
          binding.outsideBrowser ? binding.action : "",
          label,
        );
      }
    }
  }
});

test("shared keys keep their exact modifiers, with Shift optional only where listed", () => {
  assert.equal(browserShortcutAction({ control: true, shift: true, key: "+", type: "keyDown" }, "linux"), "zoom-in");
  assert.equal(browserChromeShortcutAction({ ctrlKey: true, shiftKey: true, key: "+" }, "linux", { scoped: true }), "zoom-in");
  assert.equal(browserChromeShortcutAction({ altKey: true, shiftKey: true, key: "ArrowLeft" }, "linux"), "");
  assert.equal(browserChromeShortcutAction({ ctrlKey: true, altKey: true, key: "f" }, "linux"), "");
  // Cmd is Mod on macOS, so Ctrl+L there stays the app's assistant pane key.
  assert.equal(browserChromeShortcutAction({ ctrlKey: true, key: "l" }, "darwin", { scoped: true }), "");
  assert.equal(browserShortcutAction({ control: true, key: "l", type: "keyDown" }, "darwin"), "assistant-pane-toggle");
  assert.equal(browserChromeShortcutAction({ metaKey: true, key: "[" }, "darwin", { scoped: false }), "");
});

test("each mapper keeps its own extras outside the shared table", () => {
  assert.equal(browserShortcutAction({ alt: true, key: "Home", type: "keyDown" }, "linux"), "home");
  assert.equal(browserShortcutAction({ key: "Escape", type: "keyDown" }, "linux"), "stop-loading");
  assert.equal(browserShortcutAction({ control: true, key: "w", type: "keyDown" }, "linux"), "close");
  assert.equal(browserShortcutAction({ control: true, shift: true, key: "Tab", type: "keyDown" }, "linux"), "previous-tab");
  assert.equal(browserChromeShortcutAction({ altKey: true, key: "Home" }, "linux", { scoped: true }), "");
  assert.equal(browserChromeShortcutAction({ altKey: true, key: "d", repeat: true }, "linux"), "");
  assert.equal(browserChromeShortcutAction({ altKey: true, key: "d", defaultPrevented: true }, "linux"), null);
});

test("neither mapper writes out a key for a shared action", () => {
  const sources = [
    fs.readFileSync(new URL("../../electron/browser-utils.cjs", import.meta.url), "utf8")
      .match(/function browserShortcutAction\([\s\S]*?\n}\n/)[0],
    fs.readFileSync(new URL("./browserShortcuts.js", import.meta.url), "utf8"),
  ];
  for (const source of sources) {
    for (const action of SHARED_ACTIONS) assert.equal(source.includes(`"${action}"`), false, action);
  }
});
