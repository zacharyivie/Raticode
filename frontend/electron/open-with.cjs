// "Open with" support for downloaded files. Each platform asks the operating
// system which installed applications can open a file:
// - Linux: XDG desktop entries and mimeapps.list associations.
// - macOS: NSWorkspace, through JavaScript for Automation.
// - Windows: SHAssocEnumHandlers, through PowerShell.
// Applications are always re-resolved before launch, so a renderer can only
// choose among the handlers the system reports for that file.
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const LOOKUP_TIMEOUT_MS = 20 * 1000;
const MAX_APPS = 40;
const WINDOWS_CHOOSER_ID = "system-chooser";

function createOpenWith({
  platform = process.platform,
  env = process.env,
  execFile = defaultExecFile,
  spawn = childProcess.spawn,
  fileSystem = fs,
  homedir = os.homedir(),
  selfExecutables = [process.execPath, process.env.APPIMAGE],
} = {}) {
  async function listApps(filePath) {
    if (!provider) return [];
    const apps = await provider.list(filePath);
    return apps.slice(0, MAX_APPS).map(({ id, name, isDefault }) => ({ id, name, isDefault: Boolean(isDefault) }));
  }

  async function openWith(filePath, appId) {
    if (!provider) throw new Error("Open with is not supported on this system.");
    const apps = await provider.list(filePath);
    const app = apps.find((candidate) => candidate.id === appId);
    if (!app) throw new Error("That application can no longer open this file.");
    await provider.launch(filePath, app);
  }

  // -------------------------------------------------------------------------
  // Linux

  const linuxProvider = {
    async list(filePath) {
      const mime = await linuxMimeType(filePath);
      const types = mimeCandidates(mime);
      const entries = desktopEntries();
      const associations = mimeAssociations();
      const defaultId = await execFile("xdg-mime", ["query", "default", mime]).then((value) => value.trim(), () => "");
      const ids = [];
      const add = (id) => { if (entries.has(id) && !ids.includes(id)) ids.push(id); };
      add(defaultId);
      for (const type of types) for (const id of associations.defaults.get(type) || []) add(id);
      for (const type of types) for (const id of associations.added.get(type) || []) add(id);
      const removed = new Set(types.flatMap((type) => associations.removed.get(type) || []));
      for (const [id, entry] of entries) {
        if (entry.mimeTypes.some((type) => types.includes(type))) add(id);
      }
      return ids
        .filter((id) => id === defaultId || !removed.has(id))
        .map((id) => ({ id, name: entries.get(id).name, isDefault: id === defaultId, entry: entries.get(id) }));
    },
    async launch(filePath, app) {
      const argv = desktopExecArgv(app.entry.exec, filePath);
      if (!argv.length) throw new Error(`${app.name} has no launch command.`);
      detach(argv[0], argv.slice(1));
    },
  };

  async function linuxMimeType(filePath) {
    for (const [command, args] of [["xdg-mime", ["query", "filetype", filePath]], ["file", ["--brief", "--mime-type", filePath]]]) {
      const value = await execFile(command, args).then((output) => output.trim().split(";")[0].trim(), () => "");
      if (/^[\w.+-]+\/[\w.+-]+$/.test(value)) return value;
    }
    return "application/octet-stream";
  }

  function dataDirs() {
    const home = env.XDG_DATA_HOME || path.join(homedir, ".local", "share");
    const system = (env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":").filter(Boolean);
    return [...new Set([home, ...system].map((dir) => path.resolve(dir)))];
  }

  function configDirs() {
    const home = env.XDG_CONFIG_HOME || path.join(homedir, ".config");
    const system = (env.XDG_CONFIG_DIRS || "/etc/xdg").split(":").filter(Boolean);
    return [home, ...system].map((dir) => path.resolve(dir));
  }

  // Desktop file IDs follow the XDG spec: the path below applications/ with
  // "/" replaced by "-". Earlier data directories take precedence.
  function desktopEntries() {
    const entries = new Map();
    const seen = new Set();
    for (const dir of dataDirs()) {
      const root = path.join(dir, "applications");
      for (const file of walkDesktopFiles(root)) {
        const id = path.relative(root, file).split(path.sep).join("-");
        if (seen.has(id)) continue;
        seen.add(id);
        const entry = parseDesktopEntry(readText(file));
        if (entry && usableEntry(entry)) entries.set(id, { ...entry, file });
      }
    }
    return entries;
  }

  function walkDesktopFiles(root, depth = 0) {
    let names;
    try { names = fileSystem.readdirSync(root, { withFileTypes: true }); } catch { return []; }
    const files = [];
    for (const item of names) {
      const target = path.join(root, item.name);
      if (item.isDirectory() && depth < 3) files.push(...walkDesktopFiles(target, depth + 1));
      else if (item.name.endsWith(".desktop")) files.push(target);
    }
    return files;
  }

  function usableEntry(entry) {
    if (entry.type !== "Application" || entry.hidden || entry.noDisplay || entry.terminal || !entry.exec || !entry.name) return false;
    const program = desktopExecArgv(entry.exec, "")[0] || "";
    if (selfExecutables.some((self) => self && path.resolve(self) === path.resolve(program))) return false;
    if (entry.tryExec && !commandExists(entry.tryExec)) return false;
    return true;
  }

  function commandExists(command) {
    if (path.isAbsolute(command)) return fileSystem.existsSync(command);
    return (env.PATH || "").split(":").some((dir) => dir && fileSystem.existsSync(path.join(dir, command)));
  }

  function mimeAssociations() {
    const result = { defaults: new Map(), added: new Map(), removed: new Map() };
    const desktops = (env.XDG_CURRENT_DESKTOP || "").toLowerCase().split(":").filter(Boolean);
    const names = [...desktops.map((desktop) => `${desktop}-mimeapps.list`), "mimeapps.list"];
    const files = [
      ...configDirs().flatMap((dir) => names.map((name) => path.join(dir, name))),
      ...dataDirs().flatMap((dir) => names.map((name) => path.join(dir, "applications", name))),
    ];
    for (const file of files) {
      const sections = parseIni(readText(file));
      for (const [section, key] of [["Default Applications", "defaults"], ["Added Associations", "added"], ["Removed Associations", "removed"]]) {
        for (const [type, value] of Object.entries(sections[section] || {})) {
          const list = result[key].get(type) || [];
          for (const id of value.split(";").map((item) => item.trim()).filter(Boolean)) if (!list.includes(id)) list.push(id);
          result[key].set(type, list);
        }
      }
    }
    return result;
  }

  function readText(file) {
    try { return fileSystem.readFileSync(file, "utf8"); } catch { return ""; }
  }

  // -------------------------------------------------------------------------
  // macOS

  const macProvider = {
    async list(filePath) {
      const output = await execFile("osascript", ["-l", "JavaScript", "-e", MAC_LIST_SCRIPT, filePath]);
      const result = JSON.parse(output || "{}");
      const apps = Array.isArray(result.apps) ? result.apps : [];
      const seen = new Set();
      return apps
        .filter((app) => app && typeof app.path === "string" && !seen.has(app.path) && seen.add(app.path))
        .filter((app) => !selfExecutables.some((self) => self && self.startsWith(`${app.path}/`)))
        .map((app) => ({ id: app.path, name: app.name || path.basename(app.path, ".app"), isDefault: app.path === result.default }))
        .sort((left, right) => Number(right.isDefault) - Number(left.isDefault));
    },
    async launch(filePath, app) {
      await execFile("open", ["-a", app.id, filePath]);
    },
  };

  // -------------------------------------------------------------------------
  // Windows

  const windowsProvider = {
    async list(filePath) {
      const output = await runPowerShell("list", filePath, "");
      const result = JSON.parse(output || "{}");
      const handlers = Array.isArray(result.apps) ? result.apps : [];
      const defaultPath = String(result.default || "").toLowerCase();
      const apps = handlers
        .filter((app) => app && typeof app.id === "string" && app.id)
        .filter((app) => !selfExecutables.some((self) => self && path.resolve(self).toLowerCase() === app.id.toLowerCase()))
        .map((app) => ({ id: app.id, name: app.name || path.basename(app.id), isDefault: Boolean(defaultPath) && app.id.toLowerCase() === defaultPath }))
        .sort((left, right) => Number(right.isDefault) - Number(left.isDefault));
      return [...apps, { id: WINDOWS_CHOOSER_ID, name: "Choose another app…", isDefault: false }];
    },
    async launch(filePath, app) {
      if (app.id === WINDOWS_CHOOSER_ID) {
        detach(path.join(env.SystemRoot || "C:\\Windows", "System32", "rundll32.exe"), ["shell32.dll,OpenAs_RunDLL", filePath]);
        return;
      }
      await runPowerShell("open", filePath, app.id);
    },
  };

  // Values travel through the environment so file names never reach the
  // PowerShell parser.
  function runPowerShell(mode, filePath, appId) {
    const encoded = Buffer.from(WINDOWS_SCRIPT, "utf16le").toString("base64");
    return execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], {
      env: { ...env, RATICODE_OPEN_WITH_MODE: mode, RATICODE_OPEN_WITH_PATH: filePath, RATICODE_OPEN_WITH_APP: appId },
      windowsHide: true,
    });
  }

  function detach(command, args) {
    const child = spawn(command, args, { detached: true, stdio: "ignore" });
    child.on?.("error", () => {});
    child.unref?.();
  }

  const provider = { linux: linuxProvider, darwin: macProvider, win32: windowsProvider }[platform];
  return { listApps, openWith };
}

