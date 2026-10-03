/* global __dirname, process, console, clearTimeout, setTimeout, window, document, KeyboardEvent, Event, HTMLInputElement */
const { app, BrowserWindow, ipcMain, clipboard, session: electronSession, Menu, shell, webContents } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL, fileURLToPath } = require("node:url");
const vm = require("node:vm");
const utils = require("../electron/browser-utils.cjs");
const { installPermissionPolicy } = require("../electron/studio-policy.cjs");
const { createIpcSecurity, isSafeExternalUrl } = require("../electron/security.cjs");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-dev-shm-usage");
let server, win;
const deadline = setTimeout(() => finish(new Error("Mac UI regression timed out")), 90000);
async function finish(error) {
  clearTimeout(deadline);
  if (win && !win.isDestroyed()) win.destroy();
  if (server) await server.close();
  if (error) console.error(error);
  else console.log("Mac UI fixes: real Monaco actions, paste, draft recovery, language services, menu/tree navigation, focused inputs, reduced motion and real browser Find passed.");
  app.exit(error ? 1 : 0);
}
app.whenReady().then(async () => {
  const frontend = path.resolve(__dirname, "..");
  process.chdir(frontend);
  const { createServer } = await import("vite");
  server = await createServer({ root: frontend, cacheDir: "/tmp/rem-mac-ui-vite", configFile: false, css: { postcss: { plugins: [(await import("tailwindcss")).default({ config: path.join(frontend, "tailwind.config.js") }), (await import("autoprefixer")).default()] } }, server: { host: "127.0.0.1", port: 0 }, plugins: [
    (await import("@vitejs/plugin-react")).default(), { name: "mac-ui-fixture", configureServer(dev) { dev.middlewares.use(async (req, res, next) => {
      if (req.url === "/page") { res.setHeader("Content-Type", "text/html"); res.end("<html><title>Find fixture</title><body>needle one<br>needle two<br>other text</body></html>"); return; }
      if (req.url !== "/") return next();
      res.setHeader("Content-Type", "text/html"); res.end(await dev.transformIndexHtml("/", '<html><body><div id="root"></div><script type="module" src="/browser-tests/mac-ui-fixes.jsx"></script></body></html>'));
    }); } },
  ] });
  await server.listen();
  const url = server.resolvedUrls.local[0];
  win = new BrowserWindow({ width: 1280, height: 1000, show: true, webPreferences: { preload: path.join(frontend, "electron/preload.cjs"), sandbox: false, contextIsolation: true, nodeIntegration: false, webviewTag: true } });
  const errors = [];
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error" && !message.includes("Content Security Policy")) { errors.push(message); console.log("Renderer error:", message); } });
  const policy = { indexPath: path.join(frontend, "dist/index.html"), devServerUrl: url, isProduction: false };
  installPermissionPolicy(electronSession.fromPartition("persist:raticode-browser"), win.webContents.session, () => win.webContents, policy);
  const security = createIpcSecurity({ appRoots: [frontend], getDataDir: () => "/fixture", devServerUrl: url, isProduction: false, getMainWebContents: () => win.webContents });
  const native = { ...utils, isSafeExternalUrl, Menu, shell, webContents, path, fs, pathToFileURL, fileURLToPath, crypto: require("node:crypto"), process: { platform: "darwin" }, mainWindow: win,
    browserSessions: new Map(), browserOwnersWithCleanup: new WeakSet(), resolveExactPath: value => value };
  vm.createContext(native);
  const main = fs.readFileSync(path.join(frontend, "electron/main.js"), "utf8");
  vm.runInContext(main.slice(main.indexOf("function createBrowser("), main.indexOf("async function createTerminal(")), native);
  const data = { "/fixture/sample.ts": "const value: number = 1;\nconsole.log(value);\n", "/fixture/sample.py": "print(  'python')\n" };
  function handle(channel, action) { ipcMain.handle(channel, security.secureHandler(action)); }
  handle("gofer:browser-create", native.createBrowser);
  handle("gofer:browser-action", native.browserAction);
  handle("gofer:browser-owner-zoom", native.browserOwnerZoom);
  handle("gofer:read-text-file", (_event, options) => ({ content: data[options.targetPath] || "" }));
  handle("gofer:write-text-file", (_event, options) => { data[options.targetPath] = options.content; return {}; });
  handle("gofer:clipboard-read", () => clipboard.readText());
  handle("gofer:edit-focused", (event, { action }) => { event.sender[action](); return true; });
  handle("gofer:git-file-baseline", () => ({ tracked: false, changed: false }));
  handle("gofer:git-status", () => ({ active: false, entries: [] }));
  handle("gofer:grant-path", (_event, { targetPath }) => ({ path: targetPath, grantId: "fixture" }));
  handle("gofer:path-info", () => ({ exists: true }));
  handle("gofer:list-directory", (_event, { currentPath }) => ({ entries: currentPath === "/fixture" ? [
    { path: "/fixture/folder", name: "folder", isDirectory: true }, { path: "/fixture/sample.ts", name: "sample.ts", isDirectory: false },
  ] : [{ path: "/fixture/folder/nested.ts", name: "nested.ts", isDirectory: false }] }));
  const deleted = [];
  handle("gofer:delete-path", (_event, { targetPath }) => { deleted.push(targetPath); return true; });
  handle("gofer:search-project", () => ({ files: [], count: 0 }));
  handle("gofer:renderer-log", () => true);
  console.log("Loading Mac UI fixture", url);
  await win.loadURL(url);
  console.log("Loaded fixture document");
  const evaluate = fn => win.webContents.executeJavaScript(`(${fn})()`);
  async function waitFor(fn) { for (let i = 0; i < 300; i++) { if (await evaluate(fn)) return; await new Promise(resolve => setTimeout(resolve, 50)); } console.log(await evaluate(() => ({ body: document.body.textContent.slice(-2000), ready: window.fixtureReady, state: window.fileStateFixture }))); console.log(errors); throw Error(`Timed out waiting for ${fn}`); }
  console.log("Waiting for editors and guest");
  await waitFor(() => window.fileStateFixture && !window.fileStateFixture.loading && document.querySelector("webview"));
  assert.ok(await evaluate(() => window.fileStateFixture.commands["selection.expand"].supported));
  await waitFor(() => window.fileStateFixture.commands["edit.formatDocument"].supported);
  await evaluate(() => document.querySelector('[data-menu-trigger="Edit"]').click());
  assert.match(await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Copy")).textContent), /⌘|Cmd|Command/);
  await evaluate(() => document.querySelector('[data-menu-trigger="Edit"]').click());
  // Actual selection actions and native clipboard bridge under denied clipboard-read permission.
  assert.ok(await evaluate(async () => {
    const editor = window.monacoFixture.editor.getEditors()[0]; editor.setPosition({ lineNumber: 1, column: 8 }); editor.focus();
    const before = editor.getSelection().toString(); window.runFixtureCommand("selection.expand"); await new Promise(resolve => setTimeout(resolve, 100));
    const expanded = editor.getSelection().toString(); window.runFixtureCommand("selection.shrink"); await new Promise(resolve => setTimeout(resolve, 100));
    return before !== expanded && editor.getSelection().toString() === before;
  }));
  clipboard.writeText("native paste sentinel");
  await evaluate(() => { const editor = window.monacoFixture.editor.getEditors()[0]; editor.setSelection(new window.monacoFixture.Range(1, 1, 1, 1)); window.runFixtureCommand("edit.paste"); });
  await waitFor(() => window.monacoFixture.editor.getEditors()[0].getValue().includes("native paste sentinel"));
  await evaluate(() => window.flushDrafts());
  await win.webContents.reload();
  await waitFor(() => window.fileStateFixture?.dirty && document.querySelector("webview"));
  assert.ok(await evaluate(() => window.monacoFixture.editor.getEditors()[0].getValue().includes("native paste sentinel")));
  // Type-aware worker operates on the real file URI and diagnoses an incompatible assignment.
  assert.ok(await evaluate(async () => {
    const model = window.monacoFixture.editor.getEditors()[0].getModel(); model.setValue('const bad: number = "text";');
    const factory = await window.monacoFixture.languages.typescript.getTypeScriptWorker();
    const worker = await factory(model.uri);
    return (await worker.getSemanticDiagnostics(model.uri.toString())).some(error => error.code === 2322);
  }));
  await evaluate(() => { document.documentElement.dataset.reducedMotion = "true"; });
  await waitFor(() => !window.monacoFixture.editor.getEditors()[0].getOption(window.monacoFixture.editor.EditorOption.smoothScrolling));
  // Menu keys open, traverse, and restore focus.
  assert.ok(await evaluate(async () => {
    const trigger = document.querySelector('[data-menu-trigger="Edit"]'); trigger.focus(); trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true })); await new Promise(resolve => setTimeout(resolve, 50));
    const first = document.activeElement; first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    const next = document.activeElement; next.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    return first !== next && document.activeElement === trigger;
  }));
  // Focused text input receives Select All rather than the code editor.
  await evaluate(() => { const field = document.querySelector('[aria-label="Composer fixture"]'); field.focus(); field.setSelectionRange(0, 0); document.querySelector('[data-menu-trigger="Selection"]').click(); });
  await waitFor(() => document.querySelector('[role="menu"]'));
  await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Select All")).click());
  await waitFor(() => { const input = document.querySelector('[aria-label="Composer fixture"]'); return input.selectionEnd === input.value.length; });
  // General Edit commands follow the composer through the native bridge.
  await evaluate(() => { const input = document.querySelector('[aria-label="Composer fixture"]'); input.focus(); input.select(); document.querySelector('[data-menu-trigger="Edit"]').click(); });
  await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Copy")).click());
  for (let i = 0; i < 50 && clipboard.readText() !== "composer text"; i++) await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(clipboard.readText(), "composer text");
  clipboard.writeText("focused paste");
  await evaluate(() => { const input = document.querySelector('[aria-label="Composer fixture"]'); input.focus(); input.select(); document.querySelector('[data-menu-trigger="Edit"]').click(); });
  await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Paste")).click());
  await waitFor(() => document.querySelector('[aria-label="Composer fixture"]').value === "focused paste");
  await evaluate(() => document.querySelector('[data-menu-trigger="Edit"]').click());
  await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Undo")).click());
  await waitFor(() => document.querySelector('[aria-label="Composer fixture"]').value === "composer text");
  assert.ok(await evaluate(async () => {
    const tree = document.querySelector('[role="tree"]'); const root = tree.querySelector('[role="treeitem"]'); root.focus();
    root.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true })); await new Promise(resolve => setTimeout(resolve, 50));
    return document.activeElement.dataset.path === "/fixture/folder" && tree.querySelectorAll('[role="treeitem"][tabindex="0"]').length === 1;
  }));
  await evaluate(() => {
    window.fixtureConfirmCount = 0;
    window.confirm = () => { window.fixtureConfirmCount++; return false; };
    const item = document.querySelector('[data-path="/fixture/folder"]');
    item.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace", bubbles: true, cancelable: true }));
  });
  await waitFor(() => window.fixtureConfirmCount === 1);
  assert.deepEqual(deleted, []);
  await evaluate(() => {
    window.confirm = () => true;
    const item = document.querySelector('[data-path="/fixture/folder"]');
    item.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace", bubbles: true, cancelable: true }));
  });
  for (let i = 0; i < 50 && !deleted.length; i++) await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(deleted, ["/fixture/folder"]);
  // A recovered draft must not silently replace external disk edits through autosave.
  data["/fixture/sample.ts"] = "external disk change";
  await evaluate(() => window.flushDrafts());
  await win.webContents.reload();
  await waitFor(() => window.fileStateFixture?.error.includes("file changed on disk"));
  await evaluate(() => window.setFixtureSetting("general.autosave", true));
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal(data["/fixture/sample.ts"], "external disk change");
  await evaluate(() => window.setFixturePath("/fixture/sample.py"));
  await waitFor(() => window.fileStateFixture?.path.endsWith("sample.py") && !window.fileStateFixture.loading);
  assert.equal(await evaluate(() => window.fileStateFixture.commands["edit.formatDocument"].supported), false);
  await evaluate(() => document.querySelector('[data-menu-trigger="Edit"]').click());
  assert.ok(await evaluate(() => [...document.querySelectorAll('[role="menu"] button')].find(button => button.textContent.includes("Format Document")).disabled));
  await evaluate(() => document.querySelector('[data-menu-trigger="Edit"]').click());
  // Production browser IPC and found-in-page events, including Cmd+F dispatched in the guest.
  await waitFor(() => [...document.querySelectorAll('section[aria-label="Integrated browser"] button')].some(button => button.getAttribute("aria-label") === "Find in Page" && !button.disabled));
  const guest = [...native.browserSessions.values()][0].contents;
  guest.focus(); guest.sendInputEvent({ type: "keyDown", keyCode: "F", modifiers: ["meta"] }); guest.sendInputEvent({ type: "keyUp", keyCode: "F", modifiers: ["meta"] });
  await waitFor(() => document.querySelector('[aria-label="Find text in page"]'));
  await evaluate(() => { const field = document.querySelector('[aria-label="Find text in page"]'); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; setter.call(field, "needle"); field.dispatchEvent(new Event("input", { bubbles: true })); });
  await waitFor(() => document.querySelector('[role="search"] [role="status"]').textContent === "1 of 2");
  await evaluate(() => document.querySelector('[aria-label="Next match"]').click());
  await waitFor(() => document.querySelector('[role="search"] [role="status"]').textContent === "2 of 2");
  await evaluate(() => document.querySelector('[aria-label="Previous match"]').click());
  await waitFor(() => document.querySelector('[role="search"] [role="status"]').textContent === "1 of 2");
  await evaluate(() => document.querySelector('[aria-label="Close page find"]').click());
  assert.equal(await evaluate(() => Boolean(document.querySelector('[role="search"]'))), false);
  assert.deepEqual(errors, []);
  await finish();
}).catch(finish);
