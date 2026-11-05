import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

import {
  DEFAULT_APP_SETTINGS,
  KEYBINDING_COMMANDS,
  formatKeybinding,
  keybindingConflictIds,
  matchesCommand,
  normalizeAppSettings,
  typesAltGraphCharacter,
} from "./settings.js";
import {
  DEFAULT_VOICE_BINDING,
  VOICE_COMMAND_ID,
  DICTATION_MODES,
  ariaKeyshortcuts,
  pickVoiceComposer,
  registerVoiceComposer,
  requestVoiceDictation,
  voiceShortcutLabel,
} from "./voiceShortcut.js";

const require = createRequire(import.meta.url);
const { browserApplicationShortcutAction } = require("../../electron/browser-utils.cjs");

const key = (value, code, extras = {}) => ({ key: value, code, repeat: false, ...extras });

test("dictation defaults to Ctrl+Alt+V, or Command+Option+V on a Mac, and clashes with no other default", () => {
  const command = KEYBINDING_COMMANDS.find((item) => item.id === VOICE_COMMAND_ID);
  assert.equal(command.defaultBinding, DEFAULT_VOICE_BINDING);
  assert.equal(command.scope, "global");
  assert.equal(DEFAULT_APP_SETTINGS.keybindings[VOICE_COMMAND_ID], "Mod+Alt+KeyV");
  for (const platform of ["Linux x86_64", "Win32", "MacIntel"]) {
    assert.deepEqual(keybindingConflictIds(DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, undefined, platform), [], platform);
  }
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Linux x86_64"), true);
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Win32"), true);
  // Option+V types "√" on a Mac; the physical key still decides.
  assert.equal(matchesCommand(key("√", "KeyV", { metaKey: true, altKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "MacIntel"), true);
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "MacIntel"), false);
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Linux x86_64"), false);
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true, repeat: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Win32"), false);

  assert.equal(formatKeybinding(DEFAULT_VOICE_BINDING, "Win32"), "Ctrl+Alt+V");
  assert.equal(formatKeybinding(DEFAULT_VOICE_BINDING, "MacIntel"), "Command+Option+V");
  assert.equal(voiceShortcutLabel(DEFAULT_VOICE_BINDING, "Linux x86_64"), "Ctrl+Alt+V");
  assert.equal(ariaKeyshortcuts(DEFAULT_VOICE_BINDING, "Linux x86_64"), "Control+Alt+V");
  assert.equal(ariaKeyshortcuts(DEFAULT_VOICE_BINDING, "MacIntel"), "Meta+Alt+V");
  assert.equal(ariaKeyshortcuts("", "Win32"), undefined);
});

test("the dictation shortcut can be changed or cleared and survives a reload", () => {
  const changed = normalizeAppSettings({ keybindings: { [VOICE_COMMAND_ID]: "Ctrl+Shift+KeyM" } });
  assert.equal(changed.keybindings[VOICE_COMMAND_ID], "Ctrl+Shift+KeyM");
  assert.equal(matchesCommand(key("M", "KeyM", { ctrlKey: true, shiftKey: true }), changed, VOICE_COMMAND_ID, "Win32"), true);
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true }), changed, VOICE_COMMAND_ID, "Win32"), false);
  const cleared = normalizeAppSettings({ keybindings: { [VOICE_COMMAND_ID]: "" } });
  assert.equal(matchesCommand(key("v", "KeyV", { ctrlKey: true, altKey: true }), cleared, VOICE_COMMAND_ID, "Win32"), false);
  // Older settings without the command get the default.
  assert.equal(normalizeAppSettings({ keybindings: {} }).keybindings[VOICE_COMMAND_ID], DEFAULT_VOICE_BINDING);
});

