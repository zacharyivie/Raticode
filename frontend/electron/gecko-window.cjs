// Gecko as Raticode's second built-in browser on desktop. Mozilla offers no
// library for putting Gecko inside a desktop program, so Raticode runs real
// Firefox in a profile of its own and places Firefox's window inside Raticode's
// window over X11. Firefox draws it directly, without streaming, and brings its
// own tabs, address bar, settings, bookmarks, extensions and Mozilla account.
//
// No remote-control port is opened. Raticode only starts Firefox, positions its
// window and passes keyboard focus; it cannot read pages or passwords.
const { spawn: spawnProcess } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const { EVENT, MASK, X11Client } = require("./x11-client.cjs");
const { setWantsX11, waylandDesktop } = require("./gecko-display.cjs");
const { findFirefox } = require("./firefox-engine.cjs");
const { watchGeckoMedia } = require("./gecko-media.cjs");
const { IMPORT_ITEMS, findFirefoxProfiles, importProfile, inspectProfile, prefLine } = require("./firefox-profile.cjs");

const WINDOW_CLASS = "RaticodeGecko";
const NEW_TAB_PAGE = "about:newtab";
const WINDOW_TIMEOUT_MS = 45000;
const STOP_TIMEOUT_MS = 8000;
const POSIX_WATCHDOG = "while kill -0 \"$1\" 2>/dev/null && kill -0 \"$2\" 2>/dev/null; do sleep 2; done; kill -TERM \"$2\" 2>/dev/null";

// Written to user.js on every launch. Only what embedding needs; everything
// else is the user's to change in Firefox's own settings.
const PROFILE_PREFS = {
  "browser.aboutwelcome.enabled": false,
  "browser.shell.checkDefaultBrowser": false,
  "browser.startup.homepage_override.mstone": "ignore",
  // Tabs come back when the browser starts again.
  "browser.startup.page": 3,
  // Closing the last tab must not close the window inside Raticode.
  "browser.tabs.closeWindowWithLastTab": false,
  // There is no window manager frame, and no minimize or close buttons to offer.
  "browser.tabs.inTitlebar": 0,
  "datareporting.healthreport.uploadEnabled": false,
  "datareporting.policy.dataSubmissionEnabled": false,
  // Extensions the user imported into this profile load without a second
  // prompt. Firefox still refuses any that Mozilla hasn't signed.
  "extensions.autoDisableScopes": 14,
  "extensions.startupScanScopes": 1,
  "toolkit.telemetry.reportingpolicy.firstRun": false,
};

function supportProblem({ platform, env, x11Switch }) {
  if (platform !== "linux") {
    return platform === "darwin"
      ? "macOS doesn't let one program place another program's window inside its own, so Gecko can't run inside Raticode here."
      : "Gecko inside Raticode hasn't been built for Windows yet.";
  }
  if (!env.DISPLAY) return "Gecko needs an X11 display, and this desktop session has none. Turn on XWayland in your desktop's settings.";
  return x11Switch || !waylandDesktop(env) ? "" : "restart";
}

