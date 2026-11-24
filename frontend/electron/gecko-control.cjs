// Lets Rem drive tabs in Raticode (gecko) when the user turns on "Let Rem use
// Raticode (gecko)". Gecko is real Firefox in its own window (gecko-window.cjs),
// so Rem's tabs are background tabs in that window, in a Firefox container of
// their own that starts signed out. Rem never sees or reaches the user's tabs:
// it can only use the tabs it opened, and a tab the user shares with it.
//
// Sharing: the user can hand Rem the tab in front in Gecko's own window.
// Firefox's control port reports a private window's tabs exactly like regular
// ones (no private flag in browsingContext.getTree or browser.getClientWindows,
// checked on Firefox 158), so Raticode only offers tabs from the windows that
// were open when Gecko started. Private windows never are: Firefox doesn't
// restore them, and Raticode never starts Gecko private. A profile set to
// always browse privately can't share at all. Rem's own tabs open in that
// same window, so they never land in a private window either.
//
// Firefox offers remote control only through WebDriver BiDi on a loopback port,
// with no password. As for the windowless Firefox engine (firefox-engine.cjs),
// Raticode claims Firefox's only session the moment the port opens, and closes
// Firefox if another program got there first. While the port is open, Firefox
// tells every page it is automated (navigator.webdriver) and shows its robot
// icon; that is why this is a setting the user turns on.
//
// This object has the engine interface FirefoxContents and browser-sessions.cjs
// use for the windowless Firefox, so Rem's site rules, approvals, pause,
// activity log, uploads and downloads apply to Gecko tabs through the same code.
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { cookieMatchesSite } = require("./browser-policy.cjs");
const { createBidiConnection } = require("./firefox-engine.cjs");

