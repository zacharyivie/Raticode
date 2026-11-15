// The Browser plugin in its own window, through the real desktop entry point
// and the built studio: Pop out moves the tab bar and every tab into a new
// window, closing that window or Bring browser back returns them, and the
// window reopens where it was left. A Raticode (chromium) tab keeps the same
// running page through both moves: its sound keeps playing, its counter keeps
// counting and a filled-in field keeps its text. While popped out, DevTools,
// a download, a permission request and the media bar work. Private tabs stay
// private, Rem keeps control of its tab, and with Firefox installed a
// Raticode (gecko) tab follows the window without its page reloading.
// Runs on a hidden display with its own profile, data folder and home folder.
// Needs a built dist/ (npm run build).
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app, BrowserWindow, webContents } = require("electron");
const { X11Client } = require("../x11-client.cjs");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-browser-window-test-"));
const home = path.join(profile, "home");
fs.mkdirSync(path.join(profile, "logs"), { recursive: true });
fs.mkdirSync(home, { recursive: true });
app.setPath("userData", path.join(profile, "user"));
app.setPath("logs", path.join(profile, "logs"));
const downloadsDir = path.join(profile, "Downloads");
fs.mkdirSync(downloadsDir);
app.setPath("downloads", downloadsDir);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
Object.assign(process.env, {
  GOFER_ELECTRON_MODE: "production",
  GOFER_DATA_DIR: path.join(profile, "data"),
  GOFER_ELECTRON_TEST_HOOKS: "1",
  HOME: home,
  XDG_CONFIG_HOME: path.join(home, ".config"),
  XDG_CACHE_HOME: path.join(home, ".cache"),
  XDG_DATA_HOME: path.join(home, ".local", "share"),
});
const timer = setTimeout(() => { console.error("Browser window test timed out"); app.exit(1); }, 240000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
// "/" counts up in its title from the moment it loads, so a reload shows.
const site = http.createServer((request, response) => {
  response.setHeader("Content-Type", "text/html");
  if (request.url === "/form") {
    // Counts, keeps what is typed, and plays a quiet tone once asked.
    response.end(`<title>Form page</title><body style="background:#2a7"><input id="field"><script>
      window.started = Date.now(); window.count = 0; setInterval(() => { window.count += 1; }, 50);
      window.play = () => { const context = new AudioContext(); const tone = context.createOscillator(); const gain = context.createGain(); gain.gain.value = 0.05; tone.connect(gain).connect(context.destination); tone.start(); window.sound = context; return context.state; };
    </script></body>`);
  } else if (request.url === "/file.txt") {
    response.setHeader("Content-Type", "text/plain");
    response.setHeader("Content-Disposition", "attachment; filename=\"popout-download.txt\"");
    response.end("downloaded while popped out");
  } else if (request.url === "/two") {
    response.end("<title>Pop two</title><body style=\"background:#2a7\">Second page</body>");
  } else if (request.url === "/agent") {
    response.end("<title>Agent page</title><body><button>Run</button></body>");
  } else {
    response.end("<title>Tick 0</title><body style=\"background:#2a7\">A page<script>let n = 0; setInterval(() => { n += 1; document.title = 'Tick ' + n; }, 250);</script></body>");
  }
});
const log = (message) => console.log(`[browser window] ${message}`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await check();
      if (last) return last;
    } catch (error) {
      last = error;
    }
    await wait(100);
  }
  throw new Error(`${message}${last instanceof Error ? ` (${last.message})` : ""}`);
}

