const test = require("node:test");
const assert = require("node:assert/strict");
const { restoreShellPath } = require("../shell-path.cjs");

test("Dock and terminal launches recover the same provider PATH without importing other variables", async () => {
  for (const inherited of ["/usr/bin:/bin", "/Users/test/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"]) {
    const env = { PATH: inherited, SHELL: "/bin/zsh", APP_SETTING: "preserve" };
    await restoreShellPath({ platform: "darwin", env, home: "/Users/test", run: (shell, args, options, done) => {
      assert.equal(shell, "/bin/zsh");
      assert.equal(args[0], "-ilc");
      assert.equal(options.cwd, "/Users/test");
      assert.equal(options.timeout, 5000);
      done(null, "Welcome\n\0RATICODE_PATH\0/Users/test/.local/bin:/opt/homebrew/bin:/Users/test/.nvm/versions/node/v22/bin:/usr/bin:/bin\0\nbye");
    } });
    assert.equal(env.PATH.split(":")[0], "/Users/test/.local/bin");
    assert.ok(env.PATH.includes("/Users/test/.nvm/versions/node/v22/bin"));
    assert.equal(env.PATH.split(":").filter(entry => entry === "/usr/bin").length, 1);
    assert.equal(env.APP_SETTING, "preserve");
    assert.equal(Object.keys(env).length, 3);
  }
});

test("failed or noisy shell startup retains inherited PATH and adds macOS install locations", async () => {
  for (const failure of [new Error("timeout"), null]) {
    const env = { PATH: "/custom/bin:/usr/bin:.", SHELL: "/bin/zsh" };
    await restoreShellPath({ platform: "darwin", env, home: "/Users/test", run: (_shell, _args, _options, done) => done(failure, "unframed output") });
    assert.equal(env.PATH.split(":")[0], "/custom/bin");
    assert.ok(env.PATH.includes("/opt/homebrew/bin"));
    assert.ok(env.PATH.includes("/Users/test/.local/bin"));
    assert.ok(!env.PATH.split(":").includes("."));
  }
});

test("other platforms do not launch a login shell or change PATH", async () => {
  const env = { PATH: "original" };
  await restoreShellPath({ platform: "win32", env, run: () => assert.fail("unexpected shell") });
  assert.equal(env.PATH, "original");
});
