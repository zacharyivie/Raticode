// Rem driving Raticode (gecko) for real: a windowed Firefox started the way
// gecko-window.cjs starts Gecko with "Let Rem use Raticode (gecko)" on, Rem's
// connection (gecko-control.cjs), Firefox tabs (firefox-contents.cjs) and every
// browser tool (agent-browser.cjs). Firefox runs on a private Xvfb display with
// a throwaway home folder and no session bus, so nothing reaches the desktop.
// Skipped when Firefox or Xvfb is missing. The page's sound is far below hearing.
require("./test-tmp.cjs");
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { findFirefox } = require("../firefox-engine.cjs");
const { prefLine } = require("../firefox-profile.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");
const { PROFILE_PREFS, REM_CONTROL_PREFS, WINDOW_CLASS } = require("../gecko-window.cjs");
const { createGeckoControl, listeningAddress } = require("../gecko-control.cjs");
const { createAgentBrowserController } = require("../agent-browser.cjs");
const { findExecutable } = require("./background-display.cjs");
const { pageText } = require("./hidden-text-pages.cjs");

const FIREFOX = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const XVFB = process.platform === "linux" ? findExecutable("Xvfb") : "";
const AGENT = "agent-gecko-0123456789";

function tone() {
  const rate = 8000;
  const samples = rate * 20;
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + samples * 2, 4); buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34); buffer.write("data", 36); buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i += 1) buffer.writeInt16LE(Math.round(8 * Math.sin(i / 3)), 44 + i * 2);
  return buffer;
}

const PAGE = `<!doctype html><title>Gecko player</title><body style="margin:0;height:4000px">
<input id=q aria-label=Search style="position:absolute;left:10px;top:10px;width:200px">
<button id=go style="position:absolute;left:10px;top:50px" onclick="document.title='clicked:'+document.getElementById('q').value+':'+event.isTrusted">Go</button>
<audio id=song src="/tone.wav" loop></audio>
<p style="position:absolute;top:3500px">Bottom of the page</p>
<script>
  navigator.mediaSession.metadata = new MediaMetadata({ title: "PSGFY", artist: "Griz", album: "Test album" });
  document.cookie = "seen=" + (document.cookie.includes("user=1") ? "user" : "nobody");
</script></body>`;

