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
const { NETWORK_PATH_ERROR, isNetworkPath } = require("./security.cjs");
const { FirefoxContents } = require("./firefox-contents.cjs");
const { PAGE_AGENT } = require("./agent-browser.cjs");
const { ChromiumContents, isInternalPage: isChromiumInternalPage } = require("./chromium-contents.cjs");
const { addonSlugFromUrl, createFirefoxManager } = require("./firefox-manager.cjs");
const { watchMedia } = require("./browser-media.cjs");
const { createBrowserContextMenu } = require("./browser-context-menu.cjs");
const { createPreviewGuard } = require("./browser-preview-guard.cjs");
const { createBrowserDownloads } = require("./browser-downloads.cjs");
const {
  acceptLanguageList,
  createClientHints,
  defaultMetadata,
  mergeMetadata,
  reducedUserAgent,
} = require("./browser-client-hints.cjs");

const {
  PARTITIONS,
  allowPageTab,
  clampZoom,
  classifyLoadError,
  cookieMatchesSite,
  hostOf,
  isCertificateErrorCode,
  nextZoomLevel,
  permissionDecision,
  permissionKeys,
  permissionLabel,
  popupDecision,
  remSiteAllowed,
  sanitizeBrowserPolicy,
  securityState,
  siteKey,
  userAgentWithoutEmbedderTokens,
  externalProtocolOf,
} = policyRules;

const PROMPT_TIMEOUT_MS = 2 * 60 * 1000;
// Google's "Couldn't sign you in · This browser or app may not be secure" page.
const GOOGLE_REJECTED = /^https:\/\/accounts\.google\.com\/(?:v\d+\/)?signin\/rejected/i;
const PAGE_CONTEXT_WORLD_ID = 1918;
const UA_SAMPLE_WORLD_ID = 1919;
// Where a file from another app (scripts off) notes the link the user clicked.
const LINK_CLICK_WORLD_ID = 1921;
// A clicked link's navigation follows its click within moments.
const LINK_CLICK_MS = 3000;
// The offline preview profile's only way out: a proxy on the discard port,
// loopback included, so routes the request guard can't see (WebRTC over TCP,
// preconnect hints, WebTransport) lead nowhere. WebRTC there may use only
// proxied routes, which turns its UDP off.
const OFFLINE_PROXY = Object.freeze({ mode: "fixed_servers", proxyRules: "http://127.0.0.1:9", proxyBypassRules: "<-loopback>" });
const OFFLINE_WEBRTC_POLICY = "disable_non_proxied_udp";

