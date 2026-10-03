/* global window, document, Event, DataTransfer, DragEvent, KeyboardEvent, __dirname, console, setTimeout, clearTimeout */
const { app, BrowserWindow, ipcMain, protocol } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const { createMediaPreviews } = require("../electron/media-preview.cjs");
const safeFiles = require("../electron/safe-files.cjs");
const { inspectPath } = require("../electron/path-info.cjs");
const { studioCsp } = require("../electron/studio-policy.cjs");
const { createIpcSecurity } = require("../electron/security.cjs");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
protocol.registerSchemesAsPrivileged([{ scheme: "raticode-media", privileges: { standard: true, secure: true, stream: true } }]);
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "rem-file-media-browser-")));
const previews = createMediaPreviews();
let server, win;
const requests = [], errors = [];
const deadline = setTimeout(() => finish(new Error("File/media browser test timed out")), 90000);
async function finish(error) {
  clearTimeout(deadline);
  if (win && !win.isDestroyed()) win.destroy();
  if (server) await server.close();
  fs.rmSync(root, { force: true, recursive: true });
  if (error) console.error(error, errors);
  else console.log("File/media browser checks passed: duplicate decisions, range selection and group drags, copy/cut across projects, tree moves, native imports, tab drops, animated GIF, PNG, MP4/WebM video, MP3/WAV audio, seeking and inactive playback pause.");
  app.exit(error ? 1 : 0);
}
app.whenReady().then(async () => {
  fs.mkdirSync(`${root}/a/docs`, { recursive: true }); fs.mkdirSync(`${root}/b`); fs.mkdirSync(`${root}/outside`);
  fs.writeFileSync(`${root}/a/note.txt`, "test file");
  for (const name of ["batch-1.txt", "batch-2.txt", "batch-3.txt"]) {
    fs.writeFileSync(`${root}/a/${name}`, `incoming ${name}`);
    fs.writeFileSync(`${root}/a/docs/${name}`, `old ${name}`);
  }
  function ffmpeg(args, name) { execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args, "-threads", "1", `${root}/outside/${name}`]); }
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=2", "-frames:v", "1"], "image.png");
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=2"], "animation.gif");
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"], "movie.mp4");
  ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=2", "-c:v", "libvpx-vp9"], "movie.webm");
  ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2"], "sound.wav");
  ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2"], "sound.mp3");
  protocol.handle("raticode-media", request => { requests.push(request.headers.get("range")); return previews.handle(request); });
  const frontend = path.resolve(__dirname, "..");
  const { createServer } = await import("vite");
  server = await createServer({ root: frontend, configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [(await import("@vitejs/plugin-react")).default(), {
    name: "file-media-fixture", configureServer(dev) { dev.middlewares.use(async (req, res, next) => {
      if (!req.url.startsWith("/?")) return next();
      res.setHeader("Content-Type", "text/html");
      res.setHeader("Content-Security-Policy", studioCsp({ apiBaseUrl: dev.resolvedUrls.local[0], devServerUrl: dev.resolvedUrls.local[0], isProduction: false }));
      res.end(await dev.transformIndexHtml("/", '<!doctype html><html><body><div id="root"></div><script type="module" src="/browser-tests/file-media.jsx"></script></body></html>'));
    }); },
  }] });
  await server.listen();
  const url = server.resolvedUrls.local[0];
  win = new BrowserWindow({ show: true, width: 1200, height: 850, webPreferences: { preload: path.join(frontend, "electron/preload.cjs"), sandbox: false, contextIsolation: true, nodeIntegration: false } });
  const security = createIpcSecurity({ appRoots: [frontend], getDataDir: () => root, devServerUrl: url, isProduction: false, getMainWebContents: () => win.webContents });
  function handle(channel, callback) { ipcMain.handle(channel, security.secureHandler(callback)); }
  handle("gofer:grant-path", (_event, { targetPath }) => ({ path: targetPath, grantId: "fixture" }));
  handle("gofer:git-status", () => ({ active: false }));
  handle("gofer:list-directory", async (_event, { currentPath }) => ({ entries: (await fs.promises.readdir(currentPath, { withFileTypes: true })).map(entry => ({ name: entry.name, path: path.join(currentPath, entry.name), isDirectory: entry.isDirectory(), isFile: entry.isFile() })) }));
  handle("gofer:path-info", (_event, { targetPath }) => inspectPath(targetPath));
  // Exercise production IPC handlers as well as the real preload. A simplified
  // fixture handler can hide a dropped replacement flag at either boundary.
  const mainSource = fs.readFileSync(path.join(frontend, "electron/main.js"), "utf8");
  const transfers = vm.runInNewContext(`${mainSource.slice(mainSource.indexOf("async function copyPath("), mainSource.indexOf("async function deletePath("))}\n({ copyPath, movePath })`, {
    fs, path, safeFiles,
    resolveExactPath: (target, options) => security.resolveDesktopPath(target, options),
    pathHandle: target => security.grantUserPath(target),
  });
  handle("gofer:copy-path", transfers.copyPath);
  handle("gofer:move-path", transfers.movePath);
  handle("gofer:open-media-preview", (event, { targetPath }) => previews.open(targetPath, event.sender.id));
  handle("gofer:close-media-preview", (event, { id }) => previews.close(id, event.sender.id));
  handle("gofer:read-text-file", async (_event, { targetPath }) => ({ content: await fs.promises.readFile(targetPath, "utf8") }));
  handle("gofer:git-file-baseline", () => ({ tracked: false, changed: false }));
  handle("gofer:renderer-log", () => true);
  win.webContents.on("console-message", ({ level, message }) => { if (level === "error") errors.push(message); });
  await win.loadURL(`${url}?root=${encodeURIComponent(root)}`);
  win.webContents.setZoomFactor(1);
  const evaluate = fn => win.webContents.executeJavaScript(`(${fn})()`);
  const evalArgs = (fn, ...args) => win.webContents.executeJavaScript(`(${fn})(...${JSON.stringify(args)})`);
  async function waitFor(fn) { for (let i = 0; i < 250; i++) { if (await evaluate(fn)) return; await new Promise(resolve => setTimeout(resolve, 40)); } console.log(await evaluate(() => document.body.innerText)); throw new Error(`Timed out: ${fn}`); }
  await waitFor(() => document.querySelector('[title$="/a/note.txt"]'));
  await evaluate(() => document.querySelector('[title$="/a/note.txt"]').focus());
  await waitFor(() => !document.querySelector('[aria-label="Copy selected file"]').disabled);
  await evaluate(() => document.querySelector('[title$="/a/note.txt"]').dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "c", ctrlKey: true })));
  await evalArgs(project => window.selectFixtureProject(project), `${root}/b`);
  await waitFor(() => document.querySelector('[data-path$="/b"][role="treeitem"]') && !document.querySelector('[aria-label="Paste files"]').disabled);
  await evaluate(() => document.querySelector('[aria-label="Paste files"]').click());
  await waitFor(() => document.querySelector('[title$="/b/note.txt"]'));
  assert.equal(fs.readFileSync(`${root}/b/note.txt`, "utf8"), "test file");
  await evaluate(() => document.querySelector('[title$="/b/note.txt"]').focus());
  await waitFor(() => !document.querySelector('[aria-label="Cut selected file"]').disabled);
  await evaluate(() => document.querySelector('[title$="/b/note.txt"]').dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "x", ctrlKey: true })));
  await evalArgs(project => window.selectFixtureProject(project), `${root}/a`);
  await waitFor(() => document.querySelector('[title$="/a/docs"]') && !document.querySelector('[aria-label="Paste files"]').disabled);
  await evaluate(() => document.querySelector('[title$="/a/docs"]').focus());
  await waitFor(() => document.querySelector('[title$="/a/docs"]').getAttribute("aria-selected") === "true");
  await evaluate(() => document.querySelector('[aria-label="Paste files"]').click());
  await waitFor(() => document.querySelector('[title$="/a/docs/note.txt"]'));
  assert.equal(fs.existsSync(`${root}/b/note.txt`), false);
  fs.writeFileSync(`${root}/a/docs/note.txt`, "old destination");
  await evaluate(() => document.querySelector('[title$="/a/note.txt"]').click());
  await evaluate(() => document.querySelector('[aria-label="Copy selected file"]').click());
  await evaluate(() => document.querySelector('[title$="/a/docs"]').focus());
  await waitFor(() => document.querySelector('[title$="/a/docs"]').getAttribute("aria-selected") === "true");
  await evaluate(() => document.querySelector('[aria-label="Paste files"]').click());
  await waitFor(() => document.querySelector('[role="dialog"]'));
  await evaluate(() => [...document.querySelectorAll('[role="dialog"] button')].find(button => button.textContent === "Replace file").click());
  await waitFor(() => !document.querySelector('[role="dialog"]') && !document.querySelector('[aria-label="Paste files"]').disabled);
  assert.equal(fs.readFileSync(`${root}/a/docs/note.txt`, "utf8"), "test file");
  assert.equal(fs.readFileSync(`${root}/a/note.txt`, "utf8"), "test file");
  await evaluate(() => {
    const source = document.querySelector('[title$="/a/note.txt"]'), target = document.querySelector('[title$="/a/docs"]'), transfer = new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: transfer }));
    target.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer, ctrlKey: true }));
  });
  await waitFor(() => document.querySelector('[role="dialog"]'));
  await evaluate(() => [...document.querySelectorAll('[role="dialog"] button')].find(button => button.textContent === "Keep both").click());
  await waitFor(() => document.querySelector('[title$="/a/docs/note copy.txt"]'));
  async function clickFile(name, shift = false) {
    const position = await evalArgs(name => {
      const row = document.querySelector(`[title$="/a/${name}"]`);
      const rect = row.getBoundingClientRect();
      return { x: Math.round(rect.left + 60), y: Math.round(rect.top + rect.height / 2) };
    }, name);
    win.webContents.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...position, modifiers: shift ? ["shift"] : [] });
    win.webContents.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...position, modifiers: shift ? ["shift"] : [] });
    await waitFor(() => document.querySelector('[role="treeitem"][data-path$="batch-3.txt"]'));
  }
  await clickFile("batch-1.txt"); await clickFile("batch-3.txt", true);
  await waitFor(() => ["batch-1.txt", "batch-2.txt", "batch-3.txt"].every(name => document.querySelector(`[title$="/a/${name}"]`).getAttribute("aria-selected") === "true"));
  await clickFile("note.txt");
  await waitFor(() => ["batch-1.txt", "batch-2.txt", "batch-3.txt"].every(name => document.querySelector(`[title$="/a/${name}"]`).getAttribute("aria-selected") === "false"));
  await clickFile("batch-1.txt"); await clickFile("batch-3.txt", true);
  await waitFor(() => document.querySelector('[title$="/a/batch-2.txt"]').getAttribute("aria-selected") === "true");
  const dragged = await evaluate(() => {
    const source = document.querySelector('[title$="/a/batch-2.txt"]'), transfer = new DataTransfer();
    source.dispatchEvent(new Event("pointerdown", { bubbles: true })); source.focus();
    source.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: transfer }));
    const entries = JSON.parse(transfer.getData("application/x-raticode-files"));
    document.querySelector('[title$="/a/docs"]').dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
    return entries.map(entry => entry.name);
  });
  assert.deepEqual(dragged, ["batch-1.txt", "batch-2.txt", "batch-3.txt"]);
  await waitFor(() => document.querySelector('[role="dialog"]'));
  await evaluate(() => {
    const checkbox = document.querySelector('[role="dialog"] input[type="checkbox"]'); checkbox.click();
    [...document.querySelectorAll('[role="dialog"] button')].find(button => button.textContent === "Replace file").click();
  });
  await waitFor(() => !document.querySelector('[title$="/a/batch-3.txt"]') && !document.querySelector('[role="dialog"]'));
  for (const name of dragged) {
    assert.equal(fs.existsSync(`${root}/a/${name}`), false);
    assert.equal(fs.readFileSync(`${root}/a/docs/${name}`, "utf8"), `incoming ${name}`);
  }
  // Populate a real File-backed input through Chromium, then drop that native File.
  win.webContents.debugger.attach("1.3");
  const { root: domRoot } = await win.webContents.debugger.sendCommand("DOM.getDocument");
  const { nodeId } = await win.webContents.debugger.sendCommand("DOM.querySelector", { nodeId: domRoot.nodeId, selector: 'input[type="file"]' });
  await win.webContents.debugger.sendCommand("DOM.setFileInputFiles", { nodeId, files: [`${root}/outside/image.png`, `${root}/outside/animation.gif`] });
  await evaluate(() => {
    const transfer = new DataTransfer(); for (const file of document.querySelector('input[type="file"]').files) transfer.items.add(file);
    document.querySelector('[aria-label="Project files"]').dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await waitFor(() => document.querySelector('[title$="/a/image.png"]'));
  assert.equal(fs.existsSync(`${root}/outside/image.png`), true);
  await evaluate(() => {
    const transfer = new DataTransfer(); for (const file of document.querySelector('input[type="file"]').files) transfer.items.add(file);
    document.querySelector('[aria-label="Code workspace"]').dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await waitFor(() => window.fixturePaths.some(path => path.endsWith("/outside/image.png")) && window.fixturePaths.some(path => path.endsWith("/outside/animation.gif")) && document.querySelector('img[src^="raticode-media:"]'));
  await evaluate(async () => { await document.querySelector('[aria-label="animation.gif image preview"] img').decode(); });
  const imageRect = await evaluate(() => { const rect = document.querySelector('[aria-label="animation.gif image preview"] img').getBoundingClientRect(); return { x: Math.floor(rect.x), y: Math.floor(rect.y), width: Math.ceil(rect.width), height: Math.ceil(rect.height) }; });
  const beforeFrame = (await win.webContents.capturePage(imageRect)).toPNG();
  let animationChanged = false;
  for (let frame = 0; frame < 10 && !animationChanged; frame++) {
    await new Promise(resolve => setTimeout(resolve, 240));
    const afterFrame = (await win.webContents.capturePage(imageRect)).toPNG();
    animationChanged = !beforeFrame.equals(afterFrame);
  }
  assert.equal(animationChanged, true, "GIF frames must animate");
  await evaluate(() => document.querySelector('[title$="/a/image.png"]').dispatchEvent(new Event("dblclick", { bubbles: true })));
  await waitFor(() => window.fixturePaths.some(path => path.endsWith("/a/image.png")));
  await evaluate(() => {
    const transfer = new DataTransfer();
    document.querySelector('[title$="/a/image.png"]').dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: transfer }));
    document.querySelector('[title$="/a/docs"]').dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await waitFor(() => window.fixturePaths.some(path => path.endsWith("/a/docs/image.png")));
  assert.equal(fs.existsSync(`${root}/a/image.png`), false);
  assert.equal(fs.existsSync(`${root}/a/docs/image.png`), true);
  assert.equal(await evaluate(() => window.fixturePaths.some(path => path.endsWith("/a/image.png"))), false);
  for (const name of ["movie.mp4", "movie.webm", "sound.wav", "sound.mp3"]) {
    await evalArgs(file => window.openFixtureFiles([file]), `${root}/outside/${name}`);
    await waitFor(() => [...document.querySelectorAll("video,audio")].some(element => element.readyState >= 1 && element.closest('[aria-hidden="false"]')));
    const playback = await evaluate(async () => {
      const element = [...document.querySelectorAll("video,audio")].find(element => element.closest('[aria-hidden="false"]'));
      element.muted = true; await element.play(); await new Promise(resolve => setTimeout(resolve, 180));
      const advances = element.currentTime > 0; element.pause(); element.currentTime = 1;
      await new Promise((resolve, reject) => { element.addEventListener("seeked", resolve, { once: true }); setTimeout(() => reject(new Error("Seek timed out")), 3000); });
      return { advances, seek: element.currentTime, duration: element.duration };
    });
    assert.equal(playback.advances, true, name); assert.equal(playback.seek, 1); assert.ok(playback.duration >= 2);
  }
  await evaluate(() => document.querySelector('audio[aria-label="Play sound.mp3"]').play());
  await evalArgs(file => window.openFixtureFiles([file]), `${root}/outside/image.png`);
  await waitFor(() => document.querySelector('audio[aria-label="Play sound.mp3"]').paused);
  assert.ok(requests.some(range => range?.startsWith("bytes=")), "Media must use byte-range requests");
  assert.deepEqual(errors, []);
  await finish();
}).catch(finish);
