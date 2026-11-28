const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { test } = require("node:test");
const { MARKER, dropInheritedSettings, inheritedSetting } = require("./test-env.cjs");

// What Raticode passes to an agent's commands: its push block and commit hook as git
// config, and its own settings.
const PUSH_BLOCK = {
  GIT_CONFIG_COUNT: "2",
  GIT_CONFIG_KEY_0: "url.raticode-push-blocked-ask-the-user://.pushInsteadOf",
  GIT_CONFIG_VALUE_0: "",
  GIT_CONFIG_KEY_1: "hook.raticode-strip-coauthor.event",
  GIT_CONFIG_VALUE_1: "commit-msg",
  GIT_CONFIG_PARAMETERS: "'core.hooksPath'='/dev/null'",
};

test("drops inherited git config and app settings and keeps the test switches", () => {
  const env = dropInheritedSettings({
    ...PUSH_BLOCK,
    GIT_CONFIG: "/home/u/.gitconfig-raticode",
    GOFER_DATA_DIR: "/home/u/.local/state/raticode/data",
    gofer_log_level: "debug",
    RATICODE_COMMAND_SLOTS: "/run/slots",
    RATICODE_AGENT_SESSION: "abc",
    RATICODE_TEST_JOBS: "4",
    RATICODE_TEST_TMP_BASE: "/var/tmp",
    RATICODE_SKIP_CHROME_TESTS: "1",
    RATICODE_FIREFOX_NETWORK_TESTS: "1",
    RATICODE_EDGE_PATH: "/opt/msedge",
    GIT_AUTHOR_NAME: "Test",
    PATH: "/usr/bin",
  });
  assert.deepEqual(env, {
    RATICODE_TEST_JOBS: "4",
    RATICODE_TEST_TMP_BASE: "/var/tmp",
    RATICODE_SKIP_CHROME_TESTS: "1",
    RATICODE_FIREFOX_NETWORK_TESTS: "1",
    RATICODE_EDGE_PATH: "/opt/msedge",
    GIT_AUTHOR_NAME: "Test",
    PATH: "/usr/bin",
    [MARKER]: "1",
  });
});

test("only the exact prefixes and switches count", () => {
  for (const name of ["GIT_CONFIG_NOSYSTEM", "Gofer_Data_Dir", "RATICODE_TESTING", "RATICODE_SKIP_TESTS", "RATICODE_SAFARI"]) {
    assert.equal(inheritedSetting(name), true, name);
  }
  for (const name of ["GIT_CONFIGURED", "GOFERS", "RATICODE", "RATICODE_TEST_VISIBLE", "RATICODE_FIREFOX_PATH"]) {
    assert.equal(inheritedSetting(name), false, name);
  }
});

test("a test's own child keeps the settings the test gave it", () => {
  const env = { [MARKER]: "1", GOFER_DATA_DIR: "/tmp/test-data", GIT_CONFIG_COUNT: "0" };
  assert.equal(dropInheritedSettings(env), env);
  assert.deepEqual(env, { [MARKER]: "1", GOFER_DATA_DIR: "/tmp/test-data", GIT_CONFIG_COUNT: "0" });
});

test("a test file run on its own sees no push block once it loads the shared setup", () => {
  const env = { ...process.env, ...PUSH_BLOCK, GOFER_DATA_DIR: "/nonexistent/raticode-data", RATICODE_TEST_JOBS: "3" };
  delete env[MARKER];
  const script = [
    `require(${JSON.stringify(path.join(__dirname, "test-tmp.cjs"))});`,
    'const { execFileSync } = require("node:child_process");',
    'let blocked = "";',
    'try { blocked = execFileSync("git", ["config", "--get-regexp", "pushinsteadof"], { encoding: "utf8" }); } catch {}',
    "const seen = Object.keys(process.env).filter((name) => /^(GIT_CONFIG|GOFER_)/.test(name));",
    "process.stdout.write(JSON.stringify({ blocked: blocked.includes('raticode-push-blocked'), seen, jobs: process.env.RATICODE_TEST_JOBS }));",
  ].join("\n");
  const result = spawnSync(process.execPath, ["-e", script], { env, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { blocked: false, seen: [], jobs: "3" });
});
