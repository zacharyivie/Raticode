// Signing in from a normal Chrome window. Google refuses to sign in while
// Chrome is under remote control, so Raticode opens its Chrome profile in a
// window without the DevTools pipe. The real-Chrome test draws that window on
// a private, hidden X display and is skipped without Chrome or Xvfb.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawn: spawnProcess, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { CHROME, createChromiumEngine, findBrowser } = require("../chromium-engine.cjs");
const { ChromiumContents } = require("../chromium-contents.cjs");

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
const XVFB = process.platform === "linux" && spawnSync("which", ["Xvfb"]).status === 0;
const realWindow = { skip: CHROME_PATH && XVFB ? false : "Needs Chrome and Xvfb" };

function temp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function fakeProcess() {
  const child = new EventEmitter();
  child.pid = 4242;
  child.exitCode = null;
  child.kill = () => { child.exitCode = 0; child.emit("exit", 0); };
  return child;
}

test("opening the Chrome sign-in window pauses Raticode's Chrome until the window closes", async (t) => {
  const launches = [];
  const dataDir = temp("chrome-signin-unit-");
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const engine = createChromiumEngine({
    dataDir,
    find: () => "/opt/google/chrome/chrome",
    platform: "linux",
    env: { DISPLAY: ":0", PATH: "/usr/bin" },
    spawn: (command, args, options) => {
      const child = fakeProcess();
      launches.push({ command, args, options, child });
      return child;
    },
  });
  await assert.rejects(engine.openSignInWindow("javascript:alert(1)"), /web address/);
  await assert.rejects(engine.openSignInWindow("file:///etc/passwd"), /web address/);
  await assert.rejects(engine.openSignInWindow("chrome://flags"), /web address/);
  await assert.rejects(engine.openSignInWindow("chrome-extension://abc/page.html"), /web address/);
  assert.deepEqual(await engine.openSignInWindow("https://accounts.google.com/"), { opened: true });
  const [launch] = launches;
  assert.equal(launch.args[0], `--user-data-dir=${path.join(dataDir, "chrome-profile")}`);
  assert.ok(launch.args.includes("--password-store=basic"));
  assert.equal(launch.args.at(-1), "https://accounts.google.com/");
  assert.equal(launch.args.includes("--headless"), false);
  assert.equal(launch.args.some((arg) => /remote-debugging|enable-automation/.test(arg)), false);
  assert.equal(launch.options.env.DISPLAY, ":0");
  assert.equal(engine.signingIn, true);
  assert.deepEqual(await engine.openSignInWindow("https://example.com/"), { alreadyOpen: true });
  await assert.rejects(engine.ensure(), /open in a window for signing in/);
  const closed = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
  launch.child.kill();
  await closed;
  assert.equal(engine.signingIn, false);
  assert.deepEqual(await engine.openSignInWindow("chrome://settings/syncSetup"), { opened: true });
  assert.equal(launches.at(-1).args.at(-1), "chrome://settings/syncSetup");
});

test("a sign-in in the Chrome window carries over to Raticode's Chrome tabs, not Rem's", realWindow, async () => {
  const display = `:${90 + Math.floor(Math.random() * 20)}`;
  const xvfb = spawnProcess("Xvfb", [display, "-screen", "0", "1280x900x24", "-nolisten", "tcp"], { stdio: "ignore" });
  const reports = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/report") {
      reports.push(url.searchParams.get("webdriver"));
      response.end("ok");
      return;
    }
    response.setHeader("Content-Type", "text/html");
    if (url.pathname === "/signin") {
      // Stands in for a sign-in page: it sets a session cookie and reports
      // whether the page sees an automated browser.
      response.setHeader("Set-Cookie", "session=signed-in; Path=/; Max-Age=3600");
      response.end("<title>sign in</title><script>fetch('/report?webdriver=' + navigator.webdriver)</script>");
      return;
    }
    response.end("<title>home</title>");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const windows = [];
  const dataDir = temp("chrome-signin-real-");
  // A private display and no session bus: the window can't reach the desktop.
  const env = { ...process.env, DISPLAY: display, RATICODE_CHROME_PATH: CHROME_PATH, XDG_SESSION_TYPE: "x11" };
  delete env.WAYLAND_DISPLAY;
  delete env.DBUS_SESSION_BUS_ADDRESS;
  const socket = `/tmp/.X11-unix/X${display.slice(1)}`;
  const started = Date.now() + 5000;
  while (!fs.existsSync(socket) && Date.now() < started) await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(fs.existsSync(socket), `Xvfb did not start on ${display}; it needs free space in /tmp for its lock file`);
  const engine = createChromiumEngine({
    dataDir,
    env,
    spawn: (command, args, options) => {
      const child = spawnProcess(command, args, options);
      if (!args.includes("--headless")) windows.push({ args, child });
      return child;
    },
  });
  try {
    await new Promise((resolve) => setTimeout(resolve, 800));
    // Raticode's Chrome has been running and is controlled.
    const before = new ChromiumContents({ engine, kind: "user" });
    await before.loadURL(`${base}/`);
    assert.equal(await before.evaluateIn("test", "navigator.webdriver"), true);
    before.close();

    await engine.openSignInWindow(`${base}/signin`);
    const deadline = Date.now() + 30000;
    while (!reports.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 200));
    assert.deepEqual(reports, ["false"], "the window's page should see a browser that is not automated");
    assert.equal(windows.length, 1);
    assert.equal(windows[0].args.some((arg) => /remote-debugging|enable-automation/.test(arg)), false);
    assert.equal(fs.existsSync(path.join(engine.profileDir(), "DevToolsActivePort")), false, "no control port was opened");
    await assert.rejects(engine.ensure(), /open in a window for signing in/);

    // The user closes the window. Chrome writes cookies to the profile in
    // batches every 30 seconds and when it quits normally; the test ends the
    // window with a signal, so it first waits for that batch.
    const closed = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
    await new Promise((resolve) => setTimeout(resolve, 33000));
    windows[0].child.kill("SIGTERM");
    await closed;

    const after = new ChromiumContents({ engine, kind: "user" });
    await after.loadURL(`${base}/`);
    assert.equal(await after.evaluateIn("test", "document.cookie"), "session=signed-in");
    after.close();
    const rem = new ChromiumContents({ engine, kind: "rem" });
    await rem.loadURL(`${base}/`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "", "Rem's tabs stay signed out");
    rem.close();
  } finally {
    for (const item of windows) { try { item.child.kill("SIGKILL"); } catch { /* Already closed. */ } }
    await engine.stopAndWait();
    server.close();
    xvfb.kill();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
