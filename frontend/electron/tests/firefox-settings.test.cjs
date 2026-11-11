require("./test-tmp.cjs");
// Firefox settings, extensions and importing from an everyday Firefox.
// Everyday profiles here are synthetic folders in a temporary directory; the
// real user's Firefox profiles are never read by these tests. The last group
// starts a real, windowless Firefox and is skipped when none is installed.
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {
  PROFILE_PREFS,
  createFirefoxEngine,
  findFirefox,
  sanitizeUserPrefs,
  writeProfile,
} = require("../firefox-engine.cjs");
const {
  findFirefoxProfiles,
  importProfile,
  importablePrefs,
  inspectProfile,
  profileLockState,
  readExtensionManifest,
  readPrefs,
  removeAutomationContainers,
  stripContainerRows,
  writePrefs,
} = require("../firefox-profile.cjs");
const { addonSlugFromUrl, createAddonCatalog, versionCompatible } = require("../firefox-addons.cjs");
const { createFirefoxManager, permissionLines } = require("../firefox-manager.cjs");
const { FirefoxContents } = require("../firefox-contents.cjs");
const { zip } = require("./zip-fixture.cjs");

const FIREFOX = process.env.RATICODE_SKIP_FIREFOX_TESTS === "1" ? "" : findFirefox();
const realFirefox = { skip: FIREFOX ? false : "Firefox is not installed" };
const network = { skip: process.env.RATICODE_FIREFOX_NETWORK_TESTS === "1" && FIREFOX ? false : "Set RATICODE_FIREFOX_NETWORK_TESTS=1 to install a real extension from Firefox Add-ons" };

function temp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

// A cookie database with the column Firefox uses to tell containers apart.
function cookieDatabase(file, rows) {
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(file);
  db.exec("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '', name TEXT, value TEXT, host TEXT)");
  const insert = db.prepare("INSERT INTO moz_cookies (originAttributes, name, value, host) VALUES (?, ?, 'v', 'example.com')");
  for (const [attributes, name] of rows) insert.run(attributes, name);
  db.close();
}

function cookieNames(file) {
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(file);
  try { return db.prepare("SELECT name FROM moz_cookies ORDER BY name").all().map((row) => row.name); } finally { db.close(); }
}

// A synthetic everyday profile with a few files and one extension.
function everydayProfile(root, name = "abcd1234.default-release") {
  const profile = path.join(root, name);
  fs.mkdirSync(path.join(profile, "extensions"), { recursive: true });
  writePrefs(path.join(profile, "prefs.js"), new Map([
    ["font.size.variable.x-western", 22],
    ["dom.security.https_only_mode", true],
    ["services.sync.username", "person@example.com"],
    ["identity.fxaccounts.account.device.name", "Laptop"],
    ["toolkit.telemetry.cachedClientID", "x"],
    ["extensions.webextensions.uuids", JSON.stringify({ "blocker@example.com": "11111111-2222-3333-4444-555555555555", "other@example.com": "99999999-2222-3333-4444-555555555555" })],
  ]));
  fs.writeFileSync(path.join(profile, "places.sqlite"), "bookmarks");
  fs.writeFileSync(path.join(profile, "places.sqlite-wal"), "wal");
  cookieDatabase(path.join(profile, "cookies.sqlite"), [
    ["", "everyday-default"],
    ["^userContextId=6", "work-container"],
    ["^partitionKey=%28https%2Cexample.com%29", "partitioned"],
    ["^userContextId=12&partitionKey=%28https%2Cexample.com%29", "shopping-partitioned"],
  ]);
  fs.writeFileSync(path.join(profile, "containers.json"), JSON.stringify({ version: 5, lastUserContextId: 9, identities: [{ userContextId: 6, name: "Work", public: true }] }));
  fs.writeFileSync(path.join(profile, "logins.json"), "secret");
  fs.writeFileSync(path.join(profile, "key4.db"), "secret");
  fs.writeFileSync(path.join(profile, "extensions", "blocker@example.com.xpi"), zip({ "manifest.json": JSON.stringify({ manifest_version: 2, name: "Blocker", version: "1.2", browser_specific_settings: { gecko: { id: "blocker@example.com" } }, permissions: ["<all_urls>", "storage"] }) }));
  fs.writeFileSync(path.join(profile, "extensions.json"), JSON.stringify({ addons: [
    { id: "blocker@example.com", type: "extension", location: "app-profile", version: "1.2", active: true, defaultLocale: { name: "Blocker" } },
    { id: "theme@example.com", type: "theme", location: "app-profile", version: "1", active: true, defaultLocale: { name: "Theme" } },
    { id: "system@mozilla.org", type: "extension", location: "app-system-defaults", version: "1", active: true },
  ] }));
  const storage = path.join(profile, "storage", "default", "moz-extension+++11111111-2222-3333-4444-555555555555^userContextId=4294967295", "idb");
  fs.mkdirSync(storage, { recursive: true });
  fs.writeFileSync(path.join(storage, "data.sqlite"), "extension settings");
  fs.mkdirSync(path.join(profile, "storage", "default", "moz-extension+++99999999-2222-3333-4444-555555555555"), { recursive: true });
  return profile;
}

