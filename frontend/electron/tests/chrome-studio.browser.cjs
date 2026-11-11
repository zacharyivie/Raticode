// Chrome as the web browser, end to end through the desktop entry point: the
// choice in Settings > Browser with its Chrome section, a web tab drawn from
// Chrome's stream with the user's clicks and typing passed back, and Rem
// driving a Chrome tab in the background. Chrome runs headless; Electron runs
// on a hidden display.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app } = require("electron");
const { CHROME, findBrowser } = require("../chromium-engine.cjs");

// A Chrome for Testing build that Playwright downloaded stands in for Chrome
// when Google Chrome itself isn't installed.
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

if (!findBrowser(CHROME)) {
  const stand = process.platform === "linux" ? chromeForTesting() : "";
  if (!stand) {
    console.log("Chrome is not installed; skipping the Chrome studio test.");
    app.exit(0);
  }
  process.env.RATICODE_CHROME_PATH = stand;
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-chrome-studio-test-"));
// The runner checks that no Chrome from this profile outlives the app.
console.log(`RATICODE_TEST_PROFILE=${profile}`);
app.setPath("userData", profile);
fs.mkdirSync(path.join(profile, "logs"));
app.setPath("logs", path.join(profile, "logs"));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
process.env.GOFER_ELECTRON_MODE = "production";
process.env.GOFER_DATA_DIR = path.join(profile, "data");
process.env.GOFER_ELECTRON_TEST_HOOKS = "1";
const timer = setTimeout(() => { console.error("Chrome studio test timed out"); app.exit(1); }, 180000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
const site = http.createServer((request, response) => {
  response.setHeader("Content-Type", "text/html");
  if (request.url === "/rem") {
    response.end("<title>Rem page</title><label>Query <input id=q></label><button onclick=\"document.title='done:'+document.getElementById('q').value\">Run</button>");
    return;
  }
  response.end(`<title>Chrome page</title><body style="margin:0">
    <input id=field style="position:absolute;left:20px;top:20px;width:300px;height:40px" oninput="document.title='typed:'+this.value">
    <button style="position:absolute;left:20px;top:100px;width:200px;height:60px" onclick="document.title='clicked:'+event.isTrusted+':'+navigator.userAgent.includes('HeadlessChrome')">Press</button>
  </body>`);
});

async function waitFor(check, message, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

function chromePids() {
  if (process.platform !== "linux") return [];
  return fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
    try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(path.join(profile, "chrome-profile")); } catch { return false; }
  });
}

