// Files in Firefox profiles: Raticode's own Firefox profile, and the user's
// everyday Firefox profiles when the user asks to import from one.
//
// The everyday profiles are only ever read, and only after the user picks
// one. Nothing here writes to them, starts their Firefox, or logs what they
// contain. Databases are copied only while that Firefox is closed, because a
// running Firefox may be part-way through writing them.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");

// What an import can bring over. Passwords and the Firefox account are not
// offered: Firefox's login prompts cannot appear in a windowless Firefox,
// and Firefox fills saved logins in every container, Rem's included.
const IMPORT_ITEMS = {
  settings: { files: [], label: "Settings", needsClosed: false },
  bookmarks: { files: ["places.sqlite", "favicons.sqlite"], label: "Bookmarks and history", needsClosed: true },
  permissions: { files: ["permissions.sqlite", "content-prefs.sqlite"], label: "Site permissions and zoom", needsClosed: true },
  cookies: { files: ["cookies.sqlite"], label: "Sign-ins to websites (cookies)", needsClosed: true },
  extensions: { files: [], label: "Extensions", needsClosed: false },
  extensionData: { files: ["storage-sync-v2.sqlite"], label: "Extension settings and data", needsClosed: true },
};
const SQLITE_SIDE_FILES = ["-wal", "-shm"];
// Rows that belong to one of the user's containers carry "userContextId=<n>"
// in their origin attributes. Copied as they are, they would appear in
// whichever Raticode container is given that number, including Rem's.
const CONTAINER_ROWS = {
  "cookies.sqlite": [["moz_cookies", "originAttributes"]],
  "permissions.sqlite": [["moz_perms", "origin"], ["moz_hosts", "originAttributes"]],
};
const CONTAINER_ID = /userContextId=(\d+)/;
// Firefox keeps a few internal containers, such as extension storage, near
// 2^32. They are never handed out to new containers, so they don't count.
const MAX_CONTAINER_ID = 2 ** 31 - 1;
const containerNumber = (value) => {
  const id = Number(value) || 0;
  return id > 0 && id <= MAX_CONTAINER_ID ? id : 0;
};

// Preferences that describe another installation, an account, telemetry or
// remote control are left behind; they would be wrong or unsafe here.
const SKIPPED_PREF_PREFIXES = [
  "app.normandy.", "app.update.", "browser.laterrun.", "browser.migration.", "browser.newtabpage.activity-stream.",
  "browser.sessionstore.", "browser.startup.homepage_override.", "datareporting.", "devtools.debugger.", "doh-rollout.",
  "extensions.", "identity.fxaccounts.", "idle.", "marionette.", "places.database.", "remote.", "services.",
  "storage.vacuum.", "toolkit.telemetry.", "toolkit.startup.", "xpinstall.",
];
const MAX_MANIFEST_BYTES = 1024 * 1024;

function profileRoots({ platform = process.platform, home = os.homedir(), env = process.env } = {}) {
  if (platform === "darwin") return [path.join(home, "Library", "Application Support", "Firefox")];
  if (platform === "win32") return env.APPDATA ? [path.join(env.APPDATA, "Mozilla", "Firefox")] : [];
  const xdg = env.XDG_CONFIG_HOME && path.isAbsolute(env.XDG_CONFIG_HOME) ? env.XDG_CONFIG_HOME : path.join(home, ".config");
  return [
    path.join(xdg, "mozilla", "firefox"),
    path.join(home, ".mozilla", "firefox"),
    path.join(home, "snap", "firefox", "common", ".mozilla", "firefox"),
    path.join(home, ".var", "app", "org.mozilla.firefox", ".mozilla", "firefox"),
    path.join(home, ".var", "app", "org.mozilla.firefox", "config", "mozilla", "firefox"),
  ];
}

function parseIni(text) {
  const sections = [];
  let current = null;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith(";") || line.startsWith("#")) continue;
    const header = /^\[(.+)\]$/.exec(line);
    if (header) {
      current = { name: header[1], values: {} };
      sections.push(current);
    } else if (current) {
      const at = line.indexOf("=");
      if (at > 0) current.values[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
  }
  return sections;
}

