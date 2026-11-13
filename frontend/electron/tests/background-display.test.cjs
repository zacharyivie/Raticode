const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { BUS_CONFIG, NO_SESSION_BUS, backgroundLaunch, needsBackgroundDisplay } = require("./background-display.cjs");

const desktopEnv = {
  DISPLAY: ":0",
  WAYLAND_DISPLAY: "wayland-1",
  XDG_SESSION_TYPE: "wayland",
  DBUS_SESSION_BUS_ADDRESS: "unix:path=/run/user/1000/bus",
  DBUS_SESSION_BUS_PID: "77",
  KEEP: "yes",
};
const xvfbArgs = ["-a", "-s", "-screen 0 1920x1080x24", "/fixture/electron", "--ozone-platform=x11", "--password-store=basic", "--no-sandbox", "test.browser.cjs"];

test("Linux Electron tests open on a private X display and session bus, never the desktop's", () => {
  const launch = backgroundLaunch("/fixture/electron", ["--no-sandbox", "test.browser.cjs"], desktopEnv, "linux", () => "/usr/bin/dbus-run-session");
  assert.equal(launch.command, "dbus-run-session");
  assert.deepEqual(launch.args, [`--config-file=${BUS_CONFIG}`, "--", "xvfb-run", ...xvfbArgs]);
  assert.equal(launch.env.WAYLAND_DISPLAY, undefined);
  assert.equal(launch.env.XDG_SESSION_TYPE, "x11");
  assert.equal(launch.env.DBUS_SESSION_BUS_ADDRESS, NO_SESSION_BUS);
  assert.equal(launch.env.DBUS_SESSION_BUS_PID, undefined);
  assert.equal(launch.env.NO_AT_BRIDGE, "1");
  assert.equal(launch.env.KEEP, "yes");
  assert.equal(launch.env.RATICODE_TEST_BACKGROUND_DISPLAY, "1");
  assert.equal(desktopEnv.WAYLAND_DISPLAY, "wayland-1");
  assert.equal(needsBackgroundDisplay(launch.env, "linux"), false);
});

test("the private bus can't start desktop services", () => {
  const config = fs.readFileSync(BUS_CONFIG, "utf8");
  assert.match(config, /<type>session<\/type>/);
  assert.doesNotMatch(config, /servicedir|standard_session_servicedirs|<include/);
});

test("without dbus-run-session, Electron gets no session bus rather than the desktop's", () => {
  const launch = backgroundLaunch("/fixture/electron", ["--no-sandbox", "test.browser.cjs"], desktopEnv, "linux", () => "");
  assert.equal(launch.command, "xvfb-run");
  assert.deepEqual(launch.args, xvfbArgs);
  assert.equal(launch.env.DBUS_SESSION_BUS_ADDRESS, NO_SESSION_BUS);
});

test("without xvfb-run the launch fails on xvfb-run itself, so the missing-Xvfb message applies", () => {
  const launch = backgroundLaunch("/fixture/electron", ["test.browser.cjs"], desktopEnv, "linux", name => (name === "dbus-run-session" ? "/usr/bin/dbus-run-session" : ""));
  assert.equal(launch.command, "xvfb-run");
});

test("visible runs, nested launches and other platforms keep the plain Electron launch", () => {
  for (const [env, platform] of [
    [{ RATICODE_TEST_VISIBLE: "1" }, "linux"],
    [{ RATICODE_TEST_BACKGROUND_DISPLAY: "1" }, "linux"],
    [{}, "darwin"],
    [{}, "win32"],
  ]) {
    const args = ["test.browser.cjs"];
    assert.deepEqual(backgroundLaunch("/fixture/electron", args, env, platform), { command: "/fixture/electron", args, env });
  }
});

// Every real-Electron entry script and launcher must go through this helper, so a
// new test can't open on the developer's desktop.
const frontend = path.resolve(__dirname, "../..");
const scanned = ["browser-tests", "electron/tests", "benchmarks", "electron"];
// The developer's own app: visible on purpose, and it refuses a profile in use.
const visibleOnPurpose = new Set(["electron/dev-runner.cjs"]);

function scripts() {
  return scanned.flatMap(dir => fs.readdirSync(path.join(frontend, dir))
    .filter(name => name.endsWith(".cjs"))
    .map(name => `${dir}/${name}`))
    .filter(file => !visibleOnPurpose.has(file));
}

const ENTRY = /const\s*\{[^}]*\bapp\b[^}]*\}\s*=\s*require\(\s*["']electron["']\s*\)/;

test("every Electron test entry script moves itself to the hidden display first", () => {
  const entries = scripts().filter(file => ENTRY.test(fs.readFileSync(path.join(frontend, file), "utf8")));
  assert.ok(entries.length >= 30, `found only ${entries.length} entry scripts`);
  const missing = entries.filter(file => {
    if (file.startsWith("electron/") && !file.startsWith("electron/tests/")) return false;
    const source = fs.readFileSync(path.join(frontend, file), "utf8");
    const helper = source.indexOf("runInBackgroundDisplay()");
    const electron = source.search(/require\(\s*["']electron["']\s*\)/);
    return helper < 0 || helper > electron;
  });
  assert.deepEqual(missing, []);
});

test("every script that spawns Electron launches it through backgroundLaunch", () => {
  // Entry scripts already run inside Electron; what they spawn are backends,
  // which inherit the hidden display and private bus.
  const launchers = scripts().filter(file => {
    const source = fs.readFileSync(path.join(frontend, file), "utf8");
    return !ENTRY.test(source) && /\bspawn(Sync)?\(/.test(source) && /require\(\s*["']electron["']\s*\)/.test(source);
  });
  assert.ok(launchers.length >= 15, `found only ${launchers.length} launchers`);
  const missing = launchers.filter(file => !fs.readFileSync(path.join(frontend, file), "utf8").includes("backgroundLaunch("));
  assert.deepEqual(missing, []);
});

test("every Vite dev server in a test uses its own cache, not the running app's", () => {
  const unitTests = ["src/pages", "src/lib"].flatMap(dir => fs.readdirSync(path.join(frontend, dir))
    .filter(name => /\.test\.m?js$/.test(name)).map(name => `${dir}/${name}`));
  const servers = [...scripts(), ...unitTests].filter(file => /import\(\s*["']vite["']\s*\)|import\s*\{[^}]*\bcreateServer\b[^}]*\}\s*from\s*["']vite["']/.test(fs.readFileSync(path.join(frontend, file), "utf8")));
  assert.ok(servers.length >= 17, `found only ${servers.length} Vite test servers`);
  // A test that only bundles with Vite's build() needs its own cache too.
  const shared = servers.filter(file => !/(?:createServer|build)\(\{[^]*?\bcacheDir\b/.test(fs.readFileSync(path.join(frontend, file), "utf8")));
  assert.deepEqual(shared, []);
});