// ---------------------------------------------------------------------------

test("everyday Firefox profiles are found from profiles.ini, skipping Raticode's own", () => {
  const home = temp("firefox-home-");
  const root = path.join(home, ".mozilla", "firefox");
  const own = path.join(home, "raticode", "firefox-profile");
  everydayProfile(root, "abcd1234.default-release");
  everydayProfile(root, "efgh5678.work");
  fs.mkdirSync(own, { recursive: true });
  fs.writeFileSync(path.join(own, "prefs.js"), "");
  fs.mkdirSync(path.join(root, "missing"), { recursive: true });
  fs.writeFileSync(path.join(root, "profiles.ini"), [
    "[Install4F96D1932A9F858E]", "Default=abcd1234.default-release", "",
    "[Profile0]", "Name=default-release", "IsRelative=1", "Path=abcd1234.default-release", "",
    "[Profile1]", "Name=work", "IsRelative=1", "Path=efgh5678.work", "",
    "[Profile2]", "Name=gone", "IsRelative=1", "Path=missing", "",
    "[Profile3]", "Name=raticode", "IsRelative=0", `Path=${own}`, "",
  ].join("\n"));
  const found = findFirefoxProfiles({ platform: "linux", home, env: {}, exclude: own });
  assert.deepEqual(found.map((item) => item.name), ["default-release", "work"]);
  assert.equal(found[0].isDefault, true);
  assert.equal(found[0].state, "closed");
});

test("a profile in use is detected from Firefox's lock on Linux", () => {
  const profile = temp("firefox-lock-");
  assert.equal(profileLockState(profile, { platform: "linux" }), "closed");
  fs.symlinkSync("127.0.1.1:+4242", path.join(profile, "lock"));
  assert.equal(profileLockState(profile, { platform: "linux", processAlive: (pid) => pid === 4242 }), "running");
  assert.equal(profileLockState(profile, { platform: "linux", processAlive: () => false }), "closed");
  const mac = temp("firefox-lock-mac-");
  fs.writeFileSync(path.join(mac, ".parentlock"), "");
  assert.equal(profileLockState(mac, { platform: "darwin" }), "unknown");
});

test("inspecting a profile lists what it has, with user extensions only", () => {
  const profile = everydayProfile(temp("firefox-inspect-"));
  const info = inspectProfile(profile, { platform: "linux" });
  assert.deepEqual(info.available, { settings: true, bookmarks: true, permissions: false, cookies: true, extensions: true, extensionData: true });
  assert.deepEqual(info.extensions, [{ id: "blocker@example.com", name: "Blocker", version: "1.2", active: true }]);
  assert.throws(() => inspectProfile(temp("not-a-profile-")), /not a Firefox profile/);
});

test("imported settings leave out accounts, telemetry, sync and add-on bookkeeping", () => {
  const prefs = importablePrefs(new Map([
    ["font.size.variable.x-western", 22],
    ["services.sync.username", "a"],
    ["identity.fxaccounts.lastSignedInUserHash", "b"],
    ["toolkit.telemetry.cachedClientID", "c"],
    ["remote.enabled", true],
    ["extensions.webextensions.uuids", "{}"],
    ["network.trr.mode", 2],
  ]));
  assert.deepEqual([...prefs.keys()], ["font.size.variable.x-western", "network.trr.mode"]);
});

