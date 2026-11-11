// The media bar through a real tab: a <webview> with Raticode's own preload,
// the real browser sessions and bridge, and the real bar component drawn with
// the studio's styles. Small enough to need no app build: esbuild and Tailwind
// compile only the bar. Runs on a hidden display; the page plays silence.
// Set RATICODE_MEDIA_BAR_SHOT to a file path to save a picture of the bar.
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-media-bar-test-"));
app.setPath("userData", profile);
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Media bar test timed out"); app.exit(1); }, 90000);

const WAV = `const rate = 8000, samples = rate * 2, buffer = new ArrayBuffer(44 + samples * 2), view = new DataView(buffer);
  const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
  const audio = new Audio(URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }))); audio.loop = true; document.body.append(audio);`;
const ART = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const PAGES = {
  "/player": `<!doctype html><title>Player page</title><body><script>${WAV}
    navigator.mediaSession.metadata = new MediaMetadata({ title: "First song", artist: "The Band", artwork: [{ src: "${ART}", sizes: "128x128", type: "image/png" }] });
    let track = 0;
    navigator.mediaSession.setActionHandler("nexttrack", () => { track += 1; document.title = "next:" + track; navigator.mediaSession.metadata.title = "Song " + track; });
    navigator.mediaSession.setActionHandler("previoustrack", () => { track -= 1; document.title = "previous:" + track; });
  </script>`,
  "/plain": `<!doctype html><title>Plain page</title><body><script>${WAV}</script>`,
  "/muted": `<!doctype html><title>Muted page</title><body><script>${WAV} audio.muted = true;</script>`,
  "/quiet": "<!doctype html><title>Quiet page</title><body>Nothing playing</body>",
};

