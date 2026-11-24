// System-wide shortcuts on a Linux Wayland desktop, where an app can't simply grab a key
// (Electron's globalShortcut only works there while one of Raticode's windows is focused,
// or not at all). Raticode asks the desktop instead, trying in order:
//
// 1. "kde": KDE Plasma's shortcut service (kglobalaccel over D-Bus), which is what every
//    KDE app's shortcuts go through. No prompt; the shortcut shows up under Raticode in
//    System Settings > Keyboard > Shortcuts, where it can be changed.
// 2. "portal": the desktop's Global Shortcuts portal (GNOME 48+, Hyprland and others). The
//    desktop may ask once to allow it and owns the key from then on.
// 3. "gnome": older GNOME without that portal: a custom shortcut in GNOME's own keyboard
//    settings that runs `Raticode --toggle-dictation`, added and removed by Raticode.
// 4. "manual": none of these: Settings shows the command to bind by hand.
//
// Shortcuts are { id, description, accelerator } with accelerators from toAccelerator().
// Nothing here sees audio or words; a press only calls onPress(id).

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Variant, connect, matchRule } = require("./dbus-session.cjs");
const { gnomeBinding, kdeKeys, portalTrigger, readableTrigger } = require("./shortcut-keys.cjs");

const KDE_SERVICE = "org.kde.kglobalaccel";
const KDE_PATH = "/kglobalaccel";
const KDE_INTERFACE = "org.kde.KGlobalAccel";
const KDE_COMPONENT_INTERFACE = "org.kde.kglobalaccel.Component";
// KGlobalAccel::SetShortcutFlag
const KDE_SET_PRESENT = 2;
const KDE_NO_AUTOLOADING = 4;
const KDE_IS_DEFAULT = 8;

const PORTAL_SERVICE = "org.freedesktop.portal.Desktop";
const PORTAL_PATH = "/org/freedesktop/portal/desktop";
const PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts";
const REQUEST_INTERFACE = "org.freedesktop.portal.Request";
const SESSION_INTERFACE = "org.freedesktop.portal.Session";
const REGISTRY_INTERFACE = "org.freedesktop.host.portal.Registry";

const GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys";
const GNOME_KEY = "custom-keybindings";
const GNOME_ENTRY_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding";
const GNOME_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings";

// The XDG shortcuts route only exists for a running session; how long to wait before
// trying to reach the desktop again after its shortcut service went away.
const RECONNECT_MS = 5000;
// How long a portal answer may take before Raticode decides the desktop is showing a prompt.
const PROMPT_GRACE_MS = 400;

function desktopNames(env) {
  return String(env.XDG_CURRENT_DESKTOP || "").split(":").map((name) => name.trim().toLowerCase()).filter(Boolean);
}

/** Tokens for portal requests: letters, digits and underscores only. */
function tokenFor(prefix, count) {
  return `raticode_${prefix}_${count}`;
}

/** The object path the portal uses for a request, so its answer can be awaited first. */
function requestPath(uniqueName, token) {
  return `${PORTAL_PATH}/request/${String(uniqueName).replace(/^:/, "").replace(/\./g, "_")}/${token}`;
}

/**
 * deps:
 *   connectBus()      resolves to a dbus-session connection (tests pass a fake)
 *   env               process.env, for the desktop's name
 *   appId             Raticode's desktop file name without ".desktop" ("gofer-flow"); the
 *                     portal only accepts an app whose desktop file is installed
 *   hasDesktopFile(id) whether that desktop file is installed
 *   component         { unique, friendly } for KDE's shortcut list
 *   runGsettings(args) resolves to { code, stdout } (GNOME route only)
 *   gnomeCommand      the command a GNOME custom shortcut runs, per shortcut id
 *   onPress(id)       a shortcut was pressed
 *   onChange()        status() changed on its own (approved, rebound, desktop restarted)
 *   setTimer          setTimeout, for tests
 */
