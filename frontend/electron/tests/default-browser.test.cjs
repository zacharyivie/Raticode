const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  createDefaultBrowser,
  createExternalOpenQueue,
  execProgram,
  externalOpenFromArg,
  externalOpensFromArgv,
  parseDesktopEntry,
} = require("../default-browser.cjs");

const FRONTEND = path.resolve(__dirname, "..", "..");
const REPO = path.resolve(FRONTEND, "..");

// An in-memory file system: { "/path": "contents" } for files, a trailing / for folders.
function fakeFileSystem(files = {}, links = {}) {
  const has = (file) => Object.hasOwn(files, file);
  const dir = (folder) => Object.keys(files).some((file) => file.startsWith(`${folder}/`));
  return {
    statSync(file) {
      if (has(file)) return { isFile: () => true, size: String(files[file]).length };
      if (dir(file)) return { isFile: () => false, size: 0 };
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    },
    readFileSync(file) {
      if (!has(file)) throw Object.assign(new Error("missing"), { code: "ENOENT" });
      return files[file];
    },
    readdirSync(folder) {
      if (!dir(folder)) throw Object.assign(new Error("missing"), { code: "ENOENT" });
      return [...new Set(Object.keys(files).filter((file) => file.startsWith(`${folder}/`)).map((file) => file.slice(folder.length + 1).split("/")[0]))];
    },
    realpathSync(file) {
      if (Object.hasOwn(links, file)) return links[file];
      if (has(file) || dir(file)) return file;
      throw Object.assign(new Error("missing"), { code: "ENOENT" });
    },
  };
}

test("only web addresses and existing web-page files are taken from a command line", () => {
  const fileSystem = fakeFileSystem({ "/home/me/page.html": "<p>", "/home/me/notes.HTM": "<p>", "/home/me/run.sh": "x", "/home/me/folder.html/a": "" });
  const argv = [
    "/opt/Raticode/gofer-flow",
    "--",
    "--ozone-platform=x11",
    "https://example.com/a b?q=1#x",
    "http://example.org",
    "file:///home/me/page.html",
    "notes.HTM",
    "/home/me/run.sh",
    "/home/me/missing.html",
    "/home/me/folder.html",
    "javascript:alert(1)",
    "raticode://home",
    "ftp://example.com/file.html",
    "https://",
    "https://example.com/\nsecond",
    `https://example.com/${"a".repeat(9000)}`,
    "https://example.com/a b?q=1#x",
  ];
  assert.deepEqual(externalOpensFromArgv(argv, { cwd: "/home/me", defaultApp: false, fileSystem }), [
    { kind: "url", url: "https://example.com/a%20b?q=1#x" },
    { kind: "url", url: "http://example.org/" },
    { kind: "file", path: "/home/me/page.html" },
    { kind: "file", path: "/home/me/notes.HTM" },
  ]);
});

test("a development launch skips Electron's app folder, and a launch with nothing to open finds nothing", () => {
  const fileSystem = fakeFileSystem({ "/repo/frontend/index.html": "<p>" });
  assert.deepEqual(externalOpensFromArgv(["electron", "/repo/frontend/index.html", "https://example.com"], { defaultApp: true, fileSystem }), [{ kind: "url", url: "https://example.com/" }]);
  assert.deepEqual(externalOpensFromArgv(["/opt/Raticode/gofer-flow", "--toggle-dictation"], { defaultApp: false, fileSystem }), []);
  assert.deepEqual(externalOpensFromArgv(["/opt/Raticode/gofer-flow", "--run-workflow-shortcut=abc"], { defaultApp: false, fileSystem }), []);
  assert.equal(externalOpenFromArg("-psn_0_12345", { fileSystem }), null);
});

test("a command line opens at most 20 items", () => {
  const argv = ["exe", ...Array.from({ length: 30 }, (_, index) => `https://example.com/${index}`)];
  assert.equal(externalOpensFromArgv(argv, { defaultApp: false }).length, 20);
});

test("links wait until the window asks for them, then go straight to it until it reloads", () => {
  const queue = createExternalOpenQueue({ limit: 3 });
  const link = (n) => ({ kind: "url", url: `https://example.com/${n}` });
  assert.deepEqual(queue.add([link(1), link(2)]), []);
  assert.deepEqual(queue.add([link(3), link(4)]), []);
  assert.equal(queue.pending, 3);
  assert.deepEqual(queue.take(), [link(2), link(3), link(4)]);
  assert.deepEqual(queue.add([link(5)]), [link(5)]);
  assert.equal(queue.pending, 0);
  queue.reset();
  assert.deepEqual(queue.add([link(6)]), []);
  assert.deepEqual(queue.take(), [link(6)]);
  assert.deepEqual(queue.add([]), []);
});

