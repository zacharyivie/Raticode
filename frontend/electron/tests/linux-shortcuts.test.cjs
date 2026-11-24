// System-wide shortcuts on Linux Wayland desktops (linux-shortcuts.cjs), against a fake KDE
// shortcut service and a fake Global Shortcuts portal on a private dbus-daemon, and a fake
// gsettings for GNOME. Nothing here reaches the developer's desktop.
const test = require("node:test");
const assert = require("node:assert/strict");

const { connect } = require("../dbus-session.cjs");
const { createLinuxShortcuts, desktopFileInstalled, hyprlandBind, qtKeyText, requestPath } = require("../linux-shortcuts.cjs");
const { gnomeBinding, kdeKeys, portalTrigger, readableTrigger } = require("../shortcut-keys.cjs");
const { dbusDaemonInstalled, startBus, startFakeKde, startFakePortal } = require("./fake-desktop-bus.cjs");

const DICTATE = { id: "dictate-to-rem", description: "Dictate to Rem", accelerator: "Control+Alt+Space" };
const SCREEN = { id: "ask-rem-about-screen", description: "Ask Rem about your screen", accelerator: "Control+Alt+S" };
const CTRL_ALT_SPACE = 0x0c000020;
const settle = (ms = 150) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check, ms = 3000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out");
    await settle(20);
  }
}

/** A private bus with the given fake services, and shortcuts that talk to it. */
async function desktop(t, { kde = null, portal = null, env = {}, ...options } = {}) {
  if (!dbusDaemonInstalled()) {
    t.skip("dbus-daemon is not installed");
    return null;
  }
  const bus = await startBus();
  const fakes = {};
  if (kde) fakes.kde = await startFakeKde(bus.socketPath, kde === true ? {} : kde);
  if (portal) fakes.portal = await startFakePortal(bus.socketPath, portal === true ? {} : portal);
  const pressed = [];
  const changes = [];
  const shortcuts = createLinuxShortcuts({
    connectBus: () => connect({ socketPath: bus.socketPath }),
    env,
    appId: "gofer-flow",
    hasDesktopFile: (id) => id === "gofer-flow",
    component: { unique: "raticode-test", friendly: "Raticode" },
    onPress: (id) => pressed.push(id),
    onChange: (status) => changes.push(status),
    ...options,
  });
  t.after(async () => {
    await shortcuts.dispose();
    fakes.kde?.close();
    fakes.portal?.close();
    bus.stop();
  });
  return { shortcuts, pressed, changes, ...fakes, socketPath: bus.socketPath };
}

test("keys are written the way each desktop expects", () => {
  assert.equal(portalTrigger("Control+Alt+Space"), "CTRL+ALT+space");
  assert.equal(portalTrigger("Super+Shift+D"), "LOGO+SHIFT+d");
  assert.equal(portalTrigger("Alt+F9"), "ALT+F9");
  assert.equal(portalTrigger("Control+num5"), "CTRL+KP_5");
  assert.equal(portalTrigger("Control+`"), "CTRL+grave");
  assert.deepEqual(kdeKeys("Control+Alt+Space"), [CTRL_ALT_SPACE]);
  assert.deepEqual(kdeKeys("Super+Space"), [0x10000020]);
  assert.deepEqual(kdeKeys("Super+Shift+D"), [0x12000044]);
  assert.deepEqual(kdeKeys("F13"), [0x0100003c]);
  assert.deepEqual(kdeKeys("Alt+Up"), [0x08000000 | 0x01000013]);
  assert.equal(gnomeBinding("Control+Alt+Space"), "<Control><Alt>space");
  assert.equal(gnomeBinding("Super+/"), "<Super>slash");
  assert.equal(portalTrigger("Hyper+X"), "");
  assert.deepEqual(kdeKeys("Control+Nope"), []);
  assert.equal(readableTrigger("CTRL+ALT+space"), "Ctrl+Alt+Space");
  assert.equal(readableTrigger("LOGO+d"), "Meta+D");
  assert.equal(qtKeyText(0x1000004c), "Meta+L");
  assert.equal(qtKeyText(0x0f000047), "Ctrl+Alt+Shift+F24");
  assert.equal(hyprlandBind("Super+Space"), "SUPER, space");
  assert.equal(hyprlandBind("Control+Alt+D"), "CTRL ALT, D");
});

