const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the main process handlers without launching Electron or contacting GitHub.
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
function context(platform, signing, packaged = true, smoke = false, packageType = null) {
  const calls = [];
  const state = { available: true, info: { installerUrl: "https://github.com/zacharyivie/Raticode/releases/download/v0.3.5/Raticode-0.3.5-arm64.dmg" } };
  const sandbox = {
    app: { isPackaged: packaged, getVersion: () => "0.2.6" },
    process: { platform, arch: "arm64", resourcesPath: "/opt/raticode/resources" },
    path,
    fs: {
      readFileSync: (file) => {
        calls.push(`read ${file}`);
        if (packageType === null) throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
        return packageType;
      },
    },
    raticodeReleaseSigning: signing,
    RELEASES_URL: "https://github.com/zacharyivie/Raticode/releases",
    isSmokeTest: smoke,
    updateState: state,
    setUpdateState: (patch) => Object.assign(state, patch),
    checkLatestReleaseFallback: async () => { calls.push("manual-check"); return { checking: false }; },
    shell: { openExternal: async (url) => calls.push(url.endsWith(".dmg") ? "mac-installer" : "release-page") },
    prepareLifecycle: async () => true,
    stopBackend: () => calls.push("stop-backend"),
    autoUpdater: {
      checkForUpdates: async () => calls.push("auto-check"),
      downloadUpdate: async () => calls.push("auto-download"),
      quitAndInstall: () => calls.push("auto-install"),
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    source.slice(source.indexOf("let installedPackageManager;"), source.indexOf("function setupAutoUpdater()"))
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
    await sandbox.installDownloadedUpdate();
    assert.deepEqual(calls.filter((call) => !call.startsWith("read ")), supported
      ? ["auto-check", "auto-download", "stop-backend", "auto-install"]
      : ["manual-check", "mac-installer"]);
  });
}

test("a pacman-installed copy never updates itself and points to the package manager", async () => {
  const { sandbox, calls } = context("linux", "unsigned", true, false, "pacman\n");
  const state = sandbox.getUpdateState();
  assert.equal(state.supported, false);
  assert.equal(state.managedBy, "pacman");
  await sandbox.checkForUpdates();
  await sandbox.downloadAndInstallUpdate();
  await sandbox.installDownloadedUpdate();
  assert.deepEqual(calls, ["read /opt/raticode/resources/package-type", "manual-check", "release-page"]);
});

for (const packageType of ["deb\n", "rpm", null]) {
  test(`linux package type ${JSON.stringify(packageType)} keeps electron-updater`, async () => {
    const { sandbox, calls } = context("linux", "unsigned", true, false, packageType);
    assert.equal(sandbox.getUpdateState().supported, true);
    assert.equal(sandbox.getUpdateState().managedBy, "");
    await sandbox.checkForUpdates();
    assert.deepEqual(calls.filter((call) => !call.startsWith("read ")), ["auto-check"]);
  });
}

test("the package marker is only read for packaged Linux builds", () => {
  for (const [platform, packaged] of [["darwin", true], ["win32", true], ["linux", false]]) {
    const { sandbox, calls } = context(platform, "signed", packaged, false, "pacman");
    assert.equal(sandbox.getUpdateState().managedBy, "");
    assert.deepEqual(calls, []);
  }
});

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

const MAC_METADATA = `version: 0.4.0
files:
  - url: Raticode-0.4.0-arm64.zip
    sha512: abc==
    size: 235085848
  - url: Raticode-0.4.0-arm64.dmg
    sha512: def==
    size: 235822411
path: Raticode-0.4.0-arm64.zip
sha512: abc==
releaseDate: '2026-10-28T12:37:15.041Z'
`;

function fallbackContext({ platform = "darwin", arch = "arm64", version = "0.3.9", fetch } = {}) {
  const requests = [];
  const sandbox = {
    app: { getVersion: () => version },
    process: { platform, arch },
    isSmokeTest: false,
    fetch: async (url, options) => { requests.push({ url, options }); return fetch(url); },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    source.match(/^const RELEASES_URL = .*$/m)[0] + "\n"
    + source.slice(source.indexOf("async function checkLatestReleaseFallback()"), source.indexOf("async function openPath(")),
    sandbox,
  );
  return { sandbox, requests };
}

function metadataResponse(body, status = 200, headers = {}) {
  return { ok: status >= 200 && status < 300, status, text: async () => body, headers: new Headers(headers) };
}