test("desktop entries are read from the main section and Exec's program is unquoted", () => {
  const entry = parseDesktopEntry("# x\n[Desktop Entry]\nName=Raticode\nExec=\"/opt/My Apps/gofer-flow\" -- %U\nMimeType=text/html;\n[Desktop Action new]\nExec=other\n");
  assert.equal(entry.Name, "Raticode");
  assert.equal(execProgram(entry.Exec), "/opt/My Apps/gofer-flow");
  assert.equal(execProgram("raticode -- %U"), "raticode");
  assert.equal(execProgram(""), "");
});

const LINUX_ENTRY = "[Desktop Entry]\nExec=/opt/Raticode/gofer-flow -- %U\nMimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n";

function linuxBrowser({ files = {}, links = {}, commands = {}, env = {}, packaged = true, confirmAnswer = false, spawned = [] } = {}) {
  const calls = [];
  const fileSystem = fakeFileSystem({ "/usr/bin/xdg-settings": "", ...files }, links);
  return {
    calls,
    browser: createDefaultBrowser({
      platform: "linux",
      env: { PATH: "/usr/bin", XDG_DATA_HOME: "/home/me/.local/share", XDG_DATA_DIRS: "/usr/share", ...env },
      homedir: "/home/me",
      execPath: "/opt/Raticode/gofer-flow",
      packaged,
      fileSystem,
      execFile: async (command, args) => {
        calls.push([command, ...args]);
        const key = [command, ...args].join(" ");
        if (key in commands) {
          if (commands[key] instanceof Error) throw commands[key];
          return commands[key];
        }
        throw new Error(`unexpected ${key}`);
      },
      spawn: (command, args) => {
        spawned.push([command, ...args]);
        return { once: (name, callback) => { if (name === "spawn") setImmediate(callback); }, unref() {} };
      },
      confirm: async (question) => { calls.push(["confirm", question.title]); return confirmAnswer; },
    }),
  };
}

test("Linux finds the desktop entry that starts this Raticode and says whether it is the default browser", async () => {
  const { browser } = linuxBrowser({
    files: {
      "/usr/share/applications/firefox.desktop": "[Desktop Entry]\nExec=firefox %u\nMimeType=x-scheme-handler/https;\n",
      "/usr/share/applications/gofer-flow.desktop": LINUX_ENTRY,
    },
    commands: { "xdg-settings get default-web-browser": "firefox.desktop\n" },
  });
  assert.equal(browser.linuxDesktopEntry(), "gofer-flow.desktop");
  const status = await browser.status();
  assert.equal(status.registered, true);
  assert.equal(status.isDefault, false);
  assert.equal(status.current, "firefox.desktop");
  assert.equal(status.canMakeDefault, true);
  assert.equal(status.problem, "");
});