async function startSite() {
  const wav = tone();
  const server = http.createServer((request, response) => {
    if (request.url === "/tone.wav") { response.setHeader("Content-Type", "audio/wav"); response.end(wav); return; }
    if (request.url === "/sign-in") { response.setHeader("Set-Cookie", "user=1; Path=/"); response.end("<title>Signed in</title>"); return; }
    response.setHeader("Content-Type", "text/html");
    response.end(PAGE);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

// A private X display; -displayfd picks a free number.
async function startDisplay() {
  const xvfb = spawn(XVFB, ["-displayfd", "1", "-screen", "0", "1280x800x24", "-nolisten", "tcp"], { stdio: ["ignore", "pipe", "ignore"] });
  const display = await new Promise((resolve, reject) => {
    let text = "";
    const timer = setTimeout(() => reject(new Error("Xvfb did not start.")), 15000);
    xvfb.stdout.on("data", (chunk) => {
      text += chunk;
      const match = /^(\d+)\s*$/m.exec(text);
      if (match) { clearTimeout(timer); resolve(`:${match[1]}`); }
    });
    xvfb.once("exit", () => reject(new Error("Xvfb exited.")));
  });
  return { xvfb, display };
}

// Gecko's launch with Rem's control on: same class, profile prefs and port.
async function startGecko(root, display, control) {
  const profile = path.join(root, "profile");
  fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(profile, "user.js"), Object.entries({ ...PROFILE_PREFS, ...REM_CONTROL_PREFS }).map(([name, value]) => prefLine(name, value)).join("\n"));
  const home = path.join(root, "home");
  fs.mkdirSync(home, { recursive: true });
  const env = { ...process.env, HOME: home, DISPLAY: display, GDK_BACKEND: "x11", MOZ_ENABLE_WAYLAND: "0", MOZ_CRASHREPORTER_DISABLE: "1", DBUS_SESSION_BUS_ADDRESS: "unix:path=/nonexistent/raticode-test-bus" };
  delete env.WAYLAND_DISPLAY;
  delete env.MOZ_HEADLESS;
  const firefox = spawn(FIREFOX, ["--class", WINDOW_CLASS, "--new-instance", "--profile", profile, "--remote-debugging-port", "0", "about:blank"], { env, stdio: ["ignore", "ignore", "pipe"] });
  const address = await new Promise((resolve, reject) => {
    let text = "";
    const timer = setTimeout(() => reject(new Error("Firefox did not open its control port.")), 60000);
    firefox.stderr.on("data", (chunk) => {
      text = (text + chunk).slice(-8000);
      const found = listeningAddress(text);
      if (found) { clearTimeout(timer); resolve(found); }
    });
    firefox.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Firefox exited (${code}).`)); });
  });
  await control.attach(address, { kill: () => firefox.kill("SIGKILL") });
  return firefox;
}

const fakeImage = { createFromBuffer: (buffer) => ({ isEmpty: () => !buffer.length, getSize: () => ({ width: 1280, height: 800 }), resize() { return this; }, toPNG: () => buffer }) };

test("Rem drives Raticode (gecko): background tabs in its own container, every tool, and playback", { skip: !FIREFOX ? "Firefox is not installed" : !XVFB ? "Xvfb is not installed" : false, timeout: 150000 }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-gecko-rem-"));
  const { server, base } = await startSite();
  const { xvfb, display } = await startDisplay();
  const control = createGeckoControl({ stagingDir: path.join(root, "downloads") });
  control.setHost({ enabled: () => true, available: () => ({ available: true, executable: FIREFOX }), start: async () => { throw new Error("already started"); } });
  let firefox = null;
  t.after(async () => {
    control.detach({ expected: true });
    if (firefox) {
      const exited = new Promise((resolve) => firefox.once("exit", resolve));
      firefox.kill("SIGTERM");
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 8000))]);
      firefox.kill("SIGKILL");
    }
    xvfb.kill("SIGTERM");
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  });
  firefox = await startGecko(root, display, control);

  // The user's own tab, signed in to the site. Rem never sees it.
  const { contexts } = await control.send("browsingContext.getTree", { maxDepth: 0 });
  const userTab = contexts[0].context;
  await control.send("browsingContext.navigate", { context: userTab, url: `${base}/sign-in`, wait: "complete" });

  const tabs = [];
  const approvals = [];
  const controller = createAgentBrowserController({
    openTab: ({ agent, label, engine }) => {
      assert.equal(engine, "gecko");
      const contents = new FirefoxContents({ engine: control, kind: "rem", nativeImage: fakeImage });
      contents.guardNavigation = true;
      const session = { id: `tab-${tabs.length}`, engine: "gecko", agent: { id: agent, label, shared: false }, contents, viewSize: null };
      tabs.push(session);
      return session;
    },
    agentTabs: (agent) => tabs.filter((item) => item.agent?.id === agent && !item.contents.isDestroyed()),
    closeTab: (session) => session.contents.close(),
    normalizeUrl: (value) => value,
    policy: () => ({ remConfirm: "always" }),
    confirm: async (_session, details) => { approvals.push(details.label); return true; },
  });
  const act = (request) => controller.handle({ agent: AGENT, ...request });

  const opened = await act({ action: "open", url: `${base}/player`, browser: "gecko" });
  assert.equal(opened.browser, "gecko");
  assert.equal(opened.title, "Gecko player");
  const tab = tabs[0];
  const evaluate = async (expression) => (await control.send("script.evaluate", { expression, target: { context: tab.contents.context }, awaitPromise: true })).result?.value;
  // A background tab in the user's window, in a container that starts signed out.
  assert.equal(await evaluate("document.visibilityState"), "hidden");
  assert.equal(await evaluate("document.cookie.includes('user=1')"), false);
  const tree = await control.send("browsingContext.getTree", { maxDepth: 0 });
  const remEntry = tree.contexts.find((item) => item.context === tab.contents.context);
  assert.ok(remEntry && control.isRemContext(remEntry.userContext));
  assert.equal(remEntry.clientWindow, tree.contexts.find((item) => item.context === userTab).clientWindow);
  // The user's tab is not among Rem's.
  assert.deepEqual((await act({ action: "tabs" })).tabs.map((item) => item.tab), ["tab-0"]);

  const snapshot = (await act({ action: "snapshot" })).snapshot;
  const search = /textbox "Search" \[ref=(e\d+)\]/.exec(snapshot)?.[1];
  const go = /button "Go" \[ref=(e\d+)\]/.exec(snapshot)?.[1];
  assert.ok(search && go, snapshot);
  await act({ action: "type", ref: search, text: "griz" });
  await act({ action: "press", key: "End" });
  // "Go" isn't a consequential label; remConfirm "always" still asks only for consequential clicks.
  const clicked = await act({ action: "click", ref: go });
  assert.equal(clicked.title, "clicked:griz:true");

  const scrolled = await act({ action: "scroll", direction: "down", amount: 1000 });
  assert.equal(scrolled.moved, true);
  assert.equal(scrolled.scroll.y, 1000);

  const before = await act({ action: "media" });
  assert.equal(before.media.length, 1);
  assert.equal(before.media[0].paused, true);
  assert.deepEqual([before.session.title, before.session.artist], ["PSGFY", "Griz"]);
  const played = await act({ action: "media", command: "play" });
  assert.equal(played.outcome, "playing", JSON.stringify(played));
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const playing = await act({ action: "media" });
  assert.equal(playing.media[0].playing, true, JSON.stringify(playing));
  assert.ok(playing.media[0].currentTime > 0.5, JSON.stringify(playing));
  const seeked = await act({ action: "media", command: "seek", time: 10 });
  assert.ok(seeked.media[0].currentTime >= 10, JSON.stringify(seeked));
  const paused = await act({ action: "media", command: "pause" });
  assert.equal(paused.media[0].paused, true);

  const shot = await act({ action: "screenshot" });
  assert.ok(Buffer.from(shot.image, "base64").length > 1000);

  // Page scripts still ask first.
  const evaluated = await act({ action: "evaluate", expression: "document.title" });
  assert.equal(pageText(evaluated.result, base), JSON.stringify("clicked:griz:true"));
  assert.deepEqual(approvals, ["Run a script on this page"]);

  await act({ action: "close" });
  const after = await control.send("browsingContext.getTree", { maxDepth: 0 });
  assert.ok(!after.contexts.some((item) => item.context === remEntry.context));
  assert.ok(after.contexts.some((item) => item.context === userTab));

  // The user shares the tab in front of Gecko's window. Rem drives it with
  // the shared-tab rules: it asks before running scripts, and the user declines.
  const userEvaluate = async (expression) => (await control.send("script.evaluate", { expression, target: { context: userTab }, awaitPromise: true })).result?.value;
  const width = await userEvaluate("innerWidth");
  const found = await control.shareableTab();
  assert.equal(found.context, userTab);
  assert.equal(found.title, "Signed in");
  const sharedContents = new FirefoxContents({ engine: control, kind: "user", nativeImage: fakeImage, adopt: found.context });
  await sharedContents.ready;
  const sharedTabs = [{ id: "shared-0", engine: "gecko", agent: { id: AGENT, label: "Rem", shared: true }, contents: sharedContents, viewSize: null }];
  const declined = [];
  const sharedController = createAgentBrowserController({
    openTab: () => { throw new Error("This test only uses the shared tab."); },
    agentTabs: (agent) => sharedTabs.filter((item) => item.agent?.id === agent && !item.contents.isDestroyed()),
    closeTab: (session) => session.contents.close(),
    normalizeUrl: (value) => value,
    policy: () => ({}),
    confirm: async (_session, details) => { declined.push(details.label); return false; },
  });
  const share = (request) => sharedController.handle({ agent: AGENT, tab: "shared-0", ...request });
  const listed = (await share({ action: "tabs" })).tabs;
  assert.deepEqual(listed.map((item) => [item.tab, item.browser, item.sharedByUser]), [["shared-0", "gecko", true]]);
  const navigated = await share({ action: "navigate", url: `${base}/player` });
  assert.equal(navigated.title, "Gecko player");
  // It's the user's tab: in front, signed in, and still the size of Gecko's window.
  assert.equal(await userEvaluate("document.visibilityState"), "visible");
  assert.equal(await userEvaluate("document.cookie.includes('user=1')"), true);
  assert.equal(await userEvaluate("innerWidth"), width);
  const sharedSnapshot = (await share({ action: "snapshot" })).snapshot;
  const sharedSearch = /textbox "Search" \[ref=(e\d+)\]/.exec(sharedSnapshot)?.[1];
  await share({ action: "type", ref: sharedSearch, text: "psgfy" });
  assert.equal(await userEvaluate("document.getElementById('q').value"), "psgfy");
  assert.equal((await share({ action: "media" })).media.length, 1);
  assert.ok(Buffer.from((await share({ action: "screenshot" })).image, "base64").length > 1000);
  await assert.rejects(share({ action: "evaluate", expression: "document.cookie" }), /declined|didn't approve|did not approve|not approved/i);
  assert.deepEqual(declined, ["Run a script on this page"]);
  // Ending the sharing lets go of the tab; it stays open, and Rem's events stop.
  await share({ action: "close" });
  assert.equal(sharedContents.isDestroyed(), true);
  const finalTree = await control.send("browsingContext.getTree", { maxDepth: 0 });
  assert.ok(finalTree.contexts.some((item) => item.context === userTab));
  assert.equal(await userEvaluate("document.title"), "Gecko player");
});