test("an import copies only the chosen items, keeps a backup and never touches the source", () => {
  const source = everydayProfile(temp("firefox-source-"));
  const before = Object.fromEntries(fs.readdirSync(source).map((name) => [name, fs.statSync(path.join(source, name)).mtimeMs]));
  const target = path.join(temp("firefox-target-"), "firefox-profile");
  fs.mkdirSync(target);
  fs.writeFileSync(path.join(target, "places.sqlite"), "raticode history");
  writePrefs(path.join(target, "prefs.js"), new Map([["browser.example", 1], ["extensions.webextensions.uuids", JSON.stringify({ "own@example.com": "aaaa" })]]));
  const backupDir = path.join(temp("firefox-backup-"), "backup");
  const summary = importProfile({
    source,
    target,
    items: { settings: true, bookmarks: true, extensions: true, extensionData: true, cookies: false },
    extensionIds: ["blocker@example.com"],
    backupDir,
    options: { platform: "linux" },
  });
  assert.equal(fs.readFileSync(path.join(target, "places.sqlite"), "utf8"), "bookmarks");
  assert.equal(fs.readFileSync(path.join(target, "places.sqlite-wal"), "utf8"), "wal");
  assert.equal(fs.readFileSync(path.join(backupDir, "places.sqlite"), "utf8"), "raticode history");
  assert.equal(fs.existsSync(path.join(target, "cookies.sqlite")), false);
  assert.equal(fs.existsSync(path.join(target, "logins.json")), false);
  assert.equal(fs.existsSync(path.join(target, "key4.db")), false);
  // Only the chosen extension's data comes along.
  assert.ok(fs.existsSync(path.join(target, "storage", "default", "moz-extension+++11111111-2222-3333-4444-555555555555^userContextId=4294967295", "idb", "data.sqlite")));
  assert.equal(fs.existsSync(path.join(target, "storage", "default", "moz-extension+++99999999-2222-3333-4444-555555555555")), false);
  const prefs = readPrefs(path.join(target, "prefs.js"));
  assert.equal(prefs.get("font.size.variable.x-western"), 22);
  assert.equal(prefs.get("browser.example"), 1);
  assert.equal(prefs.has("services.sync.username"), false);
  assert.deepEqual(JSON.parse(prefs.get("extensions.webextensions.uuids")), { "own@example.com": "aaaa", "blocker@example.com": "11111111-2222-3333-4444-555555555555" });
  assert.deepEqual(summary.extensions.map((item) => item.id), ["blocker@example.com"]);
  assert.equal(readPrefs(path.join(backupDir, "prefs.js")).get("browser.example"), 1);
  const after = Object.fromEntries(fs.readdirSync(source).map((name) => [name, fs.statSync(path.join(source, name)).mtimeMs]));
  assert.deepEqual(after, before);
});

test("databases are not copied while that Firefox is open", () => {
  const source = everydayProfile(temp("firefox-open-"));
  fs.symlinkSync(`127.0.1.1:+${process.pid}`, path.join(source, "lock"));
  const target = path.join(temp("firefox-target-"), "firefox-profile");
  const options = { platform: "linux", processAlive: () => true };
  assert.throws(() => importProfile({ source, target, items: { bookmarks: true }, backupDir: path.join(target, "..", "b1"), options }), /Close Firefox before importing bookmarks and history/);
  // Settings and extension packages can be read while it runs.
  const summary = importProfile({ source, target, items: { settings: true, extensions: true }, extensionIds: ["blocker@example.com"], backupDir: path.join(target, "..", "b2"), options });
  assert.deepEqual(summary.copied, ["prefs.js"]);
  // Where Firefox's lock can't be read, the user's confirmation counts.
  const mac = everydayProfile(temp("firefox-mac-"));
  fs.writeFileSync(path.join(mac, ".parentlock"), "");
  assert.throws(() => importProfile({ source: mac, target, items: { cookies: true }, backupDir: path.join(target, "..", "b3"), options: { platform: "darwin" } }), /cannot tell whether Firefox is closed/);
  importProfile({ source: mac, target, items: { cookies: true }, backupDir: path.join(target, "..", "b4"), options: { platform: "darwin", assumeClosed: true } });
  // Only sign-ins from that Firefox's normal tabs come along.
  assert.deepEqual(cookieNames(path.join(target, "cookies.sqlite")), ["everyday-default", "partitioned"]);
  // Containers made later, Rem's among them, are numbered above every imported one.
  assert.equal(JSON.parse(fs.readFileSync(path.join(target, "containers.json"), "utf8")).lastUserContextId, 12);
});

test("a sign-in database Raticode can't filter is not imported, and Raticode's own copy returns", () => {
  const source = everydayProfile(temp("firefox-broken-"));
  fs.writeFileSync(path.join(source, "cookies.sqlite"), "not a database");
  const target = path.join(temp("firefox-target-"), "firefox-profile");
  fs.mkdirSync(target);
  cookieDatabase(path.join(target, "cookies.sqlite"), [["", "raticode-own"]]);
  const summary = importProfile({ source, target, items: { cookies: true }, backupDir: path.join(target, "..", "backup"), options: { platform: "linux" } });
  assert.deepEqual(summary.skipped, ["Sign-ins to websites (cookies)"]);
  assert.deepEqual(summary.copied, []);
  assert.deepEqual(cookieNames(path.join(target, "cookies.sqlite")), ["raticode-own"]);
});

test("site permissions from other containers are left out too", () => {
  const { DatabaseSync } = require("node:sqlite");
  const file = path.join(temp("firefox-perms-"), "permissions.sqlite");
  const db = new DatabaseSync(file);
  db.exec("CREATE TABLE moz_perms (id INTEGER PRIMARY KEY, origin TEXT, type TEXT)");
  for (const origin of ["https://example.com", "https://example.com^userContextId=3", "https://bank.example^userContextId=15"]) db.prepare("INSERT INTO moz_perms (origin, type) VALUES (?, 'camera')").run(origin);
  db.close();
  assert.deepEqual(stripContainerRows(file, [["moz_perms", "origin"], ["moz_hosts", "originAttributes"]]), { highest: 15, removed: 2 });
  const check = new DatabaseSync(file);
  assert.deepEqual(check.prepare("SELECT origin FROM moz_perms").all().map((row) => row.origin), ["https://example.com"]);
  check.close();
});

