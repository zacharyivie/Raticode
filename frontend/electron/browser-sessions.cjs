// Integrated browser sessions: user tabs, local previews and Rem's tabs.
// The studio renders each tab as a <webview>; this module owns the guest
// web contents, their profiles and every browser policy applied to them.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { fileURLToPath, pathToFileURL } = require("node:url");
const {
  RATICODE_HOME_URL,
  browserCommandRequiresOwnerFocus,
  browserContentZoomFactor,
  browserLoadUrl,
  browserSessionShortcutAction,
  canOpenInExternalBrowser,
  normalizeBrowserUrl,
} = require("./browser-utils.cjs");
const policyRules = require("./browser-policy.cjs");
const {
  acceptLanguageList,
  createClientHints,
  defaultMetadata,
  mergeMetadata,
  reducedUserAgent,
} = require("./browser-client-hints.cjs");

const {
  BROWSER_PARTITIONS,
  PARTITIONS,
  allowPageTab,
  clampZoom,
  classifyLoadError,
  cookieMatchesSite,
  hostOf,
  isCertificateErrorCode,
  isDangerousDownload,
  nextZoomLevel,
  permissionDecision,
  permissionKeys,
  permissionLabel,
  popupDecision,
  remSiteAllowed,
  sanitizeBrowserPolicy,
  sanitizeFilename,
  securityState,
  siteKey,
  uniqueDownloadPath,
  userAgentWithoutEmbedderTokens,
  externalProtocolOf,
} = policyRules;

const PROMPT_TIMEOUT_MS = 2 * 60 * 1000;
const PDF_TIMEOUT_MS = 60 * 1000;
const PAGE_CONTEXT_WORLD_ID = 1918;
const UA_SAMPLE_WORLD_ID = 1919;
const HIGH_ENTROPY_FIELDS = ["architecture", "bitness", "formFactors", "fullVersionList", "model", "platformVersion", "uaFullVersion", "wow64"];
// Client hint headers only apply to potentially trustworthy URLs.
const CLIENT_HINT_URLS = ["https://*/*", "wss://*/*", "http://localhost/*", "http://127.0.0.1/*", "http://*.localhost/*", "ws://localhost/*", "ws://127.0.0.1/*"];
const MAX_RESTORED_ENTRIES = 25;
const MAX_PAGE_STATE_CHARS = 16 * 1024;
const USER_INPUT_TYPES = new Set(["mouseDown", "keyDown", "rawKeyDown", "touchStart", "gestureTap", "char"]);

// Matches browser_owner() in src/gofer/ui/browser_tools.py.
function remOwnerForThread(threadId) {
  return crypto.createHash("sha256").update(String(threadId)).digest("hex").slice(0, 32);
}

