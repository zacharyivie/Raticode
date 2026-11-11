// The media bar's page scripts in real headless Chrome, Edge and Firefox: what
// a tab reports, and the page's own previous, next and play/pause. Each group
// is skipped when its browser isn't installed. The page plays silence.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { CHROME, EDGE, createChromiumEngine, findBrowser } = require("../chromium-engine.cjs");
const { ChromiumContents } = require("../chromium-contents.cjs");
const { createFirefoxEngine, findFirefox } = require("../firefox-engine.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");
const { watchMedia } = require("../browser-media.cjs");

function chromeForTesting() {
  const root = path.join(os.homedir(), ".cache", "ms-playwright");
  try {
    for (const entry of fs.readdirSync(root).filter((name) => /^chromium-\d+$/.test(name)).sort().reverse()) {
      const candidate = path.join(root, entry, "chrome-linux64", "chrome");
      if (fs.existsSync(candidate)) return candidate;
    }
  } catch { /* No Playwright downloads. */ }
  return "";
}

const CHROME_PATH = process.env.RATICODE_SKIP_CHROME_TESTS === "1" ? "" : (findBrowser(CHROME) || (process.platform === "linux" ? chromeForTesting() : ""));
const EDGE_PATH = process.env.RATICODE_SKIP_EDGE_TESTS === "1" ? "" : (findBrowser(EDGE) || (process.platform === "linux" ? chromeForTesting() : ""));
const FIREFOX_PATH = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

const ENGINES = {
  chrome: {
    path: CHROME_PATH,
    engine: (dataDir) => createChromiumEngine({ dataDir, env: { ...process.env, RATICODE_CHROME_PATH: CHROME_PATH }, idleShutdownMs: 1000 }),
    contents: (engine) => new ChromiumContents({ engine, kind: "user", nativeImage: fakeImage }),
  },
  edge: {
    path: EDGE_PATH,
    engine: (dataDir) => createChromiumEngine({ browser: EDGE, dataDir, env: { ...process.env, RATICODE_EDGE_PATH: EDGE_PATH }, idleShutdownMs: 1000 }),
    contents: (engine) => new ChromiumContents({ engine, kind: "user", nativeImage: fakeImage }),
  },
  firefox: {
    path: FIREFOX_PATH,
    engine: (dataDir) => createFirefoxEngine({ dataDir, idleShutdownMs: 1000 }),
    contents: (engine) => new FirefoxContents({ engine, kind: "user", nativeImage: fakeImage }),
  },
};

const WAV = `const rate = 8000, samples = rate * 2, buffer = new ArrayBuffer(44 + samples * 2), view = new DataView(buffer);
  const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
  const url = URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));`;

const PAGES = {
  "/player": `<!doctype html><title>Player page</title><body><script>
    ${WAV}
    const audio = new Audio(url); audio.loop = true; document.body.append(audio);
    navigator.mediaSession.metadata = new MediaMetadata({ title: "First song", artist: "The Band", artwork: [{ src: "https://art.test/small.jpg", sizes: "48x48" }, { src: "https://art.test/big.jpg", sizes: "128x128" }] });
    let track = 0;
    navigator.mediaSession.setActionHandler("nexttrack", () => { track += 1; document.title = "next:" + track; navigator.mediaSession.metadata.title = "Song " + track; });
    navigator.mediaSession.setActionHandler("previoustrack", () => { track -= 1; document.title = "previous:" + track; });
  </script>`,
  "/plain": `<!doctype html><title>Plain page</title><body><script>
    ${WAV}
    const audio = new Audio(url); audio.loop = true; document.body.append(audio);
  </script>`,
  "/muted": `<!doctype html><title>Hero page</title><body><script>
    ${WAV}
    const audio = new Audio(url); audio.loop = true; audio.muted = true; document.body.append(audio);
  </script>`,
  "/quiet": "<!doctype html><title>Quiet page</title><body>Nothing playing</body>",
};

