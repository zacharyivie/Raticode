// Integrated browser sessions: user tabs, local previews and Rem's tabs.
// The studio renders each tab as a <webview>; this module owns the guest
// web contents, their profiles and every browser policy applied to them.
// When Firefox or Chrome is the chosen web browser, web tabs and Rem's tabs
// run in that browser without a window instead (firefox-contents.cjs,
// chromium-contents.cjs) and the studio draws the frames they stream. Local
// previews always stay on Raticode's own engine.
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
const { FirefoxContents } = require("./firefox-contents.cjs");
const { ChromiumContents, isInternalPage: isChromiumInternalPage } = require("./chromium-contents.cjs");
const { addonSlugFromUrl, createFirefoxManager } = require("./firefox-manager.cjs");
const { watchMedia } = require("./browser-media.cjs");
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
// Google's "Couldn't sign you in · This browser or app may not be secure" page.
const GOOGLE_REJECTED = /^https:\/\/accounts\.google\.com\/(?:v\d+\/)?signin\/rejected/i;
const PDF_TIMEOUT_MS = 60 * 1000;
const SAVE_AS_TIMEOUT_MS = 30 * 1000;
const PAGE_CONTEXT_WORLD_ID = 1918;
const UA_SAMPLE_WORLD_ID = 1919;
const HIGH_ENTROPY_FIELDS = ["architecture", "bitness", "formFactors", "fullVersionList", "model", "platformVersion", "uaFullVersion", "wow64"];
// Client hint headers only apply to potentially trustworthy URLs.
const CLIENT_HINT_URLS = ["https://*/*", "wss://*/*", "http://localhost/*", "http://127.0.0.1/*", "http://*.localhost/*", "ws://localhost/*", "ws://127.0.0.1/*"];
const MAX_RESTORED_ENTRIES = 25;
const MAX_PAGE_STATE_CHARS = 16 * 1024;
const USER_INPUT_TYPES = new Set(["mouseDown", "keyDown", "rawKeyDown", "touchStart", "gestureTap", "char"]);

// The page reports the element it was right-clicked on; keep a bounded copy.
function contextElement(payload) {
  if (!payload || typeof payload !== "object") return null;
  const limits = { tag: 40, path: 500, role: 100, label: 500, alt: 500, title: 500, href: 2000, src: 2000, text: 8000, html: 8000 };
  const element = Object.fromEntries(Object.entries(limits).map(([key, limit]) => [key, typeof payload[key] === "string" ? payload[key].slice(0, limit) : ""]));
  return element.tag ? element : null;
}

// Matches browser_owner() in src/gofer/ui/browser_tools.py.
function remOwnerForThread(threadId) {
  return crypto.createHash("sha256").update(String(threadId)).digest("hex").slice(0, 32);
}

