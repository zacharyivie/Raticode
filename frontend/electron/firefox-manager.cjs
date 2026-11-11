// Firefox settings, extensions and importing, for Settings > Browser.
//
// Firefox's own settings and add-on pages run with full browser privileges,
// and Firefox refuses to show them, or extension pages, to remote control
// unless it is started with system access. That would let anything that
// reaches Firefox's control port run programs as the user, so Raticode
// offers these controls itself instead:
// - settings are written to the profile's user.js and apply when Firefox restarts;
// - extensions come from Firefox Add-ons (or a file) and are installed by
//   Firefox, which checks Mozilla's signature;
// - an everyday Firefox profile can be imported from, only when the user
//   picks it, and only by reading it.
// Every install, removal and import is confirmed in a Raticode dialog.
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { PROFILE_PREFS, prefLocked } = require("./firefox-engine.cjs");
const {
  IMPORT_ITEMS,
  findFirefoxProfiles,
  importProfile,
  inspectProfile,
  readExtensionManifest,
  readPrefs,
} = require("./firefox-profile.cjs");
const { addonSlugFromUrl, createAddonCatalog, versionCompatible } = require("./firefox-addons.cjs");

// Common Firefox settings, with the values Firefox uses when none is set.
const FIREFOX_SETTINGS = [
  { pref: "dom.security.https_only_mode", type: "boolean", default: false, group: "Privacy and security", label: "HTTPS-Only Mode", description: "Load every site over HTTPS, and ask before opening one that doesn't support it." },
  { pref: "privacy.trackingprotection.enabled", type: "boolean", default: false, group: "Privacy and security", label: "Block trackers in every window", description: "Firefox blocks known tracking content; by default it does so only in private windows." },
  { pref: "network.cookie.cookieBehavior", type: "choice", default: 5, group: "Privacy and security", label: "Cookies", options: [[5, "Keep each site's cookies separate (default)"], [4, "Block cross-site tracking cookies"], [1, "Block all third-party cookies"], [0, "Allow all cookies"]] },
  { pref: "privacy.globalprivacycontrol.enabled", type: "boolean", default: false, group: "Privacy and security", label: "Ask sites not to sell or share my data", description: "Sends the Global Privacy Control signal." },
  { pref: "browser.safebrowsing.downloads.remote.block_potentially_unwanted", type: "boolean", default: true, group: "Privacy and security", label: "Warn about unwanted software in downloads" },
  { pref: "dom.disable_open_during_load", type: "boolean", default: true, group: "Pages", label: "Block pop-up windows" },
  { pref: "media.autoplay.default", type: "choice", default: 1, group: "Pages", label: "Autoplay", options: [[1, "Block audio and video with sound (default)"], [5, "Block all audio and video"], [0, "Allow audio and video"]] },
  { pref: "layout.css.prefers-color-scheme.content-override", type: "choice", default: 2, group: "Pages", label: "Website appearance", options: [[2, "Automatic (default)"], [0, "Dark"], [1, "Light"]] },
  { pref: "intl.accept_languages", type: "string", default: "", group: "Pages", label: "Languages for websites", description: "In order of preference, such as \"en-US, en\". Leave empty for Firefox's default." },
  { pref: "font.size.variable.x-western", type: "integer", default: 16, min: 6, max: 72, group: "Pages", label: "Default font size" },
  { pref: "font.minimum-size.x-western", type: "integer", default: 0, min: 0, max: 72, group: "Pages", label: "Minimum font size", description: "0 means no minimum." },
  { pref: "general.smoothScroll", type: "boolean", default: true, group: "Pages", label: "Smooth scrolling" },
  { pref: "network.trr.mode", type: "choice", default: 0, group: "Network", label: "DNS over HTTPS", options: [[0, "Firefox decides (default)"], [2, "On, with fallback to your system's DNS"], [3, "On only, never fall back"], [5, "Off"]] },
  { pref: "network.trr.uri", type: "string", default: "", group: "Network", label: "DNS over HTTPS provider", description: "A provider address. Leave empty for Firefox's default provider." },
  { pref: "network.proxy.type", type: "choice", default: 5, group: "Network", label: "Proxy", options: [[5, "Use the system's proxy settings (default)"], [0, "No proxy"], [4, "Detect automatically"], [2, "Use a configuration address"]] },
  { pref: "network.proxy.autoconfig_url", type: "string", default: "", group: "Network", label: "Proxy configuration address" },
];