async function waitFor(check, message, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

for (const [name, spec] of Object.entries(ENGINES)) {
  const skip = { skip: spec.path ? false : `${name} is not installed` };

  test(`${/^[aeiou]/.test(name) ? "an" : "a"} ${name} tab reports what it plays and obeys the media bar`, skip, async () => {
    const server = http.createServer((request, response) => {
      response.setHeader("Content-Type", "text/html");
      response.end(PAGES[request.url] || "");
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const base = `http://127.0.0.1:${server.address().port}`;
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-media-test-`));
    const engine = spec.engine(dataDir);
    const contents = spec.contents(engine);
    const watcher = watchMedia(contents, { audible: () => contents.isCurrentlyAudible(), muted: () => false });
    const start = () => contents.evaluateIn("test", "document.querySelector('audio').play().then(() => true)", { userGesture: true, userActivation: true });
    const pollSnapshot = (check, message) => waitFor(() => { contents.emit("media-started-playing"); const media = watcher.snapshot(); return check(media) && (media || true); }, message);
    try {
      await contents.ready;
      await contents.loadURL(`${base}/player`);
      contents.setView({ width: 800, height: 600, scale: 1 }, true);

      // The page's API still looks like the browser's own.
      assert.equal(await contents.evaluateIn("", "typeof navigator.mediaSession.setActionHandler"), "function");
      assert.equal(await contents.evaluateIn("", "navigator.mediaSession.setActionHandler.name"), "setActionHandler");

      await start();
      const playing = await pollSnapshot((media) => media?.playing, "the playing page never reached the bar");
      assert.deepEqual([playing.title, playing.artist, playing.artwork, playing.canPlayPause, playing.canPrevious, playing.canNext], ["First song", "The Band", "https://art.test/big.jpg", true, true, true]);

      assert.deepEqual(await watcher.control("next"), { handled: true });
      await waitFor(async () => (await contents.evaluateIn("test", "document.title")) === "next:1", "next did not reach the page's handler");
      await pollSnapshot((media) => media?.title === "Song 1", "the new track's title did not reach the bar");
      assert.deepEqual(await watcher.control("previous"), { handled: true });
      await waitFor(async () => (await contents.evaluateIn("test", "document.title")) === "previous:0", "previous did not reach the page's handler");

      // Pause and resume, with the bar kept for the Play button in between.
      assert.deepEqual(await watcher.control("play-pause"), { handled: true });
      await waitFor(() => contents.evaluateIn("test", "document.querySelector('audio').paused"), "pause did not reach the element");
      const paused = await pollSnapshot((media) => media && !media.playing, "the bar did not show the pause");
      assert.equal(paused.canPlayPause, true);
      assert.deepEqual(await watcher.control("play-pause"), { handled: true });
      await waitFor(async () => !(await contents.evaluateIn("test", "document.querySelector('audio').paused")), "play did not reach the element");

      // A new page has none of the old page's media, even if its sound lingers.
      await contents.loadURL(`${base}/quiet`);
      assert.equal(watcher.snapshot(), null, "the bar outlived the page");
      await new Promise((resolve) => setTimeout(resolve, 3000));
      contents.emit("did-stop-loading");
      await new Promise((resolve) => setTimeout(resolve, 200));
      assert.equal(watcher.snapshot(), null, "a page with no media opened the bar");

      // No handlers: only play and pause are offered, and next reports nothing done.
      await contents.loadURL(`${base}/plain`);
      await start();
      const plain = await pollSnapshot((media) => media?.playing, "a plain player never reached the bar");
      assert.deepEqual([plain.title, plain.artist, plain.canPlayPause, plain.canPrevious, plain.canNext], ["Plain page", "", true, false, false]);
      assert.deepEqual(await watcher.control("next"), { handled: false });

      // A silent, muted autoplaying element isn't playing media.
      await contents.loadURL(`${base}/muted`);
      await start();
      await new Promise((resolve) => setTimeout(resolve, 2500));
      contents.emit("media-started-playing");
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.equal(watcher.snapshot(), null, "a muted element opened the bar");
    } finally {
      watcher.stop();
      contents.close();
      await engine.stopAndWait?.();
      server.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
}