// The user's Firefox profiles, from each installation's profiles.ini.
function findFirefoxProfiles(options = {}) {
  const exclude = options.exclude ? path.resolve(options.exclude) : "";
  const seen = new Set();
  const profiles = [];
  for (const root of profileRoots(options)) {
    let text;
    try { text = fs.readFileSync(path.join(root, "profiles.ini"), "utf8"); } catch { continue; }
    const sections = parseIni(text);
    const installDefaults = new Set(sections.filter((section) => /^Install/i.test(section.name)).map((section) => section.values.Default).filter(Boolean));
    for (const section of sections) {
      if (!/^Profile\d+$/i.test(section.name) || !section.values.Path) continue;
      const relative = section.values.IsRelative !== "0";
      const directory = path.resolve(relative ? path.join(root, section.values.Path) : section.values.Path);
      if (seen.has(directory) || directory === exclude || !isProfileDirectory(directory)) continue;
      seen.add(directory);
      profiles.push({
        name: section.values.Name || path.basename(directory),
        path: directory,
        isDefault: installDefaults.has(section.values.Path) || section.values.Default === "1",
        state: profileLockState(directory, options),
        modifiedAt: modifiedAt(directory),
      });
    }
  }
  return profiles.sort((a, b) => Number(b.isDefault) - Number(a.isDefault) || b.modifiedAt - a.modifiedAt);
}

function isProfileDirectory(directory) {
  try {
    if (!fs.statSync(directory).isDirectory()) return false;
    return ["prefs.js", "times.json", "places.sqlite", "extensions.json"].some((name) => fs.existsSync(path.join(directory, name)));
  } catch {
    return false;
  }
}

function modifiedAt(directory) {
  for (const name of ["prefs.js", "times.json"]) {
    try { return fs.statSync(path.join(directory, name)).mtimeMs; } catch { /* Try the next file. */ }
  }
  return 0;
}

// "running" when that Firefox is open, "closed" when it is not, "unknown"
// when the platform gives no reliable answer. On Linux Firefox's lock is a
// symlink naming its process; macOS and Windows use a file lock instead.
function profileLockState(directory, { platform = process.platform, processAlive = defaultProcessAlive } = {}) {
  if (platform === "linux" || platform === "freebsd") {
    let target;
    try { target = fs.readlinkSync(path.join(directory, "lock")); } catch (error) {
      return error.code === "ENOENT" ? "closed" : "unknown";
    }
    const pid = Number(/:\+?(\d+)$/.exec(target)?.[1]);
    if (!pid) return "unknown";
    return processAlive(pid) ? "running" : "closed";
  }
  const lock = path.join(directory, platform === "darwin" ? ".parentlock" : "parent.lock");
  return fs.existsSync(lock) ? "unknown" : "closed";
}

function defaultProcessAlive(pid) {
  try {
    process.kill(pid, 0);
  } catch (error) {
    return error.code === "EPERM";
  }
  // A recycled process number is unlikely to belong to another Firefox.
  try {
    return /firefox|librewolf|waterfox|floorp/i.test(fs.readFileSync(`/proc/${pid}/comm`, "utf8"));
  } catch {
    return true;
  }
}

// What the chosen profile holds. Names of files and extensions only.
function inspectProfile(directory, options = {}) {
  if (!isProfileDirectory(directory)) throw new Error("That folder is not a Firefox profile.");
  const has = (name) => fs.existsSync(path.join(directory, name));
  return {
    path: directory,
    state: profileLockState(directory, options),
    available: {
      settings: has("prefs.js"),
      bookmarks: has("places.sqlite"),
      permissions: has("permissions.sqlite") || has("content-prefs.sqlite"),
      cookies: has("cookies.sqlite"),
      extensions: profileExtensions(directory).length > 0,
      extensionData: has("storage") || has("browser-extension-data"),
    },
    extensions: profileExtensions(directory).map(({ id, name, version, active }) => ({ id, name, version, active })),
  };
}

// Extensions the user installed in that profile, with their package files.
function profileExtensions(directory) {
  let data;
  try { data = JSON.parse(fs.readFileSync(path.join(directory, "extensions.json"), "utf8")); } catch { return []; }
  return (Array.isArray(data.addons) ? data.addons : []).flatMap((addon) => {
    if (addon?.type !== "extension" || addon.location !== "app-profile" || !addon.id) return [];
    const file = path.join(directory, "extensions", `${addon.id}.xpi`);
    if (!fs.existsSync(file)) return [];
    return [{
      id: String(addon.id).slice(0, 200),
      name: String(addon.defaultLocale?.name || addon.id).slice(0, 200),
      version: String(addon.version || "").slice(0, 50),
      active: addon.active === true,
      file,
    }];
  });
}

