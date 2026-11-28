/* global __dirname, process, console, clearTimeout, setTimeout, document, window */
// A Rattish "Did you mean" fix in the real Monaco editor: the markers Monaco hands back
// map to "Change 'sumary' to 'summary'", and the fix edits only that name as one step
// Monaco's own Undo reverses. Headless Chromium draws the Quick Fix list with no rows,
// so the menu itself isn't driven here.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
process.chdir(path.resolve(__dirname, ".."));
// Keep Electron's caches out of the developer's own Electron folder.
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-test-rattish-quick-fix-"));
process.once("exit", () => fs.rmSync(userData, { recursive: true, force: true }));
app.setPath("userData", userData);
const deadline = setTimeout(() => { console.error("Timed out"); app.exit(1); }, 90000);
let server;
app.whenReady().then(async () => {
  const { createServer } = await import("vite");
  // Same Monaco bundling fixes as vite.config.js (DOMPurify, language workers).
  const { monacoPatches } = await import("../dev/monaco-patches.js");
  server = await createServer({
    cacheDir: testViteCacheDir("rattish-quick-fix"),
    configFile: false,
    logLevel: "error",
    root: path.resolve(__dirname, ".."),
    optimizeDeps: { rolldownOptions: { plugins: [monacoPatches()] } },
    server: { host: "127.0.0.1", port: 0 },
    plugins: [monacoPatches(), (await import("@vitejs/plugin-react")).default(), { name: "rattish-quick-fix-fixture", configureServer(dev) {
      dev.middlewares.use(async (req, res, next) => {
        if (req.url !== "/") return next();
        res.setHeader("Content-Type", "text/html");
        res.end(await dev.transformIndexHtml("/", '<html><body><script type="module" src="/browser-tests/rattish-quick-fix.jsx"></script></body></html>'));
      });
    } }],
  });
  await server.listen();
  const win = new BrowserWindow({ width: 1000, height: 700, show: true, webPreferences: { sandbox: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") errors.push(message); });
  await win.loadURL(server.resolvedUrls.local[0]);
  const evaluate = (fn) => win.webContents.executeJavaScript(`Promise.resolve((${fn})())`);
  const waitFor = async (fn, what) => {
    for (let i = 0; i < 100; i++) {
      const value = await evaluate(fn);
      if (value) return value;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`Timed out waiting for ${what}`);
  };
  await waitFor(() => window.quickFixReady && document.querySelector(".monaco-editor textarea"), "the editor");

  // Real Monaco markers map back to the fix the Quick Fix menu offers.
  assert.equal(await evaluate(() => window.quickFix.focusName()), true, "The editor has focus on the underlined name");
  assert.deepEqual(await evaluate(() => window.quickFix.titles()), ["Change 'sumary' to 'summary'"]);

  // The Problems button's edit changes only the name, and one Undo puts it back.
  assert.equal(await evaluate(() => window.quickFix.applyFromProblems()), true);
  assert.ok(await evaluate(() => window.quickFix.text().includes("  to: summary\n")));
  assert.ok(!(await evaluate(() => window.quickFix.text().includes("sumary"))), "Only the misspelled name changes");
  assert.ok(await evaluate(() => window.quickFix.text().includes("  needs: greet\n")), "The rest of the file stays as it was");
  await evaluate(() => window.quickFix.run("undo"));
  await waitFor(() => window.quickFix.text().includes("  to: sumary\n"), "Undo of the Problems fix");

  assert.deepEqual(errors, []);
  console.log("Rattish Quick Fix: the marker maps to the change, the fix edits only the name, and Undo puts it back.");
  win.destroy(); await server.close(); clearTimeout(deadline); app.quit();
}).catch(async (error) => { console.error(error); if (server) await server.close(); app.exit(1); });