// Why Raticode keeps each of its own settings fixed.
const REQUIRED_REASONS = [
  [/^permissions\.default\./, "Firefox cannot show its permission prompt without a window, so camera, microphone, location and notification requests are declined."],
  [/^signon\./, "Firefox's password prompts cannot appear, and it would fill saved passwords in Rem's tabs too."],
  [/^extensions\.(enabledScopes|installDistroAddons|update)/, "Only extensions you install through Raticode load."],
  [/^remote\.|^dom\.file\.createInChild/, "Keeps Firefox's normal protections while Raticode controls it."],
  [/./, "Firefox runs without a window inside Raticode."],
];

// Plain-language meanings of common extension permissions.
const PERMISSION_TEXT = {
  "<all_urls>": "Read and change your data on all websites",
  "*://*/*": "Read and change your data on all websites",
  "http://*/*": "Read and change your data on all websites",
  "https://*/*": "Read and change your data on all websites",
  tabs: "Access your Firefox tabs",
  history: "Read and change your browsing history",
  bookmarks: "Read and change your bookmarks",
  cookies: "Read and change cookies",
  downloads: "Download files and manage downloads",
  clipboardRead: "Read what you copy",
  clipboardWrite: "Put data on your clipboard",
  webRequest: "Watch network requests",
  webRequestBlocking: "Block or change network requests",
  proxy: "Control Firefox's proxy settings",
  privacy: "Read and change privacy settings",
  management: "Manage other extensions",
  nativeMessaging: "Talk to programs outside Firefox",
  geolocation: "Know your location",
  browsingData: "Clear browsing data",
  webNavigation: "See where you navigate",
  dns: "Look up website addresses",
};

const FIREFOX_SYNC_URL = "https://accounts.firefox.com/?context=fx_desktop_v3&entrypoint=raticode&action=email&service=sync";
const MAX_LISTED = 12;

