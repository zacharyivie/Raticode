// Every action Rem's and workers' browser tools offer (agent-browser.cjs),
// run against a real headless Chrome, Microsoft Edge and Firefox with the
// same page, so the streamed engines can be compared action by action.
// Each group is skipped when its browser isn't installed. Chrome comes from
// RATICODE_CHROME_PATH, an installed Google Chrome, or a Chrome for Testing
// build Playwright downloaded. Edge comes from RATICODE_EDGE_PATH or an
// installed Edge; without one, the same Chrome for Testing build stands in,
// since Edge is Chromium with the same DevTools protocol.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { CHROME, EDGE, createChromiumEngine, findBrowser } = require("../chromium-engine.cjs");
const { ChromiumContents } = require("../chromium-contents.cjs");
const { createFirefoxEngine, findFirefox } = require("../firefox-engine.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { remSiteAllowed, sanitizeBrowserPolicy } = require("../browser-policy.cjs");

function chromeForTesting() {
  const root = path.join(os.homedir(), ".cache", "ms-playwright");
  try {
    for (const entry of fs.readdirSync(root).filter((name) => /^chromium-\d+$/.test(name)).sort().reverse()) {
      const candidate = path.join(root, entry, "chrome-linux64", "chrome");
      if (fs.existsSync(candidate)) return candidate;
    }
  } catch { /* No Playwright downloads. */ }
  return "";
}

const CHROME_PATH = process.env.RATICODE_SKIP_CHROME_TESTS === "1" ? "" : (findBrowser(CHROME) || (process.platform === "linux" ? chromeForTesting() : ""));
const EDGE_PATH = process.env.RATICODE_SKIP_EDGE_TESTS === "1" ? "" : (findBrowser(EDGE) || (process.platform === "linux" ? chromeForTesting() : ""));
const FIREFOX_PATH = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();

const ENGINES = {
  chrome: {
    path: CHROME_PATH,
    engine: (dataDir) => createChromiumEngine({ dataDir, env: { ...process.env, RATICODE_CHROME_PATH: CHROME_PATH }, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  edge: {
    path: EDGE_PATH,
    engine: (dataDir) => createChromiumEngine({ browser: EDGE, dataDir, env: { ...process.env, RATICODE_EDGE_PATH: EDGE_PATH }, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  firefox: {
    path: FIREFOX_PATH,
    engine: (dataDir) => createFirefoxEngine({ dataDir, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new FirefoxContents({ engine, kind, nativeImage: fakeImage }),
  },
};

const PAGE = `<!doctype html><title>Actions</title><body style="margin:0">
<form id=form onsubmit="event.preventDefault(); document.title='submitted:'+document.getElementById('q').value">
  <input id=q aria-label=Query style="position:absolute;left:10px;top:10px;width:200px;height:30px">
</form>
<button id=mouse style="position:absolute;left:10px;top:60px;width:160px;height:40px"
  onmousedown="log('down:'+event.button+':'+event.isTrusted)" ondblclick="log('double:'+event.isTrusted)"
  oncontextmenu="event.preventDefault(); log('menu')">Mouse</button>
<div id=hover role=button tabindex=0 aria-label=Hover style="position:absolute;left:10px;top:110px;width:160px;height:40px;background:#ddd"
  onmouseenter="log('hovered:'+event.isTrusted)">Hover</div>
<select id=one aria-label=One style="position:absolute;left:10px;top:160px"><option>red</option><option>green</option><option>blue</option></select>
<select id=many aria-label=Many multiple style="position:absolute;left:10px;top:200px;height:60px"><option>a</option><option>b</option><option>c</option></select>
<button id=later style="position:absolute;left:10px;top:270px" onclick="setTimeout(() => { const p = document.createElement('p'); p.id = 'late'; p.textContent = 'Arrived late'; document.body.appendChild(p); document.getElementById('soon').remove(); }, 600)">Later</button>
<p id=soon style="position:absolute;left:10px;top:300px">Leaving soon</p>
<button id=ask style="position:absolute;left:10px;top:340px" onclick="log('prompt:'+prompt('Name?', 'x'))">Ask</button>
<a href="/two" style="position:absolute;left:10px;top:380px">Two</a>
<div id=out style="position:absolute;left:300px;top:10px"></div>
<script>
  const seen = [];
  function log(item) { seen.push(item); document.getElementById('out').textContent = seen.join('|'); }
  document.addEventListener('keydown', (event) => { if (event.target.id === 'q') log('key:' + event.key + (event.ctrlKey ? '+ctrl' : '') + ':' + event.isTrusted); });
  document.getElementById('q').addEventListener('focus', () => log('focus'));
</script></body>`;

async function startSite() {
  let loads = 0;
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    if (request.url === "/two") { response.end("<title>Two</title><p>second</p>"); return; }
    loads += 1;
    response.end(PAGE.replace("<title>Actions</title>", `<title>Actions ${loads}</title>`));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

function harness(spec, engine, { sharedContents = null } = {}) {
  const policy = sanitizeBrowserPolicy({ remConfirm: "never" });
  const tabs = [];
  const controller = createAgentBrowserController({
    openTab: ({ agent, label }) => {
      const contents = spec.contents(engine, "rem");
      contents.guardNavigation = true;
      const session = { id: `tab-${tabs.length}`, agent: { id: agent, label, shared: false }, contents, viewSize: null };
      contents.on("will-navigate", (event, url) => { if (!remSiteAllowed(url, policy)) event.preventDefault(); });
      tabs.push(session);
      return session;
    },
    agentTabs: (agent) => tabs.filter((item) => item.agent?.id === agent && !item.contents.isDestroyed()),
    closeTab: (session) => session.contents.close(),
    normalizeUrl: (value) => value,
    policy: () => policy,
    confirm: async () => true,
  });
  // A tab the user opened and shared with Rem, as browser-sessions.cjs does.
  if (sharedContents) tabs.push({ id: "shared", agent: { id: "agent-shared-0001", label: "Rem", shared: true }, contents: sharedContents, viewSize: { width: 1200, height: 800 } });
  return { controller, tabs };
}

const ref = (snapshot, role, name) => {
  const match = new RegExp(`- ${role} "${name}"[^\\n]*\\[ref=(e\\d+)\\]`).exec(snapshot);
  assert.ok(match, `no ${role} "${name}" in the snapshot:\n${snapshot}`);
  return match[1];
};

async function waitFor(check, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}

for (const [name, spec] of Object.entries(ENGINES)) {
  const skip = { skip: spec.path ? false : `${name} is not installed` };

  test(`every Rem browser action works in ${/^[aeiou]/.test(name) ? "an" : "a"} ${name} tab`, skip, async () => {
    const { server, base } = await startSite();
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-actions-test-`));
    const engine = spec.engine(dataDir);
    const { controller, tabs } = harness(spec, engine);
    const call = (action, extra = {}) => controller.handle({ agent: "agent-actions-0001", action, ...extra });
    try {
      const opened = await call("open", { url: `${base}/` });
      assert.equal(opened.title, "Actions 1");
      assert.deepEqual((await call("tabs")).tabs.map((tab) => tab.tab), [opened.tab]);
      const page = tabs[0].contents;
      const out = async () => (await call("evaluate", { expression: "document.getElementById('out').textContent" })).result;
      let { snapshot } = await call("snapshot");

      // Hover reaches the page as real pointer movement.
      await call("hover", { ref: ref(snapshot, "button", "Hover") });
      await waitFor(async () => /hovered:true/.test(await out()), "hover did not reach the page");

      // Right, middle and double clicks.
      await call("click", { ref: ref(snapshot, "button", "Mouse"), button: "right" });
      await call("click", { ref: ref(snapshot, "button", "Mouse"), button: "middle" });
      await call("click", { ref: ref(snapshot, "button", "Mouse"), doubleClick: true });
      await waitFor(async () => /down:2:true.*menu.*down:1:true.*down:0:true.*down:0:true.*double:true/.test(await out()), `clicks did not reach the page: ${await out()}`);

      // Typing, keys with modifiers, and Enter submitting the form.
      const query = ref(snapshot, "textbox", "Query");
      await call("type", { ref: query, text: "hello" });
      assert.equal((await call("evaluate", { expression: "document.getElementById('q').value" })).result, '"hello"');
      await call("press", { key: "Backspace" });
      await call("press", { key: "Control+a" });
      await call("press", { key: "X" });
      assert.equal((await call("evaluate", { expression: "document.getElementById('q').value" })).result, '"X"', "Control+a should select the text, so X replaces it");
      await waitFor(async () => /key:a\+ctrl:true/.test(await out()), `Control+a did not reach the page: ${await out()}`);
      const submitted = await call("type", { ref: query, text: "go", submit: true });
      await waitFor(async () => (await call("tabs")).tabs[0].title === "submitted:go", `Enter did not submit: ${submitted.title}`);
      const tabbed = await call("press", { key: "Tab" });
      assert.notEqual(tabbed.focused, "input#q", "Tab moved focus on");

      // Drop-down lists, single and multiple.
      ({ snapshot } = await call("snapshot"));
      assert.deepEqual((await call("select", { ref: ref(snapshot, "combobox", "One"), values: ["blue"] })).selected, ["blue"]);
      assert.deepEqual((await call("select", { ref: ref(snapshot, "listbox", "Many"), values: ["a", "c"] })).selected, ["a", "c"]);
      assert.equal((await call("evaluate", { expression: "document.getElementById('one').value" })).result, '"blue"');

      // Waiting for text to appear and to go.
      await call("click", { ref: ref(snapshot, "button", "Later") });
      await call("wait", { text: "Arrived late", time: 5 });
      await call("wait", { textGone: "Leaving soon", time: 5 });
      await assert.rejects(call("wait", { text: "Never here", time: 1 }), /did not appear/);
      await call("wait", { time: 0.2 });

      // A prompt answered with typed text.
      ({ snapshot } = await call("snapshot"));
      const asked = await call("click", { ref: ref(snapshot, "button", "Ask") });
      assert.deepEqual(asked.dialog, { type: "prompt", message: "Name?", defaultPrompt: "x" });
      await assert.rejects(call("snapshot"), /dialog is open/);
      await call("dialog", { accept: true, promptText: "Ada" });
      await waitFor(async () => /prompt:Ada/.test(await out()), `the prompt answer did not reach the page: ${await out()}`);

      // Reload, then back and forward through history.
      const reloaded = await call("reload");
      await waitFor(async () => /^Actions [2-9]/.test((await call("tabs")).tabs[0].title), `reload did not load the page again: ${reloaded.title}`);
      ({ snapshot } = await call("snapshot"));
      const second = await call("click", { ref: ref(snapshot, "link", "Two") });
      await waitFor(async () => (await call("tabs")).tabs[0].title === "Two", `the link did not open: ${second.title}`);
      await call("back");
      await waitFor(async () => /^Actions/.test((await call("tabs")).tabs[0].title), "back did not return");
      await call("forward");
      await waitFor(async () => (await call("tabs")).tabs[0].title === "Two", "forward did not return to the second page");
      await assert.rejects(call("forward"), /no page to go forward/);

      const shot = await call("screenshot");
      assert.ok(shot.image.length > 1000);
      await call("close");
      assert.equal(page.isDestroyed(), true);
      await assert.rejects(call("snapshot"), /No browser tab is open/);
    } finally {
      for (const tab of tabs) tab.contents.close();
      await (engine.stopAndWait?.() ?? engine.stop());
      server.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  test(`Rem can work in ${/^[aeiou]/.test(name) ? "an" : "a"} ${name} tab the user shared, and the user's sign-ins stay theirs`, skip, async () => {
    const { server, base } = await startSite();
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-shared-test-`));
    const engine = spec.engine(dataDir);
    const user = spec.contents(engine, "user");
    const { controller } = harness(spec, engine, { sharedContents: user });
    const call = (action, extra = {}) => controller.handle({ agent: "agent-shared-0001", action, ...extra });
    try {
      await user.loadURL(`${base}/`);
      const { snapshot } = await call("snapshot");
      await call("type", { ref: ref(snapshot, "textbox", "Query"), text: "shared", submit: true });
      await waitFor(async () => (await call("tabs")).tabs[0].title === "submitted:shared", "Rem's typing did not reach the shared tab");
      assert.equal((await call("tabs")).tabs[0].sharedByUser, true);
      const shot = await call("screenshot");
      assert.ok(shot.image.length > 1000);
    } finally {
      user.close();
      await (engine.stopAndWait?.() ?? engine.stop());
      server.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
}

// Chrome's certificate warning has an in-page "Proceed to … (unsafe)" link,
// and Firefox's has Advanced, then "Accept the Risk and Continue" (before the
// fix, Rem reached the page behind Firefox's warning with two clicks).
// Raticode's own tabs never let Rem past a certificate error, so these tabs
// refuse Rem's page tools on the browser's error pages, in Rem's own tabs and
// in tabs the user shared. The user can still decide in their own tab.
const OPENSSL = require("node:child_process").spawnSync("openssl", ["version"]).status === 0;
for (const [name, label] of [["chrome", "Chrome"], ["edge", "Edge"], ["firefox", "Firefox"]]) {
  test(`Rem can't get past ${label}'s certificate warning, but can leave it`, { skip: ENGINES[name].path && OPENSSL ? false : `Needs ${label} and openssl` }, async () => {
    const https = require("node:https");
    const { spawnSync } = require("node:child_process");
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-cert-test-`));
    const key = path.join(dataDir, "key.pem");
    const cert = path.join(dataDir, "cert.pem");
    spawnSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=localhost"], { stdio: "ignore" });
    const secure = https.createServer({ key: fs.readFileSync(key), cert: fs.readFileSync(cert) }, (_request, response) => {
      response.setHeader("Content-Type", "text/html");
      response.end("<title>Behind the warning</title>reached");
    });
    await new Promise((resolve) => secure.listen(0, "127.0.0.1", resolve));
    const { server, base } = await startSite();
    const unsafe = `https://localhost:${secure.address().port}/`;
    const spec = ENGINES[name];
    const engine = spec.engine(dataDir);
    const user = spec.contents(engine, "user");
    const { controller, tabs } = harness(spec, engine, { sharedContents: user });
    const rem = (action, extra = {}) => controller.handle({ agent: "agent-actions-0001", action, ...extra });
    const shared = (action, extra = {}) => controller.handle({ agent: "agent-shared-0001", action, ...extra });
    try {
      await assert.rejects(rem("open", { url: unsafe }), /ERR_CERT_AUTHORITY_INVALID|SELF_SIGNED_CERT/);
      for (const action of ["snapshot", "evaluate"]) {
        await assert.rejects(rem(action, { expression: "document.title" }), /own error or warning page/);
      }
      await assert.rejects(rem("click", { selector: "#details-button" }), /own error or warning page/);
      await assert.rejects(rem("press", { key: "Enter" }), /own error or warning page/);
      assert.notEqual(tabs[0].contents.getTitle(), "Behind the warning");
      // Rem can leave the warning and carry on.
      const away = await rem("navigate", { url: `${base}/two` });
      assert.equal(away.title, "Two");
      assert.match((await rem("snapshot")).snapshot, /second/);

      // The same in a tab the user shared with Rem.
      await user.loadURL(unsafe).catch(() => {});
      await assert.rejects(shared("snapshot"), /own error or warning page/);
      // Once Rem stops using the tab, the user's own tab works as Chrome's does.
      user.debugger.detach();
      // Chrome says "is not private"; Edge says "isn't private"; Firefox's page is titled
      // "Problem loading page" until it fills in "Security Risk". Firefox fills its
      // warning's text in after the page loads, which takes a while on a busy machine.
      const warning = /(?:not|n[’']t) private|security risk|problem loading page/i;
      let text = "";
      for (const deadline = Date.now() + 15000; Date.now() < deadline; await new Promise((resolve) => setTimeout(resolve, 250))) {
        text = await user.executeJavaScriptInIsolatedWorld(1, [{ code: "document.title + ' ' + document.body.innerText" }]);
        if (warning.test(text)) break;
      }
      assert.match(text, warning);
    } finally {
      for (const tab of tabs) tab.contents.close();
      await engine.stopAndWait();
      server.close();
      secure.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
}

test("a Firefox reload never sends the ignoreCache argument Firefox refuses, and a hard reload falls back", async () => {
  const sent = [];
  const { EventEmitter } = require("node:events");
  const engine = Object.assign(new EventEmitter(), {
    createContext: async () => ({ context: "c1", generation: 1 }),
    register() {},
    unregister() {},
    send: async (method, params) => {
      sent.push([method, params]);
      if (method === "browsingContext.reload" && "ignoreCache" in params) throw new Error('Argument "ignoreCache" is not supported yet.');
      return {};
    },
  });
  const contents = new FirefoxContents({ engine, kind: "user" });
  await contents.ready;
  await contents.reloadPage(false);
  await contents.reloadPage(true);
  const reloads = sent.filter(([method]) => method === "browsingContext.reload").map(([, params]) => params);
  assert.deepEqual(reloads, [
    { context: "c1", wait: "none" },
    { context: "c1", wait: "none", ignoreCache: true },
    { context: "c1", wait: "none" },
  ]);
  contents.close();
});
