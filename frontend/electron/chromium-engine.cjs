// Runs one headless Chromium-family browser (Google Chrome) for the
// integrated browser and drives it over the Chrome DevTools Protocol. The
// browser uses a profile folder that Raticode creates and owns; the user's own
// Chrome profiles are never opened or changed.
//
// Raticode talks to the browser over a private pipe (--remote-debugging-pipe)
// instead of a network port, so no other program on the computer can connect
// to it. The browser closes when that pipe closes, which also happens when
// Raticode's process dies, so no watchdog process is needed.
//
// Each browser is described by an entry in BROWSERS: its name, where it is
// installed on each system and its profile folder. Other Chromium-based
// browsers can be added there and reuse this engine and chromium-contents.cjs.
const { spawn: spawnProcess } = require("node:child_process");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { cookieMatchesSite } = require("./browser-policy.cjs");

const LAUNCH_TIMEOUT_MS = 45000;
const COMMAND_TIMEOUT_MS = 60000;
const IDLE_SHUTDOWN_MS = 5 * 60 * 1000;
const STOP_TIMEOUT_MS = 10000;
const MAX_MESSAGE_BYTES = 256 * 1024 * 1024;

// Site permissions a windowless browser could never ask about. Like Firefox
// mode, they are refused in every tab while Raticode controls the browser.
const DENIED_PERMISSIONS = ["geolocation", "notifications", "videoCapture", "audioCapture", "midiSysex", "idleDetection", "windowManagement", "localNetworkAccess"];

const CHROME = {
  id: "chrome",
  name: "Chrome",
  fullName: "Google Chrome",
  profileFolder: "chrome-profile",
  envPath: "RATICODE_CHROME_PATH",
  candidates({ platform = process.platform, env = process.env, home = os.homedir() } = {}) {
    const explicit = env.RATICODE_CHROME_PATH ? [env.RATICODE_CHROME_PATH] : [];
    if (platform === "darwin") {
      return [...explicit, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", path.join(home, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")];
    }
    if (platform === "win32") {
      const roots = [env.ProgramFiles, env["ProgramFiles(x86)"], env.LOCALAPPDATA].filter(Boolean);
      return [...explicit, ...roots.map((root) => path.win32.join(root, "Google", "Chrome", "Application", "chrome.exe"))];
    }
    const names = ["google-chrome", "google-chrome-stable"];
    const fromPath = String(env.PATH || "").split(path.delimiter).filter(Boolean).flatMap((dir) => names.map((name) => path.join(dir, name)));
    return [...explicit, ...fromPath, "/opt/google/chrome/chrome", "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome"];
  },
};

// Microsoft Edge. Release first, then Beta, Dev and Canary. Edge opens
// chrome:// addresses as its own edge:// pages.
const EDGE = {
  id: "edge",
  name: "Edge",
  fullName: "Microsoft Edge",
  profileFolder: "edge-profile",
  envPath: "RATICODE_EDGE_PATH",
  ownPageSchemes: ["edge:", "chrome:"],
  ownPageHosts: ["settings", "extensions", "history", "favorites", "wallet", "downloads"],
  candidates({ platform = process.platform, env = process.env, home = os.homedir() } = {}) {
    const explicit = env.RATICODE_EDGE_PATH ? [env.RATICODE_EDGE_PATH] : [];
    if (platform === "darwin") {
      return [...explicit, ...["Microsoft Edge", "Microsoft Edge Beta", "Microsoft Edge Dev", "Microsoft Edge Canary"].flatMap((name) => [
        `/Applications/${name}.app/Contents/MacOS/${name}`,
        path.join(home, `Applications/${name}.app/Contents/MacOS/${name}`),
      ])];
    }
    if (platform === "win32") {
      const roots = [env["ProgramFiles(x86)"], env.ProgramFiles, env.LOCALAPPDATA].filter(Boolean);
      return [...explicit, ...["Edge", "Edge Beta", "Edge Dev", "Edge SxS"].flatMap((folder) => roots.map((root) => path.win32.join(root, "Microsoft", folder, "Application", "msedge.exe")))];
    }
    const names = ["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev"];
    const fromPath = String(env.PATH || "").split(path.delimiter).filter(Boolean).flatMap((dir) => names.map((name) => path.join(dir, name)));
    return [...explicit, ...fromPath, "/opt/microsoft/msedge/msedge", "/usr/bin/microsoft-edge-stable", "/usr/bin/microsoft-edge",
      "/opt/microsoft/msedge-beta/msedge", "/opt/microsoft/msedge-dev/msedge"];
  },
};

