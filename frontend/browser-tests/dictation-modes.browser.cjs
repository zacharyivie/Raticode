/* global __dirname, process, setTimeout, clearTimeout, console */
// Both dictation modes in real Electron, with real mouse presses on the mic button and
// Chromium's fake microphone: Hold to talk listens while the button is held (even if the
// pointer slides off it) and stops on release; Press to start and stop toggles. Neither
// needs a Done click, and stopping goes straight on to the tidied words.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const { app, BrowserWindow, session } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("use-fake-device-for-media-stream");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "rem-dictation-modes-"));
app.setPath("userData", profile);
let server, win;
const deadline = setTimeout(() => finish(new Error("Dictation modes browser test timed out")), 90000);
async function finish(error) {
  clearTimeout(deadline);
  if (win && !win.isDestroyed()) win.destroy();
  if (server) await server.close();
  fs.rmSync(profile, { force: true, recursive: true });
  if (error) console.error(error);
  else console.log("Dictation mode browser checks passed: hold to talk listens while held, through a pointer slide, and stops on release; a quick tap explains holding; press to start and stop toggles; both go straight on to the tidied words with no Done.");
  app.exit(error ? 1 : 0);
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, ms = 8000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await check()) return;
    await wait(50);
  }
  throw new Error(`Timed out waiting for ${label}`);
}
const page = (expression) => win.webContents.executeJavaScript(expression);
const mic = () => page(`(() => { const b = document.querySelector("[data-dictation-mode]"); const r = b.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), label: b.getAttribute("aria-label"), pressed: b.getAttribute("aria-pressed") }; })()`);
const log = () => page("({ ...window.dictationLog, tidy: [...window.dictationLog.tidy] })");
const draft = () => page("window.fixtureDraft()");
// Screenshots for a person to look at, when RATICODE_TEST_SHOTS names a folder.
const shot = async (name) => {
  const folder = process.env.RATICODE_TEST_SHOTS;
  if (folder) fs.writeFileSync(path.join(folder, `dictation-${name}.png`), (await win.webContents.capturePage()).toPNG());
};
// While the button is down, moves carry it too, as a real held mouse does.
const mouse = (type, x, y, held = false) => win.webContents.sendInputEvent({ type, x, y, button: "left", clickCount: 1, ...(held ? { modifiers: ["leftButtonDown"] } : {}) });

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === "media"));
  session.defaultSession.setPermissionCheckHandler(() => true);
  const frontend = path.resolve(__dirname, "..");
  const { createServer } = await import("vite");
  server = await createServer({ cacheDir: testViteCacheDir(), root: frontend, configFile: false, logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [(await import("@vitejs/plugin-react")).default(), {
    name: "dictation-modes-fixture", configureServer(dev) { dev.middlewares.use(async (req, res, next) => {
      if (!req.url.startsWith("/?")) return next();
      res.setHeader("Content-Type", "text/html");
      res.end(await dev.transformIndexHtml("/", '<!doctype html><html><body><div id="root"></div><script type="module" src="/browser-tests/dictation-modes.jsx"></script></body></html>'));
    }); },
  }] });
  await server.listen();
  const url = server.resolvedUrls.local[0];
  win = new BrowserWindow({ show: true, width: 900, height: 600, webPreferences: { sandbox: false, contextIsolation: true } });

  // Hold to talk.
  await win.loadURL(`${url}?mode=hold`);
  await until(() => page("Boolean(document.querySelector('[data-dictation-mode=hold]'))"), "the composer");
  let button = await mic();
  assert.equal(button.label, "Dictate a message");
  mouse("mouseDown", button.x, button.y);
  await until(async () => (await mic()).label === "Let go to stop dictating", "listening while held");
  await until(async () => (await log()).chunks >= 2, "microphone audio reaching the recognizer");
  // Sliding off the button while still holding doesn't stop it.
  await page(`(() => { window.pointerLog = []; const b = document.querySelector("[data-dictation-mode]"); for (const t of ["pointerup", "pointercancel", "lostpointercapture", "gotpointercapture", "pointerleave"]) b.addEventListener(t, () => window.pointerLog.push(t)); })()`);
  mouse("mouseMove", button.x + 150, button.y + 100, true);
  mouse("mouseMove", button.x + 300, button.y + 200, true);
  await wait(400);
  assert.deepEqual(JSON.parse(await page("JSON.stringify(window.pointerLog)")), [], "the held pointer stays captured");
  assert.equal((await mic()).pressed, "true");
  assert.equal((await log()).finished, 0);
  assert.equal(await page("document.body.innerText.includes('Done')"), false, "no Done button");
  await shot("holding");
  mouse("mouseUp", button.x + 300, button.y + 200, true);
  await until(async () => (await draft()) === "Note: Book a room for Friday.", "the tidied words after letting go");
  assert.deepEqual((await log()).tidy, ["book a room for friday"]);
  assert.equal((await mic()).pressed, "false");
  await wait(400);
  await shot("after-hold");

  // A quick tap explains holding and changes nothing.
  button = await mic();
  mouse("mouseDown", button.x, button.y);
  mouse("mouseUp", button.x, button.y);
  await until(() => page("document.body.innerText.includes('Hold to talk: keep the mic pressed')"), "the hold hint");
  await wait(600);
  assert.equal(await draft(), "Note: Book a room for Friday.");
  assert.equal((await log()).tidy.length, 1);

  // Press to start and stop.
  await win.loadURL(`${url}?mode=toggle`);
  await until(() => page("Boolean(document.querySelector('[data-dictation-mode=toggle]'))"), "the composer in toggle mode");
  button = await mic();
  mouse("mouseDown", button.x, button.y);
  mouse("mouseUp", button.x, button.y);
  await until(async () => (await mic()).label === "Stop dictating", "listening after one press");
  // Chromium's fake microphone beeps about once a second; wait for some sound.
  await until(async () => (await log()).chunks >= 8, "audio in toggle mode");
  assert.equal((await log()).finished, 0, "letting go of the press doesn't stop it");
  // The listening line opens above the buttons, so the mic has moved down.
  button = await mic();
  mouse("mouseDown", button.x, button.y);
  mouse("mouseUp", button.x, button.y);
  await until(async () => (await draft()) === "Note: Book a room for Friday.", "the tidied words after the second press");
  assert.deepEqual((await log()).tidy, ["book a room for friday"]);
  assert.equal(await page("document.body.innerText.includes('Done')"), false, "no Done button");

  // A slow tidy round while listening: the words stop changing (a pause), so a round goes out
  // even though audio keeps streaming, and capture carries on the whole time it is out.
  await win.loadURL(`${url}?mode=toggle&slowTidy=3000`);
  await until(() => page("Boolean(document.querySelector('[data-dictation-mode=toggle]'))"), "the composer for the slow round");
  button = await mic();
  mouse("mouseDown", button.x, button.y);
  mouse("mouseUp", button.x, button.y);
  await until(async () => (await log()).chunks >= 2, "audio before the pause round");
  await until(async () => (await log()).tidyOut === 1, "a pause round while audio keeps streaming", 6000);
  const atStart = (await log()).chunks;
  await wait(1500);
  const during = await log();
  assert.equal(during.tidyOut, 1, "the round is still out");
  assert.ok(during.chunks - atStart >= 4, `audio kept flowing while the round was out (${during.chunks - atStart} chunks in 1.5 s)`);
  assert.equal((await mic()).label, "Stop dictating", "still listening");
  button = await mic();
  mouse("mouseDown", button.x, button.y);
  mouse("mouseUp", button.x, button.y);
  await until(async () => (await draft()) === "Note: Book a room for Friday.", "the words after stopping", 10000);
  await finish();
}).catch(finish);