test("Linux matches the AUR's raticode launcher through PATH, and a development build matches nothing it doesn't start", async () => {
  const files = {
    "/usr/share/applications/raticode.desktop": "[Desktop Entry]\nExec=raticode -- %U\nMimeType=text/html;x-scheme-handler/https;\n",
    "/usr/bin/raticode": "#!/bin/sh",
  };
  const packaged = linuxBrowser({ files, commands: { "xdg-settings get default-web-browser": "raticode.desktop" } });
  assert.equal(packaged.browser.linuxDesktopEntry(), "raticode.desktop");
  assert.equal((await packaged.browser.status()).isDefault, true);
  const development = linuxBrowser({ files, packaged: false, commands: { "xdg-settings get default-web-browser": "raticode.desktop" } });
  assert.equal(development.browser.linuxDesktopEntry(), "");
  const status = await development.browser.status();
  assert.equal(status.registered, false);
  assert.equal(status.isDefault, null);
  assert.match(status.problem, /isn't installed as an app/);
  // An entry that doesn't claim web links is never Raticode's browser entry.
  const plain = linuxBrowser({ files: { "/usr/share/applications/gofer-flow.desktop": "[Desktop Entry]\nExec=/opt/Raticode/gofer-flow %U\n" } });
  assert.equal(plain.browser.linuxDesktopEntry(), "");
});

test("Linux changes the default browser only after the user agrees in the main process's dialog", async () => {
  const files = { "/usr/share/applications/gofer-flow.desktop": LINUX_ENTRY };
  const declined = linuxBrowser({ files, confirmAnswer: false, commands: { "xdg-settings get default-web-browser": "firefox.desktop" } });
  assert.deepEqual(await declined.browser.makeDefault(), { changed: false, cancelled: true });
  assert.equal(declined.calls.some((call) => call[0] === "xdg-settings" && call[1] === "set"), false);

  const agreed = linuxBrowser({
    files,
    confirmAnswer: true,
    commands: { "xdg-settings set default-web-browser gofer-flow.desktop": "", "xdg-settings get default-web-browser": "gofer-flow.desktop" },
  });
  const result = await agreed.browser.makeDefault();
  assert.equal(result.changed, true);
  assert.equal(result.status.isDefault, true);
  const order = agreed.calls.map((call) => call.slice(0, 2).join(" "));
  assert.ok(order.indexOf("confirm Make Raticode your default web browser?") < order.indexOf("xdg-settings set"));

  const failing = linuxBrowser({ files, confirmAnswer: true, commands: { "xdg-settings set default-web-browser gofer-flow.desktop": new Error("no desktop") } });
  const failed = await failing.browser.makeDefault();
  assert.equal(failed.changed, false);
  assert.match(failed.problem, /no desktop/);

  const unregistered = linuxBrowser({ confirmAnswer: true });
  assert.equal((await unregistered.browser.makeDefault()).changed, false);
  assert.equal(unregistered.calls.some((call) => call[0] === "confirm"), false);
});

test("Linux opens the desktop's own default-apps settings where it knows them", async () => {
  const spawned = [];
  const kde = linuxBrowser({ env: { XDG_CURRENT_DESKTOP: "KDE" }, files: { "/usr/bin/systemsettings": "" }, spawned });
  assert.deepEqual(await kde.browser.openSettings(), { opened: true, target: "systemsettings kcm_componentchooser" });
  assert.deepEqual(spawned, [["systemsettings", "kcm_componentchooser"]]);
  const gnome = linuxBrowser({ env: { XDG_CURRENT_DESKTOP: "ubuntu:GNOME" }, files: { "/usr/bin/gnome-control-center": "" }, spawned: [] });
  assert.equal((await gnome.browser.openSettings()).target, "gnome-control-center default-apps");
  const unknown = linuxBrowser({ env: { XDG_CURRENT_DESKTOP: "sway" } });
  const result = await unknown.browser.openSettings();
  assert.equal(result.opened, false);
  assert.match(result.problem, /Make Raticode the default browser/);
});

test("Windows reads the user's choice and opens Default apps at Raticode's page; it never changes the default itself", async () => {
  const opened = [];
  const calls = [];
  const registered = {
    "reg query HKCU\\Software\\RegisteredApplications /v Raticode": "    Raticode    REG_SZ    Software\\Clients\\StartMenuInternet\\Raticode\\Capabilities",
    "reg query HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice /v ProgId": "    ProgId    REG_SZ    RaticodeURL",
  };
  const browser = createDefaultBrowser({
    platform: "win32",
    execFile: async (command, args) => {
      const key = [command, ...args].join(" ");
      calls.push(key);
      if (key in registered) return registered[key];
      throw new Error("not found");
    },
    openExternal: async (url) => { opened.push(url); },
  });
  const status = await browser.status();
  assert.equal(status.registered, true);
  assert.equal(status.isDefault, true);
  assert.equal(status.canMakeDefault, false);
  assert.deepEqual(await browser.openSettings(), { opened: true, target: "ms-settings:defaultapps?registeredAppUser=Raticode" });
  assert.deepEqual(opened, ["ms-settings:defaultapps?registeredAppUser=Raticode"]);
  assert.equal((await browser.makeDefault()).changed, false);
  assert.equal(calls.some((key) => / add | delete /i.test(` ${key} `)), false);

  const portable = createDefaultBrowser({ platform: "win32", execFile: async () => { throw new Error("not found"); }, openExternal: async (url) => { opened.push(url); } });
  const portableStatus = await portable.status();
  assert.equal(portableStatus.registered, false);
  assert.match(portableStatus.problem, /installer/);
  assert.equal((await portable.openSettings()).target, "ms-settings:defaultapps");
});

test("macOS asks Launch Services and opens Desktop & Dock settings", async () => {
  const opened = [];
  const browser = createDefaultBrowser({ platform: "darwin", osRelease: "24.1.0", isDefaultProtocolClient: (scheme) => scheme === "https", openExternal: async (url) => { opened.push(url); } });
  assert.equal((await browser.status()).isDefault, true);
  assert.equal((await browser.openSettings()).target, "x-apple.systempreferences:com.apple.Desktop-Settings.extension");
  const older = createDefaultBrowser({ platform: "darwin", osRelease: "21.6.0", openExternal: async () => {} });
  assert.equal((await older.openSettings()).target, "x-apple.systempreferences:com.apple.preference.general");
  assert.equal((await older.makeDefault()).changed, false);
});

test("packaging registers Raticode as a browser without making it the default", () => {
  const build = JSON.parse(fs.readFileSync(path.join(FRONTEND, "package.json"), "utf8")).build;
  // Linux: the generated .desktop file lists web links and pages, and links follow `--`
  // so Chromium never reads one as a switch.
  assert.deepEqual(build.linux.mimeTypes, ["text/html", "application/xhtml+xml", "x-scheme-handler/http", "x-scheme-handler/https"]);
  assert.deepEqual(build.linux.executableArgs, ["--", "%U"]);
  assert.match(build.linux.category, /\bWebBrowser\b/);
  // electron-builder's own protocol and file registration would set Windows defaults; it isn't used.
  assert.equal(build.protocols, undefined);
  assert.equal(build.fileAssociations, undefined);
  assert.equal(build.win.fileAssociations, undefined);
  assert.equal(build.win.protocols, undefined);
  // macOS: Viewer for http/https and an Alternate (not default) handler for web pages.
  const info = build.mac.extendInfo;
  assert.deepEqual(info.CFBundleURLTypes[0].CFBundleURLSchemes, ["http", "https"]);
  assert.equal(info.CFBundleDocumentTypes[0].LSHandlerRank, "Alternate");
  assert.deepEqual(info.CFBundleDocumentTypes[0].LSItemContentTypes, ["public.html", "public.xhtml"]);

  const nsis = fs.readFileSync(path.join(FRONTEND, "build-resources", "installer.nsh"), "utf8");
  for (const key of [
    "Software\\RegisteredApplications",
    "${RATICODE_BROWSER_KEY}\\Capabilities\\URLAssociations\" \"https\" \"RaticodeURL\"",
    "${RATICODE_BROWSER_KEY}\\Capabilities\\FileAssociations\" \".html\" \"RaticodeHTML\"",
    "Software\\Classes\\RaticodeURL\\shell\\open\\command\" \"\" '\"$INSTDIR\\${APP_EXECUTABLE_FILENAME}\" -- \"%1\"'",
  ]) assert.ok(nsis.includes(key), key);
  assert.match(nsis, /!insertmacro RaticodeRegisterBrowser/);
  assert.match(nsis, /!insertmacro RaticodeUnregisterBrowser/);
  assert.match(nsis, /DeleteRegKey HKCU "\$\{RATICODE_BROWSER_KEY\}"/);
  // Never the user's choice, and never a class default for web links or pages.
  const commands = nsis.split("\n").filter((line) => !line.trim().startsWith(";")).join("\n");
  assert.doesNotMatch(commands, /UserChoice/);
  assert.doesNotMatch(commands, /Software\\Classes\\(?:https?|\.html?|\.xhtml|\.shtml)" ""/);

  const aur = fs.readFileSync(path.join(REPO, "packaging", "aur", "raticode-bin", "raticode.desktop"), "utf8");
  const entry = parseDesktopEntry(aur);
  assert.equal(entry.Exec, "raticode -- %U");
  assert.deepEqual(entry.MimeType.split(";").filter(Boolean), build.linux.mimeTypes);
  assert.match(entry.Categories, /\bWebBrowser\b/);
});

test("the running Raticode receives links from a second launch and from macOS, and its window asks for the waiting ones", () => {
  const main = fs.readFileSync(path.join(FRONTEND, "electron", "main.js"), "utf8");
  const secondInstance = main.slice(main.indexOf('app.on("second-instance"'));
  assert.match(secondInstance.slice(0, 400), /receiveExternalOpens\(externalOpensFromArgv\(argv, \{ cwd: workingDirectory/);
  assert.match(main, /app\.on\("open-url"/);
  assert.match(main, /app\.on\("open-file"/);
  assert.match(main, /receiveExternalOpens\(externalOpensFromArgv\(process\.argv\)\)/);
  assert.match(main, /case "take-opens": return externalOpenQueue\.take\(\)/);
  const handlers = require("../ipc-handlers.cjs").desktopIpcHandlers;
  assert.ok(handlers.some(([channel, name]) => channel === "gofer:default-browser" && name === "defaultBrowser"));
});
