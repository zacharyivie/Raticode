// A popped-out Browser with the app zoomed: a Raticode (chromium) tab's page
// sits exactly on its slot in the Browser's own window at 125% and at 80%, and
// again in Raticode's window once it comes back. The slot is measured in the
// window that shows it, so the page's place follows that window's zoom, not
// Raticode's. Through the real desktop entry point and the built studio, on a
// hidden display with its own profile, data folder and home folder.
// Needs a built dist/ (npm run build).
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { app, BrowserWindow, screen, webContents } = require("electron");
const { browserContentZoomFactor } = require("../browser-utils.cjs");
const { waitFor } = require("./test-wait.cjs");
const WAIT = { timeout: 15_000, interval: 100, retryErrors: true };

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-browser-window-zoom-test-"));
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
  HOME: home,
  XDG_CONFIG_HOME: path.join(home, ".config"),
  XDG_CACHE_HOME: path.join(home, ".cache"),
  XDG_DATA_HOME: path.join(home, ".local", "share"),
});
const timer = setTimeout(() => { console.error("Browser window zoom test timed out"); app.exit(1); }, 180000);
const api = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.end(JSON.stringify({ workflows: [], providers: [], runs: [], queue: [], entries: [] }));
});
const site = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end("<title>Zoom page</title><body style=\"background:#2a7\">A page</body>");
});
const log = (message) => console.log(`[browser window zoom] ${message}`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  await waitFor(() => page("document.querySelector('#root')?.children.length > 0"), "Studio did not render", WAIT);
  // The app at 125%, a size the zoom keys never reach.
  await page(`localStorage.setItem("raticode.settings.v1", JSON.stringify({ browser: { homepage: ${JSON.stringify(siteUrl)} } })); localStorage.setItem("raticode.textZoom.v1", "125")`);
  window.webContents.reload();
  await new Promise((resolve) => window.webContents.once("did-finish-load", resolve));
  await waitFor(() => page("Boolean(document.querySelector('.studio-sidebar #sidebar-tab-browser'))"), "Studio did not render after reload", WAIT);
  await waitFor(() => Math.abs(window.webContents.getZoomFactor() - 1.25) < 0.001, "The app did not zoom to 125%", WAIT);
  await page("document.querySelector('.studio-sidebar #sidebar-tab-browser').click()");

  const inWindow = (target) => (code) => target.webContents.executeJavaScript(code);
  const click = (target, selector) => inWindow(target)(`(() => { const node = ${selector}; if (!node) return false; node.click(); return true; })()`);
  const otherWindow = () => BrowserWindow.getAllWindows().find((item) => item !== window && !item.isDestroyed());

  await waitFor(() => page("Boolean(document.querySelector('[data-testid=browser-empty]')?.offsetParent)"), "The empty Browser plugin did not show", WAIT);
  await click(window, "document.querySelector('[data-browser-family=\"raticode\"]')");
  const guest = await waitFor(() => webContents.getAllWebContents().find((contents) => !contents.isDestroyed() && contents.getType() === "window" && contents.getURL() === siteUrl), "No view for the chromium tab", WAIT);
  await waitFor(async () => (await guest.executeJavaScript("document.title")) === "Zoom page", "The chromium tab did not load the test page", WAIT);

  // Where the slot is in the window's own pixels: its CSS box times that
  // window's zoom, read from the page that measured it (devicePixelRatio is
  // the display's scale times the page's zoom).
  const slotInWindow = async (target) => {
    const box = await inWindow(target)(`(() => {
      const slot = [...document.querySelectorAll('[data-browser-view]')].find((node) => node.checkVisibility());
      if (!slot) return null;
      const rect = slot.getBoundingClientRect();
      return { x: rect.left, y: rect.top, width: rect.width, height: rect.height, ratio: window.devicePixelRatio };
    })()`);
    if (!box) return null;
    const scale = screen.getDisplayMatching(target.getBounds()).scaleFactor || 1;
    const zoom = box.ratio / scale;
    return { zoom, bounds: { x: box.x * zoom, y: box.y * zoom, width: box.width * zoom, height: box.height * zoom } };
  };
  const viewOf = (target) => target.contentView.children.find((child) => child.webContents === guest);
  const near = (a, b) => ["x", "y", "width", "height"].every((key) => Math.abs(a[key] - b[key]) <= 1);
  // The view covers the slot within a pixel, and the page is zoomed like the window it's in.
  const assertOnSlot = async (target, where) => {
    let last = null;
    await waitFor(async () => {
      const view = viewOf(target);
      const slot = await slotInWindow(target);
      if (!view || !slot || !view.getVisible()) return false;
      last = { view: view.getBounds(), slot };
      return near(last.view, slot.bounds);
    }, `The chromium tab's page is not on its slot ${where}`, { ...WAIT, timeout: 10000 }).catch((error) => {
      throw new Error(`${error.message}: view ${JSON.stringify(last?.view)}, slot ${JSON.stringify(last?.slot?.bounds)} at window zoom ${last?.slot?.zoom}`);
    });
    const expectedPageZoom = browserContentZoomFactor(last.slot.zoom, 1);
    await waitFor(() => Math.abs(guest.getZoomFactor() - expectedPageZoom) < 0.01, `The page's zoom ${guest.getZoomFactor()} doesn't follow its window's zoom ${last.slot.zoom} ${where}`, { ...WAIT, timeout: 5000 });
    log(`${where}: view ${JSON.stringify(last.view)} on slot ${JSON.stringify(last.slot.bounds)} (window zoom ${last.slot.zoom}, page zoom ${guest.getZoomFactor()}).`);
  };

  const popOut = async () => {
    assert.equal(await click(window, "document.querySelector('[data-testid=browser-window-toggle]')"), true, "the tab bar has a Pop out button");
    const popout = await waitFor(otherWindow, "Pop out did not open a window", WAIT);
    await waitFor(() => viewOf(popout), "The chromium tab's page didn't move into the browser's window", WAIT);
    return popout;
  };
  const popIn = async (popout) => {
    assert.equal(await click(popout, "document.querySelector('[data-testid=browser-window-toggle]')"), true, "the browser's window has a Bring browser back button");
    await waitFor(() => popout.isDestroyed(), "Bring browser back did not close the window", WAIT);
    await waitFor(() => viewOf(window), "The chromium tab's page didn't come back to Raticode's window", WAIT);
  };

  await assertOnSlot(window, "in Raticode's window at 125%");
  let popout = await popOut();
  await assertOnSlot(popout, "popped out at 125%");
  await popIn(popout);
  await assertOnSlot(window, "back in Raticode's window at 125%");

  // Down to 80% with the zoom keys (125 -> 120 -> ... -> 80).
  for (let step = 0; step < 6 && Math.abs(window.webContents.getZoomFactor() - 0.8) > 0.001; step += 1) {
    window.webContents.focus();
    await page("window.dispatchEvent(new KeyboardEvent('keydown', { key: '-', code: 'Minus', ctrlKey: true, bubbles: true, cancelable: true }))");
    await wait(150);
  }
  await waitFor(() => Math.abs(window.webContents.getZoomFactor() - 0.8) < 0.001, `The app did not zoom to 80% (${window.webContents.getZoomFactor()})`, WAIT);
  await assertOnSlot(window, "in Raticode's window at 80%");
  popout = await popOut();
  await assertOnSlot(popout, "popped out at 80%");
  await popIn(popout);
  await assertOnSlot(window, "back in Raticode's window at 80%");

  clearTimeout(timer);
  console.log("Browser window zoom passed.");
  api.close();
  site.close();
  app.exit(0);
}

run().catch((error) => { console.error(error); app.exit(1); });
