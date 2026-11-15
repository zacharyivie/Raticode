// The Browser in its own window. The studio opens a blank window of its own
// page (window.open with FRAME_NAME) and moves the Browser plugin's DOM into it,
// so tabs, Rem's hold on them and every bit of studio state stay in the one
// studio page. This module decides whether that window may open, where it goes,
// remembers its place, keeps it on a screen that exists, and makes sure closing
// it gives the tabs back to Raticode's window before it goes.
const fs = require("node:fs");
const path = require("node:path");

const FRAME_NAME = "raticode-browser-window";
const STORE_FILE = "browser-window.json";
const DEFAULT_SIZE = { width: 1200, height: 860 };
const MIN_SIZE = { width: 560, height: 420 };
// Enough of the window on a screen to grab its title bar.
const MIN_VISIBLE = 80;
const SAVE_DELAY_MS = 400;
const CLOSE_TIMEOUT_MS = 8000;

function whole(value) {
  return Number.isFinite(value) ? Math.round(value) : NaN;
}

function overlap(a, b) {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? { width, height } : null;
}

// Where the window opens: its saved place when that is still on a screen, or
// centred on the screen Raticode's window is on.
function placeOnScreen(saved, displays = [], fallback = null) {
  const areas = displays.map((display) => display?.workArea).filter((area) => area && area.width > 0 && area.height > 0);
  const home = fallback?.workArea || areas[0] || { x: 0, y: 0, width: 1280, height: 800 };
  const size = {
    width: whole(saved?.width),
    height: whole(saved?.height),
  };
  const widthOk = size.width >= MIN_SIZE.width;
  const heightOk = size.height >= MIN_SIZE.height;
  const x = whole(saved?.x);
  const y = whole(saved?.y);
  if (widthOk && heightOk && Number.isFinite(x) && Number.isFinite(y)) {
    const rect = { x, y, width: size.width, height: size.height };
    // The top strip, where the title bar is, has to be reachable.
    const titleBar = { x, y, width: size.width, height: Math.min(size.height, 40) };
    const area = areas.find((candidate) => {
      const shared = overlap(titleBar, candidate);
      return shared && shared.width >= MIN_VISIBLE;
    });
    if (area) {
      return {
        ...rect,
        width: Math.min(rect.width, Math.max(MIN_SIZE.width, area.width)),
        height: Math.min(rect.height, Math.max(MIN_SIZE.height, area.height)),
      };
    }
  }
  const width = Math.max(MIN_SIZE.width, Math.min(widthOk ? size.width : DEFAULT_SIZE.width, home.width));
  const height = Math.max(MIN_SIZE.height, Math.min(heightOk ? size.height : DEFAULT_SIZE.height, home.height));
  return {
    x: Math.round(home.x + (home.width - width) / 2),
    y: Math.round(home.y + (home.height - height) / 2),
    width,
    height,
  };
}