test("extension packages report their name, ID and permissions, including localized names", () => {
  const directory = temp("firefox-xpi-");
  const file = path.join(directory, "x.xpi");
  fs.writeFileSync(file, zip({
    "manifest.json": JSON.stringify({ manifest_version: 3, name: "__MSG_extName__", default_locale: "en", version: "2.0", browser_specific_settings: { gecko: { id: "x@example.com" } }, permissions: ["storage"], host_permissions: ["https://example.com/*"] }),
    "_locales/en/messages.json": JSON.stringify({ extName: { message: "Example Helper" } }),
  }));
  assert.deepEqual(readExtensionManifest(file), { id: "x@example.com", name: "Example Helper", version: "2.0", manifestVersion: 3, permissions: ["storage", "https://example.com/*"] });
  fs.writeFileSync(file, "not a zip");
  assert.throws(() => readExtensionManifest(file), /not an extension package/);
});

test("Raticode's settings come before its required ones in user.js, and old automation containers go", () => {
  const profile = temp("firefox-userjs-");
  fs.writeFileSync(path.join(profile, "containers.json"), JSON.stringify({ version: 5, lastUserContextId: 9, identities: [
    { userContextId: 1, name: "Personal", public: true },
    { userContextId: 7, name: "remote-0b594fd2-e8de-4fd1-bad5-9b3d791bdb3d", public: true },
    { userContextId: 8, name: "Shopping (Multi-Account Containers)", public: true },
  ] }));
  writeProfile(profile, { "font.size.variable.x-western": 20, "signon.rememberSignons": true });
  const userJs = fs.readFileSync(path.join(profile, "user.js"), "utf8");
  assert.ok(userJs.indexOf('"font.size.variable.x-western", 20') < userJs.indexOf('"remote.prefs.recommended", false'));
  // A user choice cannot override a required setting.
  assert.equal((userJs.match(/"signon\.rememberSignons"/g) || []).length, 1);
  assert.match(userJs, /"signon\.rememberSignons", false/);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(profile, "containers.json"), "utf8")).identities.map((item) => item.userContextId), [1, 8]);
  assert.equal(removeAutomationContainers(profile), 0);
});

test("preferences the user may set exclude remote control, add-on loading and Raticode's own", () => {
  const { prefs, rejected } = sanitizeUserPrefs({
    "font.size.variable.x-western": 18,
    "intl.accept_languages": "fr, en",
    "general.smoothScroll": false,
    "remote.enabled": true,
    "devtools.debugger.remote-enabled": true,
    "extensions.enabledScopes": 15,
    "permissions.default.camera": 1,
    "bad name!": 1,
    "too.big": 2 ** 40,
  });
  assert.deepEqual(prefs, { "font.size.variable.x-western": 18, "intl.accept_languages": "fr, en", "general.smoothScroll": false });
  assert.deepEqual(rejected.map((item) => item.name).sort(), ["bad name!", "devtools.debugger.remote-enabled", "extensions.enabledScopes", "permissions.default.camera", "remote.enabled", "too.big"].sort());
  assert.equal(Object.hasOwn(PROFILE_PREFS, "extensions.enabledScopes"), true);
});

test("Firefox Add-ons addresses, versions and permission wording", () => {
  assert.equal(addonSlugFromUrl("https://addons.mozilla.org/en-US/firefox/addon/ublock-origin/?src=search"), "ublock-origin");
  assert.equal(addonSlugFromUrl("https://addons.mozilla.org/firefox/addon/dark%20reader/"), "dark reader");
  assert.equal(addonSlugFromUrl("https://evil.example/firefox/addon/x/"), "");
  assert.equal(versionCompatible("158.0.1", { min: "115.0", max: "*" }), true);
  assert.equal(versionCompatible("158.0.1", { min: "160.0", max: "*" }), false);
  assert.equal(versionCompatible("158.0.1", { min: "100.0", max: "140.*" }), false);
  assert.match(permissionLines(["<all_urls>", "tabs", "storage", "https://mail.example.com/*"]), /all websites[\s\S]*tabs[\s\S]*mail\.example\.com/);
});

function fakeFetch(routes) {
  return async (url) => {
    const handler = routes[url] || routes[new URL(url).pathname];
    if (!handler) return { ok: false, status: 404, json: async () => ({}) };
    return handler(url);
  };
}

