require("./test-tmp.cjs");
// The browser's right-click menu lives in browser-context-menu.cjs, the
// local preview's request guard in browser-preview-guard.cjs and downloads in
// browser-downloads.cjs. These tests run the menu and downloads directly, and
// keep browser-sessions.cjs from growing back past the size it had once all
// three moved out. browser-preview-guard.test.cjs tests the guard.

const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const { parse } = require("@babel/parser");
const { createBrowserContextMenu } = require("../browser-context-menu.cjs");
const { createPreviewGuard } = require("../browser-preview-guard.cjs");
const { createBrowserSessions } = require("../browser-sessions.cjs");
const { createBrowserDownloads } = require("../browser-downloads.cjs");
const { tempFolder } = require("./temp-folder.cjs");

const SESSIONS_FILE = path.join(__dirname, "..", "browser-sessions.cjs");
const MENU_FILE = path.join(__dirname, "..", "browser-context-menu.cjs");
const GUARD_FILE = path.join(__dirname, "..", "browser-preview-guard.cjs");
const DOWNLOADS_FILE = path.join(__dirname, "..", "browser-downloads.cjs");
// Measured right after downloads moved out.
const SESSIONS_MAX_LINES = 2632;
const CLOSURE_MAX_LINES = 2446;
const MOVED = [
  "copyText", "print", "showMessage", "chooseSavePath", "showContextMenu", "saveImageAs", "saveLinkAs",
  "startSaveAs", "takePendingSaveAs", "savePage", "saveAsPdf", "toggleBookmark", "withTimeout",
];
// What createBrowserContextMenu hands back to createBrowserSessions.
const RETURNED = ["print", "saveAsPdf", "savePage", "showContextMenu", "showMessage", "takePendingSaveAs", "toggleBookmark"];
const GUARD_MOVED = [
  "installPreviewRequestGuard", "previewForRequest", "previewRemoteRequestAllowed", "remoteBlockedCount", "previewFilePath",
  "remoteContentAllowed", "previewBelongsOnline", "setPreviewRemote", "previewMayRead", "previewFileAllowed",
];
// What createPreviewGuard hands back to createBrowserSessions.
const GUARD_RETURNED = ["installPreviewRequestGuard", "previewBelongsOnline", "previewMayRead", "remoteBlockedCount", "remoteContentAllowed", "setPreviewRemote"];
const DOWNLOADS_MOVED = [
  "forgetPrivateDownloads", "removeStrayQuarantine", "handleDownload", "finishedDownloadPath", "downloadApps",
  "openDownloadWith", "downloadAction", "emitDownloads", "firefoxDownloadBegan", "firefoxDownloadEnded",
  "remDownloadBlocked", "quarantinePathFor", "moveFile",
];
// What createBrowserDownloads hands back to createBrowserSessions.
const DOWNLOADS_RETURNED = [
  "downloadAction", "downloadApps", "firefoxDownloadBegan", "firefoxDownloadEnded", "forgetPrivateDownloads", "handleDownload", "removeStrayQuarantine",
];
const SESSION_MEMBERS = [
  "agentTabs", "allAgentTabs", "browserAction", "browserLibrary", "closeAgentTab", "closeAll", "closeForOwner",
  "confirmAgent", "createBrowser", "emitCommand", "firefoxStopped", "focusedSession",
  "geckoSharedAction", "geckoSharedTabs", "handleCertificateError", "handleLogin", "handleSelectClientCertificate",
  "install", "normalizeUrl", "openAgentTab", "ownerZoom", "policy",
  "recordAgentActivity", "returnViews", "reviveFirefoxTabs", "reviveStreamTabs", "setPolicy",
  "setViewportListener", "shareGeckoTab", "streamStopped",
];

const sessionsSource = fs.readFileSync(SESSIONS_FILE, "utf8");
const sessionsProgram = parse(sessionsSource, { sourceType: "script" }).program;

function lineCount(source) {
  return source.split("\n").length - (source.endsWith("\n") ? 1 : 0);
}

function walk(node, visit) {
  if (!node || typeof node.type !== "string") return;
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    if (Array.isArray(value)) for (const item of value) walk(item, visit);
    else if (value && typeof value.type === "string") walk(value, visit);
  }
}