for (const arch of ["arm64", "x64"]) {
  test(`manual ${arch} Mac check reads release metadata without the rate-limited API`, async () => {
    const { sandbox, requests } = fallbackContext({ arch, fetch: async () => metadataResponse(MAC_METADATA) });
    const state = await sandbox.checkLatestReleaseFallback();
    assert.deepEqual(requests.map(request => request.url), ["https://github.com/zacharyivie/Raticode/releases/latest/download/latest-mac.yml"]);
    assert.equal(state.available, true);
    assert.equal(state.error, "");
    assert.equal(state.info.version, "0.4.0");
    assert.equal(state.info.releaseDate, "2026-10-28T12:37:15.041Z");
    // Only an installer listed in the metadata for this architecture is offered.
    assert.equal(state.info.installerUrl, arch === "arm64"
      ? "https://github.com/zacharyivie/Raticode/releases/download/v0.4.0/Raticode-0.4.0-arm64.dmg"
      : "");
  });
}

test("manual check reports the current version as up to date and uses the platform metadata file", async () => {
  const mac = await fallbackContext({ version: "0.4.0", fetch: async () => metadataResponse(MAC_METADATA) }).sandbox.checkLatestReleaseFallback();
  assert.equal(mac.available, false);
  for (const [platform, file] of [["linux", "latest-linux.yml"], ["win32", "latest.yml"]]) {
    const { sandbox, requests } = fallbackContext({ platform, fetch: async () => metadataResponse("version: 0.4.0\n") });
    const state = await sandbox.checkLatestReleaseFallback();
    assert.ok(requests[0].url.endsWith(`/releases/latest/download/${file}`));
    assert.equal(state.available, true);
    assert.equal(state.info.installerUrl, "");
  }
});

test("manual check rejects metadata whose version could redirect the installer link", async () => {
  for (const body of ["files: []\n", "version: 0.4.0/../../evil\nfiles:\n  - url: Raticode-0.4.0/../../evil-arm64.dmg\n"]) {
    const { sandbox } = fallbackContext({ fetch: async () => metadataResponse(body) });
    await assert.rejects(sandbox.checkLatestReleaseFallback(), /without a version/);
  }
});

test("manual check treats missing release metadata as no published release", async () => {
  const { sandbox } = fallbackContext({ fetch: async () => metadataResponse("Not Found", 404) });
  const state = await sandbox.checkLatestReleaseFallback();
  assert.equal(state.available, false);
  assert.equal(state.info.noReleases, true);
});

test("manual check explains rate limits, refusals and network failures", async () => {
  const reset = Math.floor(Date.UTC(2026, 9, 28, 15, 5) / 1000);
  const cases = [
    [metadataResponse("", 403, { "x-ratelimit-remaining": "0", "x-ratelimit-reset": String(reset) }), /rate limit for this network was reached\. Try again after \S/],
    [metadataResponse("", 429, { "retry-after": "120" }), /limiting update checks from this network\. Try again in 2 minutes\./],
    [metadataResponse("", 429, { "retry-after": "30" }), /Try again in 1 minute\./],
    [metadataResponse("", 403), /refused the update check \(HTTP 403\)\. A VPN, proxy or firewall/],
    [metadataResponse("", 500), /GitHub returned HTTP 500 while checking for updates\./],
  ];
  for (const [response, message] of cases) {
    const { sandbox } = fallbackContext({ fetch: async () => response });
    await assert.rejects(sandbox.checkLatestReleaseFallback(), message);
  }
  const offline = fallbackContext({ fetch: async () => { throw new TypeError("fetch failed", { cause: new Error("getaddrinfo ENOTFOUND github.com") }); } });
  await assert.rejects(offline.sandbox.checkLatestReleaseFallback(), /Could not reach GitHub to check for updates: getaddrinfo ENOTFOUND github\.com/);
});

test("manual update buttons describe the action accurately", () => {
  const appSource = fs.readFileSync(path.join(__dirname, "../../src/pages/App.jsx"), "utf8");
  const sandbox = {};
  vm.runInNewContext(appSource.slice(appSource.indexOf("function updateButtonLabel("), appSource.indexOf("export function ChatPane(")), sandbox);
  const state = { supported: false, platform: "darwin" };
  assert.equal(sandbox.updateButtonLabel(state), "Download Mac installer");
  assert.match(sandbox.updateButtonTitle(state), /manual installation/);
  assert.equal(sandbox.updateButtonLabel({ downloaded: true, supported: true }), "Restart to update");
  assert.equal(sandbox.updateButtonLabel({ supported: false, platform: "linux" }), "Open update downloads");
  const pacman = { supported: false, platform: "linux", managedBy: "pacman", info: { version: "0.5.3" } };
  assert.equal(sandbox.updateButtonLabel(pacman), "Update 0.5.3 with pacman");
  assert.match(sandbox.updateButtonTitle(pacman), /yay -Syu/);
});
