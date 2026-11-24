require("./test-tmp.cjs");
// Exercises browser-sessions.cjs against small Electron stand-ins, so the
// policy wiring is covered without launching Chromium.
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { createBrowserSessions, remOwnerForThread, sameProfile, sanitizePromptResponse } = require("../browser-sessions.cjs");
const { createBrowserStore } = require("../browser-store.cjs");
const { browserContentZoomFactor } = require("../browser-utils.cjs");
const { PARTITIONS } = require("../browser-policy.cjs");

let nextId = 100;

class FakeProfile extends EventEmitter {
  constructor(name) {
    super();
    this.name = name;
    this.cookieList = [];
    this.cookies = Object.assign(new EventEmitter(), {
      get: async () => this.cookieList.map((cookie) => ({ ...cookie })),
      set: async (cookie) => {
        this.cookieList = this.cookieList.filter((item) => !(item.name === cookie.name && item.domain === (cookie.domain || new URL(cookie.url).hostname)));
        this.cookieList.push({ ...cookie, domain: cookie.domain || new URL(cookie.url).hostname });
      },
      remove: async (url, name) => {
        const host = new URL(url).hostname;
        this.cookieList = this.cookieList.filter((item) => !(item.name === name && item.domain.replace(/^\./, "") === host));
      },
    });
    this.availableSpellCheckerLanguages = ["en-US"];
    this.webRequest = {
      onBeforeSendHeaders: (filter, listener) => { this.beforeSendHeaders = { filter, listener }; },
      onHeadersReceived: (filter, listener) => { this.headersReceived = { filter, listener }; },
      onBeforeRequest: (filter, listener) => { this.beforeRequest = { filter, listener }; },
    };
  }
  getUserAgent() { return this.userAgent || "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Raticode/0.3.9 Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36"; }
  setUserAgent(value, acceptLanguages) { this.userAgent = value; this.acceptLanguages = acceptLanguages; }
  setPermissionRequestHandler(handler) { this.requestHandler = handler; }
  setPermissionCheckHandler(handler) { this.checkHandler = handler; }
  setDevicePermissionHandler(handler) { this.deviceHandler = handler; }
  setCertificateVerifyProc(proc) { this.verifyProc = proc; }
  setSpellCheckerEnabled(value) { this.spellcheck = value; }
  setProxy(config) { this.proxy = config; }
  setSpellCheckerLanguages(value) { this.languages = value; }
  async clearStorageData() { this.cookieList = []; }
  async clearCache() { this.cacheCleared = true; }
  async clearAuthCache() { this.authCleared = true; }
}

class FakeContents extends EventEmitter {
  constructor({ host, profile }) {
    super();
    this.id = nextId++;
    this.hostWebContents = host;
    this.session = profile;
    this.url = "about:blank";
    this.title = "";
    this.entries = [{ url: "about:blank", title: "" }];
    this.index = 0;
    this.loaded = [];
    this.zoom = 1;
    const contents = this;
    this.navigationHistory = {
      canGoBack: () => contents.index > 0,
      canGoForward: () => contents.index < contents.entries.length - 1,
      goBack: () => contents.go(contents.index - 1),
      goForward: () => contents.go(contents.index + 1),
      goToIndex: (index) => contents.go(index),
      length: () => contents.entries.length,
      getActiveIndex: () => contents.index,
      getAllEntries: () => contents.entries.map((entry) => ({ ...entry })),
      restore: async (history) => { contents.restored = history; contents.entries = history.entries; contents.index = history.index; contents.url = history.entries[history.index].url; },
    };
    this.debugger = { isAttached: () => false, detach: () => {} };
    this.mainFrame = { name: "main" };
    this.downloads = [];
  }
  downloadURL(url) { this.downloads.push(url); }
  go(index) { this.index = index; this.url = this.entries[index].url; this.emit("did-navigate", {}, this.url); }
  getType() { return "window"; }
  isDestroyed() { return this.destroyed === true; }
  close() { if (this.destroyed) return; this.destroyed = true; this.emit("destroyed"); }
  async capturePage() { return { isEmpty: () => false, toJPEG: () => Buffer.from("jpeg") }; }
  setBackgroundColor() {}
  setBackgroundThrottling(value) { this.throttling = value; }
  setZoomFactor(value) { this.zoom = value; }
  setWindowOpenHandler(handler) { this.openHandler = handler; }
  setWebRTCIPHandlingPolicy(value) { this.webrtcPolicy = value; }
  getURL() { return this.url; }
  getTitle() { return this.title; }
  isLoading() { return false; }
  isFocused() { return false; }
  focus() {}
  async loadURL(url) {
    this.loaded.push(url);
    // As in Chromium, a page's first navigation replaces its initial blank entry.
    if (this.entries.length === 1 && this.entries[0].url === "about:blank") this.entries = [];
    this.entries = [...this.entries.slice(0, this.index + 1), { url, title: "" }];
    this.index = this.entries.length - 1;
    this.url = url;
    this.emit("did-navigate", {}, url);
  }
  reload() { this.reloaded = "soft"; }
  reloadIgnoringCache() { this.reloaded = "hard"; }
  stop() {}
  setAudioMuted(value) { this.muted = value; }
  isCurrentlyAudible() { return false; }
  input(type = "mouseDown") { this.emit("input-event", {}, { type }); }
}

// A Raticode (chromium) tab's view, as browser-sessions.cjs makes it.
function fakeViewClass(owner, profiles, contentsById, created, hooks = {}) {
  return class FakeView {
    constructor({ webPreferences }) {
      this.webPreferences = webPreferences;
      this.webContents = new FakeContents({ host: owner, profile: profiles[webPreferences.partition] });
      this.bounds = null;
      this.visible = true;
      contentsById.set(this.webContents.id, this.webContents);
      created.push(this);
      // A test's set-up for the page, before Raticode wires it up.
      const prepare = hooks.prepare;
      hooks.prepare = null;
      prepare?.(this.webContents);
    }
    setBounds(bounds) { this.bounds = { ...bounds }; }
    setVisible(value) { this.visible = value; }
    setBackgroundColor(value) { this.background = value; }
  };
}

function fakeWindow(name) {
  const children = [];
  return {
    name,
    children,
    destroyed: false,
    isDestroyed() { return this.destroyed; },
    contentView: {
      addChildView(view) { if (view.parentWindow && view.parentWindow !== this.owner) view.parentWindow.contentView.removeChildView(view); if (!children.includes(view)) children.push(view); view.parentWindow = this.owner; },
      removeChildView(view) { const index = children.indexOf(view); if (index >= 0) children.splice(index, 1); },
    },
  };
}

function harness({ policy, dialog: dialogOverrides = {}, openWith = null, logs = null, firefox = null, chrome = null, chromium = {}, gecko = null, quarantine = "", beforeInstall = null, resolvePath = null, resolvePreviewPath = null, createSessions = createBrowserSessions } = {}) {
  const profiles = Object.fromEntries(Object.values(PARTITIONS).map((name) => [name, new FakeProfile(name)]));
  const owner = Object.assign(new EventEmitter(), {
    id: 1,
    sent: [],
    send(channel, payload) { this.sent.push([channel, payload]); },
    isDestroyed: () => false,
    getZoomFactor: () => 1,
    focus() {},
  });
  const mainWindow = Object.assign(fakeWindow("main"), { webContents: owner });
  mainWindow.contentView.owner = mainWindow;
  // The Browser's own window shows the studio's DOM in a page of its own, with its own zoom.
  const popout = Object.assign(fakeWindow("popout"), { webContents: { id: 2, zoom: 1, isDestroyed: () => false, getZoomFactor() { return this.zoom; } } });
  popout.contentView.owner = popout;
  let popoutOpen = false;
  const views = [];
  const viewHooks = {};
  const contentsById = new Map();
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "browser-sessions-"));
  const downloads = path.join(directory, "Downloads");
  fs.mkdirSync(downloads);
  const store = createBrowserStore(path.join(directory, "profile"), { writeDelayMs: 1 });
  const opened = [];
  const messages = [];
  const menus = [];
  const app = {
    getPath: (name) => (name === "downloads" ? downloads : directory),
    getLocale: () => "en-US",
    getPreferredSystemLanguages: () => ["fr-FR", "en-US"],
    userAgentFallback: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Raticode/0.3.9 Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36",
  };
  const browsers = createSessions({
    app,
    Menu: { buildFromTemplate: (template) => { menus.push(template); return { template, popup() {} }; } },
    clipboard: { writeText() {}, readText: () => "from the clipboard" },
    dialog: {
      showMessageBoxSync: () => 0,
      showMessageBox: async (_owner, options) => { messages.push(options); return { response: dialogOverrides.messageResponse ?? 1 }; },
      showSaveDialog: async () => (dialogOverrides.savePath ? { canceled: false, filePath: dialogOverrides.savePath } : { canceled: true }),
      showOpenDialog: async () => (dialogOverrides.openPaths ? { canceled: false, filePaths: dialogOverrides.openPaths } : { canceled: true }),
    },
    shell: { openExternal: async (url) => opened.push(url), openPath: async (target) => opened.push(target), showItemInFolder() {} },
    session: { fromPartition: (name) => profiles[name] },
    webContents: { fromId: (id) => contentsById.get(id) },
    getMainWindow: () => mainWindow,
    resolvePath: resolvePath || ((target) => {
      if (!fs.existsSync(target)) throw new Error(`Path does not exist: ${target}`);
      return target;
    }),
    ...(resolvePreviewPath ? { resolvePreviewPath } : {}),
    store,
    openWith,
    quarantineDir: quarantine || path.join(directory, "quarantine"),
    platform: "linux",
    chromeVersion: "156.0.8078.49",
    firefox,
    chrome,
    chromium,
    gecko,
    privateCleanupDelayMs: 1,
    WebContentsView: fakeViewClass(owner, profiles, contentsById, views, viewHooks),
    preloadPath: "/app/browser-preload.cjs",
    getHostWindow: (host) => (host === "popout" && popoutOpen ? popout : null),
    ...(logs ? { log: (level, message) => logs.push([level, message]) } : {}),
  });
  beforeInstall?.(store);
  browsers.install();
  if (policy) browsers.setPolicy(null, policy);
  const event = { sender: owner };
  // The main process makes each tab's view; the studio then draws its slot.
  function open(options, prepare = () => {}) {
    viewHooks.prepare = prepare;
    const state = browsers.createBrowser(event, options);
    viewHooks.prepare = null;
    const view = views.at(-1);
    const contents = view?.webContents;
    // The tab's first page counts as loaded, as a webview's src did; tests look at later loads.
    if (contents) contents.loaded = [];
    browsers.browserAction(event, { id: state.id, action: "adopt", webContentsId: -1 });
    return { state, contents, view, id: state.id };
  }
  function openRem(threadOwner = "agent-0123456789") {
    const session = browsers.openAgentTab({ agent: threadOwner, label: "Rem" });
    const view = views.at(-1);
    const contents = view.webContents;
    browsers.browserAction(event, { id: session.id, action: "adopt", webContentsId: -1 });
    return { session, contents, view, id: session.id };
  }
  const sent = (channel) => owner.sent.filter(([name]) => name === channel).map(([, payload]) => payload);
  return { app, browsers, profiles, owner, event, open, openRem, sent, store, directory, downloads, opened, contentsById, messages, menus, views, mainWindow, popout, setPopoutOpen: (value) => { popoutOpen = value; } };
}

test("tabs present a consistent Chromium identity and Electron's own to Google sign-in", async () => {
  const { app, browsers, open, profiles } = harness();
  const chromium = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36";
  // Pop-ups opened with window.open use the app-wide fallback.
  assert.equal(app.userAgentFallback, chromium);
  for (const name of [PARTITIONS.user, PARTITIONS.rem]) {
    const profile = profiles[name];
    assert.equal(profile.userAgent, chromium);
    assert.equal(profile.acceptLanguages, "fr-FR,fr,en-US,en");
    assert.ok(profile.beforeSendHeaders.filter.urls.includes("https://*/*"));
  }
  assert.equal(profiles[PARTITIONS.preview].beforeSendHeaders, undefined);
  const send = (details) => new Promise((resolve) => profiles[PARTITIONS.user].beforeSendHeaders.listener(details, resolve));
  const page = await send({ url: "https://github.com/login", resourceType: "mainFrame", requestHeaders: { "User-Agent": chromium } });
  assert.equal(page.requestHeaders["sec-ch-ua"], "\"Not:A-Brand\";v=\"8\", \"Chromium\";v=\"156\"");
  assert.equal(page.requestHeaders["sec-ch-ua-platform"], "\"Linux\"");
  assert.equal(page.requestHeaders["User-Agent"], chromium);
  const google = await send({ url: "https://accounts.google.com/v3/signin/identifier", resourceType: "mainFrame", requestHeaders: { "User-Agent": chromium } });
  assert.equal(google.requestHeaders["User-Agent"], "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36");

  // Accept-CH opts a site in; Critical-CH replays the navigation once.
  const receive = (details) => new Promise((resolve) => profiles[PARTITIONS.user].headersReceived.listener(details, resolve));
  const critical = await receive({
    url: "https://app.test/",
    method: "GET",
    resourceType: "mainFrame",
    responseHeaders: { "Accept-CH": ["Sec-CH-UA-Arch"], "Critical-CH": ["Sec-CH-UA-Arch"], Location: ["https://elsewhere.test/"], "Set-Cookie": ["a=1"] },
  });
  assert.equal(critical.statusLine, "HTTP/1.1 307 Temporary Redirect");
  assert.deepEqual(critical.responseHeaders.Location, ["https://app.test/"]);
  assert.deepEqual(critical.responseHeaders["Set-Cookie"], ["a=1"]);
  const replay = await send({ url: "https://app.test/", resourceType: "mainFrame", requestHeaders: {} });
  assert.equal(replay.requestHeaders["sec-ch-ua-arch"], "\"x86\"");
  assert.deepEqual(await receive({ url: "https://app.test/", method: "GET", resourceType: "mainFrame", responseHeaders: { "Accept-CH": ["Sec-CH-UA-Arch"], "Critical-CH": ["Sec-CH-UA-Arch"] } }), {});

  // Clearing a site's data forgets its client hint opt-in, as Chrome does.
  const tab = open({ url: "https://app.test/" });
  await browsers.browserAction({ sender: tab.contents.hostWebContents }, { id: tab.id, action: "clear-site-data" });
  const cleared = await send({ url: "https://app.test/", resourceType: "mainFrame", requestHeaders: {} });
  assert.equal(cleared.requestHeaders["sec-ch-ua-arch"], undefined);
});

test("a reloaded or crashed studio closes the tabs it had instead of leaving them running", () => {
  const { open, owner } = harness();
  const first = open({ url: "https://one.example/" });
  const second = open({ url: "https://two.example/", private: true });
  // The studio's own in-page navigation keeps every tab.
  owner.emit("did-start-navigation", { isMainFrame: true, isSameDocument: true });
  owner.emit("did-start-navigation", { isMainFrame: false, isSameDocument: false });
  assert.equal(first.contents.isDestroyed(), false);
  owner.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.equal(first.contents.isDestroyed(), true);
  assert.equal(second.contents.isDestroyed(), true);
  // The restored tabs are new, and a renderer crash closes them too.
  const restored = open({ url: "https://one.example/" });
  owner.emit("render-process-gone", {}, { reason: "crashed" });
  assert.equal(restored.contents.isDestroyed(), true);
});