// The names a declaration binds: { a, b: c, ...d } binds a, c and d.
function bindingNames(pattern) {
  if (!pattern) return [];
  if (pattern.type === "Identifier") return [pattern.name];
  if (pattern.type === "ObjectPattern") return pattern.properties.flatMap((property) => bindingNames(property.type === "RestElement" ? property.argument : property.value));
  if (pattern.type === "ArrayPattern") return pattern.elements.flatMap(bindingNames);
  if (pattern.type === "AssignmentPattern") return bindingNames(pattern.left);
  if (pattern.type === "RestElement") return bindingNames(pattern.argument);
  return [];
}

test("browser-sessions.cjs stays at most 2,632 lines", () => {
  const lines = lineCount(sessionsSource);
  assert.ok(lines <= SESSIONS_MAX_LINES, `browser-sessions.cjs is ${lines} lines, over ${SESSIONS_MAX_LINES}`);
});

test("createBrowserSessions() stays at most 2,446 lines", () => {
  const closure = sessionsProgram.body.find((node) => node.type === "FunctionDeclaration" && node.id.name === "createBrowserSessions");
  assert.ok(closure, "browser-sessions.cjs has no function createBrowserSessions");
  const lines = closure.loc.end.line - closure.loc.start.line + 1;
  assert.ok(lines <= CLOSURE_MAX_LINES, `createBrowserSessions() is ${lines} lines, over ${CLOSURE_MAX_LINES}`);
});

test("code that moved into browser-context-menu.cjs isn't declared in browser-sessions.cjs again", () => {
  const declared = [];
  const destructured = [];
  walk(sessionsProgram, (node) => {
    if ((node.type === "FunctionDeclaration" || node.type === "FunctionExpression") && node.id && MOVED.includes(node.id.name)) declared.push(`function ${node.id.name}`);
    if (node.type !== "VariableDeclarator") return;
    const names = bindingNames(node.id);
    const moved = names.filter((name) => MOVED.includes(name));
    if (!moved.length) return;
    // The only binding allowed is the one that takes them from the factory.
    const fromFactory = node.id.type === "ObjectPattern" && node.init?.type === "CallExpression" && node.init.callee.name === "createBrowserContextMenu";
    if (fromFactory) destructured.push(...moved);
    else declared.push(...moved.map((name) => `variable ${name}`));
  });
  assert.deepEqual(declared, [], `browser-sessions.cjs declares ${declared.join(", ")} again`);
  assert.deepEqual(destructured.sort(), RETURNED, "browser-sessions.cjs takes exactly the seven menu functions it calls from createBrowserContextMenu");
  for (const name of ["copyText", "chooseSavePath", "saveImageAs", "saveLinkAs", "startSaveAs", "withTimeout", "PDF_TIMEOUT_MS", "SAVE_AS_TIMEOUT_MS"]) {
    assert.doesNotMatch(sessionsSource, new RegExp(`\\b${name}\\b`), `browser-sessions.cjs uses ${name}, which only the menu module has`);
  }
  assert.equal(sessionsSource.split("createBrowserContextMenu(").length - 1, 1, "browser-sessions.cjs should call createBrowserContextMenu exactly once");
});

test("code that moved into browser-preview-guard.cjs isn't declared in browser-sessions.cjs again", () => {
  const declared = [];
  const destructured = [];
  walk(sessionsProgram, (node) => {
    if ((node.type === "FunctionDeclaration" || node.type === "FunctionExpression") && node.id && GUARD_MOVED.includes(node.id.name)) declared.push(`function ${node.id.name}`);
    if (node.type !== "VariableDeclarator") return;
    const moved = bindingNames(node.id).filter((name) => GUARD_MOVED.includes(name));
    if (!moved.length) return;
    const fromFactory = node.id.type === "ObjectPattern" && node.init?.type === "CallExpression" && node.init.callee.name === "createPreviewGuard";
    if (fromFactory) destructured.push(...moved);
    else declared.push(...moved.map((name) => `variable ${name}`));
  });
  assert.deepEqual(declared, [], `browser-sessions.cjs declares ${declared.join(", ")} again`);
  assert.deepEqual(destructured.sort(), GUARD_RETURNED, "browser-sessions.cjs takes exactly the six guard functions it calls from createPreviewGuard");
  for (const name of ["previewForRequest", "previewRemoteRequestAllowed", "previewFilePath", "previewFileAllowed", "PREVIEW_GUARDED_URLS"]) {
    assert.doesNotMatch(sessionsSource, new RegExp(`\\b${name}\\b`), `browser-sessions.cjs uses ${name}, which only the guard module has`);
  }
  assert.equal(sessionsSource.split("createPreviewGuard(").length - 1, 1, "browser-sessions.cjs should call createPreviewGuard exactly once");
  const source = fs.readFileSync(GUARD_FILE, "utf8");
  for (const name of GUARD_MOVED) assert.match(source, new RegExp(`^\\s*function ${name}\\(`, "m"), `browser-preview-guard.cjs doesn't declare ${name}`);
});

