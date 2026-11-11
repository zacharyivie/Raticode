// Runs one headless Firefox for the integrated browser's Firefox mode and
// drives it over WebDriver BiDi. Firefox uses a profile that Raticode creates
// and owns; the user's own Firefox profiles are never opened or changed.
//
// Firefox only offers remote control through an unauthenticated WebSocket on
// loopback. It refuses web pages (Origin and Host checks) but not other local
// programs. Raticode narrows that exposure as far as Firefox allows: a random
// port, a session claimed immediately after launch (Firefox then refuses any
// second client), a private profile folder, and Firefox stops when idle or
// when Raticode quits.
const { spawn: spawnProcess } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { cookieMatchesSite } = require("./browser-policy.cjs");
const { prefLine, removeAutomationContainers } = require("./firefox-profile.cjs");

const LAUNCH_TIMEOUT_MS = 45000;
const COMMAND_TIMEOUT_MS = 60000;
const IDLE_SHUTDOWN_MS = 5 * 60 * 1000;
const STOP_TIMEOUT_MS = 10000;
const LISTENING = /WebDriver BiDi listening on (ws:\/\/127\.0\.0\.1:\d+)/;
// Ends Firefox when Raticode's process goes away without quitting cleanly;
// Firefox would otherwise keep running with its control port open and
// unclaimed. On Linux and macOS a shell checks Raticode's process every two
// seconds. On Windows a Node script notices its stdin pipe from Raticode close.
const POSIX_WATCHDOG = "while kill -0 \"$1\" 2>/dev/null && kill -0 \"$2\" 2>/dev/null; do sleep 2; done; kill -9 \"$2\" 2>/dev/null";
const WATCHDOG = [
  "const pid = Number(process.argv[1]);",
  "const stop = () => { try { process.kill(pid, 'SIGKILL'); } catch {} process.exit(0); };",
  "process.stdin.on('end', stop); process.stdin.on('error', stop); process.stdin.resume();",
  "setInterval(() => { try { process.kill(pid, 0); } catch { process.exit(0); } }, 5000);",
].join(" ");

// Written to user.js on every launch. Firefox runs without a window, so any
// prompt that would appear in its own interface is turned off: such a prompt
// could never be answered and would leave the page waiting.
const PROFILE_PREFS = {
  "app.update.auto": false,
  "app.update.disabledForTesting": true,
  "browser.aboutwelcome.enabled": false,
  "browser.download.always_ask_before_handling_new_types": false,
  "browser.download.panel.shown": true,
  "browser.newtabpage.enabled": false,
  "browser.sessionstore.resume_from_crash": false,
  "browser.shell.checkDefaultBrowser": false,
  "browser.startup.homepage": "about:blank",
  "browser.startup.homepage_override.mstone": "ignore",
  "browser.startup.page": 0,
  "browser.tabs.warnOnClose": false,
  "browser.translations.automaticallyPopup": false,
  "datareporting.healthreport.uploadEnabled": false,
  "datareporting.policy.dataSubmissionEnabled": false,
  // Lets Firefox attach the files the user or Rem picks to a file input.
  "dom.file.createInChild": true,
  // Every tab is its own window and most are not the active one; keep them
  // running at full speed so the stream and Rem's actions stay responsive.
  "dom.ipc.processPriorityManager.enabled": false,
  "threads.lower_mainthread_priority_in_background.enabled": false,
  "browser.tabs.unloadOnLowMemory": false,
  "browser.pagethumbnails.capturing_disabled": true,
  "extensions.formautofill.addresses.enabled": false,
  "extensions.formautofill.creditCards.enabled": false,
  "network.captive-portal-service.enabled": false,
  "extensions.update.enabled": false,
  "permissions.default.camera": 2,
  "permissions.default.desktop-notification": 2,
  "permissions.default.geo": 2,
  "permissions.default.microphone": 2,
  "permissions.default.xr": 2,
  // Keep Firefox's normal protections (Safe Browsing, tracking protection,
  // the pop-up blocker, strict file:// origins) instead of the test defaults
  // the remote agent would apply. The few it needs are set above.
  "remote.prefs.recommended": false,
  "signon.rememberSignons": false,
  // Firefox fills saved logins in every container, Rem's included.
  "signon.autofillForms": false,
  // Only extensions installed through Raticode (profile) or shipped with
  // Firefox (application) load; add-ons other programs drop into the home
  // folder or system folders do not.
  "extensions.enabledScopes": 5,
  "extensions.installDistroAddons": false,
  // Look for extensions in the profile at every launch, so one installed
  // just before Firefox was stopped or crashed still loads.
  "extensions.startupScanScopes": 1,
  "startup.homepage_welcome_url": "",
  "toolkit.telemetry.enabled": false,
  "toolkit.telemetry.reportingpolicy.firstRun": false,
};

