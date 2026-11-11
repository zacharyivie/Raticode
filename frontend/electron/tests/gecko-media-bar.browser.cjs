// The media bar following a real Gecko tab: the real Gecko host, one real
// Firefox and its MPRIS player, and the real bar with the studio's styles. No
// app build is needed. Runs on a hidden display with a private session bus and
// a throwaway home folder. The page's sound is far below hearing.
// Set RATICODE_MEDIA_BAR_SHOT to a file path to save a picture of the bar.
require("./background-display.cjs").runInBackgroundDisplay();
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { BrowserWindow, app, ipcMain } = require("electron");
const { findFirefox } = require("../firefox-engine.cjs");
const { createGeckoWindow } = require("../gecko-window.cjs");

const frontend = path.join(__dirname, "..", "..");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-bar-test-"));
console.log(`RATICODE_TEST_PROFILE=${root}`);
app.setPath("userData", path.join(root, "electron"));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Gecko media bar test timed out"); app.exit(1); }, 120000);

// Firefox only publishes media of at least three seconds that makes sound.
const WAV = `const rate = 8000, samples = rate * 8, buffer = new ArrayBuffer(44 + samples * 2), view = new DataView(buffer);
  const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
  for (let i = 0; i < samples; i++) view.setInt16(44 + i * 2, Math.round(8 * Math.sin(i / 3)), true);
  const audio = new Audio(URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }))); audio.loop = true; document.body.append(audio);`;
const ART = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const PAGES = {
  "/player": `<!doctype html><title>Player page</title><body><script>${WAV}
    navigator.mediaSession.metadata = new MediaMetadata({ title: "First song", artist: "The Band", artwork: [{ src: "/art.png", sizes: "96x96", type: "image/png" }] });
    let track = 0;
    navigator.mediaSession.setActionHandler("nexttrack", () => { track += 1; if (track === 2) { location.href = "/plain"; return; } navigator.mediaSession.metadata.title = "Song " + track; });
    navigator.mediaSession.setActionHandler("previoustrack", () => { navigator.mediaSession.metadata.title = "Back again"; });
    audio.play();
  </script>`,
  "/plain": `<!doctype html><title>Plain page</title><body><script>${WAV} audio.play(); setTimeout(() => { location.href = "/muted"; }, 9000);</script>`,
  "/muted": `<!doctype html><title>Muted page</title><body><script>${WAV} audio.muted = true; audio.play(); setTimeout(() => { location.href = "/last"; }, 7000);</script>`,
  "/last": `<!doctype html><title>Last page</title><body><script>${WAV} audio.play();</script>`,
};

async function waitFor(check, message, timeout = 25000) {
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
      contents: `import { createRoot } from "react-dom/client"; import Bar from "./src/components/BrowserMediaBar.jsx"; createRoot(document.getElementById("bar")).render(<Bar />);`,
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
  const css = await postcss([tailwind()]).process(input, { from: path.join(frontend, "src", "styles", "gecko-media-bar-test.css") });
  return { script: script.outputFiles[0].text, css: css.css };
}