test("extensions download only from Mozilla and only when the file matches Mozilla's hash", async () => {
  const body = Buffer.from("signed package");
  const hash = crypto.createHash("sha256").update(body).digest("hex");
  const stream = (url, bytes = body) => ({ ok: true, status: 200, url, body: (async function* chunks() { yield bytes; })() });
  const catalog = createAddonCatalog({ fetchImpl: fakeFetch({
    "https://addons.mozilla.org/firefox/downloads/file/1/x.xpi": (url) => stream(url),
    "https://addons.mozilla.org/firefox/downloads/file/2/x.xpi": (url) => stream(url, Buffer.from("tampered")),
    "https://addons.mozilla.org/firefox/downloads/file/3/x.xpi": () => stream("https://evil.example/x.xpi"),
  }) });
  const directory = temp("firefox-download-");
  const file = path.join(directory, "x.xpi");
  await catalog.download({ url: "https://addons.mozilla.org/firefox/downloads/file/1/x.xpi", hash }, file);
  assert.equal(fs.readFileSync(file, "utf8"), "signed package");
  await assert.rejects(catalog.download({ url: "https://addons.mozilla.org/firefox/downloads/file/2/x.xpi", hash }, file), /does not match/);
  assert.equal(fs.existsSync(file), false);
  await assert.rejects(catalog.download({ url: "https://addons.mozilla.org/firefox/downloads/file/3/x.xpi", hash }, file), /moved away from Firefox Add-ons/);
  await assert.rejects(catalog.download({ url: "https://evil.example/x.xpi", hash }, file), /only from Firefox Add-ons/);
});

test("catalog details refuse themes and files without a published hash", async () => {
  const addon = (extra) => async () => ({ ok: true, status: 200, json: async () => ({ guid: "a@b", slug: "a", name: { "en-US": "A" }, type: "extension", current_version: { version: "1", file: { url: "https://addons.mozilla.org/f.xpi", hash: `sha256:${"0".repeat(64)}`, permissions: ["tabs"], host_permissions: ["<all_urls>"] }, compatibility: { firefox: { min: "100.0", max: "*" } } }, authors: [{ name: "Dev" }], ...extra }) });
  const catalog = (extra) => createAddonCatalog({ fetchImpl: fakeFetch({ "/api/v5/addons/addon/a/": addon(extra) }) });
  const details = await catalog({}).details("a");
  assert.equal(details.name, "A");
  assert.deepEqual(details.permissions, ["tabs", "<all_urls>"]);
  assert.equal(details.file.hash, "0".repeat(64));
  await assert.rejects(catalog({ type: "statictheme" }).details("a"), /extensions only/);
  await assert.rejects(catalog({ current_version: { file: { url: "x" } } }).details("a"), /did not offer a file/);
  await assert.rejects(catalog({}).details("../../evil"), /Choose an extension/);
});

// A stand-in engine and dialog for the manager.
function managerHarness({ responses = [], firefoxVersion = "158.0.1" } = {}) {
  const dataDir = temp("firefox-manager-");
  const profile = path.join(dataDir, "firefox-profile");
  fs.mkdirSync(profile);
  const calls = [];
  let userPrefs = {};
  let installed = [];
  const engine = {
    profileDir: () => profile,
    status: () => ({ available: true, version: firefoxVersion, running: true, profile }),
    ensure: async () => {},
    readUserPrefs: () => ({ ...userPrefs }),
    saveUserPrefs: (prefs) => {
      const result = sanitizeUserPrefs(prefs);
      userPrefs = result.prefs;
      return { ...result, restartNeeded: true };
    },
    listExtensions: async () => installed,
    installExtension: async (file) => { calls.push(["install", fs.readFileSync(file, "utf8")]); installed = [...installed, { id: "a@b", name: "A", version: "1", active: true }]; return "a@b"; },
    uninstallExtension: async (id) => { calls.push(["uninstall", id]); installed = installed.filter((item) => item.id !== id); },
    stopAndWait: async () => { calls.push(["stop"]); },
  };
  const dialogs = [];
  const dialog = {
    showMessageBox: async (options) => { dialogs.push(options); return { response: responses.shift() ?? 1 }; },
    showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
  };
  const catalog = {
    search: async () => [],
    details: async () => ({ guid: "a@b", slug: "a", name: "A", version: "1", authors: ["Dev"], permissions: ["<all_urls>"], compatibility: { min: "160.0", max: "*" }, file: { url: "https://addons.mozilla.org/f.xpi", hash: "x" } }),
    download: async (_file, destination) => { calls.push(["download"]); fs.writeFileSync(destination, "package"); },
  };
  const hooks = { stopFirefox: () => calls.push(["stop-tabs"]), reviveFirefoxTabs: async () => calls.push(["revive"]), restartFirefox: async () => calls.push(["restart"]), openTab: (url) => calls.push(["open", url]) };
  const manager = createFirefoxManager({ engine, dataDir, dialog, hooks, catalog, platform: "linux", home: dataDir, env: {} });
  return { manager, calls, dialogs, catalog, engine, dataDir, profile, setUserPrefs: (value) => { userPrefs = value; } };
}