const SUBSCRIBED_EVENTS = [
  "browsingContext.contextCreated",
  "browsingContext.contextDestroyed",
  "browsingContext.navigationStarted",
  "browsingContext.navigationCommitted",
  "browsingContext.fragmentNavigated",
  "browsingContext.historyUpdated",
  "browsingContext.domContentLoaded",
  "browsingContext.load",
  "browsingContext.navigationFailed",
  "browsingContext.userPromptOpened",
  "browsingContext.userPromptClosed",
  "browsingContext.downloadWillBegin",
  "browsingContext.downloadEnd",
  "input.fileDialogOpened",
];

function firefoxCandidates({ platform = process.platform, env = process.env, home = os.homedir() } = {}) {
  const explicit = env.RATICODE_FIREFOX_PATH ? [env.RATICODE_FIREFOX_PATH] : [];
  if (platform === "darwin") {
    return [...explicit, "/Applications/Firefox.app/Contents/MacOS/firefox", path.join(home, "Applications/Firefox.app/Contents/MacOS/firefox")];
  }
  if (platform === "win32") {
    const roots = [env.ProgramFiles, env["ProgramFiles(x86)"], env.LOCALAPPDATA].filter(Boolean);
    return [...explicit, ...roots.map((root) => path.join(root, "Mozilla Firefox", "firefox.exe"))];
  }
  const fromPath = String(env.PATH || "").split(path.delimiter).filter(Boolean).map((dir) => path.join(dir, "firefox"));
  return [...explicit, ...fromPath, "/usr/bin/firefox", "/usr/lib/firefox/firefox", "/opt/firefox/firefox", "/snap/bin/firefox"];
}

function findFirefox(options = {}) {
  const exists = options.exists || ((candidate) => {
    try { return fs.statSync(candidate).isFile(); } catch { return false; }
  });
  for (const candidate of firefoxCandidates(options)) {
    if (candidate && exists(candidate)) return candidate;
  }
  return "";
}

// Snap's Firefox cannot read hidden folders in the home directory, where
// Raticode keeps its data, so its profile lives in the snap's own folder.
function profileDirFor(executable, dataDir, home = os.homedir()) {
  let resolved = executable;
  try { resolved = fs.realpathSync(executable); } catch { /* Use the path as given. */ }
  if (/^\/snap\//.test(executable) || /^\/snap\//.test(resolved)) {
    return path.join(home, "snap", "firefox", "common", "raticode-profile");
  }
  return path.join(dataDir, "firefox-profile");
}

// Settings the user chose in Raticode come first; Raticode's required ones
// follow, and the later line wins when Firefox reads user.js at launch.
function writeProfile(profileDir, userPrefs = {}) {
  fs.mkdirSync(profileDir, { recursive: true, mode: 0o700 });
  // An existing folder keeps its mode; the profile holds cookies and history.
  try { fs.chmodSync(profileDir, 0o700); } catch { /* Not supported on every file system. */ }
  const chosen = Object.entries(userPrefs).filter(([name]) => !Object.hasOwn(PROFILE_PREFS, name)).map(([name, value]) => prefLine(name, value));
  const required = Object.entries(PROFILE_PREFS).map(([name, value]) => prefLine(name, value));
  fs.writeFileSync(path.join(profileDir, "user.js"), [
    "// Written by Raticode on every Firefox launch. Edit Firefox settings in Raticode instead.",
    "// Settings chosen in Raticode > Settings > Browser > Firefox:",
    ...chosen,
    "// Required while Firefox runs without a window:",
    ...required,
    "",
  ].join("\n"), { mode: 0o600 });
  // Containers Firefox's automation made in earlier runs.
  removeAutomationContainers(profileDir);
  // A file left by an earlier run would point at a port that is now closed.
  fs.rmSync(path.join(profileDir, "WebDriverBiDiServer.json"), { force: true });
}

