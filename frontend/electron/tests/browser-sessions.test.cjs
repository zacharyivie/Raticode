// Exercises browser-sessions.cjs against small Electron stand-ins, so the
// policy wiring is covered without launching Chromium.
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createBrowserSessions, remOwnerForThread, sameProfile, sanitizePromptResponse } = require("../browser-sessions.cjs");
const { createBrowserStore } = require("../browser-store.cjs");
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
    };
  }
  getUserAgent() { return this.userAgent || "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Raticode/0.3.9 Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36"; }
  setUserAgent(value, acceptLanguages) { this.userAgent = value; this.acceptLanguages = acceptLanguages; }
  setPermissionRequestHandler(handler) { this.requestHandler = handler; }
  setPermissionCheckHandler(handler) { this.checkHandler = handler; }
  setDevicePermissionHandler(handler) { this.deviceHandler = handler; }
  setCertificateVerifyProc(proc) { this.verifyProc = proc; }
  setSpellCheckerEnabled(value) { this.spellcheck = value; }
  setSpellCheckerLanguages(value) { this.languages = value; }
  async clearStorageData() { this.cookieList = []; }
  async clearCache() {}
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
  }
  go(index) { this.index = index; this.url = this.entries[index].url; this.emit("did-navigate", {}, this.url); }
  getType() { return "webview"; }
  isDestroyed() { return false; }
  setBackgroundColor() {}
  setBackgroundThrottling(value) { this.throttling = value; }
  setZoomFactor(value) { this.zoom = value; }
  setWindowOpenHandler(handler) { this.openHandler = handler; }
  getURL() { return this.url; }
  getTitle() { return this.title; }
  isLoading() { return false; }
  isFocused() { return false; }
  focus() {}
  async loadURL(url) {
    this.loaded.push(url);
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

function harness({ policy, dialog: dialogOverrides = {} } = {}) {
  const profiles = Object.fromEntries(Object.values(PARTITIONS).map((name) => [name, new FakeProfile(name)]));
  const owner = Object.assign(new EventEmitter(), {
    id: 1,
    sent: [],
    send(channel, payload) { this.sent.push([channel, payload]); },
    isDestroyed: () => false,
    getZoomFactor: () => 1,
    focus() {},
  });
  const mainWindow = { isDestroyed: () => false, webContents: owner };
  const contentsById = new Map();
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "browser-sessions-"));
  const downloads = path.join(directory, "Downloads");
  fs.mkdirSync(downloads);
  const store = createBrowserStore(path.join(directory, "profile"), { writeDelayMs: 1 });
  const opened = [];
  const messages = [];
  const app = {
    getPath: (name) => (name === "downloads" ? downloads : directory),
    getLocale: () => "en-US",
    getPreferredSystemLanguages: () => ["fr-FR", "en-US"],
    userAgentFallback: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Raticode/0.3.9 Chrome/156.0.8078.49 Electron/45.1.0 Safari/537.36",
  };
  const browsers = createBrowserSessions({
    app,
    Menu: { buildFromTemplate: (template) => ({ template, popup() {} }) },
    clipboard: { writeText() {} },
    dialog: {
      showMessageBoxSync: () => 0,
      showMessageBox: async (_owner, options) => { messages.push(options); return { response: dialogOverrides.messageResponse ?? 1 }; },
      showSaveDialog: async () => (dialogOverrides.savePath ? { canceled: false, filePath: dialogOverrides.savePath } : { canceled: true }),
    },
    shell: { openExternal: async (url) => opened.push(url), openPath: async (target) => opened.push(target), showItemInFolder() {} },
    session: { fromPartition: (name) => profiles[name] },
    webContents: { fromId: (id) => contentsById.get(id) },
    getMainWindow: () => mainWindow,
    resolvePath: (target) => {
      if (!fs.existsSync(target)) throw new Error(`Path does not exist: ${target}`);
      return target;
    },
    store,
    quarantineDir: path.join(directory, "quarantine"),
    platform: "linux",
    chromeVersion: "156.0.8078.49",
  });
  browsers.install();
  if (policy) browsers.setPolicy(null, policy);
  const event = { sender: owner };
  function open(options, prepare = () => {}) {
    const state = browsers.createBrowser(event, options);
    const contents = new FakeContents({ host: owner, profile: profiles[state.partition] });
    // A webview has already started loading its src when the studio adopts it.
    if (state.src !== "about:blank") {
      contents.url = state.src;
      contents.entries = [{ url: state.src, title: "" }];
    }
    contentsById.set(contents.id, contents);
    prepare(contents);
    browsers.browserAction(event, { id: state.id, action: "adopt", webContentsId: contents.id });
    return { state, contents, id: state.id };
  }
  function openRem(threadOwner = "agent-0123456789") {
    const session = browsers.openAgentTab({ agent: threadOwner, label: "Rem" });
    const contents = new FakeContents({ host: owner, profile: profiles[PARTITIONS.rem] });
    contentsById.set(contents.id, contents);
    browsers.browserAction(event, { id: session.id, action: "adopt", webContentsId: contents.id });
    return { session, contents, id: session.id };
  }
  const sent = (channel) => owner.sent.filter(([name]) => name === channel).map(([, payload]) => payload);
  return { app, browsers, profiles, owner, event, open, openRem, sent, store, directory, downloads, opened, contentsById, messages };
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

