// Raticode as the computer's web browser. Packaging registers Raticode with the
// operating system (see package.json "build", build-resources/installer.nsh and
// packaging/aur/raticode-bin/raticode.desktop); this file covers the running app:
//
// - Links and web pages that other apps hand over arrive as command-line
//   arguments (Linux and Windows, forwarded to the running Raticode through the
//   single-instance lock) or as macOS open-url/open-file events.
//   externalOpensFromArgv() keeps only web addresses and existing web-page files.
// - status() says whether Raticode is the default browser, and openSettings()
//   opens the system's own default-apps settings. Windows and macOS only let the
//   user make that change there. Linux has no such rule, so makeDefault() runs
//   xdg-settings, but only after the user confirms a dialog the main process
//   shows itself. Nothing here changes a default without the user's say.
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { fileURLToPath } = require("node:url");
const { isNetworkPath } = require("./security.cjs");

const MAX_URL_LENGTH = 8192;
const MAX_ITEMS = 20;
// Web pages saved as files. Other file types keep opening in their own apps.
const PAGE_EXTENSIONS = new Set([".html", ".htm", ".xhtml", ".shtml"]);
const MAX_DESKTOP_FILE_BYTES = 64 * 1024;
const LINUX_KNOWN_ENTRIES = ["gofer-flow.desktop", "raticode.desktop"];
// The registered-application name build-resources/installer.nsh writes.
const WINDOWS_APP_NAME = "Raticode";
const WINDOWS_URL_PROGID = "RaticodeURL";
const COMMAND_TIMEOUT_MS = 10 * 1000;

function hasControlCharacter(text) {
  for (const character of text) {
    const code = character.codePointAt(0);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

// One argument handed to Raticode, as a web address or a web-page file, or null.
function externalOpenFromArg(value, { cwd = process.cwd(), fileSystem = fs } = {}) {
  const text = typeof value === "string" ? value : "";
  if (!text || text.length > MAX_URL_LENGTH || text.startsWith("-") || hasControlCharacter(text)) return null;
  if (/^https?:/i.test(text)) {
    let url;
    try { url = new URL(text); } catch { return null; }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!url.hostname) return null;
    return { kind: "url", url: url.toString() };
  }
  // A file on another computer is refused before its path is resolved or read.
  if (isNetworkPath(text)) return null;
  let filePath;
  if (/^file:/i.test(text)) {
    try { filePath = fileURLToPath(new URL(text)); } catch { return null; }
  } else if (/^[a-z][a-z\d+.-]*:\/\//i.test(text)) {
    // Any other scheme (javascript:, raticode:, ftp:) is refused.
    return null;
  } else {
    filePath = path.resolve(cwd || process.cwd(), text);
  }
  if (isNetworkPath(filePath)) return null;
  if (!PAGE_EXTENSIONS.has(path.extname(filePath).toLowerCase())) return null;
  try {
    const stat = fileSystem.statSync(filePath);
    if (!stat.isFile()) return null;
  } catch { return null; }
  return { kind: "file", path: filePath };
}

// The links and files in a command line. Skips Electron's own arguments: the
// executable, the app folder in development, switches and "--".
function externalOpensFromArgv(argv = [], { cwd, defaultApp = Boolean(process.defaultApp), fileSystem = fs } = {}) {
  const args = Array.isArray(argv) ? argv.slice(defaultApp ? 2 : 1) : [];
  const items = [];
  for (const arg of args) {
    const item = externalOpenFromArg(arg, { cwd, fileSystem });
    if (item && !items.some((existing) => sameItem(existing, item))) items.push(item);
    if (items.length >= MAX_ITEMS) break;
  }
  return items;
}

function sameItem(left, right) {
  return left.kind === right.kind && (left.url || left.path) === (right.url || right.path);
}

// Links waiting for the window. The window takes them once it has loaded
// (`take`), and later ones go straight to it.
function createExternalOpenQueue({ limit = MAX_ITEMS } = {}) {
  let items = [];
  let ready = false;
  return {
    add(newItems = []) {
      const added = newItems.filter(Boolean);
      if (!added.length) return [];
      if (ready) return added;
      items = [...items, ...added].slice(-limit);
      return [];
    },
    take() {
      ready = true;
      const taken = items;
      items = [];
      return taken;
    },
    // A reloaded window asks again.
    reset() { ready = false; },
    get pending() { return items.length; },
    get ready() { return ready; },
  };
}

function parseDesktopEntry(text) {
  const entry = {};
  let inMain = false;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("[")) { inMain = line === "[Desktop Entry]"; continue; }
    if (!inMain) continue;
    const index = line.indexOf("=");
    if (index < 1) continue;
    const key = line.slice(0, index).trim();
    if (!(key in entry)) entry[key] = line.slice(index + 1).trim();
  }
  return entry;
}