test("the portal's request path is predicted from Raticode's bus name", () => {
  assert.equal(requestPath(":1.42", "raticode_bind_3"), "/org/freedesktop/portal/desktop/request/1_42/raticode_bind_3");
});

test("the portal only gets an app id whose desktop file is installed", () => {
  const files = new Set(["/home/me/.local/share/applications/gofer-flow.desktop", "/usr/share/applications/other.desktop"]);
  const exists = (file) => files.has(file);
  assert.equal(desktopFileInstalled("gofer-flow", { HOME: "/home/me" }, exists), true);
  assert.equal(desktopFileInstalled("other", { HOME: "/home/me", XDG_DATA_DIRS: "/usr/share" }, exists), true);
  assert.equal(desktopFileInstalled("missing", { HOME: "/home/me" }, exists), false);
  assert.equal(desktopFileInstalled("../escape", { HOME: "/home/me" }, () => true), false);
});

test("KDE: the shortcut is added with no prompt, a press reaches Raticode, and turning it off removes it", async (t) => {
  const setup = await desktop(t, { kde: true, portal: true, env: { XDG_CURRENT_DESKTOP: "KDE" } });
  if (!setup) return;
  const { shortcuts, kde, portal, pressed } = setup;
  const status = await shortcuts.set([DICTATE]);
  assert.equal(status.route, "kde");
  assert.deepEqual(status.shortcuts[DICTATE.id], { state: "registered", trigger: "Ctrl+Alt+Space", message: "", elsewhere: false });
  const action = kde.actions.get("raticode-test/dictate-to-rem");
  assert.deepEqual(action.keys, [CTRL_ALT_SPACE]);
  assert.deepEqual(action.defaults, [CTRL_ALT_SPACE]);
  // KDE is used directly: the portal (and its prompt) is never asked.
  assert.deepEqual(portal.state.calls.filter(([member]) => member === "CreateSession"), []);

  kde.press("raticode-test", "dictate-to-rem");
  kde.press("someone-else", "dictate-to-rem");
  await until(() => pressed.length >= 1);
  await settle();
  assert.deepEqual(pressed, ["dictate-to-rem"]);

  // Another program on the bus can't fake a press: only kglobalaccel's signals are heard.
  const spoofer = await connect({ socketPath: setup.socketPath });
  spoofer.signal({ path: "/component/raticode_test", interface: "org.kde.kglobalaccel.Component", member: "globalShortcutPressed", signature: "ssx", body: ["raticode-test", "dictate-to-rem", 1n] });
  await settle(250);
  spoofer.close();
  assert.deepEqual(pressed, ["dictate-to-rem"]);

  await shortcuts.set([]);
  assert.equal(kde.actions.size, 0);
  assert.deepEqual(shortcuts.status(), { route: "", error: "", version: 0, shortcuts: {} });
});

test("KDE: a key another shortcut has is never taken, and the holder is named", async (t) => {
  const setup = await desktop(t, { kde: { taken: { [CTRL_ALT_SPACE]: ["Lock Session", "Session Management"] } }, env: { XDG_CURRENT_DESKTOP: "KDE" } });
  if (!setup) return;
  const { shortcuts, kde } = setup;
  const status = await shortcuts.set([DICTATE]);
  const shortcut = status.shortcuts[DICTATE.id];
  assert.equal(shortcut.state, "unassigned");
  assert.match(shortcut.message, /KDE already uses Ctrl\+Alt\+Space for "Lock Session" \(Session Management\)/);
  // Listed under Raticode with no key, so it can be given one in System Settings.
  assert.deepEqual(kde.actions.get("raticode-test/dictate-to-rem").keys, []);
  // A free key from Settings > Keybindings is taken.
  const next = await shortcuts.set([{ ...DICTATE, accelerator: "Control+Alt+D" }]);
  assert.equal(next.shortcuts[DICTATE.id].state, "registered");
  assert.equal(next.shortcuts[DICTATE.id].trigger, "Ctrl+Alt+D");
});

