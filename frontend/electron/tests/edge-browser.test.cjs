// Edge mode: Microsoft Edge on the same windowless Chromium engine as Chrome
// mode (chromium-engine.cjs, chromium-contents.cjs). These tests cover what
// is Edge's own: where Edge is installed, its profile, the Edge pages its
// window may open, and a real run through the Edge entry. Edge is Chromium
// with the same DevTools protocol, so the real-browser tests use Edge when it
// is installed, or a Chromium build from RATICODE_EDGE_PATH or Playwright's
// downloads, and are skipped when there is none.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { BROWSERS, EDGE, createChromiumEngine, findBrowser } = require("../chromium-engine.cjs");
const { ChromiumContents } = require("../chromium-contents.cjs");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { remSiteAllowed, sanitizeBrowserPolicy } = require("../browser-policy.cjs");

function chromiumForTesting() {
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

const EDGE_PATH = process.env.RATICODE_SKIP_EDGE_TESTS === "1" ? "" : (findBrowser(EDGE) || (process.platform === "linux" ? chromiumForTesting() : ""));
const realEdge = { skip: EDGE_PATH ? false : "Neither Microsoft Edge nor a Chromium stand-in is installed" };
const engineOptions = (dataDir, extra = {}) => ({ browser: EDGE, dataDir, env: { ...process.env, RATICODE_EDGE_PATH: EDGE_PATH }, ...extra });

function temp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

async function waitFor(check, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}

test("Edge is a browser of the Chromium engine, found from the explicit path or the usual install folders", () => {
  assert.equal(BROWSERS.edge, EDGE);
  assert.equal(EDGE.id, "edge");
  assert.equal(EDGE.name, "Edge");
  assert.equal(EDGE.fullName, "Microsoft Edge");
  assert.equal(EDGE.profileFolder, "edge-profile");
  const linux = EDGE.candidates({ platform: "linux", env: { PATH: "/usr/local/bin", RATICODE_EDGE_PATH: "/x/msedge" }, home: "/home/u" });
  assert.equal(linux[0], "/x/msedge");
  assert.ok(linux.includes("/usr/local/bin/microsoft-edge"));
  assert.ok(linux.includes("/usr/local/bin/microsoft-edge-stable"));
  assert.ok(linux.includes("/opt/microsoft/msedge/msedge"));
  // Release before Beta and Dev.
  assert.ok(linux.indexOf("/opt/microsoft/msedge/msedge") < linux.indexOf("/opt/microsoft/msedge-beta/msedge"));
  const mac = EDGE.candidates({ platform: "darwin", env: {}, home: "/Users/u" });
  assert.equal(mac[0], "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge");
  assert.ok(mac.includes("/Users/u/Applications/Microsoft Edge Beta.app/Contents/MacOS/Microsoft Edge Beta"));
  const windows = EDGE.candidates({ platform: "win32", env: { "ProgramFiles(x86)": "C:\\PF86", LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" }, home: "C:\\Users\\u" });
  assert.equal(windows[0], "C:\\PF86\\Microsoft\\Edge\\Application\\msedge.exe");
  assert.ok(windows.includes("C:\\Users\\u\\AppData\\Local\\Microsoft\\Edge SxS\\Application\\msedge.exe"));
  assert.equal(findBrowser(EDGE, { platform: "linux", env: { PATH: "" }, exists: (candidate) => candidate === "/opt/microsoft/msedge/msedge" }), "/opt/microsoft/msedge/msedge");
  assert.equal(findBrowser(EDGE, { platform: "linux", env: { PATH: "" }, exists: () => false }), "");
});

test("a missing Edge fails with an answer the user can act on", async () => {
  const dataDir = temp("edge-missing-test-");
  const engine = createChromiumEngine({ browser: EDGE, dataDir, find: () => "" });
  try {
    const status = engine.status();
    assert.equal(status.available, false);
    assert.equal(status.id, "edge");
    assert.equal(status.name, "Microsoft Edge");
    assert.equal(status.profile, path.join(dataDir, "edge-profile"));
    await assert.rejects(engine.ensure(), /Microsoft Edge is not installed/);
    await assert.rejects(engine.openSignInWindow("https://login.microsoftonline.com/"), /Microsoft Edge is not installed/);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

function fakeProcess() {
  const child = new EventEmitter();
  child.pid = 4243;
  child.exitCode = null;
  child.kill = () => { child.exitCode = 0; child.emit("exit", 0); };
  return child;
}

test("the Edge window opens Raticode's Edge profile without remote control, at web pages or Edge's own settings", async (t) => {
  const launches = [];
  const dataDir = temp("edge-window-test-");
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const engine = createChromiumEngine({
    browser: EDGE,
    dataDir,
    find: () => "/opt/microsoft/msedge/msedge",
    platform: "linux",
    env: { DISPLAY: ":0", PATH: "/usr/bin" },
    spawn: (command, args, options) => {
      const child = fakeProcess();
      launches.push({ command, args, options, child });
      return child;
    },
  });
  for (const refused of ["javascript:alert(1)", "file:///etc/passwd", "edge://flags", "chrome://flags", "edge://inspect", "chrome-extension://abc/page.html", "microsoft-edge:https://example.com/"]) {
    await assert.rejects(engine.openSignInWindow(refused), /web address/, refused);
  }
  assert.deepEqual(await engine.openSignInWindow("https://login.microsoftonline.com/"), { opened: true });
  const [launch] = launches;
  assert.equal(launch.command, "/opt/microsoft/msedge/msedge");
  assert.equal(launch.args[0], `--user-data-dir=${path.join(dataDir, "edge-profile")}`);
  assert.ok(launch.args.includes("--password-store=basic"));
  assert.equal(launch.args.at(-1), "https://login.microsoftonline.com/");
  assert.equal(launch.args.some((arg) => /headless|remote-debugging|enable-automation/.test(arg)), false);
  assert.equal(launch.options.env.DISPLAY, ":0");
  assert.equal(engine.signingIn, true);
  await assert.rejects(engine.ensure(), /Edge is open in a window/);
  const closed = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
  launch.child.kill();
  await closed;
  // Every Edge page Settings > Browser > Edge offers.
  for (const page of ["edge://settings/profiles", "edge://settings", "edge://extensions", "edge://wallet/passwords", "edge://settings/profiles/importBrowsingData", "https://microsoftedge.microsoft.com/addons/Microsoft-Edge-Extensions-Home"]) {
    assert.deepEqual(await engine.openSignInWindow(page), { opened: true }, page);
    assert.equal(launches.at(-1).args.at(-1), page);
    const done = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
    launches.at(-1).child.kill();
    await done;
  }
});

test("the Settings panel's Edge pages are all ones the Edge window accepts", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "..", "src", "components", "EdgeSettings.jsx"), "utf8");
  const pages = [...source.matchAll(/^\s*\["((?:https|edge):\/\/[^"]+)"/gm)].map((match) => match[1]);
  assert.ok(pages.length >= 6);
  for (const page of pages) {
    const address = new URL(page);
    if (address.protocol === "edge:") {
      assert.ok(EDGE.ownPageSchemes.includes(address.protocol), page);
      assert.ok(EDGE.ownPageHosts.includes(address.hostname), page);
    } else {
      assert.equal(address.protocol, "https:", page);
    }
  }
});

const PAGE = `<!doctype html><title>Edge test</title><body style="margin:0;height:3000px">
<input id=name aria-label=Name style="position:absolute;left:10px;top:10px;width:200px;height:30px">
<button id=press style="position:absolute;left:10px;top:60px;width:120px;height:40px" onclick="document.getElementById('out').textContent='clicked:'+event.isTrusted">Press</button>
<input id=file type=file aria-label=Upload style="position:absolute;left:10px;top:110px">
<button id=ask style="position:absolute;left:10px;top:150px;width:120px;height:40px" onclick="document.getElementById('out').textContent='confirm:'+confirm('Sure?')">Ask</button>
<a id=two href="/two" style="position:absolute;left:10px;top:250px">Two</a>
<a id=blocked href="BLOCKED" style="position:absolute;left:10px;top:280px">Blocked</a>
<div id=out style="position:absolute;left:10px;top:320px">none</div>
</body>`;

async function startSite() {
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    if (request.url === "/two") { response.end("<title>Two</title><p>second</p>"); return; }
    if (request.url === "/login") { response.setHeader("Set-Cookie", "sid=user-secret; Path=/; Max-Age=3600"); response.end("<title>Signed in</title>"); return; }
    if (request.url === "/ua") { response.end(`<title>${String(request.headers["user-agent"]).includes("Headless") ? "headless" : "normal"}</title>`); return; }
    const port = server.address().port;
    response.end(PAGE.replace("BLOCKED", `http://localhost:${port}/secret`));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

test("Rem drives an Edge tab with real input, keeps its site rules and asks for approval", realEdge, async () => {
  const { server, base } = await startSite();
  const dataDir = temp("edge-rem-test-");
  const engine = createChromiumEngine(engineOptions(dataDir, { idleShutdownMs: 1000 }));
  const policy = sanitizeBrowserPolicy({ engine: "edge", remBlockedSites: ["localhost"], remConfirm: "always" });
  assert.equal(policy.engine, "edge");
  const tabs = [];
  const approvals = [];
  const controller = createAgentBrowserController({
    openTab: ({ agent, label }) => {
      const contents = new ChromiumContents({ engine, kind: "rem", nativeImage: fakeImage });
      contents.guardNavigation = true;
      const session = { id: `tab-${tabs.length}`, agent: { id: agent, label, shared: false }, contents, viewSize: null };
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
  const call = (action, extra = {}) => controller.handle({ agent: "agent-edge-0001", action, ...extra });
  try {
    const opened = await call("open", { url: `${base}/` });
    assert.equal(opened.title, "Edge test");
    const page = tabs[0].contents;
    assert.equal(page.engine, "edge");
    assert.equal(page.name, "Edge");
    const ref = (snapshot, name) => new RegExp(`"${name}"[^\\n]*\\[ref=(e\\d+)\\]`).exec(snapshot)[1];
    let { snapshot } = await call("snapshot");
    const read = (expression) => page.evaluateIn("test", expression);
    await call("type", { ref: ref(snapshot, "Name"), text: "Ada" });
    assert.equal(await read("document.getElementById('name').value"), "Ada");
    await call("click", { ref: ref(snapshot, "Press") });
    assert.equal(await read("document.getElementById('out').textContent"), "clicked:true");
    const upload = path.join(dataDir, "notes.txt");
    fs.writeFileSync(upload, "hello");
    const uploaded = await controller.handle({ agent: "agent-edge-0001", action: "upload", ref: ref(snapshot, "Upload"), paths: [upload], uploadRoots: [dataDir] });
    assert.deepEqual(uploaded.uploaded, ["notes.txt"]);
    const asked = await call("click", { ref: ref(snapshot, "Ask") });
    assert.deepEqual(asked.dialog, { type: "confirm", message: "Sure?" });
    await call("dialog", { accept: true });
    assert.equal(await read("document.getElementById('out').textContent"), "confirm:true");
    assert.deepEqual(approvals, ["Upload notes.txt"]);
    await assert.rejects(call("navigate", { url: "http://localhost/" }), /blocked Rem from localhost/);
    ({ snapshot } = await call("snapshot"));
    const blocked = await call("click", { ref: ref(snapshot, "Blocked") });
    assert.equal(new URL(blocked.url).hostname, "127.0.0.1");
    const second = await call("click", { ref: ref(snapshot, "Two") });
    assert.equal(second.title, "Two");
    const back = await call("back");
    assert.equal(back.title, "Edge test");
    // Edge's own pages stay out of Rem's tabs.
    await assert.rejects(page.loadURL("edge://settings"), /own pages|can't be shown|not allowed/i);
    await call("close");
    assert.equal(page.isDestroyed(), true);
  } finally {
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("a streamed Edge tab sends frames, takes typing, keeps its sign-ins across restarts and shares them with Rem only on request", realEdge, async () => {
  const { server, base } = await startSite();
  const dataDir = temp("edge-stream-test-");
  const engine = createChromiumEngine(engineOptions(dataDir));
  const contents = new ChromiumContents({ engine, kind: "user" });
  const frames = [];
  contents.on("stream-frame", (frame) => {
    frames.push(frame);
    setTimeout(() => contents.ackFrame(frame.seq), 5);
  });
  let rem = null;
  try {
    await contents.loadURL(`${base}/`);
    contents.setView({ width: 800, height: 600, scale: 1 }, true);
    await waitFor(() => frames.length >= 1, "no frame arrived");
    assert.equal(frames[0].data[0], 0xff, "frames are JPEG");
    contents.handleInput([{ t: "down", x: 50, y: 25, button: 0 }, { t: "up", x: 50, y: 25, button: 0 }]);
    for (const key of ["H", "i"]) contents.handleInput([{ t: "keydown", key }, { t: "keyup", key }]);
    await waitFor(async () => (await contents.evaluateIn("test", "document.getElementById('name').value")) === "Hi", "typing did not reach the page");
    // Pages see the browser's own name, not HeadlessChrome.
    await contents.loadURL(`${base}/ua`);
    assert.equal(contents.getTitle(), "normal");

    await contents.loadURL(`${base}/login`);
    rem = new ChromiumContents({ engine, kind: "rem" });
    await rem.loadURL(`${base}/two`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "", "Rem's Edge tab starts signed out");
    await engine.mirrorSignIns(["127.0.0.1"]);
    await rem.loadURL(`${base}/two`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "sid=user-secret");
    rem.close();
    rem = null;

    // The profile keeps the user's sign-in when Edge restarts.
    await engine.stopAndWait();
    contents.markDead("Edge stopped");
    await contents.reloadPage(false);
    await waitFor(() => !contents.isLoading() && contents.getTitle() === "Signed in", "the tab did not come back");
    await contents.loadURL(`${base}/two`);
    assert.equal(await contents.evaluateIn("test", "document.cookie"), "sid=user-secret");
    assert.equal(fs.statSync(path.join(dataDir, "edge-profile")).mode & 0o777, 0o700);
    assert.equal(fs.existsSync(path.join(dataDir, "edge-profile", "DevToolsActivePort")), false, "no network control port");
  } finally {
    rem?.close();
    contents.close();
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("Edge does not outlive a Raticode process that dies", realEdge, async () => {
  const dataDir = temp("edge-life-test-");
  const script = `
    const { EDGE, createChromiumEngine } = require(${JSON.stringify(path.join(__dirname, "..", "chromium-engine.cjs"))});
    const engine = createChromiumEngine({ browser: EDGE, dataDir: process.argv[1], env: { ...process.env, RATICODE_EDGE_PATH: process.argv[2] } });
    engine.ensure().then(() => { console.log("ready"); setInterval(() => {}, 1000); }, (error) => { console.error(error); process.exit(2); });
  `;
  const child = spawn(process.execPath, ["-e", script, dataDir, EDGE_PATH], { stdio: ["ignore", "pipe", "inherit"], env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } });
  await new Promise((resolve, reject) => {
    child.stdout.on("data", (data) => { if (String(data).includes("ready")) resolve(); });
    child.once("exit", (code) => reject(new Error(`helper exited ${code}`)));
  });
  const running = () => process.platform !== "linux" ? [] : fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
    try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(`--user-data-dir=${path.join(dataDir, "edge-profile")}`); } catch { return false; }
  });
  if (process.platform === "linux") assert.ok(running().length > 0, "Edge should be running");
  child.kill("SIGKILL");
  await waitFor(() => running().length === 0, "Edge kept running after its Raticode process died", 15000);
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test("a right-click in an Edge tab describes what was clicked, and the menu's commands edit the page", realEdge, async () => {
  const server = http.createServer((request, response) => {
    if (request.url === "/pic.png") {
      response.setHeader("Content-Type", "image/png");
      response.end(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
      return;
    }
    response.setHeader("Content-Type", "text/html");
    response.end(`<!doctype html><title>Menu</title><body style="margin:0">
      <a id=link href="/next" style="position:absolute;left:10px;top:10px;width:100px;height:20px;display:block">Next page</a>
      <img id=pic src="/pic.png" style="position:absolute;left:10px;top:50px;width:60px;height:60px">
      <textarea id=box style="position:absolute;left:10px;top:130px;width:200px;height:40px">hello</textarea>
      <div id=own style="position:absolute;left:10px;top:200px;width:100px;height:40px" oncontextmenu="event.preventDefault()">Own menu</div>
    </body>`);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const dataDir = temp("edge-menu-test-");
  const engine = createChromiumEngine(engineOptions(dataDir));
  const copied = [];
  const contents = new ChromiumContents({ engine, kind: "user", clipboard: { writeText: (text) => copied.push(text) } });
  const menus = [];
  contents.on("context-menu", (_event, params) => menus.push(params));
  const rightClick = async (x, y) => {
    const before = menus.length;
    contents.handleInput([{ t: "down", x, y, button: 2 }, { t: "up", x, y, button: 2 }]);
    await new Promise((resolve) => setTimeout(resolve, 600));
    return menus.length > before ? menus.at(-1) : null;
  };
  const value = () => contents.evaluateIn("test", "document.getElementById('box').value");
  try {
    await contents.loadURL(`${base}/`);
    contents.setView({ width: 800, height: 600, scale: 1 }, true);
    const link = await rightClick(30, 20);
    assert.equal(link?.linkURL, `${base}/next`);
    assert.equal(link.isEditable, false);
    const picture = await rightClick(30, 70);
    assert.equal(picture?.mediaType, "image");
    assert.equal(picture.srcURL, `${base}/pic.png`);
    // A page with a menu of its own keeps it; Raticode's doesn't open.
    assert.equal(await rightClick(30, 220), null);
    const field = await rightClick(30, 150);
    assert.equal(field?.isEditable, true);

    await contents.editCommand("selectAll");
    await contents.editCommand("copy");
    assert.deepEqual(copied, ["hello"]);
    await contents.editCommand("cut");
    assert.equal(await value(), "");
    assert.deepEqual(copied, ["hello", "hello"]);
    await contents.editCommand("paste", "pasted");
    assert.equal(await value(), "pasted");
    // Undo steps back through the paste, then the cut; Redo repeats the cut.
    await contents.editCommand("undo");
    assert.equal(await value(), "");
    await contents.editCommand("undo");
    assert.equal(await value(), "hello");
    await contents.editCommand("redo");
    assert.equal(await value(), "");
    await assert.rejects(contents.editCommand("format-disk"), /Unknown edit command/);

    // Save page as… writes one MHTML file with the page and its picture.
    const file = path.join(dataDir, "page.mhtml");
    await contents.savePage(file);
    const saved = fs.readFileSync(file, "utf8");
    assert.match(saved, /MIME-Version: 1\.0/);
    assert.match(saved, /Content-Type: image\/png/);
    if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  } finally {
    contents.close();
    await engine.stopAndWait();
    server.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