function createBrowserSessions({
  app,
  Menu,
  clipboard,
  dialog,
  shell,
  session: electronSession,
  webContents: webContentsApi,
  getMainWindow,
  resolvePath,
  store,
  log = () => {},
  quarantineDir,
  platform = process.platform,
  chromeVersion = process.versions.chrome,
}) {
  const sessions = new Map();
  const ownersWithCleanup = new WeakSet();
  const prompts = new Map();
  const downloads = new Map();
  const certificateExceptions = new Set();
  const verifiedCertificates = new Map();
  const clientCertificateChoices = new Map();
  const childWindows = new Map();
  const clientHints = new Map();
  let policy = sanitizeBrowserPolicy({});
  let installed = false;

  const profile = (kind) => electronSession.fromPartition(PARTITIONS[kind]);

  // ---------------------------------------------------------------------------
  // Profiles

  function install() {
    if (installed) return;
    installed = true;
    const identity = browserIdentity(app.userAgentFallback || profile("user").getUserAgent());
    // Pop-ups opened with window.open start without their profile's user
    // agent and use this app-wide fallback, which would otherwise announce
    // Electron and Raticode. Windows that already exist keep theirs.
    app.userAgentFallback = identity.userAgent;
    for (const kind of ["user", "rem"]) {
      const ses = profile(kind);
      installIdentity(kind, ses, identity);
      ses.setPermissionRequestHandler((contents, permission, callback, details) => {
        void requestPermission(contents, permission, details || {}, kind).then(callback, () => callback(false));
      });
      ses.setPermissionCheckHandler((contents, permission, _origin, details) => checkPermission(contents, permission, details || {}, kind));
      ses.setDevicePermissionHandler(() => false);
      ses.on("will-download", (event, item, contents) => handleDownload(event, item, contents, kind));
      ses.setCertificateVerifyProc((request, callback) => {
        if (request.errorCode === 0 && request.certificate) verifiedCertificates.set(request.hostname, certificateSummary(request.certificate));
        callback(-3);
      });
    }
    sampleUserAgentMetadata();
    const user = profile("user");
    try {
      user.setSpellCheckerEnabled(true);
      if (platform !== "darwin") {
        const available = user.availableSpellCheckerLanguages || [];
        const locale = app.getLocale();
        const language = [locale, locale.split("-")[0], "en-US"].find((item) => available.includes(item));
        if (language) user.setSpellCheckerLanguages([language]);
      }
    } catch (error) {
      log("warn", `Spellcheck unavailable: ${error.message}`);
    }
    try { profile("rem").setSpellCheckerEnabled(false); } catch { /* Spellcheck is optional. */ }
    // Shared sign-ins: mirror cookies for granted sites into Rem's profile.
    user.cookies.on("changed", (_event, cookie, _cause, removed) => {
      if (!policy.remSignInSites.some((site) => cookieMatchesSite(cookie.domain, site))) return;
      void mirrorCookie(cookie, removed).catch(() => {});
    });
  }

  // Tabs present as the Chromium they run on: Chrome's reduced user agent,
  // Accept-Language matching navigator.languages, and the Sec-CH-UA headers
  // Electron omits. Google's sign-in hosts get Electron's own user agent
  // instead; see browser-client-hints.cjs.
  function browserIdentity(original) {
    const preferred = typeof app.getPreferredSystemLanguages === "function" ? app.getPreferredSystemLanguages() : [];
    return {
      userAgent: reducedUserAgent(userAgentWithoutEmbedderTokens(original), chromeVersion),
      embedderUserAgent: userAgentWithoutEmbedderTokens(original, { keepElectron: true }),
      languages: acceptLanguageList(preferred.length ? preferred : [app.getLocale()]),
    };
  }

  function installIdentity(kind, ses, identity) {
    ses.setUserAgent(identity.userAgent, identity.languages.join(","));
    const platformVersion = platform === "darwin" && typeof process.getSystemVersion === "function" ? process.getSystemVersion() : "";
    const hints = createClientHints(defaultMetadata({ chromeVersion, platform, platformVersion }), {
      embedderUserAgent: identity.embedderUserAgent,
    });
    clientHints.set(kind, hints);
    if (!ses.webRequest) return;
    ses.webRequest.onBeforeSendHeaders({ urls: CLIENT_HINT_URLS }, (details, callback) => {
      try {
        callback({ requestHeaders: hints.requestHeaders({ url: details.url, firstPartyUrl: firstPartyUrl(details), headers: details.requestHeaders }) });
      } catch (error) {
        log("warn", `Client hints failed: ${error.message}`);
        callback({});
      }
    });
    ses.webRequest.onHeadersReceived({ urls: CLIENT_HINT_URLS }, (details, callback) => {
      let restart = false;
      try {
        restart = hints.responseNeedsRestart(details);
      } catch (error) {
        log("warn", `Client hints failed: ${error.message}`);
      }
      if (!restart) {
        callback({});
        return;
      }
      // Critical-CH: replay the request once with the hints the site needs.
      // A 307 keeps the method and body, like Chromium's own restart.
      const responseHeaders = Object.fromEntries(Object.entries(details.responseHeaders || {}).filter(([key]) => key.toLowerCase() !== "location"));
      callback({ statusLine: "HTTP/1.1 307 Temporary Redirect", responseHeaders: { ...responseHeaders, Location: [details.url] } });
    });
  }

  // Electron 44 made clipboard writes asynchronous.
  function copyText(text) {
    void Promise.resolve(clipboard.writeText(text)).catch((error) => log("warn", `Copy failed: ${error.message}`));
  }

  function firstPartyUrl(details) {
    if (details.resourceType === "mainFrame") return details.url;
    try {
      return details.frame?.top?.url || details.webContents?.getURL?.() || "";
    } catch {
      return "";
    }
  }

  // navigator.userAgentData is browser-wide, so the studio window reports the
  // exact values tabs expose to scripts. Headers then match them exactly.
  function sampleUserAgentMetadata() {
    const contents = getMainWindow()?.webContents;
    if (!contents || contents.isDestroyed() || typeof contents.executeJavaScriptInIsolatedWorld !== "function") return;
    const sample = () => {
      if (contents.isDestroyed()) return;
      const code = `navigator.userAgentData ? navigator.userAgentData.getHighEntropyValues(${JSON.stringify(HIGH_ENTROPY_FIELDS)}).then((value) => JSON.parse(JSON.stringify(value))) : null`;
      void Promise.resolve(contents.executeJavaScriptInIsolatedWorld(UA_SAMPLE_WORLD_ID, [{ code }]))
        .then((sampled) => {
          if (!sampled) return;
          for (const hints of clientHints.values()) hints.setMetadata(mergeMetadata(hints.metadata(), sampled));
        })
        .catch((error) => log("warn", `Could not read browser identity: ${error.message}`));
    };
    const url = contents.getURL();
    if (contents.isLoading() || !url || url === "about:blank") contents.once("did-finish-load", sample); else sample();
  }

  function setPolicy(_event, value = {}) {
    const previous = policy.remSignInSites;
    policy = sanitizeBrowserPolicy(value);
    const added = policy.remSignInSites.filter((site) => !previous.includes(site));
    const removed = previous.filter((site) => !policy.remSignInSites.includes(site));
    if (added.length || removed.length) void syncRemSignIns(added, removed).catch((error) => log("warn", `Sign-in sharing failed: ${error.message}`));
    return { ok: true };
  }

  async function syncRemSignIns(added = policy.remSignInSites, removed = []) {
    const user = profile("user");
    const rem = profile("rem");
    const userCookies = await user.cookies.get({});
    for (const site of added) {
      for (const cookie of userCookies.filter((item) => cookieMatchesSite(item.domain, site))) await mirrorCookie(cookie, false);
    }
    if (removed.length) {
      const remCookies = await rem.cookies.get({});
      for (const cookie of remCookies) {
        if (!removed.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        if (policy.remSignInSites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        await rem.cookies.remove(cookieUrl(cookie), cookie.name).catch(() => {});
      }
    }
  }

  async function mirrorCookie(cookie, removed) {
    const rem = profile("rem");
    if (removed) {
      await rem.cookies.remove(cookieUrl(cookie), cookie.name);
      return;
    }
    await rem.cookies.set({
      url: cookieUrl(cookie),
      name: cookie.name,
      value: cookie.value,
      ...(cookie.hostOnly ? {} : { domain: cookie.domain }),
      path: cookie.path,
      secure: cookie.secure,
      httpOnly: cookie.httpOnly,
      ...(cookie.session ? {} : { expirationDate: cookie.expirationDate }),
      ...(cookie.sameSite ? { sameSite: cookie.sameSite } : {}),
    });
  }

  // ---------------------------------------------------------------------------
  // Session lifecycle

  function createBrowser(event, options = {}) {
    const mainWindow = getMainWindow();
    if (!mainWindow || mainWindow.isDestroyed()) throw new Error("The browser window is unavailable.");
    const requestedPath = typeof options.path === "string" ? options.path.trim() : "";
    let url = "about:blank";
    let grantId = "";
    let kind = "user";
    if (requestedPath) {
      const targetPath = resolvePath(requestedPath, { grantId: options.grantId, mustExist: true });
      if (!fs.statSync(targetPath).isFile()) throw new Error(`Browser preview path is not a file: ${targetPath}`);
      url = pathToFileURL(targetPath).toString();
      grantId = typeof options.grantId === "string" ? options.grantId : "";
      kind = "preview";
    } else if (options.url) {
      url = browserLoadUrl(normalizeBrowserUrl(options.url, policy.searchUrl));
      // Local files always render in the in-memory preview profile.
      if (url.startsWith("file:")) {
        resolvePath(fileURLToPath(url), { mustExist: true });
        kind = "preview";
      }
    }
    const history = sanitizeHistory(options.history);
    const id = crypto.randomUUID();
    const session = baseSession({
      id,
      owner: event.sender,
      kind,
      url: history ? "about:blank" : url,
      initialUrl: history ? history.entries[history.index].url : url,
      grantId,
      clientId: typeof options.clientId === "string" ? options.clientId : "",
      applicationKeybindings: sanitizeBrowserKeybindings(options.applicationKeybindings),
      openBrowserBinding: typeof options.openBrowserBinding === "string" ? options.openBrowserBinding.slice(0, 120) : "Mod+Alt+Slash",
    });
    session.pendingHistory = history;
    sessions.set(id, session);
    registerOwnerCleanup(event.sender);
    return { ...sessionState(session), src: session.pendingSrc, partition: PARTITIONS[kind] };
  }

  function baseSession({ id, owner, kind, url, initialUrl = url, grantId = "", clientId = "", applicationKeybindings = {}, openBrowserBinding = "Mod+Alt+Slash", agent = null }) {
    return {
      agent,
      agentLog: [],
      agentPaused: false,
      applicationChord: null,
      applicationKeybindings,
      audible: false,
      blockedPopups: [],
      certificateError: null,
      certificateException: false,
      clientId,
      contents: null,
      crashed: null,
      error: "",
      favicon: "",
      findRequestId: null,
      findResult: null,
      grantId,
      id,
      initialUrl,
      internalHomeUrl: url.startsWith("data:text/html") ? url : "",
      kind,
      lastRecordedUrl: "",
      lastUserInputAt: 0,
      loadError: null,
      localNavigation: url.startsWith("file:"),
      muted: false,
      openBrowserBinding,
      owner,
      ownerId: owner.id,
      ownerZoomFactor: 1,
      pageTabTimes: [],
      pageZoomFactor: 1,
      pendingSrc: url,
      pendingTyped: false,
      projectChordDeadline: 0,
      unresponsive: false,
      viewSize: null,
    };
  }

  function sanitizeHistory(value) {
    if (!value || !Array.isArray(value.entries)) return null;
    const entries = value.entries.slice(-MAX_RESTORED_ENTRIES).flatMap((entry) => {
      const url = String(entry?.url ?? "");
      if (!/^https?:/i.test(url)) return [];
      const pageState = typeof entry.pageState === "string" && entry.pageState.length <= MAX_PAGE_STATE_CHARS ? entry.pageState : undefined;
      return [{ url: url.slice(0, 8192), title: String(entry.title ?? "").slice(0, 300), ...(pageState ? { pageState } : {}) }];
    });
    if (!entries.length) return null;
    const offset = Math.max(0, value.entries.length - MAX_RESTORED_ENTRIES);
    const index = Math.min(Math.max(Number(value.index) - offset || 0, 0), entries.length - 1);
    return { entries, index };
  }

  function isPendingAttach(ownerId, params = {}) {
    if (!BROWSER_PARTITIONS.has(params.partition)) return false;
    const requested = String(params.src ?? "");
    for (const session of sessions.values()) {
      if (session.ownerId === ownerId && session.pendingSrc && session.pendingSrc === requested
          && PARTITIONS[session.kind] === params.partition) return true;
    }
    return requested === "about:blank";
  }

  // Until a guest is adopted it may not open anything.
  function hardenGuest(guest) {
    guest.setWindowOpenHandler(() => ({ action: "deny" }));
  }

  function adopt(event, session, webContentsId) {
    const guest = webContentsApi.fromId(Number(webContentsId));
    if (!guest || guest.isDestroyed() || guest.hostWebContents !== event.sender || guest.getType() !== "webview") {
      throw new Error("Browser view is unavailable.");
    }
    if (!sameProfile(guest.session, profile(session.kind))) throw new Error("Browser view uses the wrong profile.");
    if (session.contents === guest) return;
    if (session.contents) throw new Error("Browser session already has a view.");
    session.contents = guest;
    session.pendingSrc = "";
    if (typeof guest.setBackgroundColor === "function") guest.setBackgroundColor("#ffffff");
    guest.once("destroyed", () => closeSession(session));
    configure(session);
    session.ownerZoomFactor = session.owner.getZoomFactor();
    // The first page loads before the view is adopted, so apply its site
    // settings and history entry now.
    const current = guest.getURL();
    if (current && current !== "about:blank") {
      session.localNavigation = current.startsWith("file:");
      applySiteZoom(session, current);
      recordHistory(session, current);
    } else {
      syncZoom(session);
    }
    if (session.pendingHistory) {
      const history = session.pendingHistory;
      session.pendingHistory = null;
      void restoreHistory(session, guest, history);
    }
    emitState(session);
  }

  // Chromium only restores history into a view that has not navigated yet,
  // and the view has already shown about:blank. When restoring fails, still
  // open the page the tab was showing.
  async function restoreHistory(session, guest, history) {
    const target = history.entries[history.index].url;
    try {
      const restore = guest.navigationHistory?.restore;
      if (typeof restore !== "function") throw new Error("History restore is unavailable.");
      await restore.call(guest.navigationHistory, history);
      if (!guest.isDestroyed() && /^(?:about:blank)?$/.test(guest.getURL())) throw new Error("History was not restored.");
    } catch {
      if (guest.isDestroyed() || !sessions.has(session.id)) return;
      await run(session, () => guest.loadURL(target));
    }
  }

  function closeSession(session) {
    if (!session || !sessions.has(session.id)) return;
    const contents = liveContents(session);
    const restoreOwnerFocus = contents?.isFocused() === true;
    sessions.delete(session.id);
    session.closed = true;
    session.contents = null;
    for (const [requestId, prompt] of prompts) {
      if (prompt.session === session) settlePrompt(requestId, null);
    }
    for (const [window, owner] of childWindows) {
      if (owner === session && !window.isDestroyed()) window.close();
    }
    if (restoreOwnerFocus && !session.owner.isDestroyed()) session.owner.focus();
  }

  function registerOwnerCleanup(owner) {
    if (ownersWithCleanup.has(owner)) return;
    ownersWithCleanup.add(owner);
    const ownerId = owner.id;
    owner.once("destroyed", () => closeForOwner(ownerId));
  }

  function closeForOwner(ownerId) {
    for (const session of [...sessions.values()]) if (session.ownerId === ownerId) closeSession(session);
  }

  function closeAll() {
    for (const session of [...sessions.values()]) closeSession(session);
    store.flush();
  }

  function liveContents(session) {
    const contents = session?.contents;
    return contents && !contents.isDestroyed() ? contents : null;
  }

  function sessionForContents(contents) {
    if (!contents) return null;
    for (const session of sessions.values()) if (session.contents === contents) return session;
    return null;
  }

  function ownedSession(event, id) {
    const session = typeof id === "string" ? sessions.get(id) : null;
    if (!session || session.ownerId !== event.sender.id) throw new Error("Browser session was not found.");
    return session;
  }

  // ---------------------------------------------------------------------------
  // Guest wiring

  function configure(session) {
    const contents = session.contents;
    const update = () => emitState(session);
    contents.on("input-event", (_event, input) => {
      if (USER_INPUT_TYPES.has(input?.type)) session.lastUserInputAt = Date.now();
    });
    contents.on("found-in-page", (_event, result) => {
      if (result.requestId !== session.findRequestId) return;
      session.findResult = { matches: result.matches, activeMatchOrdinal: result.activeMatchOrdinal, finalUpdate: result.finalUpdate };
      update();
    });
    contents.on("did-start-navigation", (event, _url, legacySameDocument, legacyMainFrame) => {
      const mainFrame = event?.isMainFrame ?? legacyMainFrame;
      const sameDocument = event?.isSameDocument ?? legacySameDocument;
      if (!mainFrame || sameDocument) return;
      // Prompts belong to the page that asked.
      for (const [requestId, pending] of prompts) {
        if (pending.session === session && pending.kind === "permission") settlePrompt(requestId, null);
      }
    });
    contents.on("did-start-loading", () => {
      session.error = "";
      session.loadError = null;
      session.crashed = null;
      session.findResult = null;
      session.findRequestId = null;
      update();
    });
    contents.on("did-stop-loading", update);
    contents.on("did-navigate", (_event, url) => {
      session.favicon = "";
      session.localNavigation = String(url).startsWith("file:");
      if (!session.certificateErrorPending) session.certificateError = null;
      session.certificateErrorPending = false;
      session.certificateException = certificateExceptionFor(url);
      session.blockedPopups = [];
      applySiteZoom(session, url);
      recordHistory(session, url);
      update();
    });
    contents.on("did-navigate-in-page", (_event, url, isMainFrame) => {
      if (isMainFrame) recordHistory(session, url);
      update();
    });
    contents.on("page-title-updated", (_event, title) => {
      if (session.kind === "user") store.updateTitle(contents.getURL(), title);
      update();
    });
    contents.on("page-favicon-updated", (_event, favicons) => {
      session.favicon = Array.isArray(favicons) ? String(favicons.find((item) => /^(?:https?:|data:image\/)/i.test(String(item))) || "") : "";
      update();
    });
    contents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return;
      session.error = `${errorDescription}: ${validatedUrl}`;
      session.loadError = { code: errorCode, url: validatedUrl, ...classifyLoadError(errorCode, errorDescription) };
      if (isCertificateErrorCode(errorCode) && !session.certificateError) {
        session.certificateError = { url: validatedUrl, host: hostOf(validatedUrl), error: errorDescription };
      }
      update();
    });
    contents.on("render-process-gone", (_event, details) => {
      session.crashed = { reason: details?.reason || "crashed" };
      session.error = `Browser renderer stopped: ${session.crashed.reason}`;
      update();
    });
    contents.on("unresponsive", () => { session.unresponsive = true; update(); });
    contents.on("responsive", () => { session.unresponsive = false; update(); });
    contents.on("audio-state-changed", (event) => {
      session.audible = typeof event?.audible === "boolean" ? event.audible : contents.isCurrentlyAudible();
      update();
    });
    // Pages with unsaved work ask before leaving, as in any browser.
    contents.on("will-prevent-unload", (event) => {
      if (session.agent && !session.agent.shared) { event.preventDefault(); return; }
      const owner = getMainWindow();
      const choice = dialog.showMessageBoxSync(owner && !owner.isDestroyed() ? owner : undefined, {
        type: "question",
        buttons: ["Leave", "Stay"],
        defaultId: 1,
        cancelId: 1,
        message: "Leave this site?",
        detail: "Changes you made may not be saved.",
      });
      if (choice === 0) event.preventDefault();
    });
    const guardNavigation = (event, url) => {
      if (!isAllowedNavigation(session, url)) event.preventDefault();
    };
    contents.on("will-navigate", guardNavigation);
    contents.on("will-redirect", guardNavigation);
    contents.on("ipc-message", (event, channel, payload) => {
      if (event.senderFrame !== contents.mainFrame) return;
      if (channel === "gofer:browser-link-clicked") {
        openPageLink(session, payload?.url, { background: payload?.disposition === "background" });
      }
      if (channel === "gofer:browser-zoom" && (payload?.direction === 1 || payload?.direction === -1)) {
        changePageZoom(session, payload.direction);
      }
      if (channel === "gofer:browser-navigation" && payload?.action === "back" && contents.navigationHistory.canGoBack()) {
        contents.navigationHistory.goBack();
      }
    });
    contents.on("before-input-event", (event, input) => {
      const action = browserSessionShortcutAction(session, input, platform);
      if (!action) return;
      if (action === "stop-loading") {
        if (!contents.isLoading()) return;
        event.preventDefault();
        contents.stop();
        return;
      }
      event.preventDefault();
      runShortcut(session, action);
    });
    contents.setWindowOpenHandler((details) => openWindow(session, details));
    contents.on("did-create-window", (window) => hardenChildWindow(session, window));
    contents.on("context-menu", (_event, params) => showContextMenu(session, params));
  }

  function runShortcut(session, action) {
    const contents = liveContents(session);
    if (!contents) return;
    if (action === "chord-pending") return;
    if (action.startsWith("command:")) {
      emitCommand(session, "application-shortcut", { commandId: action.slice("command:".length) });
      return;
    }
    switch (action) {
      case "reload": reload(session, false); return;
      case "reload-hard": reload(session, true); return;
      case "back": if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack(); return;
      case "forward": if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward(); return;
      case "home": void navigate(session, policy.homepage || RATICODE_HOME_URL); return;
      case "zoom-in": changePageZoom(session, 1); return;
      case "zoom-out": changePageZoom(session, -1); return;
      case "zoom-reset": changePageZoom(session, 0); return;
      case "toggle-devtools": toggleDevTools(session); return;
      case "print": void print(session); return;
      default: emitCommand(session, action);
    }
  }

  function reload(session, hard) {
    const contents = liveContents(session);
    if (!contents) return;
    // A failed load leaves nothing to reload; retry its address instead.
    const failed = session.loadError?.url || (!contents.getURL() || contents.getURL() === "about:blank" ? session.initialUrl : "");
    session.crashed = null;
    session.loadError = null;
    if (failed && failed !== "about:blank") {
      void run(session, () => contents.loadURL(failed));
      return;
    }
    if (hard) contents.reloadIgnoringCache(); else contents.reload();
  }

  function toggleDevTools(session) {
    const contents = liveContents(session);
    if (!contents) return;
    if (contents.isDevToolsOpened()) contents.closeDevTools();
    else contents.openDevTools({ mode: "detach" });
  }

  // Chromium's print dialog fails, and can crash, when the system has no
  // printers (no CUPS on Linux). Check first and offer a PDF instead.
  async function print(session) {
    const contents = liveContents(session);
    if (!contents) return;
    let printers = [];
    try {
      printers = typeof contents.getPrintersAsync === "function" ? await contents.getPrintersAsync() : [];
    } catch (error) {
      log("warn", `Could not list printers: ${error.message}`);
    }
    if (!printers.length) {
      const choice = await showMessage({
        type: "info",
        message: "No printers are set up",
        detail: "Raticode could not find a printer on this computer. Set one up in your system settings (CUPS on Linux), or save the page as a PDF.",
        buttons: ["Save as PDF…", "Cancel"],
        defaultId: 0,
        cancelId: 1,
      });
      if (choice === 0) await saveAsPdf(session);
      return;
    }
    if (!liveContents(session)) return;
    try {
      contents.print({ printBackground: true }, (success, reason) => {
        if (!success && reason && !/cancel/i.test(reason)) {
          log("warn", `Print failed: ${reason}`);
          void showMessage({ type: "error", message: "Could not print this page", detail: `${reason}. You can save the page as a PDF instead.`, buttons: ["OK"] });
        }
      });
    } catch (error) {
      log("warn", `Print failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not print this page", detail: error.message, buttons: ["OK"] });
    }
  }

  async function showMessage(options) {
    const owner = getMainWindow();
    try {
      const result = owner && !owner.isDestroyed() ? await dialog.showMessageBox(owner, options) : await dialog.showMessageBox(options);
      return result?.response ?? -1;
    } catch (error) {
      log("warn", `Could not show a message: ${error.message}`);
      return -1;
    }
  }

  async function chooseSavePath(options) {
    const owner = getMainWindow();
    const result = owner && !owner.isDestroyed() ? await dialog.showSaveDialog(owner, options) : await dialog.showSaveDialog(options);
    return result.canceled || !result.filePath ? "" : result.filePath;
  }

  function isAllowedNavigation(session, value) {
    let url;
    try { url = new URL(value); } catch { return false; }
    if (session.agent && !session.agent.shared) {
      if (url.protocol === "about:" || url.protocol === "blob:") return true;
      return remSiteAllowed(url.toString(), policy);
    }
    if (["http:", "https:", "about:", "blob:"].includes(url.protocol)) return true;
    // App links such as mailto: continue to Chromium, which asks the user first.
    if (policyRules.EXTERNAL_PROTOCOLS.has(url.protocol) && session.kind === "user") return true;
    if (url.protocol !== "file:" || session.kind !== "preview") return false;
    try {
      resolvePath(fileURLToPath(url), { grantId: session.grantId, mustExist: true });
      return true;
    } catch {
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Links, tabs and popups

  function openPageLink(session, value, { background = false } = {}) {
    if (session.owner.isDestroyed()) return;
    let url;
    try { url = new URL(value); } catch { return; }
    if (url.protocol === "file:") {
      // Local previews hand links to the studio, which checks the destination's
      // own path grant and reports missing files. Never grant remote pages this route.
      if (session.kind !== "preview" || !/^file:/i.test(liveContents(session)?.getURL() || "")) return;
      session.owner.send("gofer:browser-open-file", { href: url.toString() });
      return;
    }
    if (!["http:", "https:"].includes(url.protocol)) return;
    if (!allowPageTab(session)) {
      log("warn", "Blocked a burst of new tabs from one page.");
      return;
    }
    // Pages and Rem never take the user's view; only user clicks may.
    const foreground = !background && !session.agent && Date.now() - session.lastUserInputAt <= policyRules.USER_ACTIVATION_MS;
    session.owner.send("gofer:browser-open-tab", { url: url.toString(), background: !foreground });
  }

  function openWindow(session, details = {}) {
    const contents = liveContents(session);
    if (!contents) return { action: "deny" };
    if (session.kind === "preview" && /^file:/i.test(contents.getURL())) {
      openPageLink(session, details.url, { background: details.disposition === "background-tab" });
      return { action: "deny" };
    }
    const origin = siteKey(contents.getURL());
    const decision = popupDecision({
      url: details.url,
      disposition: details.disposition,
      features: details.features,
      postBody: details.postBody,
      agent: Boolean(session.agent && !session.agent.shared),
      lastUserInputAt: session.lastUserInputAt,
      popupsAllowed: store.site(origin).popups === "allow",
    });
    if (decision.kind === "same-tab") {
      void run(session, () => contents.loadURL(details.url));
      return { action: "deny" };
    }
    if (decision.kind === "blocked") {
      session.blockedPopups = [...session.blockedPopups, { url: String(details.url || "about:blank").slice(0, 2048) }].slice(-5);
      emitState(session);
      return { action: "deny" };
    }
    if (decision.kind === "tab") {
      openPageLink(session, details.url, { background: decision.background });
      return { action: "deny" };
    }
    if (decision.kind === "window") {
      const owner = getMainWindow();
      return {
        action: "allow",
        outlivesOpener: false,
        overrideBrowserWindowOptions: {
          width: decision.width,
          height: decision.height,
          autoHideMenuBar: true,
          backgroundColor: "#ffffff",
          ...(owner && !owner.isDestroyed() ? { parent: owner } : {}),
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            webSecurity: true,
            allowRunningInsecureContent: false,
          },
        },
      };
    }
    return { action: "deny" };
  }

  // Sign-in popups keep window.opener, but nothing else: no further popups,
  // web-only navigation, and the site's origin always in the title bar.
  function hardenChildWindow(session, window) {
    childWindows.set(window, session);
    window.once("closed", () => childWindows.delete(window));
    window.setMenuBarVisibility(false);
    const contents = window.webContents;
    const title = () => {
      if (window.isDestroyed()) return;
      const host = hostOf(contents.getURL());
      window.setTitle(`${host || "Sign in"} — ${contents.getTitle() || "Raticode"}`);
    };
    contents.on("page-title-updated", (event) => { event.preventDefault(); title(); });
    contents.on("did-navigate", title);
    contents.setWindowOpenHandler(({ url }) => {
      openPageLink(session, url, { background: false });
      return { action: "deny" };
    });
    const guard = (event, url) => {
      if (!/^(?:https?:|about:blank|blob:)/i.test(String(url))) event.preventDefault();
    };
    contents.on("will-navigate", guard);
    contents.on("will-redirect", guard);
  }

  function openBlockedPopup(session, index) {
    const popup = session.blockedPopups[Number(index)];
    if (!popup) return;
    session.blockedPopups = session.blockedPopups.filter((_item, position) => position !== Number(index));
    if (/^https?:/i.test(popup.url)) session.owner.send("gofer:browser-open-tab", { url: popup.url, background: false });
    emitState(session);
  }

  // ---------------------------------------------------------------------------
  // Zoom

  function applySiteZoom(session, url) {
    const origin = siteKey(url);
    session.pageZoomFactor = clampZoom(origin ? store.site(origin).zoom || 1 : 1);
    syncZoom(session);
  }

  function changePageZoom(session, direction) {
    const contents = liveContents(session);
    if (!contents) return;
    session.pageZoomFactor = direction === 0 ? 1 : nextZoomLevel(session.pageZoomFactor, direction);
    const origin = siteKey(contents.getURL());
    if (origin && session.kind !== "rem") store.updateSite(origin, { zoom: session.pageZoomFactor === 1 ? null : session.pageZoomFactor });
    syncZoom(session);
    emitState(session);
  }

  function ownerZoom(event) {
    const ownerZoomFactor = event.sender.getZoomFactor();
    for (const session of sessions.values()) {
      if (session.ownerId !== event.sender.id || session.ownerZoomFactor === ownerZoomFactor) continue;
      session.ownerZoomFactor = ownerZoomFactor;
      syncZoom(session);
    }
    return { zoomFactor: ownerZoomFactor };
  }

  function syncZoom(session) {
    const contents = liveContents(session);
    if (!contents) return;
    contents.setZoomFactor(browserContentZoomFactor(session.ownerZoomFactor, session.pageZoomFactor));
  }

  // ---------------------------------------------------------------------------
  // History

  function recordHistory(session, url) {
    if (session.kind !== "user" || !/^https?:/i.test(String(url))) return;
    const key = String(url).split("#", 1)[0];
    if (key === session.lastRecordedUrl) return;
    session.lastRecordedUrl = key;
    store.recordVisit({ url, title: liveContents(session)?.getTitle() || "", typed: session.pendingTyped });
    session.pendingTyped = false;
  }

  function historyEntries(session) {
    const contents = liveContents(session);
    if (!contents) return { entries: [], index: 0 };
    const nav = contents.navigationHistory;
    const entries = typeof nav.getAllEntries === "function"
      ? nav.getAllEntries()
      : Array.from({ length: nav.length() }, (_item, index) => nav.getEntryAtIndex(index));
    return {
      index: typeof nav.getActiveIndex === "function" ? nav.getActiveIndex() : entries.length - 1,
      entries: entries.map((entry) => ({
        url: String(entry?.url ?? ""),
        title: String(entry?.title ?? ""),
        ...(typeof entry?.pageState === "string" && entry.pageState.length <= MAX_PAGE_STATE_CHARS ? { pageState: entry.pageState } : {}),
      })),
    };
  }

  // ---------------------------------------------------------------------------
  // Permissions

  async function requestPermission(contents, permission, details, kind) {
    const session = sessionForContents(contents);
    const agent = kind === "rem" || Boolean(session?.agent && !session.agent.shared);
    if (!session) return policyRules.AUTO_ALLOWED_PERMISSIONS.has(permission) && kind === "user" && permission !== "pointerLock";
    const origin = siteKey(details.requestingUrl || contents.getURL());
    const stored = store.site(origin).permissions || {};
    const decision = permissionDecision({ permission, details, stored, agent });
    if (decision !== "prompt") return decision === "allow";
    const keys = permission === "openExternal"
      ? [`openExternal:${externalProtocolOf(details)}`]
      : permissionKeys(permission, details);
    const response = await prompt(session, "permission", {
      origin,
      permissions: keys.map((key) => ({
        key,
        label: key.startsWith("openExternal:") ? `Open ${key.slice("openExternal:".length).replace(/:$/, "")} links with an app on your computer` : permissionLabel(key),
      })),
    });
    if (response === "allow" || response === "block") {
      for (const key of keys) store.setPermission(origin, key, response);
    }
    return response === "allow" || response === "allow-once";
  }

  function checkPermission(contents, permission, details, kind) {
    if (policyRules.AUTO_ALLOWED_PERMISSIONS.has(permission)) return kind === "user" || permission === "clipboard-sanitized-write";
    if (kind === "rem") return false;
    const session = sessionForContents(contents);
    if (!session) return false;
    const origin = siteKey(details.requestingUrl || details.securityOrigin || contents?.getURL?.() || "");
    const stored = store.site(origin).permissions || {};
    const keys = permission === "media" ? permissionKeys("media", { mediaType: details.mediaType }) : [permission];
    return keys.length > 0 && keys.every((key) => stored[key] === "allow");
  }

  // ---------------------------------------------------------------------------
  // Prompts shown inside the tab: permissions, sign-in, client certificates
  // and Rem's approval requests. Never carry secrets in state.

  function prompt(session, kind, payload) {
    if (!session || session.owner.isDestroyed()) return Promise.resolve(null);
    const requestId = crypto.randomUUID();
    return new Promise((resolve) => {
      const timer = setTimeout(() => settlePrompt(requestId, null), PROMPT_TIMEOUT_MS);
      timer.unref?.();
      prompts.set(requestId, { session, kind, payload, resolve, timer, createdAt: Date.now() });
      emitState(session);
      session.owner.send("gofer:browser-prompt", { id: session.id, clientId: session.clientId, requestId, kind, ...payload });
    });
  }

  function settlePrompt(requestId, response) {
    const pending = prompts.get(requestId);
    if (!pending) return false;
    prompts.delete(requestId);
    clearTimeout(pending.timer);
    pending.resolve(response);
    if (sessions.has(pending.session.id)) emitState(pending.session);
    return true;
  }

  function promptsFor(session) {
    return [...prompts.entries()]
      .filter(([, item]) => item.session === session)
      .map(([requestId, item]) => ({ requestId, kind: item.kind, ...item.payload }));
  }

  // ---------------------------------------------------------------------------
  // Certificates, HTTP authentication and client certificates (app events)

  function certificateExceptionFor(url) {
    const host = hostOf(url);
    return [...certificateExceptions].some((key) => key.startsWith(`${host}|`));
  }

  function handleCertificateError(event, contents, url, error, certificate, callback, isMainFrame = true) {
    const session = sessionForContents(contents);
    if (!session) return false;
    event.preventDefault();
    const key = `${hostOf(url)}|${certificate?.fingerprint || ""}`;
    if (session.kind === "user" && certificateExceptions.has(key)) {
      session.certificateException = true;
      callback(true);
      return true;
    }
    if (isMainFrame) {
      session.certificateErrorPending = true;
      session.certificateError = {
        url,
        host: hostOf(url),
        error: String(error || "Certificate error"),
        fingerprint: certificate?.fingerprint || "",
        ...certificateSummary(certificate),
        canProceed: session.kind === "user",
      };
    }
    callback(false);
    return true;
  }

  function allowCertificate(session) {
    const failure = session.certificateError;
    if (!failure || session.kind !== "user" || !failure.fingerprint) throw new Error("There is no certificate to accept for this page.");
    certificateExceptions.add(`${failure.host}|${failure.fingerprint}`);
    session.certificateError = null;
    session.certificateException = true;
    const contents = liveContents(session);
    if (contents) void run(session, () => contents.loadURL(failure.url));
  }

  function handleLogin(event, contents, details, authInfo, callback) {
    const session = sessionForContents(contents);
    if (!session) return false;
    event.preventDefault();
    // Rem never receives or types the user's credentials.
    if (session.kind !== "user") {
      callback();
      return true;
    }
    void prompt(session, "auth", {
      host: String(authInfo?.host || hostOf(details?.url) || ""),
      realm: String(authInfo?.realm || "").slice(0, 200),
      isProxy: authInfo?.isProxy === true,
      scheme: String(authInfo?.scheme || ""),
    }).then((response) => {
      if (response && typeof response.username === "string") callback(response.username, String(response.password ?? ""));
      else callback();
    });
    return true;
  }

  function handleSelectClientCertificate(event, contents, url, list, callback) {
    const session = sessionForContents(contents);
    if (!session) return false;
    event.preventDefault();
    const host = hostOf(url);
    if (session.kind !== "user" || !Array.isArray(list) || !list.length) {
      callback();
      return true;
    }
    const remembered = clientCertificateChoices.get(host);
    const match = remembered && list.find((item) => item.fingerprint === remembered);
    if (match) {
      callback(match);
      return true;
    }
    void prompt(session, "client-certificate", {
      host,
      certificates: list.slice(0, 20).map((item, index) => ({ index, ...certificateSummary(item) })),
    }).then((response) => {
      const chosen = Number.isInteger(response?.index) ? list[response.index] : null;
      if (chosen) clientCertificateChoices.set(host, chosen.fingerprint);
      if (chosen) callback(chosen); else callback();
    });
    return true;
  }

  // ---------------------------------------------------------------------------
  // Downloads

  function handleDownload(event, item, contents, kind) {
    const session = sessionForContents(contents);
    const byRem = kind === "rem" || Boolean(session?.agent && !session.agent.shared);
    const filename = sanitizeFilename(item.getFilename());
    if (byRem && policy.remDownloads !== "allow") {
      event.preventDefault();
      if (session) {
        session.lastBlockedDownload = filename;
        recordAgentActivity(session, { action: "download blocked", detail: filename, at: Date.now() });
      }
      return;
    }
    const id = crypto.randomUUID();
    const dangerous = isDangerousDownload(filename);
    const directory = app.getPath("downloads");
    let finalPath = "";
    if (dangerous) {
      fs.mkdirSync(quarantineDir, { recursive: true, mode: 0o700 });
      item.setSavePath(path.join(quarantineDir, `${id}.download`));
    } else if (policy.askWhereToSave && !byRem) {
      item.setSaveDialogOptions({ defaultPath: path.join(directory, filename), title: "Save download" });
    } else {
      finalPath = uniqueDownloadPath(directory, filename, (candidate) => fs.existsSync(candidate) || [...downloads.values()].some((value) => value.entry.path === candidate));
      item.setSavePath(finalPath);
    }
    const entry = {
      id,
      url: String(item.getURL()).slice(0, 4096),
      filename,
      path: finalPath,
      mimeType: item.getMimeType(),
      state: "progressing",
      paused: false,
      receivedBytes: 0,
      totalBytes: item.getTotalBytes(),
      startedAt: Date.now(),
      dangerous,
      byRem,
    };
    downloads.set(id, { item, entry });
    store.upsertDownload(entry);
    emitDownloads();
    let lastEmit = 0;
    item.on("updated", (_event, state) => {
      entry.state = state === "interrupted" ? "interrupted" : "progressing";
      entry.paused = item.isPaused();
      entry.receivedBytes = item.getReceivedBytes();
      entry.totalBytes = item.getTotalBytes();
      if (!entry.path && !dangerous) entry.path = item.getSavePath();
      const time = Date.now();
      if (time - lastEmit > 250) {
        lastEmit = time;
        store.upsertDownload(entry);
        emitDownloads();
      }
    });
    item.once("done", (_event, state) => {
      entry.receivedBytes = item.getReceivedBytes();
      entry.paused = false;
      if (!entry.path && !dangerous) entry.path = item.getSavePath();
      entry.state = state === "completed" ? (dangerous ? "quarantined" : "completed") : state;
      entry.finishedAt = Date.now();
      if (dangerous && state === "completed") entry.quarantinePath = item.getSavePath();
      store.upsertDownload(entry);
      emitDownloads();
    });
  }

  function downloadAction(action, id) {
    const live = downloads.get(id);
    const entry = live?.entry || store.listDownloads().find((item) => item.id === id);
    if (!entry && action !== "clear") throw new Error("That download is no longer listed.");
    switch (action) {
      case "pause": live?.item.pause(); break;
      case "resume": if (live?.item.canResume()) live.item.resume(); break;
      case "cancel": live?.item.cancel(); break;
      case "open":
        if (entry.state !== "completed" || !entry.path) throw new Error("Only finished downloads can be opened.");
        void shell.openPath(entry.path);
        break;
      case "show":
        if (entry.path && fs.existsSync(entry.path)) shell.showItemInFolder(entry.path);
        else throw new Error("The file was moved or deleted.");
        break;
      case "keep": {
        if (entry.state !== "quarantined" || !entry.quarantinePath) throw new Error("This download is not waiting for review.");
        const target = uniqueDownloadPath(app.getPath("downloads"), entry.filename, fs.existsSync);
        moveFile(entry.quarantinePath, target);
        Object.assign(entry, { state: "completed", path: target, quarantinePath: "", dangerous: false, kept: true });
        store.upsertDownload(entry);
        break;
      }
      case "discard":
        if (entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
        Object.assign(entry, { state: "cancelled", quarantinePath: "" });
        store.upsertDownload(entry);
        break;
      case "retry": {
        const owner = getMainWindow();
        if (!/^https?:/i.test(entry.url) || !owner) throw new Error("This download cannot be retried.");
        const userTab = [...sessions.values()].find((item) => item.kind === "user" && liveContents(item));
        if (!userTab) throw new Error("Open a browser tab to retry this download.");
        liveContents(userTab).downloadURL(entry.url);
        store.removeDownload(id);
        break;
      }
      case "remove":
        if (entry.state === "progressing") live?.item.cancel();
        if (entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
        downloads.delete(id);
        store.removeDownload(id);
        break;
      case "clear":
        for (const item of store.listDownloads()) {
          if (item.state === "progressing" || item.state === "quarantined") continue;
          downloads.delete(item.id);
        }
        store.clearDownloads();
        break;
      default:
        throw new Error(`Unknown download action: ${action}`);
    }
    emitDownloads();
    return { downloads: store.listDownloads() };
  }

  function emitDownloads() {
    const owner = getMainWindow();
    if (owner && !owner.isDestroyed()) owner.webContents.send("gofer:browser-downloads", { downloads: store.listDownloads() });
  }

  // ---------------------------------------------------------------------------
  // Context menu

  function showContextMenu(session, params) {
    const contents = liveContents(session);
    if (!contents) return;
    const template = [];
    const separator = () => { if (template.length && template.at(-1).type !== "separator") template.push({ type: "separator" }); };
    if (params.misspelledWord) {
      for (const suggestion of (params.dictionarySuggestions || []).slice(0, 5)) {
        template.push({ label: suggestion, click: () => contents.replaceMisspelling(suggestion) });
      }
      if (!params.dictionarySuggestions?.length) template.push({ label: "No spelling suggestions", enabled: false });
      template.push({ label: "Add to dictionary", click: () => contents.session.addWordToSpellCheckerDictionary(params.misspelledWord) });
      separator();
    }
    if (params.linkURL && /^https?:/i.test(params.linkURL)) {
      template.push(
        { label: "Open link in new tab", click: () => openPageLink(session, params.linkURL, { background: true }) },
        { label: "Open link in default browser", click: () => void shell.openExternal(params.linkURL) },
        { label: "Copy link address", click: () => copyText(params.linkURL) },
      );
      separator();
    }
    if (params.mediaType === "image" && params.srcURL) {
      template.push(
        { label: "Save image as…", click: () => contents.downloadURL(params.srcURL) },
        { label: "Copy image", click: () => contents.copyImageAt(params.x, params.y) },
        { label: "Copy image address", click: () => copyText(params.srcURL) },
      );
      separator();
    }
    if (params.isEditable) {
      template.push({ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" });
      separator();
    } else if (params.selectionText) {
      const text = params.selectionText.trim().replace(/\s+/g, " ");
      template.push(
        { role: "copy" },
        { label: `Search for “${text.length > 30 ? `${text.slice(0, 29)}…` : text}”`, click: () => openPageLink(session, policy.searchUrl.replace("{query}", encodeURIComponent(text.slice(0, 500))), { background: false }) },
      );
      separator();
    }
    if (session.kind !== "preview") {
      template.push({ label: "Ask Rem about this", click: () => emitCommand(session, "ask-rem", { selection: String(params.selectionText || "").slice(0, 20000) }) });
      separator();
    }
    template.push(
      { label: "Back", enabled: contents.navigationHistory.canGoBack(), click: () => contents.navigationHistory.goBack() },
      { label: "Forward", enabled: contents.navigationHistory.canGoForward(), click: () => contents.navigationHistory.goForward() },
      { label: "Reload", click: () => reload(session, false) },
      { type: "separator" },
      ...(session.kind === "user" ? [{ label: store.bookmarkFor(contents.getURL()) ? "Remove bookmark" : "Bookmark this page", click: () => toggleBookmark(session) }] : []),
      { label: "Save page as…", click: () => void savePage(session) },
      { label: "Print…", click: () => void print(session) },
      { label: "Save as PDF…", click: () => void saveAsPdf(session) },
      { type: "separator" },
      { label: "Inspect", click: () => contents.inspectElement(params.x, params.y) },
    );
    const owner = getMainWindow();
    Menu.buildFromTemplate(template).popup(owner && !owner.isDestroyed() ? { window: owner } : {});
  }

  async function savePage(session) {
    const contents = liveContents(session);
    if (!contents) return;
    try {
      const filePath = await chooseSavePath({
        title: "Save page",
        defaultPath: path.join(app.getPath("downloads"), `${sanitizeFilename(contents.getTitle() || "page")}.html`),
        filters: [{ name: "Web page", extensions: ["html", "htm"] }],
      });
      if (!filePath || !liveContents(session)) return;
      await contents.savePage(filePath, "HTMLComplete");
    } catch (error) {
      log("warn", `Save page failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not save this page", detail: error.message, buttons: ["OK"] });
    }
  }

  async function saveAsPdf(session) {
    const contents = liveContents(session);
    if (!contents) return;
    try {
      const filePath = await chooseSavePath({
        title: "Save as PDF",
        defaultPath: path.join(app.getPath("downloads"), `${sanitizeFilename(contents.getTitle() || "page")}.pdf`),
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      });
      if (!filePath || !liveContents(session)) return;
      const data = await withTimeout(contents.printToPDF({ printBackground: true, preferCSSPageSize: true }), PDF_TIMEOUT_MS, "The page did not finish rendering a PDF within a minute.");
      await fs.promises.writeFile(filePath, data);
      shell.showItemInFolder(filePath);
    } catch (error) {
      log("warn", `Save as PDF failed: ${error.message}`);
      await showMessage({ type: "error", message: "Could not save this page as a PDF", detail: error.message, buttons: ["OK"] });
    }
  }

  function toggleBookmark(session) {
    const contents = liveContents(session);
    if (!contents || session.kind !== "user") return null;
    const existing = store.bookmarkFor(contents.getURL());
    if (existing) store.removeBookmark(existing.id);
    else store.addBookmark({ url: contents.getURL(), title: contents.getTitle() });
    emitLibraryChanged("bookmarks");
    emitState(session);
    return !existing;
  }

  // ---------------------------------------------------------------------------
  // Rem

  function openAgentTab({ agent, label }) {
    const mainWindow = getMainWindow();
    if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) {
      throw new Error("The Raticode window is closed. Open Raticode and retry.");
    }
    const owner = mainWindow.webContents;
    const session = baseSession({ id: crypto.randomUUID(), owner, kind: "rem", url: "about:blank", agent: { id: agent, label, shared: false } });
    sessions.set(session.id, session);
    registerOwnerCleanup(owner);
    owner.send("gofer:browser-agent-open", { id: session.id, label, partition: PARTITIONS.rem });
    return session;
  }

  function agentTabs(agent) {
    return [...sessions.values()].filter((item) => item.agent?.id === agent);
  }

  function allAgentTabs() {
    return [...sessions.values()].filter((item) => item.agent);
  }

  function closeAgentTab(session) {
    if (session.agent?.shared) {
      stopSharing(session);
      return;
    }
    closeSession(session);
    if (!session.owner.isDestroyed()) session.owner.send("gofer:browser-agent-close", { id: session.id });
  }

  function recordAgentActivity(session, entry) {
    session.agentLog = [...(session.agentLog || []), entry].slice(-50);
    emitState(session);
  }

  function confirmAgent(session, details) {
    emitCommand(session, "rem-approval", { label: details.label });
    return prompt(session, "rem-confirm", {
      action: String(details.action || ""),
      label: String(details.label || "").slice(0, 200),
      url: String(details.url || "").slice(0, 2048),
      agentLabel: session.agent?.label || "Rem",
    }).then((response) => response === "approve");
  }

  function detachAgent(session) {
    const contents = liveContents(session);
    try { if (contents?.debugger.isAttached()) contents.debugger.detach(); } catch { /* Already detached. */ }
    try { contents?.setBackgroundThrottling(true); } catch { /* View closed. */ }
    for (const [requestId, item] of prompts) {
      if (item.session === session && item.kind === "rem-confirm") settlePrompt(requestId, null);
    }
    session.agent = null;
    session.agentPaused = false;
    session.agentDialog = null;
  }

  function shareWithRem(session, threadId, label = "Rem") {
    if (session.kind !== "user") throw new Error("Only web tabs can be shared with Rem.");
    if (typeof threadId !== "string" || !threadId.trim()) throw new Error("Choose a Rem thread to share this tab with.");
    if (session.agent) throw new Error("This tab is already shared with Rem.");
    session.agent = { id: remOwnerForThread(threadId.trim()), label: String(label || "Rem").slice(0, 60), shared: true };
    session.agentUsedAt = Date.now();
    session.agentLog = [{ action: "shared", detail: "You shared this tab", at: Date.now() }];
    emitState(session);
  }

  function stopSharing(session) {
    if (!session.agent) return;
    detachAgent(session);
    emitState(session);
  }

  // Ask Rem: the page's address, title, selection and readable text.
  async function pageContext(session, selection = "") {
    const contents = liveContents(session);
    if (!contents) throw new Error("Browser view is unavailable.");
    let text = "";
    let pageSelection = selection;
    try {
      const result = await Promise.race([
        contents.executeJavaScriptInIsolatedWorld(PAGE_CONTEXT_WORLD_ID, [{
          code: "({ selection: String(getSelection() || ''), text: (document.body?.innerText || '').slice(0, 40000) })",
        }]),
        new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
      ]);
      text = String(result?.text || "");
      pageSelection = pageSelection || String(result?.selection || "");
    } catch { /* Some pages refuse scripts; the address still helps. */ }
    return { url: contents.getURL(), title: contents.getTitle(), selection: pageSelection.slice(0, 20000), text };
  }

  // ---------------------------------------------------------------------------
  // State

  function sessionState(session, fallbackUrl = "") {
    const fallback = fallbackUrl || session.initialUrl || "about:blank";
    const contents = liveContents(session);
    const base = {
      agent: session.agent ? {
        label: session.agent.label,
        shared: Boolean(session.agent.shared),
        paused: session.agentPaused,
        dialog: session.agentDialog || null,
        log: (session.agentLog || []).slice(-20),
      } : null,
      agentLabel: session.agent?.label || "",
      audible: session.audible,
      blockedPopups: session.blockedPopups,
      certificateError: session.certificateError ? { ...session.certificateError } : null,
      clientId: session.clientId,
      crashed: session.crashed,
      error: session.error,
      favicon: session.favicon || "",
      id: session.id,
      loadError: session.loadError,
      muted: session.muted,
      profile: session.kind,
      prompts: promptsFor(session),
      unresponsive: session.unresponsive,
      zoom: session.pageZoomFactor,
    };
    if (!contents) {
      return {
        ...base,
        ...(sessions.has(session.id) ? { ready: false } : {}),
        canGoBack: false,
        canGoForward: false,
        canOpenExternal: false,
        bookmarked: false,
        error: sessions.has(session.id) ? session.error : session.error || "Browser view is unavailable.",
        loading: sessions.has(session.id) ? !session.error : false,
        security: securityState({ url: fallback }),
        title: "",
        url: session.internalHomeUrl ? RATICODE_HOME_URL : fallback,
      };
    }
    const currentUrl = contents.getURL() || fallback;
    const displayUrl = session.internalHomeUrl && currentUrl === session.internalHomeUrl ? RATICODE_HOME_URL : currentUrl;
    return {
      ...base,
      ready: true,
      bookmarked: session.kind === "user" && Boolean(store.bookmarkFor(currentUrl)),
      canGoBack: contents.navigationHistory.canGoBack(),
      canGoForward: contents.navigationHistory.canGoForward(),
      canOpenExternal: canOpenInExternalBrowser(currentUrl),
      findResult: session.findResult || null,
      loading: contents.isLoading(),
      // Rem is using a shared sign-in on this page.
      remSignIn: Boolean(session.agent && !session.agent.shared && policyRules.matchesAnySite(hostOf(currentUrl), policy.remSignInSites)),
      security: securityState({ url: displayUrl === RATICODE_HOME_URL ? "about:blank" : currentUrl, certificateException: session.certificateException, certificateError: session.certificateError }),
      title: contents.getTitle(),
      url: displayUrl,
    };
  }

  function emitState(session) {
    try {
      if (sessions.get(session.id) === session && liveContents(session) && !session.owner.isDestroyed()) {
        session.owner.send("gofer:browser-state", sessionState(session));
      }
    } catch (error) {
      // Electron can destroy either contents between the checks and the send.
      session.error = error instanceof Error ? error.message : String(error);
    }
  }

  function emitCommand(session, action, details = {}) {
    if (session.owner.isDestroyed()) return;
    if (browserCommandRequiresOwnerFocus(action)) session.owner.focus();
    session.owner.send("gofer:browser-command", { action, clientId: session.clientId, ...details, id: session.id });
  }

  function emitLibraryChanged(kind) {
    const owner = getMainWindow();
    if (owner && !owner.isDestroyed()) owner.webContents.send("gofer:browser-library-changed", { kind });
  }

  async function run(session, operation) {
    try {
      await operation();
    } catch (error) {
      if (/ERR_ABORTED/.test(String(error?.message))) return;
      session.error = error instanceof Error ? error.message : String(error);
      try { emitState(session); } catch { /* No live view to notify. */ }
    }
  }

  function navigate(session, value, { typed = false } = {}) {
    const contents = liveContents(session);
    if (!contents) throw new Error("Browser view is unavailable.");
    const normalizedUrl = normalizeBrowserUrl(value, policy.searchUrl);
    // Typed local paths open as previews in their own profile.
    if (normalizedUrl.startsWith("file:") && session.kind !== "preview") {
      resolvePath(fileURLToPath(normalizedUrl), { mustExist: true });
      session.owner.send("gofer:browser-open-file", { href: normalizedUrl });
      return Promise.resolve();
    }
    if (session.agent && !session.agent.shared && !remSiteAllowed(normalizedUrl, policy) && !normalizedUrl.startsWith("about:")) {
      throw new Error("Rem may not open that site. Change this in Settings > Browser.");
    }
    session.error = "";
    session.loadError = null;
    session.pendingTyped = typed;
    const loadUrl = browserLoadUrl(normalizedUrl);
    session.internalHomeUrl = normalizedUrl === RATICODE_HOME_URL ? loadUrl : "";
    return run(session, () => contents.loadURL(loadUrl));
  }

  // ---------------------------------------------------------------------------
  // IPC

  function browserAction(event, options = {}) {
    // Destruction of a guest can precede the renderer's cleanup IPC.
    if (["close", "adopt", "focus", "set-preferences", "viewport", "prompt-response"].includes(options.action) && !sessions.has(options.id)) {
      return { closed: true, ready: false };
    }
    const session = ownedSession(event, options.id);
    switch (options.action) {
      case "adopt":
        // Rem's tabs are created before the studio picks their tab identity.
        if (session.agent && !session.clientId && typeof options.clientId === "string") session.clientId = options.clientId.slice(0, 200);
        adopt(event, session, options.webContentsId);
        return sessionState(session);
      case "viewport": {
        const width = Number(options.width);
        const height = Number(options.height);
        session.viewSize = Number.isFinite(width) && Number.isFinite(height)
          ? { width: Math.max(0, Math.round(width)), height: Math.max(0, Math.round(height)) }
          : null;
        onViewportChanged(session);
        return { ready: Boolean(liveContents(session)) };
      }
      case "close":
        closeSession(session);
        return { closed: true };
      case "set-preferences":
        if (options.applicationKeybindings) session.applicationKeybindings = sanitizeBrowserKeybindings(options.applicationKeybindings);
        session.openBrowserBinding = typeof options.openBrowserBinding === "string" && options.openBrowserBinding
          ? options.openBrowserBinding.slice(0, 120)
          : session.openBrowserBinding;
        return sessionState(session);
      case "prompt-response": {
        const pending = prompts.get(String(options.requestId || ""));
        if (!pending || pending.session !== session) return { settled: false };
        return { settled: settlePrompt(options.requestId, sanitizePromptResponse(pending.kind, options.response)) };
      }
      default:
        break;
    }
    const contents = liveContents(session);
    if (!contents && options.action === "focus") return sessionState(session);
    if (!contents) throw new Error("Browser view is unavailable.");
    switch (options.action) {
      case "back":
        if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack();
        break;
      case "forward":
        if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward();
        break;
      case "go-to-index": {
        const index = Number(options.index);
        if (Number.isInteger(index) && index >= 0 && index < contents.navigationHistory.length()) {
          contents.navigationHistory.goToIndex(index);
        }
        break;
      }
      case "history-entries":
        return historyEntries(session);
      case "focus":
        contents.focus();
        break;
      case "navigate":
        void navigate(session, options.url, { typed: options.typed === true });
        break;
      case "home":
        void navigate(session, policy.homepage || RATICODE_HOME_URL);
        break;
      case "open-external": {
        const url = contents.getURL();
        if (canOpenInExternalBrowser(url)) void run(session, () => shell.openExternal(url));
        break;
      }
      case "find": {
        const text = typeof options.text === "string" ? options.text.slice(0, 2048) : "";
        if (!text) {
          contents.stopFindInPage("clearSelection");
          session.findResult = null;
          session.findRequestId = null;
        } else {
          session.findRequestId = contents.findInPage(text, { forward: options.forward !== false, findNext: options.newSearch !== false });
        }
        break;
      }
      case "stop-find":
        contents.stopFindInPage("clearSelection");
        session.findResult = null;
        session.findRequestId = null;
        break;
      case "reload":
        reload(session, options.hard === true);
        break;
      case "stop":
        contents.stop();
        break;
      case "zoom":
        changePageZoom(session, options.reset === true ? 0 : Number(options.direction) > 0 ? 1 : -1);
        break;
      case "mute":
        session.muted = options.muted === true;
        contents.setAudioMuted(session.muted);
        break;
      case "toggle-devtools":
        toggleDevTools(session);
        break;
      case "print":
        void print(session);
        break;
      case "save-pdf":
        void saveAsPdf(session);
        break;
      case "save-page":
        void savePage(session);
        break;
      case "bookmark":
        return { ...sessionState(session), bookmarked: toggleBookmark(session) };
      case "allow-certificate":
        allowCertificate(session);
        break;
      case "open-blocked-popup":
        openBlockedPopup(session, options.index);
        break;
      case "allow-popups": {
        const origin = siteKey(contents.getURL());
        if (origin) store.updateSite(origin, { popups: "allow" });
        for (const popup of session.blockedPopups) {
          if (/^https?:/i.test(popup.url)) session.owner.send("gofer:browser-open-tab", { url: popup.url, background: false });
        }
        session.blockedPopups = [];
        break;
      }
      case "dismiss-blocked-popups":
        session.blockedPopups = [];
        break;
      case "site-info":
        return siteInfo(session);
      case "site-permission": {
        const origin = siteKey(contents.getURL());
        if (!origin || typeof options.key !== "string") throw new Error("This page has no site settings.");
        if (options.key === "popups") store.updateSite(origin, { popups: options.value === "allow" ? "allow" : null });
        else store.setPermission(origin, options.key.slice(0, 80), ["allow", "block"].includes(options.value) ? options.value : null);
        return siteInfo(session);
      }
      case "clear-site-data":
        return clearSiteData(siteKey(contents.getURL()), session.kind).then(() => siteInfo(session));
      case "page-context":
        return pageContext(session, typeof options.selection === "string" ? options.selection : "");
      case "share-with-rem":
        shareWithRem(session, options.threadId, options.label);
        break;
      case "stop-sharing":
        stopSharing(session);
        break;
      case "take-over":
        if (!session.agent) break;
        detachAgent(session);
        session.takenOver = true;
        break;
      case "pause-rem":
        if (!session.agent) throw new Error("Rem is not using this tab.");
        session.agentPaused = options.paused === true;
        recordAgentActivity(session, { action: session.agentPaused ? "paused" : "resumed", detail: "by you", at: Date.now() });
        break;
      default:
        throw new Error(`Unknown browser action: ${options.action || "missing"}`);
    }
    // Every listener, including the tab strip, sees the change immediately.
    emitState(session);
    return sessionState(session);
  }

  let onViewportChanged = () => {};

  function siteInfo(session) {
    const contents = liveContents(session);
    const url = contents?.getURL() || "";
    const origin = siteKey(url);
    const host = hostOf(url);
    const settings = origin ? store.site(origin) : {};
    return {
      origin,
      host,
      profile: session.kind,
      security: securityState({ url, certificateException: session.certificateException, certificateError: session.certificateError }),
      certificate: url.startsWith("https:") ? verifiedCertificates.get(host) || null : null,
      permissions: Object.entries(settings.permissions || {}).map(([key, value]) => ({
        key,
        value,
        label: key.startsWith("openExternal:") ? `Open ${key.slice(13).replace(/:$/, "")} links` : permissionLabel(key),
      })),
      popups: settings.popups || "ask",
      zoom: session.pageZoomFactor,
      remSignIn: Boolean(host && policy.remSignInSites.some((site) => host === site || host.endsWith(`.${site}`))),
    };
  }

  async function clearSiteData(origin, kind = "user", cookieHost = "") {
    if (!origin || origin === "file://") return;
    const ses = profile(kind === "rem" ? "rem" : "user");
    const host = cookieHost || hostOf(origin);
    await ses.clearStorageData({ origin });
    clientHints.get(kind === "rem" ? "rem" : "user")?.clear(origin);
    const cookies = await ses.cookies.get({});
    for (const cookie of cookies) {
      if (cookieMatchesSite(cookie.domain, host)) await ses.cookies.remove(cookieUrl(cookie), cookie.name).catch(() => {});
    }
    if (kind !== "rem") store.clearSite(origin);
  }

  async function sitesWithData(kind = "user") {
    const ses = profile(kind === "rem" ? "rem" : "user");
    const cookies = await ses.cookies.get({});
    const counts = new Map();
    for (const cookie of cookies) {
      const domain = String(cookie.domain || "").replace(/^\./, "");
      counts.set(domain, (counts.get(domain) || 0) + 1);
    }
    const settings = kind === "rem" ? [] : store.listSites();
    const rows = new Map();
    for (const [domain, cookieCount] of counts) rows.set(domain, { host: domain, cookies: cookieCount, settings: false });
    for (const item of settings) {
      const host = hostOf(item.origin);
      if (!host) continue;
      rows.set(host, { ...(rows.get(host) || { host, cookies: 0 }), origin: item.origin, settings: true, permissions: item.permissions || {}, zoom: item.zoom || null, popups: item.popups || null });
    }
    return [...rows.values()].sort((a, b) => a.host.localeCompare(b.host));
  }

  async function clearBrowsingData(options = {}) {
    const kind = options.profile === "rem" ? "rem" : "user";
    const ses = profile(kind);
    const range = ["hour", "day", "week", "month", "all"].includes(options.range) ? options.range : "all";
    if (options.history && kind === "user") store.clearHistory(range);
    if (options.downloads) store.clearDownloads();
    if (options.cache) await ses.clearCache();
    if (options.cookies) {
      // Electron cannot clear cookies and site storage by age, so these are
      // always cleared for all time; the UI says so.
      await ses.clearStorageData();
      clientHints.get(kind)?.clear();
      if (kind === "rem" && policy.remSignInSites.length) await syncRemSignIns(policy.remSignInSites, []);
    }
    if (options.siteSettings && kind === "user") store.clearSites();
    for (const session of sessions.values()) emitState(session);
    emitLibraryChanged("all");
    return { cleared: true };
  }

  async function browserLibrary(_event, options = {}) {
    const text = typeof options.text === "string" ? options.text.slice(0, 500) : "";
    switch (options.action) {
      case "suggest":
        return { suggestions: store.suggest(text, 8), inline: store.inlineCompletion(text) };
      case "history":
        return { history: store.searchHistory(text, options.limit || 200) };
      case "history-remove":
        store.removeHistory(options.url);
        emitLibraryChanged("history");
        return { ok: true };
      case "history-clear":
        store.clearHistory(options.range);
        emitLibraryChanged("history");
        return { ok: true };
      case "bookmarks":
        return { bookmarks: store.listBookmarks(text) };
      case "bookmark-add": {
        const bookmark = store.addBookmark({ url: options.url, title: options.title });
        emitLibraryChanged("bookmarks");
        refreshBookmarkState();
        return { bookmark };
      }
      case "bookmark-update": {
        const bookmark = store.updateBookmark(String(options.id || ""), { title: options.title, url: options.url });
        emitLibraryChanged("bookmarks");
        refreshBookmarkState();
        return { bookmark };
      }
      case "bookmark-remove":
        store.removeBookmark(String(options.id || ""));
        emitLibraryChanged("bookmarks");
        refreshBookmarkState();
        return { ok: true };
      case "downloads":
        return { downloads: store.listDownloads() };
      case "download":
        return downloadAction(String(options.command || ""), String(options.id || ""));
      case "sites":
        return { sites: await sitesWithData(options.profile) };
      case "site-clear": {
        const host = typeof options.host === "string" ? options.host : hostOf(options.origin);
        const origins = options.origin ? [String(options.origin)] : [`https://${host}`, `http://${host}`];
        for (const origin of origins) await clearSiteData(origin, options.profile, host);
        emitLibraryChanged("sites");
        return { sites: await sitesWithData(options.profile) };
      }
      case "clear-data":
        return clearBrowsingData(options);
      default:
        throw new Error(`Unknown browser library action: ${options.action || "missing"}`);
    }
  }

  function refreshBookmarkState() {
    for (const session of sessions.values()) if (session.kind === "user") emitState(session);
  }

  function focusedSession() {
    return [...sessions.values()].find((item) => liveContents(item)?.isFocused()) || null;
  }

  return {
    agentTabs,
    allAgentTabs,
    browserAction,
    browserLibrary,
    closeAgentTab,
    closeAll,
    closeForOwner,
    confirmAgent,
    createBrowser,
    emitCommand,
    focusedSession,
    handleCertificateError,
    handleLogin,
    handleSelectClientCertificate,
    hardenGuest,
    install,
    isPendingAttach,
    normalizeUrl: (value) => normalizeBrowserUrl(value, policy.searchUrl),
    openAgentTab,
    ownerZoom,
    policy: () => policy,
    recordAgentActivity,
    setPolicy,
    setViewportListener: (listener) => { onViewportChanged = listener; },
    emitState,
  };
}

