const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the main process handlers without launching Electron or contacting GitHub.
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
const MAC_APP = "/Applications/Raticode.app/Contents/MacOS/Raticode";

function context(platform, signing, packaged = true, smoke = false, packageType = null, { execPath = MAC_APP, unwritable = {} } = {}) {
  const calls = [];
  const state = { available: true, info: { installerUrl: "https://github.com/zacharyivie/Raticode/releases/download/v0.3.5/Raticode-0.3.5-arm64.dmg" } };
  const sandbox = {
    app: { isPackaged: packaged, getVersion: () => "0.2.6" },
    process: { platform, arch: "arm64", resourcesPath: "/opt/raticode/resources", execPath },
    path: path.posix,
    fs: {
      constants: { W_OK: 2 },
      accessSync: (target) => {
        if (unwritable[target]) throw Object.assign(new Error(unwritable[target]), { code: unwritable[target] });
      },
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
    logUpdaterError: () => {},
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
    // On a Mac, "downloaded" means Squirrel.Mac accepted the update (tested below).
    sandbox.updateState.downloaded = true;
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
  sandbox.autoUpdater.downloadUpdate = async () => { throw new Error("net::ERR_INTERNET_DISCONNECTED"); };
  await assert.rejects(sandbox.downloadAndInstallUpdate(), /Couldn't reach GitHub to update Raticode\. Check your internet connection/);
  assert.match(sandbox.updateState.error, /Check your internet connection/);
  assert.equal(sandbox.updateState.downloading, false);
  assert.equal(sandbox.installUpdateAfterDownload, false);
});

test("a signed Mac copy in Applications installs updates itself", () => {
  const state = context("darwin", "signed").sandbox.getUpdateState();
  assert.equal(state.supported, true);
  assert.equal(state.manualReason, "");
});

for (const [where, options, reason] of [
  ["opened from the DMG", { execPath: "/Volumes/Raticode/Raticode.app/Contents/MacOS/Raticode", unwritable: { "/Volumes/Raticode/Raticode.app": "EROFS" } }, /running from the disk image.*drag it into Applications/],
  ["translocated by macOS", { execPath: "/private/var/folders/x/AppTranslocation/1234/d/Raticode.app/Contents/MacOS/Raticode" }, /temporary read-only copy.*drag it into Applications/],
]) {
  test(`a signed Mac copy ${where} falls back to the installer and says why`, async () => {
    const { sandbox, calls } = context("darwin", "signed", true, false, null, options);
    const state = sandbox.getUpdateState();
    assert.equal(state.supported, false);
    assert.match(state.manualReason, reason);
    await sandbox.checkForUpdates();
    await sandbox.downloadAndInstallUpdate();
    await sandbox.installDownloadedUpdate();
    assert.deepEqual(calls, ["manual-check", "mac-installer"]);
  });
}

test("a signed Mac copy this account can't write still updates itself (Squirrel.Mac asks for a password)", () => {
  const state = context("darwin", "signed", true, false, null, { unwritable: { "/Applications/Raticode.app": "EACCES" } }).sandbox.getUpdateState();
  assert.equal(state.supported, true);
  assert.equal(state.manualReason, "");
});

test("unsigned and development builds explain the manual update", () => {
  assert.match(context("darwin", "unsigned").sandbox.getUpdateState().manualReason, /isn't signed with Raticode's Apple Developer ID/);
  assert.match(context("darwin", "signed", false).sandbox.getUpdateState().manualReason, /Development builds/);
  assert.match(context("linux", "unsigned", false).sandbox.getUpdateState().manualReason, /Development builds/);
  assert.equal(context("linux", "unsigned").sandbox.getUpdateState().manualReason, "");
  assert.equal(context("linux", "unsigned", true, false, "pacman").sandbox.getUpdateState().manualReason, "");
});

class Emitter {
  constructor() { this.listeners = {}; }
  on(name, listener) { (this.listeners[name] ||= []).push(listener); return this; }
  emit(name, ...args) { for (const listener of this.listeners[name] || []) listener(...args); }
}

// Runs the real setupAutoUpdater wiring with fake electron-updater and Squirrel.Mac.
function updaterContext(platform) {
  const calls = [];
  const updater = new Emitter();
  const squirrel = new Emitter();
  Object.assign(updater, {
    checkForUpdates: async () => calls.push("auto-check"),
    downloadUpdate: async () => calls.push("auto-download"),
    quitAndInstall: () => calls.push("auto-install"),
  });
  const sandbox = {
    app: { isPackaged: true, getVersion: () => "0.5.4" },
    process: { platform, arch: "arm64", resourcesPath: "/resources", execPath: MAC_APP },
    path: path.posix,
    fs: { constants: { W_OK: 2 }, accessSync: () => {}, readFileSync: () => { throw Object.assign(new Error("ENOENT"), { code: "ENOENT" }); } },
    raticodeReleaseSigning: "signed",
    isSmokeTest: false,
    applicationLog: null,
    mainWindow: undefined,
    updateState: { available: true, info: { version: "0.5.5" } },
    installUpdateAfterDownload: false,
    isNoPublishedVersionsError: () => false,
    prepareLifecycle: async () => { calls.push("prepare"); return true; },
    stopBackend: () => calls.push("stop-backend"),
    setImmediate: (callback) => setImmediate(callback),
    timers: [],
    setTimeout: (callback, ms) => { sandbox.timers.push({ callback, ms, cleared: false }); return sandbox.timers.at(-1); },
    clearTimeout: (timer) => { if (timer) timer.cleared = true; },
    require: (name) => { assert.equal(name, "electron"); return { autoUpdater: squirrel }; },
    autoUpdater: updater,
  };
  vm.createContext(sandbox);
  vm.runInContext(
    source.slice(source.indexOf("let installedPackageManager;"), source.indexOf("async function checkLatestReleaseFallback()")),
    sandbox,
  );
  sandbox.setupAutoUpdater();
  return { sandbox, calls, updater, squirrel };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test("a Mac update restarts only after Squirrel.Mac accepts its signature", async () => {
  const { sandbox, calls, updater, squirrel } = updaterContext("darwin");
  await sandbox.downloadAndInstallUpdate();
  // electron-updater saved the zip and matched its sha512; Squirrel hasn't checked it yet.
  updater.emit("update-downloaded", { version: "0.5.5" });
  await settle();
  assert.equal(sandbox.updateState.verifying, true);
  assert.equal(sandbox.updateState.downloaded, false);
  assert.deepEqual(calls, ["auto-download"]);
  await assert.rejects(sandbox.installDownloadedUpdate(), /still checking the downloaded update/);
  assert.deepEqual(calls, ["auto-download"]);
  squirrel.emit("update-downloaded");
  await settle();
  assert.equal(sandbox.updateState.verifying, false);
  assert.equal(sandbox.updateState.downloaded, true);
  assert.equal(sandbox.updateState.info.version, "0.5.5");
  assert.deepEqual(calls, ["auto-download", "prepare", "stop-backend", "auto-install"]);
});

test("a Mac update Squirrel.Mac rejects leaves Raticode and its backend running", async () => {
  const { sandbox, calls, updater } = updaterContext("darwin");
  await sandbox.downloadAndInstallUpdate();
  updater.emit("update-downloaded", { version: "0.5.5" });
  // MacUpdater re-emits Squirrel.Mac's errors as its own.
  updater.emit("error", new Error("Code signature at URL file:///private/var/folders/x/update.abc/Raticode.app/ did not pass validation: code failed to satisfy specified code requirement(s)"));
  await settle();
  assert.deepEqual(calls, ["auto-download"]);
  assert.equal(sandbox.updateState.verifying, false);
  assert.equal(sandbox.updateState.downloaded, false);
  assert.equal(sandbox.installUpdateAfterDownload, false);
  assert.match(sandbox.updateState.error, /signature doesn't match this copy of Raticode\. Nothing was installed/);
});

test("a Mac update Squirrel.Mac never answers ends with an error instead of a stuck button", async () => {
  const { sandbox, calls, updater, squirrel } = updaterContext("darwin");
  await sandbox.downloadAndInstallUpdate();
  updater.emit("update-downloaded", { version: "0.5.5" });
  const [timer] = sandbox.timers;
  assert.equal(timer.ms, 10 * 60 * 1000);
  timer.callback();
  assert.equal(sandbox.updateState.verifying, false);
  assert.match(sandbox.updateState.error, /didn't finish checking the downloaded update, so nothing was installed/);
  // A late answer still makes the update available, but doesn't restart on its own.
  squirrel.emit("update-downloaded");
  await settle();
  assert.equal(sandbox.updateState.downloaded, true);
  assert.deepEqual(calls, ["auto-download"]);
});

test("Squirrel.Mac's answer cancels the verification timeout", async () => {
  const { sandbox, updater, squirrel } = updaterContext("darwin");
  await sandbox.downloadAndInstallUpdate();
  updater.emit("update-downloaded", { version: "0.5.5" });
  squirrel.emit("update-downloaded");
  await settle();
  assert.equal(sandbox.timers[0].cleared, true);
});

test("Linux and Windows still restart as soon as the download is verified", async () => {
  for (const platform of ["linux", "win32"]) {
    const { sandbox, calls, updater } = updaterContext(platform);
    await sandbox.downloadAndInstallUpdate();
    updater.emit("update-downloaded", { version: "0.5.5" });
    await settle();
    assert.equal(sandbox.updateState.downloaded, true);
    assert.deepEqual(calls, ["auto-download", "prepare", "stop-backend", "auto-install"]);
  }
});

test("a declined restart keeps the Mac update ready for Restart to update", async () => {
  const { sandbox, calls, updater, squirrel } = updaterContext("darwin");
  sandbox.prepareLifecycle = async () => { calls.push("prepare"); return false; };
  await sandbox.downloadAndInstallUpdate();
  updater.emit("update-downloaded", { version: "0.5.5" });
  squirrel.emit("update-downloaded");
  await settle();
  assert.deepEqual(calls, ["auto-download", "prepare"]);
  assert.equal(sandbox.updateState.downloaded, true);
});

test("updater errors are described accurately instead of passed through raw", () => {
  const { sandbox } = updaterContext("darwin");
  const describe = (error) => sandbox.describeUpdateError(error);
  const httpError = (statusCode) => Object.assign(new Error(`HTTP error: ${statusCode}`), { statusCode, code: `HTTP_ERROR_${statusCode}` });
  for (const [error, expected] of [
    [new Error("net::ERR_INTERNET_DISCONNECTED"), /Couldn't reach GitHub.*internet connection/],
    [new Error("getaddrinfo ENOTFOUND github.com"), /Couldn't reach GitHub/],
    [new Error("net::ERR_NAME_NOT_RESOLVED"), /Couldn't reach GitHub/],
    [httpError(429), /GitHub is limiting update requests/],
    [httpError(403), /GitHub is limiting update requests/],
    [Object.assign(new Error("Unable to find latest version on GitHub (https://github.com/zacharyivie/Raticode/releases/latest), please ensure a production release exists: HttpError: 429 Too Many Requests"), { code: "ERR_UPDATER_LATEST_VERSION_NOT_FOUND" }), /GitHub is limiting update requests/],
    [httpError(502), /GitHub returned HTTP 502/],
    [Object.assign(new Error("sha512 checksum mismatch, expected abc, got def"), { code: "ERR_CHECKSUM_MISMATCH" }), /didn't match the checksum.*Nothing was installed/],
    [new Error("Code signature at URL file:///x did not pass validation: code object is not signed at all"), /signature doesn't match this copy of Raticode/],
    [new Error("Cannot update while running on a read-only volume. The application is on a read-only volume."), /read-only location.*drag it into Applications/],
    [Object.assign(new Error("ZIP file not provided: []"), { code: "ERR_UPDATER_ZIP_FILE_NOT_FOUND" }), /no Mac update package/],
    [Object.assign(new Error("Cannot find latest-mac.yml in the latest release artifacts"), { code: "ERR_UPDATER_CHANNEL_FILE_NOT_FOUND" }), /doesn't list its update files yet/],
    [Object.assign(new Error("GitHub's rate limit for this network was reached. Try again after 3:05 PM."), { userFacing: true }), /^GitHub's rate limit for this network was reached\. Try again after 3:05 PM\.$/],
    [new Error("Something unexpected"), /^Something unexpected$/],
  ]) {
    assert.match(describe(error), expected, error.message);
  }
});

test("a failed check reports its error instead of up to date", async () => {
  const { sandbox, updater } = updaterContext("linux");
  updater.checkForUpdates = async () => { throw new Error("net::ERR_INTERNET_DISCONNECTED"); };
  const state = await sandbox.checkForUpdates();
  assert.equal(state.checking, false);
  assert.match(state.error, /Couldn't reach GitHub/);
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
  vm.runInNewContext(appSource.slice(appSource.indexOf("export function updateCheckNotice("), appSource.indexOf("export function ChatPane(")).replace(/^export /gm, ""), sandbox);
  const state = { supported: false, platform: "darwin", info: { installerUrl: "https://github.com/zacharyivie/Raticode/releases/download/v0.5.4/Raticode-0.5.4-arm64.dmg" } };
  assert.equal(sandbox.updateButtonLabel(state), "Download Mac installer");
  assert.match(sandbox.updateButtonTitle(state), /manual installation/);
  const blocked = { ...state, manualReason: "Raticode is running from the disk image, so it can't replace itself." };
  assert.match(sandbox.updateButtonTitle(blocked), /^Raticode is running from the disk image.*manual installation/);
  assert.match(sandbox.manualUpdateNotice(blocked), /^Raticode is running from the disk image.*Quit Raticode, open the downloaded DMG/);
  // A development build on a Mac opens the release page, so it must not promise an installer.
  assert.equal(sandbox.updateButtonLabel({ supported: false, platform: "darwin", info: {} }), "Open update downloads");
  assert.equal(sandbox.updateButtonLabel({ supported: true, verifying: true }), "Verifying update");
  assert.match(sandbox.updateButtonTitle({ supported: true, verifying: true }), /checking the downloaded update's signature/);
  assert.equal(sandbox.updateButtonLabel({ downloaded: true, supported: true }), "Restart to update");
  assert.equal(sandbox.updateButtonLabel({ supported: false, platform: "linux" }), "Open update downloads");
  const pacman = { supported: false, platform: "linux", managedBy: "pacman", info: { version: "0.5.3" } };
  assert.equal(sandbox.updateButtonLabel(pacman), "Update 0.5.3 with pacman");
  assert.match(sandbox.updateButtonTitle(pacman), /yay -Syu/);
});

test("the renderer never reports a failed check as up to date", () => {
  const appSource = fs.readFileSync(path.join(__dirname, "../../src/pages/App.jsx"), "utf8");
  const sandbox = {};
  vm.runInNewContext(appSource.slice(appSource.indexOf("export function updateCheckNotice("), appSource.indexOf("export function ChatPane(")).replace(/^export /gm, ""), sandbox);
  assert.deepEqual({ ...sandbox.updateCheckNotice({ available: false, error: "Couldn't reach GitHub to update Raticode." }) }, { type: "error", message: "Couldn't reach GitHub to update Raticode." });
  assert.equal(sandbox.updateCheckNotice(undefined).type, "error");
  assert.deepEqual({ ...sandbox.updateCheckNotice({ available: false, error: "" }) }, { type: "success", message: "Raticode is up to date" });
  assert.equal(sandbox.updateCheckNotice({ available: true, info: { version: "0.5.4" } }).message, "Raticode 0.5.4 is available");
  assert.equal(sandbox.updateCheckNotice({ info: { noReleases: true } }).message, "No published Raticode releases yet");
});