async function run() {
  await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  process.env.GOFER_API_BASE_URL = `http://127.0.0.1:${api.address().port}`;
  const created = new Promise((resolve) => app.once("browser-window-created", (_event, window) => resolve(window)));
  await import("../main.js");
  const window = await created;
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  const page = (code) => window.webContents.executeJavaScript(code);
  await waitFor(() => page("document.querySelector('#root')?.children.length > 0"), "Studio did not render");

  // Choose Chrome in Settings > Browser, as the user would.
  await page("document.querySelector('[aria-label=\"Open settings\"]').click()");
  await waitFor(() => page("Boolean(document.querySelector('nav[aria-label=\"Settings categories\"]'))"), "Settings did not open");
  await page("[...document.querySelectorAll('nav[aria-label=\"Settings categories\"] button')].find((item) => item.textContent.trim() === 'Browser').click()");
  await waitFor(() => page("Boolean(document.querySelector('select[aria-label=\"Default browser for new tabs\"]'))"), "The web browser setting is missing");
  await page(`(() => {
    const select = document.querySelector('select[aria-label="Default browser for new tabs"]');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, "chrome");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await waitFor(async () => JSON.parse(await page("localStorage.getItem('raticode.settings.v1')") || "{}").browser?.engine === "chrome", "The choice was not saved");
  await waitFor(() => page("/Google Chrome found at/.test(document.body.innerText)"), "Settings did not report the installed Chrome");
  await waitFor(() => page("Boolean(document.querySelector('[data-testid=\"chrome-settings\"]'))"), "The Chrome section did not appear");
  await waitFor(() => page("[...document.querySelectorAll('[data-testid=\"chrome-settings\"] button')].some((item) => item.textContent.trim() === 'Chrome settings' && !item.disabled)"), "The Chrome window buttons are not available");
  assert.equal(await page("Boolean(document.querySelector('[data-testid=\"firefox-settings\"]'))"), false);
  await page("document.querySelector('[aria-label=\"Open settings\"]').click()");

  // A web tab now streams from Chrome.
  const base = `http://127.0.0.1:${site.address().port}`;
  window.webContents.send("gofer:browser-open-tab", { url: `${base}/`, background: false });
  await waitFor(() => page("Number(document.querySelector('[data-engine=\"chrome\"]')?.dataset.frames || 0) > 0"), "No Chrome frame was drawn", 30000);
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("Chrome page"), "The tab strip did not show the Chrome page title");
  // Hidden tabs have no visible text; the active one carries the Chrome label.
  assert.equal(await page("[...document.querySelectorAll('[data-integrated-browser]')].some((item) => item.innerText.includes('Chrome'))"), true);
  assert.equal(await page("document.querySelectorAll('webview').length"), 0);

  // The user's click and typing reach Chrome through the studio.
  const box = await page("JSON.parse(JSON.stringify(document.querySelector('[data-engine=\"chrome\"] canvas').getBoundingClientRect()))");
  const click = (x, y) => {
    window.webContents.sendInputEvent({ type: "mouseDown", x: Math.round(box.x + x), y: Math.round(box.y + y), button: "left", clickCount: 1 });
    window.webContents.sendInputEvent({ type: "mouseUp", x: Math.round(box.x + x), y: Math.round(box.y + y), button: "left", clickCount: 1 });
  };
  click(100, 40);
  await waitFor(() => page("document.activeElement?.getAttribute('aria-label') === 'Chrome page'"), "The Chrome view did not take keyboard focus");
  for (const key of ["h", "i"]) {
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: key });
    window.webContents.sendInputEvent({ type: "char", keyCode: key });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: key });
  }
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("typed:hi"), "Typing did not reach the Chrome page");
  click(100, 130);
  // Trusted input, and the page doesn't see "HeadlessChrome".
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("clicked:true:false"), "The click did not reach Chrome as trusted input");

  // Rem works in its own Chrome tab in the background; the user's focus stays put.
  const service = await waitFor(() => globalThis.__raticodeTestHooks?.agentBrowserService, "Browser service did not start");
  const grant = await (await fetch(`${service.url}/admin`, {
    method: "POST",
    headers: { Authorization: `Bearer ${service.adminToken}` },
    body: JSON.stringify({ action: "grant", owner: "agent-chrome-thread-01", ttlSeconds: 600 }),
  })).json();
  const call = async (action, extra = {}) => {
    const response = await fetch(service.url, { method: "POST", headers: { Authorization: `Bearer ${grant.capability}` }, body: JSON.stringify({ action, ...extra }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    return body;
  };
  await page(`(() => { const field = document.createElement("input"); field.id = "user-field"; document.body.appendChild(field); field.focus(); })()`);
  const chromeViews = () => page("document.querySelectorAll('[data-engine=\"chrome\"]').length");
  const before = await chromeViews();
  const opened = await call("open", { url: `${base}/rem` });
  assert.equal(opened.title, "Rem page");
  await waitFor(() => (async () => (await chromeViews()) === before + 1)(), "Rem's Chrome tab did not join the studio");
  const { snapshot } = await call("snapshot");
  const ref = (pattern) => snapshot.match(pattern)[1];
  await call("type", { ref: ref(/- textbox "Query" \[ref=(e\d+)\]/), text: "chrome" });
  const clicked = await call("click", { ref: ref(/- button "Run" \[ref=(e\d+)\]/) });
  assert.equal(clicked.title, "done:chrome");
  assert.equal(await page("document.activeElement.id"), "user-field");
  await call("close");
  await waitFor(() => (async () => (await chromeViews()) === before)(), "Closing Rem's tab did not remove it");

  if (process.platform === "linux") assert.ok(chromePids().length >= 1, "Chrome should run from Raticode's own profile");
  assert.ok(fs.statSync(path.join(profile, "chrome-profile")).isDirectory(), "Chrome uses a profile inside Raticode's data folder");
  // No network control port.
  assert.equal(fs.existsSync(path.join(profile, "chrome-profile", "DevToolsActivePort")), false);

  clearTimeout(timer);
  console.log("Chrome in the studio passed.");
  api.close();
  site.close();
  // A normal quit stops Chrome; the runner confirms none is left.
  setTimeout(() => app.exit(0), 15000).unref();
  app.quit();
}

run().catch((error) => { console.error(error); app.exit(1); });