const BROWSERS = { chrome: CHROME, edge: EDGE };

function findBrowser(browser, options = {}) {
  const exists = options.exists || ((candidate) => {
    try { return fs.statSync(candidate).isFile(); } catch { return false; }
  });
  for (const candidate of browser.candidates(options)) {
    if (candidate && exists(candidate)) return candidate;
  }
  return "";
}

// Flags shared by the windowless browser and the sign-in window, so both read
// the same profile the same way.
function profileArgs(profileDir, platform) {
  return [
    `--user-data-dir=${profileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    // On Linux, Chrome keeps the key that protects saved cookies in the
    // desktop's keyring when it can reach one and in the profile otherwise.
    // The windowless browser can't reach the keyring, so both use the
    // profile's key; otherwise a sign-in made in the window would be
    // unreadable in Raticode's tabs. The profile folder is private (0700).
    ...(platform === "linux" ? ["--password-store=basic"] : []),
  ];
}

function headlessArgs(profileDir, platform) {
  return [
    "--headless",
    "--remote-debugging-pipe",
    ...profileArgs(profileDir, platform),
    // Every tab is its own window and most are not on screen; keep them
    // running at full speed so the stream and Rem's actions stay responsive.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--hide-crash-restore-bubble",
    "about:blank",
  ];
}

function launchEnvironment(env, platform) {
  const next = { ...env };
  // A headless browser needs no display. Without one it cannot show
  // anything on the desktop or take keyboard focus, even by accident.
  if (platform === "linux") {
    delete next.DISPLAY;
    delete next.WAYLAND_DISPLAY;
  }
  return next;
}

// The browser's profile folder holds cookies and history; only this user
// may read it.
function prepareProfile(profileDir) {
  fs.mkdirSync(profileDir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(profileDir, 0o700); } catch { /* Not supported on every file system. */ }
  // A crashed run leaves a lock that names a process that is gone; Chrome
  // clears it itself. Nothing else in the profile is touched.
}

// One DevTools connection over the pipe Chrome reads on fd 3 and writes on
// fd 4. Messages are JSON, each ended by a NUL byte. Commands resolve with
// their result and reject with Chrome's error; events go to `onEvent`.
function createPipeConnection(writable, readable, { onEvent = () => {}, onClose = () => {}, name = "Chrome" } = {}) {
  const pending = new Map();
  let nextId = 0;
  let closed = false;
  let buffered = [];
  let bufferedBytes = 0;
  function finish() {
    if (closed) return;
    closed = true;
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(new Error(`${name} closed the connection.`));
    }
    pending.clear();
    onClose();
  }
  function dispatch(text) {
    let payload;
    try { payload = JSON.parse(text); } catch { return; }
    if (payload.id === undefined) {
      if (payload.method) onEvent(payload.method, payload.params || {}, payload.sessionId || "");
      return;
    }
    const entry = pending.get(payload.id);
    if (!entry) return;
    pending.delete(payload.id);
    clearTimeout(entry.timer);
    if (payload.error) entry.reject(Object.assign(new Error(payload.error.message || `${name} reported an error.`), { code: payload.error.code }));
    else entry.resolve(payload.result || {});
  }
  readable.on("data", (chunk) => {
    let start = 0;
    for (let at = chunk.indexOf(0); at >= 0; at = chunk.indexOf(0, start)) {
      buffered.push(chunk.subarray(start, at));
      const text = Buffer.concat(buffered).toString("utf8");
      buffered = [];
      bufferedBytes = 0;
      start = at + 1;
      dispatch(text);
    }
    if (start < chunk.length) {
      buffered.push(chunk.subarray(start));
      bufferedBytes += chunk.length - start;
      // A message this large is not one Raticode asked for.
      if (bufferedBytes > MAX_MESSAGE_BYTES) {
        buffered = [];
        bufferedBytes = 0;
        readable.destroy();
      }
    }
  });
  readable.on("close", finish);
  readable.on("error", finish);
  writable.on("error", finish);
  function send(method, params = {}, { sessionId = "", timeout = COMMAND_TIMEOUT_MS } = {}) {
    if (closed) return Promise.reject(new Error(`${name} is not running.`));
    nextId += 1;
    const id = nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${name} did not answer ${method} in time.`));
      }, timeout);
      timer.unref?.();
      pending.set(id, { resolve, reject, timer });
      writable.write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
    });
  }
  return {
    send,
    get closed() { return closed; },
    close: () => {
      try { writable.end(); } catch { /* Already closed. */ }
    },
  };
}

