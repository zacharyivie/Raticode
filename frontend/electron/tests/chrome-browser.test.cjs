require("./test-tmp.cjs");
// Chrome mode: the engine that runs a headless Google Chrome over a private
// DevTools pipe, the tab adapter the integrated browser drives, and Rem's
// tools on top of it. The real-Chrome group starts Chrome without a window
// and is skipped when no Chrome is found. RATICODE_CHROME_PATH picks the
// binary; without it the tests also accept a "Chrome for Testing" build that
// Playwright downloaded.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {
  CHROME,
  cookieParam,
  createChromiumEngine,
  createPipeConnection,
  findBrowser,
  headlessArgs,
  launchEnvironment,
  profileArgs,
} = require("../chromium-engine.cjs");
const {
  ChromiumContents,
  createDebuggerPassthrough,
  keyDefinition,
  macCommands,
  modifierBits,
  netErrorCode,
  visibleHistory,
} = require("../chromium-contents.cjs");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { remSiteAllowed, sanitizeBrowserPolicy } = require("../browser-policy.cjs");

function chromeForTesting() {
  const root = path.join(os.homedir(), ".cache", "ms-playwright");
  try {
    for (const entry of fs.readdirSync(root).filter((name) => /^chromium-\d+$/.test(name)).sort().reverse()) {
      for (const candidate of [path.join(root, entry, "chrome-linux64", "chrome"), path.join(root, entry, "chrome-linux", "chrome")]) {
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  } catch { /* No Playwright downloads. */ }
  return "";
}

const CHROME_PATH = process.env.RATICODE_SKIP_CHROME_TESTS === "1" ? "" : (findBrowser(CHROME) || (process.platform === "linux" ? chromeForTesting() : ""));
const realChrome = { skip: CHROME_PATH ? false : "Chrome is not installed" };
const engineOptions = (dataDir, extra = {}) => ({ dataDir, env: { ...process.env, RATICODE_CHROME_PATH: CHROME_PATH }, ...extra });

test("keys map to DevTools key codes and printable keys type themselves", () => {
  assert.deepEqual(keyDefinition("Enter"), { key: "Enter", code: "Enter", keyCode: 13, text: "\r" });
  assert.deepEqual(keyDefinition("Backspace"), { key: "Backspace", code: "Backspace", keyCode: 8, text: "" });
  assert.equal(keyDefinition("ArrowDown").keyCode, 40);
  assert.deepEqual(keyDefinition("a"), { key: "a", code: "KeyA", keyCode: 65, text: "a" });
  assert.deepEqual(keyDefinition("!"), { key: "!", code: "Digit1", keyCode: 49, text: "!" });
  assert.equal(keyDefinition("?").code, "Slash");
  assert.equal(keyDefinition("É").text, "É");
  assert.equal(keyDefinition(" ").text, " ");
  assert.equal(keyDefinition("Space").key, " ");
  assert.equal(keyDefinition("Unidentified"), null);
  assert.equal(modifierBits(["Control", "Shift"]), 10);
  assert.equal(modifierBits(["Alt", "Meta"]), 5);
  assert.deepEqual(macCommands("a", 4), ["selectAll"]);
  assert.deepEqual(macCommands("z", 12), ["redo"]);
  assert.deepEqual(macCommands("a", 2), []);
  assert.equal(netErrorCode("net::ERR_NAME_NOT_RESOLVED"), -105);
  // Certificate errors stay plain failures: Chrome shows its own warning page.
  assert.equal(netErrorCode("net::ERR_CERT_AUTHORITY_INVALID"), -2);
});

test("Chrome's history hides the blank page every tab starts on", () => {
  const history = visibleHistory({ currentIndex: 2, entries: [{ id: 1, url: "about:blank" }, { id: 2, url: "https://a.test/" }, { id: 3, url: "https://b.test/" }] });
  assert.deepEqual(history.entries.map((entry) => entry.url), ["https://a.test/", "https://b.test/"]);
  assert.equal(history.index, 1);
  assert.deepEqual(visibleHistory({ currentIndex: 0, entries: [{ id: 1, url: "about:blank" }] }).entries.map((entry) => entry.url), ["about:blank"]);
  assert.deepEqual(visibleHistory(null), { entries: [], index: 0 });
});

test("Chrome is found from the explicit path, PATH or the usual install folders", () => {
  const linux = CHROME.candidates({ platform: "linux", env: { PATH: "/custom/bin" }, home: "/home/u" });
  assert.deepEqual(linux.slice(0, 2), ["/custom/bin/google-chrome", "/custom/bin/google-chrome-stable"]);
  assert.ok(linux.includes("/opt/google/chrome/chrome"));
  assert.equal(CHROME.candidates({ platform: "linux", env: { RATICODE_CHROME_PATH: "/x/chrome" } })[0], "/x/chrome");
  assert.equal(CHROME.candidates({ platform: "darwin", env: {}, home: "/Users/u" })[0], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  const windows = CHROME.candidates({ platform: "win32", env: { ProgramFiles: "C:\\Program Files", LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" } });
  assert.deepEqual(windows, ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Users\\u\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"]);
  assert.equal(findBrowser(CHROME, { platform: "linux", env: { PATH: "" }, exists: (file) => file === "/usr/bin/google-chrome" }), "/usr/bin/google-chrome");
  assert.equal(findBrowser(CHROME, { platform: "linux", env: { PATH: "" }, exists: () => false }), "");
});

test("Chrome runs without a display, a network control port or the desktop keyring", () => {
  const args = headlessArgs("/data/chrome-profile", "linux");
  assert.ok(args.includes("--headless"));
  assert.ok(args.includes("--remote-debugging-pipe"));
  assert.ok(!args.some((arg) => arg.startsWith("--remote-debugging-port")));
  assert.ok(!args.includes("--enable-automation"));
  assert.ok(args.includes("--user-data-dir=/data/chrome-profile"));
  // The sign-in window reads the profile the same way the headless browser does.
  assert.deepEqual(profileArgs("/p", "linux"), ["--user-data-dir=/p", "--no-first-run", "--no-default-browser-check", "--password-store=basic"]);
  assert.ok(!profileArgs("/p", "darwin").includes("--password-store=basic"));
  const env = launchEnvironment({ DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-0", HOME: "/home/u" }, "linux");
  assert.equal(env.DISPLAY, undefined);
  assert.equal(env.WAYLAND_DISPLAY, undefined);
  assert.equal(env.HOME, "/home/u");
});

test("the DevTools pipe splits messages on NUL bytes, routes events and fails pending commands on close", async () => {
  const toChrome = new PassThrough();
  const fromChrome = new PassThrough();
  const events = [];
  let closed = 0;
  const connection = createPipeConnection(toChrome, fromChrome, { onEvent: (...event) => events.push(event), onClose: () => { closed += 1; } });
  const written = [];
  toChrome.on("data", (chunk) => written.push(String(chunk)));
  const answer = connection.send("Browser.getVersion", {});
  const failed = connection.send("Nope.nothing", {}, { sessionId: "S1" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(written.join(""), `${JSON.stringify({ id: 1, method: "Browser.getVersion", params: {} })}\0${JSON.stringify({ id: 2, method: "Nope.nothing", params: {}, sessionId: "S1" })}\0`);
  // One message split across chunks, then two in one chunk.
  const first = JSON.stringify({ id: 1, result: { product: "Chrome/1.2" } });
  fromChrome.write(Buffer.from(first.slice(0, 7)));
  fromChrome.write(Buffer.from(`${first.slice(7)}\0${JSON.stringify({ method: "Page.loadEventFired", params: { timestamp: 1 }, sessionId: "S1" })}\0${JSON.stringify({ id: 2, error: { code: -32601, message: "'Nope.nothing' wasn't found" } })}\0`));
  assert.deepEqual(await answer, { product: "Chrome/1.2" });
  await assert.rejects(failed, /wasn't found/);
  assert.deepEqual(events, [["Page.loadEventFired", { timestamp: 1 }, "S1"]]);
  const pending = connection.send("Page.navigate", { url: "https://example.com/" });
  fromChrome.destroy();
  await assert.rejects(pending, /closed the connection/);
  assert.equal(closed, 1);
  await assert.rejects(connection.send("Page.reload"), /not running/);
});

test("a missing Chrome fails with an answer the user can act on", async () => {
  const engine = createChromiumEngine({ dataDir: fs.mkdtempSync(path.join(os.tmpdir(), "chrome-missing-")), find: () => "" });
  await assert.rejects(engine.ensure(), /Google Chrome is not installed\. Install Google Chrome, or choose Raticode/);
  assert.equal(engine.status().available, false);
  assert.match(engine.status().profile, /chrome-profile$/);
  await assert.rejects(engine.openSignInWindow("https://accounts.google.com/"), /not installed/);
  await assert.rejects(engine.openSignInWindow("file:///etc/passwd"), /Choose a web address/);
  await assert.rejects(engine.openSignInWindow("chrome://flags"), /Choose a web address/);
});

test("cookies copied into Rem's context keep their scope and expiry", () => {
  assert.deepEqual(cookieParam({ name: "a", value: "1", domain: ".example.com", path: "/", secure: true, httpOnly: true, sameSite: "Lax", session: false, expires: 2000000000, size: 2, priority: "Medium", sourceScheme: "Secure", sourcePort: 443 }), {
    name: "a", value: "1", domain: ".example.com", path: "/", secure: true, httpOnly: true, sameSite: "Lax", expires: 2000000000, priority: "Medium", sourceScheme: "Secure", sourcePort: 443,
  });
  // A session cookie stays one.
  assert.equal(cookieParam({ name: "s", value: "2", domain: "a.test", path: "/", session: true, expires: -1 }).expires, undefined);
});

test("Rem's DevTools commands go straight to Chrome, except those that would change Raticode's viewport", async () => {
  const sent = [];
  const contents = Object.assign(new EventEmitter(), {
    name: "Chrome",
    destroyed: false,
    dialog: { type: "confirm", message: "Sure?", defaultValue: "" },
    alive: async () => {},
    command: async (method, params) => { sent.push([method, params]); return { ok: method }; },
  });
  const shim = createDebuggerPassthrough(contents);
  const messages = [];
  shim.on("message", (_event, method, params) => messages.push([method, params]));
  shim.attach();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(messages, [["Page.javascriptDialogOpening", { type: "confirm", message: "Sure?", defaultPrompt: "" }]]);
  assert.deepEqual(await shim.sendCommand("Runtime.evaluate", { expression: "1" }), { ok: "Runtime.evaluate" });
  assert.deepEqual(await shim.sendCommand("Emulation.setDeviceMetricsOverride", { width: 1 }), {});
  await assert.rejects(shim.sendCommand("Browser.close"), /do not support Browser\.close/);
  await assert.rejects(shim.sendCommand("Storage.getCookies"), /do not support/);
  assert.deepEqual(sent, [["Runtime.evaluate", { expression: "1" }]]);
  let detached = 0;
  shim.on("detach", () => { detached += 1; });
  shim.detach();
  shim.detach();
  assert.equal(detached, 1);
  assert.equal(shim.isAttached(), false);
});

const PAGE = `<!doctype html><title>Chrome test</title><body style="margin:0;height:3000px">
<input id=name aria-label=Name style="position:absolute;left:10px;top:10px;width:200px;height:30px">
<button id=press style="position:absolute;left:10px;top:60px;width:120px;height:40px" onclick="document.getElementById('out').textContent='clicked:'+event.isTrusted">Press</button>
<input id=file type=file aria-label=Upload style="position:absolute;left:10px;top:110px">
<button id=ask style="position:absolute;left:10px;top:150px;width:120px;height:40px" onclick="document.getElementById('out').textContent='confirm:'+confirm('Sure?')">Ask</button>
<select id=pick aria-label=Pick style="position:absolute;left:10px;top:200px;width:150px;height:30px"><option>one</option><option>two</option><option>three</option></select>
<a id=two href="/two" style="position:absolute;left:10px;top:250px">Two</a>
<a id=blocked href="BLOCKED" style="position:absolute;left:10px;top:280px">Blocked</a>
<div id=out style="position:absolute;left:10px;top:320px">none</div>
<a id=pop href="/two" target="_blank" style="position:absolute;left:10px;top:360px">Pop</a>
<a id=get href="/file.bin" download style="position:absolute;left:10px;top:390px">Get</a></body>`;

async function startSite() {
  const server = http.createServer((request, response) => {
    if (request.url === "/two") { response.setHeader("Content-Type", "text/html"); response.end("<title>Two</title><p>second</p>"); return; }
    if (request.url === "/file.bin") { response.setHeader("Content-Type", "application/octet-stream"); response.end("payload"); return; }
    if (request.url === "/cookie") { response.setHeader("Set-Cookie", "kept=yes; Path=/; Max-Age=3600"); response.setHeader("Content-Type", "text/html"); response.end("<title>Cookie</title>"); return; }
    response.setHeader("Content-Type", "text/html");
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

test("Rem drives Chrome with real input, keeps its site rules and asks for approval", realChrome, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "chrome-rem-test-"));
  const engine = createChromiumEngine(engineOptions(dataDir, { idleShutdownMs: 1000 }));
  const policy = sanitizeBrowserPolicy({ engine: "chrome", remBlockedSites: ["localhost"], remConfirm: "always" });
  const tabs = [];
  const approvals = [];
  const controller = createAgentBrowserController({
    openTab: ({ agent, label }) => {
      const contents = new ChromiumContents({ engine, kind: "rem", nativeImage: fakeImage });
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
  const call = (action, extra = {}) => controller.handle({ agent: "agent-chrome-0001", action, ...extra });
  try {
    const opened = await call("open", { url: `${base}/` });
    assert.equal(opened.title, "Chrome test");
    const ref = (snapshot, name) => new RegExp(`"${name}"[^\\n]*\\[ref=(e\\d+)\\]`).exec(snapshot)[1];
    let { snapshot } = await call("snapshot");
    assert.match(snapshot, /textbox "Name"/);
    assert.match(snapshot, /\[file-upload\]/);
    const page = tabs[0].contents;
    const read = (expression) => page.evaluateIn("test", expression);
    // Pages see the Chrome it is, not "HeadlessChrome", and see that it is automated.
    assert.doesNotMatch(await read("navigator.userAgent"), /HeadlessChrome/);
    assert.equal(await read("navigator.webdriver"), true);

    await call("type", { ref: ref(snapshot, "Name"), text: "Ada" });
    assert.equal(await read("document.getElementById('name').value"), "Ada");
    await call("click", { ref: ref(snapshot, "Press") });
    assert.equal(await read("document.getElementById('out').textContent"), "clicked:true");

    const upload = path.join(dataDir, "notes.txt");
    fs.writeFileSync(upload, "hello");
    const uploaded = await controller.handle({ agent: "agent-chrome-0001", action: "upload", ref: ref(snapshot, "Upload"), paths: [upload], uploadRoots: [dataDir] });
    assert.deepEqual(uploaded.uploaded, ["notes.txt"]);
    assert.equal(await read("document.getElementById('file').files[0].name"), "notes.txt");
    await assert.rejects(controller.handle({ agent: "agent-chrome-0001", action: "upload", ref: ref(snapshot, "Upload"), paths: ["/etc/hostname"], uploadRoots: [dataDir] }), /outside this task's project folders|File not found/);

    const asked = await call("click", { ref: ref(snapshot, "Ask") });
    assert.deepEqual(asked.dialog, { type: "confirm", message: "Sure?" });
    await call("dialog", { accept: true });
    assert.equal(await read("document.getElementById('out').textContent"), "confirm:true");

    const evaluated = await call("evaluate", { expression: "navigator.userActivation.isActive" });
    assert.equal(evaluated.result, "true");
    assert.deepEqual(approvals, ["Upload notes.txt", "Run a script on this page"]);

    // Site rules: typed addresses and links the page follows.
    await assert.rejects(call("navigate", { url: "http://localhost/" }), /blocked Rem from localhost/);
    // Chrome opens about:settings as chrome://settings; Rem's tabs never reach it.
    for (const url of ["about:settings", "chrome://settings", "chrome://extensions"]) {
      await assert.rejects(page.loadURL(url), /own pages can't be opened/);
    }
    await assert.rejects(call("navigate", { url: "about:settings" }), /http and https pages only|own pages can't be opened/);
    assert.equal(page.getURL(), `${base}/`);
    ({ snapshot } = await call("snapshot"));
    const blocked = await call("click", { ref: ref(snapshot, "Blocked") });
    assert.equal(new URL(blocked.url).hostname, "127.0.0.1");
    assert.ok(tabs[0].error, "the blocked navigation should report an error");

    const second = await call("click", { ref: ref(snapshot, "Two") });
    assert.equal(second.title, "Two");
    const back = await call("back");
    assert.equal(back.title, "Chrome test");
    const shot = await call("screenshot");
    assert.ok(shot.image.length > 1000);
    await call("close");
    assert.equal(page.isDestroyed(), true);
  } finally {
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("a streamed Chrome tab sends frames and takes the user's mouse, wheel and keys", realChrome, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "chrome-stream-test-"));
  const engine = createChromiumEngine(engineOptions(dataDir));
  const copied = [];
  const contents = new ChromiumContents({ engine, kind: "user", clipboard: { writeText: (text) => copied.push(text) } });
  const frames = [];
  const selects = [];
  const cursors = [];
  const opened = [];
  contents.on("stream-frame", (frame) => {
    frames.push(frame);
    setTimeout(() => contents.ackFrame(frame.seq), 5);
  });
  contents.on("stream-select", (details) => selects.push(details));
  contents.on("stream-cursor", (details) => cursors.push(details.cursor));
  contents.on("stream-open-tab", (details) => opened.push(details.url));
  contents.setWindowOpenHandler(() => ({ action: "allow" }));
  const read = (expression) => contents.evaluateIn("test", expression);
  try {
    await contents.loadURL(`${base}/`);
    assert.equal(contents.getTitle(), "Chrome test");
    assert.equal(contents.navigationHistory.canGoBack(), false, "the blank first page is not history");
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
    // Control+A selected instead of typing an "a".
    assert.equal(await read("document.getElementById('name').value"), "Hi ther");

    // A trusted click on the button.
    contents.handleInput([{ t: "move", x: 60, y: 80 }, { t: "down", x: 60, y: 80, button: 0 }, { t: "up", x: 60, y: 80, button: 0 }]);
    await waitFor(async () => (await read("document.getElementById('out').textContent")) === "clicked:true", "click did not reach the page");
    await waitFor(() => cursors.length, "no cursor report");

    // A <select> opens the studio's list; choosing an option changes the page.
    contents.handleInput([{ t: "down", x: 50, y: 215, button: 0 }, { t: "up", x: 50, y: 215, button: 0 }]);
    await waitFor(() => selects.length, "no option list for the select");
    assert.deepEqual(selects[0].options.map((option) => option.label), ["one", "two", "three"]);
    contents.handleInput([{ t: "select-option", index: 2 }]);
    await waitFor(async () => (await read("document.getElementById('pick').value")) === "three", "the option was not chosen");
    // The keyboard still reaches the page after the list closes.
    contents.handleInput([{ t: "down", x: 50, y: 25, button: 0 }, { t: "up", x: 50, y: 25, button: 0 }, { t: "keydown", key: "End" }, { t: "keyup", key: "End" }, { t: "keydown", key: "!" }, { t: "keyup", key: "!" }]);
    await waitFor(async () => (await read("document.getElementById('name').value")) === "Hi ther!", "typing after the select did not reach the page");

    // The wheel scrolls the page, and the stream follows.
    const before = frames.length;
    contents.handleInput([{ t: "wheel", x: 300, y: 300, dx: 0, dy: 400 }]);
    await waitFor(async () => (await read("scrollY")) > 0, "the wheel did not scroll");
    await waitFor(() => frames.length > before, "no new frame after scrolling");
    await contents.evaluateIn("test", "scrollTo(0, 0)");

    // Find in page counts matches and selects the first one.
    const found = new Promise((resolve) => contents.once("found-in-page", (_event, result) => resolve(result)));
    const requestId = contents.findInPage("press", { forward: true, findNext: true });
    const result = await found;
    assert.equal(result.requestId, requestId);
    assert.equal(result.matches, 1);
    assert.equal(result.activeMatchOrdinal, 1);
    assert.equal(await read("getSelection().toString()"), "Press");
    contents.stopFindInPage();

    // A link that opens a new window becomes a Raticode tab instead.
    contents.handleInput([{ t: "down", x: 15, y: 368, button: 0 }, { t: "up", x: 15, y: 368, button: 0 }]);
    await waitFor(() => opened.length, "the new window did not become a tab");
    assert.equal(opened[0], `${base}/two`);

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
    contents.navigationHistory.goBack();
    await waitFor(() => contents.getURL() === `${base}/`, "back did not return to the first page");
    assert.equal(contents.navigationHistory.canGoForward(), true);
    const pdf = await contents.printToPDF({});
    assert.equal(pdf.subarray(0, 4).toString(), "%PDF");
  } finally {
    contents.close();
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("Rem's Chrome tabs start signed out and get only the sign-ins the user shares; downloads land in staging", realChrome, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "chrome-signin-test-"));
  const engine = createChromiumEngine(engineOptions(dataDir));
  const user = new ChromiumContents({ engine, kind: "user" });
  let rem = null;
  try {
    await user.loadURL(`${base}/cookie`);
    assert.equal(await user.evaluateIn("test", "document.cookie"), "kept=yes");
    rem = new ChromiumContents({ engine, kind: "rem" });
    await rem.loadURL(`${base}/`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "", "Rem's tab starts signed out");
    await engine.mirrorSignIns(["127.0.0.1"]);
    await rem.loadURL(`${base}/`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "kept=yes", "a shared site's sign-in reaches Rem");
    await engine.mirrorSignIns([], ["127.0.0.1"]);
    await rem.loadURL(`${base}/`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "", "an unshared site's sign-in leaves Rem");
    assert.equal(await user.evaluateIn("test", "document.cookie"), "kept=yes", "the user's sign-in stays");
    assert.equal(await engine.clearSiteCookies("user", "127.0.0.1"), 1);
    await user.loadURL(`${base}/`);
    assert.equal(await user.evaluateIn("test", "document.cookie"), "");

    // Downloads: the user's land in the staging folder; Rem's are refused by default.
    const ended = new Promise((resolve) => user.once("stream-download-end", resolve));
    const began = new Promise((resolve) => user.once("stream-download-begin", resolve));
    await user.loadURL(`${base}/file.bin`).catch((error) => assert.match(error.message, /ERR_ABORTED/));
    assert.equal((await began).filename, "file.bin");
    const end = await ended;
    assert.equal(end.status, "complete");
    assert.equal(path.dirname(end.filepath), engine.stagingDir);
    assert.equal(fs.readFileSync(end.filepath, "utf8"), "payload");
    // Chrome still announces the refused download, which Raticode logs as blocked.
    const remEnded = new Promise((resolve) => rem.once("stream-download-end", resolve));
    await rem.loadURL(`${base}/file.bin`).catch(() => {});
    assert.equal((await remEnded).status, "canceled");
    assert.equal(fs.readdirSync(engine.stagingDir).length, 1, "Rem's download was refused");

    // A file the page builds itself and revokes straight after the click, as
    // Open WebUI's "Download" does.
    await user.loadURL(`${base}/`);
    const blobBegan = new Promise((resolve) => user.once("stream-download-begin", resolve));
    const blobEnded = new Promise((resolve) => user.once("stream-download-end", resolve));
    await user.evaluateIn("test", `(() => {
      const url = URL.createObjectURL(new Blob(["<p>chat</p>"], { type: "text/html" }));
      const a = document.createElement("a");
      a.href = url; a.download = "chat.html";
      document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      return true;
    })()`, { userGesture: true });
    const blobStart = await blobBegan;
    assert.equal(blobStart.filename, "chat.html");
    assert.match(blobStart.url, /^blob:/);
    const blobEnd = await blobEnded;
    assert.equal(blobEnd.navigation, blobStart.navigation);
    assert.equal(blobEnd.status, "complete");
    assert.equal(fs.readFileSync(blobEnd.filepath, "utf8"), "<p>chat</p>");

    // No network control port: Chrome writes no DevToolsActivePort file.
    assert.equal(fs.existsSync(path.join(engine.profileDir(), "DevToolsActivePort")), false);
    assert.equal(fs.statSync(engine.profileDir()).mode & 0o777, 0o700);
  } finally {
    user.close();
    rem?.close();
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("a dead Chrome tab restarts on reload, and Chrome does not outlive a Raticode process that dies", realChrome, async () => {
  const { server, base } = await startSite();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "chrome-life-test-"));
  const engine = createChromiumEngine(engineOptions(dataDir));
  const contents = new ChromiumContents({ engine, kind: "user" });
  try {
    await contents.loadURL(`${base}/`);
    engine.stop();
    await waitFor(() => !engine.status().running, "Chrome did not stop");
    contents.markDead("Chrome stopped");
    assert.equal(contents.dead, true);
    await contents.reloadPage(false);
    await waitFor(() => contents.getTitle() === "Chrome test", "the tab did not come back");
  } finally {
    contents.close();
    await engine.stopAndWait();
    server.close();
  }

  // A Node process that starts Chrome and is then killed outright.
  const script = `
    const { createChromiumEngine } = require(${JSON.stringify(path.join(__dirname, "..", "chromium-engine.cjs"))});
    const engine = createChromiumEngine({ dataDir: process.argv[1], env: { ...process.env, RATICODE_CHROME_PATH: process.argv[2] } });
    engine.ensure().then(() => { console.log("ready"); setInterval(() => {}, 1000); }, (error) => { console.error(error); process.exit(2); });
  `;
  const child = spawn(process.execPath, ["-e", script, dataDir, CHROME_PATH], { stdio: ["ignore", "pipe", "inherit"], env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } });
  await new Promise((resolve, reject) => {
    child.stdout.on("data", (data) => { if (String(data).includes("ready")) resolve(); });
    child.once("exit", (code) => reject(new Error(`helper exited ${code}`)));
  });
  const running = () => process.platform !== "linux" ? [] : fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
    try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(`--user-data-dir=${path.join(dataDir, "chrome-profile")}`); } catch { return false; }
  });
  if (process.platform === "linux") assert.ok(running().length > 0, "Chrome should be running");
  child.kill("SIGKILL");
  await waitFor(() => running().length === 0, "Chrome kept running after its Raticode process died", 15000);
  fs.rmSync(dataDir, { recursive: true, force: true });
});