// prefs.js and user.js hold one user_pref("name", value); per line.
function readPrefs(file) {
  const prefs = new Map();
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { return prefs; }
  const pattern = /^\s*user_pref\(\s*("(?:[^"\\]|\\.)*")\s*,\s*("(?:[^"\\]|\\.)*"|-?\d+|true|false)\s*\);/gm;
  for (const match of text.matchAll(pattern)) {
    try { prefs.set(JSON.parse(match[1]), JSON.parse(match[2])); } catch { /* Skip a line Firefox would not accept either. */ }
  }
  return prefs;
}

function prefLine(name, value) {
  return `user_pref(${JSON.stringify(name)}, ${JSON.stringify(value)});`;
}

function writePrefs(file, prefs) {
  const lines = [...prefs].sort(([a], [b]) => a.localeCompare(b)).map(([name, value]) => prefLine(name, value));
  fs.writeFileSync(file, `// Mozilla User Preferences\n// Written by Raticode while Firefox was closed.\n\n${lines.join("\n")}\n`, { mode: 0o600 });
}

function importablePrefs(prefs) {
  return new Map([...prefs].filter(([name]) => !SKIPPED_PREF_PREFIXES.some((prefix) => name.startsWith(prefix))));
}

// Extension storage belongs to a per-profile ID that Firefox maps from the
// extension's ID. Keeping the mapping keeps the extension's saved settings.
function extensionUuids(prefs) {
  try {
    const value = JSON.parse(String(prefs.get("extensions.webextensions.uuids") || "{}"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

// BiDi names the containers it creates "remote-<uuid>", and gives them new
// IDs each session, so Raticode cannot find Rem's container again. Drop
// those before Firefox starts; containers the user or an extension made stay.
function removeAutomationContainers(profileDir) {
  const file = path.join(profileDir, "containers.json");
  let data;
  try { data = JSON.parse(fs.readFileSync(file, "utf8")); } catch { return 0; }
  if (!Array.isArray(data.identities)) return 0;
  const kept = data.identities.filter((identity) => !/^remote-[0-9a-f-]{36}$/i.test(String(identity?.name || "")));
  const removed = data.identities.length - kept.length;
  if (removed) fs.writeFileSync(file, JSON.stringify({ ...data, identities: kept }), { mode: 0o600 });
  return removed;
}

// Deletes the container rows from a copied database and returns the highest
// container number it held. Throws when the file is not a readable database.
function stripContainerRows(file, tables) {
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(file);
  let highest = 0;
  let removed = 0;
  try {
    const existing = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map((row) => row.name));
    for (const [table, column] of tables) {
      if (!existing.has(table)) continue;
      for (const row of db.prepare(`SELECT ${column} AS value FROM ${table} WHERE ${column} LIKE '%userContextId=%'`).all()) {
        highest = Math.max(highest, containerNumber(CONTAINER_ID.exec(String(row.value))?.[1]));
      }
      removed += Number(db.prepare(`DELETE FROM ${table} WHERE ${column} LIKE '%userContextId=%'`).run().changes) || 0;
    }
    // Fold the write-ahead log in and drop the deleted rows from the file.
    db.exec("PRAGMA wal_checkpoint(TRUNCATE)");
    db.exec("VACUUM");
  } finally {
    db.close();
  }
  for (const suffix of SQLITE_SIDE_FILES) {
    try { if (fs.statSync(file + suffix).size === 0) fs.rmSync(file + suffix, { force: true }); } catch { /* Not there. */ }
  }
  return { highest, removed };
}

function readContainers(profileDir) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(profileDir, "containers.json"), "utf8"));
    return data && Array.isArray(data.identities) ? data : null;
  } catch {
    return null;
  }
}

// New containers in Raticode's Firefox, Rem's among them, get numbers above
// every container number the import could have brought in.
function reserveContainerIds(profileDir, atLeast) {
  const file = path.join(profileDir, "containers.json");
  const data = readContainers(profileDir) || { version: 5, lastUserContextId: 0, identities: [] };
  const current = Number(data.lastUserContextId) || 0;
  if (current >= atLeast) return current;
  fs.writeFileSync(file, JSON.stringify({ ...data, lastUserContextId: atLeast }), { mode: 0o600 });
  return atLeast;
}

function copyIfPresent(source, target) {
  if (!fs.existsSync(source)) return false;
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  fs.cpSync(source, target, { recursive: true, force: true, errorOnExist: false, dereference: false });
  return true;
}