test("KDE: a key changed in System Settings is reported, kept after a restart and after recording a key, and replaced by a new key from Raticode", async (t) => {
  const setup = await desktop(t, { kde: true, env: { XDG_CURRENT_DESKTOP: "KDE" } });
  if (!setup) return;
  const { shortcuts, kde, changes } = setup;
  await shortcuts.set([DICTATE]);
  kde.rebind("raticode-test", "dictate-to-rem", 0x0c000044);
  await until(() => changes.length >= 1);
  assert.deepEqual(changes.at(-1).shortcuts[DICTATE.id], { state: "registered", trigger: "Ctrl+Alt+D", message: "", elsewhere: true });

  // Settings records a new key: Raticode's shortcut stands aside, then comes back as it was.
  await shortcuts.suspend(true);
  assert.equal(kde.actions.get("raticode-test/dictate-to-rem").present, false);
  const resumed = await shortcuts.suspend(false);
  assert.equal(resumed.shortcuts[DICTATE.id].trigger, "Ctrl+Alt+D");
  assert.equal(kde.actions.get("raticode-test/dictate-to-rem").present, true);

  // A restart (a new connection) keeps the key chosen in System Settings.
  await shortcuts.dispose();
  const again = createLinuxShortcuts({
    connectBus: () => connect({ socketPath: setup.socketPath }),
    env: { XDG_CURRENT_DESKTOP: "KDE" },
    component: { unique: "raticode-test", friendly: "Raticode" },
  });
  t.after(() => again.dispose());
  const restarted = await again.set([DICTATE]);
  assert.equal(restarted.shortcuts[DICTATE.id].trigger, "Ctrl+Alt+D");
  assert.equal(restarted.shortcuts[DICTATE.id].elsewhere, true);

  // Changing the key in Raticode's Settings replaces it.
  const changed = await again.set([{ ...DICTATE, accelerator: "Super+Space" }]);
  assert.deepEqual(changed.shortcuts[DICTATE.id], { state: "registered", trigger: "Meta+Space", message: "", elsewhere: false });
  assert.deepEqual(kde.actions.get("raticode-test/dictate-to-rem").keys, [0x10000020]);
});

test("KDE: dropping one of two shortcuts removes only that one", async (t) => {
  const setup = await desktop(t, { kde: true, env: { XDG_CURRENT_DESKTOP: "KDE" } });
  if (!setup) return;
  const { shortcuts, kde } = setup;
  const [first, second] = await Promise.all([shortcuts.update(DICTATE.id, DICTATE), shortcuts.update(SCREEN.id, SCREEN)]);
  assert.equal(first.state, "registered");
  assert.equal(second.state, "registered");
  assert.deepEqual([...kde.actions.keys()].sort(), ["raticode-test/ask-rem-about-screen", "raticode-test/dictate-to-rem"]);
  const dropped = await shortcuts.update(SCREEN.id, null);
  assert.equal(dropped.state, "off");
  assert.deepEqual([...kde.actions.keys()], ["raticode-test/dictate-to-rem"]);
});

test("portal: Raticode names itself, asks once with its key, waits for the person, and hears presses only from its own session", async (t) => {
  const setup = await desktop(t, { portal: { holdBind: true, knownApps: ["gofer-flow"] }, env: { XDG_CURRENT_DESKTOP: "GNOME" } });
  if (!setup) return;
  const { shortcuts, portal, pressed, changes } = setup;
  // set() returns as soon as the desktop's prompt shows, so Settings isn't held.
  const waiting = await shortcuts.set([DICTATE]);
  assert.equal(waiting.route, "portal");
  assert.equal(waiting.version, 2);
  assert.equal(waiting.shortcuts[DICTATE.id].state, "approval");
  assert.deepEqual([...portal.state.apps.values()], ["gofer-flow"]);
  const [[id, properties]] = portal.state.bindRequests[0];
  assert.equal(id, "dictate-to-rem");
  assert.deepEqual(properties, { description: "Dictate to Rem", preferred_trigger: "CTRL+ALT+space" });

  portal.answerBind(0);
  await until(() => changes.some((status) => status.shortcuts[DICTATE.id]?.state === "registered"));
  assert.deepEqual(shortcuts.status().shortcuts[DICTATE.id], { state: "registered", trigger: "Ctrl+Alt+Space", message: "" });

  portal.press("dictate-to-rem");
  portal.press("dictate-to-rem", "/org/freedesktop/portal/desktop/session/1_999/someone_else");
  portal.press("not-ours");
  await until(() => pressed.length >= 1);
  await settle();
  assert.deepEqual(pressed, ["dictate-to-rem"]);

  // The key is changed in the desktop's settings.
  portal.rebind("dictate-to-rem", "LOGO+d");
  await until(() => shortcuts.status().shortcuts[DICTATE.id].trigger === "Meta+D");

  // Turning it off closes the session: no more presses.
  await shortcuts.set([]);
  assert.ok(portal.state.calls.some(([member]) => member === "Close"));
});

