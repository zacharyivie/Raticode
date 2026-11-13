// The Browser plugin's tab bar through the real desktop entry point and the
// built studio: each browser's tabs sit together with a + after them, "+ Browser"
// starts a browser that has no tabs yet, and nothing fails without saying why.
// With Firefox installed it also opens a real Raticode (gecko) tab and checks
// that its + opens a Firefox tab and that the tab bar never covers its page.
// Runs on a hidden display with its own profile, data folder and home folder.
// Needs a built dist/ (npm run build). Set RATICODE_TAB_BAR_SHOT to a .png path
// to save a picture of the tab bar.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app } = require("electron");
const { X11Client } = require("../x11-client.cjs");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-tab-bar-test-"));
const home = path.join(profile, "home");
fs.mkdirSync(path.join(profile, "logs"), { recursive: true });
fs.mkdirSync(home, { recursive: true });
app.setPath("userData", path.join(profile, "user"));
app.setPath("logs", path.join(profile, "logs"));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
Object.assign(process.env, {
  GOFER_ELECTRON_MODE: "production",
  GOFER_DATA_DIR: path.join(profile, "data"),
  GOFER_ELECTRON_TEST_HOOKS: "1",
  // Firefox writes to the home folder; keep it in the test's.
  HOME: home,
  XDG_CONFIG_HOME: path.join(home, ".config"),
  XDG_CACHE_HOME: path.join(home, ".cache"),
  XDG_DATA_HOME: path.join(home, ".local", "share"),
});
const timer = setTimeout(() => { console.error("Browser tab bar test timed out"); app.exit(1); }, 150000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
const site = http.createServer((request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end(`<title>Smoke page ${request.url === "/" ? "home" : request.url.slice(1)}</title><body style="background:#2a7">A page</body>`);
});
const log = (message) => console.log(`[tab bar] ${message}`);

async function waitFor(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
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

  // New tabs open the local test page.
  await page(`localStorage.setItem("raticode.settings.v1", JSON.stringify({ appearance: { theme: "dark" }, browser: { homepage: ${JSON.stringify(siteUrl)} } }))`);
  window.webContents.reload();
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  await waitFor(() => page("Boolean(document.querySelector('.studio-sidebar #sidebar-tab-browser'))"), "Studio did not render after reload");
  await page("document.querySelector('.studio-sidebar #sidebar-tab-browser').click()");

  const byLabel = (label) => `[...document.querySelectorAll('[aria-label]')].find((node) => node.getAttribute('aria-label') === ${JSON.stringify(label)} && node.offsetParent)`;
  const click = (selector) => page(`(() => { const node = ${selector}; if (!node) return false; node.click(); return true; })()`);
  const badges = () => page(`[...document.querySelectorAll('[aria-label="Browser tabs"] [data-browser-engine]')].map((node) => node.getAttribute('data-browser-engine'))`);
  const tabTitles = () => page(`[...document.querySelectorAll('[aria-label="Browser tabs"] [role=tab]')].map((node) => node.textContent)`);
  const groupButtons = () => page(`[...document.querySelectorAll('[aria-label="Browser tabs"] [data-browser-group-new-tab]')].map((node) => node.getAttribute('data-browser-group-new-tab'))`);
  const notice = () => page(`(() => { const node = document.querySelector('[data-testid=browser-tab-notice]'); return node ? { role: node.getAttribute('role'), text: node.textContent } : null; })()`);
  const family = (engine) => `document.querySelector('[data-browser-family="${engine}"]')`;

  // Empty Browser plugin: one button per browser, and no old top-right controls.
  await waitFor(() => page("Boolean(document.querySelector('[data-testid=browser-empty]')?.offsetParent)"), "The empty Browser plugin did not show");
  assert.equal(await page("document.querySelectorAll('[data-testid=browser-empty] [data-browser-family]').length"), 5);
  const status = await page("window.goferBrowser.library('engine-status')");
  log(`installed: ${JSON.stringify(Object.fromEntries(Object.entries(status || {}).map(([key, value]) => [key, Boolean(value?.available)])))}`);

  await click(`${family("raticode")}`);
  await waitFor(async () => (await tabTitles()).some((title) => title.includes("Smoke page home")), "The first chromium tab did not load the test page");
  assert.equal(await page(`Boolean(${byLabel("All browser tabs")})`), false, "the all-tabs dropdown is gone");
  assert.equal(await page("Boolean(document.querySelector('[data-testid=browser-new-tab], [data-testid=browser-new-tab-menu]'))"), false, "the top-right + and arrow are gone");
  assert.deepEqual(await groupButtons(), ["raticode"]);

  // The + after chromium's tabs.
  await click(byLabel("New Raticode (chromium) tab"));
  await waitFor(async () => (await badges()).length === 2, "+ did not open a second chromium tab");
  assert.deepEqual(await badges(), ["raticode", "raticode"]);

  // "+ Browser" lists browsers with no tabs yet, inline in the tab bar.
  await click(byLabel("Open a tab in another browser"));
  await waitFor(() => page(`Boolean(${family("private")})`), "The browser chooser did not open");
  const offered = await page(`[...document.querySelectorAll('[aria-label="Open a tab in"] [data-browser-family]')].map((node) => node.getAttribute('data-browser-family'))`);
  assert.deepEqual(offered, ["gecko", "firefox", "chrome", "edge", "private"]);
  // Escape closes it and puts focus back on its button. Opening it focuses the first choice.
  window.webContents.focus();
  await waitFor(() => page("document.activeElement?.getAttribute('data-browser-family') === 'gecko'"), "Opening the chooser did not focus its first choice");
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
  await waitFor(async () => !(await page(`Boolean(${family("private")})`)), "Escape did not close the browser chooser");
  assert.equal(await page("document.activeElement?.getAttribute('aria-label')"), "Open a tab in another browser");
  // A click elsewhere closes it too.
  await click(byLabel("Open a tab in another browser"));
  await waitFor(() => page(`Boolean(${family("private")})`), "The browser chooser did not reopen");
  const tab = await page(`(() => { const box = document.querySelector('[aria-label="Browser tabs"] [role=tab]').getBoundingClientRect(); return { x: Math.round(box.x + 20), y: Math.round(box.y + box.height / 2) }; })()`);
  window.webContents.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...tab });
  window.webContents.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...tab });
  await waitFor(async () => !(await page(`Boolean(${family("private")})`)), "An outside click did not close the browser chooser");

  // Private tabs group apart, with a + that opens another private tab.
  await click(byLabel("Open a tab in another browser"));
  await waitFor(() => page(`Boolean(${family("private")})`), "The chooser did not open for a private tab");
  await click(family("private"));
  await waitFor(async () => (await groupButtons()).includes("private"), "The private tab did not get its own group");
  await click(byLabel("New private tab"));
  await waitFor(async () => (await badges()).length === 4, "The private group's + did not open a private tab");
  assert.equal(await page(`[...document.querySelectorAll('[aria-label="Browser tabs"] [role=tab]')].filter((node) => node.textContent.startsWith('Private')).length`), 2);
  assert.deepEqual(await groupButtons(), ["raticode", "private"]);

  // A browser that isn't installed says so instead of doing nothing.
  const missing = ["firefox", "chrome", "edge"].find((engine) => status?.[engine] && !status[engine].available);
  if (missing) {
    const before = (await badges()).length;
    await click(byLabel("Open a tab in another browser"));
    await waitFor(() => page(`${family(missing)}?.getAttribute('aria-disabled') === 'true'`), `${missing} was not marked unavailable`);
    await click(family(missing));
    const shown = await waitFor(notice, `Choosing ${missing} showed no message`);
    assert.equal(shown.role, "alert");
    assert.match(shown.text, /isn't installed/);
    assert.equal((await badges()).length, before);
    await click(byLabel("Dismiss"));
    log(`an uninstalled ${missing} showed: ${shown.text.replace(/\s+/g, " ")}`);
  } else {
    log("Firefox, Chrome and Edge are all installed here; the not-installed message is covered by App.test.mjs.");
  }

  // Ctrl+Tab follows the tab bar's grouped order.
  const order = await tabTitles();
  await page(`document.querySelector('[aria-label="Browser tabs"] [role=tab]').focus()`);
  await click(`document.querySelector('[aria-label="Browser tabs"] [role=tab]')`);
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab", modifiers: ["control"] });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab", modifiers: ["control"] });
  const selected = () => page(`[...document.querySelectorAll('[aria-label="Browser tabs"] [role=tab]')].findIndex((node) => node.getAttribute('aria-selected') === 'true')`);
  await waitFor(async () => (await selected()) === 1, `Ctrl+Tab did not move to the next tab in ${JSON.stringify(order)}`);

  if (process.env.RATICODE_TAB_BAR_SHOT) {
    await click(byLabel("Open a tab in another browser"));
    await new Promise((resolve) => setTimeout(resolve, 400));
    fs.writeFileSync(process.env.RATICODE_TAB_BAR_SHOT, (await window.webContents.capturePage()).toPNG());
    await click(byLabel("Close the browser choices"));
  }

  // Raticode (gecko), with a real Firefox when there is one.
  const gecko = await page("window.goferBrowser.gecko('status')");
  if (!gecko?.available || !gecko?.supported) {
    log(`Raticode (gecko) can't run here (${gecko?.problem || (gecko?.executable ? "unsupported display" : "no Firefox")}); skipping its part.`);
  } else {
    await click(byLabel("Open a tab in another browser"));
    await waitFor(() => page(`Boolean(${family("gecko")})`), "The chooser did not offer Raticode (gecko)");
    await click(family("gecko"));
    await waitFor(async () => (await page("window.goferBrowser.gecko('status')"))?.placed, "Firefox's window did not move into the Gecko tab", 60000);
    // Firefox's window has its own "+", so the Gecko tab gets none beside "+ Browser".
    assert.deepEqual(await groupButtons(), ["raticode", "private"]);
    await waitFor(async () => /Smoke page home/.test((await page("window.goferBrowser.gecko('status')"))?.title || ""), "Firefox did not open the home page", 30000);

    // What is on screen, read from the display itself: Firefox's window doesn't
    // show in Electron's own captures. The test page is green (#22aa77).
    const display = await new X11Client().connect();
    const origin = await display.translate(window.getNativeWindowHandle().readUInt32LE(0), display.root);
    const ratio = await page("window.devicePixelRatio || 1");
    const green = async ({ x, y }) => {
      const { data } = await display.image(display.root, Math.round(origin.x + x * ratio), Math.round(origin.y + y * ratio), 1, 1);
      return Math.abs(data[0] - 0x77) < 24 && Math.abs(data[1] - 0xaa) < 24 && Math.abs(data[2] - 0x22) < 24;
    };
    const slotBox = await page("(() => { const box = document.querySelector('[data-testid=gecko-slot]').getBoundingClientRect(); return { left: box.left, top: box.top, right: box.right, bottom: box.bottom }; })()");
    const farCorner = { x: slotBox.right - 40, y: slotBox.bottom - 40 };
    await waitFor(() => green(farCorner), "Firefox's page did not show in the Gecko tab", 15000);

    // A tab's right-click menu over the page: a hole for the menu, the rest of the page stays.
    await page(`(() => { const gecko = [...document.querySelectorAll('[aria-label="Browser tabs"] [role=tab]')].find((node) => node.querySelector('[data-browser-engine=gecko]')); const box = gecko.getBoundingClientRect(); gecko.parentElement.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.x + 20, clientY: box.bottom })); })()`);
    const menu = await waitFor(() => page("(() => { const node = [...document.querySelectorAll('[role=menu]')].find((item) => item.getClientRects().length); if (!node) return null; const box = node.getBoundingClientRect(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; })()"), "The tab's right-click menu did not open");
    await waitFor(async () => !(await green(menu)), "The right-click menu was hidden under Firefox's window", 5000);
    assert.equal(await green(farCorner), true, "the page around the menu stays on screen");
    // A click in Firefox's page closes the menu, as a click anywhere else would.
    const xtest = await display.extension("XTEST");
    const at = { x: Math.round(origin.x + farCorner.x * ratio), y: Math.round(origin.y + farCorner.y * ratio) };
    display.fakeInput(xtest, 6, 0, at.x, at.y);
    display.fakeInput(xtest, 4, 1, at.x, at.y);
    display.fakeInput(xtest, 5, 1, at.x, at.y);
    await waitFor(async () => !(await page("[...document.querySelectorAll('[role=menu]')].some((item) => item.getClientRects().length)")), "A click in Firefox's page did not close the menu", 5000);
    await waitFor(() => green(menu), "The hole stayed after the menu closed", 5000);
    log("Raticode (gecko): a menu over the page cuts a hole in Firefox's window; the page stays on screen around it, and a click in the page closes the menu.");
    display.close();
    // The tab bar, the chooser and the notice never cover Firefox's page, so it stays on screen.
    const covered = () => page(`(() => {
      const slot = document.querySelector('[data-testid=gecko-slot]');
      const box = slot.getBoundingClientRect();
      const points = [];
      for (let row = 0; row < 7; row += 1) for (let column = 0; column < 9; column += 1) points.push([box.left + 1 + (box.width - 2) * column / 8, box.top + 1 + (box.height - 2) * row / 6]);
      return points.some(([x, y]) => { const node = document.elementFromPoint(x, y); return node && !slot.contains(node); });
    })()`);
    // "+ Browser" still offers Raticode (gecko): a new tab inside Firefox's window.
    await click(byLabel("Open a tab in another browser"));
    await waitFor(() => page(`${family("gecko")}?.getAttribute('aria-label') === "New tab inside Raticode (gecko)'s Firefox window"`), "+ Browser did not offer a tab inside Raticode (gecko)");
    await click(family("gecko"));
    const shown = await waitFor(notice, "A new Gecko tab showed no message");
    assert.equal(shown.role, "status");
    assert.match(shown.text, /one Firefox window/);
    assert.equal((await badges()).filter((engine) => engine === "gecko").length, 1, "Gecko keeps one tab");
    let lastTitle = "";
    await waitFor(async () => { lastTitle = (await page("window.goferBrowser.gecko('status')"))?.title || ""; return !/Smoke page home/.test(lastTitle); }, "Firefox did not open a new tab", 30000).catch((error) => { throw new Error(`${error.message}; its title stayed ${JSON.stringify(lastTitle)}`); });
    // An empty Firefox tab has no title of its own, so the window is just "Mozilla Firefox".
    assert.match(lastTitle, /^(Mozilla Firefox|New Tab)$/);
    await click(byLabel("Open a tab in another browser"));
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(await covered(), false, "the open chooser and the notice leave Firefox's page uncovered");
    await click(byLabel("Close the browser choices"));
    log(`Raticode (gecko): + Browser opened an empty Firefox tab (window title ${JSON.stringify(lastTitle)}), and its page stayed uncovered with the chooser and notice open.`);
    await page("window.goferBrowser.gecko('stop')");
  }

  // Closing every tab brings back the empty Browser plugin.
  for (let attempt = 0; attempt < 10 && (await badges()).length; attempt += 1) {
    await click(`document.querySelector('[aria-label="Browser tabs"] [aria-label^="Close "]')`);
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  await waitFor(() => page("Boolean(document.querySelector('[data-testid=browser-empty]')?.offsetParent)"), "Closing every tab did not show the empty Browser plugin");

  clearTimeout(timer);
  console.log("Browser tab bar passed.");
  api.close();
  site.close();
  app.exit(0);
}

run().catch((error) => { console.error(error); app.exit(1); });
