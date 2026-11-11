// Signing in from a normal Firefox window. Google refuses to sign in while
// Firefox is under remote control, so Raticode opens its Firefox profile in
// a window without a control port. The real-Firefox test draws that window on
// a private, hidden X display and is skipped without Firefox or Xvfb.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawn: spawnProcess, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { createFirefoxEngine, findFirefox, signInEnvironment } = require("../firefox-engine.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");

const FIREFOX = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const XVFB = process.platform === "linux" && spawnSync("which", ["Xvfb"]).status === 0;
const realWindow = { skip: FIREFOX && XVFB ? false : "Needs Firefox and Xvfb" };

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

test("the sign-in window runs Firefox on the user's display without remote control", () => {
  const env = signInEnvironment({ DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-0", MOZ_HEADLESS: "1", MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: "1", MOZ_MARIONETTE: "1", HOME: "/home/a" });
  assert.equal(env.DISPLAY, ":0");
  assert.equal(env.WAYLAND_DISPLAY, "wayland-0");
  assert.equal(env.MOZ_HEADLESS, undefined);
  assert.equal(env.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS, undefined);
  assert.equal(env.MOZ_MARIONETTE, undefined);
  assert.equal(env.MOZ_NO_REMOTE, "1");
});

test("opening the sign-in window pauses Raticode's Firefox until the window closes", async (t) => {
  const launches = [];
  const dataDir = temp("firefox-signin-unit-");
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const engine = createFirefoxEngine({
    dataDir,
    find: () => "/usr/bin/firefox",
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
  await assert.rejects(engine.openSignInWindow("about:config"), /web address/);
  assert.deepEqual(await engine.openSignInWindow("https://accounts.google.com/"), { opened: true });
  const [launch] = launches;
  assert.deepEqual(launch.args.slice(0, 2), ["--no-remote", "--profile"]);
  assert.equal(launch.args.at(-1), "https://accounts.google.com/");
  assert.equal(launch.args.includes("--headless"), false);
  assert.equal(launch.args.some((arg) => /remote-debugging|marionette/.test(arg)), false);
  assert.equal(launch.options.env.DISPLAY, ":0");
  assert.equal(engine.signingIn, true);
  assert.deepEqual(await engine.openSignInWindow("https://example.com/"), { alreadyOpen: true });
  await assert.rejects(engine.ensure(), /open in a window for signing in/);
  const closed = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
  launch.child.kill();
  await closed;
  assert.equal(engine.signingIn, false);
  assert.deepEqual(await engine.openSignInWindow("about:preferences#sync"), { opened: true });
  assert.equal(launches.at(-1).args.at(-1), "about:preferences#sync");
});

test("a sign-in in the Firefox window carries over to Raticode's Firefox tabs", realWindow, async () => {
  const display = `:${70 + Math.floor(Math.random() * 20)}`;
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
  const dataDir = temp("firefox-signin-real-");
  const env = { ...process.env, DISPLAY: display };
  delete env.WAYLAND_DISPLAY;
  const engine = createFirefoxEngine({
    dataDir,
    env,
    spawn: (command, args, options) => {
      const child = spawnProcess(command, args, options);
      if (!args.includes("--headless") && command !== "/bin/sh" && command !== process.execPath) windows.push({ args, child });
      return child;
    },
  });
  try {
    await new Promise((resolve) => setTimeout(resolve, 800));
    // Raticode's Firefox has been running and is controlled.
    const before = new FirefoxContents({ engine, kind: "user" });
    await before.loadURL(`${base}/`);
    assert.equal(await before.evaluateIn("test", "navigator.webdriver"), true);
    before.close();

    await engine.openSignInWindow(`${base}/signin`);
    const deadline = Date.now() + 30000;
    while (!reports.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 200));
    assert.deepEqual(reports, ["false"], "the window's page should see a browser that is not automated");
    assert.equal(windows.length, 1);
    assert.equal(windows[0].args.some((arg) => /remote-debugging|marionette/.test(arg)), false);
    assert.equal(fs.existsSync(path.join(engine.profileDir(), "WebDriverBiDiServer.json")), false, "no control port was opened");
    await assert.rejects(engine.ensure(), /open in a window for signing in/);

    // The user closes the window. Closing it from the desktop lets Firefox
    // finish saving; the test ends it with a signal, so it first waits for
    // Firefox's batched write of the cookie to reach the profile.
    const journal = path.join(engine.profileDir(), "cookies.sqlite-wal");
    const saved = Date.now() + 15000;
    while (Date.now() < saved && !(fs.existsSync(journal) && fs.statSync(journal).size > 0)) await new Promise((resolve) => setTimeout(resolve, 200));
    const closed = new Promise((resolve) => engine.events.once("sign-in-window-closed", resolve));
    windows[0].child.kill("SIGTERM");
    await closed;

    const after = new FirefoxContents({ engine, kind: "user" });
    await after.loadURL(`${base}/`);
    assert.equal(await after.evaluateIn("test", "document.cookie"), "session=signed-in");
    after.close();
    const rem = new FirefoxContents({ engine, kind: "rem" });
    await rem.loadURL(`${base}/`);
    assert.equal(await rem.evaluateIn("test", "document.cookie"), "", "Rem's container stays signed out");
    rem.close();
  } finally {
    for (const item of windows) { try { item.child.kill("SIGKILL"); } catch { /* Already closed. */ } }
    await engine.stopAndWait();
    server.close();
    xvfb.kill();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