async function waitFor(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function compileBar(esbuild) {
  const { default: tailwind } = await import("@tailwindcss/postcss");
  const { default: postcss } = await import("postcss");
  const script = await esbuild.build({
    stdin: {
      // The studio tells the bar which tabs are on screen; the test sets it with window.viewTabs.
      contents: `import { useState } from "react"; import { createRoot } from "react-dom/client"; import Bar from "./src/components/BrowserMediaBar.jsx";
        function Host() { const [viewed, setViewed] = useState([]); window.viewTabs = setViewed; return <Bar viewedTabs={viewed} />; }
        createRoot(document.getElementById("bar")).render(<Host />);`,
      loader: "jsx",
      resolveDir: frontend,
    },
    bundle: true,
    write: false,
    jsx: "automatic",
    format: "iife",
    define: { "process.env.NODE_ENV": "\"production\"" },
  });
  const input = `@import "tailwindcss" source(none); @import "./palette.css"; @import "./primitives.css"; @config "../../tailwind.config.js"; @source "../components/BrowserMediaBar.jsx";`;
  const css = await postcss([tailwind()]).process(input, { from: path.join(frontend, "src", "styles", "media-bar-test.css") });
  return { script: script.outputFiles[0].text, css: css.css };
}

async function run() {
  let esbuild;
  try {
    esbuild = require("esbuild");
  } catch {
    console.log("esbuild is not installed; skipping the media bar test.");
    return;
  }
  const assets = await compileBar(esbuild);
  const host = `<!doctype html><style>${assets.css}</style><body class="bg-canvas text-ink" style="margin:0">
    <section style="display:flex;flex-direction:column;width:100%;height:100vh;overflow:hidden"><div id="bar"></div><input id="typing" aria-label="Typing here">
    <div id="view" style="position:relative;flex:1"></div></section>
    <script>
      window.openTab = async (url) => {
        const state = await window.goferBrowser.create({ url, clientId: "tab" });
        const element = document.createElement("webview");
        element.setAttribute("partition", state.partition);
        element.setAttribute("preload", window.goferBrowser.preloadPath);
        element.setAttribute("src", state.src);
        element.style.cssText = "position:absolute;inset:0";
        element.addEventListener("dom-ready", () => void window.goferBrowser.adopt(state.id, element.getWebContentsId(), "tab"), { once: true });
        document.getElementById("view").append(element);
        window.tab = { id: state.id, element };
        return state.id;
      };
    </script><script>${assets.script.replace(/<\/script/gi, "<\\/script")}</script>`;
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end(request.url === "/host" ? host : PAGES[request.url] || "");
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

  const preload = path.join(__dirname, "..", "browser-preload.cjs");
  window = new BrowserWindow({
    width: 900,
    height: 420,
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
  const bar = "document.querySelector('[data-testid=browser-media-bar]')";
  const button = (label) => `${bar}?.querySelector('[aria-label="${label}"]')`;
  const disabledControls = () => page(`[${["Previous", "Pause", "Next"].map((label) => `${button(label)}?.disabled`).join(",")}]`);
  const barText = () => page(`${bar}?.textContent || ""`);
  const hasBar = () => page(`Boolean(${bar})`);
  const guest = () => webContents.getAllWebContents().find((item) => item.getType() === "webview" && !item.isDestroyed());
  const play = () => guest().executeJavaScript("document.querySelector('audio').play().then(() => true)", true);
  const inGuest = (code) => guest().executeJavaScript(code);
  async function click(label) {
    const at = await page(`(() => { const box = ${button(label)}.getBoundingClientRect(); return { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2) }; })()`);
    window.webContents.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...at });
    window.webContents.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...at });
  }
  const load = async (route) => {
    await page(`window.goferBrowser.navigate(window.tab.id, ${JSON.stringify(`${base}${route}`)})`);
    await waitFor(async () => guest()?.getURL() === `${base}${route}` && !guest().isLoading(), `${route} did not load`);
  };

  // Nothing playing: no bar, and no space taken.
  await page(`window.openTab(${JSON.stringify(`${base}/player`)})`);
  await waitFor(() => guest() && guest().getURL() === `${base}/player` && !guest().isLoading(), "the tab did not load");
  await waitFor(() => page("window.tab && window.goferBrowser.mediaSessions().then(() => true)"), "the bridge did not answer");
  await new Promise((resolve) => setTimeout(resolve, 800));
  assert.equal(await hasBar(), false, "a loaded page that plays nothing opened the bar");
  assert.equal(await page("document.getElementById('bar').offsetHeight"), 0);
  assert.equal(await inGuest("navigator.mediaSession.setActionHandler.name"), "setActionHandler");

  // It starts: title, artist, thumbnail and the page's own controls.
  await page("document.getElementById('typing').focus()");
  await play();
  await waitFor(hasBar, "playback never opened the bar");
  await waitFor(async () => /First song/.test(await barText()) && /The Band/.test(await barText()), "the bar did not show the track");
  await waitFor(() => page(`${bar}.querySelector('img')?.naturalWidth > 0`), "the thumbnail did not load");
  const height = await page(`${bar}.getBoundingClientRect().height`);
  assert.ok(height > 0 && height <= 30, `the bar is ${height}px tall`);
  assert.deepEqual(await disabledControls(), [false, false, false]);

  // Looking at the playing tab hides the bar, and looking away brings it back.
  await page("window.viewTabs(['tab']); true");
  await waitFor(async () => !(await hasBar()), "the bar stayed over the tab that plays");
  assert.equal(await page("document.getElementById('bar').offsetHeight"), 0, "the hidden bar still takes space");
  assert.equal(await inGuest("document.querySelector('audio').paused"), false, "hiding the bar stopped playback");
  await page("window.viewTabs(['some-other-tab']); true");
  await waitFor(hasBar, "the bar didn't return on another tab");
  await page("window.viewTabs([]); true");

  if (process.env.RATICODE_MEDIA_BAR_SHOT) {
    const image = await window.webContents.capturePage();
    fs.writeFileSync(process.env.RATICODE_MEDIA_BAR_SHOT, image.toPNG());
  }

  // Controls work with real clicks while the tab is hidden, as in another area.
  await page("window.tab.element.style.display = 'none'");
  await click("Next");
  await waitFor(async () => (await inGuest("document.title")) === "next:1", "Next did not reach the page");
  await waitFor(async () => /Song 1/.test(await barText()), "the bar did not follow the new track");
  await click("Previous");
  await waitFor(async () => (await inGuest("document.title")) === "previous:0", "Previous did not reach the page");
  assert.equal(await page("document.activeElement.id"), "typing", "a control took the keyboard");
  assert.equal(await inGuest("document.querySelector('audio').paused"), false, "playback stopped while the tab was hidden");

  // Pause keeps the bar with a Play button; Play resumes.
  await click("Pause");
  await waitFor(() => inGuest("document.querySelector('audio').paused"), "Pause did not reach the page");
  await waitFor(() => page(`Boolean(${button("Play")})`), "the toggle did not change to Play");
  assert.equal(await hasBar(), true);
  await click("Play");
  await waitFor(async () => !(await inGuest("document.querySelector('audio').paused")), "Play did not resume");
  await waitFor(() => page(`Boolean(${button("Pause")})`), "the toggle did not change back");
  await page("window.tab.element.style.display = ''");

  // A same-page address change keeps playing and keeps the bar.
  await inGuest("history.pushState({}, '', '/player?v=2'); true");
  await new Promise((resolve) => setTimeout(resolve, 1200));
  assert.equal(await hasBar(), true, "a same-page change closed the bar");

  // Leaving the page closes it, and a quiet page doesn't reopen it.
  await load("/quiet");
  await waitFor(async () => !(await hasBar()), "the bar outlived the page");
  await new Promise((resolve) => setTimeout(resolve, 2500));
  assert.equal(await hasBar(), false, "a page with no media reopened the bar");

  // A page with no media session: its own title, and only play/pause works.
  await load("/plain");
  await play();
  await waitFor(hasBar, "a plain player never opened the bar");
  assert.match(await barText(), /Plain page/);
  assert.deepEqual(await disabledControls(), [true, false, true]);
  assert.equal(await page(`${bar}.querySelector('img')`), null, "no picture to show");
  assert.match(await page(`${button("Next")}.title`), /no next track/);

  // A muted element is not playing media.
  await load("/muted");
  await waitFor(async () => !(await hasBar()), "the bar outlived the plain page");
  await play();
  await new Promise((resolve) => setTimeout(resolve, 2500));
  assert.equal(await hasBar(), false, "a muted element opened the bar");

  // Closing the tab closes the bar.
  await load("/plain");
  await play();
  await waitFor(hasBar, "the plain player did not reopen the bar");
  await page("window.goferBrowser.close(window.tab.id)");
  await waitFor(async () => !(await hasBar()), "the bar outlived its tab");

  server.close();
  console.log("Media bar checks passed.");
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
