const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the main process handlers without launching Electron or contacting GitHub.
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
function context(platform, signing, packaged = true, smoke = false) {
  const calls = [];
  const state = { available: true, info: { installerUrl: "https://github.com/zacharyivie/gofer-flow/releases/download/v0.3.5/Raticode-0.3.5-arm64.dmg" } };
  const sandbox = {
    app: { isPackaged: packaged, getVersion: () => "0.2.6" },
    process: { platform, arch: "arm64" },
    raticodeReleaseSigning: signing,
    isSmokeTest: smoke,
    updateState: state,
    setUpdateState: (patch) => Object.assign(state, patch),
    checkLatestReleaseFallback: async () => { calls.push("manual-check"); return { checking: false }; },
    shell: { openExternal: async (url) => calls.push(url.endsWith(".dmg") ? "mac-installer" : "release-page") },
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
      : ["manual-check", "mac-installer"]);
  });
}

test("development and smoke builds keep the manual update fallback", () => {
  assert.equal(context("darwin", "signed", false).sandbox.getUpdateState().supported, false);
  assert.equal(context("win32", "signed", true, true).sandbox.getUpdateState().supported, false);
});


test("missing Mac asset reports an error without opening an unrelated page", async () => {
  const { sandbox, calls } = context("darwin", "unsigned");
  sandbox.updateState.info = {};
  await assert.rejects(sandbox.downloadAndInstallUpdate(), /No compatible Mac installer/);
  assert.deepEqual(calls, []);
});

test("download rejection clears busy and automatic installation state", async () => {
  const { sandbox } = context("win32", "unsigned");
  sandbox.autoUpdater.downloadUpdate = async () => { throw new Error("Offline"); };
  await assert.rejects(sandbox.downloadAndInstallUpdate(), /Offline/);
  assert.equal(sandbox.updateState.downloading, false);
  assert.equal(sandbox.installUpdateAfterDownload, false);
});

for (const arch of ["arm64", "x64"]) {
  test(`Mac download selects ${arch} and rejects foreign assets`, () => {
    const sandbox = { process: { platform: "darwin", arch } };
    vm.runInNewContext(source.slice(source.indexOf("function macInstallerUrl("), source.indexOf("async function openPath(")), sandbox);
    const name = `Raticode-0.3.5-${arch}.dmg`;
    const url = `https://github.com/zacharyivie/gofer-flow/releases/download/v0.3.5/${name}`;
    const release = { tag_name: "v0.3.5", assets: [{ name, browser_download_url: url }] };
    assert.equal(sandbox.macInstallerUrl(release), url);
    release.assets[0].browser_download_url = "https://example.com/installer.dmg";
    assert.equal(sandbox.macInstallerUrl(release), "");
    release.assets = [{ name: "wrong-arch.dmg", browser_download_url: url }];
    assert.equal(sandbox.macInstallerUrl(release), "");
    assert.equal(sandbox.macInstallerUrl({ tag_name: "v0.3.5" }), "");
  });
}

test("manual update buttons describe the action accurately", () => {
  const appSource = fs.readFileSync(path.join(__dirname, "../../src/pages/App.jsx"), "utf8");
  const sandbox = {};
  vm.runInNewContext(appSource.slice(appSource.indexOf("function updateButtonLabel("), appSource.indexOf("export function WorkflowHistoryDialog(")), sandbox);
  const state = { supported: false, platform: "darwin" };
  assert.equal(sandbox.updateButtonLabel(state), "Download Mac installer");
  assert.match(sandbox.updateButtonTitle(state), /manual installation/);
  assert.equal(sandbox.updateButtonLabel({ downloaded: true, supported: true }), "Restart to update");
  assert.equal(sandbox.updateButtonLabel({ supported: false, platform: "linux" }), "Open update downloads");
});
