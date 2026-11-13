require("./test-tmp.cjs");
// Firefox mode: the engine that runs a headless Firefox, the tab adapter the
// integrated browser drives, and Rem's tools on top of it. The last group
// starts a real Firefox without a window and is skipped when none is installed.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const {
  PROFILE_PREFS,
  createFirefoxEngine,
  findFirefox,
  launchEnvironment,
  profileDirFor,
  writeProfile,
} = require("../firefox-engine.cjs");
const {
  FirefoxContents,
  clampView,
  createDebuggerShim,
  createHistoryTracker,
  fromRemoteValue,
  webdriverKey,
} = require("../firefox-contents.cjs");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { remSiteAllowed, sanitizeBrowserPolicy } = require("../browser-policy.cjs");

const FIREFOX = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const realFirefox = { skip: FIREFOX ? false : "Firefox is not installed" };

test("named keys map to WebDriver key codes and printable keys pass through", () => {
  assert.equal(webdriverKey("Enter"), "\uE007");
  assert.equal(webdriverKey("Backspace"), "\uE003");
  assert.equal(webdriverKey("ArrowDown"), "\uE015");
  assert.equal(webdriverKey("Control"), "\uE009");
  assert.equal(webdriverKey("Meta"), "\uE03D");
  assert.equal(webdriverKey("a"), "a");
  assert.equal(webdriverKey("É"), "É");
  assert.equal(webdriverKey(" "), " ");
  assert.equal(webdriverKey("Unidentified"), "");
  assert.equal(webdriverKey("Dead"), "");
});

test("remote values become the plain values executeJavaScript returns", () => {
  const value = fromRemoteValue({
    type: "object",
    value: [
      ["text", { type: "string", value: "hi" }],
      ["count", { type: "number", value: 2 }],
      ["nan", { type: "number", value: "NaN" }],
      ["list", { type: "array", value: [{ type: "boolean", value: true }, { type: "null" }, { type: "undefined" }] }],
      ["node", { type: "node", sharedId: "x" }],
    ],
  });
  assert.equal(value.text, "hi");
  assert.equal(value.count, 2);
  assert.ok(Number.isNaN(value.nan));
  assert.deepEqual(value.list, [true, null, undefined]);
  assert.equal(value.node, null);
  assert.equal(fromRemoteValue(undefined), undefined);
});

test("views below a usable size or with bad scale are clamped", () => {
  assert.equal(clampView(null), null);
  assert.equal(clampView({ width: 10, height: 600 }), null);
  assert.deepEqual(clampView({ width: 900.4, height: 600.6, scale: 2 }), { width: 900, height: 601, scale: 2 });
  assert.deepEqual(clampView({ width: 9000, height: 600, scale: 99 }), { width: 4096, height: 600, scale: 1 });
});

test("Raticode tracks a Firefox tab's history across sites", () => {
  const history = createHistoryTracker();
  history.commit("https://a.example/", { kind: "navigate", historyLength: 1 });
  history.commit("https://b.example/", { kind: "navigate", historyLength: 2 });
  history.commit("https://c.example/", { kind: "navigate", historyLength: 3 });
  assert.equal(history.canGoBack(), true);
  assert.equal(history.canGoForward(), false);
  history.traverse(-1);
  history.commit("https://b.example/", { kind: "back_forward" });
  assert.equal(history.index(), 1);
  assert.equal(history.canGoForward(), true);
  history.commit("https://b.example/?q", { kind: "replace" });
  assert.equal(history.length(), 3);
  history.commit("https://d.example/", { kind: "navigate", historyLength: 3 });
  assert.deepEqual(history.entries().map((entry) => entry.url), ["https://a.example/", "https://b.example/?q", "https://d.example/"]);
  assert.equal(history.canGoForward(), false);
  // A back navigation the page started is found by address.
  history.commit("https://a.example/", { kind: "back_forward" });
  assert.equal(history.index(), 0);
  assert.equal(history.canGoBack(), false);
});