// Moves the files an import will replace out of Raticode's profile first.
function backUp(targetDir, names, backupDir) {
  let moved = 0;
  for (const name of names) {
    const source = path.join(targetDir, name);
    if (!fs.existsSync(source)) continue;
    const destination = path.join(backupDir, name);
    fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 });
    fs.renameSync(source, destination);
    moved += 1;
  }
  return moved;
}

// Copies the chosen items from an everyday profile into Raticode's profile.
// Raticode's Firefox must be stopped first. Extensions themselves are then
// installed through Firefox, which checks their signatures.
function importProfile({ source, target, items = {}, extensionIds = [], backupDir, options = {} }) {
  // Where the lock can't be read, the user's word that Firefox is closed
  // stands in; a Firefox seen running is never overridden.
  const detected = profileLockState(source, options);
  const state = detected === "unknown" && options.assumeClosed === true ? "closed" : detected;
  const blocked = Object.entries(items)
    .filter(([key, wanted]) => wanted && IMPORT_ITEMS[key]?.needsClosed && state !== "closed")
    .map(([key]) => IMPORT_ITEMS[key].label.toLowerCase());
  if (blocked.length) {
    throw new Error(`Close Firefox before importing ${blocked.join(", ")}: its files may be in use.${state === "unknown" ? " Raticode cannot tell whether Firefox is closed on this system." : ""}`);
  }
  const sourcePrefs = readPrefs(path.join(source, "prefs.js"));
  const uuids = extensionUuids(sourcePrefs);
  const available = profileExtensions(source);
  const extensions = items.extensions ? available.filter((extension) => extensionIds.includes(extension.id)) : [];

  // Files to bring over, relative to the profile folder.
  const files = [];
  for (const [key, wanted] of Object.entries(items)) {
    if (!wanted || !IMPORT_ITEMS[key]) continue;
    if (key === "extensionData" && !extensions.length) continue;
    for (const name of IMPORT_ITEMS[key].files) files.push(name, ...SQLITE_SIDE_FILES.map((suffix) => name + suffix));
  }
  if (items.extensionData) {
    const storageRoot = path.join(source, "storage", "default");
    const folders = fs.existsSync(storageRoot) ? fs.readdirSync(storageRoot, { withFileTypes: true }).filter((entry) => entry.isDirectory()) : [];
    for (const { id } of extensions) {
      if (uuids[id]) for (const entry of folders) if (entry.name.startsWith(`moz-extension+++${uuids[id]}`)) files.push(path.join("storage", "default", entry.name));
      if (fs.existsSync(path.join(source, "browser-extension-data", id))) files.push(path.join("browser-extension-data", id));
    }
  }

  // Raticode's own copies of those files move to a backup folder first.
  fs.mkdirSync(target, { recursive: true, mode: 0o700 });
  fs.mkdirSync(backupDir, { recursive: true, mode: 0o700 });
  const targetPrefsFile = path.join(target, "prefs.js");
  backUp(target, files, backupDir);
  if (fs.existsSync(targetPrefsFile)) fs.copyFileSync(targetPrefsFile, path.join(backupDir, "prefs.js"));

  const copied = [];
  const skipped = [];
  for (const name of files) {
    if (copyIfPresent(path.join(source, name), path.join(target, name)) && !SQLITE_SIDE_FILES.some((suffix) => name.endsWith(suffix))) copied.push(name);
  }
  // Only default-container sign-ins and permissions stay; the user's other
  // containers must never reach a Raticode container such as Rem's.
  const sourceContainers = readContainers(source);
  let highestId = Math.max(containerNumber(sourceContainers?.lastUserContextId), ...(sourceContainers?.identities || []).map((identity) => containerNumber(identity?.userContextId)));
  for (const [name, tables] of Object.entries(CONTAINER_ROWS)) {
    if (!copied.includes(name)) continue;
    const file = path.join(target, name);
    try {
      highestId = Math.max(highestId, stripContainerRows(file, tables).highest);
    } catch {
      // A database Raticode can't filter is not kept; Raticode's own copy returns.
      for (const suffix of ["", ...SQLITE_SIDE_FILES]) {
        fs.rmSync(file + suffix, { force: true });
        if (fs.existsSync(path.join(backupDir, name + suffix))) fs.copyFileSync(path.join(backupDir, name + suffix), file + suffix);
      }
      copied.splice(copied.indexOf(name), 1);
      skipped.push(IMPORT_ITEMS[name === "cookies.sqlite" ? "cookies" : "permissions"].label);
    }
  }
  if (highestId) reserveContainerIds(target, highestId);
  const targetPrefs = readPrefs(targetPrefsFile);
  let prefsChanged = false;
  if (items.settings && sourcePrefs.size) {
    for (const [name, value] of importablePrefs(sourcePrefs)) targetPrefs.set(name, value);
    copied.push("prefs.js");
    prefsChanged = true;
  }
  if (extensions.length) {
    // Firefox reuses a mapped storage ID when it installs the extension,
    // so the extension finds the settings imported with it.
    const targetUuids = extensionUuids(targetPrefs);
    for (const { id } of extensions) if (uuids[id]) targetUuids[id] = uuids[id];
    targetPrefs.set("extensions.webextensions.uuids", JSON.stringify(targetUuids));
    prefsChanged = true;
  }
  if (prefsChanged) writePrefs(targetPrefsFile, targetPrefs);
  return { copied, skipped, extensions, backupDir, sourceState: state };
}

