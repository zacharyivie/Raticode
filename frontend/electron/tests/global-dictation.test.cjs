const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  TOGGLE_FLAG,
  createGlobalDictation,
  hasToggleFlag,
  shortcutSupport,
  toAccelerator,
  toggleCommand,
} = require("../global-dictation.cjs");

const electronDir = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(electronDir, name), "utf8");

function fakeShortcuts({ refuse = [] } = {}) {
  const shortcuts = {
    registered: new Map(),
    suspended: false,
    calls: [],
    register(accelerator, callback) {
      shortcuts.calls.push(["register", accelerator]);
      if (shortcuts.suspended || refuse.includes(accelerator)) return false;
      shortcuts.registered.set(accelerator, callback);
      return true;
    },
    unregister(accelerator) {
      shortcuts.calls.push(["unregister", accelerator]);
      shortcuts.registered.delete(accelerator);
    },
    setSuspended(value) { shortcuts.suspended = value; },
    press(accelerator) { shortcuts.registered.get(accelerator)?.(); },
  };
  return shortcuts;
}

function controller(options = {}) {
  const shortcuts = options.shortcuts ?? fakeShortcuts();
  const sent = [];
  const shown = [];
  const listening = [];
  let hidden = 0;
  let window = options.window ?? true;
  const dictation = createGlobalDictation({
    globalShortcut: shortcuts,
    support: options.support,
    command: options.command ?? "",
    now: () => 1_700_000_000_000,
    send: (message) => {
      if (!window) return false;
      sent.push(message);
      return true;
    },
    overlay: { show: (state) => shown.push(state), hide: () => { hidden += 1; } },
    onListening: (value) => listening.push(value),
  });
  return { dictation, shortcuts, sent, shown, listening, hidden: () => hidden, closeWindow: () => { window = false; } };
}