function pageAddress(value) {
  try {
    const url = new URL(String(value || "").trim());
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

// Holes for menus drawn over the Gecko tab, in window pixels: at most 16
// rectangles, each kept inside the window.
function cutoutsWithin(value, width, height) {
  if (!Array.isArray(value)) return [];
  const whole = (number) => (Number.isFinite(number) ? Math.round(number) : 0);
  const rects = [];
  for (const item of value.slice(0, 16)) {
    const x = Math.min(Math.max(0, whole(item?.x)), width);
    const y = Math.min(Math.max(0, whole(item?.y)), height);
    const right = Math.min(Math.max(0, whole(item?.x) + whole(item?.width)), width);
    const bottom = Math.min(Math.max(0, whole(item?.y) + whole(item?.height)), height);
    if (right > x && bottom > y) rects.push({ x, y, width: right - x, height: bottom - y });
  }
  return rects;
}

function createGeckoWindow({
  dataDir,
  getWindow,
  log = () => {},
  ask = async () => true,
  onState = () => {},
  relaunch = async () => ({}),
  spawn = spawnProcess,
  createClient = () => new X11Client(),
  findExecutable = findFirefox,
  findProfiles = findFirefoxProfiles,
  createMediaWatch = watchGeckoMedia,
  env = process.env,
  platform = process.platform,
  x11Switch = false,
  windowTimeoutMs = WINDOW_TIMEOUT_MS,
  stopTimeoutMs = STOP_TIMEOUT_MS,
}) {
  const events = new EventEmitter();
  const root = path.join(dataDir, "gecko");
  const registry = path.join(root, "profiles.json");
  let client = null;
  let connecting = null;
  let child = null;
  let starting = null;
  let adopted = 0;
  let mapped = false;
  let pageFocused = false;
  let title = "";
  let lastError = "";
  let placement = { x: 0, y: 0, width: 0, height: 0, visible: false, cutouts: [] };
  let shapeMajor = 0;
  let shaped = false;
  let pageClicks = 0;
  let watchedWindow = null;
  let resolveWindow = null;
  const considering = new Set();
  // For the media bar; reads only what this Firefox publishes to the desktop.
  const media = createMediaWatch({ pid: () => child?.pid, title: () => title, env, log, onChange: () => changed() });

  // Profiles --------------------------------------------------------------------------

  function profiles() {
    let saved = null;
    try { saved = JSON.parse(fs.readFileSync(registry, "utf8")); } catch { /* First use. */ }
    const list = Array.isArray(saved?.list) ? saved.list.filter((item) => /^[a-z0-9-]{1,40}$/.test(item?.id)) : [];
    if (!list.some((item) => item.id === "default")) list.unshift({ id: "default", name: "Default" });
    return { current: list.some((item) => item.id === saved?.current) ? saved.current : "default", list };
  }

  function saveProfiles(next) {
    fs.mkdirSync(root, { recursive: true, mode: 0o700 });
    fs.writeFileSync(registry, JSON.stringify(next), { mode: 0o600 });
  }

  function profileDir(id = profiles().current) {
    return path.join(root, "profiles", id);
  }

  function writeProfile(directory) {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    try { fs.chmodSync(directory, 0o700); } catch { /* Not supported on every file system. */ }
    fs.writeFileSync(path.join(directory, "user.js"), [
      "// Written by Raticode each time Gecko starts. Everything else is yours to change in Gecko's settings.",
      ...Object.entries(PROFILE_PREFS).map(([name, value]) => prefLine(name, value)),
      "",
    ].join("\n"), { mode: 0o600 });
  }

  function createProfile(name) {
    const label = String(name || "").trim().slice(0, 40);
    if (!label) throw new Error("Give the profile a name.");
    const state = profiles();
    const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32) || "profile";
    let id = base;
    for (let number = 2; state.list.some((item) => item.id === id); number += 1) id = `${base}-${number}`;
    saveProfiles({ current: state.current, list: [...state.list, { id, name: label }] });
    return status();
  }

  async function chooseProfile(id) {
    const state = profiles();
    if (!state.list.some((item) => item.id === id)) throw new Error("That profile doesn't exist.");
    if (id === state.current) return status();
    await stop();
    saveProfiles({ ...state, current: id });
    return status();
  }

  // Display ---------------------------------------------------------------------------

  function hostWindow() {
    const window = getWindow();
    if (!window || window.isDestroyed()) throw new Error("Raticode's window isn't open.");
    return window;
  }

  function hostId() {
    return hostWindow().getNativeWindowHandle().readUInt32LE(0);
  }

  function connect() {
    if (client) return Promise.resolve(client);
    connecting ||= (async () => {
      const next = createClient();
      await next.connect();
      next.on("event", (event) => handleEvent(event));
      next.on("closed", () => { if (client === next) { client = null; adopted = 0; } });
      next.selectInput(next.root, MASK.SubstructureNotify);
      shapeMajor = await next.extension("SHAPE").catch(() => 0);
      client = next;
      return next;
    })().finally(() => { connecting = null; });
    return connecting;
  }

  function handleEvent({ type, data }) {
    switch (type) {
      case EVENT.CreateNotify: {
        const [parent, window] = [data.readUInt32LE(4), data.readUInt32LE(8)];
        if (parent !== client.root || data[22] || adopted) return;
        client.selectInput(window, MASK.PropertyChange | MASK.StructureNotify);
        void consider(window);
        return;
      }
      case EVENT.PropertyNotify: {
        const window = data.readUInt32LE(4);
        if (window === adopted) void refreshTitle();
        else if (!adopted) void consider(window);
        return;
      }
      case EVENT.MapNotify:
        if (data.readUInt32LE(8) !== adopted) return;
        mapped = true;
        apply();
        return;
      case EVENT.UnmapNotify:
        if (data.readUInt32LE(8) === adopted) mapped = false;
        return;
      case EVENT.DestroyNotify:
        if (data.readUInt32LE(8) !== adopted) return;
        adopted = 0;
        mapped = false;
        changed();
        return;
      case EVENT.ButtonPress:
        if (adopted) {
          client.setInputFocus(adopted);
          pageFocused = true;
        }
        client.allowClick();
        // Raticode's page never sees this click, so a menu open over the
        // Gecko tab hears about it this way and can close.
        pageClicks += 1;
        changed();
        return;
      default:
    }
  }

  // Firefox makes several windows while starting. The browser window is the one
  // it gives the role "browser"; it does that before showing it, so the window
  // moves into Raticode without ever appearing on the desktop.
  async function consider(window) {
    if (adopted || considering.has(window)) return;
    considering.add(window);
    try {
      const [windowClass, role] = await Promise.all([client.text(window, "WM_CLASS"), client.text(window, "WM_WINDOW_ROLE")]);
      if (!adopted && child && windowClass.split("\0").includes(WINDOW_CLASS) && role === "browser") adopt(window);
    } catch {
      // The window closed while Raticode was looking at it.
    } finally {
      considering.delete(window);
    }
  }

  function adopt(window) {
    adopted = window;
    client.selectInput(window, MASK.PropertyChange | MASK.StructureNotify);
    client.reparentWindow(window, hostId(), placement.x, placement.y);
    client.grabClicks(window);
    watchHost();
    apply();
    void refreshTitle();
    resolveWindow?.();
    resolveWindow = null;
    changed();
  }

  async function refreshTitle() {
    if (!adopted) return;
    try {
      const next = (await client.text(adopted, "_NET_WM_NAME")).replace(/\s+[—-]\s+(Mozilla Firefox|Firefox).*$/, "");
      if (next !== title) { title = next; changed(); }
    } catch { /* The window has just closed. */ }
  }

  function watchHost() {
    const window = hostWindow();
    if (watchedWindow === window) return;
    watchedWindow = window;
    window.on("move", () => tellPosition());
    // Keyboard focus goes back to the page when Raticode is switched back to.
    window.on("focus", () => { if (adopted && pageFocused && placement.visible) client?.setInputFocus(adopted); });
  }

  function apply() {
    if (!adopted || !client) return;
    // Without the SHAPE extension a menu over the page can only be shown by
    // hiding the page.
    const hidden = !placement.visible || placement.width < 1 || placement.height < 1 || (placement.cutouts.length > 0 && !shapeMajor);
    if (hidden) {
      if (mapped) client.unmapWindow(adopted);
      return;
    }
    client.configureWindow(adopted, { x: placement.x, y: placement.y, width: placement.width, height: placement.height });
    if (placement.cutouts.length) {
      client.shapeWindow(shapeMajor, adopted, placement.width, placement.height, placement.cutouts);
      shaped = true;
    } else if (shaped) {
      client.unshapeWindow(shapeMajor, adopted);
      shaped = false;
    }
    if (!mapped) client.mapWindow(adopted);
    tellPosition();
  }

  async function tellPosition() {
    if (!adopted || !client || !placement.visible) return;
    try {
      const origin = await client.translate(hostId(), client.root);
      client.sendConfigureNotify(adopted, { x: origin.x + placement.x, y: origin.y + placement.y, width: placement.width, height: placement.height });
    } catch { /* Raticode's window or the page's has just closed. */ }
  }

  function place(next = {}) {
    const whole = (value) => (Number.isFinite(value) ? Math.round(value) : 0);
    const width = Math.max(0, whole(next.width));
    const height = Math.max(0, whole(next.height));
    placement = { x: whole(next.x), y: whole(next.y), width, height, visible: next.visible === true, cutouts: cutoutsWithin(next.cutouts, width, height) };
    apply();
    return { placed: Boolean(adopted) };
  }

  function focusApp() {
    if (!pageFocused || !client) return { focused: false };
    pageFocused = false;
    client.setInputFocus(hostId());
    return { focused: true };
  }

  // Firefox ---------------------------------------------------------------------------

  function launchEnv() {
    const next = { ...env, GDK_BACKEND: "x11", MOZ_ENABLE_WAYLAND: "0", MOZ_CRASHREPORTER_DISABLE: "1" };
    delete next.MOZ_HEADLESS;
    delete next.MOZ_NO_REMOTE;
    delete next.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS;
    return next;
  }

  function status() {
    const executable = findExecutable({ platform, env });
    const problem = supportProblem({ platform, env, x11Switch });
    const state = profiles();
    return {
      available: Boolean(executable) && !problem,
      supported: problem !== "restart" && !problem,
      needsRestart: problem === "restart",
      problem: problem === "restart" ? "" : problem,
      executable,
      running: Boolean(child),
      placed: Boolean(adopted),
      title,
      pageClicks,
      error: lastError,
      media: media.snapshot(),
      profile: state.current,
      profiles: state.list,
    };
  }

  function changed() {
    try { onState(status()); } catch { /* Raticode's window is closing. */ }
  }

  async function start(url) {
    const current = status();
    if (current.needsRestart) throw new Error("Restart Raticode to use Gecko.");
    if (current.problem) throw new Error(current.problem);
    if (!current.executable) throw new Error("Firefox was not found. Install Firefox to use Gecko.");
    await connect();
    const directory = profileDir();
    writeProfile(directory);
    const windowReady = new Promise((resolve) => { resolveWindow = resolve; });
    lastError = "";
    title = "";
    const process_ = spawn(current.executable, ["--class", WINDOW_CLASS, "--new-instance", "--profile", directory, ...(url ? [url] : [])], { env: launchEnv(), stdio: "ignore" });
    child = process_;
    media.start();
    let failed;
    const ended = new Promise((resolve) => { failed = resolve; });
    process_.once("error", (error) => { lastError = `Firefox could not start: ${error.message}`; if (child === process_) { child = null; media.stop(); } failed(); changed(); });
    process_.once("exit", () => {
      if (child === process_) { child = null; adopted = 0; mapped = false; title = ""; media.stop(); }
      failed();
      events.emit("exit");
      changed();
    });
    if (process_.pid) {
      const watchdog = spawn("/bin/sh", ["-c", POSIX_WATCHDOG, "raticode-gecko-watchdog", String(global.process.pid), String(process_.pid)], { stdio: "ignore", detached: true });
      watchdog.on?.("error", () => {});
      watchdog.unref?.();
    }
    changed();
    let timer;
    const timeout = new Promise((resolve) => { timer = setTimeout(resolve, windowTimeoutMs, "timeout"); });
    const outcome = await Promise.race([windowReady.then(() => "window"), ended.then(() => "ended"), timeout]);
    clearTimeout(timer);
    resolveWindow = null;
    if (outcome === "window") return;
    if (outcome === "timeout") {
      process_.kill("SIGTERM");
      lastError = "Firefox started, but its window never appeared.";
    } else {
      lastError ||= "Firefox closed before its window appeared. Another copy may be using this profile.";
    }
    changed();
    throw new Error(lastError);
  }

  function ensure(url) {
    if (adopted && child) return Promise.resolve({ started: false });
    starting ||= start(url).then(() => ({ started: true })).finally(() => { starting = null; });
    return starting;
  }

  async function open(value) {
    const url = pageAddress(value);
    const { started } = await ensure(url);
    // A running Firefox takes the address through its own command line.
    if (url && !started) {
      const helper = spawn(status().executable, ["--class", WINDOW_CLASS, "--profile", profileDir(), "--new-tab", url], { env: launchEnv(), stdio: "ignore", detached: true });
      helper.on?.("error", (error) => log("warn", `Gecko could not open the address: ${error.message}`));
      helper.unref?.();
    }
    return status();
  }

  // An empty Firefox tab, for the tab bar's +. A Firefox that isn't running
  // starts with its own start page instead. The address is fixed: nothing from
  // the page or the renderer reaches Firefox's command line here.
  async function newTab() {
    const { started } = await ensure("");
    if (!started) {
      const helper = spawn(status().executable, ["--class", WINDOW_CLASS, "--profile", profileDir(), "--new-tab", NEW_TAB_PAGE], { env: launchEnv(), stdio: "ignore", detached: true });
      helper.on?.("error", (error) => log("warn", `Gecko could not open a new tab: ${error.message}`));
      helper.unref?.();
    }
    return { ...status(), newTab: true };
  }

  async function stop() {
    const running = child;
    if (!running) return;
    const exited = new Promise((resolve) => running.once("exit", resolve));
    running.kill("SIGTERM");
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(resolve, stopTimeoutMs, "late"); });
    if (await Promise.race([exited, late]) === "late") running.kill("SIGKILL");
    clearTimeout(timer);
  }

  // Importing from the user's everyday Firefox ------------------------------------------

  function importSources() {
    return { sources: findProfiles({ platform, env }), items: Object.fromEntries(Object.entries(IMPORT_ITEMS).map(([key, item]) => [key, item.label])) };
  }

  // Only a profile Firefox itself lists can be named, so a page can't point this at a folder of its choosing.
  function knownSource(value) {
    const source = findProfiles({ platform, env }).find((item) => item.path === value);
    if (!source) throw new Error("That Firefox profile wasn't found.");
    return source;
  }

  async function runImport(options = {}) {
    const source = knownSource(options.path);
    const items = Object.fromEntries(Object.keys(IMPORT_ITEMS).map((key) => [key, options.items?.[key] === true]));
    const labels = Object.keys(items).filter((key) => items[key]).map((key) => IMPORT_ITEMS[key].label);
    if (!labels.length) throw new Error("Choose something to import.");
    const confirmed = await ask({
      message: `Import from the Firefox profile “${source.name}” into Gecko?`,
      detail: `${labels.join(", ")}.\n\nYour everyday Firefox is only read. Gecko's own copies of these move to a backup folder first, and Gecko restarts.`,
    });
    if (!confirmed) return { cancelled: true, ...status() };
    await stop();
    const target = profileDir();
    fs.mkdirSync(target, { recursive: true, mode: 0o700 });
    const extensionIds = Array.isArray(options.extensionIds) ? options.extensionIds.filter((id) => typeof id === "string") : [];
    const result = importProfile({ source: source.path, target, items, extensionIds, backupDir: path.join(root, "import-backups", String(Date.now())) });
    // Firefox checks each package's signature when it loads it.
    const folder = path.join(target, "extensions");
    if (result.extensions.length) fs.mkdirSync(folder, { recursive: true, mode: 0o700 });
    for (const extension of result.extensions) fs.copyFileSync(extension.file, path.join(folder, `${path.basename(extension.file)}`));
    log("info", `Imported ${labels.join(", ")} into Gecko.`);
    return { imported: { copied: result.copied, skipped: result.skipped, extensions: result.extensions.map(({ id, name }) => ({ id, name })) }, ...status() };
  }

  async function handle(action, options = {}) {
    switch (action) {
      case "status": return status();
      case "open": return open(options.url);
      case "new-tab": return newTab();
      case "place": return place(options);
      case "focus-app": return focusApp();
      case "media-control": return media.control(options.command);
      case "stop": await stop(); return status();
      case "select": setWantsX11(dataDir, options.selected === true); return status();
      case "restart-app": setWantsX11(dataDir, true); return relaunch();
      case "profile-create": return createProfile(options.name);
      case "profile-choose": return chooseProfile(options.id);
      case "import-sources": return importSources();
      case "import-inspect": return inspectProfile(knownSource(options.path).path);
      case "import": return runImport(options);
      default: throw new Error(`Unknown Gecko action: ${action}`);
    }
  }

  function close() {
    media.stop();
    child?.kill("SIGTERM");
    client?.close();
    client = null;
  }

  return { close, events, handle, status, get adoptedWindow() { return adopted; } };
}

module.exports = { NEW_TAB_PAGE, PROFILE_PREFS, WINDOW_CLASS, createGeckoWindow, cutoutsWithin, pageAddress, supportProblem };