function createChromiumEngine({
  browser = CHROME,
  dataDir,
  stagingDir = path.join(dataDir, `${browser.id}-downloads`),
  downloadsDir = path.join(os.homedir(), "Downloads"),
  log = () => {},
  platform = process.platform,
  env = process.env,
  spawn = spawnProcess,
  find = () => findBrowser(browser, { platform, env }),
  idleShutdownMs = IDLE_SHUTDOWN_MS,
} = {}) {
  const events = new EventEmitter();
  // Tabs by their DevTools session, and the frames each tab owns.
  const targets = new Map();
  const downloadOwners = new Map();
  let child = null;
  let signInWindow = null;
  let connection = null;
  let starting = null;
  let remContext = "";
  let idleTimer = null;
  let generation = 0;
  let remDownloadsAllowed = false;
  let version = "";
  let userAgent = "";
  let stopping = false;
  // Resolves when the last browser Raticode stopped has exited. Chrome hands
  // a second launch on the same profile to the running one, so a restart
  // waits for it.
  let exited = Promise.resolve();
  const name = browser.name;
  const article = /^[aeiou]/i.test(name) ? "an" : "a";

  function profileDir() {
    return path.join(dataDir, browser.profileFolder);
  }

  function status() {
    const executable = find();
    return {
      id: browser.id,
      name: browser.fullName,
      available: Boolean(executable),
      executable,
      running: Boolean(connection && !connection.closed),
      version,
      profile: profileDir(),
    };
  }

  // Stops the windowless browser and opens `url` in a normal window on the
  // same profile, without remote control. Raticode's tabs start the browser
  // again after the user closes the window.
  async function openSignInWindow(url) {
    let address;
    try { address = new URL(String(url)); } catch { throw new Error("Choose a web address to sign in at."); }
    // A web page, or one of the browser's own settings pages, which a normal
    // window can show even though the streamed tabs can't.
    const ownPage = (browser.ownPageSchemes || ["chrome:"]).includes(address.protocol)
      && (browser.ownPageHosts || ["settings", "extensions", "history", "bookmarks", "password-manager", "downloads"]).includes(address.hostname);
    if (!["http:", "https:"].includes(address.protocol) && !ownPage) throw new Error("Choose a web address to sign in at.");
    if (signInWindow) return { alreadyOpen: true };
    const executable = find();
    if (!executable) throw new Error(`${browser.fullName} is not installed.`);
    if (starting) await starting.catch(() => {});
    await stopAndWait();
    prepareProfile(profileDir());
    const processHandle = spawn(executable, [...profileArgs(profileDir(), platform), ...(platform === "win32" || platform === "darwin" ? ["--disable-background-mode"] : []), "--new-window", address.toString()], {
      env: { ...env },
      stdio: "ignore",
      windowsHide: false,
    });
    signInWindow = processHandle;
    const finished = () => {
      if (signInWindow !== processHandle) return;
      signInWindow = null;
      log("info", `The ${name} sign-in window closed.`);
      events.emit("sign-in-window-closed");
    };
    processHandle.once("exit", finished);
    processHandle.once("error", (error) => {
      log("warn", `Could not open ${article} ${name} window: ${error.message}`);
      finished();
    });
    log("info", `Opened ${article} ${name} window without remote control for signing in.`);
    return { opened: true };
  }

  // Resolves once the browser has exited.
  function stopAndWait() {
    stop();
    return waitForExit();
  }

  async function ensure() {
    if (connection && !connection.closed) return connection;
    // Both would use the same profile, and Chrome allows one browser per profile.
    if (signInWindow) throw new Error(`${name} is open in a window for signing in. Close that window to use ${name} in Raticode again.`);
    if (!starting) starting = start().finally(() => { starting = null; });
    return starting;
  }

  async function start() {
    const executable = find();
    if (!executable) {
      throw new Error(`${browser.fullName} is not installed. Install ${browser.fullName}, or choose Raticode as the web browser in Settings > Browser.`);
    }
    await waitForExit();
    prepareProfile(profileDir());
    stopping = false;
    const processHandle = spawn(executable, headlessArgs(profileDir(), platform), {
      env: launchEnvironment(env, platform),
      stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    child = processHandle;
    const thisGeneration = ++generation;
    let stderr = "";
    processHandle.stderr?.on("data", (chunk) => { stderr = (stderr + chunk).slice(-8000); });
    const failed = new Promise((_, reject) => {
      processHandle.once("error", (error) => reject(new Error(`Could not start ${name}: ${error.message}`)));
      processHandle.once("exit", (code) => reject(new Error(`${name} exited before it was ready (${code ?? "signal"}). ${tail(stderr)}`.trim())));
    });
    failed.catch(() => {});
    processHandle.once("exit", (code, signal) => {
      if (generation !== thisGeneration) return;
      child = null;
      connection = null;
      remContext = "";
      if (!stopping) log("warn", `${name} stopped unexpectedly (${code ?? signal ?? "unknown"}).`);
      events.emit("exit", { expected: stopping });
    });
    const writable = processHandle.stdio[3];
    const readable = processHandle.stdio[4];
    const next = createPipeConnection(writable, readable, {
      name,
      onEvent: (method, params, sessionId) => route(method, params, sessionId),
      onClose: () => {
        if (generation !== thisGeneration || stopping) return;
        try { processHandle.kill(); } catch { /* Already gone. */ }
      },
    });
    const ready = (async () => {
      const info = await next.send("Browser.getVersion", {});
      version = String(info.product || "").split("/")[1] || "";
      userAgent = String(info.userAgent || "");
      await next.send("Target.setDiscoverTargets", { discover: true });
      // Rem's tabs get a separate browser context that starts signed out and
      // keeps nothing on disk; the sites the user shares are copied in.
      remContext = (await next.send("Target.createBrowserContext", { disposeOnDetach: true })).browserContextId;
      for (const browserContextId of [undefined, remContext]) {
        for (const permission of DENIED_PERMISSIONS) {
          await next.send("Browser.setPermission", { permission: { name: permission }, setting: "denied", ...(browserContextId ? { browserContextId } : {}) }).catch(() => {});
        }
      }
      fs.mkdirSync(stagingDir, { recursive: true, mode: 0o700 });
      await next.send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: stagingDir, eventsEnabled: true });
    })();
    try {
      await Promise.race([ready, failed, new Promise((_, reject) => {
        const timer = setTimeout(() => reject(new Error(`${name} did not start in time.`)), LAUNCH_TIMEOUT_MS);
        timer.unref?.();
      })]);
      connection = next;
      await applyRemDownloads();
    } catch (error) {
      next.close();
      try { processHandle.kill("SIGKILL"); } catch { /* Already gone. */ }
      if (child === processHandle) child = null;
      throw error;
    }
    log("info", `Started ${browser.fullName} for the integrated browser (${path.basename(executable)}${version ? ` ${version}` : ""}).`);
    scheduleIdleCheck();
    return connection;
  }

  function route(method, params, sessionId) {
    if (sessionId) {
      targets.get(sessionId)?.handleEvent(method, params);
      return;
    }
    switch (method) {
      case "Target.targetCreated":
      case "Target.targetInfoChanged": {
        const info = params.targetInfo || {};
        for (const target of targets.values()) {
          if (info.openerId && info.openerId === target.targetId && info.type === "page" && info.targetId !== target.targetId) target.handleEvent("popup", info);
          else if (info.targetId === target.targetId) target.handleEvent("Target.targetInfoChanged", info);
        }
        break;
      }
      case "Target.targetDestroyed":
      case "Target.detachedFromTarget":
        for (const target of targets.values()) {
          if (params.targetId === target.targetId || params.sessionId === target.sessionId) target.handleEvent("Target.targetDestroyed", params);
        }
        break;
      case "Target.targetCrashed":
        for (const target of targets.values()) if (params.targetId === target.targetId) target.handleEvent("Target.targetCrashed", params);
        break;
      case "Browser.downloadWillBegin": {
        const owner = [...targets.values()].find((target) => target.ownsFrame?.(params.frameId));
        if (owner) {
          downloadOwners.set(params.guid, owner);
          owner.handleEvent("Browser.downloadWillBegin", params);
        }
        break;
      }
      case "Browser.downloadProgress": {
        if (params.state === "inProgress") break;
        const owner = downloadOwners.get(params.guid);
        downloadOwners.delete(params.guid);
        const filepath = path.join(stagingDir, String(params.guid || "").replace(/[^A-Za-z0-9-]/g, ""));
        if (owner) owner.handleEvent("Browser.downloadProgress", { ...params, filepath });
        else fs.rmSync(filepath, { force: true });
        break;
      }
      default:
        break;
    }
  }

  function send(method, params, options) {
    if (!connection || connection.closed) return Promise.reject(new Error(`${name} is not running.`));
    return connection.send(method, params, options);
  }

  // A new background window, attached with its own DevTools session.
  async function createContext(kind) {
    await ensure();
    const browserContextId = kind === "rem" ? remContext : undefined;
    const { targetId } = await send("Target.createTarget", { url: "about:blank", newWindow: true, background: true, ...(browserContextId ? { browserContextId } : {}) });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    return { targetId, sessionId, generation, userAgent };
  }

  function register(sessionId, target) {
    targets.set(sessionId, target);
    clearIdleTimer();
  }

  function unregister(sessionId) {
    targets.delete(sessionId);
    scheduleIdleCheck();
  }

  // Stops the browser some minutes after its last tab closes.
  function scheduleIdleCheck() {
    clearIdleTimer();
    if (targets.size || !connection) return;
    idleTimer = setTimeout(() => {
      idleTimer = null;
      if (!targets.size) stop();
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
    remContext = "";
    if (current && !current.closed) {
      // Browser.close lets Chrome save its profile; closing the pipe ends it either way.
      void current.send("Browser.close", {}, { timeout: STOP_TIMEOUT_MS }).catch(() => {}).finally(() => current.close());
    }
    if (processHandle && processHandle.exitCode === null && !processHandle.signalCode) {
      const timer = setTimeout(() => { try { processHandle.kill("SIGKILL"); } catch { /* Already gone. */ } }, STOP_TIMEOUT_MS);
      timer.unref?.();
      exited = new Promise((resolve) => processHandle.once("exit", () => { clearTimeout(timer); resolve(); }));
    }
  }

  function waitForExit() {
    return Promise.race([exited, new Promise((resolve) => {
      const timer = setTimeout(resolve, STOP_TIMEOUT_MS + 2000);
      timer.unref?.();
    })]);
  }

  function contextParam(kind) {
    if (kind === "rem") return remContext ? { browserContextId: remContext } : null;
    return {};
  }

  // Storage.* has no per-cookie delete for a browser context, so a removal
  // clears the context's cookies and puts back the ones that stay.
  async function replaceCookies(scope, keep) {
    await send("Storage.clearCookies", scope);
    if (keep.length) await send("Storage.setCookies", { ...scope, cookies: keep.map(cookieParam) });
  }

  // Rem's context starts signed out. Sites the user shares get a copy of the
  // user's cookies when a Rem tab opens or the list changes.
  async function mirrorSignIns(sites = [], removed = []) {
    if (!connection || !remContext) return;
    const remScope = { browserContextId: remContext };
    if (sites.length) {
      const { cookies = [] } = await send("Storage.getCookies", {});
      const shared = cookies.filter((cookie) => sites.some((site) => cookieMatchesSite(cookie.domain, site)));
      if (shared.length) {
        await send("Storage.setCookies", { ...remScope, cookies: shared.map(cookieParam) }).catch((error) => {
          log("warn", `Could not share sign-ins with Rem's ${name} tabs: ${error.message}`);
        });
      }
    }
    if (removed.length) {
      const { cookies = [] } = await send("Storage.getCookies", remScope);
      const gone = (cookie) => removed.some((site) => cookieMatchesSite(cookie.domain, site)) && !sites.some((site) => cookieMatchesSite(cookie.domain, site));
      if (cookies.some(gone)) await replaceCookies(remScope, cookies.filter((cookie) => !gone(cookie)));
    }
  }

  // Clears the cookies of one site, or of every site when host is empty.
  async function clearSiteCookies(kind, host) {
    if (!connection) return 0;
    const scope = contextParam(kind);
    if (!scope) return 0;
    if (!host) {
      await send("Storage.clearCookies", scope);
      return -1;
    }
    const { cookies = [] } = await send("Storage.getCookies", scope);
    const matching = cookies.filter((cookie) => cookieMatchesSite(cookie.domain, host));
    if (matching.length) await replaceCookies(scope, cookies.filter((cookie) => !cookieMatchesSite(cookie.domain, host)));
    return matching.length;
  }

  async function setRemDownloads(allowed) {
    remDownloadsAllowed = allowed === true;
    await applyRemDownloads().catch((error) => log("warn", `Could not apply Rem's download setting in ${name}: ${error.message}`));
  }

  async function applyRemDownloads() {
    if (!connection || !remContext) return;
    await send("Browser.setDownloadBehavior", remDownloadsAllowed
      ? { behavior: "allowAndName", downloadPath: stagingDir, eventsEnabled: true, browserContextId: remContext }
      : { behavior: "deny", eventsEnabled: true, browserContextId: remContext });
  }

  return {
    browser,
    clearSiteCookies,
    createContext,
    downloadsDir,
    ensure,
    events,
    get generation() { return generation; },
    isRemContext: (browserContextId) => Boolean(browserContextId && browserContextId === remContext),
    mirrorSignIns,
    openSignInWindow,
    profileDir,
    register,
    send,
    setRemDownloads,
    get signingIn() { return Boolean(signInWindow); },
    stagingDir,
    status,
    stop,
    stopAndWait,
    unregister,
  };
}

// The fields Storage.setCookies accepts, from a cookie Storage.getCookies returned.
function cookieParam(cookie) {
  return {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path,
    secure: cookie.secure === true,
    httpOnly: cookie.httpOnly === true,
    ...(cookie.sameSite ? { sameSite: cookie.sameSite } : {}),
    ...(cookie.session === false && Number.isFinite(cookie.expires) && cookie.expires > 0 ? { expires: cookie.expires } : {}),
    ...(cookie.priority ? { priority: cookie.priority } : {}),
    ...(cookie.sourceScheme ? { sourceScheme: cookie.sourceScheme } : {}),
    ...(Number.isInteger(cookie.sourcePort) ? { sourcePort: cookie.sourcePort } : {}),
    ...(cookie.partitionKey ? { partitionKey: cookie.partitionKey } : {}),
  };
}

function tail(text) {
  const lines = String(text || "").trim().split("\n").filter(Boolean);
  return lines.slice(-3).join(" ").slice(0, 500);
}

module.exports = {
  BROWSERS,
  CHROME,
  EDGE,
  DENIED_PERMISSIONS,
  IDLE_SHUTDOWN_MS,
  cookieParam,
  createChromiumEngine,
  createPipeConnection,
  findBrowser,
  headlessArgs,
  launchEnvironment,
  profileArgs,
};