test("Firefox settings show where each value comes from and save only valid choices", async () => {
  const { manager, profile } = managerHarness();
  writePrefs(path.join(profile, "prefs.js"), new Map([["font.size.variable.x-western", 22]]));
  let data = await manager.handle("firefox-settings");
  const font = data.settings.find((item) => item.pref === "font.size.variable.x-western");
  assert.deepEqual([font.value, font.source], [22, "firefox"]);
  assert.equal(data.settings.find((item) => item.pref === "dom.security.https_only_mode").source, "default");
  assert.ok(data.required.some((item) => item.name === "permissions.default.camera" && /permission prompt/.test(item.reason)));
  data = await manager.handle("firefox-settings-save", { prefs: { "dom.security.https_only_mode": true, "network.trr.mode": "3", "browser.custom.thing": "x" } });
  assert.equal(data.restartNeeded, true);
  assert.deepEqual(data.custom, [{ name: "browser.custom.thing", value: "x" }]);
  assert.equal(data.settings.find((item) => item.pref === "network.trr.mode").value, 3);
  await assert.rejects(manager.handle("firefox-settings-save", { prefs: { "network.trr.mode": 7 } }), /has no option 7/);
  await assert.rejects(manager.handle("firefox-settings-save", { prefs: { "remote.enabled": true } }), /keeps remote\.enabled fixed/);
  data = await manager.handle("firefox-settings-save", { prefs: { "browser.custom.thing": null } });
  assert.deepEqual(data.custom, []);
});

test("extension installs check the Firefox version and ask before downloading", async () => {
  const { manager, calls, dialogs, catalog } = managerHarness({ responses: [1, 0, 0] });
  await assert.rejects(manager.handle("firefox-addon-install", { key: "a" }), /works with Firefox 160\.0 to \*, not Firefox 158\.0\.1/);
  catalog.details = async () => ({ guid: "a@b", slug: "a", name: "A", version: "1", authors: ["Dev"], permissions: ["<all_urls>"], compatibility: { min: "100.0", max: "*" }, file: { url: "https://addons.mozilla.org/f.xpi", hash: "x" } });
  const cancelled = await manager.handle("firefox-addon-install", { key: "a" });
  assert.equal(cancelled.cancelled, true);
  assert.equal(calls.some(([name]) => name === "download"), false);
  assert.match(dialogs[0].detail, /all websites[\s\S]*including the tabs Rem uses/);
  const added = await manager.handle("firefox-addon-install", { key: "a" });
  assert.deepEqual(added.installed, { id: "a@b", name: "A" });
  assert.deepEqual(calls.filter(([name]) => name !== "stop"), [["download"], ["install", "package"]]);
  const removed = await manager.handle("firefox-addon-remove", { id: "a@b" });
  assert.deepEqual(removed.extensions, []);
  assert.deepEqual(calls.at(-1), ["uninstall", "a@b"]);
});

test("imports run only for a profile the user picked, after confirming, and restart Firefox", async () => {
  const { manager, calls, dialogs, dataDir, profile } = managerHarness({ responses: [1, 0] });
  const root = path.join(dataDir, ".mozilla", "firefox");
  const source = everydayProfile(root);
  fs.writeFileSync(path.join(root, "profiles.ini"), "[Profile0]\nName=default\nIsRelative=1\nPath=abcd1234.default-release\n");
  await assert.rejects(manager.handle("firefox-import", { path: source, items: { settings: true } }), /Choose the profile from the list/);
  const { profiles } = await manager.handle("firefox-profiles");
  assert.deepEqual(profiles.map((item) => item.path), [source]);
  const inspected = await manager.handle("firefox-profile-inspect", { path: source });
  assert.equal(inspected.profile.extensions[0].name, "Blocker");
  assert.deepEqual(await manager.handle("firefox-import", { path: source, items: { settings: true } }), { cancelled: true });
  assert.equal(calls.length, 0);
  const done = await manager.handle("firefox-import", { path: source, items: { settings: true, bookmarks: true, extensions: true }, extensionIds: ["blocker@example.com"] });
  assert.match(dialogs.at(-1).detail, /only reads that profile/);
  assert.deepEqual(calls.map(([name]) => name), ["stop-tabs", "stop", "revive", "install"]);
  assert.deepEqual(done.imported.installed, ["Blocker"]);
  assert.equal(readPrefs(path.join(profile, "prefs.js")).get("font.size.variable.x-western"), 22);
  assert.equal(fs.readFileSync(path.join(profile, "places.sqlite"), "utf8"), "bookmarks");
  assert.ok(done.imported.backupDir.startsWith(path.join(dataDir, "firefox-profile-backups")));
});

// ---------------------------------------------------------------------------
// Real Firefox, headless