test("the preview guard hands back the six functions browser-sessions.cjs calls", () => {
  const guard = createPreviewGuard({ profile: () => null, sessions: new Map(), store: {}, localFilePath: () => "", resolvePreviewPath: () => { throw new Error("no grant"); } });
  assert.deepEqual(Object.keys(guard).sort(), GUARD_RETURNED);
});

test("browser-context-menu.cjs keeps no state and reads the policy only through getPolicy()", () => {
  const source = fs.readFileSync(MENU_FILE, "utf8");
  assert.doesNotMatch(source, /new (?:Weak)?(?:Map|Set)\b/, "the menu module must not keep a Map or Set");
  assert.doesNotMatch(source, /^let /m, "the menu module must not have a top-level let");
  assert.doesNotMatch(source.replace(/getPolicy\(\)/g, ""), /(?<![\w-])policy\.(?!cjs)/, "the menu reads the policy only through getPolicy()");
  for (const name of MOVED) assert.match(source, new RegExp(`^\\s*(?:async )?function ${name}\\(`, "m"), `browser-context-menu.cjs doesn't declare ${name}`);
});

test("createBrowserSessions still returns the same 30 members", () => {
  const browser = createBrowserSessions({ session: { fromPartition() { throw new Error("no profile in this test"); } }, getMainWindow: () => null, store: {} });
  assert.deepEqual(Object.keys(browser).sort(), SESSION_MEMBERS);
});

function contents({ url = "https://example.test/page", title = "Example", streamed = false } = {}) {
  return {
    engine: streamed ? "chrome" : "chromium",
    streamed,
    mainFrame: {},
    getURL: () => url,
    getTitle: () => title,
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack() {}, goForward() {} },
    editCommand: async () => {},
    copyImageAt() {},
    inspectElement() {},
  };
}

function menuHarness({ clipboardWrite = async () => {}, policy = { searchUrl: "https://search.test/?q={query}" } } = {}) {
  const menus = [];
  const copied = [];
  const logs = [];
  const opened = [];
  const commands = [];
  const states = [];
  const libraryChanges = [];
  const bookmarks = new Map();
  let nextId = 1;
  let currentPolicy = policy;
  const store = {
    bookmarkFor: (url) => [...bookmarks.values()].find((item) => item.url === url) || null,
    addBookmark: ({ url, title }) => { const item = { id: String(nextId++), url, title }; bookmarks.set(item.id, item); return item; },
    removeBookmark: (id) => bookmarks.delete(id),
  };
  const menu = createBrowserContextMenu({
    Menu: { buildFromTemplate: (template) => { menus.push(template); return { popup() {} }; } },
    app: { getPath: () => "/downloads" },
    clipboard: { writeText: (text) => { copied.push(text); return clipboardWrite(text); }, readText: () => "" },
    dialog: { showMessageBox: async () => ({ response: 1 }), showSaveDialog: async () => ({ canceled: true }) },
    shell: { openExternal: async (url) => opened.push(url), showItemInFolder() {} },
    getMainWindow: () => null,
    log: (level, message) => logs.push({ level, message }),
    store,
    getPolicy: () => currentPolicy,
    liveContents: (session) => session.contents,
    isStreamed: (item) => Boolean(item?.streamed),
    engineLabel: (name) => name,
    openPageLink: (session, url, options) => opened.push({ url, ...options }),
    reload() {},
    emitCommand: (session, action, details) => commands.push({ session, action, details }),
    emitState: (session) => states.push(session),
    emitLibraryChanged: (kind) => libraryChanges.push(kind),
  });
  const session = (options = {}) => ({ kind: "user", privateBrowsing: false, contextElement: null, contents: contents(options), ...options.session });
  const labels = (template) => template.filter((item) => item.label).map((item) => item.label);
  const item = (template, label) => template.find((entry) => entry.label === label);
  return {
    menu, menus, copied, logs, opened, commands, states, libraryChanges, bookmarks, session, labels, item,
    setPolicy: (value) => { currentPolicy = value; },
  };
}