function launchEnvironment(env, platform) {
  const next = { ...env, MOZ_HEADLESS: "1", MOZ_CRASHREPORTER_DISABLE: "1", MOZ_NO_REMOTE: "1" };
  // Headless Firefox needs no display. Without one it cannot show anything
  // on the desktop or take keyboard focus, even by accident.
  if (platform === "linux") {
    delete next.DISPLAY;
    delete next.WAYLAND_DISPLAY;
  }
  delete next.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS;
  return next;
}

// A normal Firefox window on Raticode's profile, for signing in to sites that
// refuse a browser under remote control. Firefox tells every page it is
// automated (navigator.webdriver) whenever its control port is open, and
// Google's sign-in then answers "This browser or app may not be secure".
// This window has no control port, and it uses the user's display.
function signInEnvironment(env) {
  const next = { ...env, MOZ_CRASHREPORTER_DISABLE: "1", MOZ_NO_REMOTE: "1" };
  delete next.MOZ_HEADLESS;
  delete next.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS;
  delete next.MOZ_MARIONETTE;
  return next;
}

// One WebSocket to Firefox's remote agent. Commands resolve with their result
// and reject with Firefox's error message; events go to `onEvent`.
function createBidiConnection(url, { WebSocketImpl = globalThis.WebSocket, onEvent = () => {}, onClose = () => {} } = {}) {
  if (typeof WebSocketImpl !== "function") throw new Error("This version of Raticode cannot connect to Firefox (no WebSocket support).");
  const socket = new WebSocketImpl(`${url}/session`);
  const pending = new Map();
  let nextId = 0;
  let closed = false;
  const opened = new Promise((resolve, reject) => {
    socket.onopen = () => resolve();
    socket.onerror = (event) => reject(new Error(`Could not connect to Firefox: ${event?.message || "connection failed"}`));
  });
  socket.onmessage = (message) => {
    let payload;
    try { payload = JSON.parse(typeof message.data === "string" ? message.data : Buffer.from(message.data).toString("utf8")); }
    catch { return; }
    if (payload.type === "event") {
      onEvent(payload.method, payload.params || {});
      return;
    }
    const entry = pending.get(payload.id);
    if (!entry) return;
    pending.delete(payload.id);
    clearTimeout(entry.timer);
    if (payload.type === "error") entry.reject(Object.assign(new Error(payload.message || payload.error || "Firefox reported an error."), { code: payload.error }));
    else entry.resolve(payload.result || {});
  };
  socket.onclose = () => {
    closed = true;
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(new Error("Firefox closed the connection."));
    }
    pending.clear();
    onClose();
  };
  function send(method, params = {}, { timeout = COMMAND_TIMEOUT_MS } = {}) {
    if (closed) return Promise.reject(new Error("Firefox is not running."));
    nextId += 1;
    const id = nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Firefox did not answer ${method} in time.`));
      }, timeout);
      timer.unref?.();
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  return {
    opened,
    send,
    get closed() { return closed; },
    close: () => { try { socket.close(); } catch { /* Already closed. */ } },
  };
}

function createFirefoxEngine({
  dataDir,
  downloadsDir = path.join(os.homedir(), "Downloads"),
  stagingDir = path.join(dataDir, "firefox-downloads"),
  log = () => {},
  platform = process.platform,
  env = process.env,
  spawn = spawnProcess,
  find = () => findFirefox({ platform, env }),
  WebSocketImpl = globalThis.WebSocket,
  idleShutdownMs = IDLE_SHUTDOWN_MS,
  watchdog = true,
  settingsFile = path.join(dataDir, "firefox-settings.json"),
} = {}) {
  const events = new EventEmitter();
  const contexts = new Map();
  let child = null;
  let guard = null;
  let signInWindow = null;
  let connection = null;
  let starting = null;
  let remUserContext = "";
  let idleTimer = null;
  let generation = 0;
  let remDownloadsAllowed = false;
  let browserVersion = "";
  let stopping = false;

  function status() {
    const executable = find();
    return {
      available: Boolean(executable),
      executable,
      running: Boolean(connection && !connection.closed),
      snap: /^\/snap\//.test(executable),
      version: browserVersion,
      profile: executable ? profileDirFor(executable, dataDir) : path.join(dataDir, "firefox-profile"),
    };
  }

  function profileDir() {
    return status().profile;
  }

  // ---------------------------------------------------------------------------
  // Settings the user chose in Raticode, applied at the next launch

  function readUserPrefs() {
    try {
      const value = JSON.parse(fs.readFileSync(settingsFile, "utf8"));
      return value && typeof value.prefs === "object" && !Array.isArray(value.prefs) ? sanitizeUserPrefs(value.prefs).prefs : {};
    } catch {
      return {};
    }
  }

  function saveUserPrefs(prefs) {
    const { prefs: clean, rejected } = sanitizeUserPrefs(prefs);
    fs.mkdirSync(path.dirname(settingsFile), { recursive: true, mode: 0o700 });
    fs.writeFileSync(settingsFile, JSON.stringify({ prefs: clean }, null, 2), { mode: 0o600 });
    return { prefs: clean, rejected, restartNeeded: Boolean(connection && !connection.closed) };
  }

  // ---------------------------------------------------------------------------
  // Extensions, installed and removed through Firefox itself

  async function listExtensions() {
    await ensure();
    const { extensions = [] } = await send("webExtension.moz:listExtensions", {});
    return extensions.filter((item) => !item.isSystem && !item.hidden).map((item) => ({
      id: String(item.id),
      name: String(item.name || item.id),
      version: String(item.version || ""),
      active: item.isActive === true,
      temporary: item.temporarilyInstalled === true,
    })).sort((a, b) => a.name.localeCompare(b.name));
  }

  // Firefox refuses packages Mozilla has not signed.
  async function installExtension(file) {
    await ensure();
    const result = await send("webExtension.install", { extensionData: { type: "archivePath", path: file }, "moz:permanent": true }, { timeout: 120000 });
    return String(result.extension || "");
  }

  async function uninstallExtension(id) {
    await ensure();
    await send("webExtension.uninstall", { extension: String(id) });
  }

  // Stops the Firefox Raticode controls and opens `url` in a normal Firefox
  // window on the same profile, without a control port. Raticode's Firefox
  // starts again after the user closes the window.
  async function openSignInWindow(url) {
    // A web page, or Firefox's own settings and add-ons pages, which a normal
    // window can show even though the streamed tabs can't.
    let address;
    try { address = new URL(String(url)); } catch { throw new Error("Choose a web address to sign in at."); }
    const firefoxPage = /^about:(?:preferences|addons)(?:#[a-z-]*)?$/.test(address.toString());
    if (!["http:", "https:"].includes(address.protocol) && !firefoxPage) throw new Error("Choose a web address to sign in at.");
    if (signInWindow) return { alreadyOpen: true };
    const executable = find();
    if (!executable) throw new Error("Firefox is not installed.");
    if (starting) await starting.catch(() => {});
    await stopAndWait();
    const profileDir = profileDirFor(executable, dataDir);
    writeProfile(profileDir, readUserPrefs());
    const processHandle = spawn(executable, ["--no-remote", "--profile", profileDir, address.toString()], {
      env: signInEnvironment(env),
      stdio: "ignore",
      windowsHide: false,
    });
    signInWindow = processHandle;
    const finished = () => {
      if (signInWindow !== processHandle) return;
      signInWindow = null;
      log("info", "The Firefox sign-in window closed.");
      events.emit("sign-in-window-closed");
    };
    processHandle.once("exit", finished);
    processHandle.once("error", (error) => {
      log("warn", `Could not open a Firefox window: ${error.message}`);
      finished();
    });
    log("info", "Opened a Firefox window without remote control for signing in.");
    return { opened: true };
  }

  // Resolves once Firefox has exited.
  function stopAndWait(timeout = STOP_TIMEOUT_MS + 2000) {
    const processHandle = child;
    stop();
    if (!processHandle || processHandle.exitCode !== null) return Promise.resolve();
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, timeout);
      timer.unref?.();
      processHandle.once("exit", () => { clearTimeout(timer); resolve(); });
    });
  }

  async function ensure() {
    if (connection && !connection.closed) return connection;
    // Both would use the same profile, and Firefox allows one at a time.
    if (signInWindow) throw new Error("Firefox is open in a window for signing in. Close that window to use Firefox in Raticode again.");
    if (!starting) starting = start().finally(() => { starting = null; });
    return starting;
  }

  async function start() {
    const executable = find();
    if (!executable) {
      throw new Error("Firefox is not installed. Install Firefox, or choose Raticode as the web browser in Settings > Browser.");
    }
    const profileDir = profileDirFor(executable, dataDir);
    writeProfile(profileDir, readUserPrefs());
    stopping = false;
    const args = ["--headless", "--no-remote", "--profile", profileDir, "--remote-debugging-port", "0", "about:blank"];
    const processHandle = spawn(executable, args, {
      env: launchEnvironment(env, platform),
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    child = processHandle;
    if (watchdog && processHandle.pid) guard = startWatchdog(processHandle.pid);
    const thisGeneration = ++generation;
    let stderr = "";
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Firefox did not start in time.")), LAUNCH_TIMEOUT_MS);
      timer.unref?.();
      processHandle.once("error", (error) => {
        clearTimeout(timer);
        reject(new Error(`Could not start Firefox: ${error.message}`));
      });
      processHandle.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Firefox exited before it was ready (${code ?? "signal"}). ${tail(stderr)}`.trim()));
      });
      processHandle.stderr.on("data", (chunk) => {
        stderr = (stderr + chunk).slice(-8000);
        const match = LISTENING.exec(stderr);
        if (match) {
          clearTimeout(timer);
          resolve(match[1]);
        }
      });
    }).catch((error) => {
      try { processHandle.kill("SIGKILL"); } catch { /* Already gone. */ }
      child = null;
      throw error;
    });
    processHandle.once("exit", (code, signal) => {
      if (generation !== thisGeneration) return;
      child = null;
      connection = null;
      stopWatchdog();
      remUserContext = "";
      if (!stopping) log("warn", `Firefox stopped unexpectedly (${code ?? signal ?? "unknown"}).`);
      events.emit("exit", { expected: stopping });
    });
    const next = createBidiConnection(url, {
      WebSocketImpl,
      onEvent: (method, params) => route(method, params),
      onClose: () => {
        // A deliberate stop lets Firefox finish shutting down, which is when
        // it saves its list of installed extensions.
        if (generation !== thisGeneration || stopping) return;
        if (child) { try { child.kill(); } catch { /* Already gone. */ } }
      },
    });
    try {
      await next.opened;
      // Claim Firefox's only session at once: it then refuses other clients.
      const created = await next.send("session.new", {
        capabilities: { alwaysMatch: { acceptInsecureCerts: false, unhandledPromptBehavior: { default: "ignore", file: "ignore" } } },
      });
      browserVersion = String(created?.capabilities?.browserVersion || "");
      await next.send("session.subscribe", { events: SUBSCRIBED_EVENTS });
      remUserContext = (await next.send("browser.createUserContext", {})).userContext;
      // Rem's container must start signed out. A container number used
      // before, for example by imported data, could otherwise come with
      // cookies; the sites the user shares are copied in afterwards.
      await next.send("storage.deleteCookies", { partition: { type: "storageKey", userContext: remUserContext } });
      fs.mkdirSync(stagingDir, { recursive: true, mode: 0o700 });
      await next.send("browser.setDownloadBehavior", {
        downloadBehavior: { type: "allowed", destinationFolder: stagingDir },
        userContexts: ["default"],
      });
      connection = next;
      await applyRemDownloads();
    } catch (error) {
      next.close();
      stop();
      throw error;
    }
    log("info", `Started Firefox for the integrated browser (${path.basename(executable)}).`);
    scheduleIdleCheck();
    return connection;
  }

  function startWatchdog(pid) {
    try {
      const watcher = platform === "win32"
        ? spawn(process.execPath, ["-e", WATCHDOG, String(pid)], {
          env: { ...env, ELECTRON_RUN_AS_NODE: "1" },
          stdio: ["pipe", "ignore", "ignore"],
          windowsHide: true,
        })
        : spawn("/bin/sh", ["-c", POSIX_WATCHDOG, "raticode-firefox-watchdog", String(process.pid), String(pid)], {
          stdio: ["pipe", "ignore", "ignore"],
        });
      watcher.on("error", (error) => log("warn", `Firefox watchdog failed: ${error.message}`));
      watcher.stdin.on("error", () => {});
      watcher.unref?.();
      return watcher;
    } catch (error) {
      log("warn", `Firefox watchdog failed: ${error.message}`);
      return null;
    }
  }

  function stopWatchdog() {
    const watcher = guard;
    guard = null;
    if (watcher) { try { watcher.kill(); } catch { /* Already gone. */ } }
  }

  function route(method, params) {
    if (method === "browsingContext.contextCreated" && params.originalOpener && contexts.has(params.originalOpener)) {
      contexts.get(params.originalOpener).handleEvent("popup", params);
      return;
    }
    const target = contexts.get(params.context);
    if (target) target.handleEvent(method, params);
    if (method.startsWith("browsingContext.download")) events.emit(method, params);
  }

  function send(method, params, options) {
    if (!connection || connection.closed) return Promise.reject(new Error("Firefox is not running."));
    return connection.send(method, params, options);
  }

  async function createContext(kind) {
    await ensure();
    const userContext = kind === "rem" ? remUserContext : "default";
    const created = await send("browsingContext.create", { type: "window", background: true, userContext });
    return { context: created.context, generation };
  }

  function register(context, target) {
    contexts.set(context, target);
    clearIdleTimer();
  }

  function unregister(context) {
    contexts.delete(context);
    scheduleIdleCheck();
  }

  // Stops Firefox some minutes after its last tab closes.
  function scheduleIdleCheck() {
    clearIdleTimer();
    if (contexts.size || !connection) return;
    idleTimer = setTimeout(() => {
      idleTimer = null;
      if (!contexts.size) stop();
    }, idleShutdownMs);
    idleTimer.unref?.();
  }

  function clearIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = null;
  }

  function stop() {
    clearIdleTimer();
    stopping = true;
    const current = connection;
    const processHandle = child;
    connection = null;
    remUserContext = "";
    if (current && !current.closed) {
      void current.send("browser.close", {}, { timeout: STOP_TIMEOUT_MS }).catch(() => {}).finally(() => current.close());
    }
    if (processHandle) {
      // Firefox saves which extensions are installed while it shuts down.
      const timer = setTimeout(() => { try { processHandle.kill("SIGKILL"); } catch { /* Already gone. */ } }, STOP_TIMEOUT_MS);
      timer.unref?.();
      processHandle.once("exit", () => {
        clearTimeout(timer);
        stopWatchdog();
      });
    } else {
      stopWatchdog();
    }
  }

  // Rem's container starts signed out. Sites the user shares get a copy of
  // the user's Firefox cookies when a Rem tab opens or the list changes.
  async function mirrorSignIns(sites = [], removed = []) {
    if (!connection || !remUserContext) return;
    const userPartition = { type: "storageKey", userContext: "default" };
    const remPartition = { type: "storageKey", userContext: remUserContext };
    if (sites.length) {
      const { cookies = [] } = await send("storage.getCookies", { partition: userPartition });
      for (const cookie of cookies) {
        if (!sites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        const copy = {
          name: cookie.name,
          value: cookie.value,
          domain: cookie.domain,
          path: cookie.path,
          httpOnly: cookie.httpOnly,
          secure: cookie.secure,
          sameSite: cookie.sameSite,
          ...(Number.isFinite(cookie.expiry) ? { expiry: cookie.expiry } : {}),
        };
        await send("storage.setCookie", { cookie: copy, partition: remPartition }).catch((error) => {
          log("warn", `Could not share the sign-in for ${String(cookie.domain).replace(/^\./, "")} with Rem's Firefox tabs: ${error.message}`);
        });
      }
    }
    if (removed.length) {
      const { cookies = [] } = await send("storage.getCookies", { partition: remPartition });
      for (const cookie of cookies) {
        if (!removed.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        if (sites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        await send("storage.deleteCookies", { filter: { name: cookie.name, domain: cookie.domain }, partition: remPartition }).catch(() => {});
      }
    }
  }

  // Clears the cookies of one site, or of every site when host is empty.
  async function clearSiteCookies(kind, host) {
    if (!connection) return 0;
    const partition = { type: "storageKey", userContext: kind === "rem" ? remUserContext : "default" };
    if (kind === "rem" && !remUserContext) return 0;
    if (!host) {
      await send("storage.deleteCookies", { partition });
      return -1;
    }
    const { cookies = [] } = await send("storage.getCookies", { partition });
    let removed = 0;
    for (const cookie of cookies) {
      if (!cookieMatchesSite(cookie.domain, host)) continue;
      await send("storage.deleteCookies", { filter: { name: cookie.name, domain: cookie.domain }, partition }).catch(() => {});
      removed += 1;
    }
    return removed;
  }

  async function setRemDownloads(allowed) {
    remDownloadsAllowed = allowed === true;
    await applyRemDownloads().catch((error) => log("warn", `Could not apply Rem's download setting in Firefox: ${error.message}`));
  }

  async function applyRemDownloads() {
    if (!connection || !remUserContext) return;
    await send("browser.setDownloadBehavior", {
      downloadBehavior: remDownloadsAllowed ? { type: "allowed", destinationFolder: stagingDir } : { type: "denied" },
      userContexts: [remUserContext],
    });
  }

  return {
    clearSiteCookies,
    createContext,
    installExtension,
    listExtensions,
    openSignInWindow,
    get signingIn() { return Boolean(signInWindow); },
    profileDir,
    readUserPrefs,
    saveUserPrefs,
    stopAndWait,
    uninstallExtension,
    ensure,
    events,
    get generation() { return generation; },
    isRemContext: (userContext) => Boolean(userContext && userContext === remUserContext),
    mirrorSignIns,
    register,
    send,
    setRemDownloads,
    stagingDir,
    downloadsDir,
    status,
    stop,
    unregister,
  };
}

// Preferences the user may set from Raticode. Raticode's required ones, and
// those that control remote access or which add-ons load, stay fixed.
const LOCKED_PREF_PREFIXES = ["remote.", "marionette.", "devtools.debugger.", "devtools.chrome.", "xpinstall.signatures.", "extensions.enabledScopes", "extensions.autoDisableScopes", "extensions.installDistroAddons"];
const PREF_NAME = /^[A-Za-z0-9_.\-[\]@{}:]{1,200}$/;

function prefLocked(name) {
  return Object.hasOwn(PROFILE_PREFS, name) || LOCKED_PREF_PREFIXES.some((prefix) => name.startsWith(prefix));
}

function sanitizeUserPrefs(input = {}) {
  const prefs = {};
  const rejected = [];
  for (const [name, value] of Object.entries(input && typeof input === "object" ? input : {}).slice(0, 500)) {
    const valid = PREF_NAME.test(name) && (typeof value === "boolean" || Number.isInteger(value) && Math.abs(value) < 2 ** 31 || typeof value === "string" && value.length <= 4096);
    if (!valid) { rejected.push({ name: String(name).slice(0, 200), reason: "Not a valid Firefox setting." }); continue; }
    if (prefLocked(name)) { rejected.push({ name, reason: "Raticode keeps this setting fixed." }); continue; }
    prefs[name] = value;
  }
  return { prefs, rejected };
}

function tail(text) {
  const lines = String(text || "").trim().split("\n").filter(Boolean);
  return lines.slice(-3).join(" ").slice(0, 500);
}

module.exports = {
  IDLE_SHUTDOWN_MS,
  LOCKED_PREF_PREFIXES,
  prefLocked,
  sanitizeUserPrefs,
  signInEnvironment,
  PROFILE_PREFS,
  createBidiConnection,
  createFirefoxEngine,
  findFirefox,
  firefoxCandidates,
  launchEnvironment,
  profileDirFor,
  writeProfile,
};