test("Firefox is found from the explicit path, PATH or the usual install folders", () => {
  const present = new Set(["/opt/custom/firefox", "/usr/bin/firefox"]);
  const exists = (candidate) => present.has(candidate);
  assert.equal(findFirefox({ platform: "linux", env: { RATICODE_FIREFOX_PATH: "/opt/custom/firefox", PATH: "" }, exists }), "/opt/custom/firefox");
  assert.equal(findFirefox({ platform: "linux", env: { PATH: "/nowhere" }, exists }), "/usr/bin/firefox");
  assert.equal(findFirefox({ platform: "linux", env: { PATH: "" }, exists: () => false }), "");
  assert.equal(
    findFirefox({ platform: "darwin", env: {}, home: "/Users/a", exists: (item) => item === "/Applications/Firefox.app/Contents/MacOS/firefox" }),
    "/Applications/Firefox.app/Contents/MacOS/firefox",
  );
  assert.equal(
    findFirefox({ platform: "win32", env: { ProgramFiles: "C:\\Program Files" }, exists: (item) => /Mozilla Firefox/.test(item) }),
    path.join("C:\\Program Files", "Mozilla Firefox", "firefox.exe"),
  );
});

test("Firefox launches without a display, crash reporter or system access", () => {
  const env = launchEnvironment({ DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-0", MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: "1", HOME: "/home/a" }, "linux");
  assert.equal(env.DISPLAY, undefined);
  assert.equal(env.WAYLAND_DISPLAY, undefined);
  assert.equal(env.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS, undefined);
  assert.equal(env.MOZ_HEADLESS, "1");
  assert.equal(env.MOZ_CRASHREPORTER_DISABLE, "1");
  assert.equal(env.HOME, "/home/a");
});

test("the Raticode Firefox profile is private and keeps Firefox's protections", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-profile-test-"));
  const profile = path.join(directory, "firefox-profile");
  fs.mkdirSync(profile, { mode: 0o755 });
  fs.writeFileSync(path.join(profile, "WebDriverBiDiServer.json"), "{}");
  writeProfile(profile);
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(profile).mode & 0o777, 0o700);
    assert.equal(fs.statSync(path.join(profile, "user.js")).mode & 0o777, 0o600);
  }
  assert.equal(fs.existsSync(path.join(profile, "WebDriverBiDiServer.json")), false);
  const userJs = fs.readFileSync(path.join(profile, "user.js"), "utf8");
  assert.match(userJs, /user_pref\("remote\.prefs\.recommended", false\);/);
  assert.match(userJs, /user_pref\("permissions\.default\.geo", 2\);/);
  // The remote agent's test defaults would switch these off; they stay on.
  for (const pref of ["browser.safebrowsing.malware.enabled", "privacy.trackingprotection.enabled", "dom.disable_open_during_load", "security.fileuri.strict_origin_policy"]) {
    assert.equal(Object.hasOwn(PROFILE_PREFS, pref), false, pref);
  }
  assert.equal(profileDirFor("/usr/bin/firefox", "/data"), path.join("/data", "firefox-profile"));
  assert.equal(profileDirFor("/snap/bin/firefox", "/data", "/home/a"), "/home/a/snap/firefox/common/raticode-profile");
});

test("a missing Firefox fails with an answer the user can act on", async () => {
  const engine = createFirefoxEngine({ dataDir: os.tmpdir(), find: () => "" });
  assert.equal(engine.status().available, false);
  await assert.rejects(engine.ensure(), /Firefox is not installed/);
});