// text/* files can always be read by plain text editors.
function mimeCandidates(mime) {
  const types = [mime];
  if (mime.startsWith("text/") && mime !== "text/plain") types.push("text/plain");
  return types;
}

function parseIni(text) {
  const sections = {};
  let current = null;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const header = /^\[(.+)\]$/.exec(line);
    if (header) {
      current = sections[header[1]] ||= {};
      continue;
    }
    const separator = line.indexOf("=");
    if (current && separator > 0) {
      const key = line.slice(0, separator).trim();
      if (!(key in current)) current[key] = line.slice(separator + 1).trim();
    }
  }
  return sections;
}

function parseDesktopEntry(text) {
  const entry = parseIni(text)["Desktop Entry"];
  if (!entry) return null;
  const truthy = (value) => value === "true";
  return {
    type: entry.Type || "",
    name: entry.Name || "",
    exec: entry.Exec || "",
    tryExec: entry.TryExec || "",
    hidden: truthy(entry.Hidden),
    noDisplay: truthy(entry.NoDisplay),
    terminal: truthy(entry.Terminal),
    mimeTypes: (entry.MimeType || "").split(";").map((item) => item.trim()).filter(Boolean),
  };
}

// Splits a desktop entry Exec value into argv and expands its field codes,
// following the XDG Desktop Entry specification.
function desktopExecArgv(exec, filePath) {
  const tokens = [];
  let current = "";
  let quoted = false;
  let started = false;
  const source = String(exec).replace(/\\\\/g, "\\");
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === "\\" && index + 1 < source.length && "\"`$\\".includes(source[index + 1])) {
        current += source[index + 1];
        index += 1;
      } else if (char === "\"") quoted = false;
      else current += char;
    } else if (char === "\"") {
      quoted = true;
      started = true;
    } else if (/\s/.test(char)) {
      if (started || current) tokens.push(current);
      current = "";
      started = false;
    } else current += char;
  }
  if (started || current) tokens.push(current);
  const argv = [];
  let fileAdded = false;
  for (const token of tokens) {
    if (/^%[fFuU]$/.test(token)) {
      if (filePath && !fileAdded) argv.push(filePath);
      fileAdded = true;
      continue;
    }
    if (/^%[ick]$/.test(token) || /^%[dDnNvm]$/.test(token)) continue;
    const expanded = token.replace(/%([fFuU%])/g, (match, code) => {
      if (code === "%") return "%";
      fileAdded = true;
      return filePath;
    }).replace(/%[ickdDnNvm]/g, "");
    argv.push(expanded);
  }
  if (filePath && !fileAdded) argv.push(filePath);
  return argv;
}