// The path a file: URL names on this computer, or "" for anything else. A
// network share (file://server/share/page.html) is refused before any path or
// disk call: see isNetworkPath.
function localFilePath(value) {
  let url;
  try { url = value instanceof URL ? value : new URL(String(value ?? "")); } catch { return ""; }
  if (url.protocol !== "file:" || isNetworkPath(url)) return "";
  let file;
  try { file = fileURLToPath(url); } catch { return ""; }
  return isNetworkPath(file) ? "" : file;
}
const LINK_CLICK_RECORDER = `(() => {
  if (window.__raticodeLinkClick) return true;
  const state = window.__raticodeLinkClick = { url: "", at: 0 };
  const record = (event) => {
    if (!event.isTrusted || (event.type === "click" && event.button !== 0) || (event.type === "auxclick" && event.button !== 1)) return;
    const anchor = event.composedPath().find((node) => node && (node.tagName === "A" || node.tagName === "AREA"));
    if (!anchor || anchor.hasAttribute("download")) return;
    state.url = String(anchor.href || "");
    state.at = Date.now();
  };
  window.addEventListener("click", record, true);
  window.addEventListener("auxclick", record, true);
  return true;
})()`;
const LINK_CLICK_TAKE = `(() => {
  const state = window.__raticodeLinkClick;
  if (!state || !state.url) return null;
  const taken = { url: state.url, age: Date.now() - state.at };
  state.url = "";
  return taken;
})()`;
const HIGH_ENTROPY_FIELDS = ["architecture", "bitness", "formFactors", "fullVersionList", "model", "platformVersion", "uaFullVersion", "wow64"];
// Client hint headers only apply to potentially trustworthy URLs.
const CLIENT_HINT_URLS = ["https://*/*", "wss://*/*", "http://localhost/*", "http://127.0.0.1/*", "http://*.localhost/*", "ws://localhost/*", "ws://127.0.0.1/*"];
const MAX_RESTORED_ENTRIES = 25;
const MAX_PAGE_STATE_CHARS = 16 * 1024;
// Where a view lays its page out until the studio says where it is on screen.
const DEFAULT_VIEW_BOUNDS = { x: 0, y: 0, width: 1280, height: 800 };
const VIEW_DETACH_GRACE_MS = 3000;
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
  getMainWindow,
  resolvePath,
  // Checks a preview's file:// request against its folder grant; main.js passes
  // security.resolveGrantedPath. resolvePath alone may only check the file exists.
  resolvePreviewPath = resolvePath,
  store,
  log = () => {},
  openWith = null,
  quarantineDir,
  platform = process.platform,
  chromeVersion = process.versions.chrome,
  firefox = null,
  chrome = null,
  // Rem's tabs in Raticode (gecko), from gecko-control.cjs. Only Rem's tabs
  // stream from it; the user's Gecko tab is Firefox's own window.
  gecko = null,
  // Other Chromium-based browsers from chromium-engine.cjs, by engine name.
  chromium = {},
  nativeImage = null,
  privateCleanupDelayMs = PRIVATE_CLEANUP_DELAY_MS,
  // Raticode (chromium) tabs are views this module owns (Electron's WebContentsView).
  WebContentsView = null,
  preloadPath = "",
  // The window a view sits in: "main" is Raticode's, "popout" the Browser's own window.
  getHostWindow = () => null,
  // Told the user's Gecko tabs shared with Rem whenever they change, for every
  // window that can show Raticode (gecko).
  onGeckoShared = () => {},
}) {
  const sessions = new Map();
  const ownersWithCleanup = new WeakSet();
  const prompts = new Map();
  const certificateExceptions = new Set();
  const verifiedCertificates = new Map();
  const clientCertificateChoices = new Map();
  const childWindows = new Map();
  const clientHints = new Map();
  const lastMedia = new WeakMap();
  let policy = sanitizeBrowserPolicy({});
  // Browsers that run without a window and stream their tabs, by engine name.
  const streamEngines = Object.fromEntries(Object.entries({ ...chromium, firefox, chrome, gecko }).filter(([, engine]) => engine));
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
  const {
    showContextMenu, print, savePage, saveAsPdf, toggleBookmark, showMessage, takePendingSaveAs,
  } = createBrowserContextMenu({
    Menu, app, clipboard, dialog, shell, getMainWindow, log, store,
    getPolicy: () => policy,
    liveContents, isStreamed, engineLabel, openPageLink, reload, emitCommand, emitState, emitLibraryChanged,
  });
  const {
    installPreviewRequestGuard, remoteBlockedCount, remoteContentAllowed, previewBelongsOnline, setPreviewRemote, previewMayRead,
  } = createPreviewGuard({
    profile, sessions, store, liveContents, emitState, reload, moveToProfile, sessionState, localFilePath, resolvePreviewPath,
  });
  // After the menu: it takes takePendingSaveAs from there.
  const {
    handleDownload, forgetPrivateDownloads, removeStrayQuarantine, downloadAction, downloadApps, firefoxDownloadBegan, firefoxDownloadEnded,
  } = createBrowserDownloads({
    getPolicy: () => policy,
    sessions, store, log, app, shell, getMainWindow, openWith, quarantineDir,
    recordAgentActivity, sessionForContents, liveContents, takePendingSaveAs, engineLabel,
  });

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
    installPreviewRequestGuard("preview");
    installPreviewRequestGuard("previewOnline");
    installOfflineProxy();
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

  // The offline preview profile reaches nothing, even by routes its request
  // guard never sees. Previews wait for this before loading their file.
  let offlineProxyReady = null;
  function installOfflineProxy() {
    const offline = profile("preview");
    if (typeof offline?.setProxy !== "function") return;
    const failed = (error) => log("warn", `Could not close the offline preview profile's network routes: ${error?.message || error}`);
    let setting;
    try { setting = offline.setProxy(OFFLINE_PROXY); } catch (error) { failed(error); return; }
    if (typeof setting?.then !== "function") return;
    offlineProxyReady = Promise.resolve(setting).catch(failed).finally(() => { offlineProxyReady = null; });
  }

  // A preview's profile is fixed when its view is made, so changing between
  // offline and online makes a new view for the same tab and page.
  function moveToProfile(session, online, url = "") {
    if (!session.view || sessions.get(session.id) !== session) return;
    const contents = liveContents(session);
    const target = url || contents?.getURL() || session.initialUrl;
    session.online = online;
    session.mediaWatch?.stop();
    session.mediaWatch = null;
    // The old page going away must not close the tab.
    session.contents = null;
    removeView(session, contents);
    session.pendingSrc = target;
    createView(session);
    if (session.lastPlace) placeView(session, session.lastPlace);
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
    if (session.privateBrowsing) return "private";
    return session.kind === "preview" && session.online === true ? "previewOnline" : session.kind;
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
    // The folder of the file a preview opened with; without a grant it reads only there.
    let previewFolder = "";
    // A file another app opened with Raticode runs no scripts and stays offline,
    // as in the phone's file viewer.
    let external = false;
    if (requestedPath) {
      const targetPath = resolvePath(requestedPath, { grantId: options.grantId, mustExist: true });
      if (!fs.statSync(targetPath).isFile()) throw new Error(`Browser preview path is not a file: ${targetPath}`);
      url = pathToFileURL(targetPath).toString();
      previewFolder = path.dirname(fs.realpathSync(targetPath));
      grantId = typeof options.grantId === "string" ? options.grantId : "";
      kind = "preview";
      external = options.external === true;
    } else if (options.url) {
      url = browserLoadUrl(normalizeBrowserUrl(options.url, policy.searchUrl));
      // Local files always render in the in-memory preview profile.
      if (url.startsWith("file:")) {
        const file = localFilePath(url);
        if (!file) throw new Error(NETWORK_PATH_ERROR);
        previewFolder = path.dirname(fs.realpathSync(resolvePath(file, { mustExist: true })));
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
    session.external = external;
    session.previewFolder = previewFolder;
    if (kind === "preview") {
      session.online = previewBelongsOnline(session, localFilePath(url));
    }
    if (privateTab) {
      installPrivateProfile();
      session.privateBrowsing = true;
      clearTimeout(privateCleanupTimer);
    }
    sessions.set(id, session);
    registerOwnerCleanup(event.sender);
    try {
      createView(session);
    } catch (error) {
      sessions.delete(id);
      throw error;
    }
    return { ...sessionState(session), partition: PARTITIONS[profileKind(session)] };
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

  // Until a view is set up it may not open anything.
  function hardenGuest(guest) {
    guest.setWindowOpenHandler(() => ({ action: "deny" }));
  }

  // ---------------------------------------------------------------------------
  // Views
  //
  // Each Raticode (chromium) tab is a WebContentsView this module owns. The
  // studio draws an empty slot where the page goes and says where it is, in
  // which window (Raticode's or the Browser's own), and whether it shows. The
  // view follows it there. Moving a view to another window keeps the same page
  // running, so nothing reloads and media keeps playing. Hidden views stay in a
  // window, hidden, so they keep running and can still be captured.

  function viewPreferences(session) {
    return {
      partition: PARTITIONS[profileKind(session)],
      ...(session.external ? { javascript: false } : {}),
      ...(preloadPath ? { preload: preloadPath } : {}),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      // The PDF viewer.
      plugins: true,
      // Background tabs slow their timers, as webview tabs did; audio keeps playing.
      backgroundThrottling: true,
    };
  }

  function createView(session) {
    if (typeof WebContentsView !== "function") throw new Error("Browser view is unavailable.");
    const view = new WebContentsView({ webPreferences: viewPreferences(session) });
    const contents = view.webContents;
    hardenGuest(contents);
    if (!sameProfile(contents.session, profile(profileKind(session)))) {
      contents.close?.();
      throw new Error("Browser view uses the wrong profile.");
    }
    session.view = view;
    session.viewWindow = null;
    session.viewShown = false;
    if (typeof view.setBackgroundColor === "function") view.setBackgroundColor("#ffffff");
    view.setBounds({ ...DEFAULT_VIEW_BOUNDS });
    view.setVisible(false);
    hostView(session, "main");
    const first = session.pendingSrc;
    session.contents = contents;
    session.pendingSrc = "";
    contents.once("destroyed", () => { if (session.contents === contents) closeSession(session); });
    configure(session);
    session.ownerZoomFactor = session.owner.getZoomFactor();
    syncZoom(session);
    if (session.pendingHistory) {
      const history = session.pendingHistory;
      session.pendingHistory = null;
      void restoreHistory(session, contents, history);
    } else if (first && first !== "about:blank") {
      session.localNavigation = first.startsWith("file:");
      const load = () => run(session, () => contents.loadURL(first));
      const proxyPending = profileKind(session) === "preview" ? offlineProxyReady : null;
      if (proxyPending) void proxyPending.then(() => (liveContents(session) === contents ? load() : undefined));
      else void load();
    } else {
      // A view that never loaded has no page yet; a webview always showed
      // about:blank first, and Rem's controls expect a page to talk to.
      void contents.loadURL("about:blank").catch(() => {});
    }
    emitState(session);
  }

  function hostWindowFor(host) {
    const window = (host === "popout" && getHostWindow("popout")) || getMainWindow();
    return window && !window.isDestroyed() ? window : null;
  }

  function hostView(session, host) {
    const window = hostWindowFor(host);
    if (!window || !session.view) return null;
    if (session.viewWindow !== window || session.viewWindow.isDestroyed?.()) {
      // Adding a view to another window takes it out of the one it was in.
      window.contentView.addChildView(session.view);
      session.viewWindow = window;
    }
    return window;
  }

  // The zoom of the page that measured a tab's slot: the Browser's own window
  // when the slot is there, Raticode's otherwise. The two can differ.
  function hostZoomFactor(session, host) {
    const popout = host === "popout" ? getHostWindow("popout") : null;
    const contents = popout && !popout.isDestroyed() ? popout.webContents : session.owner;
    let zoom = 1;
    try {
      if (contents && !contents.isDestroyed?.()) zoom = contents.getZoomFactor?.();
    } catch {
      zoom = 1;
    }
    return Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  }

  function placeView(session, options = {}) {
    if (!session.view || !liveContents(session)) return { placed: false };
    const host = options.host === "popout" ? "popout" : "main";
    hostView(session, host);
    // The studio measures in CSS pixels of the window the slot is in; that
    // window counts them zoomed. The page is zoomed like that window too.
    const zoom = hostZoomFactor(session, host);
    if (session.ownerZoomFactor !== zoom) {
      session.ownerZoomFactor = zoom;
      syncZoom(session);
    }
    const scaled = (value, low, high) => Math.min(Math.max(Number.isFinite(value) ? Math.round(value * zoom) : 0, low), high);
    const width = scaled(Number(options.width), 0, 20000);
    const height = scaled(Number(options.height), 0, 20000);
    const shown = options.visible === true && width > 0 && height > 0;
    // A preview that changes profile gets a new view in the same place.
    session.lastPlace = { ...options };
    // A hidden view keeps its last size, so its page doesn't lay out again.
    if (shown) session.view.setBounds({ x: scaled(Number(options.x), -20000, 20000), y: scaled(Number(options.y), -20000, 20000), width, height });
    session.view.setVisible(shown);
    session.viewShown = shown;
    return { placed: true, shown };
  }

  // A picture of the page, shown in its slot while Raticode draws something over it.
  async function viewSnapshot(session) {
    const contents = liveContents(session);
    if (!session.view || !contents) return { snapshot: "" };
    try {
      const image = await contents.capturePage();
      return { snapshot: image.isEmpty() ? "" : `data:image/jpeg;base64,${image.toJPEG(85).toString("base64")}` };
    } catch {
      return { snapshot: "" };
    }
  }

  // Before the Browser's own window closes: its views go back to Raticode's, hidden.
  function returnViews(window) {
    for (const session of sessions.values()) {
      if (!session.view || session.viewWindow !== window) continue;
      session.view.setVisible(false);
      session.viewShown = false;
      session.viewWindow = null;
      hostView(session, "main");
    }
  }

  function removeView(session, contents) {
    const view = session.view;
    if (!view) return;
    session.view = null;
    try {
      if (session.viewWindow && !session.viewWindow.isDestroyed()) session.viewWindow.contentView.removeChildView(view);
    } catch { /* The window is closing. */ }
    session.viewWindow = null;
    if (contents && !contents.isDestroyed()) {
      if (typeof contents.close === "function") contents.close();
      else contents.destroy?.();
    }
  }

  // Chromium only restores history into a view that has not navigated yet.
  // When restoring fails, still open the page the tab was showing.
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
    if (session.geckoShared) notifyGeckoShared();
    session.closed = true;
    session.contents = null;
    clearTimeout(session.detachTimer);
    clearTimeout(session.moveTimer);
    session.mediaWatch?.stop();
    session.mediaWatch = null;
    emitMedia(session.owner);
    // A Gecko tab's contents call themselves Firefox; the session knows it's Gecko.
    if (isStreamed(contents) || (contents && isStreamed(session))) contents.close();
    removeView(session, contents);
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
    // A reloaded or crashed studio restores its tabs as new ones, so the tabs it had belong
    // to nobody: close them, as a reload closed the old <webview> tabs, instead of leaving
    // their pages running and drawn over the reloaded window.
    owner.on("did-start-navigation", (details) => {
      if (details?.isMainFrame && !details.isSameDocument) closeForOwner(ownerId);
    });
    owner.on("render-process-gone", () => closeForOwner(ownerId));
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
    if (profileKind(session) === "preview") {
      // WebRTC may use only proxied routes, and the offline profile's proxy leads nowhere.
      try { contents.setWebRTCIPHandlingPolicy?.(OFFLINE_WEBRTC_POLICY); } catch (error) { log("warn", `Could not limit WebRTC in a preview: ${error.message}`); }
    }
    if (session.kind === "preview" && session.external) {
      // A file from another app runs no scripts, so Raticode's preload doesn't
      // either; a script world of Raticode's own notes the links the user clicks.
      contents.on("dom-ready", () => {
        void Promise.resolve()
          .then(() => contents.executeJavaScriptInIsolatedWorld(LINK_CLICK_WORLD_ID, [{ code: LINK_CLICK_RECORDER }]))
          .catch(() => { /* Then no link in this page opens a Browser tab. */ });
      });
    }
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
      // Back and forward skip will-navigate: a file in the wrong profile moves now.
      if (session.kind === "preview" && /^file:/i.test(String(url))) setImmediate(() => moveToMatchingProfile(session, url));
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
    // A page's own full screen (a video's button): the view fills its window,
    // and the window goes full screen as it did for webview tabs.
    contents.on("enter-html-full-screen", () => {
      session.fullscreen = true;
      const window = session.viewWindow;
      if (window && !window.isDestroyed() && !window.isFullScreen?.()) {
        session.fullscreenWindow = window;
        window.setFullScreen?.(true);
      }
      update();
    });
    contents.on("leave-html-full-screen", () => {
      session.fullscreen = false;
      const window = session.fullscreenWindow;
      session.fullscreenWindow = null;
      if (window && !window.isDestroyed() && window.isFullScreen?.()) window.setFullScreen?.(false);
      update();
    });
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
    contents.on("will-navigate", (event, url) => {
      if (handOffPreviewNavigation(session, url)) event.preventDefault();
      else if (moveToMatchingProfile(session, url)) event.preventDefault();
      else guardNavigation(event, url);
    });
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
    const file = localFilePath(url);
    return Boolean(file) && previewMayRead(session, file);
  }

  // ---------------------------------------------------------------------------
  // Links, tabs and popups

  function userActivated(session) {
    return Date.now() - session.lastUserInputAt <= policyRules.USER_ACTIVATION_MS;
  }

  // A preview never turns into a website in its own profile, and only a link
  // the user really clicked opens one in a Browser tab. A click anywhere else,
  // or a key press, doesn't let the page's scripts (or a meta refresh) send
  // the user to an address that carries what the page read off.
  // - A page that runs scripts: Raticode's preload hands clicked links over
  //   before any navigation starts, so every navigation to a website here is
  //   the page's own and goes nowhere.
  // - A file from another app (scripts off, so no preload): Raticode's own
  //   script world noted the link the user clicked; only that address opens.
  function handOffPreviewNavigation(session, value) {
    if (session.kind !== "preview") return false;
    if (!/^(?:https?|wss?):/i.test(String(value ?? ""))) return false;
    openIfClickedLink(session, value, {});
    return true;
  }

  function openIfClickedLink(session, value, { background = false }) {
    const blocked = () => log("warn", "Blocked a local preview from opening a website on its own.");
    const contents = liveContents(session);
    if (!session.external || !contents || typeof contents.executeJavaScriptInIsolatedWorld !== "function") {
      blocked();
      return;
    }
    void Promise.resolve()
      .then(() => contents.executeJavaScriptInIsolatedWorld(LINK_CLICK_WORLD_ID, [{ code: LINK_CLICK_TAKE }]))
      .then((clicked) => {
        if (clickedLinkMatches(clicked, value)) openPageLink(session, value, { background });
        else blocked();
      }, blocked);
  }

  function clickedLinkMatches(clicked, value) {
    if (!clicked || typeof clicked.url !== "string" || !(Number(clicked.age) >= 0 && Number(clicked.age) <= LINK_CLICK_MS)) return false;
    try { return new URL(clicked.url).href === new URL(String(value)).href; } catch { return false; }
  }

  // A preview's file opens in the profile its remote-content choice says, so
  // a page moving within its folder can't carry the online profile with it.
  function moveToMatchingProfile(session, value) {
    if (session.kind !== "preview" || session.external || !session.view) return false;
    const file = localFilePath(value);
    if (!file || !isAllowedNavigation(session, value)) return false;
    const online = previewBelongsOnline(session, file);
    if ((session.online === true) === online) return false;
    setImmediate(() => moveToProfile(session, online, String(value)));
    return true;
  }

  function openPageLink(session, value, { background = false } = {}) {
    if (session.owner.isDestroyed()) return;
    let url;
    try { url = new URL(value); } catch { return; }
    if (url.protocol === "file:") {
      // Local previews hand links to the studio, which checks the destination's
      // own path grant and reports missing files. Never grant remote pages this route.
      if (session.kind !== "preview" || !/^file:/i.test(liveContents(session)?.getURL() || "")) return;
      // A link to a network share isn't a file on this computer.
      if (!localFilePath(url)) return;
      // A file from another app stays scripts-off through its own links, including
      // one back to itself, so a click can't reopen it (or a sibling) with scripts on.
      session.owner.send("gofer:browser-open-file", { href: url.toString(), fromPage: true, ...(session.external ? { external: true } : {}) });
      return;
    }
    if (!["http:", "https:"].includes(url.protocol)) return;
    if (!allowPageTab(session)) {
      log("warn", "Blocked a burst of new tabs from one page.");
      return;
    }
    // Pages and Rem never take the user's view; only user clicks may.
    const foreground = !background && !session.agent && userActivated(session);
    session.owner.send("gofer:browser-open-tab", { url: url.toString(), background: !foreground, engine: session.engine || "raticode", ...(session.privateBrowsing ? { private: true } : {}) });
  }

  function openWindow(session, details = {}) {
    const contents = liveContents(session);
    if (!contents) return { action: "deny" };
    if (session.kind === "preview") {
      // Only a link the user clicked opens a tab from a local file, never its
      // scripts, even right after a click (see handOffPreviewNavigation).
      openIfClickedLink(session, details.url, { background: details.disposition === "background-tab" });
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
      if (session.ownerId !== event.sender.id) continue;
      // A tab in the Browser's own window keeps that window's zoom.
      const zoom = hostZoomFactor(session, session.lastPlace?.host);
      if (session.ownerZoomFactor === zoom) continue;
      session.ownerZoomFactor = zoom;
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
    // A shared Gecko tab's history is Firefox's own.
    if (session.kind !== "user" || session.privateBrowsing || session.geckoShared || !/^https?:/i.test(String(url))) return;
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
      // The page reads the prompt from the tab state (promptsFor).
      emitState(session);
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
  // Rem

  // The browser Rem asked for, or the default from Settings > Browser. Rem
  // uses Raticode (gecko) only when it asks for it and the user lets it.
  function agentStreamEngine(requested) {
    const wanted = requested === "chromium" ? "raticode" : requested;
    if (wanted === "gecko") {
      if (!gecko) throw new Error("Rem can't use Raticode (gecko) in this version of Raticode.");
      if (!gecko.status().enabled) throw new Error(gecko.offMessage || "Turn on \"Let Rem use Raticode (gecko)\" in Settings > Browser.");
      return "gecko";
    }
    if (wanted === "raticode") return "";
    if (typeof wanted === "string" && wanted) {
      if (!streamEngines[wanted] || wanted === "gecko") throw new Error(`Rem can't open tabs in ${engineLabel(wanted)}. Use chromium, gecko, firefox, chrome or edge.`);
      return wanted;
    }
    return streamEngine();
  }

  function openAgentTab({ agent, label, engine: requested = "" }) {
    const mainWindow = getMainWindow();
    if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) {
      throw new Error("The Raticode window is closed. Open Raticode and retry.");
    }
    const owner = mainWindow.webContents;
    const streamed = agentStreamEngine(requested);
    if (streamed === "gecko") {
      const found = gecko.status();
      if (!found.executable || found.problem) throw new Error(found.problem || "Firefox was not found, so Raticode (gecko) can't start. Use browser: \"chromium\".");
    } else if (streamed && !streamEngines[streamed].status().available) {
      const name = engineLabel(streamed);
      throw new Error(`${name} is the default browser in Settings > Browser, but it is not installed. Ask the user to install ${name} or choose Raticode (chromium) as the default browser.`);
    }
    const session = baseSession({ id: crypto.randomUUID(), owner, kind: "rem", url: "about:blank", agent: { id: agent, label, shared: false } });
    sessions.set(session.id, session);
    registerOwnerCleanup(owner);
    if (streamed) createStreamContents(session, streamed);
    else {
      try {
        createView(session);
      } catch (error) {
        sessions.delete(session.id);
        throw error;
      }
    }
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
    // A shared Gecko tab has no Raticode tab of its own: Rem lets go of it,
    // and it stays open in Gecko's window.
    if (session.geckoShared) {
      detachAgent(session);
      closeSession(session);
      return;
    }
    detachAgent(session);
    emitState(session);
  }

  // ---------------------------------------------------------------------------
  // The user's Raticode (gecko) tab, shared with Rem
  //
  // Gecko is Firefox's own window, so its tabs aren't Raticode tabs. The user
  // shares the one in front; Rem drives it like a shared Raticode (chromium)
  // tab, with the same approvals, until the user stops sharing, closes it, or
  // Gecko stops. Private windows can't be shared (see gecko-control.cjs).
  async function shareGeckoTab(owner, threadId, label = "Rem") {
    if (!gecko) throw new Error("Raticode (gecko) is not available in this version of Raticode.");
    if (typeof threadId !== "string" || !threadId.trim()) throw new Error("Choose a Rem thread to share this tab with.");
    if (!gecko.status().enabled) throw new Error("Turn on \"Let Rem use Raticode (gecko)\" in Settings > Browser > Raticode (gecko) first.");
    const found = await gecko.shareableTab();
    if ([...sessions.values()].some((item) => item.geckoShared === found.context)) throw new Error("This Gecko tab is already shared with Rem.");
    const session = baseSession({
      id: crypto.randomUUID(),
      owner,
      kind: "user",
      url: found.url,
      agent: { id: remOwnerForThread(threadId.trim()), label: String(label || "Rem").slice(0, 60), shared: true },
    });
    session.geckoShared = found.context;
    session.agentUsedAt = Date.now();
    session.agentLog = [{ action: "shared", detail: "You shared this Gecko tab", at: Date.now() }];
    sessions.set(session.id, session);
    registerOwnerCleanup(owner);
    const contents = createStreamContents(session, "gecko", { adopt: found.context });
    try {
      await contents.ready;
    } catch (error) {
      closeSession(session);
      throw error;
    }
    notifyGeckoShared();
    return { id: session.id, url: contents.getURL() || found.url, title: contents.getTitle() || found.title || "" };
  }

  function geckoSharedTabs() {
    return [...sessions.values()].filter((item) => item.geckoShared && item.agent).map((session) => {
      const contents = liveContents(session);
      return {
        id: session.id,
        url: contents?.getURL() || session.initialUrl || "",
        title: contents?.getTitle() || "",
        agent: { label: session.agent.label, shared: true, paused: session.agentPaused, dialog: session.agentDialog || null, log: (session.agentLog || []).slice(-20) },
        prompts: promptsFor(session),
      };
    });
  }

  function notifyGeckoShared() {
    try { onGeckoShared(geckoSharedTabs()); } catch { /* A window is closing. */ }
  }

  // The controls for a shared Gecko tab, from whichever window shows Gecko.
  function geckoSharedAction(options = {}) {
    const session = typeof options.id === "string" ? sessions.get(options.id) : null;
    if (!session?.geckoShared) throw new Error("That Gecko tab isn't shared with Rem anymore.");
    switch (options.command) {
      case "stop":
        stopSharing(session);
        break;
      case "pause":
        session.agentPaused = options.paused === true;
        recordAgentActivity(session, { action: session.agentPaused ? "paused" : "resumed", detail: "by you", at: Date.now() });
        break;
      case "respond": {
        const pending = prompts.get(String(options.requestId || ""));
        if (!pending || pending.session !== session) throw new Error("That request has already been answered.");
        settlePrompt(String(options.requestId), sanitizePromptResponse(pending.kind, options.response));
        break;
      }
      default:
        throw new Error(`Unknown action for a shared Gecko tab: ${options.command || "missing"}`);
    }
    return geckoSharedTabs();
  }

  // Ask Rem: the page's address, title, selection and readable text.
  async function pageContext(session, selection = "") {
    const contents = liveContents(session);
    if (!contents) throw new Error("Browser view is unavailable.");
    let text = "";
    let pageSelection = selection;
    try {
      // The same reader as Rem's page snapshots, so text the page hides
      // (transparent, covered, off the page) doesn't reach Rem this way either.
      const result = await Promise.race([
        contents.executeJavaScriptInIsolatedWorld(PAGE_CONTEXT_WORLD_ID, [{
          code: `${PAGE_AGENT};__raticodeAgent("readable", { max: 40000 })`,
        }]),
        new Promise((resolve) => setTimeout(() => resolve(null), 3000)),
      ]);
      const read = result?.value || {};
      text = `${read.hidden ? "[hidden text skipped]\n" : ""}${String(read.text || "")}`;
      pageSelection = pageSelection || String(read.selection || "");
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
    // Only Rem's Gecko tabs are sessions here; the user's Gecko tab never is.
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
    for (const [name, engine] of Object.entries(streamEngines)) if (name !== "gecko") status[name] = engine.status();
    return status;
  }

  function createStreamContents(session, name, { adopt = "" } = {}) {
    const engine = streamEngines[name];
    const Contents = name === "firefox" || name === "gecko" ? FirefoxContents : ChromiumContents;
    const contents = new Contents({
      engine,
      ...(adopt ? { adopt } : {}),
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
    // A shared tab already shows its page.
    if (adopt) contents.url = session.initialUrl || contents.url;
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
    // A shared Gecko tab's dialog shows in Gecko's own window, where the user
    // answers it, or Rem does with browser_handle_dialog.
    if (session.geckoShared) return;
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

  // Firefox or Chrome stopped. Its tabs stay open and start it again when reloaded.
  function streamStopped(name, reason = `${engineLabel(name)} stopped`) {
    for (const session of [...sessions.values()]) {
      // A shared Gecko tab ends with Gecko; the user shares it again.
      if (session.geckoShared && session.engine === name) closeSession(session);
      else if (session.engine === name) liveContents(session)?.markDead(reason);
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
      // A file another app opened: no scripts, never online.
      external: session.external === true,
      favicon: session.favicon || "",
      fullscreen: session.fullscreen === true,
      id: session.id,
      loadError: session.loadError,
      muted: session.muted,
      profile: session.kind,
      prompts: promptsFor(session),
      // A local HTML preview's web content: blocked requests on this page, and
      // whether the user let this file load it.
      remoteContent: session.kind === "preview" ? { allowed: remoteContentAllowed(session), blocked: remoteBlockedCount(session) } : null,
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
    if (session.geckoShared && sessions.get(session.id) === session) notifyGeckoShared();
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
    if ((session.engine === "firefox" || session.engine === "gecko") && /^\s*(?:about:(?!blank\s*$)|moz-extension:|chrome:|resource:)/i.test(String(value ?? ""))) {
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
      const file = localFilePath(normalizedUrl);
      if (!file) throw new Error(NETWORK_PATH_ERROR);
      resolvePath(file, { mustExist: true });
      session.owner.send("gofer:browser-open-file", { href: normalizedUrl });
      return Promise.resolve();
    }
    // A preview's profile is offline; a website typed there opens in a Browser tab.
    if (session.kind === "preview" && /^https?:/i.test(normalizedUrl)) {
      session.owner.send("gofer:browser-open-tab", { url: normalizedUrl, background: false, engine: session.engine || "raticode" });
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
    if (["close", "adopt", "focus", "set-preferences", "viewport", "prompt-response", "firefox-view", "firefox-input", "frame-ack", "view-detached", "media-control", "place", "view-snapshot"].includes(options.action) && !sessions.has(options.id)) {
      return { closed: true, ready: false };
    }
    const session = ownedSession(event, options.id);
    switch (options.action) {
      case "adopt":
        // Rem's tabs are created before the studio picks their tab identity.
        if (session.agent && !session.clientId && typeof options.clientId === "string") session.clientId = options.clientId.slice(0, 200);
        // The studio has drawn the tab again: a Firefox or Chrome tab streams its
        // frames there, and a Raticode (chromium) view follows its slot.
        if (isStreamed(session) || session.view) {
          clearTimeout(session.detachTimer);
          return sessionState(session);
        }
        throw new Error("Browser view is unavailable.");
      case "place":
        return placeView(session, options);
      case "view-detached":
        if (!session.view) break;
        session.view.setVisible(false);
        session.viewShown = false;
        // Closing Rem's tab in the studio removes its slot. A remount draws
        // it again within moments; otherwise the tab is gone.
        if (session.agent && !session.agent.shared) {
          clearTimeout(session.detachTimer);
          session.detachTimer = setTimeout(() => { if (sessions.get(session.id) === session) closeAgentTab(session); }, VIEW_DETACH_GRACE_MS);
          session.detachTimer.unref?.();
        }
        return { ok: true };
      case "view-snapshot":
        return viewSnapshot(session);
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
      case "preview-remote":
        return setPreviewRemote(session, options.allowed !== false);
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
        if (name && name !== "firefox" && name !== "gecko" && streamEngines[name]) {
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
    geckoSharedAction,
    geckoSharedTabs,
    shareGeckoTab,
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
    returnViews,
    install,
    normalizeUrl: (value) => normalizeBrowserUrl(value, policy.searchUrl),
    openAgentTab,
    ownerZoom,
    policy: () => policy,
    recordAgentActivity,
    setPolicy,
    setViewportListener: (listener) => { onViewportChanged = listener; },
    firefoxStopped,
    reviveFirefoxTabs,
    reviveStreamTabs,
    streamStopped,
  };
}

// Electron reuses one Session object per partition; the storage path also
// identifies persistent profiles if a different wrapper is ever returned.
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