async function run() {
  await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
  await new Promise((resolve) => site.listen(0, "127.0.0.1", resolve));
  const siteUrl = `http://127.0.0.1:${site.address().port}/`;
  process.env.GOFER_API_BASE_URL = `http://127.0.0.1:${api.address().port}`;
  const created = new Promise((resolve) => app.once("browser-window-created", (_event, window) => resolve(window)));
  await import("../main.js");
  const window = await created;
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  const page = (code) => window.webContents.executeJavaScript(code);
  await waitFor(() => page("document.querySelector('#root')?.children.length > 0"), "Studio did not render");
  await page(`localStorage.setItem("raticode.settings.v1", JSON.stringify({ appearance: { theme: "dark" }, browser: { homepage: ${JSON.stringify(siteUrl)} } }))`);
  window.webContents.reload();
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  await waitFor(() => page("Boolean(document.querySelector('.studio-sidebar #sidebar-tab-browser'))"), "Studio did not render after reload");
  await page("document.querySelector('.studio-sidebar #sidebar-tab-browser').click()");

  const inWindow = (target) => (code) => target.webContents.executeJavaScript(code);
  const click = (target, selector) => inWindow(target)(`(() => { const node = ${selector}; if (!node) return false; node.click(); return true; })()`);
  const byLabel = (label) => `[...document.querySelectorAll('[aria-label]')].find((node) => node.getAttribute('aria-label') === ${JSON.stringify(label)} && node.getClientRects().length)`;
  const family = (engine) => `document.querySelector('[data-browser-family="${engine}"]')`;
  const tabTitles = (target) => inWindow(target)(`(() => { const strip = document.querySelector('[aria-label="Browser tabs"]'); return strip && strip.getClientRects().length ? [...strip.querySelectorAll('[role=tab]')].map((node) => node.textContent) : null; })()`);
  // Raticode (chromium) tabs are views the main process owns.
  const guests = () => webContents.getAllWebContents().filter((contents) => !contents.isDestroyed() && contents.getType() === "window");
  const guestAt = (suffix) => guests().find((contents) => contents.getURL() === `${siteUrl}${suffix}`);
  const otherWindow = () => BrowserWindow.getAllWindows().find((item) => item !== window && !item.isDestroyed());

  // A Raticode (chromium) tab with some history, and a private tab.
  await waitFor(() => page("Boolean(document.querySelector('[data-testid=browser-empty]')?.offsetParent)"), "The empty Browser plugin did not show");
  await click(window, family("raticode"));
  await waitFor(async () => (await tabTitles(window))?.some((title) => /Tick \d+/.test(title)), "The chromium tab did not load the test page");
  const first = await waitFor(() => guestAt(""), "No view for the chromium tab");
  await first.loadURL(`${siteUrl}two`);
  await first.loadURL(`${siteUrl}form`);
  await waitFor(async () => (await tabTitles(window))?.some((title) => title.includes("Form page")), "The chromium tab did not go to the form page");
  assert.equal(first.navigationHistory.canGoBack(), true);
  // Something typed, a counter and a tone: all of it has to survive the moves.
  await first.executeJavaScript("document.getElementById('field').value = 'typed before moving'");
  await first.executeJavaScript("window.play()", true);
  await waitFor(() => first.isCurrentlyAudible(), "The chromium tab's tone didn't start");
  const pageState = () => first.executeJavaScript("({ started: window.started, count: window.count, field: document.getElementById('field').value, sound: window.sound?.state })");
  const startState = await pageState();
  // The same running page: started at the same moment, still counting, field and sound intact.
  const assertSamePage = async (where) => {
    assert.equal(first.isDestroyed(), false, `the chromium page survived ${where}`);
    assert.equal(first.getURL(), `${siteUrl}form`);
    const now = await pageState();
    assert.equal(now.started, startState.started, `the chromium page didn't reload ${where}`);
    assert.equal(now.field, "typed before moving", `the typed text survived ${where}`);
    assert.equal(now.sound, "running", `the tone kept playing ${where}`);
    assert.equal(first.isCurrentlyAudible(), true, `the tab is still audible ${where}`);
    const later = await waitFor(async () => { const value = (await pageState()).count; return value > now.count && value; }, `the chromium page stopped counting ${where}`);
    return later;
  };
  const viewIn = (target) => target.contentView.children.some((child) => child.webContents === first);
  await click(window, byLabel("Open a tab in another browser"));
  await waitFor(() => page(`Boolean(${family("private")})`), "The chooser did not offer a private tab");
  await click(window, family("private"));
  await waitFor(async () => (await tabTitles(window))?.filter((title) => title.startsWith("Private")).length === 1, "The private tab did not open");

  // A tab Rem opened and controls.
  const service = await waitFor(() => globalThis.__raticodeTestHooks?.agentBrowserService, "Browser service did not start");
  const admin = async (body) => (await fetch(`${service.url}/admin`, { method: "POST", headers: { Authorization: `Bearer ${service.adminToken}` }, body: JSON.stringify(body) })).json();
  const capability = (await admin({ action: "grant", owner: "agent-thread-window-0001", ttlSeconds: 600 })).capability;
  const rem = async (action, extra = {}) => {
    const response = await fetch(service.url, { method: "POST", headers: { Authorization: `Bearer ${capability}` }, body: JSON.stringify({ action, ...extra }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    return body;
  };
  assert.equal((await rem("open", { url: `${siteUrl}agent` })).title, "Agent page");
  await waitFor(async () => (await tabTitles(window))?.some((title) => title.includes("Agent page")), "Rem's tab did not appear");

  // Raticode (gecko), when Firefox can run here.
  const geckoStatus = () => page("window.goferBrowser.gecko('status')");
  const gecko = await geckoStatus();
  const withGecko = Boolean(gecko?.available && gecko?.supported);
  let display = null;
  const ticks = async () => Number(/Tick (\d+)/.exec((await geckoStatus())?.title || "")?.[1] ?? -1);
  // The X window Firefox's window currently sits in.
  const geckoParent = async () => {
    const roots = BrowserWindow.getAllWindows().filter((item) => !item.isDestroyed()).map((item) => item.getNativeWindowHandle().readUInt32LE(0));
    for (const root of roots) {
      const { children } = await display.queryTree(root);
      for (const child of children) {
        const windowClass = await display.text(child, "WM_CLASS").catch(() => "");
        if (windowClass.split("\0").includes("RaticodeGecko")) return root;
      }
    }
    return 0;
  };
  if (withGecko) {
    await click(window, byLabel("Open a tab in another browser"));
    await waitFor(() => page(`Boolean(${family("gecko")})`), "The chooser did not offer Raticode (gecko)");
    await click(window, family("gecko"));
    await waitFor(async () => (await geckoStatus())?.placed, "Firefox's window did not move into the Gecko tab", 60000);
    await waitFor(async () => (await ticks()) > 2, "Firefox did not open the counting page", 30000);
    display = await new X11Client().connect();
    assert.equal(await geckoParent(), window.getNativeWindowHandle().readUInt32LE(0));
  } else {
    log(`Raticode (gecko) can't run here (${gecko?.problem || (gecko?.executable ? "unsupported display" : "no Firefox")}); skipping its part.`);
  }
  const before = await tabTitles(window);
  log(`tabs before: ${JSON.stringify(before)}`);

  // Pop out from the tab bar's button.
  const geckoBefore = withGecko ? await ticks() : 0;
  assert.equal(await click(window, `document.querySelector('[data-testid=browser-window-toggle]')`), true, "the tab bar has a Pop out button");
  const popout = await waitFor(otherWindow, "Pop out did not open a window");
  const inPopout = inWindow(popout);
  await waitFor(async () => (await tabTitles(popout))?.length === before.length, "The tabs did not move to the browser's window");
  assert.equal(await page("Boolean(document.querySelector('[data-testid=browser-popped-out]')?.getClientRects().length)"), true, "Raticode's window says where the browser went");
  assert.equal(await tabTitles(window), null, "the tab bar left Raticode's window");
  assert.equal(await inPopout("document.title"), "Raticode browser");
  assert.equal(await inPopout("Boolean(window.goferBrowser)"), false, "the browser's window has no desktop bridge of its own");
  // The studio's styles came along.
  assert.equal(await inPopout("getComputedStyle(document.querySelector('[aria-label=\"Browser tabs\"]')).display"), "flex");

  // The chromium tab: the same page, moved into the new window, still running.
  await waitFor(() => viewIn(popout), "The chromium tab's page didn't move into the browser's window");
  assert.equal(viewIn(window), false);
  const counted = await assertSamePage("popping out");
  assert.equal(first.navigationHistory.canGoBack(), true, "its history is the same");
  assert.ok((await tabTitles(popout)).some((title) => title.includes("Form page")));
  log(`chromium: the same page moved into the browser's window without reloading; its field, tone and counter carried on (count ${startState.count} -> ${counted}).`);
  // The private tab kept its private profile (the main process refuses a view in the wrong profile).
  await waitFor(async () => (await tabTitles(popout)).some((title) => /^Private.*Tick \d+/.test(title)), "The private tab did not come back");
  assert.equal(await inPopout("Boolean([...document.querySelectorAll('[role=alert]')].some((node) => /wrong profile|unavailable/i.test(node.textContent)))"), false);
  // Rem still controls its tab.
  await waitFor(async () => (await rem("tabs")).tabs.some((tab) => tab.title === "Agent page"), "Rem lost its tab");
  const { snapshot } = await waitFor(async () => { const result = await rem("snapshot"); return /button "Run"/.test(result.snapshot) && result; }, "Rem could not read its tab after the move", 20000);
  assert.match(snapshot, /button "Run"/);
  log("private and Rem's tabs are in the browser's window, and Rem can still read its tab.");

  // The media bar in Raticode's window: shown while the playing tab isn't on screen.
  const mediaBar = () => page("Boolean(document.querySelector('[data-testid=browser-media-bar]')?.getClientRects().length)");
  const selectTab = (text) => click(popout, `[...document.querySelectorAll('[aria-label="Browser tabs"] [role=tab]')].find((node) => node.textContent.includes(${JSON.stringify(text)}))`);
  await selectTab("Private");
  await waitFor(mediaBar, "The media bar didn't show while the playing tab was out of sight");
  await selectTab("Form page");
  await waitFor(async () => !(await mediaBar()), "The media bar stayed while the playing tab was on screen in the browser's window");
  log("media bar: shown in Raticode's window while the playing tab was behind another, hidden once it was on screen in the browser's window.");

  // DevTools for the popped-out tab.
  const shownSlot = await waitFor(() => inPopout("[...document.querySelectorAll('[data-browser-view]')].find((node) => node.checkVisibility())?.getAttribute('data-browser-view') || ''"), "The chromium tab's slot didn't show");
  // Electron here never reports DevTools as open on the first try, with webview
  // tabs too; the DevTools page it makes for the tab is what this checks.
  const devtoolsPages = () => webContents.getAllWebContents().filter((contents) => !contents.isDestroyed() && contents.getURL().startsWith("devtools://")).length;
  const devtoolsBefore = devtoolsPages();
  await page(`window.goferBrowser.toggleDevTools(${JSON.stringify(shownSlot)})`);
  await waitFor(() => devtoolsPages() > devtoolsBefore, "DevTools didn't start for the popped-out tab");
  // A site asking for a permission asks in the browser's window.
  void first.executeJavaScript("Notification.requestPermission().then((answer) => { window.permission = answer; })", true);
  await waitFor(() => inPopout("Boolean(document.querySelector('[aria-label=\"Site permission request\"]'))"), "The permission request didn't show in the browser's window");
  await click(popout, "[...document.querySelectorAll('[aria-label=\"Site permission request\"] button')].find((node) => node.textContent === 'Block')");
  await waitFor(async () => (await first.executeJavaScript("window.permission || ''")) === "denied", "Blocking the request didn't reach the page");
  // A download from the popped-out tab.
  void first.executeJavaScript("location.href = '/file.txt'");
  const downloaded = path.join(downloadsDir, "popout-download.txt");
  await waitFor(() => fs.existsSync(downloaded) && fs.readFileSync(downloaded, "utf8") === "downloaded while popped out", "The download from the popped-out tab didn't arrive");
  await assertSamePage("after DevTools, a permission request and a download");
  log("DevTools started, a permission request was asked and blocked, and a download arrived, all from the popped-out tab.");

  if (withGecko) {
    const popoutHandle = popout.getNativeWindowHandle().readUInt32LE(0);
    await waitFor(async () => (await geckoParent()) === popoutHandle, "Firefox's window did not follow the browser's window", 15000);
    // The counter kept going: Firefox didn't reload the page (a reload starts it
    // again from 0). The Gecko tab is behind another by now, so it counts slowly.
    const after = await waitFor(async () => { const value = await ticks(); return value > geckoBefore && value; }, "Firefox's page stopped or reloaded", 8000);
    log(`Raticode (gecko): Firefox's window moved into the browser's window and its page kept counting (${geckoBefore} -> ${after}).`);
  }

  // The window's place is remembered.
  popout.setBounds({ x: 120, y: 90, width: 900, height: 640 });
  const store = path.join(app.getPath("userData"), "browser-window.json");
  await waitFor(() => { try { const saved = JSON.parse(fs.readFileSync(store, "utf8")); return saved.x === 120 && saved.y === 90 && saved.width === 900 && saved.height === 640; } catch { return false; } }, "The browser window's place was not saved");

  // Closing the window brings the tabs back.
  const geckoBeforeClose = withGecko ? await ticks() : 0;
  popout.close();
  await waitFor(() => popout.isDestroyed(), "The browser's window did not close");
  await waitFor(async () => (await tabTitles(window))?.length === before.length, "Closing the window did not bring the tabs back");
  assert.equal(await page("Boolean(document.querySelector('[data-testid=browser-popped-out]'))"), false);
  assert.equal(viewIn(window), true, "the chromium page went back into Raticode's window");
  await assertSamePage("coming back");
  await waitFor(async () => (await rem("tabs")).tabs.some((tab) => tab.title === "Agent page"), "Rem lost its tab coming back");
  if (withGecko) {
    assert.equal(await geckoParent(), window.getNativeWindowHandle().readUInt32LE(0), "Firefox's window came back before its window closed");
    // The Gecko tab is behind another now, so Firefox slows its timers; a
    // reload would have started the count again from 0.
    const after = await waitFor(async () => { const value = await ticks(); return value > geckoBeforeClose && value; }, "Firefox's page stopped or reloaded through the close", 8000);
    assert.ok(after > geckoBeforeClose, `Firefox's page kept running through the close (ticks ${geckoBeforeClose} -> ${after})`);
    assert.equal((await geckoStatus())?.running, true);
  }
  log("closing the browser's window returned every tab to Raticode's window.");

  // Pop out again from a tab's menu: the window opens where it was.
  await page(`(() => { const tab = document.querySelector('[aria-label="Browser tabs"] [role=tab]'); const box = tab.getBoundingClientRect(); tab.parentElement.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.x + 20, clientY: box.bottom })); })()`);
  await waitFor(() => page("Boolean([...document.querySelectorAll('[role=menuitem]')].find((node) => node.textContent === 'Pop out browser'))"), "The tab menu has no Pop out browser item");
  await click(window, "[...document.querySelectorAll('[role=menuitem]')].find((node) => node.textContent === 'Pop out browser')");
  const again = await waitFor(otherWindow, "Pop out from the tab menu did not open a window");
  await waitFor(async () => (await tabTitles(again))?.length === before.length, "The tabs did not move again");
  assert.deepEqual(again.getBounds(), { x: 120, y: 90, width: 900, height: 640 });
  // Bring browser back, from inside the browser's window.
  assert.equal(await click(again, `document.querySelector('[data-testid=browser-window-toggle]')`), true);
  await waitFor(() => again.isDestroyed(), "Bring browser back did not close the window");
  await waitFor(async () => (await tabTitles(window))?.length === before.length, "Bring browser back did not return the tabs");
  await assertSamePage("a second trip out and back");
  log("the window reopened at its saved place, and Bring browser back returned the tabs.");

  if (withGecko) {
    await page("window.goferBrowser.gecko('stop')");
    display.close();
  }
  clearTimeout(timer);
  console.log("Browser window passed.");
  api.close();
  site.close();
  app.exit(0);
}

run().catch((error) => { console.error(error); app.exit(1); });
