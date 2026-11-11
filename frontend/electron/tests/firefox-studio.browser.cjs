// Firefox as the web browser, end to end through the desktop entry point:
// the choice in Settings > Browser, a web tab drawn from Firefox's stream with
// the user's clicks and typing passed back, and Rem driving a Firefox tab in
// the background. Firefox runs headless; Electron runs on a hidden display.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app } = require("electron");
const { findFirefox } = require("../firefox-engine.cjs");
const { writePrefs } = require("../firefox-profile.cjs");
const { zip } = require("./zip-fixture.cjs");

if (!findFirefox()) {
  console.log("Firefox is not installed; skipping the Firefox studio test.");
  app.exit(0);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-firefox-studio-test-"));
// The runner checks that no Firefox from this profile outlives the app.
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
// A stand-in home folder with one everyday Firefox profile to import from;
// the real user's Firefox profiles are never read by this test.
const fakeHome = path.join(profile, "home");
const everyday = path.join(fakeHome, ".mozilla", "firefox", "abcd1234.default-release");
fs.mkdirSync(path.join(everyday, "extensions"), { recursive: true });
writePrefs(path.join(everyday, "prefs.js"), new Map([["font.size.variable.x-western", 22], ["services.sync.username", "person@example.com"]]));
fs.writeFileSync(path.join(everyday, "places.sqlite"), "");
fs.writeFileSync(path.join(everyday, "extensions", "blocker@example.com.xpi"), zip({ "manifest.json": JSON.stringify({ manifest_version: 2, name: "Blocker", version: "1.0", browser_specific_settings: { gecko: { id: "blocker@example.com" } } }) }));
fs.writeFileSync(path.join(everyday, "extensions.json"), JSON.stringify({ addons: [{ id: "blocker@example.com", type: "extension", location: "app-profile", version: "1.0", active: true, defaultLocale: { name: "Blocker" } }] }));
fs.writeFileSync(path.join(fakeHome, ".mozilla", "firefox", "profiles.ini"), "[Profile0]\nName=default-release\nIsRelative=1\nPath=abcd1234.default-release\n");
process.env.HOME = fakeHome;
process.env.XDG_CONFIG_HOME = path.join(fakeHome, ".config");
const timer = setTimeout(() => { console.error("Firefox studio test timed out"); app.exit(1); }, 180000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
const site = http.createServer((request, response) => {
  response.setHeader("Content-Type", "text/html");
  if (request.url === "/font") {
    response.end("<p id=p>text</p><script>document.title = 'font:' + getComputedStyle(document.getElementById('p')).fontSize</script>");
    return;
  }
  if (request.url === "/rem") {
    response.end("<title>Rem page</title><label>Query <input id=q></label><button onclick=\"document.title='done:'+document.getElementById('q').value\">Run</button>");
    return;
  }
  response.end(`<title>Firefox page</title><body style="margin:0">
    <input id=field style="position:absolute;left:20px;top:20px;width:300px;height:40px" oninput="document.title='typed:'+this.value">
    <button style="position:absolute;left:20px;top:100px;width:200px;height:60px" onclick="document.title='clicked:'+event.isTrusted+':'+navigator.userAgent.includes('Firefox')">Press</button>
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

function firefoxPids() {
  if (process.platform !== "linux") return [];
  return fs.readdirSync("/proc").filter((entry) => /^\d+$/.test(entry)).filter((pid) => {
    try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(profile); } catch { return false; }
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

  // Choose Firefox in Settings > Browser, as the user would.
  await page("document.querySelector('[aria-label=\"Open settings\"]').click()");
  await waitFor(() => page("Boolean(document.querySelector('nav[aria-label=\"Settings categories\"]'))"), "Settings did not open");
  await page("[...document.querySelectorAll('nav[aria-label=\"Settings categories\"] button')].find((item) => item.textContent.trim() === 'Browser').click()");
  await waitFor(() => page("Boolean(document.querySelector('select[aria-label=\"Default browser for new tabs\"]'))"), "The web browser setting is missing");
  await waitFor(() => page("/Firefox found at/.test(document.body.innerText)"), "Settings did not report the installed Firefox");
  await page(`(() => {
    const select = document.querySelector('select[aria-label="Default browser for new tabs"]');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, "firefox");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await waitFor(async () => JSON.parse(await page("localStorage.getItem('raticode.settings.v1')") || "{}").browser?.engine === "firefox", "The choice was not saved");

  // Firefox's own settings, extensions and importing appear with Firefox chosen.
  // Raticode's confirmation dialogs answer "OK"; nobody can click them on a hidden display.
  const { dialog } = require("electron");
  const confirmations = [];
  dialog.showMessageBox = async (...args) => { confirmations.push(args.at(-1)); return { response: 0 }; };
  await waitFor(() => page("Boolean(document.querySelector('[data-testid=\"firefox-settings\"]'))"), "The Firefox section did not appear");
  await waitFor(() => page("Boolean(document.getElementById('firefox-pref-font-size-variable-x-western'))"), "Firefox settings did not load");
  await page(`(() => {
    const input = document.getElementById("firefox-pref-font-size-variable-x-western");
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "19");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.blur();
  })()`);
  await waitFor(async () => {
    const data = await page("window.goferBrowser.library('firefox-settings')");
    const font = data.settings.find((item) => item.pref === "font.size.variable.x-western");
    return font.value === 19 && font.source === "raticode";
  }, "The Firefox font size setting was not saved");
  const tab = (name) => page(`[...document.querySelectorAll('[data-testid="firefox-settings"] [role=tab]')].find((item) => item.textContent === ${JSON.stringify(name)}).click()`);
  const press = (label) => page(`[...document.querySelectorAll('[data-testid="firefox-settings"] button')].find((item) => item.textContent.trim() === ${JSON.stringify(label)}).click()`);
  await tab("Import");
  await press("Find my Firefox profiles");
  await waitFor(() => page("Boolean(document.querySelector('input[name=\"firefox-profile\"]'))"), "The everyday profile was not found");
  await page("document.querySelector('input[name=\"firefox-profile\"]').click()");
  await waitFor(() => page("/Bookmarks and history/.test(document.querySelector('[aria-label=\"What to import\"]')?.innerText || '')"), "The profile's contents were not listed");
  await page("[...document.querySelectorAll('[aria-label=\"What to import\"] button')].find((item) => item.textContent.trim() === 'Import').click()");
  await waitFor(() => page("/Import finished/.test(document.querySelector('[data-testid=\"firefox-settings\"]').innerText)"), "The import did not finish", 60000)
    .catch(async (error) => { throw new Error(`${error.message}: ${await page("document.querySelector('[data-testid=\"firefox-settings\"]').innerText")}`); });
  const report = await page("document.querySelector('[data-testid=\"firefox-settings\"]').innerText");
  assert.match(report, /Blocker wasn’t added: it is not signed by Mozilla/, report);
  assert.ok(confirmations.some((options) => /only reads that profile/.test(options.detail)), "The import was not confirmed first");
  assert.equal(fs.readFileSync(path.join(everyday, "prefs.js"), "utf8").includes("person@example.com"), true, "The everyday profile changed");
  await tab("Extensions");
  await waitFor(() => page("/No extensions yet/.test(document.querySelector('[data-testid=\"firefox-settings\"]').innerText)"), "The extension list did not load", 30000);
  await page("document.querySelector('[aria-label=\"Open settings\"]').click()");

  // The setting chosen in Raticode wins over the imported one.
  window.webContents.send("gofer:browser-open-tab", { url: `http://127.0.0.1:${site.address().port}/font`, background: false });
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("font:19px"), "Firefox did not use the font size chosen in Raticode", 30000);

  // A web tab now streams from Firefox.
  const base = `http://127.0.0.1:${site.address().port}`;
  window.webContents.send("gofer:browser-open-tab", { url: `${base}/`, background: false });
  await waitFor(() => page("Number(document.querySelector('[data-engine=\"firefox\"]')?.dataset.frames || 0) > 0"), "No Firefox frame was drawn", 30000);
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("Firefox page"), "The tab strip did not show the Firefox page title");
  // Hidden tabs have no visible text; the active one carries the Firefox label.
  assert.equal(await page("[...document.querySelectorAll('[data-integrated-browser]')].some((item) => item.innerText.includes('Firefox'))"), true);
  assert.equal(await page("document.querySelectorAll('webview').length"), 0);

  // The user's click and typing reach Firefox through the studio.
  const box = await page("JSON.parse(JSON.stringify(document.querySelector('[data-engine=\"firefox\"] canvas').getBoundingClientRect()))");
  const click = (x, y) => {
    window.webContents.sendInputEvent({ type: "mouseDown", x: Math.round(box.x + x), y: Math.round(box.y + y), button: "left", clickCount: 1 });
    window.webContents.sendInputEvent({ type: "mouseUp", x: Math.round(box.x + x), y: Math.round(box.y + y), button: "left", clickCount: 1 });
  };
  click(100, 40);
  await waitFor(() => page("document.activeElement?.getAttribute('aria-label') === 'Firefox page'"), "The Firefox view did not take keyboard focus");
  for (const key of ["h", "i"]) {
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: key });
    window.webContents.sendInputEvent({ type: "char", keyCode: key });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: key });
  }
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("typed:hi"), "Typing did not reach the Firefox page");
  click(100, 130);
  await waitFor(async () => (await page("[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join('|')")).includes("clicked:true:true"), "The click did not reach Firefox as trusted input");

  // Rem works in its own Firefox tab in the background; the user's focus stays put.
  const service = await waitFor(() => globalThis.__raticodeTestHooks?.agentBrowserService, "Browser service did not start");
  const grant = await (await fetch(`${service.url}/admin`, {
    method: "POST",
    headers: { Authorization: `Bearer ${service.adminToken}` },
    body: JSON.stringify({ action: "grant", owner: "agent-firefox-thread-01", ttlSeconds: 600 }),
  })).json();
  const call = async (action, extra = {}) => {
    const response = await fetch(service.url, { method: "POST", headers: { Authorization: `Bearer ${grant.capability}` }, body: JSON.stringify({ action, ...extra }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    return body;
  };
  await page(`(() => { const field = document.createElement("input"); field.id = "user-field"; document.body.appendChild(field); field.focus(); })()`);
  const firefoxViews = () => page("document.querySelectorAll('[data-engine=\"firefox\"]').length");
  const before = await firefoxViews();
  const opened = await call("open", { url: `${base}/rem` });
  assert.equal(opened.title, "Rem page");
  await waitFor(() => (async () => (await firefoxViews()) === before + 1)(), "Rem's Firefox tab did not join the studio");
  const { snapshot } = await call("snapshot");
  const ref = (pattern) => snapshot.match(pattern)[1];
  await call("type", { ref: ref(/- textbox "Query" \[ref=(e\d+)\]/), text: "firefox" });
  const clicked = await call("click", { ref: ref(/- button "Run" \[ref=(e\d+)\]/) });
  assert.equal(clicked.title, "done:firefox");
  assert.equal(await page("document.activeElement.id"), "user-field");
  await call("close");
  await waitFor(() => (async () => (await firefoxViews()) === before)(), "Closing Rem's tab did not remove it");

  if (process.platform === "linux") assert.ok(firefoxPids().length >= 1, "Firefox should run from Raticode's own profile");
  assert.ok(fs.statSync(path.join(profile, "firefox-profile")).isDirectory(), "Firefox uses a profile inside Raticode's data folder");

  clearTimeout(timer);
  console.log("Firefox in the studio passed.");
  api.close();
  site.close();
  // A normal quit stops Firefox; the runner confirms none is left.
  setTimeout(() => app.exit(0), 15000).unref();
  app.quit();
}

run().catch((error) => { console.error(error); app.exit(1); });