test("tabs, previews and Rem use separate profiles", () => {
  const { browsers, open, openRem, profiles, directory, owner, event, contentsById } = harness();
  const web = open({ url: "example.com" });
  assert.equal(web.state.partition, PARTITIONS.user);
  assert.equal(web.state.src, "https://example.com/");
  const file = path.join(directory, "report.html");
  fs.writeFileSync(file, "<h1>Report</h1>");
  assert.equal(open({ path: file }).state.partition, PARTITIONS.preview);
  assert.equal(open({ url: file }).state.partition, PARTITIONS.preview);
  const rem = openRem();
  assert.equal(rem.contents.session, profiles[PARTITIONS.rem]);
  assert.equal(profiles[PARTITIONS.user].userAgent.includes("Electron"), false);
  // A webview may only attach with the profile its session was created for.
  const pending = browsers.createBrowser(event, { url: "https://example.org/" });
  assert.equal(browsers.isPendingAttach(owner.id, { src: "https://example.org/", partition: PARTITIONS.rem }), false);
  assert.equal(browsers.isPendingAttach(owner.id, { src: "https://example.org/", partition: PARTITIONS.user }), true);
  assert.equal(browsers.isPendingAttach(owner.id, { src: "about:blank", partition: "persist:other" }), false);
  const wrong = new FakeContents({ host: owner, profile: profiles[PARTITIONS.rem] });
  contentsById.set(wrong.id, wrong);
  assert.throws(() => browsers.browserAction(event, { id: pending.id, action: "adopt", webContentsId: wrong.id }), /wrong profile/);
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
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/docs", background: false });
  handler({ url: "https://example.com/bg", disposition: "background-tab", features: "" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/bg", background: true });
  const popup = handler({ url: "https://accounts.test/oauth", disposition: "new-window", features: "width=500,height=600" });
  assert.equal(popup.action, "allow");
  assert.equal(popup.overrideBrowserWindowOptions.webPreferences.sandbox, true);
  assert.equal(popup.overrideBrowserWindowOptions.width, 500);
  // Allowing pop-ups for a site opens what was blocked and remembers it.
  browsers.browserAction(event, { id: web.id, action: "allow-popups" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://ads.test/", background: false });
  assert.equal(browsers.browserAction(event, { id: web.id, action: "site-info" }).popups, "allow");
});

test("page links open in the background for Rem and are rate limited", () => {
  const { openRem, open, sent } = harness();
  const rem = openRem();
  const ipc = (contents, url, disposition = "foreground") => contents.emit("ipc-message", { senderFrame: contents.mainFrame }, "gofer:browser-link-clicked", { url, disposition });
  rem.contents.input("mouseDown");
  ipc(rem.contents, "https://example.com/a");
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/a", background: true });
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

test("Rem tabs say when they use a shared sign-in", () => {
  const { browsers, openRem, sent } = harness({ policy: { remSignInSites: ["github.com"] } });
  const rem = openRem();
  void rem.contents.loadURL("https://github.com/settings");
  assert.equal(sent("gofer:browser-state").at(-1).remSignIn, true);
  void rem.contents.loadURL("https://example.com/");
  assert.equal(sent("gofer:browser-state").at(-1).remSignIn, false);
  void browsers;
});

test("restored tabs replay their saved history", async () => {
  const { open } = harness();
  const entries = [{ url: "https://a.test/", title: "A" }, { url: "https://b.test/", title: "B", pageState: "state" }, { url: "javascript:alert(1)" }];
  const tab = open({ url: "https://b.test/", history: { entries, index: 1 } });
  assert.equal(tab.state.src, "about:blank");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(tab.contents.restored, { entries: entries.slice(0, 2), index: 1 });
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
    assert.deepEqual(sent("gofer:browser-open-file").at(-1), { href });
  }
  preview.contents.input();
  ipc("https://example.com/docs");
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "https://example.com/docs", background: false });
  assert.deepEqual(preview.contents.openHandler({ url: "http://example.com/", disposition: "foreground-tab" }), { action: "deny" });
  assert.deepEqual(sent("gofer:browser-open-tab").at(-1), { url: "http://example.com/", background: false });
  for (const url of ["javascript:alert(1)", "data:text/html,hello"]) {
    assert.deepEqual(preview.contents.openHandler({ url }), { action: "deny" });
  }
  assert.equal(sent("gofer:browser-open-tab").length, 2);
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