// Reads one file from an extension package (a zip) without unpacking it.
function readZipEntry(file, wanted) {
  const data = fs.readFileSync(file);
  let end = -1;
  for (let at = data.length - 22; at >= Math.max(0, data.length - 65557); at -= 1) {
    if (data.readUInt32LE(at) === 0x06054b50) { end = at; break; }
  }
  if (end < 0) throw new Error("That file is not an extension package.");
  const count = data.readUInt16LE(end + 10);
  let at = data.readUInt32LE(end + 16);
  for (let index = 0; index < count && at + 46 <= data.length; index += 1) {
    if (data.readUInt32LE(at) !== 0x02014b50) break;
    const method = data.readUInt16LE(at + 10);
    const compressed = data.readUInt32LE(at + 20);
    const size = data.readUInt32LE(at + 24);
    const nameLength = data.readUInt16LE(at + 28);
    const extraLength = data.readUInt16LE(at + 30);
    const commentLength = data.readUInt16LE(at + 32);
    const local = data.readUInt32LE(at + 42);
    const name = data.toString("utf8", at + 46, at + 46 + nameLength);
    if (name === wanted) {
      if (size > MAX_MANIFEST_BYTES) throw new Error(`${wanted} is too large.`);
      const start = local + 30 + data.readUInt16LE(local + 26) + data.readUInt16LE(local + 28);
      const body = data.subarray(start, start + compressed);
      return method === 0 ? Buffer.from(body) : zlib.inflateRawSync(body, { maxOutputLength: MAX_MANIFEST_BYTES });
    }
    at += 46 + nameLength + extraLength + commentLength;
  }
  return null;
}

// The name, ID and permissions an extension package declares.
function readExtensionManifest(file) {
  const raw = readZipEntry(file, "manifest.json");
  if (!raw) throw new Error("That file is not an extension package (no manifest.json).");
  const manifest = JSON.parse(raw.toString("utf8").replace(/^\uFEFF/, ""));
  let name = String(manifest.name || "");
  const message = /^__MSG_(.+)__$/.exec(name);
  if (message) {
    const locale = String(manifest.default_locale || "en");
    try {
      const messages = JSON.parse(readZipEntry(file, `_locales/${locale}/messages.json`)?.toString("utf8").replace(/^\uFEFF/, "") || "{}");
      const entry = Object.entries(messages).find(([key]) => key.toLowerCase() === message[1].toLowerCase());
      name = entry?.[1]?.message || name;
    } catch { /* Keep the placeholder name. */ }
  }
  const strings = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);
  return {
    id: String(manifest.browser_specific_settings?.gecko?.id || manifest.applications?.gecko?.id || ""),
    name: name.slice(0, 200),
    version: String(manifest.version || "").slice(0, 50),
    manifestVersion: Number(manifest.manifest_version) || 0,
    permissions: [...new Set([...strings(manifest.permissions), ...strings(manifest.host_permissions)])].slice(0, 100),
  };
}

module.exports = {
  CONTAINER_ROWS,
  IMPORT_ITEMS,
  reserveContainerIds,
  stripContainerRows,
  SKIPPED_PREF_PREFIXES,
  extensionUuids,
  findFirefoxProfiles,
  importProfile,
  importablePrefs,
  inspectProfile,
  parseIni,
  prefLine,
  profileExtensions,
  profileLockState,
  profileRoots,
  readExtensionManifest,
  readPrefs,
  removeAutomationContainers,
  writePrefs,
};
