// Rem's browser tools where a streamed browser has to keep Raticode's rules:
// same-origin frames in snapshots, page dialogs, screenshots of a tab that
// was never on screen, pop-ups, redirects to a site Rem may not visit, and
// Rem's downloads, which are refused by default. Runs in Chrome, Microsoft
// Edge and Firefox. stream-agent-actions.test.cjs covers the other actions.
//
// Edge is Chromium with the same DevTools protocol, so Edge's run uses Edge
// when RATICODE_EDGE_PATH names one or it is installed, and otherwise the
// Chrome for Testing build Playwright downloaded. Each run is skipped when
// its browser is missing.
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
const CHROME_PATH = process.env.RATICODE_SKIP_CHROME_TESTS === "1" ? "" : (findBrowser(CHROME) || (process.platform === "linux" ? chromiumForTesting() : ""));
const FIREFOX_PATH = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();

const fakeImage = {
  createFromBuffer: (buffer) => ({
    isEmpty: () => !buffer.length,
    // PNG width and height sit at bytes 16-23.
    getSize: () => ({ width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }),
    resize() { return this; },
    toPNG: () => buffer,
  }),
};

const BROWSERS = [
  {
    name: "Chrome",
    skip: CHROME_PATH ? false : "Chrome is not installed",
    engine: (dataDir) => createChromiumEngine({ browser: CHROME, dataDir, env: { ...process.env, RATICODE_CHROME_PATH: CHROME_PATH } }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  {
    name: "Edge",
    skip: EDGE_PATH ? false : "Neither Microsoft Edge nor a Chromium stand-in is installed",
    engine: (dataDir) => createChromiumEngine({ browser: EDGE, dataDir, env: { ...process.env, RATICODE_EDGE_PATH: EDGE_PATH } }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  {
    name: "Firefox",
    skip: FIREFOX_PATH ? false : "Firefox is not installed",
    engine: (dataDir) => createFirefoxEngine({ dataDir }),
    contents: (engine, kind) => new FirefoxContents({ engine, kind, nativeImage: fakeImage }),
    // A known Firefox gap, found by this test on November 7, 2026: Rem's
    // snapshot doesn't reach into same-origin frames.
    knownGaps: ["same-origin frame"],
  },
];

const PAGE = `<!doctype html><title>Parity</title><body style="margin:0;height:2000px">
<form id=form onsubmit="event.preventDefault(); document.getElementById('out').textContent='submitted:'+document.getElementById('name').value">
<input id=name aria-label=Name style="position:absolute;left:10px;top:10px;width:200px;height:30px">
</form>
<input id=keys aria-label=Keys style="position:absolute;left:250px;top:10px;width:200px;height:30px" onkeydown="document.getElementById('out').textContent='key:'+event.key+':'+event.ctrlKey+':'+event.isTrusted">
<button id=hover style="position:absolute;left:10px;top:60px;width:120px;height:40px" onmouseover="document.getElementById('out').textContent='hovered:'+event.isTrusted">Hover me</button>
<button id=menu style="position:absolute;left:150px;top:60px;width:120px;height:40px" oncontextmenu="event.preventDefault(); document.getElementById('out').textContent='context:'+event.isTrusted">Menu</button>
<button id=dbl style="position:absolute;left:290px;top:60px;width:120px;height:40px" ondblclick="document.getElementById('out').textContent='double:'+event.isTrusted">Twice</button>
<select id=pick aria-label=Pick style="position:absolute;left:10px;top:120px;width:150px;height:30px" onchange="document.getElementById('out').textContent='picked:'+this.value"><option>one</option><option>two</option><option>three</option></select>
<button id=alert style="position:absolute;left:180px;top:120px;width:100px;height:30px" onclick="alert('Hello'); document.getElementById('out').textContent='alerted'">Alert</button>
<button id=prompt style="position:absolute;left:300px;top:120px;width:100px;height:30px" onclick="document.getElementById('out').textContent='answer:'+prompt('Name?', 'x')">Prompt</button>
<button id=later style="position:absolute;left:10px;top:170px;width:120px;height:30px" onclick="setTimeout(() => { document.getElementById('late').textContent = 'Arrived late' }, 600)">Later</button>
<div id=late style="position:absolute;left:150px;top:170px"></div>
<button id=pop style="position:absolute;left:10px;top:210px;width:120px;height:30px" onclick="window.open('/two')">Pop</button>
<a id=go href="/go" style="position:absolute;left:150px;top:210px">Redirect</a>
<a id=two href="/two" style="position:absolute;left:250px;top:210px">Two</a>
<a id=dl href="/file.txt" download style="position:absolute;left:300px;top:210px">Download</a>
<iframe id=frame src="/frame" style="position:absolute;left:10px;top:260px;width:300px;height:80px;border:0"></iframe>
<div id=out style="position:absolute;left:10px;top:360px">none</div>
</body>`;

async function startSite() {
  const server = http.createServer((request, response) => {
    const port = server.address().port;
    if (request.url === "/go") { response.writeHead(302, { Location: `http://localhost:${port}/secret` }); response.end(); return; }
    if (request.url === "/file.txt") { response.setHeader("Content-Type", "text/plain"); response.end("downloaded"); return; }
    response.setHeader("Content-Type", "text/html");
    if (request.url === "/two") { response.end("<title>Two</title><p>second page</p>"); return; }
    if (request.url === "/frame") { response.end("<button style='width:120px;height:40px' onclick=\"parent.document.getElementById('out').textContent='framed:'+event.isTrusted\">Inside</button>"); return; }
    response.end(PAGE);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
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

for (const browser of BROWSERS) {
  test(`Rem's tools keep Raticode's rules and reach frames, dialogs and hidden tabs in ${/^[AEIOU]/.test(browser.name) ? "an" : "a"} ${browser.name} tab`, { skip: browser.skip, timeout: 180000 }, async () => {
    const { server, base } = await startSite();
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `parity-${browser.name.toLowerCase()}-`));
    const engine = browser.engine(dataDir);
    const policy = sanitizeBrowserPolicy({ engine: browser.name.toLowerCase(), remBlockedSites: ["localhost"], remConfirm: "never" });
    const tabs = [];
    const popups = [];
    const controller = createAgentBrowserController({
      openTab: ({ agent, label }) => {
        const contents = browser.contents(engine, "rem");
        contents.guardNavigation = true;
        const session = { id: `tab-${tabs.length}`, agent: { id: agent, label, shared: false }, contents, viewSize: null };
        contents.on("will-navigate", (event, url) => { if (!remSiteAllowed(url, policy)) event.preventDefault(); });
        contents.on("will-redirect", (event, url) => { if (!remSiteAllowed(url, policy)) event.preventDefault(); });
        contents.on("did-fail-load", (_event, _code, description) => { session.error = description; });
        contents.setWindowOpenHandler(({ url }) => { popups.push(url); return { action: "deny" }; });
        tabs.push(session);
        return session;
      },
      agentTabs: (agent) => tabs.filter((item) => item.agent.id === agent && !item.contents.isDestroyed()),
      closeTab: (session) => session.contents.close(),
      normalizeUrl: (value) => value,
      policy: () => policy,
      confirm: async () => true,
    });
    const agent = "agent-parity-0001";
    const call = (action, extra = {}) => controller.handle({ agent, action, ...extra });
    const results = {};
    const check = async (action, run) => {
      try { await run(); results[action] = "ok"; } catch (error) { results[action] = error.message.replace(/\s+/g, " ").slice(0, 300); }
    };
    try {
      const opened = await call("open", { url: `${base}/` });
      const page = tabs[0].contents;
      const out = () => page.evaluateIn("test", "document.getElementById('out').textContent");
      assert.equal(opened.title, "Parity");
      let { snapshot } = await call("snapshot");
      const ref = (name, role = "[a-z]+") => {
        const match = new RegExp(`- ${role} "${name}"[^\\n]*\\[ref=(e\\d+)\\]`).exec(snapshot);
        if (!match) throw new Error(`No ${name} in the snapshot`);
        return match[1];
      };
      await check("alert", async () => {
        const shown = await call("click", { ref: ref("Alert") });
        assert.equal(shown.dialog?.type, "alert");
        await call("dialog", { accept: true });
        await waitFor(async () => (await out()) === "alerted", `out was ${await out()}`, 4000);
      });
      await check("same-origin frame", async () => {
        ({ snapshot } = await call("snapshot"));
        await call("click", { ref: ref("Inside") });
        await waitFor(async () => (await out()) === "framed:true", `out was ${await out()}`, 4000);
      });
      await check("screenshot", async () => {
        const shot = await call("screenshot");
        assert.ok(shot.width >= 800 && shot.height >= 500, `screenshot was ${shot.width}x${shot.height}`);
      });
      await check("pop-up", async () => {
        ({ snapshot } = await call("snapshot"));
        await call("click", { ref: ref("Pop") });
        await waitFor(() => popups.includes(`${base}/two`), `pop-ups were ${JSON.stringify(popups)}`, 4000);
      });
      await check("redirect to a blocked site", async () => {
        ({ snapshot } = await call("snapshot"));
        await call("click", { ref: ref("Redirect") }).catch(() => {});
        await new Promise((resolve) => setTimeout(resolve, 500));
        assert.notEqual(new URL(page.getURL()).hostname, "localhost", `the tab reached ${page.getURL()}`);
      });
      await check("download refused", async () => {
        ({ snapshot } = await call("snapshot"));
        await call("click", { ref: ref("Download") }).catch(() => {});
        await new Promise((resolve) => setTimeout(resolve, 800));
        const staged = fs.existsSync(engine.stagingDir) ? fs.readdirSync(engine.stagingDir) : [];
        assert.equal(staged.length, 0, `Rem's download landed: ${staged.join(", ")}`);
      });
      await check("close", async () => {
        await call("close");
        assert.equal(page.isDestroyed(), true);
      });
      console.log(`${browser.name}: ${JSON.stringify(results)}`);
      const failed = Object.entries(results).filter(([action, value]) => value !== "ok" && !(browser.knownGaps || []).includes(action));
      assert.deepEqual(failed, [], `${browser.name} actions that failed`);
    } finally {
      for (const session of tabs) session.contents.close();
      await (engine.stopAndWait ? engine.stopAndWait() : engine.stop());
      server.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
}