test("portal: a shortcut the desktop already knows needs no prompt after a restart", async (t) => {
  const setup = await desktop(t, { portal: { knownApps: ["gofer-flow"] }, env: { XDG_CURRENT_DESKTOP: "GNOME" } });
  if (!setup) return;
  const { shortcuts, portal } = setup;
  await shortcuts.set([DICTATE]);
  await shortcuts.dispose();
  const again = createLinuxShortcuts({
    connectBus: () => connect({ socketPath: setup.socketPath }),
    env: { XDG_CURRENT_DESKTOP: "GNOME" },
    appId: "gofer-flow",
    hasDesktopFile: () => true,
  });
  t.after(() => again.dispose());
  const status = await again.set([DICTATE]);
  assert.equal(status.shortcuts[DICTATE.id].state, "registered");
  assert.equal(portal.state.bindRequests.length, 1);
});

test("portal: a declined prompt is reported, and asking again shows it again", async (t) => {
  const setup = await desktop(t, { portal: { holdBind: true, knownApps: ["gofer-flow"] }, env: { XDG_CURRENT_DESKTOP: "GNOME" } });
  if (!setup) return;
  const { shortcuts, portal, changes } = setup;
  await shortcuts.set([DICTATE]);
  portal.answerBind(1);
  await until(() => changes.some((status) => status.shortcuts[DICTATE.id]?.state === "declined"));
  // Settings reopening doesn't nag: the same request isn't repeated on its own.
  const reopened = await shortcuts.set([DICTATE]);
  assert.equal(reopened.shortcuts[DICTATE.id].state, "declined");
  assert.equal(portal.state.bindRequests.length, 1);

  const retried = await shortcuts.retry();
  assert.equal(retried.shortcuts[DICTATE.id].state, "approval");
  assert.equal(portal.state.bindRequests.length, 2);
  portal.answerBind(0);
  await until(() => shortcuts.status().shortcuts[DICTATE.id].state === "registered");
});

test("portal: adding a second shortcut later uses a new session, since a session binds once", async (t) => {
  const setup = await desktop(t, { portal: { knownApps: ["gofer-flow"] }, env: { XDG_CURRENT_DESKTOP: "GNOME" } });
  if (!setup) return;
  const { shortcuts, portal } = setup;
  await shortcuts.update(DICTATE.id, DICTATE);
  const second = await shortcuts.update(SCREEN.id, SCREEN);
  assert.equal(second.state, "registered");
  assert.equal(portal.state.bindRequests.length, 2);
  assert.deepEqual(portal.state.bindRequests[1].map(([id]) => id), ["dictate-to-rem", "ask-rem-about-screen"]);
  assert.equal([...portal.state.sessions.values()].filter((session) => !session.closed).length, 1);
});

test("portal: Hyprland binds without a key, and Settings gets the config line to add", async (t) => {
  const setup = await desktop(t, { portal: { assignKeys: false, knownApps: ["gofer-flow"] }, env: { XDG_CURRENT_DESKTOP: "Hyprland" } });
  if (!setup) return;
  const status = await setup.shortcuts.set([DICTATE]);
  const shortcut = status.shortcuts[DICTATE.id];
  assert.equal(shortcut.state, "unassigned");
  assert.match(shortcut.message, /bind = CTRL ALT, space, global, gofer-flow:dictate-to-rem/);
});

