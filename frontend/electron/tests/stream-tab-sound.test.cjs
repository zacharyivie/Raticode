// Firefox, Chrome and Edge tabs say when their page plays sound, so the tab
// shows its speaker and a background tab playing music isn't unloaded to save
// memory. Run against a real headless Chrome, Microsoft Edge and Firefox; each
// group is skipped when its browser isn't installed. The page plays silence,
// so nothing is heard while the tests run. Chrome comes from
// RATICODE_CHROME_PATH, an installed Google Chrome, or a Chrome for Testing
// build Playwright downloaded. Edge comes from RATICODE_EDGE_PATH or an
// installed Edge; without one, the same Chrome for Testing build stands in,
// since Edge is Chromium with the same DevTools protocol.
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
const { soundExpression } = require("../stream-sound.cjs");

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

const ENGINES = {
  chrome: {
    path: CHROME_PATH,
    engine: (dataDir) => createChromiumEngine({ dataDir, env: { ...process.env, RATICODE_CHROME_PATH: CHROME_PATH }, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  edge: {
    path: EDGE_PATH,
    engine: (dataDir) => createChromiumEngine({ browser: EDGE, dataDir, env: { ...process.env, RATICODE_EDGE_PATH: EDGE_PATH }, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new ChromiumContents({ engine, kind, nativeImage: fakeImage }),
  },
  firefox: {
    path: FIREFOX_PATH,
    engine: (dataDir) => createFirefoxEngine({ dataDir, idleShutdownMs: 1000 }),
    contents: (engine, kind) => new FirefoxContents({ engine, kind, nativeImage: fakeImage }),
  },
};

// Two seconds of silence as a WAV file, looped, with buttons to play and pause it.
const PAGE = `<!doctype html><title>Sound</title><body style="margin:0">
<button id=play style="position:absolute;left:10px;top:10px;width:160px;height:60px">Play</button>
<button id=pause style="position:absolute;left:10px;top:100px;width:160px;height:60px">Pause</button>
<script>
  const rate = 8000, samples = rate * 2, buffer = new ArrayBuffer(44 + samples * 2), view = new DataView(buffer);
  const text = (at, value) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
  const audio = new Audio(URL.createObjectURL(new Blob([buffer], { type: "audio/wav" })));
  audio.loop = true;
  document.body.append(audio);
  document.getElementById("play").onclick = () => audio.play();
  document.getElementById("pause").onclick = () => audio.pause();
</script>`;

async function startSite() {
  const server = http.createServer((_request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end(PAGE);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

async function waitFor(check, message, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

const click = (contents, x, y) => contents.handleInput([{ t: "down", x, y, button: 0 }, { t: "up", x, y, button: 0 }]);

test("a playing element with its volume up counts, and a tab's own mute doesn't hide it", () => {
  assert.match(soundExpression(false), /!media\.paused/);
  assert.match(soundExpression(false), /!media\.muted \|\| false/);
  assert.match(soundExpression(true), /!media\.muted \|\| true/);
});

for (const [name, spec] of Object.entries(ENGINES)) {
  const skip = { skip: spec.path ? false : `${name} is not installed` };

  test(`${/^[aeiou]/.test(name) ? "an" : "a"} ${name} tab says when it plays sound, also in the background and when muted`, skip, async () => {
    const { server, base } = await startSite();
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${name}-sound-test-`));
    const engine = spec.engine(dataDir);
    const contents = spec.contents(engine, "user");
    const changes = [];
    contents.on("audio-state-changed", (event) => changes.push(event.audible));
    try {
      await contents.loadURL(`${base}/`);
      contents.setView({ width: 800, height: 600, scale: 1 }, true);
      assert.equal(contents.isCurrentlyAudible(), false);

      // The user's click starts the sound; the tab says so within a few seconds.
      click(contents, 60, 40);
      await waitFor(() => contents.isCurrentlyAudible(), "the playing page was never reported as playing");
      assert.deepEqual(changes, [true]);

      // Muting the tab mutes the page, and the tab still counts as playing (muted).
      contents.setAudioMuted(true);
      await waitFor(async () => (await contents.evaluateIn("test", "document.querySelector('audio').muted")) === true, "the tab's mute didn't reach the page");
      await new Promise((resolve) => setTimeout(resolve, 3000));
      assert.equal(contents.isCurrentlyAudible(), true, "a muted tab that is playing still counts");
      contents.setAudioMuted(false);

      // Pausing ends it.
      click(contents, 60, 130);
      await waitFor(() => !contents.isCurrentlyAudible(), "the paused page was still reported as playing");

      // A tab in the background, not on screen, still says it plays: this keeps it from being unloaded.
      contents.setView({ width: 800, height: 600, scale: 1 }, false);
      await contents.evaluateIn("test", "document.querySelector('audio').play().then(() => true)", { userGesture: true, userActivation: true });
      await waitFor(() => contents.isCurrentlyAudible(), "a hidden tab playing sound was not reported as playing");
      assert.deepEqual(changes, [true, false, true]);
    } finally {
      contents.close();
      await engine.stopAndWait?.();
      server.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
}