async function fontSize(engine) {
  const server = http.createServer((_request, response) => { response.setHeader("Content-Type", "text/html"); response.end("<p id=p>text</p>"); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const contents = new FirefoxContents({ engine, kind: "user" });
  try {
    await contents.loadURL(`http://127.0.0.1:${server.address().port}/`);
    return await contents.evaluateIn("test", "getComputedStyle(document.getElementById('p')).fontSize");
  } finally {
    contents.close();
    server.close();
  }
}

test("settings chosen in Raticode and settings imported from another profile reach Firefox", realFirefox, async () => {
  const dataDir = temp("firefox-real-settings-");
  const engine = createFirefoxEngine({ dataDir });
  try {
    assert.equal(await fontSize(engine), "16px");
    engine.saveUserPrefs({ "font.size.variable.x-western": 20 });
    await engine.stopAndWait();
    assert.equal(await fontSize(engine), "20px");
    // Imported prefs land in prefs.js; a Raticode choice still wins over them.
    engine.saveUserPrefs({});
    await engine.stopAndWait();
    const source = everydayProfile(temp("firefox-real-source-"));
    importProfile({ source, target: engine.profileDir(), items: { settings: true }, backupDir: path.join(dataDir, "backup"), options: { platform: "linux" } });
    assert.equal(await fontSize(engine), "22px");
    // Containers made by automation do not pile up across launches; others stay.
    await engine.stopAndWait();
    const file = path.join(engine.profileDir(), "containers.json");
    const data = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : { version: 5, lastUserContextId: 0, identities: [] };
    data.identities = [...data.identities.filter((item) => !/^remote-/.test(item.name)), { userContextId: 900, name: "remote-0b594fd2-e8de-4fd1-bad5-9b3d791bdb3d", public: true }, { userContextId: 901, name: "Kept", public: true }];
    data.lastUserContextId = Math.max(data.lastUserContextId || 0, 901);
    fs.writeFileSync(file, JSON.stringify(data));
    await engine.ensure();
    const names = JSON.parse(fs.readFileSync(file, "utf8")).identities.map((item) => item.name);
    assert.equal(names.includes("remote-0b594fd2-e8de-4fd1-bad5-9b3d791bdb3d"), false);
    assert.equal(names.includes("Kept"), true);
  } finally {
    engine.stop();
  }
});

test("Firefox refuses unsigned extensions and Firefox's own pages stay out of reach", realFirefox, async () => {
  const dataDir = temp("firefox-real-unsigned-");
  const engine = createFirefoxEngine({ dataDir });
  try {
    const file = path.join(dataDir, "unsigned.xpi");
    fs.writeFileSync(file, zip({ "manifest.json": JSON.stringify({ manifest_version: 2, name: "Unsigned", version: "1.0", browser_specific_settings: { gecko: { id: "unsigned@example.com" } } }) }));
    await assert.rejects(engine.installExtension(file), /signature|signed|corrupt|Could not install/i);
    assert.deepEqual(await engine.listExtensions(), []);
    const contents = new FirefoxContents({ engine, kind: "user" });
    await assert.rejects(contents.loadURL("about:preferences"), /not allowed/);
    contents.close();
  } finally {
    engine.stop();
  }
});

test("a signed extension from Firefox Add-ons installs, survives a restart and imports with its storage ID", network, async () => {
  const catalog = createAddonCatalog();
  const addon = await catalog.details("ublock-origin");
  const sourceDir = temp("firefox-real-addon-source-");
  const source = createFirefoxEngine({ dataDir: sourceDir });
  const download = path.join(sourceDir, "addon.xpi");
  try {
    await catalog.download(addon.file, download);
    assert.equal(await source.installExtension(download), addon.guid);
    await source.stopAndWait();
    const afterRestart = await source.listExtensions();
    assert.ok(afterRestart.some((item) => item.id === addon.guid && item.active), JSON.stringify({ guid: addon.guid, afterRestart }));
    await source.stopAndWait();
    const sourceUuid = JSON.parse(readPrefs(path.join(source.profileDir(), "prefs.js")).get("extensions.webextensions.uuids"))[addon.guid];
    assert.ok(sourceUuid);
    // Import it into a second Raticode profile, as from an everyday Firefox.
    const targetDir = temp("firefox-real-addon-target-");
    const target = createFirefoxEngine({ dataDir: targetDir });
    try {
      const summary = importProfile({ source: source.profileDir(), target: target.profileDir(), items: { extensions: true, extensionData: true }, extensionIds: [addon.guid], backupDir: path.join(targetDir, "backup"), options: { platform: "linux" } });
      assert.equal(summary.extensions.length, 1);
      const copy = path.join(targetDir, "copy.xpi");
      fs.copyFileSync(summary.extensions[0].file, copy);
      await target.installExtension(copy);
      await target.stopAndWait();
      const targetUuid = JSON.parse(readPrefs(path.join(target.profileDir(), "prefs.js")).get("extensions.webextensions.uuids"))[addon.guid];
      assert.equal(targetUuid, sourceUuid);
      await target.uninstallExtension(addon.guid);
      assert.deepEqual(await target.listExtensions(), []);
    } finally {
      target.stop();
    }
  } finally {
    source.stop();
  }
});

test("Firefox's install errors are reported in plain words", () => {
  const { installFailure } = require("../firefox-manager.cjs");
  assert.match(installFailure(new Error("Could not install Add-on: ERROR_SIGNEDSTATE_REQUIRED: The addon must be signed and isn't.")), /not signed by Mozilla/);
  assert.match(installFailure(new Error("Could not install Add-on: ERROR_CORRUPT_FILE: The file appears to be corrupt.")), /damaged or is not a Firefox extension/);
  assert.equal(installFailure(new Error("Could not install Add-on: ERROR_NETWORK_FAILURE: Something else.")), "Something else");
});

test("imported sign-ins from other containers never reach Rem's container, across restarts", realFirefox, async () => {
  const { DatabaseSync } = require("node:sqlite");
  const server = http.createServer((_request, response) => { response.setHeader("Content-Type", "text/html"); response.end("<title>cookies</title>"); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const page = `http://127.0.0.1:${server.address().port}/`;
  const cookie = (name) => ({ name, value: { type: "string", value: "secret" }, domain: "127.0.0.1", path: "/", sameSite: "lax", httpOnly: false, secure: false, expiry: Math.floor(Date.now() / 1000) + 86400 });
  const everyday = createFirefoxEngine({ dataDir: temp("firefox-real-everyday-") });
  const raticode = createFirefoxEngine({ dataDir: temp("firefox-real-raticode-") });
  try {
    // An everyday Firefox signed in to the same site in its normal tabs and in eight containers.
    await everyday.ensure();
    await everyday.send("storage.setCookie", { cookie: cookie("everyday-default"), partition: { type: "storageKey", userContext: "default" } });
    for (let index = 0; index < 8; index += 1) {
      const { userContext } = await everyday.send("browser.createUserContext", {});
      await everyday.send("storage.setCookie", { cookie: cookie(`container-${index}`), partition: { type: "storageKey", userContext } });
    }
    await everyday.stopAndWait();
    const source = new DatabaseSync(path.join(everyday.profileDir(), "cookies.sqlite"), { readOnly: true });
    const containerIds = source.prepare("SELECT originAttributes FROM moz_cookies WHERE originAttributes LIKE '%userContextId=%'").all().map((row) => Number(/userContextId=(\d+)/.exec(row.originAttributes)[1]));
    source.close();
    assert.equal(containerIds.length, 8, "the everyday profile should hold container sign-ins");

    // Raticode's Firefox has run before, so its next container numbers overlap those.
    await raticode.ensure();
    await raticode.stopAndWait();
    const summary = importProfile({ source: everyday.profileDir(), target: raticode.profileDir(), items: { cookies: true }, backupDir: path.join(raticode.profileDir(), "..", "backup"), options: { platform: "linux" } });
    assert.deepEqual(summary.copied, ["cookies.sqlite"]);
    const containers = JSON.parse(fs.readFileSync(path.join(raticode.profileDir(), "containers.json"), "utf8"));

    // Plant a sign-in under the very number Rem's container gets next: it must be cleared.
    const target = new DatabaseSync(path.join(raticode.profileDir(), "cookies.sqlite"));
    const columns = target.prepare("PRAGMA table_info(moz_cookies)").all().map((row) => row.name).filter((name) => name !== "id");
    const values = columns.map((name) => (name === "originAttributes" ? `'^userContextId=${containers.lastUserContextId + 1}'` : name === "name" ? "'planted'" : name));
    target.exec(`INSERT INTO moz_cookies (${columns.join(", ")}) SELECT ${values.join(", ")} FROM moz_cookies WHERE name = 'everyday-default'`);
    target.close();

    for (let launch = 1; launch <= 2; launch += 1) {
      const rem = new FirefoxContents({ engine: raticode, kind: "rem" });
      await rem.loadURL(page);
      const remCookies = await rem.evaluateIn("test", "document.cookie");
      rem.close();
      const user = new FirefoxContents({ engine: raticode, kind: "user" });
      await user.loadURL(page);
      const userCookies = await user.evaluateIn("test", "document.cookie");
      user.close();
      assert.equal(remCookies, "", `Rem's tab saw sign-ins on launch ${launch}`);
      assert.equal(userCookies, "everyday-default=secret", `launch ${launch}`);
      await raticode.stopAndWait();
    }
    assert.ok(containers.lastUserContextId >= Math.max(...containerIds), "new containers should be numbered above the imported ones");
  } finally {
    everyday.stop();
    raticode.stop();
    server.close();
  }
});
