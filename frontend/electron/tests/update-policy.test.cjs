const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the main process handlers without launching Electron or contacting GitHub.
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
function context(platform, signing, packaged = true, smoke = false) {
  const calls = [];
  const state = { available: true };
  const sandbox = {
    app: { isPackaged: packaged, getVersion: () => "0.2.6" },
    process: { platform, arch: "arm64" },
    raticodeReleaseSigning: signing,
    isSmokeTest: smoke,
    updateState: state,
    setUpdateState: (patch) => Object.assign(state, patch),
    checkLatestReleaseFallback: async () => { calls.push("manual-check"); return { checking: false }; },
    shell: { openExternal: async () => calls.push("release-page") },
    stopBackend: () => calls.push("stop-backend"),
    autoUpdater: {
      checkForUpdates: async () => calls.push("auto-check"),
      downloadUpdate: async () => calls.push("auto-download"),
      quitAndInstall: () => calls.push("auto-install"),
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    source.slice(source.indexOf("function supportsAutoUpdates()"), source.indexOf("function setupAutoUpdater()"))
    + source.slice(source.indexOf("async function checkForUpdates()"), source.indexOf("function setUpdateState(")),
    sandbox,
  );
  return { sandbox, calls };
}

for (const [platform, signing, supported] of [
  ["darwin", undefined, false], ["darwin", "unsigned", false],
  ["darwin", "signed", true], ["win32", "unsigned", true], ["linux", "unsigned", true],
]) {
  test(`${platform} ${signing}: update handlers follow packaged signing policy`, async () => {
    const { sandbox, calls } = context(platform, signing);
    assert.equal(sandbox.getUpdateState().supported, supported);
    await sandbox.checkForUpdates();
    await sandbox.downloadAndInstallUpdate();
    sandbox.installDownloadedUpdate();
    assert.deepEqual(calls, supported
      ? ["auto-check", "auto-download", "stop-backend", "auto-install"]
      : ["manual-check", "release-page"]);
  });
}

test("development and smoke builds keep the manual update fallback", () => {
  assert.equal(context("darwin", "signed", false).sandbox.getUpdateState().supported, false);
  assert.equal(context("win32", "signed", true, true).sandbox.getUpdateState().supported, false);
});