// The first word of an Exec line, without quotes.
function execProgram(exec = "") {
  const match = /^\s*(?:"((?:[^"\\]|\\.)*)"|(\S+))/.exec(String(exec));
  if (!match) return "";
  return (match[1] ?? match[2] ?? "").replace(/\\(.)/g, "$1");
}

function linuxApplicationDirs(env = process.env, homedir = os.homedir()) {
  const dataHome = env.XDG_DATA_HOME || path.join(homedir, ".local", "share");
  const dataDirs = (env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":").filter(Boolean);
  return [...new Set([dataHome, ...dataDirs].map((dir) => path.join(dir, "applications")))];
}

function createDefaultBrowser({
  platform = process.platform,
  env = process.env,
  homedir = os.homedir(),
  execPath = process.execPath,
  packaged = true,
  fileSystem = fs,
  execFile = defaultExecFile,
  spawn = childProcess.spawn,
  openExternal = async () => {},
  confirm = async () => false,
  isDefaultProtocolClient = () => false,
  osRelease = os.release(),
} = {}) {
  const realpath = (value) => { try { return fileSystem.realpathSync(value); } catch { return ""; } };

  function onPath(program) {
    if (!program) return "";
    if (path.isAbsolute(program)) return realpath(program);
    for (const dir of String(env.PATH || "").split(path.delimiter).filter(Boolean)) {
      const candidate = realpath(path.join(dir, program));
      if (candidate) return candidate;
    }
    return "";
  }

  // The desktop entry that starts this Raticode and says it opens web links.
  // Installed packages: the .deb/.rpm's gofer-flow.desktop or the AUR's
  // raticode.desktop. A development build or an AppImage without desktop
  // integration has none.
  function linuxDesktopEntry() {
    const own = new Set([realpath(execPath), env.APPIMAGE ? realpath(env.APPIMAGE) : ""].filter(Boolean));
    const known = [];
    for (const dir of linuxApplicationDirs(env, homedir)) {
      let names;
      try { names = fileSystem.readdirSync(dir).filter((name) => name.endsWith(".desktop")); } catch { continue; }
      for (const name of names) {
        let entry;
        try {
          const file = path.join(dir, name);
          const stat = fileSystem.statSync(file);
          if (!stat.isFile() || stat.size > MAX_DESKTOP_FILE_BYTES) continue;
          entry = parseDesktopEntry(fileSystem.readFileSync(file, "utf8"));
        } catch { continue; }
        const types = String(entry.MimeType || "").split(";");
        if (!types.includes("x-scheme-handler/https")) continue;
        if (own.has(onPath(execProgram(entry.Exec)))) return name;
        if (packaged && LINUX_KNOWN_ENTRIES.includes(name)) known.push(name);
      }
    }
    return known[0] || "";
  }

  async function run(command, args) {
    return execFile(command, args, { timeout: COMMAND_TIMEOUT_MS });
  }

  async function windowsRegistered() {
    try {
      const output = await run("reg", ["query", "HKCU\\Software\\RegisteredApplications", "/v", WINDOWS_APP_NAME]);
      return output.includes(WINDOWS_APP_NAME);
    } catch { return false; }
  }

  async function windowsIsDefault() {
    try {
      const output = await run("reg", ["query", "HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice", "/v", "ProgId"]);
      return new RegExp(`\\b${WINDOWS_URL_PROGID}\\b`).test(output);
    } catch { return false; }
  }

  function linuxDesktop() {
    return String(env.XDG_CURRENT_DESKTOP || env.DESKTOP_SESSION || "").toUpperCase();
  }

  // Programs that open a desktop's default-apps page, best first.
  function linuxSettingsCommands() {
    const desktop = linuxDesktop();
    if (desktop.includes("KDE")) return [["systemsettings", ["kcm_componentchooser"]], ["kcmshell6", ["kcm_componentchooser"]], ["kcmshell5", ["componentchooser"]]];
    if (desktop.includes("GNOME") || desktop.includes("UNITY") || desktop.includes("BUDGIE")) return [["gnome-control-center", ["default-apps"]]];
    if (desktop.includes("CINNAMON")) return [["cinnamon-settings", ["default"]]];
    if (desktop.includes("XFCE")) return [["xfce4-mime-settings", []]];
    return [];
  }

  async function status() {
    if (platform === "linux") {
      const entry = linuxDesktopEntry();
      let current = "";
      let readable = true;
      try { current = (await run("xdg-settings", ["get", "default-web-browser"])).trim(); } catch { readable = false; }
      const settings = linuxSettingsCommands().some(([command]) => onPath(command));
      return {
        platform,
        registered: Boolean(entry),
        desktopEntry: entry,
        isDefault: entry && readable ? current === entry : null,
        current: readable ? current : "",
        canMakeDefault: Boolean(entry) && Boolean(onPath("xdg-settings")),
        canOpenSettings: settings,
        problem: entry ? "" : "Raticode isn't installed as an app here, so the system can't list it as a browser. Install the .deb, .rpm or AUR package, or add an AppImage to your apps with your desktop's AppImage integration.",
      };
    }
    if (platform === "win32") {
      const registered = await windowsRegistered();
      return {
        platform,
        registered,
        isDefault: registered ? await windowsIsDefault() : false,
        canMakeDefault: false,
        canOpenSettings: true,
        problem: registered ? "" : "This copy of Raticode isn't registered with Windows as a browser. Install it with the Raticode installer.",
      };
    }
    if (platform === "darwin") {
      let isDefault;
      try { isDefault = Boolean(isDefaultProtocolClient("https")); } catch { isDefault = null; }
      return { platform, registered: packaged, isDefault, canMakeDefault: false, canOpenSettings: true, problem: packaged ? "" : "Only the installed Raticode app can be the default browser." };
    }
    return { platform, registered: false, isDefault: null, canMakeDefault: false, canOpenSettings: false, problem: "This system has no default browser setting Raticode knows." };
  }

  function macSettingsUrl() {
    // macOS 13 and later moved Default web browser to Desktop & Dock.
    const major = Number.parseInt(String(osRelease).split(".")[0], 10);
    return Number.isFinite(major) && major >= 22
      ? "x-apple.systempreferences:com.apple.Desktop-Settings.extension"
      : "x-apple.systempreferences:com.apple.preference.general";
  }

  function launchDetached(command, args) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (value) => { if (!settled) { settled = true; resolve(value); } };
      let child;
      try {
        child = spawn(command, args, { detached: true, stdio: "ignore" });
      } catch { done(false); return; }
      child.once?.("error", () => done(false));
      child.once?.("spawn", () => { child.unref?.(); done(true); });
      // Older Node versions have no spawn event.
      setTimeout(() => done(true), 1500).unref?.();
    });
  }

  // Opens the system's default-apps settings, where the user makes the change.
  async function openSettings() {
    if (platform === "win32") {
      const url = (await windowsRegistered()) ? `ms-settings:defaultapps?registeredAppUser=${WINDOWS_APP_NAME}` : "ms-settings:defaultapps";
      await openExternal(url);
      return { opened: true, target: url };
    }
    if (platform === "darwin") {
      const url = macSettingsUrl();
      await openExternal(url);
      return { opened: true, target: url };
    }
    if (platform === "linux") {
      for (const [command, args] of linuxSettingsCommands()) {
        if (!onPath(command)) continue;
        if (await launchDetached(command, args)) return { opened: true, target: [command, ...args].join(" ") };
      }
      return { opened: false, problem: "Your desktop has no default-apps settings Raticode knows how to open. Use Make Raticode the default browser instead." };
    }
    return { opened: false, problem: "This system has no default-apps settings Raticode knows how to open." };
  }

  // Linux only, after the user agrees in a dialog shown here, outside the page.
  async function makeDefault() {
    if (platform !== "linux") return { changed: false, problem: "Choose Raticode in the system's default-apps settings." };
    const entry = linuxDesktopEntry();
    if (!entry) return { changed: false, problem: "Raticode isn't installed as an app here, so it can't be the default browser." };
    const agreed = await confirm({
      title: "Make Raticode your default web browser?",
      message: "Links you open in other apps will open in Raticode.",
      detail: `Raticode will run: xdg-settings set default-web-browser ${entry}\nYou can change it back in your desktop's default-apps settings.`,
      confirmLabel: "Make default",
    });
    if (!agreed) return { changed: false, cancelled: true };
    try {
      await run("xdg-settings", ["set", "default-web-browser", entry]);
    } catch (error) {
      return { changed: false, problem: `xdg-settings could not change the default browser: ${String(error?.message || error).slice(0, 300)}` };
    }
    return { changed: true, status: await status() };
  }

  return { linuxDesktopEntry, makeDefault, openSettings, status };
}

function defaultExecFile(command, args, options) {
  return new Promise((resolve, reject) => {
    childProcess.execFile(command, args, { ...options, windowsHide: true, maxBuffer: 256 * 1024 }, (error, stdout) => {
      if (error) reject(error);
      else resolve(String(stdout));
    });
  });
}

module.exports = {
  MAX_ITEMS,
  PAGE_EXTENSIONS,
  WINDOWS_APP_NAME,
  WINDOWS_URL_PROGID,
  createDefaultBrowser,
  createExternalOpenQueue,
  execProgram,
  externalOpenFromArg,
  externalOpensFromArgv,
  linuxApplicationDirs,
  parseDesktopEntry,
};