test("Settings bindings become system-wide accelerators, and keys you type with are refused", () => {
  assert.deepEqual(toAccelerator("Ctrl+Alt+Space", "linux"), { accelerator: "Control+Alt+Space" });
  assert.deepEqual(toAccelerator("Ctrl+Alt+Space", "darwin"), { accelerator: "Control+Alt+Space" });
  assert.deepEqual(toAccelerator("Mod+Alt+KeyV", "win32"), { accelerator: "Control+Alt+V" });
  assert.deepEqual(toAccelerator("Mod+Alt+KeyV", "darwin"), { accelerator: "Command+Alt+V" });
  assert.deepEqual(toAccelerator("Meta+Shift+Digit5", "linux"), { accelerator: "Super+Shift+5" });
  assert.deepEqual(toAccelerator("Ctrl+Backquote", "linux"), { accelerator: "Control+`" });
  assert.deepEqual(toAccelerator("Alt+ArrowUp", "linux"), { accelerator: "Alt+Up" });
  assert.deepEqual(toAccelerator("F13", "linux"), { accelerator: "F13" });
  assert.match(toAccelerator("", "linux").error, /No shortcut is assigned/);
  assert.match(toAccelerator("Mod+KeyK Mod+KeyO", "linux").error, /single key combination/);
  assert.match(toAccelerator("KeyV", "linux").error, /Add Ctrl, Alt/);
  assert.match(toAccelerator("Shift+KeyV", "linux").error, /Add Ctrl, Alt/);
  assert.match(toAccelerator("Ctrl+IntlRo", "linux").error, /can't be used/);
  assert.match(toAccelerator("Hyper+KeyV", "linux").error, /Unknown modifier/);
});

test("each desktop is described honestly: native keys, the Wayland portal, or X11 inside Wayland", () => {
  assert.equal(shortcutSupport({ platform: "win32", env: {} }).kind, "native");
  assert.equal(shortcutSupport({ platform: "darwin", env: { WAYLAND_DISPLAY: "x" } }).kind, "native");
  assert.equal(shortcutSupport({ platform: "linux", env: { DISPLAY: ":0", XDG_SESSION_TYPE: "x11" } }).kind, "native");
  assert.equal(shortcutSupport({ platform: "linux", env: { WAYLAND_DISPLAY: "wayland-0" } }).kind, "portal");
  assert.equal(shortcutSupport({ platform: "linux", env: { XDG_SESSION_TYPE: "wayland" }, ozonePlatform: "wayland" }).kind, "portal");
  assert.equal(shortcutSupport({ platform: "linux", env: { WAYLAND_DISPLAY: "wayland-0" }, ozonePlatform: "x11" }).kind, "xwayland");
  assert.equal(shortcutSupport({ platform: "linux", env: { WAYLAND_DISPLAY: "wayland-0", ELECTRON_OZONE_PLATFORM_HINT: "x11" } }).kind, "xwayland");
});

test("the fallback command reaches the same running Raticode and is quoted for a shell", () => {
  assert.equal(toggleCommand({ execPath: "/opt/Raticode/raticode", appImage: "", packaged: true }), `/opt/Raticode/raticode ${TOGGLE_FLAG}`);
  assert.equal(toggleCommand({ execPath: "/tmp/.mount/raticode", appImage: "/home/me/Apps/Raticode.AppImage", packaged: true }), `/home/me/Apps/Raticode.AppImage ${TOGGLE_FLAG}`);
  assert.equal(
    toggleCommand({ execPath: "/repo/node_modules/electron/dist/electron", packaged: false, appPath: "/repo/My App/frontend", configHome: "/home/me/.local/state/dev config" }),
    `env XDG_CONFIG_HOME='/home/me/.local/state/dev config' /repo/node_modules/electron/dist/electron '/repo/My App/frontend' ${TOGGLE_FLAG}`,
  );
  assert.equal(toggleCommand({ execPath: "/a/it's", packaged: true }), `'/a/it'\\''s' ${TOGGLE_FLAG}`);
  assert.equal(hasToggleFlag(["/usr/bin/raticode", TOGGLE_FLAG]), true);
  assert.equal(hasToggleFlag(["/usr/bin/raticode", "--toggle-dictation=1"]), false);
  assert.equal(hasToggleFlag(undefined), false);
});

test("nothing is registered and nothing records until the user turns it on", () => {
  const { dictation, shortcuts, sent } = controller();
  assert.deepEqual(dictation.configure({ enabled: false, binding: "Ctrl+Alt+Space" }), {
    enabled: false, accelerator: "", registered: false, error: "", support: "native", command: "", lastPressedAt: 0,
  });
  assert.deepEqual(shortcuts.calls, []);
  // The command-line trigger is ignored while it is off.
  assert.equal(dictation.trigger("command"), false);
  assert.deepEqual(sent, []);
});

test("turned on, the shortcut toggles dictation in the window, and changing or clearing it swaps the registration", () => {
  const { dictation, shortcuts, sent } = controller();
  const status = dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space", label: "Ctrl+Alt+Space" });
  assert.equal(status.registered, true);
  assert.equal(status.accelerator, "Control+Alt+Space");
  shortcuts.press("Control+Alt+Space");
  assert.deepEqual(sent, [{ action: "toggle", source: "shortcut" }]);
  assert.equal(dictation.status().lastPressedAt, 1_700_000_000_000);
  assert.equal(dictation.trigger("command"), true);
  assert.deepEqual(sent.at(-1), { action: "toggle", source: "command" });

  // The same binding again (Settings reopened) doesn't re-register.
  const before = shortcuts.calls.length;
  dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space" });
  assert.equal(shortcuts.calls.length, before);

  dictation.configure({ enabled: true, binding: "Mod+Shift+KeyD" });
  assert.deepEqual([...shortcuts.registered.keys()], [process.platform === "darwin" ? "Command+Shift+D" : "Control+Shift+D"]);
  dictation.configure({ enabled: false, binding: "Mod+Shift+KeyD" });
  assert.deepEqual([...shortcuts.registered.keys()], []);
  dictation.configure({ enabled: true, binding: "" });
  assert.match(dictation.status().error, /No shortcut is assigned/);
  assert.deepEqual([...shortcuts.registered.keys()], []);
});

test("a shortcut another app holds is reported as a conflict instead of failing silently", () => {
  const { dictation, shortcuts } = controller({ shortcuts: fakeShortcuts({ refuse: ["Control+Alt+Space"] }) });
  const status = dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space" });
  assert.equal(status.registered, false);
  assert.match(status.error, /Another app already uses this shortcut/);
  assert.deepEqual([...shortcuts.registered.keys()], []);
  const thrown = createGlobalDictation({ globalShortcut: { register() { throw new Error("Wayland portal unavailable"); }, unregister() {} }, send: () => true });
  assert.equal(thrown.configure({ enabled: true, binding: "Ctrl+Alt+Space" }).error, "Wayland portal unavailable");
});

test("recording a new key suspends the shortcut, and resuming registers the new one", () => {
  const { dictation, shortcuts } = controller();
  dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space" });
  dictation.suspend(true);
  assert.equal(shortcuts.suspended, true);
  // Settings saves the new key while suspended; registration fails until resumed.
  assert.equal(dictation.configure({ enabled: true, binding: "Ctrl+Alt+KeyM" }).registered, false);
  assert.equal(dictation.suspend(false).registered, true);
  assert.deepEqual([...shortcuts.registered.keys()], ["Control+Alt+M"]);
});

test("the overlay follows only dictation started from another app and gets phases, never words", () => {
  const { dictation, shown, listening, hidden } = controller();
  dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space", label: "Ctrl+Alt+Space" });
  // The mic button's dictation doesn't show the overlay.
  assert.equal(dictation.report({ phase: "listening", global: false }), false);
  assert.deepEqual(shown, []);
  assert.equal(dictation.report({ phase: "starting", global: true }), true);
  assert.equal(dictation.report({ phase: "listening", global: true }), true);
  assert.equal(dictation.report({ phase: "tidying", global: true }), true);
  assert.equal(dictation.report({ phase: "done", global: true, sent: true, message: "x".repeat(1000) }), true);
  assert.deepEqual(shown.map((state) => state.phase), ["starting", "listening", "tidying", "done"]);
  assert.equal(shown.at(-1).message.length, 300);
  assert.equal(shown.at(-1).sent, true);
  assert.equal(shown[1].label, "Ctrl+Alt+Space");
  assert.deepEqual(Object.keys(shown[1]).sort(), ["label", "message", "note", "phase", "sent"]);
  // The tray says "listening" only while the microphone is open.
  assert.deepEqual(listening, [true, true, false, false, false]);
  assert.equal(dictation.report({ phase: "idle", global: false }), false);
  assert.equal(dictation.report({ phase: "words", global: true }), false);
  // A dictation that couldn't start still says why.
  assert.equal(dictation.report({ phase: "error", global: true, message: "Rem is still answering." }), true);
  assert.equal(shown.at(-1).message, "Rem is still answering.");
  dictation.report({ phase: "listening", global: true });
  dictation.report({ phase: "idle", global: true });
  assert.equal(hidden(), 1);
});

test("pressing the shortcut before the window is ready says so in the overlay", () => {
  const { dictation, shown, closeWindow } = controller();
  dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space" });
  closeWindow();
  assert.equal(dictation.trigger("shortcut"), false);
  assert.equal(shown.at(-1).phase, "error");
  assert.match(shown.at(-1).message, /isn't ready yet/);
});

test("main.js wires the shortcut without stealing focus and keeps the window where it was", () => {
  const main = read("main.js");
  // The toggle command never shows the window; other second launches still do.
  assert.match(main, /app\.on\("second-instance", \(_event, argv\) => \{\n {2}if \(hasToggleFlag\(argv\)\) \{\n {4}globalDictation\.trigger\("command"\);\n {4}return;\n {2}\}\n {2}showMainWindow\(\);/);
  assert.match(main, /focusable: false/);
  assert.match(main, /showInactive\(\)/);
  assert.match(main, /sandbox: true,\n {8}spellcheck: false/);
  assert.match(main, /event\.sender !== dictationOverlayWindow\.webContents/);
  assert.match(main, /app\.on\("will-quit", \(\) => globalDictation\.dispose\(\)\)/);
  const overlayBlock = main.slice(main.indexOf("function showDictationOverlay"), main.indexOf("const globalDictation = createGlobalDictation"));
  assert.doesNotMatch(overlayBlock, /\.focus\(\)|\.show\(\)/);
  // The window's own handlers only pass a phase, a short message and a flag along.
  assert.match(main, /configureGlobalDictation: \(_event, options = \{\}\) =>/);
  assert.match(main, /reportGlobalDictation: \(_event, state = \{\}\) =>/);
  const ipc = read("ipc-handlers.cjs");
  for (const channel of ["gofer:global-dictation-configure", "gofer:global-dictation-report", "gofer:global-dictation-suspend"]) {
    assert.ok(ipc.includes(channel), channel);
  }
  assert.match(read("preload.cjs"), /globalDictation: \{/);
});

test("the overlay page has no remote content, no inline script and only stop/open actions", () => {
  const html = read("dictation-overlay.html");
  assert.match(html, /Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'self'"/);
  assert.doesNotMatch(html, /<script>(?!<\/script>)/);
  assert.doesNotMatch(html, /https?:\/\//);
  const script = read("dictation-overlay.js");
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|eval\(/);
  const preload = read("dictation-overlay-preload.cjs");
  assert.match(preload, /exposeInMainWorld\("dictationOverlay"/);
  assert.deepEqual([...preload.matchAll(/ipcRenderer\.send\("gofer:dictation-overlay-action", "(\w+)"\)/g)].map((match) => match[1]), ["stop", "open"]);
});

test("the shortcut code never logs and never handles audio or words", () => {
  // Code only: the comments explain what it doesn't do.
  const source = read("global-dictation.cjs").split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
  assert.doesNotMatch(source, /console\.|applicationLog|\.write\(/);
  assert.doesNotMatch(source, /transcript|pcm|audio\b/i);
});

test("a note about a screenshot shows while that dictation runs, then clears", () => {
  const { dictation, shown } = controller();
  dictation.configure({ enabled: true, binding: "Ctrl+Alt+Space" });
  dictation.setNote("Screenshot of Terminal attached; it waits for you to send it.");
  dictation.report({ phase: "listening", global: true });
  assert.match(shown.at(-1).note, /Screenshot of Terminal/);
  dictation.report({ phase: "done", global: true });
  assert.match(shown.at(-1).note, /Screenshot of Terminal/);
  // The next dictation has no screenshot unless one is taken again.
  dictation.report({ phase: "listening", global: true });
  assert.equal(shown.at(-1).note, "");
  dictation.setNote("x".repeat(500));
  dictation.report({ phase: "listening", global: true });
  assert.equal(shown.at(-1).note.length, 160);
});