test("the factory hands back the seven functions browser-sessions.cjs calls", () => {
  const { menu } = menuHarness();
  assert.deepEqual(Object.keys(menu).sort(), RETURNED);
  for (const name of RETURNED) assert.equal(typeof menu[name], "function", `${name} is not a function`);
});

test("Copy link address and Copy image address copy the address", async () => {
  const h = menuHarness();
  const tab = h.session();
  h.menu.showContextMenu(tab, { linkURL: "https://example.test/linked", mediaType: "image", srcURL: "https://example.test/cat.png" });
  const template = h.menus.at(-1);
  h.item(template, "Copy link address").click();
  h.item(template, "Copy image address").click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(h.copied, ["https://example.test/linked", "https://example.test/cat.png"]);
  assert.deepEqual(h.logs, []);
});

test("a failed copy is logged instead of thrown", async () => {
  const h = menuHarness({ clipboardWrite: async () => { throw new Error("clipboard is busy"); } });
  h.menu.showContextMenu(h.session(), { linkURL: "https://example.test/linked" });
  h.item(h.menus.at(-1), "Copy link address").click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(h.logs, [{ level: "warn", message: "Copy failed: clipboard is busy" }]);
});

test("toggleBookmark adds the page, then removes it, and tells the studio each time", () => {
  const h = menuHarness();
  const tab = h.session({ url: "https://example.test/read", title: "Reading" });
  assert.equal(h.menu.toggleBookmark(tab), true);
  assert.deepEqual([...h.bookmarks.values()].map(({ url, title }) => ({ url, title })), [{ url: "https://example.test/read", title: "Reading" }]);
  h.menu.showContextMenu(tab, {});
  assert.ok(h.item(h.menus.at(-1), "Remove bookmark"), "a bookmarked page offers Remove bookmark");
  assert.equal(h.menu.toggleBookmark(tab), false);
  assert.equal(h.bookmarks.size, 0);
  h.menu.showContextMenu(tab, {});
  h.item(h.menus.at(-1), "Bookmark this page").click();
  assert.equal(h.bookmarks.size, 1, "the menu item bookmarks the page");
  assert.deepEqual(h.libraryChanges, ["bookmarks", "bookmarks", "bookmarks"]);
  assert.deepEqual(h.states, [tab, tab, tab]);
});

test("toggleBookmark leaves previews and Rem's tabs alone", () => {
  const h = menuHarness();
  for (const kind of ["preview", "agent"]) {
    const tab = h.session({ session: { kind } });
    assert.equal(h.menu.toggleBookmark(tab), null);
    h.menu.showContextMenu(tab, {});
    assert.equal(h.item(h.menus.at(-1), "Bookmark this page"), undefined, `a ${kind} tab offers no bookmark`);
  }
  assert.equal(h.bookmarks.size, 0);
  assert.deepEqual(h.libraryChanges, []);
});

test("a private tab's menu has no Ask Rem about this", () => {
  const h = menuHarness();
  h.menu.showContextMenu(h.session(), { selectionText: "a secret" });
  assert.ok(h.labels(h.menus.at(-1)).includes("Ask Rem about this"), "a normal tab offers Ask Rem about this");
  h.menu.showContextMenu(h.session({ session: { privateBrowsing: true } }), { selectionText: "a secret" });
  assert.ok(!h.labels(h.menus.at(-1)).includes("Ask Rem about this"), "a private tab must not offer Ask Rem about this");
  assert.deepEqual(h.commands, []);
});

