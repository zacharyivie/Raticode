// A pretend Linux desktop for tests: a private dbus-daemon with a fake Global Shortcuts
// portal and a fake KDE shortcut service (kglobalaccel) on it, so linux-shortcuts.cjs talks
// real D-Bus without touching the developer's desktop. Only what Raticode uses is
// implemented, with the answers the real services give (checked against Plasma 6.8 and the
// portal spec; see linux-shortcuts.cjs).
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Variant, connect } = require("../dbus-session.cjs");

const PORTAL = "org.freedesktop.portal.Desktop";
const PORTAL_PATH = "/org/freedesktop/portal/desktop";
const SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts";

function dbusDaemonInstalled() {
  return process.platform === "linux" && spawnSync("dbus-daemon", ["--version"]).status === 0;
}

/** Starts a private bus. Returns { socketPath, stop }. */
async function startBus() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-fake-desktop-"));
  const socketPath = path.join(dir, "bus");
  const daemon = spawn("dbus-daemon", [
    `--config-file=${path.join(__dirname, "test-session-bus.conf")}`,
    `--address=unix:path=${socketPath}`,
    "--nofork",
    "--print-address",
  ], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve, reject) => {
    daemon.stdout.once("data", resolve);
    daemon.once("exit", (code) => reject(new Error(`dbus-daemon exited with ${code}`)));
  });
  return {
    socketPath,
    stop() {
      daemon.kill();
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

function shortcutList(entries) {
  return entries.map(([id, info]) => [id, {
    description: new Variant("s", info.description),
    trigger_description: new Variant("s", info.trigger),
  }]);
}

/**
 * A fake portal. options:
 *   version          GlobalShortcuts version (0: no GlobalShortcuts interface)
 *   requireAppId     refuse CreateSession unless Register named an app (xdg-desktop-portal 1.21+)
 *   knownApps        app ids with an installed desktop file, for Register
 *   assignKeys       false: like Hyprland, bind without giving a key
 *   holdBind         true: BindShortcuts waits for answerBind(code) (a prompt on screen)
 */
async function startFakePortal(socketPath, options = {}) {
  const service = await connect({ socketPath });
  await service.requestName(PORTAL);
  const state = {
    calls: [],
    apps: new Map(), // connection name -> app id
    stored: new Map(), // app id -> Map(id -> { description, trigger })
    sessions: new Map(), // session path -> { sender, app, bound, closed }
    pendingBind: null,
  };
  const version = options.version ?? 2;

  function respond(message, requestPath, code, results) {
    service.signal({
      destination: message.sender,
      path: requestPath,
      interface: "org.freedesktop.portal.Request",
      member: "Response",
      signature: "ua{sv}",
      body: [code, results],
    });
  }

  function requestPathFor(message, token) {
    return `${PORTAL_PATH}/request/${message.sender.slice(1).replace(/\./g, "_")}/${token}`;
  }

  service.serve((message) => {
    const { member, sender } = message;
    state.calls.push([member, message.interface, message.path]);
    if (message.interface === "org.freedesktop.host.portal.Registry" && member === "Register") {
      const [appId] = message.body;
      if (state.apps.has(sender)) return service.reply(message, { error: "org.freedesktop.portal.Error.Failed", errorMessage: "Connection already associated with an application ID" });
      if (!(options.knownApps ?? []).includes(appId)) return service.reply(message, { error: "org.freedesktop.portal.Error.Failed", errorMessage: `App info not found for '${appId}'` });
      state.apps.set(sender, appId);
      return service.reply(message);
    }
    if (message.interface === "org.freedesktop.DBus.Properties" && member === "Get") {
      if (message.body[0] !== SHORTCUTS || !version) return service.reply(message, { error: "org.freedesktop.DBus.Error.InvalidArgs", errorMessage: "No such interface" });
      return service.reply(message, { signature: "v", body: [new Variant("u", version)] });
    }
    if (message.interface === "org.freedesktop.portal.Session" && member === "Close") {
      const session = state.sessions.get(message.path);
      if (session) session.closed = true;
      return service.reply(message);
    }
    if (message.interface !== SHORTCUTS || !version) {
      return service.reply(message, { error: "org.freedesktop.DBus.Error.UnknownMethod", errorMessage: `No ${member}` });
    }
    const app = state.apps.get(sender) || "";
    if (member === "CreateSession") {
      const [opts] = message.body;
      if (options.requireAppId !== false && !app) return service.reply(message, { error: "org.freedesktop.portal.Error.NotAllowed", errorMessage: "An app id is required" });
      const handle = requestPathFor(message, opts.handle_token);
      const sessionPath = `${PORTAL_PATH}/session/${sender.slice(1).replace(/\./g, "_")}/${opts.session_handle_token}`;
      state.sessions.set(sessionPath, { sender, app, bound: false, closed: false });
      service.reply(message, { signature: "o", body: [handle] });
      return respond(message, handle, 0, { session_handle: new Variant("s", sessionPath) });
    }
    const [sessionPath] = message.body;
    const session = state.sessions.get(sessionPath);
    if (!session || session.closed || session.sender !== sender) {
      return service.reply(message, { error: "org.freedesktop.portal.Error.InvalidArgument", errorMessage: "Invalid session" });
    }
    const stored = state.stored.get(session.app) ?? new Map();
    if (member === "ListShortcuts") {
      const handle = requestPathFor(message, message.body[1].handle_token);
      service.reply(message, { signature: "o", body: [handle] });
      return respond(message, handle, 0, { shortcuts: new Variant("a(sa{sv})", shortcutList([...stored])) });
    }
    if (member === "BindShortcuts") {
      const [, wanted, , opts] = message.body;
      if (session.bound) return service.reply(message, { error: "org.freedesktop.portal.Error.Failed", errorMessage: "An application can only attempt to bind shortcuts of a session once" });
      session.bound = true;
      const handle = requestPathFor(message, opts.handle_token);
      service.reply(message, { signature: "o", body: [handle] });
      state.bindRequests = [...(state.bindRequests ?? []), wanted];
      const finish = (code) => {
        if (code === 0) {
          for (const [id, info] of wanted) {
            if (!stored.has(id)) stored.set(id, { description: info.description, trigger: options.assignKeys === false ? "" : info.preferred_trigger });
          }
          state.stored.set(session.app, stored);
          const bound = [...stored].filter(([id]) => wanted.some(([wantedId]) => wantedId === id));
          respond(message, handle, 0, { shortcuts: new Variant("a(sa{sv})", shortcutList(bound)) });
        } else {
          respond(message, handle, code, {});
        }
      };
      if (options.holdBind) state.pendingBind = finish;
      else finish(options.bindCode ?? 0);
      return undefined;
    }
    return service.reply(message, { error: "org.freedesktop.DBus.Error.UnknownMethod", errorMessage: `No ${member}` });
  });

  function liveSession() {
    return [...state.sessions.entries()].find(([, session]) => !session.closed && session.bound);
  }

  return {
    state,
    /** The person answers the prompt: 0 allow, 1 decline. */
    answerBind(code) {
      const finish = state.pendingBind;
      state.pendingBind = null;
      finish?.(code);
    },
    /** The bound shortcut is pressed. */
    press(id, sessionPath = liveSession()?.[0]) {
      service.signal({ path: PORTAL_PATH, interface: SHORTCUTS, member: "Activated", signature: "osta{sv}", body: [sessionPath, id, 1234n, {}] });
    },
    /** The key is changed in the desktop's settings. */
    rebind(id, trigger) {
      const [sessionPath, session] = liveSession();
      const stored = state.stored.get(session.app);
      stored.get(id).trigger = trigger;
      service.signal({ path: PORTAL_PATH, interface: SHORTCUTS, member: "ShortcutsChanged", signature: "oa(sa{sv})", body: [sessionPath, shortcutList([...stored])] });
    },
    close: () => service.close(),
  };
}

/** A fake KDE shortcut service. options.taken: { [qtKey]: [friendlyName, componentFriendly] } */
async function startFakeKde(socketPath, options = {}) {
  const service = await connect({ socketPath });
  await service.requestName("org.kde.kglobalaccel");
  // "component/action" -> { keys, defaults, present, saved }
  const actions = new Map();
  const calls = [];
  const taken = options.taken ?? {};

  const keyOf = (id) => `${id[0]}/${id[1]}`;
  const sequences = (keys) => keys.map((key) => [[key, 0, 0, 0]]);
  const flat = (seqs) => (seqs || []).map((sequence) => sequence[0][0]).filter(Boolean);

  service.serve((message) => {
    const { member, body } = message;
    calls.push([member, JSON.stringify(body, (_key, value) => (typeof value === "bigint" ? Number(value) : value))]);
    switch (member) {
      case "doRegister": {
        const key = keyOf(body[0]);
        if (!actions.has(key)) actions.set(key, { keys: [], defaults: [], present: false, saved: false });
        return service.reply(message);
      }
      case "setShortcutKeys": {
        const [id, seqs, flags] = body;
        const action = actions.get(keyOf(id));
        if (!action) return service.reply(message, { signature: "a(ai)", body: [[]] });
        action.present = Boolean(flags & 2);
        if (flags & 8) {
          action.defaults = flat(seqs);
          return service.reply(message, { signature: "a(ai)", body: [seqs] });
        }
        if (!(flags & 4) && action.saved) return service.reply(message, { signature: "a(ai)", body: [sequences(action.keys)] });
        action.keys = flat(seqs);
        action.saved = true;
        return service.reply(message, { signature: "a(ai)", body: [sequences(action.keys)] });
      }
      case "globalShortcutAvailable": {
        const [[keys], component] = body;
        const key = keys[0];
        const usedByOther = Boolean(taken[key]) || [...actions.entries()].some(([name, action]) => !name.startsWith(`${component}/`) && action.keys.includes(key));
        return service.reply(message, { signature: "b", body: [!usedByOther] });
      }
      case "globalShortcutsByKey": {
        const key = body[0][0][0];
        const holder = taken[key];
        const found = holder ? [[holder[0], holder[0], "other", holder[1], "default", "Default Context", [key], [key]]] : [];
        return service.reply(message, { signature: "a(ssssssaiai)", body: [found] });
      }
      case "setInactive": {
        const action = actions.get(keyOf(body[0]));
        if (action) action.present = false;
        return service.reply(message);
      }
      case "unRegister": {
        actions.delete(keyOf(body[0]));
        return service.reply(message);
      }
      default:
        return service.reply(message, { error: "org.freedesktop.DBus.Error.UnknownMethod", errorMessage: `No ${member}` });
    }
  });

  return {
    actions,
    calls,
    /** A press, as KWin reports it. */
    press(component, action) {
      service.signal({
        path: `/component/${component.replace(/[^a-zA-Z0-9]/g, "_")}`,
        interface: "org.kde.kglobalaccel.Component",
        member: "globalShortcutPressed",
        signature: "ssx",
        body: [component, action, 99n],
      });
    },
    /** The key is changed in System Settings. */
    rebind(component, action, key) {
      const entry = actions.get(`${component}/${action}`);
      entry.keys = [key];
      service.signal({ path: "/kglobalaccel", interface: "org.kde.KGlobalAccel", member: "yourShortcutsChanged", signature: "asa(ai)", body: [[component, action, "", ""], sequences([key])] });
    },
    close: () => service.close(),
  };
}

module.exports = { dbusDaemonInstalled, startBus, startFakeKde, startFakePortal };
