const assert = require("node:assert/strict");
const { test } = require("node:test");
const { backgroundLaunch, needsBackgroundDisplay } = require("./background-display.cjs");

test("Linux Electron tests open on a private X display, never the Wayland desktop", () => {
  const env = { DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-1", XDG_SESSION_TYPE: "wayland", KEEP: "yes" };
  const launch = backgroundLaunch("/fixture/electron", ["--no-sandbox", "test.browser.cjs"], env, "linux");
  assert.equal(launch.command, "xvfb-run");
  assert.deepEqual(launch.args, ["-a", "-s", "-screen 0 1920x1080x24", "/fixture/electron", "--ozone-platform=x11", "--no-sandbox", "test.browser.cjs"]);
  assert.equal(launch.env.WAYLAND_DISPLAY, undefined);
  assert.equal(launch.env.XDG_SESSION_TYPE, "x11");
  assert.equal(launch.env.KEEP, "yes");
  assert.equal(launch.env.RATICODE_TEST_BACKGROUND_DISPLAY, "1");
  assert.equal(env.WAYLAND_DISPLAY, "wayland-1");
  assert.equal(needsBackgroundDisplay(launch.env, "linux"), false);
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