const LISTENING = /WebDriver BiDi listening on (ws:\/\/127\.0\.0\.1:\d+)/;
// Only these are subscribed for every tab: a pop-up from Rem's tab is found
// through contextCreated. Everything else is subscribed per Rem tab, so the
// user's own tabs, downloads, file pickers and dialogs work as before.
const GLOBAL_EVENTS = ["browsingContext.contextCreated", "browsingContext.contextDestroyed"];
const TAB_EVENTS = [
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
// A shared tab is the user's: Firefox keeps its downloads, file pickers and
// pop-ups. Rem gets its page events and dialogs, and attaches files itself.
const SHARED_TAB_EVENTS = TAB_EVENTS.filter((name) => !name.startsWith("browsingContext.download") && name !== "input.fileDialogOpened");
const PRIVATE_ONLY_MESSAGE = "This Gecko profile always browses privately, so its tabs can't be shared with Rem.";
const NO_TAB_MESSAGE = "Show the Gecko tab you want to share in Raticode's Browser, then try again. Only regular web pages in Gecko's main window can be shared; private windows never can.";
const CLOSED_FOR_SAFETY = "Raticode closed Gecko for safety, so no other program can take control of it.";
const OFF_MESSAGE = "Rem can't use Raticode (gecko) yet. Ask the user to turn on \"Let Rem use Raticode (gecko)\" in Settings > Browser > Raticode (gecko), or use browser: \"chromium\".";

function createGeckoControl({
  stagingDir,
  downloadsDir = path.join(os.homedir(), "Downloads"),
  log = () => {},
  WebSocketImpl = globalThis.WebSocket,
  connectBidi = createBidiConnection,
} = {}) {
  const events = new EventEmitter();
  const contexts = new Map();
  const frames = new Map();
  // Gecko's main window(s): open when Gecko started, so never private.
  const mainWindows = new Set();
  // The user's tabs shared with Rem, with the subscription to drop on release.
  const shared = new Map();
  let attachedAt = 0;
  let connection = null;
  // Closes the Firefox behind connection (attach's kill).
  let killGecko = null;
  let generation = 0;
  let remUserContext = "";
  let remDownloadsAllowed = false;
  // Supplied by gecko-window.cjs: whether the setting is on, and a function
  // that starts Gecko (hidden, inside Raticode) with its control port.
  let host = { enabled: () => false, privateOnly: () => false, available: () => ({ available: false, problem: "" }), start: async () => { throw new Error(OFF_MESSAGE); } };
  let attaching = null;

  function setHost(next) {
    host = { ...host, ...next };
  }

  // Called by gecko-window.cjs with Firefox's control address as it starts.
  // kill closes that Firefox. It runs whenever Raticode doesn't end up holding
  // the control port: a Firefox left running with its port open and no
  // Raticode session could be driven by any program on this computer.
  async function attach(url, { kill = () => {} } = {}) {
    detach({ expected: true });
    const thisGeneration = ++generation;
    const next = connectBidi(url, {
      WebSocketImpl,
      onEvent: (method, params) => route(method, params),
      onClose: () => { if (generation === thisGeneration && connection === next) detach(); },
    });
    attaching = (async () => {
      await next.opened;
      await next.send("session.new", {
        capabilities: { alwaysMatch: { acceptInsecureCerts: false, unhandledPromptBehavior: { default: "ignore", file: "ignore" } } },
      }).catch((error) => {
        if (error?.code !== "session not created") throw error;
        // Another program, possibly another account on this computer,
        // reached the control port first and could drive the user's Gecko.
        log("warn", `Another program took control of Raticode (gecko) before Raticode could (${error.message}).`);
        throw new Error("Another program on this computer took control of Raticode (gecko) as it started. Try again. If this keeps happening, check what else runs on this computer.");
      });
      await next.send("session.subscribe", { events: GLOBAL_EVENTS });
      // Gecko has just started, so every window open now is a regular one.
      attachedAt = Date.now();
      const { contexts: open = [] } = await next.send("browsingContext.getTree", { maxDepth: 0 });
      for (const item of open) if (item.clientWindow && !item.parent) mainWindows.add(item.clientWindow);
      remUserContext = (await next.send("browser.createUserContext", {})).userContext;
      // Rem's container starts signed out; shared sign-ins are copied in later.
      await next.send("storage.deleteCookies", { partition: { type: "storageKey", userContext: remUserContext } });
      connection = next;
      killGecko = kill;
      await applyRemDownloads();
      log("info", "Rem can use Raticode (gecko).");
    })().catch((error) => {
      // Cleared first, so closing the socket isn't taken for a dropped connection.
      if (generation === thisGeneration) { connection = null; killGecko = null; remUserContext = ""; }
      next.close();
      kill();
      throw error;
    }).finally(() => { attaching = null; });
    return attaching;
  }

  // Firefox stopped or the connection closed: Rem's Gecko tabs stop too.
  // expected: Raticode ended the connection itself. stopped: Firefox has
  // already exited. Anything else is a connection that dropped while Firefox
  // may still be running with its control port open, so Raticode closes it.
  function detach({ expected = false, stopped = false } = {}) {
    const current = connection;
    const kill = killGecko;
    connection = null;
    killGecko = null;
    remUserContext = "";
    frames.clear();
    mainWindows.clear();
    shared.clear();
    if (current && !current.closed) current.close();
    if (current && kill && !expected && !stopped) {
      log("warn", "Rem's connection to Raticode (gecko) closed unexpectedly.");
      kill(`Rem's connection to Raticode (gecko) closed unexpectedly. ${CLOSED_FOR_SAFETY}`);
    }
    if (current || contexts.size) {
      contexts.clear();
      events.emit("exit", { expected });
    }
  }

  function topLevelOf(context) {
    return frames.get(context) || context;
  }

  function route(method, params) {
    if (method === "browsingContext.contextCreated" && params.parent) {
      frames.set(params.context, topLevelOf(params.parent));
    } else if (method === "browsingContext.contextCreated" && params.clientWindow && !mainWindows.size && attachedAt && Date.now() - attachedAt < 60000) {
      // Gecko's first window hadn't opened yet when Raticode connected.
      mainWindows.add(params.clientWindow);
    } else if (method === "browsingContext.contextDestroyed") {
      frames.delete(params.context);
      for (const [frame, top] of frames) if (top === params.context) frames.delete(frame);
    }
    const opener = params.originalOpener ? topLevelOf(params.originalOpener) : "";
    // Pop-ups from a shared tab stay the user's, in Gecko's own window.
    if (method === "browsingContext.contextCreated" && opener && contexts.has(opener) && !shared.has(opener)) {
      contexts.get(opener).handleEvent("popup", params);
      return;
    }
    let target = contexts.get(params.context);
    if (!target && method.startsWith("network.")) target = contexts.get(frames.get(params.context));
    if (target) target.handleEvent(method, params);
    else if (method === "network.beforeRequestSent" && params.isBlocked) {
      void send("network.continueRequest", { request: params.request?.request }).catch(() => {});
    }
    // Only Rem's own downloads; the user's Gecko downloads are Firefox's own.
    if (method.startsWith("browsingContext.download") && contexts.has(topLevelOf(params.context)) && !shared.has(topLevelOf(params.context))) events.emit(method, params);
  }

  function send(method, params, options) {
    if (!connection || connection.closed) return Promise.reject(new Error("Raticode (gecko) is not running."));
    return connection.send(method, params, options);
  }

  async function ensure() {
    if (connection && !connection.closed) return connection;
    if (!host.enabled()) throw new Error(OFF_MESSAGE);
    if (attaching) { await attaching; return connection; }
    await host.start();
    if (attaching) await attaching;
    if (!connection) throw new Error(`Raticode (gecko) started, but Rem could not connect to it. ${CLOSED_FOR_SAFETY} Try again.`);
    return connection;
  }

  async function createContext(kind) {
    if (kind !== "rem") throw new Error("Only Rem's tabs open through this connection.");
    await ensure();
    // Firefox puts a new tab in the window used last, which may be a private
    // one; Rem's tabs go next to the last tab of Gecko's main window instead.
    const reference = (await topLevelTabs()).filter((item) => mainWindows.has(item.clientWindow)).at(-1);
    if (!reference && mainWindows.size) throw new Error("Raticode (gecko)'s main window is closed. Open Raticode (gecko) in the Browser, then try again.");
    const created = await send("browsingContext.create", { type: "tab", background: true, userContext: remUserContext, ...(reference ? { referenceContext: reference.context } : {}) });
    // Everything a tab reports, for this tab only.
    await send("session.subscribe", { events: TAB_EVENTS, contexts: [created.context] }).catch((error) => {
      void send("browsingContext.close", { context: created.context, promptUnload: false }).catch(() => {});
      throw error;
    });
    return { context: created.context, generation };
  }

  async function topLevelTabs() {
    const { contexts: open = [] } = await send("browsingContext.getTree", { maxDepth: 0 });
    return open.filter((item) => item && !item.parent && item.context);
  }

  // The user's tabs that may be shared: regular web pages in Gecko's main
  // window, not Rem's container, not already in use by Rem.
  async function shareCandidates() {
    if (host.privateOnly()) throw new Error(PRIVATE_ONLY_MESSAGE);
    await ensure();
    return (await topLevelTabs()).filter((item) => mainWindows.has(item.clientWindow)
      && item.userContext !== remUserContext
      && !contexts.has(item.context)
      && /^https?:/i.test(String(item.url || "")));
  }

  // The tab in front of Gecko's main window, which the user is looking at.
  async function shareableTab() {
    const candidates = await shareCandidates();
    const shown = [];
    for (const item of candidates) {
      const result = await send("script.evaluate", {
        expression: "[document.visibilityState, document.title]",
        target: { context: item.context, sandbox: "raticode-share" },
        awaitPromise: false,
      }).catch(() => null);
      const [visibility, title] = (result?.result?.value || []).map((entry) => entry?.value);
      item.title = typeof title === "string" ? title.slice(0, 300) : "";
      if (visibility === "visible") shown.push(item);
    }
    const found = shown.length === 1 ? shown[0] : !shown.length && candidates.length === 1 ? candidates[0] : null;
    if (!found) throw new Error(NO_TAB_MESSAGE);
    return { context: found.context, url: String(found.url), title: found.title || "" };
  }

  // Starts Rem's use of one of the user's tabs, checked again at this moment.
  async function adoptContext(context) {
    const allowed = (await shareCandidates()).some((item) => item.context === context);
    if (!allowed) throw new Error(NO_TAB_MESSAGE);
    const subscribed = await send("session.subscribe", { events: SHARED_TAB_EVENTS, contexts: [context] });
    shared.set(context, subscribed?.subscription || "");
    return { context, generation };
  }

  // Ends Rem's use of a shared tab. The tab stays open for the user.
  function release(context) {
    if (!shared.has(context)) return;
    const subscription = shared.get(context);
    shared.delete(context);
    contexts.delete(context);
    if (!connection || connection.closed) return;
    const request = subscription
      ? send("session.unsubscribe", { subscriptions: [subscription] })
      : send("session.unsubscribe", { events: SHARED_TAB_EVENTS, contexts: [context] });
    void request.catch((error) => log("warn", `Could not stop following a shared Gecko tab: ${error.message}`));
  }

  function register(context, target) {
    contexts.set(context, target);
  }

  function unregister(context) {
    contexts.delete(context);
  }

  function status() {
    const found = host.available();
    return { available: Boolean(found.available) && host.enabled(), enabled: host.enabled(), executable: found.executable || "", running: Boolean(connection && !connection.closed), problem: found.problem || "" };
  }

  // Rem's container starts signed out. Sites the user shares get a copy of
  // the user's Gecko cookies, as in Firefox and Chrome tabs.
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
          log("warn", `Could not share the sign-in for ${String(cookie.domain).replace(/^\./, "")} with Rem's Gecko tabs: ${error.message}`);
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

  // Only Rem's container. The user's Gecko cookies are cleared in Gecko's own
  // settings, never by Raticode.
  async function clearSiteCookies(kind, host_) {
    if (kind !== "rem" || !connection || !remUserContext) return 0;
    const partition = { type: "storageKey", userContext: remUserContext };
    if (!host_) {
      await send("storage.deleteCookies", { partition });
      return -1;
    }
    const { cookies = [] } = await send("storage.getCookies", { partition });
    let removed = 0;
    for (const cookie of cookies) {
      if (!cookieMatchesSite(cookie.domain, host_)) continue;
      await send("storage.deleteCookies", { filter: { name: cookie.name, domain: cookie.domain }, partition }).catch(() => {});
      removed += 1;
    }
    return removed;
  }

  async function setRemDownloads(allowed) {
    remDownloadsAllowed = allowed === true;
    await applyRemDownloads().catch((error) => log("warn", `Could not apply Rem's download setting in Raticode (gecko): ${error.message}`));
  }

  async function applyRemDownloads() {
    if (!connection || !remUserContext) return;
    if (remDownloadsAllowed) fs.mkdirSync(stagingDir, { recursive: true, mode: 0o700 });
    await send("browser.setDownloadBehavior", {
      downloadBehavior: remDownloadsAllowed ? { type: "allowed", destinationFolder: stagingDir } : { type: "denied" },
      userContexts: [remUserContext],
    });
  }

  // Closes Rem's Gecko tabs; Gecko itself keeps running for the user.
  function stop() {
    for (const context of [...contexts.keys()]) {
      if (shared.has(context)) { release(context); continue; }
      void send("browsingContext.close", { context, promptUnload: false }).catch(() => {});
    }
  }

  return {
    adoptContext,
    attach,
    clearSiteCookies,
    createContext,
    detach,
    downloadsDir,
    ensure,
    events,
    get generation() { return generation; },
    isRemContext: (userContext) => Boolean(userContext && userContext === remUserContext),
    mirrorSignIns,
    openSignInWindow: async () => { throw new Error("Sign in to sites in Raticode (gecko) itself."); },
    register,
    release,
    send,
    shareableTab,
    setHost,
    setRemDownloads,
    signingIn: false,
    stagingDir,
    status,
    stop,
    stopAndWait: async () => stop(),
    unregister,
    offMessage: OFF_MESSAGE,
    get remUserContext() { return remUserContext; },
  };
}

// The control address Firefox prints when its remote agent starts listening.
function listeningAddress(text) {
  return LISTENING.exec(String(text || ""))?.[1] || "";
}

function removeStaleServerFile(profileDir) {
  fs.rmSync(path.join(profileDir, "WebDriverBiDiServer.json"), { force: true });
}

module.exports = { CLOSED_FOR_SAFETY, GLOBAL_EVENTS, NO_TAB_MESSAGE, OFF_MESSAGE, PRIVATE_ONLY_MESSAGE, SHARED_TAB_EVENTS, TAB_EVENTS, createGeckoControl, listeningAddress, removeStaleServerFile };