test("a Firefox whose control session another program took is closed, not used", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-takeover-"));
  const killed = [];
  const sent = [];
  const logs = [];
  const spawnFake = () => {
    const child = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = (signal) => { killed.push(signal); setImmediate(() => child.emit("exit", null, signal)); return true; };
    setImmediate(() => child.stderr.emit("data", "WebDriver BiDi listening on ws://127.0.0.1:45678\n"));
    return child;
  };
  // Firefox answers session.new this way when a session already exists.
  class TakenSocket {
    constructor(url) { this.url = url; setImmediate(() => this.onopen?.()); }
    send(text) {
      const { id, method } = JSON.parse(text);
      sent.push(method);
      setImmediate(() => this.onmessage?.({ data: JSON.stringify({ type: "error", id, error: "session not created", message: "Maximum number of active sessions" }) }));
    }
    close() { setImmediate(() => this.onclose?.()); }
  }
  try {
    const engine = createFirefoxEngine({
      dataDir, find: () => "/usr/bin/firefox-fake", spawn: spawnFake, WebSocketImpl: TakenSocket,
      watchdog: false, log: (level, message) => logs.push([level, message]),
    });
    await assert.rejects(engine.ensure(), /Another program on this computer took control of Firefox/);
    assert.deepEqual(sent, ["session.new"]);
    assert.equal(killed[0], "SIGKILL");
    assert.ok(logs.some(([level, message]) => level === "warn" && /took control of Firefox/.test(message)));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("Rem's DevTools commands map to WebDriver BiDi", async () => {
  const calls = [];
  const contents = Object.assign(new EventEmitter(), {
    context: "ctx",
    pageZoom: 2,
    destroyed: false,
    alive: async () => {},
    command: async (method, params) => {
      calls.push([method, params]);
      if (method === "script.evaluate") {
        if (params.expression === "boom") return { type: "exception", exceptionDetails: { text: "ReferenceError: boom" } };
        return { type: "success", result: { type: "object", value: [["ok", { type: "boolean", value: true }]] } };
      }
      if (method === "browsingContext.locateNodes") return { nodes: [{ sharedId: "node-1" }] };
      return {};
    },
    setFiles: async (element, files) => calls.push(["setFiles", { element, files }]),
    answerDialog: async (accept, text) => calls.push(["answer", { accept, text }]),
  });
  const shim = createDebuggerShim(contents);
  shim.attach();
  assert.equal(shim.isAttached(), true);
  const evaluated = await shim.sendCommand("Runtime.evaluate", { expression: "x", awaitPromise: true, returnByValue: true, userGesture: true });
  assert.deepEqual(evaluated.result.value, { ok: true });
  assert.equal(calls[0][1].userActivation, true);
  assert.deepEqual(calls[0][1].target, { context: "ctx" });
  const failed = await shim.sendCommand("Runtime.evaluate", { expression: "boom" });
  assert.match(failed.exceptionDetails.text, /boom/);
  const search = await shim.sendCommand("DOM.performSearch", { query: "[data-raticode-upload=\"n\"]" });
  assert.equal(search.resultCount, 1);
  const { nodeIds } = await shim.sendCommand("DOM.getSearchResults", { searchId: search.searchId, fromIndex: 0, toIndex: 1 });
  await shim.sendCommand("DOM.setFileInputFiles", { nodeId: nodeIds[0], files: ["/tmp/a.txt"] });
  assert.deepEqual(calls.find(([name]) => name === "setFiles")[1], { element: "node-1", files: ["/tmp/a.txt"] });
  await shim.sendCommand("Page.handleJavaScriptDialog", { accept: false, promptText: "x" });
  assert.deepEqual(calls.find(([name]) => name === "answer")[1], { accept: false, text: "x" });
  const highlights = [];
  contents.on("firefox-highlight", (area) => highlights.push(area));
  await shim.sendCommand("Overlay.highlightRect", { x: 1, y: 2, width: 3, height: 4 });
  assert.deepEqual(highlights[0], { x: 2, y: 4, width: 6, height: 8 });
  await assert.rejects(shim.sendCommand("Network.enable"), /Firefox tabs do not support Network.enable/);
  let detached = false;
  shim.once("detach", () => { detached = true; });
  shim.detach();
  assert.equal(detached, true);
});

// ---------------------------------------------------------------------------
// Real Firefox, headless

const PAGE = `<!doctype html><title>Firefox test</title><body style="margin:0;height:3000px">
<input id=name aria-label=Name style="position:absolute;left:10px;top:10px;width:200px;height:30px">
<button id=press style="position:absolute;left:10px;top:60px;width:120px;height:40px" onclick="document.getElementById('out').textContent='clicked:'+event.isTrusted">Press</button>
<input id=file type=file aria-label=Upload style="position:absolute;left:10px;top:110px">
<button id=ask style="position:absolute;left:10px;top:150px;width:120px;height:40px" onclick="document.getElementById('out').textContent='confirm:'+confirm('Sure?')">Ask</button>
<select id=pick aria-label=Pick style="position:absolute;left:10px;top:200px;width:150px;height:30px"><option>one</option><option>two</option><option>three</option></select>
<a id=two href="/two" style="position:absolute;left:10px;top:250px">Two</a>
<a id=blocked href="BLOCKED" style="position:absolute;left:10px;top:280px">Blocked</a>
<div id=out style="position:absolute;left:10px;top:320px">none</div>
<script>window.addEventListener('scroll', () => document.title = 'scrolled:' + scrollY)</script></body>`;

async function startSite() {
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    if (request.url === "/two") { response.end("<title>Two</title><p>second</p>"); return; }
    const port = server.address().port;
    response.end(PAGE.replace("BLOCKED", `http://localhost:${port}/secret`));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

async function waitFor(check, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}

test("Rem drives Firefox with real input, keeps its site rules and asks for approval", realFirefox, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-rem-test-"));
  const engine = createFirefoxEngine({ dataDir, idleShutdownMs: 1000 });
  let policy = sanitizeBrowserPolicy({ engine: "firefox", remBlockedSites: ["localhost"], remConfirm: "always" });
  const tabs = [];
  const approvals = [];
  const controller = createAgentBrowserController({
    openTab: ({ agent, label }) => {
      const contents = new FirefoxContents({ engine, kind: "rem", nativeImage: fakeImage });
      contents.guardNavigation = true;
      const session = { id: `tab-${tabs.length}`, agent: { id: agent, label, shared: false }, contents, viewSize: null };
      // browser-sessions.cjs applies this same rule through will-navigate.
      contents.on("will-navigate", (event, url) => { if (!remSiteAllowed(url, policy)) event.preventDefault(); });
      contents.on("did-fail-load", (_event, _code, description) => { session.error = description; });
      tabs.push(session);
      return session;
    },
    agentTabs: (agent) => tabs.filter((item) => item.agent.id === agent && !item.contents.isDestroyed()),
    closeTab: (session) => session.contents.close(),
    normalizeUrl: (value) => value,
    policy: () => policy,
    confirm: async (_session, details) => { approvals.push(details.label); return true; },
  });
  const call = (action, extra = {}) => controller.handle({ agent: "agent-firefox-0001", action, ...extra });
  try {
    const opened = await call("open", { url: `${base}/` });
    assert.equal(opened.title, "Firefox test");
    const ref = (snapshot, name) => new RegExp(`"${name}"[^\\n]*\\[ref=(e\\d+)\\]`).exec(snapshot)[1];
    let { snapshot } = await call("snapshot");
    assert.match(snapshot, /textbox "Name"/);
    assert.match(snapshot, /\[file-upload\]/);
    const page = tabs[0].contents;
    const read = (expression) => page.evaluateIn("test", expression);

    await call("type", { ref: ref(snapshot, "Name"), text: "Ada" });
    assert.equal(await read("document.getElementById('name').value"), "Ada");
    await call("click", { ref: ref(snapshot, "Press") });
    assert.equal(await read("document.getElementById('out').textContent"), "clicked:true");

    const upload = path.join(dataDir, "notes.txt");
    fs.writeFileSync(upload, "hello");
    const uploaded = await controller.handle({ agent: "agent-firefox-0001", action: "upload", ref: ref(snapshot, "Upload"), paths: [upload], uploadRoots: [dataDir] });
    assert.deepEqual(uploaded.uploaded, ["notes.txt"]);
    assert.equal(await read("document.getElementById('file').files[0].name"), "notes.txt");
    await assert.rejects(controller.handle({ agent: "agent-firefox-0001", action: "upload", ref: ref(snapshot, "Upload"), paths: ["/etc/hostname"], uploadRoots: [dataDir] }), /outside this task's project folders|File not found/);

    const asked = await call("click", { ref: ref(snapshot, "Ask") });
    assert.deepEqual(asked.dialog, { type: "confirm", message: "Sure?" });
    await call("dialog", { accept: true });
    assert.equal(await read("document.getElementById('out').textContent"), "confirm:true");

    const evaluated = await call("evaluate", { expression: "navigator.userActivation.isActive" });
    assert.equal(evaluated.result, "true");
    assert.deepEqual(approvals, ["Upload notes.txt", "Run a script on this page"]);

    // Site rules: typed addresses and links the page follows.
    await assert.rejects(call("navigate", { url: "http://localhost/" }), /blocked Rem from localhost/);
    ({ snapshot } = await call("snapshot"));
    const blocked = await call("click", { ref: ref(snapshot, "Blocked") });
    assert.equal(new URL(blocked.url).hostname, "127.0.0.1");
    assert.ok(tabs[0].error, "the blocked navigation should report an error");

    const second = await call("click", { ref: ref(snapshot, "Two") });
    assert.equal(second.title, "Two");
    const back = await call("back");
    assert.equal(back.title, "Firefox test");
    const shot = await call("screenshot");
    assert.ok(shot.image.length > 1000);
    await call("close");
    assert.equal(page.isDestroyed(), true);
  } finally {
    engine.stop();
    server.close();
  }
});

test("a streamed Firefox tab sends frames and takes the user's mouse, wheel and keys", realFirefox, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-stream-test-"));
  const engine = createFirefoxEngine({ dataDir });
  const copied = [];
  const contents = new FirefoxContents({ engine, kind: "user", clipboard: { writeText: (text) => copied.push(text) } });
  const frames = [];
  const selects = [];
  const cursors = [];
  contents.on("firefox-frame", (frame) => {
    frames.push(frame);
    setTimeout(() => contents.ackFrame(frame.seq), 5);
  });
  contents.on("firefox-select", (details) => selects.push(details));
  contents.on("firefox-cursor", (details) => cursors.push(details.cursor));
  const read = (expression) => contents.evaluateIn("test", expression);
  try {
    await contents.loadURL(`${base}/`);
    assert.equal(contents.getTitle(), "Firefox test");
    contents.setView({ width: 800, height: 600, scale: 1 }, true);
    await waitFor(() => frames.length >= 1, "no frame arrived");
    assert.equal(frames[0].width, 800);
    assert.equal(frames[0].height, 600);
    assert.equal(frames[0].data[0], 0xff, "frames are JPEG");
    assert.equal(frames[0].data[1], 0xd8, "frames are JPEG");

    // Click into the text box and type, then paste text.
    contents.handleInput([{ t: "down", x: 50, y: 25, button: 0 }, { t: "up", x: 50, y: 25, button: 0 }]);
    for (const key of ["H", "i"]) contents.handleInput([{ t: "keydown", key }, { t: "keyup", key }]);
    contents.handleInput([{ t: "text", text: " there" }]);
    await waitFor(async () => (await read("document.getElementById('name').value")) === "Hi there", "typing did not reach the page");
    contents.handleInput([{ t: "keydown", key: "Backspace" }, { t: "keyup", key: "Backspace" }]);
    await waitFor(async () => (await read("document.getElementById('name').value")) === "Hi ther", "Backspace did not reach the page");

    // Select all and copy to the system clipboard.
    contents.handleInput([{ t: "keydown", key: "Control" }, { t: "keydown", key: "a" }, { t: "keyup", key: "a" }, { t: "keyup", key: "Control" }, { t: "copy" }]);
    await waitFor(() => copied.includes("Hi ther"), "copy did not reach the clipboard");

    // A trusted click on the button.
    contents.handleInput([{ t: "move", x: 60, y: 80 }, { t: "down", x: 60, y: 80, button: 0 }, { t: "up", x: 60, y: 80, button: 0 }]);
    await waitFor(async () => (await read("document.getElementById('out').textContent")) === "clicked:true", "click did not reach the page");
    await waitFor(() => cursors.length, "no cursor report");

    // A <select> opens the studio's list; choosing an option changes the page.
    contents.handleInput([{ t: "down", x: 50, y: 215, button: 0 }, { t: "up", x: 50, y: 215, button: 0 }]);
    await waitFor(() => selects.length, "no option list for the select");
    assert.deepEqual(selects[0].options.map((option) => option.label), ["one", "two", "three"]);
    contents.handleInput([{ t: "keydown", key: "Escape" }, { t: "keyup", key: "Escape" }, { t: "select-option", index: 2 }]);
    await waitFor(async () => (await read("document.getElementById('pick').value")) === "three", "the option was not chosen");

    // The wheel scrolls the page, and the stream follows.
    const before = frames.length;
    contents.handleInput([{ t: "wheel", x: 300, y: 300, dx: 0, dy: 400 }]);
    await waitFor(async () => (await read("scrollY")) > 0, "the wheel did not scroll");
    await waitFor(() => frames.length > before, "no new frame after scrolling");

    // Find in page counts matches and selects the first one.
    const found = new Promise((resolve) => contents.once("found-in-page", (_event, result) => resolve(result)));
    const requestId = contents.findInPage("press", { forward: true, findNext: true });
    const result = await found;
    assert.equal(result.requestId, requestId);
    assert.equal(result.matches, 1);
    assert.equal(result.activeMatchOrdinal, 1);
    // Firefox's Selection.toString() can still report a text field's old
    // selection; the selected range is what the stream shows.
    assert.equal(await read("getSelection().getRangeAt(0).toString()"), "Press");
    contents.stopFindInPage();

    // Page zoom lays the page out smaller.
    contents.setPageZoom(2);
    await waitFor(async () => (await read("innerWidth")) === 400, "page zoom did not change the layout width");

    // A hidden view stops sending frames.
    contents.setView(null, false);
    await new Promise((resolve) => setTimeout(resolve, 300));
    const stopped = frames.length;
    await contents.loadURL(`${base}/two`);
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(frames.length, stopped);
    assert.equal(contents.navigationHistory.canGoBack(), true);
  } finally {
    contents.close();
    engine.stop();
    server.close();
  }
});

test("Firefox's control port refuses web pages and second clients, and a dead Firefox restarts on reload", realFirefox, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-port-test-"));
  const engine = createFirefoxEngine({ dataDir });
  const contents = new FirefoxContents({ engine, kind: "user" });
  try {
    await contents.loadURL(`${base}/`);
    const profile = path.join(dataDir, "firefox-profile");
    const { ws_port: port } = JSON.parse(fs.readFileSync(path.join(profile, "WebDriverBiDiServer.json"), "utf8"));
    assert.ok(port > 0);
    const handshake = (headers) => new Promise((resolve) => {
      const socket = net.connect(port, "127.0.0.1", () => {
        socket.write(`GET /session HTTP/1.1\r\n${headers}Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      });
      socket.once("data", (data) => { resolve(String(data).split("\r\n")[0]); socket.destroy(); });
      socket.once("error", (error) => resolve(`error ${error.message}`));
    });
    assert.match(await handshake(`Host: 127.0.0.1:${port}\r\nOrigin: https://evil.example\r\n`), / 400 /);
    assert.match(await handshake(`Host: rebound.example:${port}\r\n`), / 400 /);
    // A local program can connect, but Raticode already holds Firefox's only session.
    const socket = new WebSocket(`ws://127.0.0.1:${port}/session`);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    const reply = new Promise((resolve) => { socket.onmessage = (message) => resolve(JSON.parse(message.data)); });
    socket.send(JSON.stringify({ id: 1, method: "session.new", params: { capabilities: {} } }));
    const refused = await reply;
    socket.close();
    assert.equal(refused.type, "error");
    assert.match(refused.message, /Maximum number of active sessions/);

    // Firefox stops: the tab reports it and starts Firefox again on reload.
    const gone = new Promise((resolve) => contents.once("render-process-gone", resolve));
    engine.events.once("exit", () => contents.markDead("Firefox stopped"));
    engine.stop();
    await gone;
    assert.equal(contents.dead, true);
    await assert.rejects(contents.loadURL(`${base}/two`), /Firefox stopped/);
    await contents.reloadPage(false);
    await waitFor(() => contents.getTitle() === "Firefox test" && !contents.isLoading(), "the tab did not come back after reload");
  } finally {
    contents.close();
    engine.stop();
    server.close();
  }
});

// Pages such as Open WebUI build a file in the page and click a link with a
// download attribute to it. Each download's start and end must carry the
// same key, or Raticode deletes the finished file (browser-sessions.cjs).
test("a real Firefox reports files a page builds (blob: and data: links) with matching start and end", realFirefox, async () => {
  const html = "<!doctype html><title>Chat</title><p>Exported chat</p>";
  const page = `<!doctype html><title>Downloads</title><script>
    const html = ${JSON.stringify(html)};
    function link(href, name) { const a = document.createElement("a"); a.href = href; a.download = name; return a; }
    window.cases = {
      fileSaver(name) { const a = link(URL.createObjectURL(new Blob([html], { type: "text/html" })), name); setTimeout(() => a.dispatchEvent(new MouseEvent("click")), 0); },
      revokeNow(name) { const url = URL.createObjectURL(new Blob([html], { type: "text/html" })); const a = link(url, name); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url); },
      dataUrl(name) { link("data:text/html," + encodeURIComponent(html), name).click(); },
      server(name) { link("/file.html", name).click(); },
    };</script>`;
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end(request.url === "/file.html" ? html : page);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-download-test-"));
  const engine = createFirefoxEngine({ dataDir });
  const contents = new FirefoxContents({ engine, kind: "user" });
  try {
    await contents.loadURL(`http://127.0.0.1:${server.address().port}/`);
    for (const [name, filename] of [["fileSaver", "file-saver.html"], ["revokeNow", "revoke-now.html"], ["dataUrl", "data-url.html"], ["server", "server.html"]]) {
      const began = new Promise((resolve) => contents.once("firefox-download-begin", resolve));
      const ended = new Promise((resolve) => contents.once("firefox-download-end", resolve));
      await contents.evaluateIn(null, `cases.${name}(${JSON.stringify(filename)}); true`, { userActivation: true });
      const [start, end] = await Promise.all([began, ended]);
      assert.equal(start.filename, filename);
      assert.ok(start.navigation, `${filename} has no key`);
      assert.equal(end.navigation, start.navigation, `${filename}'s start and end keys differ`);
      assert.equal(end.status, "complete", filename);
      assert.equal(fs.readFileSync(end.filepath, "utf8"), html, filename);
    }
    assert.equal(contents.isLoading(), false, "a download left the tab loading");
  } finally {
    contents.close();
    engine.stop();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("Firefox does not outlive a Raticode process that dies without quitting", realFirefox, async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "firefox-watchdog-test-"));
  const script = `
    const { createFirefoxEngine } = require(${JSON.stringify(path.join(__dirname, "..", "firefox-engine.cjs"))});
    const engine = createFirefoxEngine({ dataDir: ${JSON.stringify(dataDir)} });
    engine.ensure().then(() => console.log("ready"), (error) => { console.error(error); process.exit(1); });
  `;
  const parent = spawn(process.execPath, ["-e", script], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve, reject) => {
    parent.stdout.on("data", (data) => { if (String(data).includes("ready")) resolve(); });
    parent.once("exit", (code) => reject(new Error(`engine host exited early (${code})`)));
  });
  const profile = path.join(dataDir, "firefox-profile");
  const firefoxPids = () => {
    if (process.platform !== "linux") return [];
    return fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
      try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0").includes(profile); } catch { return false; }
    });
  };
  if (process.platform === "linux") assert.ok(firefoxPids().length >= 1, "Firefox should be running");
  parent.kill("SIGKILL");
  if (process.platform === "linux") await waitFor(() => firefoxPids().length === 0, "Firefox kept running after its host died", 15000);
});
