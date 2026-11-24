// Rem's connection to Raticode (gecko), against a stand-in for Firefox's
// WebDriver BiDi. gecko-rem-control.test.cjs drives a real Firefox.
require("./test-tmp.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { CLOSED_FOR_SAFETY, GLOBAL_EVENTS, NO_TAB_MESSAGE, OFF_MESSAGE, PRIVATE_ONLY_MESSAGE, SHARED_TAB_EVENTS, TAB_EVENTS, createGeckoControl, listeningAddress } = require("../gecko-control.cjs");

// tree: the top-level tabs Firefox reports; shown: which of them is in front;
// failOn: a request Firefox answers with an error; unreachable: the socket never opens.
function fakeBidi({ taken = false, tree = null, shown = [], failOn = "", unreachable = false } = {}) {
  const sent = [];
  let onEvent = () => {};
  let onClose = () => {};
  const connection = {
    opened: unreachable ? Promise.reject(new Error("Could not connect to Firefox: connection refused")) : Promise.resolve(),
    closed: false,
    sent,
    async send(method, params = {}) {
      sent.push([method, params]);
      if (method === failOn) throw Object.assign(new Error(`${method} failed`), { code: "unknown error" });
      if (method === "session.new" && taken) throw Object.assign(new Error("Maximum number of active sessions"), { code: "session not created" });
      if (method === "browser.createUserContext") return { userContext: "rem-container" };
      if (method === "browsingContext.create") return { context: `tab-${sent.length}` };
      if (method === "browsingContext.getTree") return { contexts: typeof tree === "function" ? tree() : tree || [] };
      if (method === "session.subscribe" && params.contexts) return { subscription: `sub-${params.contexts[0]}` };
      if (method === "script.evaluate") {
        const context = params.target?.context;
        return { result: { type: "array", value: [{ type: "string", value: shown.includes(context) ? "visible" : "hidden" }, { type: "string", value: `Title of ${context}` }] } };
      }
      if (method === "storage.getCookies") {
        return { cookies: [{ name: "sid", value: "1", domain: ".music.example", path: "/" }, { name: "x", value: "2", domain: "bank.example", path: "/" }] };
      }
      return {};
    },
    close() { connection.closed = true; },
    event: (method, params) => onEvent(method, params),
    drop: () => { connection.closed = true; onClose(); },
  };
  return {
    connection,
    // Each connect is a new socket to the same stand-in.
    connect: (url, options) => { connection.url = url; connection.closed = false; onEvent = options.onEvent; onClose = options.onClose; return connection; },
  };
}

function setup(options = {}) {
  const stagingDir = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-control-"));
  const bidi = fakeBidi(options);
  const control = createGeckoControl({ stagingDir, connectBidi: bidi.connect });
  let enabled = options.enabled ?? true;
  const starts = [];
  control.setHost({
    enabled: () => enabled,
    privateOnly: () => options.privateOnly === true,
    available: () => ({ available: true, executable: "/usr/bin/firefox", problem: "" }),
    start: async () => { starts.push(1); await control.attach("ws://127.0.0.1:1234"); },
  });
  return { bidi, control, starts, setEnabled: (value) => { enabled = value; }, done: () => fs.rmSync(stagingDir, { recursive: true, force: true }) };
}

test("Firefox's control address is read from its startup message", () => {
  assert.equal(listeningAddress("noise\nWebDriver BiDi listening on ws://127.0.0.1:40123\n"), "ws://127.0.0.1:40123");
  assert.equal(listeningAddress("WebDriver BiDi listening on ws://10.0.0.2:40123"), "");
  assert.equal(listeningAddress(""), "");
});

test("Rem can't use Gecko until the user turns it on", async (t) => {
  const context = setup({ enabled: false });
  t.after(context.done);
  await assert.rejects(context.control.createContext("rem"), (error) => error.message === OFF_MESSAGE);
  assert.equal(context.starts.length, 0);
  assert.equal(context.control.status().available, false);
  assert.match(OFF_MESSAGE, /Let Rem use Raticode \(gecko\)/);
});

test("the session is claimed at once, Rem's container starts signed out, and its downloads are refused", async (t) => {
  const context = setup();
  t.after(context.done);
  await context.control.ensure();
  const methods = context.bidi.connection.sent.map(([method]) => method);
  assert.deepEqual(methods.slice(0, 6), ["session.new", "session.subscribe", "browsingContext.getTree", "browser.createUserContext", "storage.deleteCookies", "browser.setDownloadBehavior"]);
  const sent = Object.fromEntries(context.bidi.connection.sent);
  // Only tab creation is watched for every tab; nothing about the user's own tabs.
  assert.deepEqual(sent["session.subscribe"], { events: GLOBAL_EVENTS });
  assert.deepEqual(sent["storage.deleteCookies"], { partition: { type: "storageKey", userContext: "rem-container" } });
  assert.deepEqual(sent["browser.setDownloadBehavior"], { downloadBehavior: { type: "denied" }, userContexts: ["rem-container"] });
  assert.equal(context.control.status().running, true);
});

test("a control port someone else claimed first closes Gecko instead of being used", async (t) => {
  const context = setup({ taken: true });
  t.after(context.done);
  const killed = [];
  await assert.rejects(context.control.attach("ws://127.0.0.1:1", { kill: () => killed.push(1) }), /Another program on this computer took control/);
  assert.equal(killed.length, 1);
  assert.equal(context.control.status().running, false);
});

// Every step after the socket opens, in the order attach takes them.
const ATTACH_STEPS = ["session.new", "session.subscribe", "browsingContext.getTree", "browser.createUserContext", "storage.deleteCookies", "browser.setDownloadBehavior"];

test("a connection that fails at any step closes Gecko, so its control port is never left open without Raticode", async (t) => {
  for (const failOn of ["", ...ATTACH_STEPS]) {
    const context = setup({ failOn, unreachable: !failOn });
    t.after(context.done);
    const killed = [];
    const exits = [];
    context.control.events.on("exit", (details) => exits.push(details));
    await assert.rejects(context.control.attach("ws://127.0.0.1:1", { kill: (reason) => killed.push(reason) }), failOn ? new RegExp(`${failOn} failed`) : /connection refused/);
    assert.deepEqual(killed, [undefined], `Gecko is closed once when ${failOn || "the socket"} fails`);
    assert.equal(context.bidi.connection.closed, true, `the socket is closed when ${failOn || "the socket"} fails`);
    if (failOn) assert.equal(context.bidi.connection.sent.at(-1)[0], failOn, "nothing is sent after the failing step");
    assert.equal(context.control.status().running, false);
    assert.equal(context.control.remUserContext, "");
    assert.deepEqual(exits, [], "closing the failed socket isn't taken for a dropped connection");
    await assert.rejects(context.control.send("browsingContext.getTree", {}), /not running/);
  }
});

test("session.subscribe failing right after session.new succeeds closes Gecko", async (t) => {
  const context = setup({ failOn: "session.subscribe" });
  t.after(context.done);
  const killed = [];
  await assert.rejects(context.control.attach("ws://127.0.0.1:1", { kill: () => killed.push(1) }), /session\.subscribe failed/);
  assert.deepEqual(context.bidi.connection.sent.map(([method]) => method), ["session.new", "session.subscribe"]);
  assert.equal(killed.length, 1);
});

test("a connection that drops after Rem has it closes Gecko; one Raticode ends itself doesn't", async (t) => {
  const context = setup();
  t.after(context.done);
  const killed = [];
  const kill = (reason) => killed.push(reason);

  await context.control.attach("ws://127.0.0.1:1", { kill });
  assert.equal(context.control.status().running, true);
  assert.deepEqual(killed, []);
  context.bidi.connection.drop();
  assert.equal(killed.length, 1, "a dropped connection closes Gecko");
  assert.match(killed[0], /closed unexpectedly/);
  assert.ok(killed[0].endsWith(CLOSED_FOR_SAFETY));
  assert.equal(context.control.status().running, false);
  context.bidi.connection.drop();
  assert.equal(killed.length, 1, "only once");

  // Raticode turning Rem's use of Gecko off, or stopping Gecko itself.
  await context.control.attach("ws://127.0.0.1:1", { kill });
  context.control.detach({ expected: true });
  context.bidi.connection.drop();
  assert.equal(killed.length, 1, "an expected detach leaves Gecko to Raticode");

  // Firefox already exited.
  await context.control.attach("ws://127.0.0.1:1", { kill });
  context.control.detach({ stopped: true });
  assert.equal(killed.length, 1, "a Firefox that already exited isn't closed again");

  // A new Gecko replaces the connection: the old Firefox is Raticode's to stop.
  await context.control.attach("ws://127.0.0.1:1", { kill });
  await context.control.attach("ws://127.0.0.1:2", { kill: () => killed.push("second") });
  assert.equal(killed.length, 1, "replacing the connection doesn't close the old Gecko");
  context.bidi.connection.drop();
  assert.equal(killed[1], "second", "the drop closes the Gecko it belongs to");
});

test("Rem's tabs are background tabs in its own container, and only they report events", async (t) => {
  const context = setup();
  t.after(context.done);
  const { context: tab } = await context.control.createContext("rem");
  const sent = context.bidi.connection.sent;
  assert.deepEqual(sent.find(([method]) => method === "browsingContext.create")[1], { type: "tab", background: true, userContext: "rem-container" });
  assert.deepEqual(sent.findLast(([method]) => method === "session.subscribe")[1], { events: TAB_EVENTS, contexts: [tab] });
  await assert.rejects(context.control.createContext("user"), /Only Rem's tabs/);
  const seen = [];
  const downloads = [];
  context.control.register(tab, { handleEvent: (method, params) => seen.push([method, params.context]) });
  context.control.events.on("browsingContext.downloadEnd", (params) => downloads.push(params.context));
  context.bidi.connection.event("browsingContext.load", { context: tab });
  context.bidi.connection.event("browsingContext.load", { context: "users-tab" });
  // The user's own downloads stay Firefox's: Raticode must never see or remove them.
  context.bidi.connection.event("browsingContext.downloadEnd", { context: "users-tab", filepath: "/home/u/Downloads/a.pdf" });
  context.bidi.connection.event("browsingContext.downloadEnd", { context: tab });
  context.bidi.connection.event("browsingContext.contextCreated", { context: "popup", originalOpener: tab });
  assert.deepEqual(seen, [["browsingContext.load", tab], ["browsingContext.downloadEnd", tab], ["popup", "popup"]]);
  assert.deepEqual(downloads, [tab]);
});

test("sign-ins are shared only for chosen sites, and only Rem's cookies are ever cleared", async (t) => {
  const context = setup();
  t.after(context.done);
  await context.control.ensure();
  await context.control.mirrorSignIns(["music.example"]);
  const copies = context.bidi.connection.sent.filter(([method]) => method === "storage.setCookie");
  assert.deepEqual(copies.map(([, params]) => [params.cookie.domain, params.partition.userContext]), [[".music.example", "rem-container"]]);
  const before = context.bidi.connection.sent.length;
  assert.equal(await context.control.clearSiteCookies("user", ""), 0);
  assert.equal(context.bidi.connection.sent.length, before);
  assert.equal(await context.control.clearSiteCookies("rem", ""), -1);
  assert.deepEqual(context.bidi.connection.sent.at(-1), ["storage.deleteCookies", { partition: { type: "storageKey", userContext: "rem-container" } }]);
});

test("allowing Rem's downloads sends them to Raticode's staging folder; Gecko stopping ends Rem's tabs", async (t) => {
  const context = setup();
  t.after(context.done);
  await context.control.ensure();
  await context.control.setRemDownloads(true);
  assert.deepEqual(context.bidi.connection.sent.at(-1)[1].downloadBehavior, { type: "allowed", destinationFolder: context.control.stagingDir });
  const { context: tab } = await context.control.createContext("rem");
  context.control.register(tab, { handleEvent() {} });
  const exits = [];
  context.control.events.on("exit", (details) => exits.push(details));
  context.bidi.connection.drop();
  assert.deepEqual(exits, [{ expected: false }]);
  assert.equal(context.control.status().running, false);
  await assert.rejects(context.control.send("script.evaluate", {}), /not running/);
});

// Gecko started with one regular window; the user later opened a private one.
// Firefox reports both windows' tabs the same way, with no private flag.
function startedTree() {
  const tabs = [
    { context: "music", url: "https://music.example/watch", userContext: "default", clientWindow: "main-window", parent: null },
    { context: "mail", url: "https://mail.example/", userContext: "default", clientWindow: "main-window", parent: null },
  ];
  let late = [];
  return {
    tree: () => [...tabs, ...late],
    openPrivateWindow: () => { late = [{ context: "secret", url: "https://bank.example/", userContext: "default", clientWindow: "private-window", parent: null }]; },
  };
}

test("Rem's tabs open in Gecko's main window, never in a private window opened later", async (t) => {
  const windows = startedTree();
  const context = setup({ tree: windows.tree });
  t.after(context.done);
  await context.control.ensure();
  windows.openPrivateWindow();
  await context.control.createContext("rem");
  const create = context.bidi.connection.sent.find(([method]) => method === "browsingContext.create")[1];
  assert.deepEqual(create, { type: "tab", background: true, userContext: "rem-container", referenceContext: "mail" });
});

test("only the tab in front of Gecko's main window can be shared, never a private window's", async (t) => {
  const windows = startedTree();
  const context = setup({ tree: windows.tree, shown: ["mail", "secret"] });
  t.after(context.done);
  await context.control.ensure();
  windows.openPrivateWindow();
  assert.deepEqual(await context.control.shareableTab(), { context: "mail", url: "https://mail.example/", title: "Title of mail" });
  // A private window's tab can't be adopted, even by asking for it directly.
  await assert.rejects(context.control.adoptContext("secret"), (error) => error.message === NO_TAB_MESSAGE);
  assert.equal(context.bidi.connection.sent.some(([method, params]) => method === "session.subscribe" && params.contexts?.includes("secret")), false);
});

test("a shared tab gets page events but keeps its downloads, pop-ups and file pickers; releasing it leaves it open", async (t) => {
  const windows = startedTree();
  const context = setup({ tree: windows.tree, shown: ["music"] });
  t.after(context.done);
  await context.control.ensure();
  const found = await context.control.shareableTab();
  assert.equal(found.context, "music");
  await context.control.adoptContext("music");
  assert.deepEqual(context.bidi.connection.sent.at(-1), ["session.subscribe", { events: SHARED_TAB_EVENTS, contexts: ["music"] }]);
  assert.equal(SHARED_TAB_EVENTS.includes("input.fileDialogOpened"), false);
  assert.equal(SHARED_TAB_EVENTS.some((name) => name.startsWith("browsingContext.download")), false);
  assert.equal(SHARED_TAB_EVENTS.includes("browsingContext.userPromptOpened"), true);
  const seen = [];
  const downloads = [];
  context.control.register("music", { handleEvent: (method, params) => seen.push([method, params.context]) });
  context.control.events.on("browsingContext.downloadWillBegin", (params) => downloads.push(params.context));
  context.bidi.connection.event("browsingContext.contextCreated", { context: "sign-in-popup", originalOpener: "music" });
  context.bidi.connection.event("browsingContext.downloadWillBegin", { context: "music" });
  context.bidi.connection.event("browsingContext.load", { context: "music" });
  assert.deepEqual(seen, [["browsingContext.downloadWillBegin", "music"], ["browsingContext.load", "music"]]);
  assert.deepEqual(downloads, []);
  // A tab Rem is already using isn't offered again; the only other one is.
  assert.equal((await context.control.shareableTab()).context, "mail");
  context.control.unregister("music");
  context.control.release("music");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(context.bidi.connection.sent.at(-1), ["session.unsubscribe", { subscriptions: ["sub-music"] }]);
  assert.equal(context.bidi.connection.sent.some(([method, params]) => method === "browsingContext.close" && params.context === "music"), false);
});

test("sharing asks which tab when none is in front, and refuses a profile that always browses privately", async (t) => {
  const windows = startedTree();
  const context = setup({ tree: windows.tree, shown: [] });
  t.after(context.done);
  await assert.rejects(context.control.shareableTab(), (error) => error.message === NO_TAB_MESSAGE);
  const privateProfile = setup({ tree: windows.tree, shown: ["music"], privateOnly: true });
  t.after(privateProfile.done);
  await assert.rejects(privateProfile.control.shareableTab(), (error) => error.message === PRIVATE_ONLY_MESSAGE);
  await assert.rejects(privateProfile.control.adoptContext("music"), (error) => error.message === PRIVATE_ONLY_MESSAGE);
});

test("Gecko's first window is learned from its first tab when it opens after Raticode connects", async (t) => {
  let tabs = [];
  const context = setup({ tree: () => tabs, shown: ["first"] });
  t.after(context.done);
  await context.control.ensure();
  tabs = [{ context: "first", url: "https://first.example/", userContext: "default", clientWindow: "late-window", parent: null }];
  context.bidi.connection.event("browsingContext.contextCreated", tabs[0]);
  tabs.push({ context: "later", url: "https://later.example/", userContext: "default", clientWindow: "other-window", parent: null });
  context.bidi.connection.event("browsingContext.contextCreated", tabs[1]);
  assert.equal((await context.control.shareableTab()).context, "first");
  await assert.rejects(context.control.adoptContext("later"), (error) => error.message === NO_TAB_MESSAGE);
});