test("a mailto: link gets no Open link in default browser", () => {
  const h = menuHarness();
  h.menu.showContextMenu(h.session(), { linkURL: "https://example.test/linked" });
  assert.ok(h.labels(h.menus.at(-1)).includes("Open link in default browser"), "a web link offers the default browser");
  h.menu.showContextMenu(h.session(), { linkURL: "mailto:someone@example.test" });
  const labels = h.labels(h.menus.at(-1));
  for (const label of ["Open link in default browser", "Open link in new tab", "Save link as…", "Copy link address"]) {
    assert.ok(!labels.includes(label), `a mailto: link must not offer ${label}`);
  }
});

test("Search for the selection uses the policy in force when the menu opens", () => {
  const h = menuHarness();
  h.setPolicy({ searchUrl: "https://other.test/find?text={query}" });
  h.menu.showContextMenu(h.session(), { selectionText: "  two   words " });
  h.item(h.menus.at(-1), "Search for “two words”").click();
  assert.deepEqual(h.opened, [{ url: "https://other.test/find?text=two%20words", background: false }]);
});

test("code that moved into browser-downloads.cjs isn't declared in browser-sessions.cjs again", () => {
  const declared = [];
  const destructured = [];
  walk(sessionsProgram, (node) => {
    if ((node.type === "FunctionDeclaration" || node.type === "FunctionExpression") && node.id && DOWNLOADS_MOVED.includes(node.id.name)) declared.push(`function ${node.id.name}`);
    if (node.type !== "VariableDeclarator") return;
    const moved = bindingNames(node.id).filter((name) => DOWNLOADS_MOVED.includes(name) || ["downloads", "firefoxDownloads"].includes(name));
    if (!moved.length) return;
    const fromFactory = node.id.type === "ObjectPattern" && node.init?.type === "CallExpression" && node.init.callee.name === "createBrowserDownloads";
    if (fromFactory) destructured.push(...moved);
    else declared.push(...moved.map((name) => `variable ${name}`));
  });
  assert.deepEqual(declared, [], `browser-sessions.cjs declares ${declared.join(", ")} again`);
  assert.deepEqual(destructured.sort(), DOWNLOADS_RETURNED, "browser-sessions.cjs takes exactly the seven download functions it calls from createBrowserDownloads");
  for (const name of ["finishedDownloadPath", "openDownloadWith", "emitDownloads", "remDownloadBlocked", "quarantinePathFor", "moveFile", "firefoxDownloads"]) {
    assert.doesNotMatch(sessionsSource, new RegExp(`\\b${name}\\b`), `browser-sessions.cjs uses ${name}, which only the downloads module has`);
  }
  assert.equal(sessionsSource.split("createBrowserDownloads(").length - 1, 1, "browser-sessions.cjs should call createBrowserDownloads exactly once");
  const source = fs.readFileSync(DOWNLOADS_FILE, "utf8");
  for (const name of DOWNLOADS_MOVED) assert.match(source, new RegExp(`^\\s*(?:async )?function ${name}\\(`, "m"), `browser-downloads.cjs doesn't declare ${name}`);
  assert.doesNotMatch(source.replace(/getPolicy\(\)/g, ""), /(?<![\w-])policy\.(?!cjs)/, "the downloads module reads the policy only through getPolicy()");
});

test("the factory is called after the menu it takes takePendingSaveAs from", () => {
  const closure = sessionsProgram.body.find((node) => node.type === "FunctionDeclaration" && node.id.name === "createBrowserSessions");
  const callLine = (callee) => closure.body.body.find((node) => node.type === "VariableDeclaration" && node.declarations[0].init?.callee?.name === callee)?.loc.start.line;
  assert.ok(callLine("createBrowserContextMenu") < callLine("createBrowserDownloads"), "createBrowserDownloads must run after createBrowserContextMenu");
});

test("one Rem-download rule and one quarantine step serve both engines", () => {
  const electron = path.join(__dirname, "..");
  const all = fs.readdirSync(electron).filter((name) => name.endsWith(".cjs")).map((name) => fs.readFileSync(path.join(electron, name), "utf8")).join("\n");
  assert.equal(all.split('remDownloads !== "allow"').length - 1, 1, "the Rem-download rule is written once");
  const source = fs.readFileSync(DOWNLOADS_FILE, "utf8");
  assert.equal(source.split("${id}.download`").length - 1, 1, "the quarantine file name is made in one place");
  assert.equal(source.split("fs.mkdirSync(quarantineDir").length - 1, 1, "the quarantine folder is made in one place");
  assert.equal(source.split("remDownloadBlocked(").length - 1, 3, "both engines call remDownloadBlocked");
  assert.equal(source.split("quarantinePathFor(").length - 1, 3, "both engines call quarantinePathFor");
});