function createBrowserWindowManager({
  dataDir,
  getMainWindow,
  screen,
  icon = "",
  backgroundColor = "#1f1f1f",
  beforeClose = async () => {},
  onChange = () => {},
  log = () => {},
  closeTimeoutMs = CLOSE_TIMEOUT_MS,
}) {
  const storePath = path.join(dataDir, STORE_FILE);
  let window = null;
  let closing = false;
  let saveTimer = null;
  let closeTimer = null;

  function readSaved() {
    try {
      const saved = JSON.parse(fs.readFileSync(storePath, "utf8"));
      return saved && typeof saved === "object" ? saved : null;
    } catch {
      return null;
    }
  }

  function save() {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (!window || window.isDestroyed()) return;
    // A maximized or full-screen window remembers the size it goes back to.
    const bounds = typeof window.getNormalBounds === "function" ? window.getNormalBounds() : window.getBounds();
    const record = { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height, maximized: window.isMaximized?.() === true };
    try {
      fs.mkdirSync(path.dirname(storePath), { recursive: true });
      fs.writeFileSync(storePath, JSON.stringify(record), { mode: 0o600 });
    } catch (error) {
      log("warn", `Could not remember the browser window's place: ${error.message}`);
    }
  }

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, SAVE_DELAY_MS);
    saveTimer.unref?.();
  }

  function mainDisplay() {
    const main = getMainWindow();
    try {
      return main && !main.isDestroyed() ? screen.getDisplayMatching(main.getBounds()) : screen.getPrimaryDisplay();
    } catch {
      return null;
    }
  }

  function startBounds() {
    return placeOnScreen(readSaved(), screen.getAllDisplays(), mainDisplay());
  }

  function isOpen() {
    return Boolean(window && !window.isDestroyed());
  }

  function state() {
    return { open: isOpen() };
  }

  function tell(payload) {
    const main = getMainWindow();
    if (main && !main.isDestroyed() && !main.webContents.isDestroyed()) main.webContents.send("gofer:browser-window", payload);
  }

  // From the studio's setWindowOpenHandler. Anything that isn't the studio's own
  // blank Browser window gets null and keeps the old rule (open it as a tab).
  function windowOpenRequest({ url, frameName } = {}) {
    if (frameName !== FRAME_NAME) return null;
    if (url !== "about:blank" && url !== "") return { action: "deny" };
    if (isOpen()) {
      window.show();
      window.focus();
      return { action: "deny" };
    }
    const bounds = startBounds();
    return {
      action: "allow",
      outlivesOpener: false,
      overrideBrowserWindowOptions: {
        ...bounds,
        minWidth: MIN_SIZE.width,
        minHeight: MIN_SIZE.height,
        title: "Raticode browser",
        ...(icon ? { icon } : {}),
        backgroundColor,
        autoHideMenuBar: true,
        show: true,
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          webviewTag: false,
          // Pages and media in it keep running when it's behind another window.
          backgroundThrottling: false,
        },
      },
    };
  }

  // From did-create-window on the studio. The window only ever shows the
  // studio's own DOM; it may not navigate, open windows or host views itself.
  function adopt(child, details = {}) {
    if (details.frameName !== FRAME_NAME) return false;
    if (isOpen() && window !== child) {
      child.destroy();
      return false;
    }
    window = child;
    closing = false;
    watchScreens();
    child.setMenu?.(null);
    const contents = child.webContents;
    contents.on("will-navigate", (event) => event.preventDefault());
    contents.on("will-redirect", (event) => event.preventDefault());
    contents.on("will-attach-webview", (event) => event.preventDefault());
    contents.setWindowOpenHandler(() => ({ action: "deny" }));
    if (readSaved()?.maximized) child.maximize?.();
    child.on("move", saveSoon);
    child.on("resize", saveSoon);
    child.on("maximize", saveSoon);
    child.on("unmaximize", saveSoon);
    child.on("close", (event) => {
      if (closing) return;
      // The studio first takes the tabs back, then asks for the window to close.
      event.preventDefault();
      const main = getMainWindow();
      const studioAlive = main && !main.isDestroyed() && !main.webContents.isDestroyed() && !main.webContents.isCrashed?.();
      if (!studioAlive) {
        void finishClose();
        return;
      }
      tell({ ...state(), closeRequested: true });
      clearTimeout(closeTimer);
      // A studio that doesn't answer (stopped or crashed) can't take its tabs
      // back, so the window closes anyway: its pages are views the main process
      // owns, and they go back to Raticode's window first (beforeClose). The
      // studio puts its tab bar back when it answers again.
      closeTimer = setTimeout(() => { void finishClose(); }, closeTimeoutMs);
      closeTimer.unref?.();
    });
    child.on("closed", () => {
      clearTimeout(closeTimer);
      if (window === child) window = null;
      closing = false;
      onChange(state());
      tell(state());
    });
    onChange(state());
    tell(state());
    return true;
  }

  async function finishClose() {
    if (!isOpen()) return state();
    clearTimeout(closeTimer);
    save();
    closing = true;
    try {
      await beforeClose(window);
    } catch (error) {
      log("warn", `Getting the browser ready to close its window failed: ${error.message}`);
    }
    if (isOpen()) window.destroy();
    return { open: false };
  }

  // A screen went away or changed: bring the window back onto one that exists.
  function keepOnScreen() {
    if (!isOpen()) return;
    const bounds = window.getBounds();
    const next = placeOnScreen(bounds, screen.getAllDisplays(), mainDisplay());
    if (next.x !== bounds.x || next.y !== bounds.y || next.width !== bounds.width || next.height !== bounds.height) {
      log("info", "The browser window's screen went away; it moved to one that is connected.");
      window.setBounds(next);
    }
  }

  // Electron's screen works only once the app is ready, which it is by the
  // time a window opens.
  let watchingScreens = false;
  function watchScreens() {
    if (watchingScreens) return;
    watchingScreens = true;
    screen.on("display-removed", keepOnScreen);
    screen.on("display-metrics-changed", keepOnScreen);
  }

  async function handle(action) {
    switch (action) {
      case "status": return state();
      case "focus":
        if (isOpen()) {
          if (window.isMinimized()) window.restore();
          window.show();
          window.focus();
        }
        return state();
      // The studio has taken the tabs back.
      case "close": return finishClose();
      default: throw new Error(`Unknown browser window action: ${action}`);
    }
  }

  // Raticode is quitting or its studio page went away.
  function destroy() {
    clearTimeout(closeTimer);
    if (!isOpen()) return;
    save();
    closing = true;
    window.destroy();
  }

  return {
    adopt, closeNow: finishClose, destroy, handle, keepOnScreen, state, watchScreens, windowOpenRequest,
    // A window that is closing no longer hosts anything (Firefox's window must not move back into it).
    get window() { return isOpen() && !closing ? window : null; },
  };
}

module.exports = { FRAME_NAME, MIN_SIZE, DEFAULT_SIZE, createBrowserWindowManager, placeOnScreen };