async function run() {
  let esbuild;
  try {
    esbuild = require("esbuild");
  } catch {
    console.log("esbuild is not installed; skipping the Gecko media bar test.");
    return;
  }
  const home = path.join(root, "home");
  const env = { ...process.env, HOME: home, XDG_CONFIG_HOME: path.join(home, ".config"), XDG_CACHE_HOME: path.join(home, ".cache"), XDG_DATA_HOME: path.join(home, ".local", "share") };
  fs.mkdirSync(home, { recursive: true });
  if (!findFirefox({ platform: process.platform, env })) {
    console.log("Firefox is not installed; skipping the Gecko media bar test.");
    return;
  }
  const dataDir = path.join(root, "data");
  const firefoxProfile = path.join(dataDir, "gecko", "profiles", "default");
  fs.mkdirSync(firefoxProfile, { recursive: true });
  // Only so the test page can start its sound without a click.
  fs.writeFileSync(path.join(firefoxProfile, "prefs.js"), 'user_pref("media.autoplay.default", 0);\n');

  const assets = await compileBar(esbuild);
  const host = `<!doctype html><style>${assets.css}</style><body class="bg-canvas text-ink" style="margin:0">
    <section style="display:flex;flex-direction:column;width:100%;height:100vh;overflow:hidden"><div id="bar"></div><div id="slot" style="flex:1"></div></section>
    <script>${assets.script.replace(/<\/script/gi, "<\\/script")}</script>`;
  const server = http.createServer((request, response) => {
    if (request.url === "/art.png") { response.setHeader("Content-Type", "image/png"); response.end(ART); return; }
    response.setHeader("Content-Type", "text/html");
    response.end(request.url === "/host" ? host : PAGES[request.url] || "");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;

  let window = null;
  const gecko = createGeckoWindow({
    dataDir,
    env,
    x11Switch: true,
    findProfiles: () => [],
    getWindow: () => window,
    log: (level, message) => console.log(`[${level}] ${message}`),
    onState: (state) => { if (window && !window.isDestroyed()) window.webContents.send("gofer:gecko-state", state); },
  });
  ipcMain.handle("gofer:gecko", (_event, options = {}) => gecko.handle(String(options.action || ""), options));
  ipcMain.handle("gofer:browser-library", () => ({ sessions: [] }));

  const preload = path.join(__dirname, "..", "browser-preload.cjs");
  window = new BrowserWindow({
    width: 900,
    height: 500,
    show: true,
    webPreferences: {
      contextIsolation: true,
      preload: path.join(__dirname, "..", "preload.cjs"),
      sandbox: true,
      additionalArguments: [`--gofer-browser-preload=${pathToFileURL(preload)}`],
    },
  });
  await window.loadURL(`${base}/host`);
  const page = (code) => window.webContents.executeJavaScript(code);
  const bar = "document.querySelector('[data-testid=browser-media-bar]')";
  const button = (label) => `${bar}?.querySelector('[aria-label="${label}"]')`;
  const barText = () => page(`${bar}?.textContent || ""`);
  const hasBar = () => page(`Boolean(${bar})`);
  const disabledControls = () => page(`[${["Previous", "Pause", "Next"].map((label) => `${button(label)}?.disabled`).join(",")}]`);
  async function click(label) {
    const at = await page(`(() => { const box = ${button(label)}.getBoundingClientRect(); return { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2) }; })()`);
    window.webContents.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...at });
    window.webContents.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...at });
  }

  assert.equal(await hasBar(), false, "the bar showed before Gecko started");
  await page(`window.goferBrowser.gecko("place", { x: 0, y: 40, width: 900, height: 440, visible: true })`);
  await page(`window.goferBrowser.gecko("open", { url: ${JSON.stringify(`${base}/player`)} })`);
  await waitFor(async () => /First song/.test(await barText()) && /The Band/.test(await barText()), "Gecko's playback never reached the bar");
  await waitFor(() => page(`${bar}.querySelector('img')?.naturalWidth > 0`), "the thumbnail did not load");
  assert.deepEqual(await disabledControls(), [false, false, false]);
  const height = await page(`${bar}.getBoundingClientRect().height`);
  assert.ok(height > 0 && height <= 30, `the bar is ${height}px tall`);
  if (process.env.RATICODE_MEDIA_BAR_SHOT) fs.writeFileSync(process.env.RATICODE_MEDIA_BAR_SHOT, (await window.webContents.capturePage()).toPNG());

  await click("Next");
  await waitFor(async () => /Song 1/.test(await barText()), "Next did not reach Gecko's page");
  await click("Previous");
  await waitFor(async () => /Back again/.test(await barText()), "Previous did not reach Gecko's page");
  await click("Pause");
  await waitFor(() => page(`Boolean(${button("Play")})`), "Pause did not reach Gecko");
  assert.equal(await hasBar(), true, "pausing closed the bar");
  await click("Play");
  await waitFor(() => page(`Boolean(${button("Pause")})`), "Play did not reach Gecko");

  // A new page: its own title, and no previous or next it doesn't offer.
  await click("Next");
  await waitFor(async () => /Plain page/.test(await barText()), "navigation did not reach the bar");
  await waitFor(async () => JSON.stringify(await disabledControls()) === "[true,false,true]", "the plain page's controls were wrong");

  // A muted page isn't playing media; the page after it is.
  await waitFor(async () => !(await hasBar()), "the bar stayed open for a muted page");
  await new Promise((resolve) => setTimeout(resolve, 2500));
  assert.equal(await hasBar(), false, "a muted page reopened the bar");
  await waitFor(async () => /Last page/.test(await barText()), "the last page did not open the bar");

  // Closing Gecko closes the bar.
  await page(`window.goferBrowser.gecko("stop")`);
  await waitFor(async () => !(await hasBar()), "the bar outlived Firefox");
  assert.equal(await page(`window.goferBrowser.gecko("status").then((status) => status.media)`), null);

  gecko.close();
  server.close();
  console.log("Gecko media bar checks passed.");
}

function finish(code) {
  clearTimeout(timer);
  fs.rmSync(root, { recursive: true, force: true });
  app.exit(code);
}

app.whenReady().then(run).then(() => finish(0)).catch((error) => {
  console.error(error);
  finish(1);
});
