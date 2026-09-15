const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "run-platform-smoke.cjs"), "utf8");

for (const platform of ["linux", "win32", "darwin"]) {
  test(`platform smoke launches Electron fixtures on ${platform}`, () => {
    const calls = [];
    const release = path.resolve(__dirname, "../../release");
    const prefix = { linux: "linux", win32: "win", darwin: "mac" }[platform];
    const unpacked = path.join(release, `${prefix}-unpacked`);
    const env = { ELECTRON_RUN_AS_NODE: "1", CI: "true" };
    vm.runInNewContext(source, {
      __dirname,
      console,
      process: { platform, env, exit: (code) => assert.fail(`Unexpected exit ${code}`) },
      require: (name) => {
        if (name === "electron") return "/fixture/electron";
        if (name === "node:path") return path;
        if (name === "node:child_process") return {
          spawnSync: (...args) => { calls.push(args); return { status: 0 }; },
        };
        if (name === "node:fs") return {
          existsSync: () => true,
          readdirSync: (directory) => [{
            name: directory === release ? `${prefix}-unpacked` : "app.asar",
            isDirectory: () => directory === release,
            isFile: () => directory === unpacked,
          }],
        };
        assert.fail(`Unexpected require ${name}`);
      },
    });
    assert.equal(calls.length, 4);
    const fixtures = [];
    for (const [command, args, options] of calls) {
      assert.equal(command, platform === "linux" ? "xvfb-run" : "/fixture/electron");
      const launchArgs = Array.from(args);
      const fixture = launchArgs.pop();
      fixtures.push(path.basename(fixture));
      assert.equal(path.dirname(fixture), __dirname);
      assert.deepEqual(launchArgs, platform === "linux" ? ["-a", "/fixture/electron", "--no-sandbox"] : []);
      assert.equal(options.env.ELECTRON_RUN_AS_NODE, undefined);
      assert.equal(options.env.CI, "true");
      assert.equal(options.env.RATICODE_SMOKE_ASAR, path.join(unpacked, "app.asar"));
    }
    assert.deepEqual(fixtures, ["native-runtime.browser.cjs", "studio-policy.browser.cjs", "legacy-project-migration.browser.cjs", "conversation-storage.browser.cjs"]);
    assert.equal(env.ELECTRON_RUN_AS_NODE, "1");
  });
}