test("tabs, previews and Rem use separate profiles", () => {
  const { browsers, open, openRem, profiles, directory, owner, event, contentsById } = harness();
  const web = open({ url: "example.com" });
  assert.equal(web.state.partition, PARTITIONS.user);
  assert.equal(web.contents.getURL(), "https://example.com/");
  const file = path.join(directory, "report.html");
  fs.writeFileSync(file, "<h1>Report</h1>");
  assert.equal(open({ path: file }).state.partition, PARTITIONS.preview);
  assert.equal(open({ url: file }).state.partition, PARTITIONS.preview);
  const rem = openRem();
  assert.equal(rem.contents.session, profiles[PARTITIONS.rem]);
  assert.equal(profiles[PARTITIONS.user].userAgent.includes("Electron"), false);
  // Each view is made in its tab's profile, locked down, and no <webview> may attach.
  assert.equal(web.view.webPreferences.partition, PARTITIONS.user);
  assert.equal(rem.view.webPreferences.partition, PARTITIONS.rem);
  assert.equal(open({ url: "https://p.test/", private: true }).view.webPreferences.partition, PARTITIONS.private);
  assert.deepEqual(
    Object.fromEntries(["sandbox", "contextIsolation", "nodeIntegration", "nodeIntegrationInSubFrames", "webSecurity", "allowRunningInsecureContent", "webviewTag", "preload"].map((key) => [key, web.view.webPreferences[key]])),
    { sandbox: true, contextIsolation: true, nodeIntegration: false, nodeIntegrationInSubFrames: false, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, preload: "/app/browser-preload.cjs" },
  );
  assert.equal(browsers.isPendingAttach(owner.id, { src: "about:blank", partition: PARTITIONS.user }), false);
  // A tab can't adopt a page it didn't make.
  const wrong = new FakeContents({ host: owner, profile: profiles[PARTITIONS.user] });
  contentsById.set(wrong.id, wrong);
  assert.equal(browsers.browserAction(event, { id: web.id, action: "adopt", webContentsId: wrong.id }).id, web.id);
  assert.equal(web.contents.isDestroyed(), false);
});

const privateSettle = () => new Promise((resolve) => setTimeout(resolve, 20));

test("private tabs: their own in-memory profile, and nothing in the saved browser data", async () => {
  const { browsers, open, profiles, event, store, sent, directory, menus } = harness();
  const normal = open({ url: "https://normal.example/" });
  const tab = open({ url: "https://secret.example/", private: true });
  assert.equal(tab.state.partition, PARTITIONS.private);
  assert.equal(PARTITIONS.private.startsWith("persist:"), false);
  assert.equal(tab.contents.session, profiles[PARTITIONS.private]);
  assert.equal(normal.contents.session, profiles[PARTITIONS.user]);
  // The same identity as the user's own tabs, so private tabs don't announce Electron.
  assert.equal(profiles[PARTITIONS.private].userAgent, profiles[PARTITIONS.user].userAgent);

  // History and titles.
  browsers.browserAction(event, { id: tab.id, action: "navigate", url: "https://secret.example/next", typed: true });
  tab.contents.emit("page-title-updated", {}, "Secret title");
  assert.deepEqual(store.searchHistory("").map((item) => item.url), ["https://normal.example/"]);

  // Permissions are asked again, kept for the private session only, and aren't taken from saved ones.
  store.setPermission("https://secret.example", "notifications", "allow");
  const handler = profiles[PARTITIONS.private].requestHandler;
  const granted = new Promise((resolve) => handler(tab.contents, "geolocation", resolve, { requestingUrl: "https://secret.example/" }));
  await privateSettle();
  const request = sent("gofer:browser-prompt").at(-1);
  browsers.browserAction(event, { id: tab.id, action: "prompt-response", requestId: request.requestId, response: "allow" });
  assert.equal(await granted, true);
  assert.equal(profiles[PARTITIONS.private].checkHandler(tab.contents, "geolocation", "", { requestingUrl: "https://secret.example/" }), true);
  assert.equal(profiles[PARTITIONS.private].checkHandler(tab.contents, "notifications", "", { requestingUrl: "https://secret.example/" }), false);
  assert.deepEqual(store.site("https://secret.example").permissions, { notifications: "allow" });

  // Links stay private, and Rem can't be given the tab.
  tab.contents.input();
  tab.contents.openHandler({ url: "https://secret.example/child", disposition: "foreground-tab", features: "", frameName: "" });
  assert.equal(sent("gofer:browser-open-tab").at(-1)?.private, true);
  await assert.rejects(async () => browsers.browserAction(event, { id: tab.id, action: "share-with-rem", label: "Rem" }));

  // A right-click offers no route to Rem.
  const params = { x: 1, y: 1, selectionText: "", linkURL: "", mediaType: "none" };
  tab.contents.emit("context-menu", {}, { ...params, frame: tab.contents.mainFrame });
  assert.equal(menus.at(-1).some((item) => item.label === "Ask Rem about this"), false);
  normal.contents.emit("context-menu", {}, { ...params, frame: normal.contents.mainFrame });
  assert.equal(menus.at(-1).some((item) => item.label === "Ask Rem about this"), true);

  // Clearing a private tab's site data can't touch the user's profile.
  await profiles[PARTITIONS.user].cookies.set({ url: "https://secret.example/", name: "saved", value: "1" });
  await profiles[PARTITIONS.private].cookies.set({ url: "https://secret.example/", name: "temp", value: "1" });
  await browsers.browserAction(event, { id: tab.id, action: "clear-site-data" });
  assert.deepEqual((await profiles[PARTITIONS.private].cookies.get({})).map((item) => item.name), []);
  assert.deepEqual((await profiles[PARTITIONS.user].cookies.get({})).map((item) => item.name), ["saved"]);

  // Downloads are listed while it runs and never written down.
  profiles[PARTITIONS.private].emit("will-download", { preventDefault() {} }, fakeDownload("private.pdf"), tab.contents);
  assert.equal(store.listDownloads().at(0).private, true);
  store.flush();
  assert.doesNotMatch(fs.readFileSync(path.join(directory, "profile", "downloads.json"), "utf8"), /private\.pdf/);

  // The last private tab takes everything with it; a second one keeps it alive.
  const second = open({ url: "https://secret.example/two", private: true });
  await profiles[PARTITIONS.private].cookies.set({ url: "https://secret.example/", name: "live", value: "1" });
  browsers.browserAction(event, { id: tab.id, action: "close" });
  await privateSettle();
  assert.deepEqual((await profiles[PARTITIONS.private].cookies.get({})).map((item) => item.name), ["live"]);
  browsers.browserAction(event, { id: second.id, action: "close" });
  await privateSettle();
  assert.deepEqual(await profiles[PARTITIONS.private].cookies.get({}), []);
  assert.equal(profiles[PARTITIONS.private].cacheCleared, true);
  assert.equal(profiles[PARTITIONS.private].authCleared, true);
  assert.equal(store.listDownloads().some((item) => item.private), false);
  assert.deepEqual((await profiles[PARTITIONS.user].cookies.get({})).map((item) => item.name), ["saved"]);
});

test("private downloads left behind: dangerous files go, unfinished ones stop, and a normal one stays", async () => {
  const { browsers, open, profiles, event, store } = harness();
  const tab = open({ url: "https://secret.example/", private: true });
  const normal = open({ url: "https://normal.example/" });
  const held = fakeDownload("private-tool.exe");
  profiles[PARTITIONS.private].emit("will-download", { preventDefault() {} }, held, tab.contents);
  fs.mkdirSync(path.dirname(held.savePath), { recursive: true });
  fs.writeFileSync(held.savePath, "x");
  held.emit("done", {}, "completed");
  const unfinished = Object.assign(fakeDownload("private-big.zip"), { cancelled: 0, cancel() { this.cancelled += 1; } });
  profiles[PARTITIONS.private].emit("will-download", { preventDefault() {} }, unfinished, tab.contents);
  const kept = fakeDownload("normal-tool.exe");
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, kept, normal.contents);
  fs.writeFileSync(kept.savePath, "y");
  kept.emit("done", {}, "completed");
  assert.equal(fs.existsSync(held.savePath), true);

  browsers.browserAction(event, { id: tab.id, action: "close" });
  await privateSettle();
  assert.equal(fs.existsSync(held.savePath), false);
  assert.equal(unfinished.cancelled, 1);
  unfinished.emit("done", {}, "cancelled");
  assert.deepEqual(store.listDownloads().map((item) => item.filename), ["normal-tool.exe"]);
  assert.equal(fs.existsSync(kept.savePath), true);
});

test("a quarantined file nothing lists is removed at startup, and a listed one stays", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "browser-quarantine-"));
  const stray = path.join(dir, "stray.download");
  const listed = path.join(dir, "listed.download");
  const other = path.join(dir, "keep.txt");
  for (const file of [stray, listed, other]) fs.writeFileSync(file, "x");
  const { browsers, profiles, store } = harness({ quarantine: dir, beforeInstall: (items) => items.upsertDownload({ id: "a", filename: "listed", state: "quarantined", quarantinePath: listed }) });
  assert.ok(browsers && profiles && store);
  assert.equal(fs.existsSync(stray), false);
  assert.equal(fs.existsSync(listed), true);
  assert.equal(fs.existsSync(other), true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("private tabs never go to Firefox, Chrome or Edge", () => {
  const { browsers, event, profiles } = harness({ policy: { engine: "chrome" }, chrome: { browser: { name: "Chrome" }, status: () => ({ available: true }) } });
  const state = browsers.createBrowser(event, { url: "https://secret.example/", private: true });
  assert.equal(state.partition, PARTITIONS.private);
  assert.ok(profiles[PARTITIONS.private].requestHandler);
});

test("typed local paths open as previews instead of loading in a web tab", async () => {
  const { browsers, open, event, sent, directory } = harness();
  const web = open({ url: "https://example.com/" });
  const file = path.join(directory, "notes.html");
  fs.writeFileSync(file, "x");
  browsers.browserAction(event, { id: web.id, action: "navigate", url: file });
  assert.deepEqual(sent("gofer:browser-open-file"), [{ href: `file://${file}` }]);
  assert.deepEqual(web.contents.loaded, []);
});

test("navigation guards block local files, data URLs and Rem's blocked sites", () => {
  const { open, openRem } = harness({ policy: { remBlockedSites: ["bank.test"] } });
  const web = open({ url: "https://example.com/" });
  const check = (contents, url) => {
    let prevented = false;
    contents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, url);
    return prevented;
  };
  assert.equal(check(web.contents, "https://other.test/"), false);
  assert.equal(check(web.contents, "file:///etc/passwd"), true);
  assert.equal(check(web.contents, "data:text/html,<script>alert(1)</script>"), true);
  // App links reach Chromium's own permission prompt; risky handlers do not.
  assert.equal(check(web.contents, "mailto:team@example.com"), false);
  assert.equal(check(web.contents, "ms-msdt:/id PCWDiagnostic"), true);
  const rem = openRem();
  assert.equal(check(rem.contents, "https://bank.test/login"), true);
  assert.equal(check(rem.contents, "https://example.com/"), false);
  assert.equal(check(rem.contents, "mailto:team@example.com"), true);
});