function createLinuxShortcuts({
  connectBus = () => connect(),
  env = process.env,
  appId = "gofer-flow",
  hasDesktopFile = () => false,
  component = { unique: "raticode", friendly: "Raticode" },
  runGsettings = null,
  gnomeCommand = () => "",
  onPress = () => {},
  onChange = () => {},
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  let wanted = [];
  let bus = null;
  let connecting = null;
  let route = "";
  let routeError = "";
  // id -> { state, trigger, message }. state: working | registered | approval | declined | unassigned | failed | manual
  let states = new Map();
  let applying = Promise.resolve();
  let disposed = false;
  let reconnectTimer = null;
  let requestCount = 0;
  // Portal
  let portalVersion = 0;
  let session = "";
  let portalBound = new Map();
  let declined = false;
  // A portal session takes one BindShortcuts; adding a shortcut later needs a new session.
  let sessionBindTried = false;
  // The portal refused Raticode (no desktop file, no app id): use the next route.
  let portalRefused = "";
  const pendingResponses = new Map();
  // KDE
  const kdeRegistered = new Map();
  // Every action Raticode asked KDE for in this run, even one that failed halfway, so
  // turning it off removes all of them.
  const kdeTouched = new Map();
  // id -> the key Raticode last gave KDE in this run.
  const kdeApplied = new Map();
  // GNOME
  const gnomeAdded = new Set();
  // Signal subscriptions made on the current connection.
  let matched = new Set();

  function status() {
    const shortcuts = {};
    for (const shortcut of wanted) {
      shortcuts[shortcut.id] = { state: "working", trigger: "", message: "", ...(states.get(shortcut.id) || {}) };
    }
    return { route, error: routeError, version: portalVersion, shortcuts };
  }

  function setState(id, next) {
    states.set(id, { trigger: "", message: "", ...next });
  }

  function changed() {
    if (!disposed) {
      try { onChange(status()); } catch { /* The listener's problem. */ }
    }
  }

  // --- The bus --------------------------------------------------------------------------

  async function ensureBus() {
    if (bus && !bus.closed) return bus;
    if (!connecting) {
      connecting = (async () => {
        const connection = await connectBus();
        connection.onSignal(handleSignal);
        connection.onClose(() => {
          if (bus !== connection) return;
          bus = null;
          matched = new Set();
          session = "";
          portalBound = new Map();
          kdeRegistered.clear();
          for (const [, waiter] of pendingResponses) waiter.reject(new Error("The desktop's session bus closed."));
          pendingResponses.clear();
          if (disposed || !wanted.length) return;
          for (const shortcut of wanted) setState(shortcut.id, { state: "working", message: "Reconnecting to the desktop…" });
          changed();
          scheduleReconnect();
        });
        bus = connection;
        return connection;
      })().finally(() => { connecting = null; });
    }
    return connecting;
  }

  function scheduleReconnect() {
    clearTimer(reconnectTimer);
    reconnectTimer = setTimer(() => {
      reconnectTimer = null;
      if (!disposed && wanted.length) void set(wanted);
    }, RECONNECT_MS);
    reconnectTimer?.unref?.();
  }

  function handleSignal(message) {
    if (message.interface === REQUEST_INTERFACE && message.member === "Response") {
      const waiter = pendingResponses.get(message.path);
      if (waiter) {
        pendingResponses.delete(message.path);
        waiter.resolve({ code: Number(message.body[0]), results: message.body[1] || {} });
      }
      return;
    }
    if (message.interface === PORTAL_INTERFACE && message.path === PORTAL_PATH) {
      if (message.body[0] !== session || !session) return;
      if (message.member === "Activated") {
        const id = String(message.body[1] || "");
        if (wanted.some((shortcut) => shortcut.id === id)) onPress(id);
      } else if (message.member === "ShortcutsChanged") {
        applyPortalList(message.body[1]);
        changed();
      }
      return;
    }
    if (message.interface === KDE_COMPONENT_INTERFACE && message.member === "globalShortcutPressed") {
      const [componentUnique, actionUnique] = message.body;
      if (componentUnique === component.unique && kdeRegistered.has(actionUnique)) onPress(actionUnique);
      return;
    }
    if (message.interface === KDE_INTERFACE && message.member === "yourShortcutsChanged") {
      const [actionId, keys] = message.body;
      if (Array.isArray(actionId) && actionId[0] === component.unique && kdeRegistered.has(actionId[1])) {
        noteKdeKeys(actionId[1], keys);
        changed();
      }
    }
  }

  // --- KDE ------------------------------------------------------------------------------

  async function kdeAvailable(connection) {
    if (!desktopNames(env).includes("kde")) return false;
    try {
      return await connection.nameHasOwner(KDE_SERVICE);
    } catch {
      return false;
    }
  }

  function actionId(shortcut) {
    return [component.unique, shortcut.id, component.friendly, shortcut.description];
  }

  function kdeCall(member, signature, body, objectPath = KDE_PATH, iface = KDE_INTERFACE) {
    return bus.call({ destination: KDE_SERVICE, path: objectPath, interface: iface, member, signature, body });
  }

  function noteKdeKeys(id, keys) {
    const shortcut = wanted.find((item) => item.id === id);
    const ours = shortcut ? kdeKeys(shortcut.accelerator)[0] : 0;
    const first = firstKey(keys);
    if (!first) {
      // KDE gives no key when another shortcut already has it, or when it was cleared in
      // System Settings.
      setState(id, {
        state: "unassigned",
        message: "KDE didn't give Raticode this key: another shortcut may already use it, or it was cleared in System Settings. Choose another key in Settings > Keybindings, or set one in System Settings > Keyboard > Shortcuts > Raticode.",
      });
      return;
    }
    // A key chosen in System Settings wins over Raticode's until Raticode's is changed.
    setState(id, { state: "registered", trigger: qtKeyText(first), elsewhere: first !== ours });
  }

  async function applyKde() {
    if (!matched.has("kde")) {
      matched.add("kde");
      // Only KDE's shortcut service can send these, so another program can't fake a press.
      await bus.addMatch(matchRule({ sender: KDE_SERVICE, interface: KDE_COMPONENT_INTERFACE, member: "globalShortcutPressed" }));
      await bus.addMatch(matchRule({ sender: KDE_SERVICE, interface: KDE_INTERFACE, member: "yourShortcutsChanged" }));
    }
    const wantedIds = new Set(wanted.map((shortcut) => shortcut.id));
    for (const [id, previous] of kdeTouched) {
      if (!wantedIds.has(id)) {
        await kdeCall("unRegister", "as", [previous]).catch(() => {});
        kdeTouched.delete(id);
        kdeRegistered.delete(id);
        states.delete(id);
      }
    }
    for (const shortcut of wanted) {
      const keys = kdeKeys(shortcut.accelerator);
      const previous = kdeRegistered.get(shortcut.id);
      if (previous && previous.accelerator === shortcut.accelerator) continue;
      const id = actionId(shortcut);
      // One key sequence of one combination.
      const sequences = [[keys]];
      try {
        kdeTouched.set(shortcut.id, id);
        await kdeCall("doRegister", "as", [id]);
        // The default first, so "Reset to default" in System Settings gives Raticode's key.
        await kdeCall("setShortcutKeys", "asa(ai)u", [id, sequences, KDE_SET_PRESENT | KDE_IS_DEFAULT | KDE_NO_AUTOLOADING]);
        // KDE keeps a key the user chose in System Settings, unless the key in Raticode's
        // Settings has just been changed: asking with no key returns the saved one.
        const changedHere = kdeApplied.has(shortcut.id) && kdeApplied.get(shortcut.id) !== shortcut.accelerator;
        let active = [];
        if (!changedHere) [active] = await kdeCall("setShortcutKeys", "asa(ai)u", [id, [], KDE_SET_PRESENT]);
        kdeRegistered.set(shortcut.id, { ...shortcut });
        kdeApplied.set(shortcut.id, shortcut.accelerator);
        if (firstKey(active)) {
          noteKdeKeys(shortcut.id, active);
          continue;
        }
        // KDE doesn't refuse a key another shortcut has (its settings page checks first), so
        // Raticode checks too, and never takes a key from another shortcut.
        const [free] = await kdeCall("globalShortcutAvailable", "(ai)s", [[[...keys, 0, 0, 0].slice(0, 4)], component.unique]);
        if (!free) {
          const [holders] = await kdeCall("globalShortcutsByKey", "(ai)(i)", [[[...keys, 0, 0, 0].slice(0, 4)], [0]]).catch(() => [[]]);
          const holder = Array.isArray(holders) && holders[0] ? `"${holders[0][1]}" (${holders[0][3]})` : "another shortcut";
          await kdeCall("setShortcutKeys", "asa(ai)u", [id, [], KDE_SET_PRESENT | KDE_NO_AUTOLOADING]);
          setState(shortcut.id, {
            state: "unassigned",
            message: `KDE already uses ${qtKeyText(keys[0])} for ${holder}. Choose another key in Settings > Keybindings, or pick one for Raticode in System Settings > Keyboard > Shortcuts.`,
          });
          continue;
        }
        [active] = await kdeCall("setShortcutKeys", "asa(ai)u", [id, sequences, KDE_SET_PRESENT | KDE_NO_AUTOLOADING]);
        noteKdeKeys(shortcut.id, active);
      } catch (error) {
        setState(shortcut.id, { state: "failed", message: `KDE didn't take the shortcut: ${shortMessage(error)}` });
      }
    }
  }

  async function removeKde() {
    if (bus && !bus.closed) {
      for (const [, id] of kdeTouched) {
        // Removes it from KDE's list and its settings file too, so nothing is left in
        // System Settings; KDE drops the Raticode entry once it has no shortcuts.
        await kdeCall("unRegister", "as", [id]).catch(() => {});
      }
    }
    kdeTouched.clear();
    kdeRegistered.clear();
  }

  // --- Portal ---------------------------------------------------------------------------

  async function portalAvailable(connection) {
    if (portalRefused) return false;
    // Tell the portal who Raticode is, before anything else on this connection: it only
    // gives shortcuts to an app it can name, by an installed desktop file. Without the
    // file (an AppImage that was never installed) the portal will most likely refuse, and
    // the next route is used. Portals older than 1.19.4 have no registry, which is fine.
    if (hasDesktopFile(appId)) await connection.call({
      destination: PORTAL_SERVICE,
      path: PORTAL_PATH,
      interface: REGISTRY_INTERFACE,
      member: "Register",
      signature: "sa{sv}",
      body: [appId, {}],
      timeoutMs: 5000,
    }).catch(() => {});
    try {
      const [version] = await connection.call({
        destination: PORTAL_SERVICE,
        path: PORTAL_PATH,
        interface: "org.freedesktop.DBus.Properties",
        member: "Get",
        signature: "ss",
        body: [PORTAL_INTERFACE, "version"],
        timeoutMs: 5000,
      });
      portalVersion = Number(version) || 0;
      return portalVersion > 0;
    } catch {
      return false;
    }
  }

  async function portalRequest(member, signature, bodyFor, prefix, timeoutMs = 0) {
    requestCount += 1;
    const token = tokenFor(prefix, requestCount);
    const expected = requestPath(bus.name, token);
    const rule = matchRule({ sender: PORTAL_SERVICE, interface: REQUEST_INTERFACE, member: "Response", path: expected });
    await bus.addMatch(rule);
    let timer = null;
    const answered = new Promise((resolve, reject) => {
      pendingResponses.set(expected, { resolve, reject });
      if (timeoutMs) {
        timer = setTimer(() => {
          pendingResponses.delete(expected);
          reject(new Error(`The desktop didn't answer ${member}.`));
        }, timeoutMs);
      }
    });
    // Awaited below; this only keeps an early failure (the bus closing mid-call) handled.
    answered.catch(() => {});
    try {
      const [handle] = await bus.call({
        destination: PORTAL_SERVICE,
        path: PORTAL_PATH,
        interface: PORTAL_INTERFACE,
        member,
        signature,
        body: bodyFor(token),
        // A prompt waits for the person, so only the call itself has a deadline.
        timeoutMs: 30000,
      });
      if (handle && handle !== expected && pendingResponses.has(expected)) {
        // Very old portals pick their own path.
        pendingResponses.set(handle, pendingResponses.get(expected));
        pendingResponses.delete(expected);
        await bus.addMatch(matchRule({ sender: PORTAL_SERVICE, interface: REQUEST_INTERFACE, member: "Response", path: handle }));
      }
      return await answered;
    } finally {
      clearTimer(timer);
      pendingResponses.delete(expected);
      void bus?.removeMatch(rule);
    }
  }

  function applyPortalList(list) {
    portalBound = new Map();
    for (const entry of Array.isArray(list) ? list : []) {
      const [id, properties = {}] = entry;
      portalBound.set(String(id), properties);
    }
    for (const shortcut of wanted) {
      const properties = portalBound.get(shortcut.id);
      if (!properties) continue;
      const trigger = readableTrigger(properties.trigger_description);
      if (trigger) setState(shortcut.id, { state: "registered", trigger });
      else setState(shortcut.id, { state: "unassigned", message: "Your desktop accepted it but hasn't given it a key yet. Choose one in your desktop's shortcut settings." });
    }
  }

  async function applyPortal() {
    if (!matched.has("portal")) {
      matched.add("portal");
      await bus.addMatch(matchRule({ sender: PORTAL_SERVICE, interface: PORTAL_INTERFACE, path: PORTAL_PATH }));
    }
    if (!session) {
      const created = await portalRequest("CreateSession", "a{sv}", (token) => [{
        handle_token: new Variant("s", token),
        session_handle_token: new Variant("s", tokenFor("session", requestCount)),
      }], "create", 30000).catch((error) => {
        throw new PortalRefused(`The desktop's shortcut portal refused Raticode: ${shortMessage(error)}`);
      });
      if (created.code !== 0 || !created.results.session_handle) {
        throw new PortalRefused("The desktop's shortcut portal wouldn't start a session for Raticode.");
      }
      session = String(created.results.session_handle);
      sessionBindTried = false;
      const listed = await portalRequest("ListShortcuts", "oa{sv}", (token) => [session, { handle_token: new Variant("s", token) }], "list", 30000);
      if (listed.code === 0) applyPortalList(listed.results.shortcuts);
    }
    const missing = wanted.filter((shortcut) => !portalBound.has(shortcut.id));
    if (!missing.length) {
      applyPortalList([...portalBound.entries()]);
      return;
    }
    if (declined) {
      for (const shortcut of missing) setState(shortcut.id, { state: "declined", message: "The desktop's request was declined." });
      return;
    }
    if (sessionBindTried) {
      // One bind per session: start again with a new one, which lists what is kept.
      await closePortal();
      await applyPortal();
      return;
    }
    sessionBindTried = true;
    for (const shortcut of missing) setState(shortcut.id, { state: "approval", message: "Waiting for you to allow it in your desktop's prompt." });
    // A desktop that already knows the shortcuts answers at once. Otherwise a prompt is up:
    // say so, and stop holding Settings.
    let prompted = false;
    const promptTimer = setTimer(() => {
      prompted = true;
      changed();
      promptShowing();
    }, PROMPT_GRACE_MS);
    try {
      await bindAll(missing);
    } finally {
      clearTimer(promptTimer);
      // Settings was already told about the prompt, so tell it how it ended.
      if (prompted) changed();
    }
  }

  async function bindAll(missing) {
    // All wanted shortcuts go together: the desktop shows them in one prompt and keeps
    // the ones it already knows.
    const bound = await portalRequest("BindShortcuts", "oa(sa{sv})sa{sv}", (token) => [
      session,
      wanted.map((shortcut) => [shortcut.id, {
        description: new Variant("s", shortcut.description),
        preferred_trigger: new Variant("s", portalTrigger(shortcut.accelerator)),
      }]),
      "",
      { handle_token: new Variant("s", token) },
    ], "bind");
    if (bound.code === 0) {
      applyPortalList(bound.results.shortcuts);
      for (const shortcut of wanted) {
        if (!portalBound.has(shortcut.id)) setState(shortcut.id, { state: "unassigned", message: "Your desktop didn't add this shortcut. Add it in your desktop's shortcut settings." });
      }
      if (desktopNames(env).includes("hyprland")) noteHyprland();
    } else if (bound.code === 1) {
      declined = true;
      for (const shortcut of missing) setState(shortcut.id, { state: "declined", message: "The desktop's request was declined." });
    } else {
      for (const shortcut of missing) setState(shortcut.id, { state: "failed", message: "The desktop couldn't add the shortcut." });
    }
  }

  // Hyprland's portal never assigns a key; its config does, with a "global" bind.
  function noteHyprland() {
    for (const shortcut of wanted) {
      if (states.get(shortcut.id)?.state !== "unassigned") continue;
      const bind = hyprlandBind(shortcut.accelerator);
      setState(shortcut.id, {
        state: "unassigned",
        message: `Hyprland lets you choose the key in its config. Add this line to hyprland.conf: bind = ${bind}, global, ${appId}:${shortcut.id}`,
      });
    }
  }

  async function closePortal() {
    const closing = session;
    session = "";
    portalBound = new Map();
    if (!closing || !bus || bus.closed) return;
    await bus.call({ destination: PORTAL_SERVICE, path: closing, interface: SESSION_INTERFACE, member: "Close", timeoutMs: 5000 }).catch(() => {});
  }

  // --- GNOME ----------------------------------------------------------------------------

  function gnomePath(id) {
    return `${GNOME_BASE}/raticode-${id}/`;
  }

  async function gnomeAvailable() {
    if (!runGsettings || !desktopNames(env).includes("gnome")) return false;
    const result = await runGsettings(["list-keys", GNOME_SCHEMA]).catch(() => ({ code: 1, stdout: "" }));
    return result.code === 0 && result.stdout.split("\n").includes(GNOME_KEY);
  }

  async function gnomeList() {
    const result = await runGsettings(["get", GNOME_SCHEMA, GNOME_KEY]);
    if (result.code !== 0) throw new Error("GNOME's keyboard settings couldn't be read.");
    return [...result.stdout.matchAll(/'([^']*)'/g)].map((match) => match[1]);
  }

  async function gnomeSetList(paths) {
    const value = `[${paths.map((item) => `'${item}'`).join(", ")}]`;
    const result = await runGsettings(["set", GNOME_SCHEMA, GNOME_KEY, value]);
    if (result.code !== 0) throw new Error("GNOME's keyboard settings couldn't be changed.");
  }

  async function gnomeSetEntry(id, key, value) {
    const result = await runGsettings(["set", `${GNOME_ENTRY_SCHEMA}:${gnomePath(id)}`, key, gvariantString(value)]);
    if (result.code !== 0) throw new Error("GNOME's keyboard settings couldn't be changed.");
  }

  async function applyGnome() {
    const wantedIds = new Set(wanted.map((shortcut) => shortcut.id));
    let list = await gnomeList();
    for (const shortcut of wanted) {
      const command = gnomeCommand(shortcut.id);
      if (!command) {
        setState(shortcut.id, { state: "manual", message: "" });
        continue;
      }
      await gnomeSetEntry(shortcut.id, "name", `Raticode: ${shortcut.description}`);
      await gnomeSetEntry(shortcut.id, "command", command);
      await gnomeSetEntry(shortcut.id, "binding", gnomeBinding(shortcut.accelerator));
      if (!list.includes(gnomePath(shortcut.id))) list = [...list, gnomePath(shortcut.id)];
      gnomeAdded.add(shortcut.id);
      setState(shortcut.id, { state: "registered" });
    }
    const stale = [...gnomeAdded].filter((id) => !wantedIds.has(id));
    if (stale.length) {
      list = list.filter((item) => !stale.some((id) => item === gnomePath(id)));
      for (const id of stale) {
        await runGsettings(["reset-recursively", `${GNOME_ENTRY_SCHEMA}:${gnomePath(id)}`]).catch(() => {});
        gnomeAdded.delete(id);
      }
    }
    await gnomeSetList(list);
  }

  async function removeGnome() {
    if (!gnomeAdded.size) return;
    try {
      const list = await gnomeList();
      await gnomeSetList(list.filter((item) => ![...gnomeAdded].some((id) => item === gnomePath(id))));
      for (const id of gnomeAdded) await runGsettings(["reset-recursively", `${GNOME_ENTRY_SCHEMA}:${gnomePath(id)}`]).catch(() => {});
    } catch { /* Best effort: the entry runs a command that does nothing while dictation is off. */ }
    gnomeAdded.clear();
  }

  // --- Choosing and applying --------------------------------------------------------------

  async function chooseRoute() {
    let connection = null;
    try {
      connection = await ensureBus();
    } catch (error) {
      routeError = shortMessage(error);
    }
    routeError = portalRefused || routeError;
    if (connection) {
      if (await kdeAvailable(connection)) return "kde";
      if (await portalAvailable(connection)) return "portal";
    }
    if (await gnomeAvailable()) return "gnome";
    return "manual";
  }

  async function reconcile() {
    if (disposed) return;
    if (!wanted.length) {
      await clearAll();
      return;
    }
    const chosen = route && (route === "gnome" || route === "manual" || (bus && !bus.closed)) ? route : await chooseRoute();
    try {
      await applyRoute(chosen);
    } catch (error) {
      if (error instanceof PortalRefused && chosen === "portal") {
        // Try the next way instead (GNOME's own settings, or the command).
        portalRefused = error.message;
        session = "";
        route = "";
        await applyRoute(await chooseRoute()).catch(() => {});
        return;
      }
      route = chosen;
      for (const shortcut of wanted) {
        const current = states.get(shortcut.id)?.state ?? "working";
        if (current === "working" || current === "approval") setState(shortcut.id, { state: "failed", message: shortMessage(error) });
      }
    }
  }

  async function applyRoute(chosen) {
    if (chosen !== route && route === "kde") await removeKde();
    route = chosen;
    if (chosen === "kde") {
      await ensureBus();
      await applyKde();
    } else if (chosen === "portal") {
      await ensureBus();
      await applyPortal();
    } else if (chosen === "gnome") {
      await applyGnome();
    } else {
      for (const shortcut of wanted) setState(shortcut.id, { state: "manual", message: routeError });
    }
    route = chosen;
  }

  async function clearAll() {
    clearTimer(reconnectTimer);
    reconnectTimer = null;
    if (route === "kde") await removeKde();
    if (route === "portal") await closePortal();
    if (route === "gnome") await removeGnome();
    route = "";
    declined = false;
    // Turning it off and on again tries the portal again (a desktop file may be installed now).
    portalRefused = "";
    routeError = "";
    kdeApplied.clear();
    states = new Map();
    if (bus && !bus.closed) {
      const closing = bus;
      bus = null;
      matched = new Set();
      closing.close();
    }
  }

  // Callers waiting for an answer. A prompt waits on a person, so they are let go as soon
  // as it shows; onChange reports the answer.
  const waiters = new Set();
  function promptShowing() {
    for (const resolve of waiters) resolve(status());
    waiters.clear();
  }

  /**
   * The shortcuts Raticode wants now ([] removes them all). Resolves to status() once the
   * desktop has answered, or once its prompt is showing.
   */
  function set(shortcuts = []) {
    const next = shortcuts.filter((shortcut) => shortcut && shortcut.id && shortcut.accelerator);
    wanted = next.map((shortcut) => ({ id: String(shortcut.id), description: String(shortcut.description || shortcut.id), accelerator: String(shortcut.accelerator) }));
    for (const shortcut of wanted) {
      if (!states.get(shortcut.id)) setState(shortcut.id, { state: "working" });
    }
    const run = applying.then(() => (suspended ? undefined : reconcile()));
    applying = run.catch(() => {});
    return new Promise((resolve) => {
      waiters.add(resolve);
      void run.then(() => {
        waiters.delete(resolve);
        resolve(status());
      }, () => {
        waiters.delete(resolve);
        resolve(status());
      });
    });
  }

  // Raticode has two system-wide shortcuts that can come and go separately; the desktop is
  // asked about them together, so a prompt lists both at once.
  const entries = new Map();
  let batch = null;
  /** Wants (entry) or drops (null) one shortcut: { description, accelerator }. */
  function update(id, entry) {
    if (entry && entry.accelerator) entries.set(id, { id, ...entry });
    else entries.delete(id);
    if (!batch) {
      batch = new Promise((resolve) => {
        setTimer(() => {
          batch = null;
          resolve(set([...entries.values()]));
        }, 0);
      });
    }
    return batch.then((result) => ({ route: result.route, error: result.error, ...(result.shortcuts[id] || { state: entry ? "working" : "off" }) }));
  }

  // While Settings records a new key, Raticode's own shortcuts stand aside so the key
  // reaches Settings.
  let suspended = false;
  async function suspend(on) {
    suspended = on === true;
    await applying;
    if (suspended && route === "kde" && bus && !bus.closed) {
      for (const [, shortcut] of kdeRegistered) await kdeCall("setInactive", "as", [actionId(shortcut)]).catch(() => {});
      // Registering again after this makes them active, with the key from Settings.
      kdeRegistered.clear();
    } else if (!suspended) {
      await set([...entries.values()].length ? [...entries.values()] : wanted);
    }
    return status();
  }

  /** Asks again after the desktop's prompt was declined. */
  function retry() {
    declined = false;
    return set(wanted);
  }

  async function dispose() {
    disposed = true;
    promptShowing();
    clearTimer(reconnectTimer);
    // KDE keeps a registered shortcut in its list until it is removed; the portal and GNOME
    // keep theirs too, so next start needs no prompt. Quitting only lets go of the key.
    if (route === "kde" && bus && !bus.closed) {
      for (const [, shortcut] of kdeRegistered) await kdeCall("setInactive", "as", [actionId(shortcut)]).catch(() => {});
    }
    if (route === "portal") await closePortal();
    if (bus && !bus.closed) bus.close();
    bus = null;
  }

  return { set, update, suspend, retry, status, dispose };
}

class PortalRefused extends Error {}

// The first key combination in kglobalaccel's a(ai): key sequences, each a struct holding
// its combinations. 0 when there is none.
function firstKey(keys) {
  return (Array.isArray(keys) ? keys : [])
    .map((sequence) => (Array.isArray(sequence) && Array.isArray(sequence[0]) ? sequence[0][0] : 0))
    .find((combination) => combination) || 0;
}

/** Whether <id>.desktop is installed where the portal looks for it. */
function desktopFileInstalled(id, env = process.env, exists = fs.existsSync) {
  if (!/^[\w.-]+$/.test(String(id || ""))) return false;
  const dataHome = env.XDG_DATA_HOME || path.join(env.HOME || os.homedir(), ".local", "share");
  const dataDirs = String(env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":").filter(Boolean);
  return [dataHome, ...dataDirs].some((dir) => exists(path.join(dir, "applications", `${id}.desktop`)));
}

function hyprlandBind(accelerator) {
  const trigger = portalTrigger(accelerator).split("+");
  const key = trigger.pop() || "";
  const mods = trigger.map((name) => ({ CTRL: "CTRL", ALT: "ALT", SHIFT: "SHIFT", LOGO: "SUPER" }[name] || name)).join(" ");
  return `${mods}, ${key.length === 1 ? key.toUpperCase() : key}`;
}

function gvariantString(value) {
  return `'${String(value).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

function shortMessage(error) {
  return String(error instanceof Error ? error.message : error).slice(0, 200);
}

// Qt key code -> readable text, for a key the user picked in KDE's settings.
function qtKeyText(code) {
  const value = Number(code) || 0;
  const parts = [];
  if (value & 0x04000000) parts.push("Ctrl");
  if (value & 0x08000000) parts.push("Alt");
  if (value & 0x10000000) parts.push("Meta");
  if (value & 0x02000000) parts.push("Shift");
  const key = value & 0x01ffffff;
  const named = { 0x20: "Space", 0x01000004: "Enter", 0x01000001: "Tab", 0x01000003: "Backspace", 0x01000007: "Del", 0x01000006: "Ins", 0x01000010: "Home", 0x01000011: "End", 0x01000016: "PgUp", 0x01000017: "PgDown", 0x01000013: "Up", 0x01000015: "Down", 0x01000012: "Left", 0x01000014: "Right" }[key];
  if (named) parts.push(named);
  else if (key >= 0x01000030 && key <= 0x01000047) parts.push(`F${key - 0x01000030 + 1}`);
  else if (key >= 0x21 && key <= 0x7e) parts.push(String.fromCharCode(key).toUpperCase());
  else parts.push("another key");
  return parts.join("+");
}

module.exports = { createLinuxShortcuts, desktopFileInstalled, desktopNames, hyprlandBind, qtKeyText, requestPath };