const PRIVATE_CLEANUP_DELAY_MS = 3000;

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
  openWith = null,
  quarantineDir,
  platform = process.platform,
  chromeVersion = process.versions.chrome,
  firefox = null,
  chrome = null,
  // Other Chromium-based browsers from chromium-engine.cjs, by engine name.
  chromium = {},
  nativeImage = null,
  privateCleanupDelayMs = PRIVATE_CLEANUP_DELAY_MS,
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
  const firefoxDownloads = new Map();
  const lastMedia = new WeakMap();
  let policy = sanitizeBrowserPolicy({});
  // Browsers that run without a window and stream their tabs, by engine name.
  const streamEngines = Object.fromEntries(Object.entries({ ...chromium, firefox, chrome }).filter(([, engine]) => engine));
  // Firefox settings, extensions and importing (Settings > Browser).
  const firefoxManager = firefox ? createFirefoxManager({
    engine: firefox,
    dataDir: app.getPath("userData"),
    dialog,
    getMainWindow,
    log,
    hooks: {
      stopFirefox: () => firefoxStopped("Raticode is restarting Firefox"),
      reviveFirefoxTabs,
      restartFirefox: async () => {
        firefoxStopped("Raticode is restarting Firefox");
        await firefox.stopAndWait();
        await reviveFirefoxTabs();
      },
      openTab: (url) => {
        const owner = getMainWindow();
        if (owner && !owner.isDestroyed()) owner.webContents.send("gofer:browser-open-tab", { url, background: false, engine: "firefox" });
      },
    },
  }) : null;
  let installed = false;

  const profile = (kind) => electronSession.fromPartition(PARTITIONS[kind]);
  const privateSites = new Map();
  let installedIdentity = null;
  let privateProfileInstalled = false;
  let privateCleanupTimer = null;

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
    installedIdentity = identity;
    for (const kind of ["user", "rem"]) installProfile(kind, identity);
    installPreviewFileGuard();
    removeStrayQuarantine();
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
      void mirrorCookie(cookie, removed).catch((error) => noteMirrorFailure(cookie.domain, error));
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

  function installProfile(kind, identity) {
    const ses = profile(kind);
    // A private tab has the same abilities as one of the user's own tabs.
    const access = kind === "private" ? "user" : kind;
    installIdentity(kind, ses, identity);
    ses.setPermissionRequestHandler((contents, permission, callback, details) => {
      void requestPermission(contents, permission, details || {}, access).then(callback, () => callback(false));
    });
    ses.setPermissionCheckHandler((contents, permission, _origin, details) => checkPermission(contents, permission, details || {}, access));
    ses.setDevicePermissionHandler(() => false);
    ses.on("will-download", (event, item, contents) => handleDownload(event, item, contents, access));
    ses.setCertificateVerifyProc((request, callback) => {
      if (request.errorCode === 0 && request.certificate) verifiedCertificates.set(request.hostname, certificateSummary(request.certificate));
      callback(-3);
    });
  }

  // Local HTML previews run as file:// pages, and Electron lets those read any other
  // file:// URL (grantFileProtocolExtraPrivileges). Without a check a previewed page
  // could fetch ~/.ssh or list folders and send the result to a website (CWE-552).
  // Every file:// request in the preview profile, the page itself included, must be
  // inside the folder an open preview was granted.
  function installPreviewFileGuard() {
    const webRequest = profile("preview")?.webRequest;
    if (typeof webRequest?.onBeforeRequest !== "function") return;
    webRequest.onBeforeRequest({ urls: ["file://*/*"] }, (details, callback) => {
      callback({ cancel: !previewFileAllowed(details?.url) });
    });
  }

  function previewFileAllowed(value) {
    let target;
    try { target = fileURLToPath(value); } catch { return false; }
    for (const session of sessions.values()) {
      if (session.kind !== "preview") continue;
      try {
        resolvePath(target, { grantId: session.grantId, mustExist: true });
        return true;
      } catch { /* Not inside this preview's grant. */ }
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // Private tabs

  function installPrivateProfile() {
    if (privateProfileInstalled) return;
    privateProfileInstalled = true;
    installProfile("private", installedIdentity);
  }

  // Which profile a tab's cookies and storage live in. Private tabs are the
  // user's own tabs in every other respect.
  function profileKind(session) {
    return session.privateBrowsing ? "private" : session.kind;
  }

  // Private tabs neither read nor write the user's saved site settings.
  function siteOf(session, origin) {
    return session?.privateBrowsing ? (privateSites.get(origin) || {}) : store.site(origin);
  }

  function updateSiteOf(session, origin, patch) {
    if (!session?.privateBrowsing) return store.updateSite(origin, patch);
    privateSites.set(origin, { ...siteOf(session, origin), ...patch });
    return undefined;
  }

  function setPermissionOf(session, origin, key, value) {
    if (!session?.privateBrowsing) return store.setPermission(origin, key, value);
    const permissions = { ...(siteOf(session, origin).permissions || {}) };
    if (value) permissions[key] = value;
    else delete permissions[key];
    updateSiteOf(session, origin, { permissions });
    return undefined;
  }

  // Nothing lists a private download after this, so an unfinished one stops
  // and a dangerous one still waiting for review doesn't stay on disk.
  function forgetPrivateDownloads() {
    for (const [id, live] of downloads) {
      if (!live.entry.private) continue;
      live.entry.forgotten = true;
      downloads.delete(id);
      if (live.entry.state === "progressing") {
        try { live.item.cancel(); } catch { /* It has just finished. */ }
      }
    }
    for (const entry of store.listDownloads()) {
      if (entry.private && entry.quarantinePath) fs.rmSync(entry.quarantinePath, { force: true });
    }
    store.forgetPrivateDownloads();
    emitDownloads();
  }

  // A private download left waiting for review when Raticode last stopped has
  // no list entry to review it from.
  function removeStrayQuarantine() {
    try {
      const listed = new Set(store.listDownloads().map((item) => item.quarantinePath).filter(Boolean));
      for (const name of fs.readdirSync(quarantineDir)) {
        const file = path.join(quarantineDir, name);
        if (name.endsWith(".download") && !listed.has(file)) fs.rmSync(file, { force: true });
      }
    } catch { /* No quarantine folder yet. */ }
  }

  // A tab that is moved between panes closes and reopens at once, so the data
  // goes only if no private tab comes back.
  function forgetPrivateDataSoon() {
    clearTimeout(privateCleanupTimer);
    privateCleanupTimer = setTimeout(() => {
      if ([...sessions.values()].some((item) => item.privateBrowsing)) return;
      privateSites.clear();
      for (const key of [...certificateExceptions]) if (key.startsWith("private|")) certificateExceptions.delete(key);
      clientHints.get("private")?.clear();
      forgetPrivateDownloads();
      const ses = profile("private");
      void Promise.all([ses.clearStorageData(), ses.clearCache(), ses.clearAuthCache()]).catch((error) => {
        log("warn", `Could not clear private browsing data: ${error.message}`);
      });
    }, privateCleanupDelayMs);
    privateCleanupTimer.unref?.();
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
    const previousDownloads = policy.remDownloads;
    policy = sanitizeBrowserPolicy(value);
    const added = policy.remSignInSites.filter((site) => !previous.includes(site));
    const removed = previous.filter((site) => !policy.remSignInSites.includes(site));
    if (added.length || removed.length) void syncRemSignIns(added, removed).catch((error) => log("warn", `Sign-in sharing failed: ${error.message}`));
    for (const [name, engine] of Object.entries(streamEngines)) {
      if (added.length || removed.length) {
        void engine.mirrorSignIns(added, removed).catch((error) => log("warn", `Sign-in sharing with Rem's ${engineLabel(name)} tabs failed: ${error.message}`));
      }
      if (previousDownloads !== policy.remDownloads) void engine.setRemDownloads(policy.remDownloads === "allow");
    }
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
      let failed = 0;
      for (const cookie of remCookies) {
        if (!removed.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        if (policy.remSignInSites.some((site) => cookieMatchesSite(cookie.domain, site))) continue;
        await rem.cookies.remove(cookieUrl(cookie), cookie.name).catch(() => { failed += 1; });
      }
      // Revoking sharing must not silently leave Rem signed in. Cookie names and values stay out of the log.
      if (failed) log("warn", `Could not remove ${failed} of Rem's shared sign-in cookies for ${removed.join(", ")}; Rem may stay signed in there. Clear that site's data in Rem's browser.`);
    }
  }

  // Mirroring runs on every cookie change; report each site and reason once.
  const mirrorFailures = new Set();
  function noteMirrorFailure(domain, error) {
    const key = `${domain}:${error?.message || error}`;
    if (mirrorFailures.has(key)) return;
    mirrorFailures.add(key);
    log("warn", `Could not update Rem's shared sign-in for ${String(domain || "").replace(/^\./, "")}: ${error?.message || error}`);
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
    const privateTab = kind === "user" && options.private === true;
    // Each tab names its browser; older windows that don't get the default.
    const streamed = kind === "user" && !privateTab ? tabStreamEngine(options.engine) : "";
    if (streamed) {
      // Firefox and Chrome cannot restore another browser's back-forward
      // list, so a restored tab reopens the page it was showing.
      const target = history ? history.entries[history.index].url : String(options.url || "");
      const session = baseSession({
        id,
        owner: event.sender,
        kind,
        url: "about:blank",
        initialUrl: target || "about:blank",
        clientId: typeof options.clientId === "string" ? options.clientId : "",
        applicationKeybindings: sanitizeBrowserKeybindings(options.applicationKeybindings),
        openBrowserBinding: typeof options.openBrowserBinding === "string" ? options.openBrowserBinding.slice(0, 120) : "Mod+Alt+Slash",
      });
      sessions.set(id, session);
      registerOwnerCleanup(event.sender);
      createStreamContents(session, streamed);
      if (target && target !== "about:blank") void navigate(session, target).catch((error) => { session.error = error.message; emitState(session); });
      return sessionState(session);
    }
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
    if (privateTab) {
      installPrivateProfile();
      session.privateBrowsing = true;
      clearTimeout(privateCleanupTimer);
    }
    sessions.set(id, session);
    registerOwnerCleanup(event.sender);
    return { ...sessionState(session), src: session.pendingSrc, partition: PARTITIONS[profileKind(session)] };
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
          && PARTITIONS[profileKind(session)] === params.partition) return true;
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
    if (!sameProfile(guest.session, profile(profileKind(session)))) throw new Error("Browser view uses the wrong profile.");
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
    if (session.privateBrowsing && ![...sessions.values()].some((item) => item.privateBrowsing)) forgetPrivateDataSoon();
    session.closed = true;
    session.contents = null;
    clearTimeout(session.detachTimer);
    session.mediaWatch?.stop();
    session.mediaWatch = null;
    emitMedia(session.owner);
    if (isStreamed(contents)) contents.close();
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
    // Raticode is quitting: Firefox and Chrome go with it.
    for (const engine of Object.values(streamEngines)) engine.stop();
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
      if (session.kind === "user" && !session.privateBrowsing) store.updateTitle(contents.getURL(), title);
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
      // Raticode ended a stuck page to reload it (reload's recover): reload it
      // now that its page process is gone, without reporting a crash.
      if (session.reloadWhenGone) {
        const hard = session.reloadWhenGone === "hard";
        session.reloadWhenGone = "";
        reload(session, hard);
        return;
      }
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
    session.mediaWatch = watchMedia(contents, {
      audible: () => session.audible === true,
      muted: () => session.muted === true,
      onChange: () => emitMedia(session.owner),
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
      if (channel === "gofer:browser-context-element") session.contextElement = contextElement(payload);
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

  // recover: the tab's page stopped answering and its owner chose to start it
  // again. A stuck page can't reload itself, so its page process is ended
  // first and the page reloads once it's gone (render-process-gone), keeping
  // the back/forward list (see browserRecovery.js).
  function reload(session, hard, recover = false) {
    const contents = liveContents(session);
    if (!contents) return;
    if (recover && session.unresponsive && !isStreamed(contents) && typeof contents.forcefullyCrashRenderer === "function") {
      session.unresponsive = false;
      session.reloadWhenGone = hard ? "hard" : "soft";
      contents.forcefullyCrashRenderer();
      return;
    }
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
    if (!contents || isStreamed(contents)) return;
    if (contents.isDevToolsOpened()) contents.closeDevTools();
    else contents.openDevTools({ mode: "detach" });
  }

  // Chromium's print dialog fails, and can crash, when the system has no
  // printers (no CUPS on Linux). Check first and offer a PDF instead.
  async function print(session) {
    const contents = liveContents(session);
    if (!contents) return;
    // A headless Firefox or Chrome cannot show a print dialog; a PDF is the closest.
    if (isStreamed(contents)) {
      await saveAsPdf(session);
      return;
    }
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
    session.owner.send("gofer:browser-open-tab", { url: url.toString(), background: !foreground, engine: session.engine || "raticode", ...(session.privateBrowsing ? { private: true } : {}) });
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
      popupsAllowed: siteOf(session, origin).popups === "allow",
    });
    if (decision.kind === "same-tab") {
      // loadURL skips will-navigate, so Rem's site rules are checked here.
      if (!isAllowedNavigation(session, details.url)) {
        log("warn", "Blocked a page from opening a site Rem may not open.");
        return { action: "deny" };
      }
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
    if (/^https?:/i.test(popup.url)) session.owner.send("gofer:browser-open-tab", { url: popup.url, background: false, engine: session.engine || "raticode", ...(session.privateBrowsing ? { private: true } : {}) });
    emitState(session);
  }

  // ---------------------------------------------------------------------------
  // Zoom

  function applySiteZoom(session, url) {
    const origin = siteKey(url);
    session.pageZoomFactor = clampZoom(origin ? siteOf(session, origin).zoom || 1 : 1);
    syncZoom(session);
  }

  function changePageZoom(session, direction) {
    const contents = liveContents(session);
    if (!contents) return;
    session.pageZoomFactor = direction === 0 ? 1 : nextZoomLevel(session.pageZoomFactor, direction);
    const origin = siteKey(contents.getURL());
    if (origin && session.kind !== "rem") updateSiteOf(session, origin, { zoom: session.pageZoomFactor === 1 ? null : session.pageZoomFactor });
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
    // Firefox and Chrome lay the page out smaller instead; the studio's own
    // zoom already reaches them through the view's pixel ratio.
    if (isStreamed(contents)) {
      contents.setPageZoom(session.pageZoomFactor);
      return;
    }
    contents.setZoomFactor(browserContentZoomFactor(session.ownerZoomFactor, session.pageZoomFactor));
  }

  // ---------------------------------------------------------------------------
  // History

  function recordHistory(session, url) {
    if (session.kind !== "user" || session.privateBrowsing || !/^https?:/i.test(String(url))) return;
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
    const stored = siteOf(session, origin).permissions || {};
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
      for (const key of keys) setPermissionOf(session, origin, key, response);
    }
    return response === "allow" || response === "allow-once";
  }

  function checkPermission(contents, permission, details, kind) {
    if (policyRules.AUTO_ALLOWED_PERMISSIONS.has(permission)) return kind === "user" || permission === "clipboard-sanitized-write";
    if (kind === "rem") return false;
    const session = sessionForContents(contents);
    if (!session) return false;
    const origin = siteKey(details.requestingUrl || details.securityOrigin || contents?.getURL?.() || "");
    const stored = siteOf(session, origin).permissions || {};
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
    const key = `${session.privateBrowsing ? "private|" : ""}${hostOf(url)}|${certificate?.fingerprint || ""}`;
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
    certificateExceptions.add(`${session.privateBrowsing ? "private|" : ""}${failure.host}|${failure.fingerprint}`);
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
    const saveAs = takePendingSaveAs(session, item);
    const askWhere = !dangerous && !byRem && (policy.askWhereToSave || Boolean(saveAs));
    let finalPath = "";
    if (dangerous) {
      fs.mkdirSync(quarantineDir, { recursive: true, mode: 0o700 });
      item.setSavePath(path.join(quarantineDir, `${id}.download`));
    } else if (askWhere) {
      item.setSaveDialogOptions({ defaultPath: path.join(directory, filename), title: saveAs || "Save download" });
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
      ...(session?.privateBrowsing ? { private: true } : {}),
    };
    downloads.set(id, { item, entry });
    store.upsertDownload(entry);
    emitDownloads();
    let lastEmit = 0;
    item.on("updated", (_event, state) => {
      if (entry.forgotten) return;
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
      if (entry.forgotten) return;
      // Cancelling the save dialog is not a download worth listing.
      if (askWhere && state === "cancelled" && !item.getSavePath()) {
        downloads.delete(id);
        store.removeDownload(id);
        emitDownloads();
        return;
      }
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

  function finishedDownloadPath(entry) {
    if (entry?.state !== "completed" || !entry.path) throw new Error("Only finished downloads can be opened.");
    if (!fs.existsSync(entry.path)) throw new Error("The file was moved or deleted.");
    return entry.path;
  }

  async function downloadApps(id) {
    const entry = store.listDownloads().find((item) => item.id === id);
    if (!entry) throw new Error("That download is no longer listed.");
    const filePath = finishedDownloadPath(entry);
    try {
      return { apps: openWith ? await openWith.listApps(filePath) : [] };
    } catch (error) {
      log("warn", `Listing apps for a download failed: ${error.message}`);
      return { apps: [] };
    }
  }

  async function openDownloadWith(entry, appId) {
    const filePath = finishedDownloadPath(entry);
    if (!openWith) throw new Error("Open with is not supported on this system.");
    await openWith.openWith(filePath, appId);
    return { downloads: store.listDownloads() };
  }

  function downloadAction(action, id, options = {}) {
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
      case "open-with":
        return openDownloadWith(entry, String(options.appId || ""));
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
        const userTab = [...sessions.values()].find((item) => item.kind === "user" && Boolean(item.privateBrowsing) === Boolean(entry.private) && liveContents(item));
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
    // Frames other than the main one do not report the element they show.
    if (params.frame && params.frame !== contents.mainFrame) session.contextElement = null;
    const element = session.contextElement || null;
    // Chrome and Edge tabs report the right-click themselves; their page,
    // clipboard and editing live in a browser without a window.
    const streamed = isStreamed(contents);
    const edit = (command) => () => {
      const text = command === "paste" ? clipboard.readText() : "";
      void contents.editCommand(command, text).catch((error) => log("warn", `${engineLabel(contents.engine)} could not ${command}: ${error.message}`));
    };
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
        ...(streamed ? [] : [{ label: "Save link as…", click: () => saveLinkAs(session, params.linkURL) }]),
        { label: "Copy link address", click: () => copyText(params.linkURL) },
      );
      separator();
    } else if (!streamed && params.linkURL && /^(?:blob|data):/i.test(params.linkURL)) {
      // A file the page built itself, such as an exported chat. It only
      // exists inside this page, so it can be saved but not opened elsewhere.
      template.push({ label: "Save link as…", click: () => saveLinkAs(session, params.linkURL) });
      separator();
    }
    if (streamed && params.mediaType === "image" && params.srcURL) {
      template.push(
        { label: "Open image in new tab", click: () => openPageLink(session, params.srcURL, { background: true }) },
        { label: "Copy image address", click: () => copyText(params.srcURL) },
      );
      separator();
    } else if (params.mediaType === "image" && params.srcURL) {
      template.push(
        { label: "Save image as…", click: () => saveImageAs(session, params.srcURL) },
        { label: "Copy image", click: () => contents.copyImageAt(params.x, params.y) },
        { label: "Copy image address", click: () => copyText(params.srcURL) },
      );
      separator();
    }
    if (streamed && params.isEditable) {
      template.push(
        { label: "Undo", click: edit("undo") },
        { label: "Redo", click: edit("redo") },
        { type: "separator" },
        { label: "Cut", enabled: Boolean(params.selectionText), click: edit("cut") },
        { label: "Copy", enabled: Boolean(params.selectionText), click: edit("copy") },
        { label: "Paste", click: edit("paste") },
        { label: "Select all", click: edit("selectAll") },
      );
      separator();
    } else if (params.isEditable) {
      template.push({ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" });
      separator();
    } else if (params.selectionText) {
      const text = params.selectionText.trim().replace(/\s+/g, " ");
      template.push(
        streamed ? { label: "Copy", click: edit("copy") } : { role: "copy" },
        { label: `Search for “${text.length > 30 ? `${text.slice(0, 29)}…` : text}”`, click: () => openPageLink(session, policy.searchUrl.replace("{query}", encodeURIComponent(text.slice(0, 500))), { background: false }) },
      );
      separator();
    }
    // Local files ask about the open page only; Rem gets no further file access.
    if (!session.privateBrowsing && (session.kind !== "preview" || /^file:/i.test(contents.getURL()))) {
      template.push({ label: "Ask Rem about this", click: () => emitCommand(session, "ask-rem", { selection: String(params.selectionText || "").slice(0, 20000), element }) });
      separator();
    }
    template.push(
      { label: "Back", enabled: contents.navigationHistory.canGoBack(), click: () => contents.navigationHistory.goBack() },
      { label: "Forward", enabled: contents.navigationHistory.canGoForward(), click: () => contents.navigationHistory.goForward() },
      { label: "Reload", click: () => reload(session, false) },
      { type: "separator" },
      ...(session.kind === "user" ? [{ label: store.bookmarkFor(contents.getURL()) ? "Remove bookmark" : "Bookmark this page", click: () => toggleBookmark(session) }] : []),
      ...(!streamed || contents.savePageFormat ? [{ label: "Save page as…", click: () => void savePage(session) }] : []),
      // A browser without a window prints only to PDF.
      ...(streamed ? [] : [{ label: "Print…", click: () => void print(session) }]),
      { label: "Save as PDF…", click: () => void saveAsPdf(session) },
      ...(streamed ? [] : [{ type: "separator" }, { label: "Inspect", click: () => contents.inspectElement(params.x, params.y) }]),
    );
    const owner = getMainWindow();
    Menu.buildFromTemplate(template).popup(owner && !owner.isDestroyed() ? { window: owner } : {});
  }

  // The download that follows asks where to save, like Save page as….
  function saveImageAs(session, url) {
    startSaveAs(session, url, "Save image");
  }

  function saveLinkAs(session, url) {
    startSaveAs(session, url, "Save link");
  }

  function startSaveAs(session, url, title) {
    const contents = liveContents(session);
    if (!contents) return;
    session.pendingSaveAs = { url, title, at: Date.now() };
    contents.downloadURL(url);
  }

  // The save dialog's title when this download is the one Save … as asked for.
  function takePendingSaveAs(session, item) {
    const pending = session?.pendingSaveAs;
    if (!pending) return "";
    session.pendingSaveAs = null;
    const original = item.getURLChain?.()[0] || item.getURL();
    return pending.url === original && Date.now() - pending.at < SAVE_AS_TIMEOUT_MS ? pending.title || "Save download" : "";
  }

  async function savePage(session) {
    const contents = liveContents(session);
    if (!contents) return;
    try {
      // Chrome and Edge tabs save the page as one file with its pictures.
      const single = contents.savePageFormat === "mhtml";
      const filePath = await chooseSavePath({
        title: "Save page",
        defaultPath: path.join(app.getPath("downloads"), `${sanitizeFilename(contents.getTitle() || "page")}.${single ? "mhtml" : "html"}`),
        filters: single ? [{ name: "Web page, single file", extensions: ["mhtml", "mht"] }] : [{ name: "Web page", extensions: ["html", "htm"] }],
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
    const streamed = streamEngine();
    if (streamed && !streamEngines[streamed].status().available) {
      const name = engineLabel(streamed);
      throw new Error(`${name} is the default browser in Settings > Browser, but it is not installed. Ask the user to install ${name} or choose Raticode (chromium) as the default browser.`);
    }
    const session = baseSession({ id: crypto.randomUUID(), owner, kind: "rem", url: "about:blank", agent: { id: agent, label, shared: false } });
    sessions.set(session.id, session);
    registerOwnerCleanup(owner);
    if (streamed) createStreamContents(session, streamed);
    owner.send("gofer:browser-agent-open", { id: session.id, label, partition: PARTITIONS.rem, engine: streamed || "raticode" });
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
    if (session.kind !== "user" || session.privateBrowsing) throw new Error("Only your regular web tabs can be shared with Rem.");
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
  // Firefox and Chrome modes: tabs streamed from a browser without a window

  // The chosen web browser when it is one that streams, otherwise "".
  function streamEngine() {
    const name = policy.engine;
    return name !== "raticode" && streamEngines[name] ? name : "";
  }

  // The streamed browser a new tab asked for: "" for Raticode (chromium) and
  // for Raticode (gecko), whose window the studio places itself. A request
  // without a browser, from a window older than per-tab browsers, gets the
  // default from Settings > Browser.
  function tabStreamEngine(requested) {
    if (typeof requested !== "string" || !requested) return streamEngine();
    if (requested === "raticode" || requested === "gecko") return "";
    if (streamEngines[requested]) return requested;
    throw new Error(`${engineLabel(requested)} is not available in this version of Raticode.`);
  }

  // A session or tab contents that streams from Firefox or Chrome.
  function isStreamed(item) {
    const name = item?.engine;
    return Boolean(name && name !== "raticode" && streamEngines[name]);
  }

  function engineLabel(name) {
    if (name === "firefox") return "Firefox";
    if (!name || name === "raticode") return "Raticode (chromium)";
    if (name === "gecko") return "Raticode (gecko)";
    return streamEngines[name]?.browser?.name || String(name);
  }

  // Whether each browser that can stream tabs is installed, by engine name.
  function engineStatus() {
    const missing = { available: false, executable: "", running: false };
    const status = { firefox: missing, chrome: missing, edge: missing };
    for (const [name, engine] of Object.entries(streamEngines)) status[name] = engine.status();
    return status;
  }

  function createStreamContents(session, name) {
    const engine = streamEngines[name];
    const Contents = name === "firefox" ? FirefoxContents : ChromiumContents;
    const contents = new Contents({
      engine,
      kind: session.kind === "rem" ? "rem" : "user",
      nativeImage,
      clipboard: { writeText: (text) => clipboard.writeText(text) },
      log,
      platform,
    });
    if (session.agent && !session.agent.shared) {
      // Rem's own tabs check page navigations against its site rules, and
      // start with copies of the sign-ins the user shares.
      contents.guardNavigation = true;
      const sites = [...policy.remSignInSites];
      contents.ready = contents.ready.then(() => engine.mirrorSignIns(sites).catch((error) => {
        log("warn", `Could not share sign-ins with Rem's ${engineLabel(name)} tabs: ${error.message}`);
      }));
      contents.ready.catch(() => {});
    }
    session.engine = name;
    session.contents = contents;
    session.pendingSrc = "";
    contents.once("destroyed", () => closeSession(session));
    configure(session);
    configureStream(session);
    syncZoom(session);
    return contents;
  }

  // Firefox tabs name their events firefox-*, Chrome tabs stream-*.
  function configureStream(session) {
    const contents = session.contents;
    const prefix = contents.eventPrefix || "firefox";
    const send = (channel, payload) => {
      if (sessions.get(session.id) === session && !session.owner.isDestroyed()) session.owner.send(channel, { ...payload, id: session.id });
    };
    contents.on(`${prefix}-frame`, (frame) => send("gofer:browser-frame", frame));
    for (const kind of ["cursor", "select", "highlight", "focus"]) {
      contents.on(`${prefix}-${kind}`, (details = {}) => send("gofer:browser-firefox", { ...details, kind }));
    }
    contents.on(`${prefix}-ready`, () => emitState(session));
    contents.on(`${prefix}-open-tab`, ({ url }) => openPageLink(session, url));
    contents.on(`${prefix}-dialog`, (details) => void answerFirefoxDialog(session, contents, details));
    contents.on(`${prefix}-file-dialog`, (details) => void chooseFirefoxFiles(session, contents, details));
    contents.on(`${prefix}-download-begin`, (details) => firefoxDownloadBegan(session, details));
    contents.on(`${prefix}-download-end`, (details) => firefoxDownloadEnded(details, session));
  }

  function firefoxViewAction(session, contents, options) {
    if (!isStreamed(contents)) throw new Error("This tab does not stream.");
    switch (options.action) {
      case "firefox-view":
        clearTimeout(session.detachTimer);
        contents.setView({ width: options.width, height: options.height, scale: options.scale }, options.visible === true);
        return { ok: true };
      case "firefox-input":
        contents.handleInput(options.events);
        return { ok: true };
      case "frame-ack":
        contents.ackFrame(options.seq);
        return { ok: true };
      case "view-detached":
        contents.setView(null, false);
        // Closing Rem's tab in the studio removes its view. A remount
        // attaches again within moments; otherwise the tab is gone.
        if (session.agent && !session.agent.shared) {
          clearTimeout(session.detachTimer);
          session.detachTimer = setTimeout(() => { if (sessions.get(session.id) === session) closeAgentTab(session); }, 3000);
          session.detachTimer.unref?.();
        }
        return { ok: true };
      default:
        throw new Error(`Unknown browser action: ${options.action}`);
    }
  }

  // Firefox shows no dialogs of its own without a window, so Raticode asks.
  async function answerFirefoxDialog(session, contents, details) {
    // Rem answers dialogs in its own tabs with browser_handle_dialog.
    if (session.agent && !session.agent.shared) return;
    const host = hostOf(contents.getURL()) || "This page";
    let accept = true;
    let text;
    if (details.type === "alert") {
      await showMessage({ type: "info", message: `${host} says`, detail: details.message, buttons: ["OK"] });
    } else if (details.type === "beforeunload") {
      accept = await showMessage({ type: "question", buttons: ["Leave", "Stay"], defaultId: 1, cancelId: 1, message: "Leave this site?", detail: "Changes you made may not be saved." }) === 0;
    } else {
      const prompt = details.type === "prompt";
      const detail = prompt
        ? `${details.message}\n\n${engineLabel(session.engine)} tabs cannot type an answer here. OK sends ${details.defaultValue ? `the suggested answer "${details.defaultValue}"` : "an empty answer"}.`
        : details.message;
      accept = await showMessage({ type: "question", message: `${host} says`, detail, buttons: ["OK", "Cancel"], defaultId: 0, cancelId: 1 }) === 0;
      if (prompt) text = details.defaultValue || "";
    }
    if (liveContents(session) === contents) await contents.answerDialog(accept, text).catch(() => {});
  }

  async function chooseFirefoxFiles(session, contents, details) {
    // Rem attaches files with browser_file_upload, which checks its folders.
    if (!details.element || session.agent && !session.agent.shared) return;
    const owner = getMainWindow();
    const options = { title: "Choose files to upload", properties: details.multiple ? ["openFile", "multiSelections"] : ["openFile"] };
    try {
      const result = owner && !owner.isDestroyed() ? await dialog.showOpenDialog(owner, options) : await dialog.showOpenDialog(options);
      if (result.canceled || !result.filePaths?.length || liveContents(session) !== contents) return;
      await contents.setFiles(details.element, result.filePaths);
    } catch (error) {
      log("warn", `Could not attach files in ${/^[aeiou]/i.test(engineLabel(session.engine)) ? "an" : "a"} ${engineLabel(session.engine)} tab: ${error.message}`);
    }
  }

  // Firefox and Chrome save into Raticode's staging folders. Finished files
  // move to Downloads, or to quarantine when their type can run code.
  function firefoxDownloadBegan(session, details) {
    const byRem = Boolean(session.agent && !session.agent.shared);
    const filename = sanitizeFilename(details.filename || "download");
    if (byRem && policy.remDownloads !== "allow") {
      session.lastBlockedDownload = filename;
      recordAgentActivity(session, { action: "download blocked", detail: filename, at: Date.now() });
      return;
    }
    const entry = {
      id: crypto.randomUUID(),
      url: String(details.url || "").slice(0, 4096),
      filename,
      path: "",
      mimeType: "",
      state: "progressing",
      paused: false,
      receivedBytes: 0,
      totalBytes: 0,
      startedAt: Date.now(),
      dangerous: isDangerousDownload(filename),
      byRem,
    };
    firefoxDownloads.set(details.navigation || entry.id, entry);
    store.upsertDownload(entry);
    emitDownloads();
  }

  function firefoxDownloadEnded(details, session = null) {
    const key = details.navigation || "";
    const entry = firefoxDownloads.get(key);
    if (!entry) {
      if (details.filepath) fs.rmSync(details.filepath, { force: true });
      return;
    }
    firefoxDownloads.delete(key);
    entry.finishedAt = Date.now();
    try {
      if (details.status !== "complete" || !details.filepath) {
        entry.state = "cancelled";
      } else if (entry.dangerous) {
        fs.mkdirSync(quarantineDir, { recursive: true, mode: 0o700 });
        const target = path.join(quarantineDir, `${entry.id}.download`);
        moveFile(details.filepath, target);
        Object.assign(entry, { state: "quarantined", quarantinePath: target, receivedBytes: fs.statSync(target).size });
      } else {
        const target = uniqueDownloadPath(app.getPath("downloads"), entry.filename, (candidate) => fs.existsSync(candidate));
        moveFile(details.filepath, target);
        Object.assign(entry, { state: "completed", path: target, receivedBytes: fs.statSync(target).size });
      }
      entry.totalBytes = entry.receivedBytes;
    } catch (error) {
      entry.state = "interrupted";
      log("warn", `Could not finish a download from ${/^[aeiou]/i.test(engineLabel(session?.engine)) ? "an" : "a"} ${engineLabel(session?.engine)} tab: ${error.message}`);
    }
    store.upsertDownload(entry);
    emitDownloads();
  }

  // Firefox or Chrome stopped. Its tabs stay open and start it again when reloaded.
  function streamStopped(name, reason = `${engineLabel(name)} stopped`) {
    for (const session of sessions.values()) {
      if (session.engine === name) liveContents(session)?.markDead(reason);
    }
  }

  function firefoxStopped(reason = "Firefox stopped") {
    streamStopped("firefox", reason);
  }

  // Google, and some other sites, refuse to sign in while Firefox or Chrome
  // is under remote control. Both tell every page they are automated while
  // Raticode controls them, so the user signs in in a normal window on the
  // same profile instead. The tabs come back when that window closes.
  async function streamSignInWindow(name, url) {
    const engine = streamEngines[name];
    if (!engine) throw new Error(`${engineLabel(name)} is not available in this version of Raticode.`);
    if (engine.signingIn) return { alreadyOpen: true };
    streamStopped(name, `${engineLabel(name)} is open in a window for signing in. Close that window to come back here`);
    return engine.openSignInWindow(url);
  }

  function firefoxSignInWindow(url) {
    return streamSignInWindow("firefox", url);
  }

  // Where to sign in from a tab: Google's sign-in page after its refusal,
  // otherwise the page the tab shows. The address goes on the browser's
  // command line, which other accounts on this computer can read, so the
  // page's query string and fragment are left off.
  function signInAddress(contents) {
    const current = contents.getURL();
    if (!GOOGLE_REJECTED.test(current)) return /^https?:/i.test(current) ? policyRules.addressWithoutQuery(current) : "https://accounts.google.com/";
    const earlier = contents.navigationHistory.getAllEntries().map((entry) => entry.url).reverse().find((url) => /^https?:/i.test(url) && hostOf(url) !== "accounts.google.com");
    return earlier && /(?:^|\.)(?:google\.[a-z.]+|youtube\.com)$/i.test(hostOf(earlier))
      ? `https://accounts.google.com/ServiceLogin?continue=${encodeURIComponent(policyRules.addressWithoutQuery(earlier))}`
      : "https://accounts.google.com/";
  }

  // After Raticode restarts Firefox or Chrome, its tabs reopen their pages.
  async function reviveStreamTabs(name) {
    const engine = streamEngines[name];
    if (!engine) return;
    const waiting = [];
    for (const session of sessions.values()) {
      const contents = liveContents(session);
      if (session.engine === name && contents?.dead) waiting.push(contents.reloadPage(false).catch(() => {}));
    }
    if (!waiting.length && streamEngine() === name) await engine.ensure().catch((error) => log("warn", `${engineLabel(name)} did not start again: ${error.message}`));
    await Promise.all(waiting);
  }

  function reviveFirefoxTabs() {
    return reviveStreamTabs("firefox");
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
      engine: session.engine || "raticode",
      // Firefox's own install prompt can't appear, so the studio offers one.
      firefoxAddon: session.engine === "firefox" && contents ? addonSlugFromUrl(contents.getURL()) : "",
      // Google refused to sign in because Firefox is under remote control.
      firefoxSignInBlocked: session.engine === "firefox" && session.kind === "user" && Boolean(contents && GOOGLE_REJECTED.test(contents.getURL())),
      // The same refusal in any browser that streams its tabs.
      signInBlocked: isStreamed(session) && session.kind === "user" && Boolean(contents && GOOGLE_REJECTED.test(contents.getURL())),
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
      remSignIn: Boolean(session.agent && !session.agent.shared && policyRules.remSignedInHost(hostOf(currentUrl), policy.remSignInSites)),
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

  // What the owner's tabs are playing: playing first, then the newest to start.
  // clientId names the owner's tab, so the bar can skip the tab on screen.
  function mediaSnapshots(ownerId) {
    return [...sessions.values()].filter((session) => session.ownerId === ownerId).flatMap((session) => {
      const media = session.mediaWatch?.snapshot();
      return media ? [{ ...media, id: session.id, clientId: session.clientId || "" }] : [];
    }).sort((a, b) => Number(b.playing) - Number(a.playing) || b.startedAt - a.startedAt);
  }

  function emitMedia(owner) {
    if (owner.isDestroyed()) return;
    const snapshots = mediaSnapshots(owner.id);
    const key = JSON.stringify(snapshots);
    if ((lastMedia.get(owner) ?? "[]") === key) return;
    lastMedia.set(owner, key);
    owner.send("gofer:browser-media", { sessions: snapshots });
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
    // Firefox shows its own pages, such as about:preferences, only to remote
    // control that is also allowed to run programs; Raticode never asks for that.
    if (session.engine === "firefox" && /^\s*(?:about:(?!blank\s*$)|moz-extension:|chrome:|resource:)/i.test(String(value ?? ""))) {
      throw new Error("Firefox's own pages, such as about:preferences, can't be shown in Raticode. Change Firefox settings and extensions in Settings > Browser > Firefox.");
    }
    // Chrome's own pages, such as chrome://settings, can change how Chrome
    // runs; they open only in a normal Chrome window the user controls.
    if (isStreamed(session) && session.engine !== "firefox" && isChromiumInternalPage(value)) {
      const name = engineLabel(session.engine);
      throw new Error(`${name}'s own pages, such as ${session.engine}://settings, can't be shown in Raticode tabs. Open them from Settings > Browser > ${name}, which opens ${/^[aeiou]/i.test(name) ? "an" : "a"} ${name} window.`);
    }
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
    if (["close", "adopt", "focus", "set-preferences", "viewport", "prompt-response", "firefox-view", "firefox-input", "frame-ack", "view-detached", "media-control"].includes(options.action) && !sessions.has(options.id)) {
      return { closed: true, ready: false };
    }
    const session = ownedSession(event, options.id);
    switch (options.action) {
      case "adopt":
        // Rem's tabs are created before the studio picks their tab identity.
        if (session.agent && !session.clientId && typeof options.clientId === "string") session.clientId = options.clientId.slice(0, 200);
        // A Firefox or Chrome tab has no view to adopt; the studio only draws its frames.
        if (isStreamed(session)) {
          clearTimeout(session.detachTimer);
          return sessionState(session);
        }
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
    if (["firefox-view", "firefox-input", "frame-ack", "view-detached"].includes(options.action)) return firefoxViewAction(session, contents, options);
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
        reload(session, options.hard === true, options.recover === true);
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
      case "media-control":
        return session.mediaWatch ? session.mediaWatch.control(options.command) : { handled: false };
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
        if (origin) updateSiteOf(session, origin, { popups: "allow" });
        for (const popup of session.blockedPopups) {
          if (/^https?:/i.test(popup.url)) session.owner.send("gofer:browser-open-tab", { url: popup.url, background: false, engine: session.engine || "raticode", ...(session.privateBrowsing ? { private: true } : {}) });
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
        if (options.key === "popups") updateSiteOf(session, origin, { popups: options.value === "allow" ? "allow" : null });
        else setPermissionOf(session, origin, options.key.slice(0, 80), ["allow", "block"].includes(options.value) ? options.value : null);
        return siteInfo(session);
      }
      case "firefox-sign-in-window":
      case "sign-in-window":
        if (!isStreamed(session)) throw new Error("This tab doesn't run in Firefox, Chrome or Edge.");
        // Rem's tabs keep their own sign-ins, which the window doesn't share.
        if (session.kind !== "user") throw new Error(`Sign in from one of your own ${engineLabel(session.engine)} tabs; Rem's tabs keep separate sign-ins.`);
        return streamSignInWindow(session.engine, signInAddress(contents));
      case "clear-site-data":
        if (isStreamed(session)) {
          return streamEngines[session.engine].clearSiteCookies(session.kind, hostOf(contents.getURL())).then(() => siteInfo(session));
        }
        return clearSiteData(siteKey(contents.getURL()), profileKind(session)).then(() => siteInfo(session));
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
    const settings = origin ? siteOf(session, origin) : {};
    return {
      origin,
      host,
      profile: session.kind,
      privateBrowsing: session.privateBrowsing === true,
      security: securityState({ url, certificateException: session.certificateException, certificateError: session.certificateError }),
      certificate: url.startsWith("https:") ? verifiedCertificates.get(host) || null : null,
      permissions: Object.entries(settings.permissions || {}).map(([key, value]) => ({
        key,
        value,
        label: key.startsWith("openExternal:") ? `Open ${key.slice(13).replace(/:$/, "")} links` : permissionLabel(key),
      })),
      popups: settings.popups || "ask",
      zoom: session.pageZoomFactor,
      remSignIn: policyRules.remSignedInHost(host, policy.remSignInSites),
    };
  }

  async function clearSiteData(origin, kind = "user", cookieHost = "") {
    if (!origin || origin === "file://") return;
    const ses = profile(kind === "rem" || kind === "private" ? kind : "user");
    const host = cookieHost || hostOf(origin);
    await ses.clearStorageData({ origin });
    clientHints.get(kind === "rem" || kind === "private" ? kind : "user")?.clear(origin);
    const cookies = await ses.cookies.get({});
    let failed = 0;
    for (const cookie of cookies) {
      if (cookieMatchesSite(cookie.domain, host)) await ses.cookies.remove(cookieUrl(cookie), cookie.name).catch(() => { failed += 1; });
    }
    if (failed) log("warn", `Could not remove ${failed} cookies while clearing ${host}; some sign-ins may remain.`);
    if (kind === "private") privateSites.delete(origin);
    else if (kind !== "rem") store.clearSite(origin);
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
      for (const [name, engine] of Object.entries(streamEngines)) {
        await engine.clearSiteCookies(kind, "").catch((error) => log("warn", `Could not clear ${engineLabel(name)} cookies: ${error.message}`));
      }
      await ses.clearStorageData();
      clientHints.get(kind)?.clear();
      if (kind === "rem" && policy.remSignInSites.length) await syncRemSignIns(policy.remSignInSites, []);
    }
    if (options.siteSettings && kind === "user") store.clearSites();
    for (const session of sessions.values()) emitState(session);
    emitLibraryChanged("all");
    return { cleared: true };
  }

  async function browserLibrary(event, options = {}) {
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
        return downloadAction(String(options.command || ""), String(options.id || ""), options);
      case "download-apps":
        return downloadApps(String(options.id || ""));
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
      case "firefox-settings":
      case "firefox-settings-save":
      case "firefox-restart":
      case "firefox-extensions":
      case "firefox-addon-search":
      case "firefox-addon-install":
      case "firefox-addon-install-file":
      case "firefox-addon-remove":
      case "firefox-profiles":
      case "firefox-profile-choose":
      case "firefox-profile-inspect":
      case "firefox-import":
      case "firefox-reset":
      case "firefox-sync":
        if (!firefoxManager) throw new Error("Firefox is not available in this version of Raticode.");
        return firefoxManager.handle(options.action, options);
      case "firefox-sign-in-window":
        return firefoxSignInWindow(typeof options.url === "string" && options.url ? options.url : "https://accounts.google.com/");
      case "engine-status":
        return engineStatus();
      case "media-sessions":
        return { sessions: mediaSnapshots(event.sender.id) };
      default: {
        // Chromium-based browsers share "<engine>-sign-in-window" and "<engine>-restart".
        const [, name, verb] = /^([a-z]+)-(sign-in-window|restart)$/.exec(String(options.action || "")) || [];
        if (name && name !== "firefox" && streamEngines[name]) {
          if (verb === "sign-in-window") return streamSignInWindow(name, typeof options.url === "string" && options.url ? options.url : "https://accounts.google.com/");
          streamStopped(name, `Raticode is restarting ${engineLabel(name)}`);
          await streamEngines[name].stopAndWait();
          await reviveStreamTabs(name);
          return engineStatus();
        }
        throw new Error(`Unknown browser library action: ${options.action || "missing"}`);
      }
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
    firefoxStopped,
    reviveFirefoxTabs,
    reviveStreamTabs,
    streamStopped,
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
