// A Raticode (chromium) tab whose page process ends or gets stuck while the
// user is away: the real IntegratedBrowser tab, its <webview> with Raticode's
// own preload, and the real browser sessions and bridge. Nothing is shown while
// the tab is hidden, and the page reloads by itself, with its back/forward
// list, once the tab is in front again. A page that keeps stopping gets a quiet
// Reload button. Small enough to need no app build: esbuild compiles only the
// tab. Runs on a hidden display.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { BrowserWindow, Menu, app, clipboard, dialog, ipcMain, nativeImage, session, shell, webContents } = require("electron");
const { createBrowserSessions } = require("../browser-sessions.cjs");
const { createBrowserStore } = require("../browser-store.cjs");

const frontend = path.join(__dirname, "..", "..");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-browser-recovery-test-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Browser recovery test timed out"); app.exit(1); }, 150000);

const PAGES = {
  "/first": "<!doctype html><title>First page</title><body>First</body>",
  "/second": "<!doctype html><title>Second page</title><body>Second <script>window.loadedAt = Date.now()</script></body>",
};
const ERROR_TEXT = /isn.t responding|stopped responding|stopped working|crashed|keeps stopping/i;

async function waitFor(check, message, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The studio says whether the tab is in front; the test sets it with window.setActive.
const ENTRY = `import { useState } from "react"; import { createRoot } from "react-dom/client"; import Tab from "${path.join(frontend, "src", "components", "IntegratedBrowser.jsx").replace(/\\/g, "/")}";
  function Host() { const [active, setActive] = useState(true); window.setActive = setActive;
    return <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column" }}><Tab active={active} clientId="browser:tab" initialUrl={window.firstUrl} /></div>; }
  createRoot(document.getElementById("root")).render(<Host />);`;

// Bundles the tab with esbuild where it's installed, otherwise with Vite's own bundler.
async function compileTab() {
  try {
    const esbuild = require("esbuild");
    const script = await esbuild.build({
      stdin: { contents: ENTRY, loader: "jsx", resolveDir: frontend },
      bundle: true, write: false, jsx: "automatic", format: "iife",
      define: { "process.env.NODE_ENV": "\"production\"" },
    });
    return script.outputFiles[0].text;
  } catch (error) {
    if (error?.code !== "MODULE_NOT_FOUND") throw error;
  }
  // A module that exists only in this build, placed beside the components so its imports resolve; nothing is written.
  const entry = path.join(frontend, "src", "browser-recovery-test-entry.jsx");
  const virtualEntry = { name: "browser-recovery-test-entry", resolveId: (id) => (id === entry ? entry : null), load: (id) => (id === entry ? ENTRY : null) };
  const { build } = await import("vite");
  const { default: react } = await import("@vitejs/plugin-react");
  const { testViteCacheDir } = require("./test-vite-cache.cjs");
  const result = await build({
    configFile: false,
    root: frontend,
    logLevel: "warn",
    cacheDir: testViteCacheDir("browser-recovery"),
    plugins: [virtualEntry, react()],
    define: { "process.env.NODE_ENV": "\"production\"" },
    build: { write: false, minify: false, lib: { entry, formats: ["iife"], name: "RecoveryTab", fileName: () => "tab.js" } },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((item) => item.output);
  return outputs.find((item) => item.type === "chunk").code;
}

async function run() {
  const bundle = await compileTab();
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    if (request.url === "/host") {
      const base = `http://127.0.0.1:${server.address().port}`;
      response.end(`<!doctype html><body style="margin:0"><div id="root" style="position:relative;width:100vw;height:100vh"></div>
        <script>window.firstUrl = ${JSON.stringify(`${base}/first`)};</script><script>${bundle.replace(/<\/script/gi, "<\\/script")}</script>`);
      return;
    }
    response.end(PAGES[request.url] || "");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;

  let window = null;
  const browsers = createBrowserSessions({
    app, Menu, clipboard, dialog, shell, session, webContents, nativeImage,
    getMainWindow: () => window,
    resolvePath: (target) => target,
    store: createBrowserStore(path.join(profile, "browser")),
    quarantineDir: path.join(profile, "quarantine"),
  });
  browsers.install();
  ipcMain.handle("gofer:browser-create", (event, options) => browsers.createBrowser(event, options));
  ipcMain.handle("gofer:browser-action", (event, options) => browsers.browserAction(event, options));
  ipcMain.handle("gofer:browser-library", (event, options) => browsers.browserLibrary(event, options));
  // A dialog would block the test; none may appear.
  const dialogs = [];
  dialog.showMessageBox = async (...args) => { dialogs.push(args); return { response: 0 }; };
  dialog.showMessageBoxSync = (...args) => { dialogs.push(args); return 0; };
  dialog.showErrorBox = (...args) => { dialogs.push(args); };

  const preload = path.join(__dirname, "..", "browser-preload.cjs");
  window = new BrowserWindow({
    width: 900,
    height: 600,
    show: true,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "..", "preload.cjs"),
      sandbox: true,
      webviewTag: true,
      additionalArguments: [`--gofer-browser-preload=${pathToFileURL(preload)}`],
    },
  });
  const page = (code) => window.webContents.executeJavaScript(code);
  await window.loadURL(`${base}/host`);
  const guest = () => webContents.getAllWebContents().find((contents) => contents.getType() === "webview" && !contents.isDestroyed());
  const shownText = () => page("document.body.innerText");
  const noMessage = async (when) => {
    const text = await shownText();
    assert.doesNotMatch(text, ERROR_TEXT, `no message ${when}`);
    assert.equal(dialogs.length, 0, `no dialog ${when}`);
  };
  const loaded = (contents, pathName) => !contents.isCrashed() && contents.getURL() === `${base}${pathName}` && !contents.isLoading();

  // A tab with history: first, then second.
  await waitFor(() => guest() && loaded(guest(), "/first"), "the tab didn't open");
  const tab = guest();
  await tab.loadURL(`${base}/second`);
  await waitFor(() => loaded(tab, "/second"), "the second page didn't open");
  assert.equal(tab.navigationHistory.canGoBack(), true);

  // 1. The page process ends while the tab is hidden: nothing shows and nothing reloads until the tab is back.
  await page("window.setActive(false)");
  const firstProcess = tab.getOSProcessId();
  tab.forcefullyCrashRenderer();
  await waitFor(() => tab.isCrashed(), "the page process didn't end");
  await sleep(1500);
  assert.equal(tab.isCrashed(), true, "a hidden tab doesn't reload");
  await noMessage("while the ended tab is hidden");
  await page("window.setActive(true)");
  await waitFor(() => loaded(tab, "/second"), "the ended tab didn't reload on return");
  assert.notEqual(tab.getOSProcessId(), firstProcess);
  assert.equal(tab.navigationHistory.canGoBack(), true, "back still goes to the first page");
  assert.equal(tab.navigationHistory.length(), 2);
  await noMessage("after the ended tab came back");

  // 2. The page gets stuck while the tab is hidden: no banner, and it isn't ended while hidden.
  await page("window.setActive(false)");
  const stuckProcess = tab.getOSProcessId();
  void tab.executeJavaScript("setTimeout(() => { for (;;) {} }, 0); true").catch(() => {});
  await sleep(300);
  // Chromium reports a stuck page when it doesn't answer input; say so the same way.
  tab.emit("unresponsive");
  await sleep(6500);
  await noMessage("while the stuck tab is hidden");
  assert.equal(tab.getOSProcessId(), stuckProcess, "a hidden stuck tab is left alone");
  // In front again: a moment's grace, then the stuck page process ends and the page reloads in place.
  await page("window.setActive(true)");
  await noMessage("during the grace period");
  await waitFor(() => tab.getOSProcessId() !== stuckProcess && loaded(tab, "/second"), "the stuck tab didn't reload", 30000)
    .catch((error) => { throw new Error(`${error.message}: ${JSON.stringify({ process: tab.getOSProcessId(), stuckProcess, crashed: tab.isCrashed(), url: tab.getURL(), loading: tab.isLoading() })}`); });
  assert.equal(tab.navigationHistory.canGoBack(), true);
  await noMessage("after the stuck tab came back");

  // 3. A page that keeps stopping while in front: automatic reloads, each later than the last, until three
  // happened within two minutes (the reload in step 1 counts), then a quiet Reload button.
  let automatic = 1;
  for (;;) {
    const process = tab.getOSProcessId();
    tab.forcefullyCrashRenderer();
    const outcome = await waitFor(async () => {
      if (/keeps stopping/i.test(await shownText())) return "button";
      return tab.getOSProcessId() !== process && loaded(tab, "/second") ? "reloaded" : "";
    }, `nothing happened after the page stopped (automatic reloads so far: ${automatic})`, 30000);
    if (outcome === "button") break;
    automatic += 1;
    await noMessage(`after automatic reload ${automatic}`);
    assert.ok(automatic <= 3, "at most three automatic reloads in a row");
  }
  assert.equal(automatic, 3, "three automatic reloads before the Reload button");
  assert.equal(dialogs.length, 0, "shown in the tab, never as a dialog");
  await sleep(3000);
  assert.equal(tab.isCrashed(), true, "no more automatic reloads");
  await page("[...document.querySelectorAll('button')].find((button) => button.textContent.trim() === 'Reload').click()");
  await waitFor(() => loaded(tab, "/second"), "Reload didn't bring the page back");
  await waitFor(async () => !ERROR_TEXT.test(await shownText()), "the panel stayed after Reload");

  server.close();
  console.log("Browser recovery checks passed.");
}

app.whenReady().then(run).then(() => {
  clearTimeout(timer);
  fs.rmSync(profile, { recursive: true, force: true });
  app.exit(0);
}).catch((error) => {
  console.error(error);
  fs.rmSync(profile, { recursive: true, force: true });
  app.exit(1);
});