function permissionLines(permissions) {
  const lines = [...new Set(permissions.map((item) => PERMISSION_TEXT[item] || (/^[a-z*]+:\/\//.test(item) ? `Read and change your data on ${item.replace(/^\*:\/\/|^https?:\/\//, "").replace(/\/\*$/, "")}` : "")).filter(Boolean))];
  const shown = lines.slice(0, MAX_LISTED).map((line) => `• ${line}`);
  if (lines.length > MAX_LISTED) shown.push(`• and ${lines.length - MAX_LISTED} more`);
  return shown.length ? shown.join("\n") : "• No special permissions";
}

// Firefox's install errors, in plain words.
function installFailure(error) {
  const message = String(error?.message || error);
  if (/SIGNEDSTATE|signature|must be signed/i.test(message)) return "it is not signed by Mozilla, and release Firefox installs only signed extensions";
  if (/CORRUPT/i.test(message)) return "the file is damaged or is not a Firefox extension";
  if (/INCOMPATIBLE|not compatible/i.test(message)) return "it does not work with this version of Firefox";
  return message.replace(/^Could not install Add-on: (?:ERROR_[A-Z_]+: )?/, "").replace(/\.+$/, "").slice(0, 200);
}

function requiredReason(name) {
  return REQUIRED_REASONS.find(([pattern]) => pattern.test(name))[1];
}

function settingValue(setting, value) {
  if (value === null || value === undefined) return undefined;
  if (setting.type === "boolean") return value === true || value === "true";
  if (setting.type === "integer" || setting.type === "choice") {
    const number = Number(value);
    if (!Number.isInteger(number)) throw new Error(`${setting.label} needs a whole number.`);
    if (setting.type === "choice" && !setting.options.some(([option]) => option === number)) throw new Error(`${setting.label} has no option ${number}.`);
    if (setting.min !== undefined && (number < setting.min || number > setting.max)) throw new Error(`${setting.label} must be between ${setting.min} and ${setting.max}.`);
    return number;
  }
  return String(value).slice(0, 4096);
}

function createFirefoxManager({
  engine,
  dataDir,
  dialog,
  getMainWindow = () => null,
  log = () => {},
  hooks = {},
  catalog = createAddonCatalog(),
  platform = process.platform,
  home = os.homedir(),
  env = process.env,
}) {
  const allowedProfiles = new Set();
  let busy = null;

  async function ask(options) {
    const owner = getMainWindow();
    const result = owner && !owner.isDestroyed() ? await dialog.showMessageBox(owner, options) : await dialog.showMessageBox(options);
    return result?.response ?? -1;
  }

  async function choose(options) {
    const owner = getMainWindow();
    const result = owner && !owner.isDestroyed() ? await dialog.showOpenDialog(owner, options) : await dialog.showOpenDialog(options);
    return result.canceled ? [] : result.filePaths || [];
  }

  // One install, removal, import or restart at a time.
  async function exclusive(task) {
    if (busy) throw new Error("Raticode is still changing Firefox. Try again in a moment.");
    busy = task();
    try { return await busy; } finally { busy = null; }
  }

  // ---------------------------------------------------------------------------
  // Settings

  function settings() {
    const chosen = engine.readUserPrefs();
    const written = readPrefs(path.join(engine.profileDir(), "prefs.js"));
    return {
      firefox: engine.status(),
      settings: FIREFOX_SETTINGS.map((setting) => {
        const source = Object.hasOwn(chosen, setting.pref) ? "raticode" : written.has(setting.pref) ? "firefox" : "default";
        const value = source === "raticode" ? chosen[setting.pref] : source === "firefox" ? written.get(setting.pref) : setting.default;
        return { ...setting, value, source };
      }),
      custom: Object.entries(chosen).filter(([name]) => !FIREFOX_SETTINGS.some((setting) => setting.pref === name)).map(([name, value]) => ({ name, value })),
      required: Object.entries(PROFILE_PREFS).map(([name, value]) => ({ name, value, reason: requiredReason(name) })),
    };
  }

  function saveSettings(changes = {}) {
    const next = { ...engine.readUserPrefs() };
    for (const [name, raw] of Object.entries(changes && typeof changes === "object" ? changes : {}).slice(0, 200)) {
      const setting = FIREFOX_SETTINGS.find((item) => item.pref === name);
      if (raw === null) { delete next[name]; continue; }
      if (prefLocked(name)) throw new Error(`Raticode keeps ${name} fixed: ${requiredReason(name)}`);
      next[name] = setting ? settingValue(setting, raw) : raw;
    }
    const result = engine.saveUserPrefs(next);
    if (result.rejected.length) throw new Error(result.rejected.map((item) => `${item.name}: ${item.reason}`).join(" "));
    return { ...settings(), restartNeeded: result.restartNeeded };
  }

  async function restart() {
    return exclusive(async () => {
      await hooks.restartFirefox?.();
      return settings();
    });
  }

  // ---------------------------------------------------------------------------
  // Extensions

  async function extensions() {
    return { extensions: await engine.listExtensions(), firefox: engine.status() };
  }

  async function confirmInstall({ name, authors = [], version = "", permissions = [] }) {
    const by = authors.length ? `By ${authors.join(", ")}. ` : "";
    const response = await ask({
      type: "question",
      buttons: ["Add extension", "Cancel"],
      defaultId: 1,
      cancelId: 1,
      message: `Add “${name}” to Raticode's Firefox?`,
      detail: `${by}${version ? `Version ${version}.` : ""}\n\nIt will be able to:\n${permissionLines(permissions)}\n\nExtensions run in every Firefox tab in Raticode, including the tabs Rem uses, and can read and change those pages. Add only extensions you trust.`,
    });
    return response === 0;
  }

  function tempDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), "raticode-firefox-addon-"));
  }

  async function installFromCatalog(key) {
    return exclusive(async () => {
      const addon = await catalog.details(key);
      await engine.ensure();
      const version = engine.status().version;
      if (!versionCompatible(version, addon.compatibility)) {
        throw new Error(`${addon.name} ${addon.version} works with Firefox ${addon.compatibility.min || "?"} to ${addon.compatibility.max || "?"}, not Firefox ${version}.`);
      }
      if (!(await confirmInstall(addon))) return { cancelled: true, ...(await extensions()) };
      const directory = tempDir();
      try {
        const file = path.join(directory, "extension.xpi");
        await catalog.download(addon.file, file);
        const id = await engine.installExtension(file);
        log("info", `Installed the Firefox extension ${addon.name} ${addon.version}.`);
        return { installed: { id, name: addon.name }, ...(await extensions()) };
      } finally {
        fs.rmSync(directory, { recursive: true, force: true });
      }
    });
  }

  async function installFromFile() {
    const [file] = await choose({ title: "Choose a Firefox extension", properties: ["openFile"], filters: [{ name: "Firefox extensions", extensions: ["xpi"] }] });
    if (!file) return { cancelled: true, ...(await extensions()) };
    return exclusive(async () => {
      const manifest = readExtensionManifest(file);
      if (!(await confirmInstall({ name: manifest.name || path.basename(file), version: manifest.version, permissions: manifest.permissions }))) return { cancelled: true, ...(await extensions()) };
      // Firefox reads a copy, so the chosen file is never held open or changed.
      const directory = tempDir();
      try {
        const copy = path.join(directory, "extension.xpi");
        fs.copyFileSync(file, copy);
        const id = await engine.installExtension(copy).catch((error) => {
          throw new Error(`Firefox did not add ${manifest.name || "this extension"}: ${installFailure(error)}.`);
        });
        log("info", `Installed the Firefox extension ${manifest.name || id} from a file.`);
        return { installed: { id, name: manifest.name }, ...(await extensions()) };
      } finally {
        fs.rmSync(directory, { recursive: true, force: true });
      }
    });
  }

  async function removeExtension(id) {
    const current = (await engine.listExtensions()).find((item) => item.id === id);
    if (!current) throw new Error("That extension is not installed in Raticode's Firefox.");
    const response = await ask({ type: "question", buttons: ["Remove", "Cancel"], defaultId: 1, cancelId: 1, message: `Remove “${current.name}” from Raticode's Firefox?`, detail: "Its settings and data in Raticode's Firefox are deleted too." });
    if (response !== 0) return { cancelled: true, ...(await extensions()) };
    return exclusive(async () => {
      await engine.uninstallExtension(id);
      log("info", `Removed the Firefox extension ${current.name}.`);
      return extensions();
    });
  }

  // ---------------------------------------------------------------------------
  // Importing from an everyday Firefox profile

  function profiles() {
    const found = findFirefoxProfiles({ platform, home, env, exclude: engine.profileDir() });
    for (const profile of found) allowedProfiles.add(profile.path);
    return { profiles: found };
  }

  async function chooseProfile() {
    const [folder] = await choose({ title: "Choose a Firefox profile folder", properties: ["openDirectory"] });
    if (!folder) return { cancelled: true };
    const resolved = path.resolve(folder);
    if (resolved === path.resolve(engine.profileDir())) throw new Error("That is Raticode's own Firefox profile.");
    const inspected = inspectProfile(resolved, { platform });
    allowedProfiles.add(resolved);
    return { profile: inspected };
  }

  function knownProfile(value) {
    const resolved = path.resolve(String(value || ""));
    if (!allowedProfiles.has(resolved)) throw new Error("Choose the profile from the list, or with Choose folder, first.");
    return resolved;
  }

  function inspect(value) {
    return { profile: inspectProfile(knownProfile(value), { platform }) };
  }

  async function runImport(options = {}) {
    const source = knownProfile(options.path);
    const items = Object.fromEntries(Object.keys(IMPORT_ITEMS).map((key) => [key, options.items?.[key] === true]));
    const extensionIds = Array.isArray(options.extensionIds) ? options.extensionIds.filter((id) => typeof id === "string").slice(0, 200) : [];
    const chosen = Object.entries(items).filter(([, wanted]) => wanted).map(([key]) => IMPORT_ITEMS[key].label);
    if (!chosen.length) throw new Error("Choose what to import.");
    const response = await ask({
      type: "question",
      buttons: ["Import", "Cancel"],
      defaultId: 1,
      cancelId: 1,
      message: "Import into Raticode's Firefox?",
      detail: `From ${path.basename(source)}: ${chosen.join(", ")}.\n\nRaticode only reads that profile; it is not changed. Raticode's Firefox restarts and its tabs reload. Anything the import replaces in Raticode's Firefox is kept in a backup folder.${items.cookies ? "\n\nWebsite sign-ins from that Firefox's normal tabs come along, so Raticode's Firefox tabs will be signed in there too. Sign-ins from its other containers are left out. Rem's tabs stay signed out unless you share a site with Rem." : ""}${items.permissions ? "\n\nSite permissions and zoom levels apply in every Firefox tab in Raticode, Rem's included." : ""}`,
    });
    if (response !== 0) return { cancelled: true };
    return exclusive(async () => {
      await hooks.stopFirefox?.();
      await engine.stopAndWait();
      const backupDir = path.join(dataDir, "firefox-profile-backups", `${new Date().toISOString().replace(/[:.]/g, "-")}-${crypto.randomBytes(3).toString("hex")}`);
      let summary;
      try {
        summary = importProfile({ source, target: engine.profileDir(), items, extensionIds, backupDir, options: { platform, assumeClosed: options.confirmedClosed === true } });
      } finally {
        await hooks.reviveFirefoxTabs?.();
      }
      const installed = [];
      const failed = [];
      for (const extension of summary.extensions) {
        const directory = tempDir();
        try {
          const copy = path.join(directory, "extension.xpi");
          fs.copyFileSync(extension.file, copy);
          await engine.installExtension(copy);
          installed.push(extension.name);
        } catch (error) {
          failed.push({ name: extension.name, reason: installFailure(error) });
        } finally {
          fs.rmSync(directory, { recursive: true, force: true });
        }
      }
      log("info", `Imported into Raticode's Firefox: ${summary.copied.length} item(s), ${installed.length} extension(s), ${failed.length} extension(s) not installed.`);
      return { imported: { copied: summary.copied, skipped: summary.skipped, installed, failed, backupDir } };
    });
  }

  async function resetProfile() {
    const response = await ask({
      type: "warning",
      buttons: ["Reset", "Cancel"],
      defaultId: 1,
      cancelId: 1,
      message: "Reset Raticode's Firefox?",
      detail: "Its settings, extensions, sign-ins, history and bookmarks move to a backup folder, and Firefox starts fresh. Your everyday Firefox is not affected.",
    });
    if (response !== 0) return { cancelled: true };
    return exclusive(async () => {
      await hooks.stopFirefox?.();
      await engine.stopAndWait();
      const profile = engine.profileDir();
      const backupDir = path.join(dataDir, "firefox-profile-backups", `${new Date().toISOString().replace(/[:.]/g, "-")}-reset`);
      fs.mkdirSync(path.dirname(backupDir), { recursive: true, mode: 0o700 });
      if (fs.existsSync(profile)) fs.renameSync(profile, backupDir);
      await hooks.reviveFirefoxTabs?.();
      log("info", "Reset Raticode's Firefox profile.");
      return { reset: { backupDir } };
    });
  }

  // ---------------------------------------------------------------------------

  async function handle(action, options = {}) {
    switch (action) {
      case "firefox-settings": return settings();
      case "firefox-settings-save": return saveSettings(options.prefs);
      case "firefox-restart": return restart();
      case "firefox-extensions": return extensions();
      case "firefox-addon-search": return { results: await catalog.search(options.text) };
      case "firefox-addon-install": return installFromCatalog(String(options.key || ""));
      case "firefox-addon-install-file": return installFromFile();
      case "firefox-addon-remove": return removeExtension(String(options.id || ""));
      case "firefox-profiles": return profiles();
      case "firefox-profile-choose": return chooseProfile();
      case "firefox-profile-inspect": return inspect(options.path);
      case "firefox-import": return runImport(options);
      case "firefox-reset": return resetProfile();
      case "firefox-sync":
        hooks.openTab?.(FIREFOX_SYNC_URL);
        return { opened: true };
      default:
        throw new Error(`Unknown Firefox action: ${action}`);
    }
  }

  return { handle, installFromCatalog, settings };
}

module.exports = { FIREFOX_SETTINGS, FIREFOX_SYNC_URL, addonSlugFromUrl, createFirefoxManager, installFailure, permissionLines };