test("portal refusing Raticode (no desktop file) falls back to GNOME's custom shortcuts, or to the command elsewhere", async (t) => {
  const store = fakeGsettings();
  const setup = await desktop(t, {
    portal: { knownApps: [] },
    env: { XDG_CURRENT_DESKTOP: "ubuntu:GNOME" },
    hasDesktopFile: () => false,
    runGsettings: store.run,
    gnomeCommand: (id) => (id === DICTATE.id ? "/opt/Raticode/raticode --toggle-dictation" : ""),
  });
  if (!setup) return;
  const status = await setup.shortcuts.set([DICTATE]);
  assert.equal(status.route, "gnome");
  assert.equal(status.shortcuts[DICTATE.id].state, "registered");
  assert.match(status.error, /refused Raticode: An app id is required/);
  assert.equal(store.get("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/raticode-dictate-to-rem/", "command"), "'/opt/Raticode/raticode --toggle-dictation'");

  const sway = await desktop(t, { portal: { version: 0 }, env: { XDG_CURRENT_DESKTOP: "sway" } });
  const manual = await sway.shortcuts.set([DICTATE]);
  assert.equal(manual.route, "manual");
  assert.equal(manual.shortcuts[DICTATE.id].state, "manual");
});

test("no session bus at all means the command fallback, not an error", async () => {
  const shortcuts = createLinuxShortcuts({ connectBus: () => Promise.reject(new Error("No D-Bus session bus in this session.")), env: { XDG_CURRENT_DESKTOP: "KDE" } });
  const status = await shortcuts.set([DICTATE]);
  assert.equal(status.route, "manual");
  assert.equal(status.shortcuts[DICTATE.id].state, "manual");
  assert.match(status.error, /No D-Bus session bus/);
  await shortcuts.dispose();
});

/** gsettings for the media-keys schema, in memory. */
function fakeGsettings(initial = ["/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"]) {
  const values = new Map([["org.gnome.settings-daemon.plugins.media-keys|custom-keybindings", `[${initial.map((item) => `'${item}'`).join(", ")}]`]]);
  const calls = [];
  return {
    calls,
    get: (schema, key) => values.get(`${schema}|${key}`),
    async run(args) {
      calls.push(args);
      const [verb, schema, key, value] = args;
      if (verb === "list-keys") return { code: 0, stdout: "custom-keybindings\nhome\nscreensaver\n" };
      if (verb === "get") return { code: 0, stdout: `${values.get(`${schema}|${key}`) ?? "@as []"}\n` };
      if (verb === "set") {
        values.set(`${schema}|${key}`, value);
        return { code: 0, stdout: "" };
      }
      if (verb === "reset-recursively") {
        for (const name of [...values.keys()]) if (name.startsWith(`${schema}|`)) values.delete(name);
        return { code: 0, stdout: "" };
      }
      return { code: 1, stdout: "" };
    },
  };
}

test("GNOME without the portal: Raticode adds its own custom shortcut, keeps the user's, and removes only its own", async () => {
  const store = fakeGsettings();
  const shortcuts = createLinuxShortcuts({
    connectBus: () => Promise.reject(new Error("no bus")),
    env: { XDG_CURRENT_DESKTOP: "GNOME" },
    runGsettings: store.run,
    gnomeCommand: (id) => (id === DICTATE.id ? "env XDG_CONFIG_HOME='/home/me/it'\\''s' /opt/raticode --toggle-dictation" : ""),
  });
  const status = await shortcuts.set([DICTATE, SCREEN]);
  assert.equal(status.route, "gnome");
  assert.equal(status.shortcuts[DICTATE.id].state, "registered");
  // There is no command for the screen shortcut, so it needs the person.
  assert.equal(status.shortcuts[SCREEN.id].state, "manual");
  const entry = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/raticode-dictate-to-rem/";
  assert.equal(store.get(entry, "binding"), "'<Control><Alt>space'");
  assert.equal(store.get(entry, "name"), "'Raticode: Dictate to Rem'");
  assert.equal(store.get(entry, "command"), "'env XDG_CONFIG_HOME=\\'/home/me/it\\'\\\\\\'\\'s\\' /opt/raticode --toggle-dictation'");
  assert.equal(
    store.get("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings"),
    "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/', '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/raticode-dictate-to-rem/']",
  );
  await shortcuts.set([]);
  assert.equal(store.get("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings"), "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/']");
  assert.equal(store.get(entry, "command"), undefined);
  await shortcuts.dispose();
});