// Electron reuses one Session object per partition; the storage path also
// identifies persistent profiles if a different wrapper is ever returned.
function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

function sameProfile(actual, expected) {
  if (!actual || !expected) return false;
  if (actual === expected) return true;
  const actualPath = typeof actual.getStoragePath === "function" ? actual.getStoragePath() : undefined;
  const expectedPath = typeof expected.getStoragePath === "function" ? expected.getStoragePath() : undefined;
  return typeof actualPath === "string" && actualPath.length > 0 && actualPath === expectedPath;
}

function sanitizePromptResponse(kind, response) {
  if (kind === "permission") return ["allow", "allow-once", "block"].includes(response) ? response : null;
  if (kind === "rem-confirm") return response === "approve" ? "approve" : null;
  if (kind === "auth") {
    return response && typeof response.username === "string"
      ? { username: response.username.slice(0, 1024), password: String(response.password ?? "").slice(0, 4096) }
      : null;
  }
  if (kind === "client-certificate") return Number.isInteger(response?.index) ? { index: response.index } : null;
  return null;
}

function certificateSummary(certificate) {
  if (!certificate) return {};
  return {
    subject: String(certificate.subjectName || certificate.subject?.commonName || ""),
    issuer: String(certificate.issuerName || certificate.issuer?.commonName || ""),
    validStart: Number(certificate.validStart) || 0,
    validExpiry: Number(certificate.validExpiry) || 0,
    fingerprint: String(certificate.fingerprint || ""),
  };
}

function cookieUrl(cookie) {
  const host = String(cookie.domain || "").replace(/^\./, "");
  return `${cookie.secure ? "https" : "http"}://${host}${cookie.path || "/"}`;
}

function moveFile(source, target) {
  try {
    fs.renameSync(source, target);
  } catch (error) {
    if (error.code !== "EXDEV") throw error;
    fs.copyFileSync(source, target);
    fs.rmSync(source, { force: true });
  }
}

function sanitizeBrowserKeybindings(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([commandId, binding]) => (
    typeof commandId === "string" && commandId.length <= 120 && typeof binding === "string"
      ? [[commandId, binding.slice(0, 240)]]
      : []
  )));
}

module.exports = {
  createBrowserSessions,
  remOwnerForThread,
  sameProfile,
  sanitizePromptResponse,
};