test("popups need real user input; sign-in popups become windows", () => {
  const { open, sent, browsers, event } = harness();
  const web = open({ url: "https://example.com/" });
  const handler = web.contents.openHandler;
  assert.deepEqual(handler({ url: "https://ads.test/", disposition: "foreground-tab", features: "" }), { action: "deny" });
  assert.deepEqual(browsers.browserAction(event, { id: web.id, action: "site-info" }).popups, "ask");
  const state = sent("gofer:browser-state").at(-1);
  assert.deepEqual(state.blockedPopups, [{ url: "https://ads.test/" }]);
  web.contents.input("mouseDown");
  assert.deepEqual(handler({ url: "https://example.com/docs", disposition: "foreground-tab", features: "" }), { action: "deny" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/docs", background: false, engine: "raticode" });
  handler({ url: "https://example.com/bg", disposition: "background-tab", features: "" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/bg", background: true, engine: "raticode" });
  const popup = handler({ url: "https://accounts.test/oauth", disposition: "new-window", features: "width=500,height=600" });
  assert.equal(popup.action, "allow");
  assert.equal(popup.overrideBrowserWindowOptions.webPreferences.sandbox, true);
  assert.equal(popup.overrideBrowserWindowOptions.width, 500);
  // Allowing pop-ups for a site opens what was blocked and remembers it.
  browsers.browserAction(event, { id: web.id, action: "allow-popups" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://ads.test/", background: false, engine: "raticode" });
  assert.equal(browsers.browserAction(event, { id: web.id, action: "site-info" }).popups, "allow");
});

test("a page in Rem's tab can't use window.open to reach a site Rem may not open", () => {
  const { openRem } = harness({ policy: { remBlockedSites: ["bank.test"] } });
  const rem = openRem();
  const loaded = [];
  const load = rem.contents.loadURL.bind(rem.contents);
  rem.contents.loadURL = (url) => { loaded.push(url); return load(url); };
  const handler = rem.contents.openHandler;
  assert.deepEqual(handler({ url: "https://bank.test/transfer", disposition: "foreground-tab", features: "" }), { action: "deny" });
  assert.deepEqual(loaded, []);
  assert.deepEqual(handler({ url: "https://example.com/next", disposition: "foreground-tab", features: "" }), { action: "deny" });
  assert.deepEqual(loaded, ["https://example.com/next"]);
});

test("page links open in the background for Rem and are rate limited", () => {
  const { openRem, open, sent } = harness();
  const rem = openRem();
  const ipc = (contents, url, disposition = "foreground") => contents.emit("ipc-message", { senderFrame: contents.mainFrame }, "gofer:browser-link-clicked", { url, disposition });
  rem.contents.input("mouseDown");
  ipc(rem.contents, "https://example.com/a");
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/a", background: true, engine: "raticode" });
  const web = open({ url: "https://example.com/" });
  for (let index = 0; index < 30; index += 1) ipc(web.contents, `https://example.com/${index}`);
  assert.equal(sent("gofer:browser-open-tab").length, 11);
  // Links to local files never leave a web page.
  ipc(web.contents, "file:///etc/passwd");
  assert.equal(sent("gofer:browser-open-file").length, 0);
});

test("permission prompts are remembered per site; Rem is never asked", async () => {
  const { open, openRem, profiles, sent, browsers, event, store } = harness();
  const web = open({ url: "https://meet.example.com/room" });
  const handler = profiles[PARTITIONS.user].requestHandler;
  const granted = new Promise((resolve) => handler(web.contents, "media", resolve, { mediaTypes: ["video", "audio"], requestingUrl: "https://meet.example.com/room" }));
  await new Promise((resolve) => setImmediate(resolve));
  const request = sent("gofer:browser-prompt").at(-1);
  assert.equal(request.kind, "permission");
  assert.equal(request.origin, "https://meet.example.com");
  assert.deepEqual(request.permissions.map((item) => item.key), ["camera", "microphone"]);
  assert.equal(sent("gofer:browser-state").at(-1).prompts.length, 1);
  assert.deepEqual(browsers.browserAction(event, { id: web.id, action: "prompt-response", requestId: request.requestId, response: "allow" }), { settled: true });
  assert.equal(await granted, true);
  assert.deepEqual(store.site("https://meet.example.com").permissions, { camera: "allow", microphone: "allow" });
  assert.equal(profiles[PARTITIONS.user].checkHandler(web.contents, "media", "", { mediaType: "video", requestingUrl: "https://meet.example.com/" }), true);
  // Clipboard copy buttons work without asking.
  assert.equal(await new Promise((resolve) => handler(web.contents, "clipboard-sanitized-write", resolve, {})), true);
  const rem = openRem();
  assert.equal(await new Promise((resolve) => profiles[PARTITIONS.rem].requestHandler(rem.contents, "notifications", resolve, {})), false);
  assert.equal(sent("gofer:browser-prompt").length, 1);
  // Leaving the page cancels its pending request.
  const pending = new Promise((resolve) => handler(web.contents, "geolocation", resolve, { requestingUrl: "https://meet.example.com/" }));
  await new Promise((resolve) => setImmediate(resolve));
  web.contents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.equal(await pending, false);
});

test("history is recorded for user tabs only, and typed visits count", () => {
  const { open, openRem, browsers, event, store } = harness();
  const web = open({ url: "https://example.com/" });
  browsers.browserAction(event, { id: web.id, action: "navigate", url: "docs.example.com", typed: true });
  const rem = openRem();
  void rem.contents.loadURL("https://rem-only.test/");
  const history = store.searchHistory("");
  assert.deepEqual(history.map((item) => item.url), ["https://docs.example.com/", "https://example.com/"]);
  assert.equal(history[0].typedCount, 1);
});

test("page zoom is per site and independent of the studio", () => {
  const { open, browsers, event, sent } = harness();
  const web = open({ url: "https://example.com/" });
  browsers.browserAction(event, { id: web.id, action: "zoom", direction: 1 });
  browsers.browserAction(event, { id: web.id, action: "zoom", direction: 1 });
  assert.equal(web.contents.zoom, 1.25);
  assert.equal(sent("gofer:browser-state").at(-1).zoom, 1.25);
  void web.contents.loadURL("https://other.test/");
  assert.equal(web.contents.zoom, 1);
  const again = open({ url: "https://example.com/page" });
  assert.equal(again.contents.zoom, 1.25);
  browsers.browserAction(event, { id: again.id, action: "zoom", reset: true });
  assert.equal(again.contents.zoom, 1);
  assert.equal(sent("gofer:browser-command").some((command) => command.action === "text-zoom"), false);
});

function fakeDownload(filename) {
  const item = Object.assign(new EventEmitter(), {
    savePath: "",
    getFilename: () => filename,
    getURL: () => `https://files.test/${filename}`,
    getMimeType: () => "application/octet-stream",
    getTotalBytes: () => 3,
    getReceivedBytes: () => 3,
    isPaused: () => false,
    canResume: () => false,
    setSavePath(value) { this.savePath = value; },
    getSavePath() { return this.savePath; },
    setSaveDialogOptions(value) { this.dialog = value; },
    cancel() { this.cancelled = true; },
  });
  return item;
}

test("downloads: Rem is blocked by default and dangerous files wait for review", async () => {
  const { open, openRem, profiles, browsers, downloads, sent } = harness();
  const rem = openRem();
  let prevented = false;
  profiles[PARTITIONS.rem].emit("will-download", { preventDefault: () => { prevented = true; } }, fakeDownload("tool.zip"), rem.contents);
  assert.equal(prevented, true);
  assert.equal(rem.session.lastBlockedDownload, "tool.zip");

  const web = open({ url: "https://example.com/" });
  const safe = fakeDownload("report.pdf");
  fs.writeFileSync(path.join(downloads, "report.pdf"), "existing");
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, safe, web.contents);
  assert.equal(safe.savePath, path.join(downloads, "report (1).pdf"));
  safe.emit("done", {}, "completed");
  assert.equal(sent("gofer:browser-downloads").at(-1).downloads[0].state, "completed");

  const risky = fakeDownload("setup.exe");
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, risky, web.contents);
  assert.match(risky.savePath, /quarantine/);
  fs.mkdirSync(path.dirname(risky.savePath), { recursive: true });
  fs.writeFileSync(risky.savePath, "MZ");
  risky.emit("done", {}, "completed");
  const listed = (await browsers.browserLibrary(null, { action: "downloads" })).downloads;
  const entry = listed.find((item) => item.filename === "setup.exe");
  assert.equal(entry.state, "quarantined");
  await assert.rejects(browsers.browserLibrary(null, { action: "download", command: "open", id: entry.id }), /Only finished/);
  await browsers.browserLibrary(null, { action: "download", command: "keep", id: entry.id });
  assert.equal(fs.readFileSync(path.join(downloads, "setup.exe"), "utf8"), "MZ");
});

test("downloads: finished files list system apps and open only with one of them", async () => {
  const launched = [];
  const openWith = {
    listApps: async (filePath) => [{ id: "kate.desktop", name: "Kate", isDefault: true, path: filePath }],
    openWith: async (filePath, appId) => { launched.push([filePath, appId]); },
  };
  const { open, profiles, browsers, downloads } = harness({ openWith });
  const web = open({ url: "https://example.com/" });
  const item = fakeDownload("notes.txt");
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, item, web.contents);
  const pending = (await browsers.browserLibrary(null, { action: "downloads" })).downloads[0];
  await assert.rejects(browsers.browserLibrary(null, { action: "download-apps", id: pending.id }), /Only finished/);
  fs.writeFileSync(item.savePath, "hello");
  item.emit("done", {}, "completed");
  const { apps } = await browsers.browserLibrary(null, { action: "download-apps", id: pending.id });
  assert.deepEqual(apps.map((app) => app.path), [path.join(downloads, "notes.txt")]);
  await browsers.browserLibrary(null, { action: "download", command: "open-with", id: pending.id, appId: "kate.desktop" });
  assert.deepEqual(launched, [[path.join(downloads, "notes.txt"), "kate.desktop"]]);
  fs.rmSync(item.savePath);
  await assert.rejects(browsers.browserLibrary(null, { action: "download", command: "open-with", id: pending.id, appId: "kate.desktop" }), /moved or deleted/);
  assert.equal(launched.length, 1);
});

test("Ask Rem about this sends the right-clicked element, but not one from another frame", () => {
  const { open, menus, sent } = harness();
  const web = open({ url: "https://example.com/" });
  const params = { x: 1, y: 1, selectionText: "", linkURL: "", mediaType: "none" };
  web.contents.emit("ipc-message", { senderFrame: web.contents.mainFrame }, "gofer:browser-context-element", { tag: "button", path: "main > button#buy", text: "Buy now", html: `<button id="buy">${"x".repeat(9000)}</button>`, extra: "ignored" });
  web.contents.emit("context-menu", {}, { ...params, frame: web.contents.mainFrame });
  menus.at(-1).find((item) => item.label === "Ask Rem about this").click();
  const { element } = sent("gofer:browser-command").at(-1);
  assert.equal(element.tag, "button");
  assert.equal(element.text, "Buy now");
  assert.equal(element.html.length, 8000);
  assert.equal(element.extra, undefined);
  web.contents.emit("context-menu", {}, { ...params, frame: { name: "ad-frame" } });
  menus.at(-1).find((item) => item.label === "Ask Rem about this").click();
  assert.equal(sent("gofer:browser-command").at(-1).element, null);
});

test("Ask Rem about this works on local files the same way as on web pages", async () => {
  const { open, menus, sent, directory, browsers, event } = harness();
  const file = path.join(directory, "report.html");
  fs.writeFileSync(file, "<h1>Report</h1>");
  const params = { x: 1, y: 1, selectionText: "Quarterly total", linkURL: "", mediaType: "none" };
  const asked = (tab) => {
    tab.contents.emit("ipc-message", { senderFrame: tab.contents.mainFrame }, "gofer:browser-context-element", { tag: "td", text: "42" });
    tab.contents.emit("context-menu", {}, { ...params, frame: tab.contents.mainFrame });
    menus.at(-1).find((item) => item.label === "Ask Rem about this").click();
    return sent("gofer:browser-command").at(-1);
  };
  for (const options of [{ path: file }, { url: file }, { url: "https://example.com/" }]) {
    const tab = open(options);
    const command = asked(tab);
    assert.equal(command.action, "ask-rem");
    assert.equal(command.id, tab.id);
    assert.equal(command.selection, "Quarterly total");
    assert.equal(command.element.tag, "td");
  }
  // The page context reads only the open document, through the isolated world.
  const preview = open({ path: file });
  const scripts = [];
  preview.contents.title = "Report";
  preview.contents.executeJavaScriptInIsolatedWorld = async (world, [script]) => { scripts.push([world, script.code]); return { value: { selection: "", text: "Report", hidden: false } }; };
  const context = await browsers.browserAction(event, { id: preview.id, action: "page-context" });
  assert.deepEqual(context, { url: pathToFileURL(file).toString(), title: "Report", selection: "", text: "Report" });
  assert.equal(scripts.length, 1);
  // It reads with Rem's page reader, which leaves out text the page hides, and says so.
  assert.match(scripts[0][1], /__raticodeAgent\("readable"/);
  preview.contents.executeJavaScriptInIsolatedWorld = async () => ({ value: { selection: "", text: "Shown", hidden: true } });
  assert.equal((await browsers.browserAction(event, { id: preview.id, action: "page-context" })).text, "[hidden text skipped]\nShown");
  assert.notEqual(scripts[0][0], 0, "the page's own scripts cannot see or change the request");
  // A preview that has left its file offers nothing to ask about.
  const blank = open({ path: file });
  blank.contents.url = "about:blank";
  blank.contents.emit("context-menu", {}, { ...params, frame: blank.contents.mainFrame });
  assert.equal(menus.at(-1).some((item) => item.label === "Ask Rem about this"), false);
});

test("Save image as asks where to save, starting in Downloads", () => {
  const { open, menus, profiles, downloads, browsers } = harness();
  const web = open({ url: "https://example.com/" });
  web.contents.emit("context-menu", {}, { x: 1, y: 1, mediaType: "image", srcURL: "https://files.test/cat.png", frame: web.contents.mainFrame });
  menus.at(-1).find((item) => item.label === "Save image as…").click();
  assert.deepEqual(web.contents.downloads, ["https://files.test/cat.png"]);
  const image = fakeDownload("cat.png");
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, image, web.contents);
  assert.deepEqual(image.dialog, { defaultPath: path.join(downloads, "cat.png"), title: "Save image" });
  assert.equal(image.savePath, "");
  // Cancelling the dialog leaves nothing in the list.
  image.emit("done", {}, "cancelled");
  return browsers.browserLibrary(null, { action: "downloads" }).then(({ downloads: listed }) => {
    assert.equal(listed.length, 0);
    // Ordinary downloads still save straight to Downloads.
    const other = fakeDownload("cat.png");
    profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, other, web.contents);
    assert.equal(other.dialog, undefined);
    assert.equal(other.savePath, path.join(downloads, "cat.png"));
  });
});

test("Save link as saves web, blob: and data: links and asks where; a page's own file offers nothing else", () => {
  const { open, openRem, menus, profiles, downloads } = harness();
  const web = open({ url: "https://chat.example.com/" });
  const menuFor = (linkURL) => {
    web.contents.emit("context-menu", {}, { x: 1, y: 1, mediaType: "none", linkURL, frame: web.contents.mainFrame });
    return menus.at(-1);
  };
  const blob = "blob:https://chat.example.com/5c7baa75-7dab-40c9-8fdd-63ea16927d62";
  const blobMenu = menuFor(blob);
  // Only this page holds the file: no new tab, default browser or address to copy.
  for (const label of ["Open link in new tab", "Open link in default browser", "Copy link address"]) {
    assert.equal(blobMenu.some((item) => item.label === label), false, label);
  }
  blobMenu.find((item) => item.label === "Save link as…").click();
  assert.deepEqual(web.contents.downloads, [blob]);
  const file = Object.assign(fakeDownload("chat.html"), { getURL: () => blob });
  profiles[PARTITIONS.user].emit("will-download", { preventDefault() {} }, file, web.contents);
  assert.deepEqual(file.dialog, { defaultPath: path.join(downloads, "chat.html"), title: "Save link" });

  const data = "data:text/html,%3Cp%3Ehi%3C%2Fp%3E";
  menuFor(data).find((item) => item.label === "Save link as…").click();
  assert.equal(web.contents.downloads.at(-1), data);
  const webMenu = menuFor("https://files.test/report.pdf");
  assert.deepEqual(webMenu.filter((item) => item.label).map((item) => item.label).slice(0, 4),
    ["Open link in new tab", "Open link in default browser", "Save link as…", "Copy link address"]);
  // Other schemes get no link entries.
  assert.equal(menuFor("javascript:alert(1)").some((item) => item.label === "Save link as…"), false);

  // Rem's tabs still may not download, even a file the user saved from its page.
  const rem = openRem();
  let prevented = false;
  const remFile = Object.assign(fakeDownload("chat.html"), { getURL: () => blob });
  profiles[PARTITIONS.rem].emit("will-download", { preventDefault: () => { prevented = true; } }, remFile, rem.contents);
  assert.equal(prevented, true);
  assert.equal(remFile.dialog, undefined);
});

test("certificate errors can be bypassed only by the user, per certificate", () => {
  const { open, openRem, browsers, event } = harness();
  const web = open({ url: "https://self-signed.test/" });
  const certificate = { fingerprint: "sha256/abc", subjectName: "self-signed.test", issuerName: "Me" };
  const outcome = [];
  const fire = (contents) => browsers.handleCertificateError({ preventDefault() {} }, contents, "https://self-signed.test/", "net::ERR_CERT_AUTHORITY_INVALID", certificate, (value) => outcome.push(value), true);
  fire(web.contents);
  assert.deepEqual(outcome, [false]);
  const state = browsers.browserAction(event, { id: web.id, action: "site-info" });
  assert.equal(state.security.level, "error");
  browsers.browserAction(event, { id: web.id, action: "allow-certificate" });
  fire(web.contents);
  assert.deepEqual(outcome, [false, true]);
  const rem = openRem();
  fire(rem.contents);
  assert.deepEqual(outcome, [false, true, false]);
  assert.throws(() => browsers.browserAction(event, { id: rem.id, action: "allow-certificate" }), /no certificate/);
});

test("a tab that reports sound shows it in the tab's state, for its speaker and so it isn't unloaded", async () => {
  const { open, sent } = harness();
  const web = open({ url: "https://music.test/" });
  const audible = () => sent("gofer:browser-state").filter((state) => state.id === web.id).at(-1)?.audible;
  // Firefox, Chrome and Edge tabs report it the way Electron's own tabs do.
  web.contents.emit("audio-state-changed", { audible: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(audible(), true);
  web.contents.emit("audio-state-changed", { audible: false });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(audible(), false);
});

test("the media bar follows the owner's tabs from first sound to closing, and each window controls only its own", async () => {
  const { open, browsers, event, sent } = harness();
  const web = open({ url: "https://music.test/", clientId: "browser-tab-1" });
  const contents = web.contents;
  const runs = [];
  contents.executeJavaScriptInIsolatedWorld = async (_world, sources, gesture) => {
    runs.push(gesture);
    return sources[0].code.includes("raticode-media-run") ? true : { hasElement: true, playing: true, title: "Song", artist: "Band", artwork: "https://art.test/a.jpg", playbackState: "playing", actions: { previous: false, next: true } };
  };
  const latest = () => sent("gofer:browser-media").at(-1)?.sessions;
  contents.emit("media-started-playing");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(latest().every((media) => Number.isFinite(media.startedAt)));
  assert.deepEqual(latest().map((media) => ({ ...media, startedAt: 0 })), [{
    title: "Song", artist: "Band", site: "music.test", artwork: "https://art.test/a.jpg", playing: true, canPlayPause: true, canPrevious: false, canNext: true, startedAt: 0, id: web.id, clientId: "browser-tab-1",
  }], "the bar learns which tab plays, so it can skip the one on screen");
  assert.deepEqual((await browsers.browserLibrary(event, { action: "media-sessions" })).sessions.map((item) => item.id), [web.id]);
  const before = sent("gofer:browser-media").length;
  contents.emit("media-paused");
  contents.emit("did-stop-loading");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(sent("gofer:browser-media").length <= before + 1, "an unchanged state isn't sent again");

  assert.deepEqual(await browsers.browserAction(event, { id: web.id, action: "media-control", command: "next" }), { handled: true });
  assert.equal(runs.at(-1), true, "a control is the user's gesture");
  const otherWindow = { sender: { id: 99 } };
  assert.throws(() => browsers.browserAction(otherWindow, { id: web.id, action: "media-control", command: "next" }), /not found/);
  assert.deepEqual((await browsers.browserLibrary(otherWindow, { action: "media-sessions" })).sessions, []);
  await assert.rejects(browsers.browserAction(event, { id: web.id, action: "media-control", command: "reload" }), /Unknown media command/);

  browsers.browserAction(event, { id: web.id, action: "close" });
  assert.deepEqual(latest(), [], "closing the tab clears the bar");
  assert.deepEqual(browsers.browserAction(event, { id: web.id, action: "media-control", command: "next" }), { closed: true, ready: false });
});

test("HTTP sign-in is asked in the tab and never offered to Rem", async () => {
  const { open, openRem, browsers, event, sent } = harness();
  const rem = openRem();
  const remCalls = [];
  browsers.handleLogin({ preventDefault() {} }, rem.contents, { url: "https://intranet.test/" }, { host: "intranet.test" }, (...args) => remCalls.push(args));
  assert.deepEqual(remCalls, [[]]);
  const web = open({ url: "https://intranet.test/" });
  const credentials = new Promise((resolve) => browsers.handleLogin({ preventDefault() {} }, web.contents, { url: "https://intranet.test/" }, { host: "intranet.test", realm: "Staff" }, (...args) => resolve(args)));
  const request = sent("gofer:browser-prompt").at(-1);
  assert.equal(request.kind, "auth");
  assert.equal(request.realm, "Staff");
  browsers.browserAction(event, { id: web.id, action: "prompt-response", requestId: request.requestId, response: { username: "ada", password: "s3cret" } });
  assert.deepEqual(await credentials, ["ada", "s3cret"]);
  assert.equal(JSON.stringify(sent("gofer:browser-state")).includes("s3cret"), false);
});

test("sharing a tab binds it to the thread's Rem owner until stopped", () => {
  const { open, openRem, browsers, event } = harness();
  const web = open({ url: "https://example.com/" });
  browsers.browserAction(event, { id: web.id, action: "share-with-rem", threadId: "thread-1" });
  const owner = crypto.createHash("sha256").update("thread-1").digest("hex").slice(0, 32);
  assert.equal(remOwnerForThread("thread-1"), owner);
  assert.deepEqual(browsers.agentTabs(owner).map((item) => item.id), [web.id]);
  assert.throws(() => browsers.browserAction(event, { id: web.id, action: "share-with-rem", threadId: "thread-2" }), /already shared/);
  browsers.browserAction(event, { id: web.id, action: "pause-rem", paused: true });
  assert.equal(browsers.agentTabs(owner)[0].agentPaused, true);
  browsers.browserAction(event, { id: web.id, action: "stop-sharing" });
  assert.deepEqual(browsers.agentTabs(owner), []);
  // Taking over a Rem tab removes Rem's access but keeps the page.
  const rem = openRem();
  browsers.browserAction(event, { id: rem.id, action: "take-over" });
  assert.deepEqual(browsers.agentTabs("agent-0123456789"), []);
  assert.equal(rem.contents.throttling, true);
});

test("sign-ins are copied to Rem's profile only for shared sites", async () => {
  const { browsers, profiles } = harness();
  profiles[PARTITIONS.user].cookieList = [
    { name: "session", value: "gh", domain: ".github.com", path: "/", secure: true, httpOnly: true, hostOnly: false, session: true },
    { name: "session", value: "bank", domain: "bank.test", path: "/", secure: true, httpOnly: true, hostOnly: true, session: true },
  ];
  browsers.setPolicy(null, { remSignInSites: ["github.com"] });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(profiles[PARTITIONS.rem].cookieList.map((cookie) => cookie.value), ["gh"]);
  browsers.setPolicy(null, { remSignInSites: [] });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(profiles[PARTITIONS.rem].cookieList, []);
});

test("revoking sign-in sharing says so when Rem's copy can't be removed", async () => {
  const logs = [];
  const { browsers, profiles } = harness({ logs });
  profiles[PARTITIONS.user].cookieList = [
    { name: "session", value: "gh-secret", domain: ".github.com", path: "/", secure: true, httpOnly: true, hostOnly: false, session: true },
  ];
  browsers.setPolicy(null, { remSignInSites: ["github.com"] });
  await new Promise((resolve) => setTimeout(resolve, 10));
  profiles[PARTITIONS.rem].cookies.remove = async () => { throw new Error("profile locked"); };
  browsers.setPolicy(null, { remSignInSites: [] });
  await new Promise((resolve) => setTimeout(resolve, 10));
  const warnings = logs.filter(([level]) => level === "warn").map(([, message]) => message);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /Could not remove 1 of Rem's shared sign-in cookies for github\.com; Rem may stay signed in there/);
  assert.ok(!warnings[0].includes("gh-secret") && !warnings[0].includes("session"), "no cookie names or values");
});

test("Rem tabs say when they use a shared sign-in", () => {
  const { browsers, openRem, sent } = harness({ policy: { remSignInSites: ["github.com"] } });
  const rem = openRem();
  void rem.contents.loadURL("https://github.com/settings");
  assert.equal(sent("gofer:browser-state").at(-1).remSignIn, true);
  void rem.contents.loadURL("https://example.com/");
  assert.equal(sent("gofer:browser-state").at(-1).remSignIn, false);
  // github.com cookies reach gist.github.com too, so it says so there.
  void rem.contents.loadURL("https://gist.github.com/");
  assert.equal(sent("gofer:browser-state").at(-1).remSignIn, true);
  void browsers;
});

test("restored tabs replay their saved history", async () => {
  const { open } = harness();
  const entries = [{ url: "https://a.test/", title: "A" }, { url: "https://b.test/", title: "B", pageState: "state" }, { url: "javascript:alert(1)" }];
  const tab = open({ url: "https://b.test/", history: { entries, index: 1 } });
  assert.deepEqual(tab.contents.loaded, [], "the view restores history instead of loading the page");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(tab.contents.restored, { entries: entries.slice(0, 2), index: 1 });
});

test("a tab's view follows its slot into the Browser's own window and back, still the same page", async () => {
  const { browsers, event, open, openRem, owner, mainWindow, popout, setPopoutOpen } = harness();
  const tab = open({ url: "https://a.test/" });
  const rem = openRem();
  // Made hidden in Raticode's window, at a desktop size.
  assert.deepEqual(mainWindow.children, [tab.view, rem.view]);
  assert.equal(tab.view.visible, false);
  assert.deepEqual(tab.view.bounds, { x: 0, y: 0, width: 1280, height: 800 });
  // Only the page that owns the tab can place it.
  assert.throws(() => browsers.browserAction({ sender: { id: 99 } }, { id: tab.id, action: "place", x: 0, y: 0, width: 10, height: 10, visible: true }), /not found/);
  // Shown where the studio says, in the studio's zoom.
  owner.getZoomFactor = () => 1.25;
  assert.deepEqual(browsers.browserAction(event, { id: tab.id, action: "place", x: 8, y: 40.4, width: 800, height: 600, visible: true, host: "main" }), { placed: true, shown: true });
  assert.deepEqual(tab.view.bounds, { x: 10, y: 51, width: 1000, height: 750 });
  assert.equal(tab.view.visible, true);
  // Into the Browser's own window: the same contents, nothing reloaded.
  setPopoutOpen(true);
  const loads = tab.contents.loaded.length;
  browsers.browserAction(event, { id: tab.id, action: "place", x: 0, y: 36, width: 900, height: 640, visible: true, host: "popout" });
  assert.deepEqual(popout.children, [tab.view]);
  assert.equal(mainWindow.children.includes(tab.view), false);
  assert.equal(tab.contents.loaded.length, loads);
  assert.equal(tab.contents.isDestroyed(), false);
  // There the slot is measured in that window's zoom, not the studio's.
  assert.deepEqual(tab.view.bounds, { x: 0, y: 36, width: 900, height: 640 });
  // Hidden keeps its size, so the page doesn't lay out again.
  browsers.browserAction(event, { id: tab.id, action: "place", x: 0, y: 0, width: 0, height: 0, visible: false, host: "popout" });
  assert.equal(tab.view.visible, false);
  assert.deepEqual(tab.view.bounds, { x: 0, y: 36, width: 900, height: 640 });
  // A picture of the page for when Raticode draws over it.
  assert.match((await browsers.browserAction(event, { id: tab.id, action: "view-snapshot" })).snapshot, /^data:image\/jpeg;base64,/);
  // The window closing hands its views back to Raticode's, hidden.
  browsers.returnViews(popout);
  assert.deepEqual(popout.children, []);
  assert.ok(mainWindow.children.includes(tab.view));
  // A window that has gone falls back to Raticode's.
  setPopoutOpen(false);
  browsers.browserAction(event, { id: rem.id, action: "place", x: 0, y: 0, width: 400, height: 300, visible: true, host: "popout" });
  assert.ok(mainWindow.children.includes(rem.view));
  // Closing the tab closes its page and takes its view away.
  browsers.browserAction(event, { id: tab.id, action: "close" });
  assert.equal(tab.contents.isDestroyed(), true);
  assert.equal(mainWindow.children.includes(tab.view), false);
  // A page that ends by itself ends its tab.
  const other = open({ url: "https://b.test/" });
  other.contents.close();
  assert.equal(browsers.browserAction(event, { id: other.id, action: "adopt", webContentsId: -1 }).closed, true);
});

test("a popped-out tab is placed and zoomed by the Browser window's zoom, not Raticode's", () => {
  const { browsers, event, open, owner, mainWindow, popout, setPopoutOpen } = harness();
  const tab = open({ url: "https://a.test/" });
  const slot = { x: 4, y: 36.4, width: 800, height: 600, visible: true };
  let ownerZoom = 1;
  owner.getZoomFactor = () => ownerZoom;
  setPopoutOpen(true);
  for (const [main, window] of [[1.25, 1.25], [0.8, 0.8], [1.25, 1], [0.8, 1], [1, 1.5]]) {
    ownerZoom = main;
    popout.webContents.zoom = window;
    browsers.browserAction(event, { id: tab.id, action: "place", ...slot, host: "popout" });
    assert.deepEqual(popout.children, [tab.view]);
    const expected = { x: Math.round(slot.x * window), y: Math.round(slot.y * window), width: Math.round(slot.width * window), height: Math.round(slot.height * window) };
    assert.deepEqual(tab.view.bounds, expected, `app at ${main}, Browser window at ${window}`);
    // The page is zoomed like the window it's in.
    assert.equal(tab.contents.zoom, browserContentZoomFactor(window, 1), `page zoom with the Browser window at ${window}`);
    // Raticode's zoom changing leaves a popped-out tab at its window's zoom.
    ownerZoom = main === 1.5 ? 1 : 1.5;
    browsers.ownerZoom(event);
    assert.equal(tab.contents.zoom, browserContentZoomFactor(window, 1));
  }
  // Back in Raticode's window, its zoom again.
  ownerZoom = 1.25;
  browsers.browserAction(event, { id: tab.id, action: "place", ...slot, host: "main" });
  assert.ok(mainWindow.children.includes(tab.view));
  assert.deepEqual(tab.view.bounds, { x: 5, y: 46, width: 1000, height: 750 });
  assert.equal(tab.contents.zoom, browserContentZoomFactor(1.25, 1));
  ownerZoom = 0.8;
  browsers.ownerZoom(event);
  assert.equal(tab.contents.zoom, browserContentZoomFactor(0.8, 1));
  // A Browser window that has gone falls back to Raticode's zoom.
  setPopoutOpen(false);
  popout.webContents.zoom = 1.5;
  browsers.browserAction(event, { id: tab.id, action: "place", ...slot, host: "popout" });
  assert.deepEqual(tab.view.bounds, { x: 3, y: 29, width: 640, height: 480 });
});

test("open in default browser accepts web pages and local HTML only", async () => {
  const { open, browsers, event, opened, directory } = harness();
  const file = path.join(directory, "My Report.html");
  fs.writeFileSync(file, "<h1>Report</h1>");
  const preview = open({ path: file });
  browsers.browserAction(event, { id: preview.id, action: "open-external" });
  const web = open({ url: "https://example.com/" });
  browsers.browserAction(event, { id: web.id, action: "open-external" });
  const script = path.join(directory, "run.sh");
  fs.writeFileSync(script, "echo");
  const unsafe = open({ path: script });
  browsers.browserAction(event, { id: unsafe.id, action: "open-external" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(opened, [`file://${file.replace(/ /g, "%20")}`, "https://example.com/"]);
});

test("HTML previews hand file links to the studio and websites to tabs", () => {
  const { open, sent, directory } = harness();
  const file = path.join(directory, "report.html");
  fs.writeFileSync(file, "<a href='guide.md'>Guide</a>");
  const preview = open({ path: file });
  const ipc = (url) => preview.contents.emit("ipc-message", { senderFrame: preview.contents.mainFrame }, "gofer:browser-link-clicked", { url, disposition: "foreground" });
  for (const href of ["file:///repo/guide.md", "file:///outside/app.py:12:3"]) {
    ipc(href);
    assert.deepEqual(sent("gofer:browser-open-file").at(-1), { href, fromPage: true });
  }
  preview.contents.input();
  ipc("https://example.com/docs");
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/docs", background: false, engine: "raticode" });
  // A page script opening a site right after the click is not a link the user clicked.
  assert.deepEqual(preview.contents.openHandler({ url: "http://example.com/", disposition: "foreground-tab" }), { action: "deny" });
  assert.equal(sent("gofer:browser-open-tab").length, 1);
  for (const url of ["javascript:alert(1)", "data:text/html,hello"]) {
    assert.deepEqual(preview.contents.openHandler({ url }), { action: "deny" });
  }
  assert.equal(sent("gofer:browser-open-tab").length, 1);
  // Once a preview shows a website, that page cannot reach local files.
  preview.contents.url = "https://example.com/";
  ipc("file:///outside/private.md");
  assert.equal(sent("gofer:browser-open-file").length, 2);
});

test("navigation failures are reported and cannot escape after the tab closes", async () => {
  const { open, browsers, event, sent, owner } = harness();
  const web = open({ url: "https://example.com/" });
  web.contents.loadURL = async () => { throw new Error("ERR_NAME_NOT_RESOLVED"); };
  browsers.browserAction(event, { id: web.id, action: "navigate", url: "https://missing.test/" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sent("gofer:browser-state").at(-1).error, "ERR_NAME_NOT_RESOLVED");
  owner.send = () => { throw new Error("Object has been destroyed"); };
  browsers.browserAction(event, { id: web.id, action: "navigate", url: "https://missing.test/" });
  await new Promise((resolve) => setImmediate(resolve));
});

test("profiles match by identity or persistent storage path", () => {
  const profile = (storage) => ({ getStoragePath: () => storage });
  assert.equal(sameProfile(profile("/p/user"), profile("/p/user")), true);
  assert.equal(sameProfile(profile("/p/user"), profile("/p/rem")), false);
  // In-memory profiles have no path, so only the same object matches.
  assert.equal(sameProfile(profile(null), profile(null)), false);
  const memory = profile(null);
  assert.equal(sameProfile(memory, memory), true);
  assert.equal(sameProfile(null, memory), false);
});

test("tab actions publish fresh state, so mute toggles both ways", () => {
  const { open, browsers, event, sent } = harness();
  const web = open({ url: "https://example.com/" });
  browsers.browserAction(event, { id: web.id, action: "mute", muted: true });
  assert.equal(sent("gofer:browser-state").at(-1).muted, true);
  browsers.browserAction(event, { id: web.id, action: "mute", muted: false });
  assert.equal(sent("gofer:browser-state").at(-1).muted, false);
  assert.equal(web.contents.muted, false);
});

test("printing without printers offers a PDF instead of opening a failing dialog", async () => {
  const { open, browsers, event, messages } = harness({ dialog: { messageResponse: 1 } });
  const web = open({ url: "https://example.com/" });
  let printed = 0;
  web.contents.getPrintersAsync = async () => [];
  web.contents.print = () => { printed += 1; };
  browsers.browserAction(event, { id: web.id, action: "print" });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(printed, 0);
  assert.equal(messages.at(-1).message, "No printers are set up");
  assert.deepEqual(messages.at(-1).buttons, ["Save as PDF…", "Cancel"]);
  web.contents.getPrintersAsync = async () => [{ name: "Office" }];
  browsers.browserAction(event, { id: web.id, action: "print" });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(printed, 1);
});

test("Save as PDF writes the file and reports failures to the user", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "browser-pdf-"));
  const target = path.join(directory, "page.pdf");
  const { open, browsers, event, messages, opened } = harness({ dialog: { savePath: target } });
  const web = open({ url: "https://example.com/" });
  web.contents.printToPDF = async () => Buffer.from("%PDF-1.7");
  browsers.browserAction(event, { id: web.id, action: "save-pdf" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(fs.readFileSync(target, "utf8"), "%PDF-1.7");
  web.contents.printToPDF = async () => { throw new Error("Printing failed"); };
  browsers.browserAction(event, { id: web.id, action: "save-pdf" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(messages.at(-1).message, "Could not save this page as a PDF");
  assert.equal(messages.at(-1).detail, "Printing failed");
  void opened;
});

test("reopened tabs load their page when history cannot be restored", async () => {
  const { open, sent } = harness();
  const entries = [{ url: "https://a.test/", title: "A" }, { url: "https://b.test/", title: "B" }];
  // A real view has already shown about:blank, which Chromium may refuse to replace.
  const rejected = open({ url: "https://b.test/", history: { entries, index: 1 } }, (contents) => {
    contents.navigationHistory.restore = async () => { throw new Error("Cannot restore"); };
  });
  const ignored = open({ url: "https://b.test/", history: { entries, index: 1 } }, (contents) => {
    contents.navigationHistory.restore = async () => {};
  });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(rejected.contents.loaded, ["https://b.test/"]);
  assert.deepEqual(ignored.contents.loaded, ["https://b.test/"]);
  assert.equal(sent("gofer:browser-state").some((state) => state.id === rejected.id && state.error), false);
});

test("prompt responses are reduced to the expected shape", () => {
  assert.equal(sanitizePromptResponse("permission", "allow"), "allow");
  assert.equal(sanitizePromptResponse("permission", "root"), null);
  assert.equal(sanitizePromptResponse("rem-confirm", "approve"), "approve");
  assert.deepEqual(sanitizePromptResponse("client-certificate", { index: 2 }), { index: 2 });
  assert.equal(sanitizePromptResponse("auth", { username: 5 }), null);
});

// A stand-in for firefox-engine.cjs that records the WebDriver BiDi commands
// Firefox tabs send.
function fakeFirefox({ available = true } = {}) {
  const calls = [];
  const contexts = new Map();
  let next = 0;
  return {
    calls,
    contexts,
    sent: (method) => calls.filter(([name]) => name === method).map(([, params]) => params),
    status: () => ({ available, executable: available ? "/usr/bin/firefox" : "", running: true, snap: false }),
    createContext: async (kind) => { next += 1; return { context: `context-${next}-${kind}`, generation: 1 }; },
    register: (context, target) => contexts.set(context, target),
    unregister: (context) => contexts.delete(context),
    send: async (method, params) => {
      calls.push([method, params]);
      // Pages "load" a moment after Firefox is asked to open them.
      if (method === "browsingContext.navigate") setTimeout(() => contexts.get(params.context)?.handleEvent("browsingContext.load", { context: params.context, url: params.url }), 1);
      if (method === "script.evaluate") {
        return { type: "success", result: { type: "array", value: [{ type: "number", value: 1 }, { type: "string", value: "navigate" }, { type: "string", value: "Page" }, { type: "string", value: "" }] } };
      }
      return {};
    },
    mirrorSignIns: async (sites, removed = []) => { calls.push(["mirror", { sites, removed }]); },
    setRemDownloads: async (allowed) => { calls.push(["rem-downloads", allowed]); },
    clearSiteCookies: async (kind, host) => { calls.push(["clear-cookies", { kind, host }]); },
    ensure: async () => {},
    openSignInWindow: async (url) => { calls.push(["sign-in-window", url]); return { opened: true }; },
    signingIn: false,
    stopAndWait: async () => { calls.push(["stop"]); },
    profileDir: () => path.join(os.tmpdir(), "raticode-fake-firefox-profile"),
    readUserPrefs: () => ({}),
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

// A stand-in for chromium-engine.cjs that records the DevTools commands
// Chrome tabs send.
function fakeChrome({ available = true, browser = { id: "chrome", name: "Chrome", fullName: "Google Chrome" } } = {}) {
  const calls = [];
  const targets = new Map();
  let next = 0;
  return {
    browser,
    calls,
    targets,
    sent: (method) => calls.filter(([name]) => name === method).map(([, params]) => params),
    status: () => ({ id: "chrome", name: "Google Chrome", available, executable: available ? "/opt/google/chrome/chrome" : "", running: true, version: "156.0.1", profile: "/data/chrome-profile" }),
    createContext: async (kind) => { next += 1; return { targetId: `target-${next}-${kind}`, sessionId: `session-${next}`, generation: 1, userAgent: "Mozilla/5.0 HeadlessChrome/156.0.0.0 Safari/537.36" }; },
    register: (sessionId, target) => targets.set(sessionId, target),
    unregister: (sessionId) => targets.delete(sessionId),
    send: async (method, params = {}, options = {}) => {
      calls.push([method, params, options.sessionId || ""]);
      const target = targets.get(options.sessionId);
      if (method === "Page.getFrameTree") return { frameTree: { frame: { id: target?.targetId || "frame", loaderId: "L0" } } };
      if (method === "Page.createIsolatedWorld") return { executionContextId: 7 };
      if (method === "Runtime.evaluate") return { result: { value: ["Page", ""] } };
      if (method === "Page.getNavigationHistory") return { currentIndex: 1, entries: [{ id: 1, url: "about:blank" }, { id: 2, url: target?.url || "about:blank" }] };
      // Pages "load" a moment after Chrome is asked to open them.
      if (method === "Page.navigate") {
        setTimeout(() => {
          target?.handleEvent("Page.frameNavigated", { frame: { id: target.mainFrameId, loaderId: "L1", url: params.url } });
          target?.handleEvent("Page.loadEventFired", {});
        }, 1);
        return { frameId: target?.mainFrameId, loaderId: "L1" };
      }
      return {};
    },
    mirrorSignIns: async (sites, removed = []) => { calls.push(["mirror", { sites, removed }]); },
    setRemDownloads: async (allowed) => { calls.push(["rem-downloads", allowed]); },
    clearSiteCookies: async (kind, host) => { calls.push(["clear-cookies", { kind, host }]); },
    ensure: async () => {},
    openSignInWindow: async (url) => { calls.push(["sign-in-window", url]); return { opened: true }; },
    signingIn: false,
    stop: () => { calls.push(["stop"]); },
    stopAndWait: async () => { calls.push(["stop"]); },
  };
}

test("a right-click in an Edge tab opens Raticode's menu, whose items act on the page in Edge", async () => {
  const edge = fakeChrome({ browser: { id: "edge", name: "Edge", fullName: "Microsoft Edge" } });
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "edge-menu-"));
  const savePath = path.join(scratch, "page.mhtml");
  const { browsers, event, menus, opened } = harness({ policy: { engine: "edge" }, chromium: { edge }, dialog: { savePath } });
  try {
    const state = browsers.createBrowser(event, { url: "https://example.com/", clientId: "tab-1" });
    assert.equal(state.engine, "edge");
    await settle();
    const contents = edge.targets.get("session-1");
    const edits = [];
    contents.editCommand = async (command, text) => { edits.push([command, text]); };
    const saved = [];
    contents.savePage = async (file) => { saved.push(file); fs.writeFileSync(file, "MIME-Version: 1.0"); };
    contents.emit("context-menu", {}, {
      x: 5, y: 5, linkURL: "https://example.com/next", mediaType: "image", srcURL: "https://example.com/cat.png",
      isEditable: true, selectionText: "hi",
    });
    const template = menus.at(-1);
    const labels = template.filter((item) => item.label).map((item) => item.label);
    for (const label of ["Open link in new tab", "Copy link address", "Open image in new tab", "Copy image address", "Undo", "Redo", "Cut", "Copy", "Paste", "Select all", "Ask Rem about this", "Back", "Reload", "Save page as…", "Save as PDF…"]) {
      assert.ok(labels.includes(label), `${label} is missing from ${labels.join(", ")}`);
    }
    // Electron's own editing roles and Raticode-only tools would act on Raticode's window, not Edge.
    for (const label of ["Save image as…", "Copy image", "Inspect", "Print…"]) assert.equal(labels.includes(label), false, label);
    assert.equal(template.some((item) => item.role), false);
    const item = (label) => template.find((entry) => entry.label === label);
    item("Paste").click();
    item("Select all").click();
    item("Copy").click();
    assert.deepEqual(edits, [["paste", "from the clipboard"], ["selectAll", ""], ["copy", ""]]);
    item("Save page as…").click();
    await settle();
    assert.deepEqual(saved, [savePath]);
    item("Open link in new tab").click();
    assert.deepEqual(opened, []);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

test("with Chrome chosen, web tabs stream from Chrome, Firefox stays idle and previews stay on Raticode", async () => {
  const chrome = fakeChrome();
  const firefox = fakeFirefox();
  const { browsers, event, sent, directory } = harness({ policy: { engine: "chrome" }, firefox, chrome });
  const state = browsers.createBrowser(event, { url: "https://example.com/", clientId: "tab-1" });
  assert.equal(state.engine, "chrome");
  assert.equal(state.partition, undefined);
  await settle();
  assert.deepEqual(chrome.sent("Page.navigate").map((params) => params.url), ["https://example.com/"]);
  // Pages see the Chrome it is, not "HeadlessChrome".
  assert.equal(chrome.sent("Emulation.setUserAgentOverride").at(-1).userAgent, "Mozilla/5.0 Chrome/156.0.0.0 Safari/537.36");
  assert.deepEqual(firefox.calls, []);
  assert.equal(browsers.browserAction(event, { id: state.id, action: "adopt", webContentsId: -1 }).engine, "chrome");

  browsers.browserAction(event, { id: state.id, action: "firefox-view", width: 800, height: 600, scale: 2, visible: true });
  await settle();
  assert.deepEqual(chrome.sent("Emulation.setDeviceMetricsOverride").at(-1), { width: 800, height: 600, deviceScaleFactor: 2, mobile: false, screenWidth: 800, screenHeight: 600 });
  assert.deepEqual(chrome.sent("Page.startScreencast").at(-1), { format: "jpeg", quality: 82, maxWidth: 1600, maxHeight: 1200, everyNthFrame: 1 });

  browsers.browserAction(event, { id: state.id, action: "firefox-input", events: [{ t: "down", x: 10, y: 20, button: 0 }] });
  await settle();
  const press = chrome.sent("Input.dispatchMouseEvent").at(-1);
  assert.equal(press.type, "mousePressed");
  assert.equal(press.button, "left");

  const contents = chrome.targets.get("session-1");
  contents.receiveFrame({ data: Buffer.from([0xff, 0xd8]).toString("base64"), sessionId: 5 });
  assert.equal(sent("gofer:browser-frame").at(-1).id, state.id);
  browsers.browserAction(event, { id: state.id, action: "frame-ack", seq: sent("gofer:browser-frame").at(-1).seq });
  await settle();
  assert.deepEqual(chrome.sent("Page.screencastFrameAck").at(-1), { sessionId: 5 });
  contents.emit("stream-cursor", { cursor: "pointer" });
  assert.deepEqual(sent("gofer:browser-firefox").at(-1), { cursor: "pointer", kind: "cursor", id: state.id });

  // Chrome's own pages open only in a Chrome window.
  for (const url of ["chrome://settings", "about:settings", "chrome-extension://abc/options.html"]) {
    assert.throws(() => browsers.browserAction(event, { id: state.id, action: "navigate", url }), /Chrome's own pages[\s\S]*Settings > Browser > Chrome/);
  }

  // Google's refusal offers a Chrome window; the tab action reaches Chrome, not Firefox.
  contents.commitUrl("https://accounts.google.com/v3/signin/rejected?x=1");
  assert.equal(browsers.browserAction(event, { id: state.id, action: "stop" }).signInBlocked, true);
  await browsers.browserAction(event, { id: state.id, action: "firefox-sign-in-window" });
  assert.deepEqual(chrome.sent("sign-in-window"), ["https://accounts.google.com/"]);
  assert.deepEqual(firefox.sent("sign-in-window"), []);

  const status = await browsers.browserLibrary(event, { action: "engine-status" });
  assert.equal(status.chrome.available, true);
  assert.equal(status.firefox.available, true);
  await browsers.browserLibrary(event, { action: "chrome-sign-in-window", url: "chrome://extensions" });
  assert.equal(chrome.sent("sign-in-window").at(-1), "chrome://extensions");

  const file = path.join(directory, "page.html");
  fs.writeFileSync(file, "<p>local</p>");
  const preview = browsers.createBrowser(event, { path: file });
  assert.equal(preview.engine, "raticode");
  assert.equal(preview.partition, PARTITIONS.preview);

  // The sign-in window paused the tab; closing it now needs no Chrome.
  assert.equal(contents.dead, true);
  browsers.browserAction(event, { id: state.id, action: "close" });
  assert.equal(contents.isDestroyed(), true);
  const second = browsers.createBrowser(event, { url: "https://example.org/", clientId: "tab-2" });
  await settle();
  browsers.browserAction(event, { id: second.id, action: "close" });
  assert.deepEqual(chrome.sent("Target.closeTarget").at(-1), { targetId: "target-2-user" });
  assert.deepEqual(firefox.calls, []);
});

test("each tab runs in the browser it asks for, whatever the default, and links open in the same browser", async () => {
  const chrome = fakeChrome();
  const firefox = fakeFirefox();
  const { browsers, event } = harness({ policy: { engine: "chrome" }, firefox, chrome });
  // Raticode (chromium) beside Chrome: a webview, no Chrome target.
  const own = browsers.createBrowser(event, { url: "https://docs.example/", clientId: "tab-1", engine: "raticode" });
  assert.equal(own.engine, "raticode");
  assert.equal(own.partition, PARTITIONS.user);
  // Firefox while Chrome is the default.
  const fox = browsers.createBrowser(event, { url: "https://music.example/", clientId: "tab-2", engine: "firefox" });
  assert.equal(fox.engine, "firefox");
  await settle();
  assert.ok(firefox.calls.length > 0, "Firefox started for its tab");
  // No browser named: the default, as windows from before per-tab browsers expect.
  const plain = browsers.createBrowser(event, { url: "https://example.org/", clientId: "tab-3" });
  assert.equal(plain.engine, "chrome");
  // Gecko tabs are placed by the studio, so the session is Raticode (chromium)'s if one is asked for.
  assert.equal(browsers.createBrowser(event, { url: "https://example.net/", clientId: "tab-4", engine: "gecko" }).engine, "raticode");
  // Private tabs stay in Raticode (chromium)'s private profile.
  const hidden = browsers.createBrowser(event, { url: "https://example.com/", clientId: "tab-5", engine: "chrome", private: true });
  assert.equal(hidden.engine, "raticode");
  assert.equal(hidden.partition, PARTITIONS.private);
  assert.throws(() => browsers.createBrowser(event, { url: "https://example.com/", clientId: "tab-6", engine: "netscape" }), /not available in this version of Raticode/);
  for (const state of [own, fox, plain, hidden]) browsers.browserAction(event, { id: state.id, action: "close" });
});

test("Rem's Chrome tabs keep its site rules, sign-in sharing and download setting", async () => {
  const chrome = fakeChrome();
  const { browsers, sent } = harness({ policy: { engine: "chrome", remBlockedSites: ["bank.example"], remSignInSites: ["github.com"] }, chrome });
  const session = browsers.openAgentTab({ agent: "agent-1", label: "Rem" });
  assert.equal(sent("gofer:browser-agent-open").at(-1).engine, "chrome");
  await settle();
  assert.deepEqual(chrome.sent("Fetch.enable").at(-1), { patterns: [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }] });
  assert.deepEqual(chrome.sent("mirror").at(-1), { sites: ["github.com"], removed: [] });
  const contents = session.contents;
  // A link to a blocked site is refused before the request leaves Chrome; others go on.
  contents.handleEvent("Fetch.requestPaused", { requestId: "r1", frameId: contents.mainFrameId, resourceType: "Document", request: { url: "https://bank.example/" } });
  contents.handleEvent("Fetch.requestPaused", { requestId: "r2", frameId: contents.mainFrameId, resourceType: "Document", request: { url: "https://example.com/" } });
  await settle();
  assert.deepEqual(chrome.sent("Fetch.failRequest"), [{ requestId: "r1", errorReason: "Aborted" }]);
  assert.deepEqual(chrome.sent("Fetch.continueRequest"), [{ requestId: "r2" }]);
  // The user can't open a Chrome window from Rem's tab.
  assert.throws(() => browsers.browserAction({ sender: session.owner }, { id: session.id, action: "sign-in-window" }), /Rem's tabs keep separate sign-ins/);
  browsers.setPolicy(null, { engine: "chrome", remBlockedSites: ["bank.example"], remSignInSites: [], remDownloads: "allow" });
  await settle();
  assert.deepEqual(chrome.sent("mirror").at(-1), { sites: [], removed: ["github.com"] });
  assert.deepEqual(chrome.sent("rem-downloads"), [true]);
});

test("without Chrome installed, Rem's Chrome tab fails with a clear message", () => {
  const { browsers } = harness({ policy: { engine: "chrome" }, chrome: fakeChrome({ available: false }) });
  assert.throws(() => browsers.openAgentTab({ agent: "agent-1", label: "Rem" }), /Chrome is the default browser in Settings > Browser, but it is not installed/);
});

test("with Firefox chosen, web tabs stream from Firefox and previews stay on Raticode", async () => {
  const firefox = fakeFirefox();
  const { browsers, event, sent, directory } = harness({ policy: { engine: "firefox" }, firefox });
  const state = browsers.createBrowser(event, { url: "https://example.com/", clientId: "tab-1" });
  assert.equal(state.engine, "firefox");
  assert.equal(state.partition, undefined);
  await settle();
  assert.deepEqual(firefox.sent("browsingContext.navigate").map((params) => params.url), ["https://example.com/"]);
  // The studio has no view to adopt; it only attaches its canvas.
  assert.equal(browsers.browserAction(event, { id: state.id, action: "adopt", webContentsId: -1 }).engine, "firefox");

  browsers.browserAction(event, { id: state.id, action: "firefox-view", width: 800, height: 600, scale: 2, visible: true });
  await settle();
  assert.deepEqual(firefox.sent("browsingContext.setViewport").at(-1), { context: "context-1-user", viewport: { width: 800, height: 600 }, devicePixelRatio: 2 });
  browsers.browserAction(event, { id: state.id, action: "zoom", direction: 1 });
  await settle();
  assert.deepEqual(firefox.sent("browsingContext.setViewport").at(-1).viewport, { width: 727, height: 545 });

  browsers.browserAction(event, { id: state.id, action: "firefox-input", events: [{ t: "down", x: 10, y: 20, button: 0 }] });
  await settle();
  const pointer = firefox.sent("input.performActions").at(-1).actions[0];
  assert.equal(pointer.type, "pointer");
  assert.deepEqual(pointer.actions.at(-1), { type: "pointerDown", button: 0 });

  const contents = firefox.contexts.get("context-1-user");
  contents.emit("firefox-frame", { seq: 1, data: Buffer.from([1, 2]), width: 800, height: 600 });
  assert.equal(sent("gofer:browser-frame").at(-1).id, state.id);
  contents.emit("firefox-cursor", { cursor: "pointer" });
  assert.deepEqual(sent("gofer:browser-firefox").at(-1), { cursor: "pointer", kind: "cursor", id: state.id });

  const file = path.join(directory, "page.html");
  fs.writeFileSync(file, "<p>local</p>");
  const preview = browsers.createBrowser(event, { path: file });
  assert.equal(preview.engine, "raticode");
  assert.equal(preview.partition, PARTITIONS.preview);

  browsers.browserAction(event, { id: state.id, action: "close" });
  assert.deepEqual(firefox.sent("browsingContext.close").at(-1), { context: "context-1-user", promptUnload: false });
});

test("Rem's Firefox tabs keep its site rules, sign-in sharing and download setting", async () => {
  const firefox = fakeFirefox();
  const { browsers, sent } = harness({ policy: { engine: "firefox", remBlockedSites: ["bank.example"], remSignInSites: ["github.com"] }, firefox });
  const session = browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" });
  assert.equal(sent("gofer:browser-agent-open").at(-1).engine, "firefox");
  await settle();
  assert.deepEqual(firefox.sent("network.addIntercept").at(-1), { phases: ["beforeRequestSent"], contexts: ["context-1-rem"] });
  assert.deepEqual(firefox.sent("mirror").at(-1), { sites: ["github.com"], removed: [] });

  // A link to a blocked site is refused before the request leaves Firefox.
  const contents = session.contents;
  contents.handleEvent("network.beforeRequestSent", { context: "context-1-rem", isBlocked: true, navigation: "n1", request: { request: "r1", url: "https://bank.example/" } });
  contents.handleEvent("network.beforeRequestSent", { context: "context-1-rem", isBlocked: true, navigation: "n2", request: { request: "r2", url: "https://docs.example/" } });
  await settle();
  assert.deepEqual(firefox.sent("network.failRequest"), [{ request: "r1" }]);
  assert.deepEqual(firefox.sent("network.continueRequest"), [{ request: "r2" }]);
  assert.match(session.error, /blocked this page/);

  browsers.setPolicy(null, { engine: "firefox", remBlockedSites: ["bank.example"], remSignInSites: [], remDownloads: "allow" });
  await settle();
  assert.deepEqual(firefox.sent("mirror").at(-1), { sites: [], removed: ["github.com"] });
  assert.deepEqual(firefox.sent("rem-downloads"), [true]);
});

test("Firefox tabs ask the user about page dialogs and files, but leave Rem's to Rem", async () => {
  const firefox = fakeFirefox();
  const { browsers, event, messages, directory } = harness({ policy: { engine: "firefox" }, firefox, dialog: { messageResponse: 0, openPaths: ["/tmp/report.pdf"] } });
  const state = browsers.createBrowser(event, { url: "https://example.com/" });
  await settle();
  const contents = firefox.contexts.get("context-1-user");
  contents.handleEvent("browsingContext.userPromptOpened", { context: "context-1-user", type: "confirm", message: "Delete it?" });
  await settle();
  assert.equal(messages.at(-1).detail, "Delete it?");
  assert.deepEqual(firefox.sent("browsingContext.handleUserPrompt").at(-1), { context: "context-1-user", accept: true });
  contents.handleEvent("input.fileDialogOpened", { context: "context-1-user", element: { sharedId: "input-1" }, multiple: false });
  await settle();
  assert.deepEqual(firefox.sent("input.setFiles").at(-1), { context: "context-1-user", element: { sharedId: "input-1" }, files: ["/tmp/report.pdf"] });

  const rem = browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" });
  await settle();
  const before = messages.length;
  rem.contents.handleEvent("browsingContext.userPromptOpened", { context: rem.contents.context, type: "alert", message: "Hi" });
  await settle();
  assert.equal(messages.length, before);
  assert.ok(state.id && directory);
});

test("without Firefox installed, Rem's Firefox tab fails with a clear message", () => {
  const { browsers } = harness({ policy: { engine: "firefox" }, firefox: fakeFirefox({ available: false }) });
  assert.throws(() => browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" }), /not installed/);
});

test("Firefox downloads move from staging to Downloads, risky ones to quarantine, and Rem's are blocked", async () => {
  const firefox = fakeFirefox();
  const { browsers, event, store, downloads, directory } = harness({ policy: { engine: "firefox" }, firefox });
  browsers.createBrowser(event, { url: "https://example.com/" });
  await settle();
  const contents = firefox.contexts.get("context-1-user");
  const staging = path.join(directory, "staging");
  fs.mkdirSync(staging);
  const finish = (name, navigation, body = "data") => {
    const file = path.join(staging, name);
    fs.writeFileSync(file, body);
    contents.handleEvent("browsingContext.downloadWillBegin", { context: "context-1-user", navigation, url: `https://example.com/${name}`, suggestedFilename: name });
    contents.handleEvent("browsingContext.downloadEnd", { context: "context-1-user", navigation, status: "complete", filepath: file });
    return file;
  };
  const report = finish("report.pdf", "d1");
  const saved = store.listDownloads().find((item) => item.filename === "report.pdf");
  assert.equal(saved.state, "completed");
  assert.equal(saved.path, path.join(downloads, "report.pdf"));
  assert.equal(fs.readFileSync(saved.path, "utf8"), "data");
  assert.equal(fs.existsSync(report), false);
  finish("setup.exe", "d2");
  const risky = store.listDownloads().find((item) => item.filename === "setup.exe");
  assert.equal(risky.state, "quarantined");
  assert.ok(risky.quarantinePath.startsWith(path.join(directory, "quarantine")));

  const rem = browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" });
  await settle();
  rem.contents.handleEvent("browsingContext.downloadWillBegin", { context: rem.contents.context, navigation: "d3", url: "https://example.com/x.zip", suggestedFilename: "x.zip" });
  assert.equal(rem.lastBlockedDownload, "x.zip");
  assert.equal(store.listDownloads().some((item) => item.filename === "x.zip"), false);
});

// Firefox 158 sends navigation null for a link with a download attribute and
// names the download by its own id. Raticode used to file the start under one
// key and look the end up under another, then delete the finished file.
test("Firefox downloads from links a page built itself (blob: and data:) reach Downloads", async () => {
  const firefox = fakeFirefox();
  const { browsers, event, store, downloads, directory } = harness({ policy: { engine: "firefox" }, firefox });
  browsers.createBrowser(event, { url: "https://chat.example.com/" });
  await settle();
  const contents = firefox.contexts.get("context-1-user");
  const staging = path.join(directory, "staging");
  fs.mkdirSync(staging);
  const html = "<!doctype html><title>Chat</title>";
  const cases = [
    { name: "chat.html", url: "blob:https://chat.example.com/5c7baa75-7dab-40c9-8fdd-63ea16927d62", download: "321cd4ef" },
    { name: "notes.html", url: `data:text/html,${encodeURIComponent(html)}`, download: "c15f5216" },
    { name: "older.html", url: "blob:https://chat.example.com/de8a9e64", download: undefined },
  ];
  for (const item of cases) {
    const file = path.join(staging, item.name);
    fs.writeFileSync(file, html);
    const params = { context: "context-1-user", navigation: null, url: item.url, ...(item.download ? { download: item.download } : {}) };
    contents.handleEvent("browsingContext.downloadWillBegin", { ...params, suggestedFilename: item.name });
    contents.handleEvent("browsingContext.downloadEnd", { ...params, status: "complete", filepath: file });
    const saved = store.listDownloads().find((entry) => entry.filename === item.name);
    assert.equal(saved.state, "completed", item.name);
    assert.equal(saved.path, path.join(downloads, item.name));
    assert.equal(fs.readFileSync(saved.path, "utf8"), html);
    assert.equal(fs.existsSync(file), false, `${item.name} stayed in staging`);
  }
  // A cancelled download is listed as cancelled, not left running.
  contents.handleEvent("browsingContext.downloadWillBegin", { context: "context-1-user", navigation: null, download: "x1", url: "blob:https://chat.example.com/x1", suggestedFilename: "gone.html" });
  contents.handleEvent("browsingContext.downloadEnd", { context: "context-1-user", navigation: null, download: "x1", url: "blob:https://chat.example.com/x1", status: "canceled" });
  assert.equal(store.listDownloads().find((entry) => entry.filename === "gone.html").state, "cancelled");

  // Rem's tabs still may not download a file the page built.
  const rem = browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" });
  await settle();
  const remFile = path.join(staging, "rem.html");
  fs.writeFileSync(remFile, html);
  rem.contents.handleEvent("browsingContext.downloadWillBegin", { context: rem.contents.context, navigation: null, download: "r1", url: "blob:https://chat.example.com/r1", suggestedFilename: "rem.html" });
  rem.contents.handleEvent("browsingContext.downloadEnd", { context: rem.contents.context, navigation: null, download: "r1", status: "complete", filepath: remFile });
  assert.equal(rem.lastBlockedDownload, "rem.html");
  assert.equal(store.listDownloads().some((entry) => entry.filename === "rem.html"), false);
  assert.equal(fs.existsSync(remFile), false, "Rem's refused file was left in staging");
  assert.equal(fs.existsSync(path.join(downloads, "rem.html")), false);
});

test("Firefox tabs offer Raticode's install on Firefox Add-ons pages and explain Firefox's own pages", async () => {
  const firefox = fakeFirefox();
  const { browsers, event } = harness({ policy: { engine: "firefox" }, firefox });
  const state = browsers.createBrowser(event, { url: "https://example.com/" });
  await settle();
  const contents = firefox.contexts.get("context-1-user");
  contents.commitUrl("https://addons.mozilla.org/en-US/firefox/addon/ublock-origin/");
  assert.equal(browsers.browserAction(event, { id: state.id, action: "stop" }).firefoxAddon, "ublock-origin");
  contents.commitUrl("https://example.com/next");
  assert.equal(browsers.browserAction(event, { id: state.id, action: "stop" }).firefoxAddon, "");
  for (const url of ["about:preferences", "about:addons", " about:config", "moz-extension://abc/options.html"]) {
    assert.throws(() => browsers.browserAction(event, { id: state.id, action: "navigate", url }), /Firefox's own pages[\s\S]*Settings > Browser > Firefox/);
  }
  // Raticode tabs keep their own answer for these addresses.
  const raticode = harness();
  const plain = raticode.open({ url: "https://example.com/" });
  assert.equal(plain.state.firefoxAddon, "");
});

test("restarting Firefox from Settings reopens its tabs at their pages", async () => {
  const firefox = fakeFirefox();
  const { browsers, event } = harness({ policy: { engine: "firefox" }, firefox });
  browsers.createBrowser(event, { url: "https://example.com/" });
  await settle();
  firefox.contexts.get("context-1-user").commitUrl("https://example.com/");
  await browsers.browserLibrary(event, { action: "firefox-restart" });
  await settle();
  assert.deepEqual(firefox.calls.filter(([name]) => name === "stop").length, 1);
  assert.ok(firefox.contexts.has("context-2-user"), "the tab moved to a new Firefox window");
  assert.deepEqual(firefox.sent("browsingContext.navigate").at(-1), { context: "context-2-user", url: "https://example.com/", wait: "none" });
  await assert.rejects(browsers.browserLibrary(event, { action: "firefox-unknown" }), /Unknown browser library action/);
});

test("after Google refuses a Firefox tab, the user can sign in from a Firefox window and come back", async () => {
  const firefox = fakeFirefox();
  const { browsers, event } = harness({ policy: { engine: "firefox" }, firefox });
  const state = browsers.createBrowser(event, { url: "https://www.youtube.com/" });
  await settle();
  const contents = firefox.contexts.get("context-1-user");
  // The address goes on Firefox's command line, so its query and fragment are left off.
  contents.commitUrl("https://www.youtube.com/?token=secret#frag");
  await settle();
  contents.commitUrl("https://accounts.google.com/v3/signin/rejected?epd=abc");
  await settle();
  assert.equal(browsers.browserAction(event, { id: state.id, action: "stop" }).firefoxSignInBlocked, true);
  await browsers.browserAction(event, { id: state.id, action: "firefox-sign-in-window" });
  // The window opens Google's sign-in, returning to the page that asked for it.
  assert.deepEqual(firefox.calls.filter(([name]) => name === "sign-in-window").at(-1), ["sign-in-window", `https://accounts.google.com/ServiceLogin?continue=${encodeURIComponent("https://www.youtube.com/")}`]);
  assert.equal(contents.dead, true);
  assert.match(contents.lastError, /open in a window for signing in/);
  // Closing the window brings the tab back to its page.
  await browsers.reviveFirefoxTabs();
  await settle();
  assert.ok(firefox.contexts.has("context-2-user"));
  // From Settings, any web address or Firefox's own settings page.
  await browsers.browserLibrary(event, { action: "firefox-sign-in-window", url: "about:preferences#sync" });
  assert.deepEqual(firefox.calls.filter(([name]) => name === "sign-in-window").at(-1), ["sign-in-window", "about:preferences#sync"]);
  await browsers.browserLibrary(event, { action: "firefox-sign-in-window" });
  assert.deepEqual(firefox.calls.filter(([name]) => name === "sign-in-window").at(-1), ["sign-in-window", "https://accounts.google.com/"]);
});

test("Rem's Firefox tabs neither flag Google's refusal nor open a sign-in window", async () => {
  const firefox = fakeFirefox();
  const { browsers, event } = harness({ policy: { engine: "firefox" }, firefox });
  const rem = browsers.openAgentTab({ agent: "agent-0123456789", label: "Rem" });
  await settle();
  rem.contents.commitUrl("https://accounts.google.com/v3/signin/rejected?epd=abc");
  assert.equal(browsers.browserAction(event, { id: rem.id, action: "stop" }).firefoxSignInBlocked, false);
  assert.throws(() => browsers.browserAction(event, { id: rem.id, action: "firefox-sign-in-window" }), /Rem's tabs keep separate sign-ins/);
  assert.equal(firefox.calls.some(([name]) => name === "sign-in-window"), false);
});

test("a stuck or ended page reloads with its history kept, and only its owner can end a stuck page", () => {
  const { browsers, event, open, sent } = harness();
  const web = open({ clientId: "browser:https://example.com/", url: "https://example.com/" });
  web.contents.loadURL("https://example.com/next");
  let crashes = 0;
  web.contents.forcefullyCrashRenderer = () => { crashes += 1; web.contents.emit("render-process-gone", {}, { reason: "killed" }); };

  // Stopping answering only flags the tab: there is no dialog, and nothing ends while it answers again.
  web.contents.emit("unresponsive");
  assert.equal(sent("gofer:browser-state").at(-1).unresponsive, true);
  web.contents.emit("responsive");
  assert.equal(sent("gofer:browser-state").at(-1).unresponsive, false);
  browsers.browserAction(event, { id: web.id, action: "reload", recover: true });
  assert.equal(crashes, 0, "a page that answers isn't ended");
  assert.equal(web.contents.reloaded, "soft");

  // A stuck page is ended, then reloaded in place, so its back/forward list stays.
  web.contents.reloaded = "";
  web.contents.emit("unresponsive");
  assert.throws(() => browsers.browserAction({ sender: { id: 99 } }, { id: web.id, action: "reload", recover: true }), /not found/);
  assert.equal(crashes, 0, "another window can't end it");
  browsers.browserAction(event, { id: web.id, action: "reload", recover: true });
  assert.equal(crashes, 1);
  assert.equal(web.contents.reloaded, "soft");
  assert.deepEqual(web.contents.loaded, ["https://example.com/next"], "reloaded, not loaded from scratch");
  assert.equal(web.contents.index, 1);

  // Ending it isn't reported as a crash: the reload is already under way.
  assert.equal(sent("gofer:browser-state").some((item) => item.crashed), false);
  assert.equal(sent("gofer:browser-state").at(-1).unresponsive, false);

  // A page process that ends by itself is reported to the tab, which reloads it once it's in front (IntegratedBrowser).
  web.contents.emit("render-process-gone", {}, { reason: "oom" });
  assert.deepEqual(sent("gofer:browser-state").at(-1).crashed, { reason: "oom" });
  web.contents.reloaded = "";
  browsers.browserAction(event, { id: web.id, action: "reload" });
  assert.equal(web.contents.reloaded, "soft");
  assert.equal(crashes, 1, "a plain reload never ends the page process");
});

test("a local HTML preview can only read files inside the folder it was granted", () => {
  // Electron lets file:// pages read other file:// URLs. A previewed page used to be
  // able to fetch ~/.ssh or list the home folder and post the result to a website.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "preview-guard-"));
  const project = path.join(root, "project");
  const secret = path.join(root, "secret.txt");
  fs.mkdirSync(project);
  fs.writeFileSync(path.join(project, "index.html"), "<p>report</p>");
  fs.writeFileSync(path.join(project, "style.css"), "p{}");
  fs.writeFileSync(secret, "private");
  const grants = new Map([["grant-1", project]]);
  const resolvePath = (target, { grantId } = {}) => {
    const allowed = grants.get(grantId);
    if (!fs.existsSync(target)) throw new Error(`Path does not exist: ${target}`);
    if (!allowed || path.relative(allowed, target).startsWith("..")) throw new Error("outside");
    return target;
  };
  try {
    const { open, profiles } = harness({ resolvePath });
    const guard = profiles[PARTITIONS.preview].beforeRequest;
    assert.deepEqual(guard.filter.urls, ["file://*/*", "http://*/*", "https://*/*", "ws://*/*", "wss://*/*"]);
    let webContentsId = 9999;
    const decide = (url) => { let result; guard.listener({ url, webContentsId }, (value) => { result = value; }); return result; };
    // Before any preview opens, nothing on disk is reachable.
    assert.deepEqual(decide(pathToFileURL(path.join(project, "index.html")).href), { cancel: true });
    webContentsId = open({ path: path.join(project, "index.html"), grantId: "grant-1" }).contents.id;
    assert.deepEqual(decide(pathToFileURL(path.join(project, "index.html")).href), { cancel: false });
    assert.deepEqual(decide(pathToFileURL(path.join(project, "style.css")).href), { cancel: false });
    assert.deepEqual(decide(pathToFileURL(secret).href), { cancel: true });
    assert.deepEqual(decide(pathToFileURL(root).href + "/"), { cancel: true });
    assert.deepEqual(decide("file:///etc/passwd"), { cancel: true });
    assert.deepEqual(decide("not a url"), { cancel: true });
    // The user and Rem profiles never get this guard; their pages can't load file:// at all.
    assert.equal(profiles[PARTITIONS.user].beforeRequest, undefined);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("local HTML previews stay offline until the user loads remote content for that file", async () => {
  const { browsers, event, open, profiles, sent, store, directory, views } = harness();
  const report = path.join(directory, "report.html");
  const chart = path.join(directory, "chart.html");
  fs.writeFileSync(report, "<p>report</p>");
  fs.writeFileSync(chart, "<img src='https://example.com/chart.png'>");
  const guard = profiles[PARTITIONS.preview].beforeRequest;
  const decide = (details) => { let result; guard.listener(details, (value) => { result = value; }); return result; };
  const one = open({ path: report });
  const two = open({ path: chart });
  // Network requests from a preview are cancelled, and counted for its bar.
  for (const url of ["https://example.com/x", "http://127.0.0.1:9/post", "ws://127.0.0.1:9/", "wss://example.com/"]) {
    assert.deepEqual(decide({ url, webContentsId: one.contents.id }), { cancel: true }, url);
  }
  // A request no open preview made is cancelled too.
  assert.deepEqual(decide({ url: "https://example.com/", webContentsId: 9999 }), { cancel: true });
  assert.deepEqual(sent("gofer:browser-state").filter((state) => state.id === one.id).at(-1).remoteContent, { allowed: false, blocked: 1 });
  // The offline profile's only way out is a proxy that isn't there, and its
  // pages' WebRTC may use only proxied routes, so routes the guard can't see
  // (WebRTC, preconnect) lead nowhere too.
  assert.deepEqual(profiles[PARTITIONS.preview].proxy, { mode: "fixed_servers", proxyRules: "http://127.0.0.1:9", proxyBypassRules: "<-loopback>" });
  assert.equal(profiles[PARTITIONS.previewOnline].proxy, undefined);
  assert.equal(one.contents.webrtcPolicy, "disable_non_proxied_udp");
  assert.equal(one.state.partition, PARTITIONS.preview);
  // Allowing one file moves its previews to the online profile, in a new view
  // at the same place, and lets only that file online.
  browsers.browserAction(event, { id: one.id, action: "place", x: 1, y: 2, width: 300, height: 200, visible: true });
  const offlineContents = one.contents;
  const state = browsers.browserAction(event, { id: one.id, action: "preview-remote", allowed: true });
  assert.deepEqual(state.remoteContent, { allowed: true, blocked: 0 });
  assert.equal(offlineContents.destroyed, true);
  const onlineView = views.at(-1);
  assert.equal(onlineView.webPreferences.partition, PARTITIONS.previewOnline);
  assert.equal(onlineView.webContents.url, pathToFileURL(report).href);
  assert.equal(onlineView.webContents.webrtcPolicy, undefined);
  assert.equal(onlineView.visible, true);
  assert.equal(onlineView.bounds.width, 300);
  assert.equal(store.previewRemoteAllowed(report), true);
  const decideOnline = (details) => { let result; profiles[PARTITIONS.previewOnline].beforeRequest.listener(details, (value) => { result = value; }); return result; };
  assert.deepEqual(decideOnline({ url: "https://example.com/x", webContentsId: onlineView.webContents.id }), { cancel: false });
  assert.deepEqual(decide({ url: "https://example.com/x", webContentsId: two.contents.id }), { cancel: true });
  // The online profile still keeps previews inside their folder.
  assert.deepEqual(decideOnline({ url: "file:///etc/passwd", webContentsId: onlineView.webContents.id }), { cancel: true });
  // A later preview of the same file remembers it.
  const three = open({ path: report });
  assert.equal(three.state.partition, PARTITIONS.previewOnline);
  assert.deepEqual(decideOnline({ url: "https://example.com/x", webContentsId: three.contents.id }), { cancel: false });
  // The online page moving to a file that isn't allowed goes back offline first.
  const navigateOnline = (url) => { let prevented = false; views.at(-1).webContents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, url); return prevented; };
  assert.equal(navigateOnline(pathToFileURL(chart).href), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(views.at(-1).webPreferences.partition, PARTITIONS.preview);
  assert.equal(views.at(-1).webContents.url, pathToFileURL(chart).href);
  browsers.browserAction(event, { id: one.id, action: "preview-remote", allowed: false });
  assert.equal(store.previewRemoteAllowed(report), false);
  assert.equal(views.at(-1).webPreferences.partition, PARTITIONS.preview);
  // Web tabs can't use the action.
  const web = open({ url: "https://example.com/" });
  assert.throws(() => browsers.browserAction(event, { id: web.id, action: "preview-remote", allowed: true }), /Only local HTML previews/);
  // A file another app opened runs no scripts and can never go online.
  const external = open({ path: chart, external: true });
  assert.equal(views.at(-1).webPreferences.javascript, false);
  assert.equal(views.find((view) => view.webContents === one.contents).webPreferences.javascript, undefined);
  assert.equal(external.state.external, true);
  assert.equal(one.state.external, false);
  assert.throws(() => browsers.browserAction(event, { id: external.id, action: "preview-remote", allowed: true }), /never load remote content/);
  // The external flag only applies to local files.
  open({ url: "https://example.com/", external: true });
  assert.notEqual(views.at(-1).webPreferences.javascript, false);
});

test("a local preview hands websites to Browser tabs only for a link the user clicked", async () => {
  const { browsers, event, open, sent, directory } = harness();
  const file = path.join(directory, "report.html");
  fs.writeFileSync(file, "<a href='https://example.com/'>site</a>");
  const preview = open({ path: file });
  const navigate = (url) => { let prevented = false; preview.contents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, url); return prevented; };
  // A page script going to a website, or opening one, goes nowhere.
  assert.equal(navigate("https://example.com/?data=secret"), true);
  assert.deepEqual(preview.contents.openHandler({ url: "https://example.com/?data=secret", disposition: "foreground-tab" }), { action: "deny" });
  assert.equal(sent("gofer:browser-open-tab").length, 0);
  // A click anywhere doesn't let the page's script, or a meta refresh, open
  // a site: Raticode's preload hands over real link clicks before any navigation.
  preview.contents.input();
  assert.equal(navigate("https://example.com/docs?data=secret"), true);
  assert.deepEqual(preview.contents.openHandler({ url: "https://example.com/?data=secret", disposition: "foreground-tab" }), { action: "deny" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sent("gofer:browser-open-tab").length, 0);
  // A file from another app runs no scripts, so Raticode's own script world
  // notes the link the user clicked; only that address opens, once.
  const external = open({ path: file, external: true });
  const worlds = [];
  let clicked = null;
  external.contents.executeJavaScriptInIsolatedWorld = async (world, [script]) => {
    worlds.push(world);
    if (script.code.includes("addEventListener")) return true;
    const taken = clicked;
    clicked = null;
    return taken;
  };
  external.contents.emit("dom-ready");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(worlds.length, 1, "the click recorder is installed in each document");
  const navigateExternal = (url) => { let prevented = false; external.contents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, url); return prevented; };
  const settled = () => new Promise((resolve) => setTimeout(resolve, 5));
  external.contents.input();
  assert.equal(navigateExternal("https://example.com/refresh"), true);
  await settled();
  assert.equal(sent("gofer:browser-open-tab").length, 0, "a navigation with no link click opened a tab");
  clicked = { url: "https://example.com/other", age: 20 };
  assert.equal(navigateExternal("https://example.com/refresh"), true);
  await settled();
  assert.equal(sent("gofer:browser-open-tab").length, 0, "a navigation to another address than the clicked link opened a tab");
  clicked = { url: "https://example.com/docs", age: 60000 };
  assert.equal(navigateExternal("https://example.com/docs"), true);
  await settled();
  assert.equal(sent("gofer:browser-open-tab").length, 0, "an old click opened a tab");
  clicked = { url: "https://example.com/docs", age: 20 };
  assert.equal(navigateExternal("https://example.com/docs"), true);
  await settled();
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/docs", background: false, engine: "raticode" });
  // The click was used up.
  assert.equal(navigateExternal("https://example.com/docs"), true);
  await settled();
  assert.equal(sent("gofer:browser-open-tab").length, 1);
  // A link with a target opens the same way, through the window handler.
  clicked = { url: "https://example.com/new", age: 10 };
  assert.deepEqual(external.contents.openHandler({ url: "https://example.com/new", disposition: "foreground-tab" }), { action: "deny" });
  await settled();
  assert.equal(sent("gofer:browser-open-tab").at(-1).url, "https://example.com/new");
  // Links to other local files still navigate inside the preview's grant.
  assert.equal(navigate(pathToFileURL(file).href), false);
  // A website typed into a preview's address bar opens in a Browser tab.
  browsers.browserAction(event, { id: preview.id, action: "navigate", url: "https://example.org/" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.org/", background: false, engine: "raticode" });
  assert.equal(preview.contents.loaded.length, 0);
});

// Raticode (gecko) as gecko-control.cjs presents it: the tab in front of
// Gecko's window can be shared, and sharing ends by letting go of it.
function fakeGecko({ enabled = true } = {}) {
  const engine = fakeFirefox();
  const released = [];
  return Object.assign(engine, {
    released,
    status: () => ({ available: enabled, enabled, executable: "/usr/bin/firefox", running: true, problem: "" }),
    shareableTab: async () => ({ context: "users-gecko-tab", url: "https://music.example/watch", title: "Music" }),
    adoptContext: async (context) => { engine.calls.push(["adopt", context]); return { context, generation: 1 }; },
    release: (context) => { released.push(context); },
  });
}

test("the user's Gecko tab can be shared with Rem under the shared-tab approvals, and stays open when sharing ends", async () => {
  const off = harness({ gecko: fakeGecko({ enabled: false }) });
  await assert.rejects(off.browsers.shareGeckoTab(off.owner, "thread-1"), /Let Rem use Raticode \(gecko\)/);

  const gecko = fakeGecko();
  const { browsers, owner } = harness({ gecko });
  await assert.rejects(browsers.shareGeckoTab(owner, ""), /Choose a Rem thread/);
  const shared = await browsers.shareGeckoTab(owner, "thread-1", "Rem");
  assert.equal(shared.url, "https://music.example/watch");
  assert.deepEqual(gecko.calls.find(([name]) => name === "adopt"), ["adopt", "users-gecko-tab"]);
  // The shared tab is never resized to fit a Raticode view.
  assert.equal(gecko.sent("browsingContext.setViewport").length, 0);
  const [session] = browsers.agentTabs(remOwnerForThread("thread-1"));
  assert.equal(session.id, shared.id);
  assert.equal(session.engine, "gecko");
  assert.equal(session.agent.shared, true);
  await assert.rejects(browsers.shareGeckoTab(owner, "thread-2"), /already shared/);

  // Rem's consequential actions wait for the user, who answers in the Gecko view.
  const approval = browsers.confirmAgent(session, { action: "click", label: "Place order", url: "https://music.example/watch" });
  const [listed] = browsers.geckoSharedTabs();
  const request = listed.prompts.find((item) => item.kind === "rem-confirm");
  assert.equal(request.label, "Place order");
  assert.throws(() => browsers.geckoSharedAction({ id: session.id, command: "respond", requestId: "someone-else" }), /already been answered/);
  browsers.geckoSharedAction({ id: session.id, command: "respond", requestId: request.requestId, response: "approve" });
  assert.equal(await approval, true);
  browsers.geckoSharedAction({ id: session.id, command: "pause", paused: true });
  assert.equal(browsers.geckoSharedTabs()[0].agent.paused, true);

  browsers.geckoSharedAction({ id: session.id, command: "stop" });
  assert.deepEqual(gecko.released, ["users-gecko-tab"]);
  assert.equal(gecko.sent("browsingContext.close").length, 0);
  assert.deepEqual(browsers.agentTabs(remOwnerForThread("thread-1")), []);
  assert.deepEqual(browsers.geckoSharedTabs(), []);
  assert.throws(() => browsers.geckoSharedAction({ id: session.id, command: "stop" }), /isn't shared/);

  // Closing the tab in Gecko, or Gecko stopping, ends the sharing too.
  const again = await browsers.shareGeckoTab(owner, "thread-1");
  gecko.contexts.get("users-gecko-tab").handleEvent("browsingContext.contextDestroyed", { context: "users-gecko-tab" });
  assert.equal(browsers.geckoSharedTabs().some((item) => item.id === again.id), false);
  await browsers.shareGeckoTab(owner, "thread-1");
  browsers.streamStopped("gecko", "Raticode (gecko) stopped");
  assert.deepEqual(browsers.geckoSharedTabs(), []);
  assert.equal(gecko.sent("browsingContext.close").length, 0);
});

test("with the app's own path resolver, a preview still can't read files outside its grant", () => {
  // main.js's resolvePath only checks that a file exists. The guard must ask the
  // grant-checking resolver instead, or every existing file on disk is readable.
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "preview-wiring-")));
  const project = path.join(root, "project");
  const other = path.join(root, "other");
  const secret = path.join(root, "secret.txt");
  fs.mkdirSync(path.join(project, "docs"), { recursive: true });
  fs.mkdirSync(other);
  fs.writeFileSync(path.join(project, "docs", "index.html"), "<p>report</p>");
  fs.writeFileSync(path.join(project, "style.css"), "p{}");
  fs.writeFileSync(path.join(other, "page.html"), "<p>loose</p>");
  fs.writeFileSync(path.join(other, "notes.txt"), "next to it");
  fs.writeFileSync(secret, "private");
  fs.symlinkSync(secret, path.join(project, "docs", "linked.txt"));
  const grants = new Map([["grant-1", project]]);
  const resolvePreviewPath = (target, { grantId } = {}) => {
    const allowed = grants.get(grantId);
    const real = fs.realpathSync(target);
    if (!allowed || path.relative(allowed, real).startsWith("..")) throw new Error("outside");
    return real;
  };
  try {
    const { open, profiles } = harness({ resolvePreviewPath });
    const guard = profiles[PARTITIONS.preview].beforeRequest;
    let webContentsId = 0;
    const decide = (file) => { let result; guard.listener({ url: pathToFileURL(file).href, webContentsId }, (value) => { result = value; }); return result.cancel === false; };
    webContentsId = open({ path: path.join(project, "docs", "index.html"), grantId: "grant-1" }).contents.id;
    assert.equal(decide(path.join(project, "docs", "index.html")), true);
    assert.equal(decide(path.join(project, "style.css")), true, "the grant covers the whole project");
    assert.equal(decide(secret), false);
    assert.equal(decide(path.join(project, "docs", "linked.txt")), false, "a link out of the grant is refused");
    assert.equal(decide("/etc/passwd"), false);
    // A preview opened without a grant reads only inside its own file's folder.
    webContentsId = open({ url: pathToFileURL(path.join(other, "page.html")).href }).contents.id;
    assert.equal(decide(path.join(other, "notes.txt")), true);
    assert.equal(decide(secret), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a preview reads only its own folder, not the folder of another open preview", () => {
  // The guard used to let a file through when any open preview could read it, so
  // a page in one folder could read everything another preview's folder held.
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "preview-own-")));
  const first = path.join(root, "first");
  const second = path.join(root, "second");
  fs.mkdirSync(first);
  fs.mkdirSync(second);
  fs.writeFileSync(path.join(first, "a.html"), "<p>a</p>");
  fs.writeFileSync(path.join(first, "shared.css"), "p{}");
  fs.writeFileSync(path.join(second, "b.html"), "<p>b</p>");
  fs.writeFileSync(path.join(second, "only-b.txt"), "B's notes");
  try {
    const { open, profiles } = harness();
    const guard = profiles[PARTITIONS.preview].beforeRequest;
    const decide = (file, webContentsId) => { let result; guard.listener({ url: pathToFileURL(file).href, webContentsId }, (value) => { result = value; }); return result; };
    const a = open({ url: pathToFileURL(path.join(first, "a.html")).href });
    const b = open({ url: pathToFileURL(path.join(second, "b.html")).href });
    assert.deepEqual(decide(path.join(first, "shared.css"), a.contents.id), { cancel: false });
    assert.deepEqual(decide(path.join(second, "only-b.txt"), b.contents.id), { cancel: false });
    // Preview A asks for a file only preview B's folder holds.
    assert.deepEqual(decide(path.join(second, "only-b.txt"), a.contents.id), { cancel: true });
    assert.deepEqual(decide(path.join(first, "a.html"), b.contents.id), { cancel: true });
    // A request no open preview made reads nothing, even inside an open preview's folder.
    assert.deepEqual(decide(path.join(first, "shared.css"), 9999), { cancel: true });
    assert.deepEqual(decide(path.join(first, "shared.css"), undefined), { cancel: true });
    // A page navigating itself is held to its own folder the same way.
    let prevented = false;
    a.contents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, pathToFileURL(path.join(second, "only-b.txt")).href);
    assert.equal(prevented, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a preview's file:// request naming another computer is refused before any disk call", (t) => {
  // On Windows file://host/share/x.html is a network share path, and resolving it
  // contacts that host with the user's sign-in. The host is refused before
  // fileURLToPath or realpath ever sees it.
  const resolved = [];
  const { open, profiles, directory } = harness({ resolvePreviewPath: (target) => { resolved.push(target); throw new Error("outside"); } });
  fs.writeFileSync(path.join(directory, "page.html"), "<p>page</p>");
  const page = open({ path: path.join(directory, "page.html") });
  const guard = profiles[PARTITIONS.preview].beforeRequest;
  const decide = (url) => { let result; guard.listener({ url, webContentsId: page.contents.id }, (value) => { result = value; }); return result; };
  const realpath = t.mock.method(fs, "realpathSync");
  for (const url of ["file://evil-host/share/x.html", "file://EVIL-HOST/share/x.html", "file://127.0.0.1/c$/x.html", "file://evil-host:445/share/x.html"]) {
    assert.deepEqual(decide(url), { cancel: true }, url);
  }
  assert.equal(realpath.mock.callCount(), 0);
  assert.deepEqual(resolved, []);
  // file://localhost/ is this computer, and still reads inside the preview's own folder.
  assert.deepEqual(decide(`file://localhost${pathToFileURL(path.join(directory, "page.html")).pathname}`), { cancel: false });
  assert.ok(realpath.mock.callCount() > 0);
  // A page link to a network share isn't handed to the studio either.
  let prevented = false;
  page.contents.emit("will-navigate", { preventDefault: () => { prevented = true; } }, "file://evil-host/share/x.html");
  assert.equal(prevented, true);
});

test("on Windows, where a file:// host is a network share path, the guard still never resolves it", (t) => {
  // Node turns file://host/share into \\host\share only on Windows, so load the
  // module with Windows file URL rules to check the order there.
  const url = require("node:url");
  const toPath = url.fileURLToPath;
  const converted = [];
  t.mock.method(url, "fileURLToPath", (value, options) => { converted.push(String(value)); return toPath(value, { ...options, windows: true }); });
  const modulePath = require.resolve("../browser-sessions.cjs");
  delete require.cache[modulePath];
  let windowsSessions;
  try {
    ({ createBrowserSessions: windowsSessions } = require("../browser-sessions.cjs"));
  } finally {
    delete require.cache[modulePath];
  }
  const { open, profiles, directory } = harness({ createSessions: windowsSessions });
  fs.writeFileSync(path.join(directory, "page.html"), "<p>page</p>");
  const page = open({ path: path.join(directory, "page.html") });
  const guard = profiles[PARTITIONS.preview].beforeRequest;
  const realpath = t.mock.method(fs, "realpathSync");
  converted.length = 0;
  let result;
  guard.listener({ url: "file://evil-host/share/x.html", webContentsId: page.contents.id }, (value) => { result = value; });
  assert.deepEqual(result, { cancel: true });
  assert.deepEqual(converted, []);
  assert.equal(realpath.mock.callCount(), 0);
});