function downloadItem(filename) {
  return Object.assign(new EventEmitter(), {
    savePath: "",
    getFilename: () => filename,
    getURL: () => `https://files.test/${filename}`,
    getMimeType: () => "application/octet-stream",
    getTotalBytes: () => 2,
    getReceivedBytes: () => 2,
    isPaused: () => false,
    canResume: () => false,
    setSavePath(value) { this.savePath = value; },
    getSavePath() { return this.savePath; },
    setSaveDialogOptions(value) { this.dialog = value; },
    cancel() { this.cancelled = true; },
  });
}

function downloadsHarness(t, { policy = { remDownloads: "block", askWhereToSave: false } } = {}) {
  const root = tempFolder("raticode-downloads-", t);
  const downloadsDir = path.join(root, "Downloads");
  const quarantineDir = path.join(root, "quarantine");
  const staging = path.join(root, "staging");
  fs.mkdirSync(downloadsDir);
  fs.mkdirSync(staging);
  const listed = new Map();
  const activity = [];
  const sent = [];
  let currentPolicy = policy;
  const tabs = new Map();
  const store = {
    listDownloads: () => [...listed.values()].map((entry) => ({ ...entry })),
    upsertDownload: (entry) => listed.set(entry.id, { ...entry }),
    removeDownload: (id) => listed.delete(id),
    clearDownloads: () => { for (const [id, entry] of listed) if (entry.state !== "progressing" && entry.state !== "quarantined") listed.delete(id); },
    forgetPrivateDownloads: () => { for (const [id, entry] of listed) if (entry.private) listed.delete(id); },
  };
  const window = { isDestroyed: () => false, webContents: { send: (channel, payload) => sent.push({ channel, payload }) } };
  const downloads = createBrowserDownloads({
    getPolicy: () => currentPolicy,
    sessions: new Map(),
    store,
    app: { getPath: (name) => { assert.equal(name, "downloads"); return downloadsDir; } },
    shell: { openPath: async () => "", showItemInFolder() {} },
    getMainWindow: () => window,
    quarantineDir,
    recordAgentActivity: (session, entry) => activity.push({ session, entry }),
    sessionForContents: (contents) => tabs.get(contents) || null,
    liveContents: (session) => session.contents,
    takePendingSaveAs: () => "",
    engineLabel: (name) => name || "Firefox",
  });
  const tab = (session) => { const contents = {}; tabs.set(contents, session); return contents; };
  const staged = (name, body = "MZ") => { const file = path.join(staging, name); fs.writeFileSync(file, body); return file; };
  return {
    downloads, store, activity, sent, tab, staged, downloadsDir, quarantineDir,
    setPolicy: (value) => { currentPolicy = value; },
  };
}

test("Rem's Chromium and Firefox downloads follow the same rule, read from the policy in force", (t) => {
  const h = downloadsHarness(t);
  const remTab = { kind: "rem", agent: { id: "agent", shared: false } };
  let prevented = 0;
  h.downloads.handleDownload({ preventDefault: () => { prevented += 1; } }, downloadItem("tool.zip"), h.tab(remTab), "rem");
  h.downloads.firefoxDownloadBegan(remTab, { url: "https://files.test/notes.txt", filename: "notes.txt", navigation: "n1" });
  assert.equal(prevented, 1);
  assert.equal(remTab.lastBlockedDownload, "notes.txt");
  assert.deepEqual(h.activity.map(({ session, entry }) => [session === remTab, entry.action, entry.detail]), [
    [true, "download blocked", "tool.zip"],
    [true, "download blocked", "notes.txt"],
  ]);
  assert.deepEqual(h.store.listDownloads(), []);

  // A blocked Chromium download from Rem's profile with no tab is still refused.
  h.downloads.handleDownload({ preventDefault: () => { prevented += 1; } }, downloadItem("orphan.zip"), {}, "rem");
  assert.equal(prevented, 2);
  assert.equal(h.activity.length, 2, "no tab, nothing to record against");

  // A tab Rem shares with the user downloads as the user's.
  const shared = { kind: "user", agent: { id: "agent", shared: true } };
  h.downloads.firefoxDownloadBegan(shared, { url: "https://files.test/a.txt", filename: "a.txt", navigation: "n2" });
  assert.equal(h.store.listDownloads().length, 1);

  h.setPolicy({ remDownloads: "allow", askWhereToSave: false });
  const item = downloadItem("tool.zip");
  h.downloads.handleDownload({ preventDefault: () => { prevented += 1; } }, item, h.tab(remTab), "rem");
  h.downloads.firefoxDownloadBegan(remTab, { url: "https://files.test/notes.txt", filename: "notes.txt", navigation: "n3" });
  assert.equal(prevented, 2, "allowed once the policy changes");
  assert.equal(item.savePath, path.join(h.downloadsDir, "tool.zip"));
  assert.deepEqual(h.store.listDownloads().filter((entry) => entry.byRem).map((entry) => entry.filename).sort(), ["notes.txt", "tool.zip"]);
});