function defaultExecFile(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    childProcess.execFile(command, args, { timeout: LOOKUP_TIMEOUT_MS, maxBuffer: 1024 * 1024, ...options }, (error, stdout) => {
      if (error) reject(error);
      else resolve(String(stdout));
    });
  });
}

const MAC_LIST_SCRIPT = `
ObjC.import("AppKit");
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const workspace = $.NSWorkspace.sharedWorkspace;
  const manager = $.NSFileManager.defaultManager;
  const preferred = workspace.URLForApplicationToOpenURL(url);
  const urls = workspace.URLsForApplicationsToOpenURL(url);
  const apps = [];
  for (let index = 0; index < urls.count; index += 1) {
    const appPath = urls.objectAtIndex(index).path.js;
    apps.push({ path: appPath, name: manager.displayNameAtPath(appPath).js.replace(/\\.app$/, "") });
  }
  return JSON.stringify({ default: preferred.isNil() ? "" : preferred.path.js, apps });
}
`;

const WINDOWS_SCRIPT = String.raw`
$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

[ComImport, Guid("F04061AC-1659-4a3f-A954-775AA57FC083"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAssocHandler {
  void GetName([MarshalAs(UnmanagedType.LPWStr)] out string name);
  void GetUIName([MarshalAs(UnmanagedType.LPWStr)] out string name);
  void GetIconLocation([MarshalAs(UnmanagedType.LPWStr)] out string path, out int index);
  [PreserveSig] int IsRecommended();
  void MakeDefault([MarshalAs(UnmanagedType.LPWStr)] string description);
  void Invoke(IDataObject data);
}

[ComImport, Guid("973810ae-9599-4b88-9e4d-6ee98c9552da"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IEnumAssocHandlers {
  [PreserveSig] int Next(uint count, out IAssocHandler handler, out uint fetched);
}

[ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem {
  void BindToHandler(IntPtr context, [In] ref Guid handler, [In] ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out object result);
}

public static class RaticodeOpenWith {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern IEnumAssocHandlers SHAssocEnumHandlers(string extension, int filter);
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string path, IntPtr context, [In] ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItem item);
  [DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
  static extern int AssocQueryString(int flags, int str, string assoc, string extra, StringBuilder output, ref uint size);

  static List<IAssocHandler> Handlers(string extension) {
    var handlers = new List<IAssocHandler>();
    IEnumAssocHandlers items = SHAssocEnumHandlers(extension, 1);
    IAssocHandler handler;
    uint fetched;
    while (items.Next(1, out handler, out fetched) == 0 && fetched == 1) handlers.Add(handler);
    return handlers;
  }

  static string Json(string value) {
    var output = new StringBuilder("\"");
    foreach (char c in value ?? "") {
      if (c == '"' || c == '\\') output.Append('\\').Append(c);
      else if (c < ' ') output.AppendFormat("\\u{0:x4}", (int)c);
      else output.Append(c);
    }
    return output.Append('"').ToString();
  }

  public static string List(string extension) {
    var apps = new List<string>();
    foreach (IAssocHandler handler in Handlers(extension)) {
      string name, label;
      try { handler.GetName(out name); handler.GetUIName(out label); } catch (COMException) { continue; }
      apps.Add("{\"id\":" + Json(name) + ",\"name\":" + Json(label) + "}");
    }
    uint size = 1024;
    var executable = new StringBuilder((int)size);
    string preferred = AssocQueryString(0, 2, extension, null, executable, ref size) == 0 ? executable.ToString() : "";
    return "{\"default\":" + Json(preferred) + ",\"apps\":[" + string.Join(",", apps) + "]}";
  }

  public static void Open(string extension, string appId, string path) {
    foreach (IAssocHandler handler in Handlers(extension)) {
      string name;
      try { handler.GetName(out name); } catch (COMException) { continue; }
      if (name != appId) continue;
      Guid shellItem = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
      Guid dataObjectHandler = new Guid("B8C0BD9F-ED24-455c-83E6-D5390C4FE8C4");
      Guid dataObject = new Guid("0000010e-0000-0000-C000-000000000046");
      IShellItem item;
      SHCreateItemFromParsingName(path, IntPtr.Zero, ref shellItem, out item);
      object data;
      item.BindToHandler(IntPtr.Zero, ref dataObjectHandler, ref dataObject, out data);
      handler.Invoke((IDataObject)data);
      return;
    }
    throw new InvalidOperationException("That application can no longer open this file.");
  }
}
"@
$path = $env:RATICODE_OPEN_WITH_PATH
$extension = [System.IO.Path]::GetExtension($path)
if (-not $extension) { $extension = "." }
if ($env:RATICODE_OPEN_WITH_MODE -eq "open") { [RaticodeOpenWith]::Open($extension, $env:RATICODE_OPEN_WITH_APP, $path) }
else { [Console]::Out.Write([RaticodeOpenWith]::List($extension)) }
`;

module.exports = { createOpenWith, desktopExecArgv, mimeCandidates, parseDesktopEntry };