test("AltGr typing is never taken as the shortcut, so Hungarian keyboards can still type @", () => {
  // Windows reports AltGr+V on a Hungarian layout as Ctrl+Alt+V that types "@".
  const altGrAt = key("@", "KeyV", { ctrlKey: true, altKey: true });
  assert.equal(typesAltGraphCharacter(altGrAt, "Win32"), true);
  assert.equal(matchesCommand(altGrAt, DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Win32"), false);
  const altGraphState = key("v", "KeyV", { ctrlKey: true, altKey: true, getModifierState: (name) => name === "AltGraph" });
  assert.equal(matchesCommand(altGraphState, DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Linux x86_64"), false);
  // A letter from another alphabet is still the V key, not typing.
  assert.equal(typesAltGraphCharacter(key("м", "KeyV", { ctrlKey: true, altKey: true }), "Win32"), false);
  assert.equal(matchesCommand(key("м", "KeyV", { ctrlKey: true, altKey: true }), DEFAULT_APP_SETTINGS, VOICE_COMMAND_ID, "Win32"), true);
  // Macs have no AltGr; Option symbols are expected there.
  assert.equal(typesAltGraphCharacter(key("√", "KeyV", { ctrlKey: true, altKey: true }), "MacIntel"), false);

  // The same rule holds inside the integrated browser, where Electron matches the keys.
  const bindings = { [VOICE_COMMAND_ID]: DEFAULT_VOICE_BINDING };
  assert.equal(browserApplicationShortcutAction({}, { type: "keyDown", code: "KeyV", key: "v", control: true, alt: true }, bindings, "win32"), "command:voice.dictate");
  assert.equal(browserApplicationShortcutAction({}, { type: "keyDown", code: "KeyV", key: "@", control: true, alt: true }, bindings, "win32"), "");
  assert.equal(browserApplicationShortcutAction({}, { type: "keyDown", code: "KeyV", key: "√", meta: true, alt: true }, bindings, "darwin"), "command:voice.dictate");
});

function fakeComposer(name, { visible = true, available = true, dictating = false, lastUsed = 0, holds = [] } = {}) {
  const element = {
    isConnected: true,
    contains: (node) => holds.includes(node),
    closest: () => null,
    getClientRects: () => (entry.visible ? [{}] : []),
  };
  const entry = {
    name,
    visible,
    toggled: 0,
    pressed: 0,
    released: 0,
    root: () => element,
    toggle: () => { entry.toggled += 1; },
    press: () => { entry.pressed += 1; entry.dictatingNow = true; },
    release: () => { entry.released += 1; entry.dictatingNow = false; },
    available: () => available,
    dictating: () => dictating || Boolean(entry.dictatingNow),
    lastUsed,
  };
  return entry;
}

test("the shortcut goes to the chat box in use, else one dictating, else the last visible one used", async () => {
  const focusedNode = {};
  const threads = fakeComposer("threads", { lastUsed: 5 });
  const rem = fakeComposer("rem", { lastUsed: 9 });
  const hidden = fakeComposer("hidden", { visible: false, lastUsed: 99 });
  const busy = fakeComposer("busy", { available: false, lastUsed: 50 });
  const remove = [threads, rem, hidden, busy].map(registerVoiceComposer);
  try {
    assert.equal(pickVoiceComposer(null).name, "rem");
    threads.root().contains = (node) => node === focusedNode;
    assert.equal(pickVoiceComposer(focusedNode).name, "threads");
    threads.root().contains = () => false;
    const listening = fakeComposer("listening", { visible: false, dictating: true });
    const removeListening = registerVoiceComposer(listening);
    assert.equal(pickVoiceComposer(null).name, "listening");
    removeListening();

    assert.equal(await requestVoiceDictation(), true);
    assert.equal(rem.toggled, 1);
  } finally {
    remove.forEach((unregister) => unregister());
  }
});

test("with no chat box on screen the shortcut shows Rem and dictates once its box appears", async () => {
  const rem = fakeComposer("rem", { visible: false });
  const remove = registerVoiceComposer(rem);
  try {
    let revealed = 0;
    const done = requestVoiceDictation({ reveal: () => { revealed += 1; setTimeout(() => { rem.visible = true; }, 20); }, stepMs: 5 });
    assert.equal(await done, true);
    assert.equal(revealed, 1);
    assert.equal(rem.toggled, 1);
    rem.visible = false;
    assert.equal(await requestVoiceDictation({ waitMs: 30, stepMs: 5 }), false);
  } finally {
    remove();
  }
});

test("holding the shortcut presses and letting go releases the box that is dictating", async () => {
  const rem = fakeComposer("rem");
  const remove = registerVoiceComposer(rem);
  try {
    assert.equal(await requestVoiceDictation({ kind: "press" }), true);
    assert.deepEqual([rem.pressed, rem.toggled], [1, 0]);
    assert.equal(await requestVoiceDictation({ kind: "release" }), true);
    assert.equal(rem.released, 1);
    // Letting go with nothing dictating does nothing.
    assert.equal(await requestVoiceDictation({ kind: "release" }), false);
    assert.equal(rem.released, 1);
  } finally {
    remove();
  }
});

test("letting go before Rem's box appears cancels the press, so it can't start listening late", async () => {
  const rem = fakeComposer("rem", { visible: false });
  const remove = registerVoiceComposer(rem);
  try {
    const press = requestVoiceDictation({ kind: "press", reveal: () => setTimeout(() => { rem.visible = true; }, 30), stepMs: 5 });
    await requestVoiceDictation({ kind: "release" });
    assert.equal(await press, false);
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(rem.pressed, 0);
  } finally {
    remove();
  }
});

test("dictation offers press to start and stop, the default, and hold to talk", () => {
  assert.deepEqual(DICTATION_MODES.map(([id]) => id), ["toggle", "hold"]);
  assert.equal(normalizeAppSettings({}).devices.dictationMode, "toggle");
  assert.equal(normalizeAppSettings({ devices: { dictationMode: "hold" } }).devices.dictationMode, "hold");
  assert.equal(normalizeAppSettings({ devices: { dictationMode: "shout" } }).devices.dictationMode, "toggle");
  assert.equal(normalizeAppSettings({}).devices.dictationAutoSend, false);
  assert.equal(normalizeAppSettings({ devices: { dictationAutoSend: true } }).devices.dictationAutoSend, true);
  assert.equal(normalizeAppSettings({ devices: { dictationAutoSend: "yes" } }).devices.dictationAutoSend, false);
});