test("risky files from Chromium and Firefox wait in the same private quarantine folder", (t) => {
  const h = downloadsHarness(t);
  const user = { kind: "user" };
  const item = downloadItem("setup.exe");
  h.downloads.handleDownload({ preventDefault() {} }, item, h.tab(user), "user");
  const chromium = h.store.listDownloads()[0];
  assert.equal(item.savePath, path.join(h.quarantineDir, `${chromium.id}.download`));
  if (process.platform !== "win32") assert.equal(fs.statSync(h.quarantineDir).mode & 0o777, 0o700);

  fs.rmSync(h.quarantineDir, { recursive: true });
  h.downloads.firefoxDownloadBegan(user, { url: "https://files.test/run.sh", filename: "run.sh", navigation: "n1" });
  const file = h.staged("run.sh", "#!/bin/sh");
  h.downloads.firefoxDownloadEnded({ navigation: "n1", status: "complete", filepath: file }, user);
  const firefox = h.store.listDownloads().find((entry) => entry.filename === "run.sh");
  assert.equal(firefox.state, "quarantined");
  assert.equal(firefox.quarantinePath, path.join(h.quarantineDir, `${firefox.id}.download`));
  assert.equal(fs.readFileSync(firefox.quarantinePath, "utf8"), "#!/bin/sh");
  assert.equal(fs.existsSync(file), false);
  if (process.platform !== "win32") assert.equal(fs.statSync(h.quarantineDir).mode & 0o777, 0o700);
});

test("only Chromium checks running downloads' paths for a free name, and only Chromium marks private ones", (t) => {
  const h = downloadsHarness(t);
  const privateTab = { kind: "user", privateBrowsing: true };
  const first = downloadItem("report.pdf");
  const second = downloadItem("report.pdf");
  h.downloads.handleDownload({ preventDefault() {} }, first, h.tab(privateTab), "user");
  h.downloads.handleDownload({ preventDefault() {} }, second, h.tab(privateTab), "user");
  assert.equal(first.savePath, path.join(h.downloadsDir, "report.pdf"));
  assert.equal(second.savePath, path.join(h.downloadsDir, "report (1).pdf"), "the first download's path is taken before its file exists");
  assert.deepEqual(h.store.listDownloads().map((entry) => entry.private), [true, true]);

  h.downloads.firefoxDownloadBegan(privateTab, { url: "https://files.test/notes.txt", filename: "notes.txt", navigation: "n1" });
  h.downloads.firefoxDownloadEnded({ navigation: "n1", status: "complete", filepath: h.staged("notes.txt", "hi") }, privateTab);
  const firefox = h.store.listDownloads().find((entry) => entry.filename === "notes.txt");
  assert.equal(firefox.state, "completed");
  assert.equal(firefox.path, path.join(h.downloadsDir, "notes.txt"));
  assert.equal("private" in firefox, false, "Firefox entries carry no private flag");
  assert.ok(h.sent.every(({ channel }) => channel === "gofer:browser-downloads"));
});

test("the downloads factory hands back the seven functions browser-sessions.cjs calls", (t) => {
  assert.deepEqual(Object.keys(downloadsHarness(t).downloads).sort(), DOWNLOADS_RETURNED);
});
