require("./test-tmp.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { createOpenWith, desktopExecArgv, mimeCandidates } = require("../open-with.cjs");

function writeFile(target, text) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, text);
}

function desktop(name, exec, mime, extra = "") {
  return `[Desktop Entry]\nType=Application\nName=${name}\nExec=${exec}\nMimeType=${mime}\n${extra}\n[Desktop Action new]\nName=Other\nExec=ignored\n`;
}

function linuxFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-with-"));
  const home = path.join(root, "home");
  const userApps = path.join(home, ".local", "share", "applications");
  const systemApps = path.join(root, "usr", "share", "applications");
  writeFile(path.join(systemApps, "firefox.desktop"), desktop("Firefox", "/usr/lib/firefox/firefox %u", "text/html;"));
  writeFile(path.join(systemApps, "kate.desktop"), desktop("Kate", "kate -b %U", "text/plain;"));
  writeFile(path.join(systemApps, "hidden.desktop"), desktop("Hidden", "hidden %f", "text/html;", "NoDisplay=true"));
  writeFile(path.join(systemApps, "vim.desktop"), desktop("Vim", "vim %F", "text/plain;", "Terminal=true"));
  writeFile(path.join(systemApps, "self.desktop"), desktop("Old Raticode", "/opt/Raticode/raticode %U", "text/html;"));
  writeFile(path.join(systemApps, "removed.desktop"), desktop("Removed", "removed %f", "text/html;"));
  writeFile(path.join(systemApps, "kde", "okular.desktop"), desktop("Okular", "okular %U", "application/pdf;"));
  // A user entry overrides the system entry with the same desktop ID.
  writeFile(path.join(userApps, "kate.desktop"), desktop("Kate (user)", "kate --user %U", "text/plain;"));
  writeFile(path.join(home, ".config", "mimeapps.list"), "[Added Associations]\ntext/html=kde-okular.desktop;\n\n[Removed Associations]\ntext/html=removed.desktop;\n");
  const calls = [];
  const spawned = [];
  const openWith = createOpenWith({
    platform: "linux",
    env: { XDG_DATA_DIRS: path.join(root, "usr", "share"), PATH: "" },
    homedir: home,
    selfExecutables: ["/opt/Raticode/raticode"],
    execFile: async (command, args) => {
      calls.push([command, ...args]);
      if (command === "xdg-mime" && args[1] === "filetype") return "text/html\n";
      if (command === "xdg-mime" && args[1] === "default") return "firefox.desktop\n";
      throw new Error("unexpected");
    },
    spawn: (command, args, options) => {
      spawned.push({ command, args, options });
      return { on() {}, unref() {} };
    },
  });
  return { openWith, calls, spawned };
}

test("linux lists desktop entries for the file type with the system default first", async () => {
  const { openWith } = linuxFixture();
  const apps = await openWith.listApps("/downloads/page.html");
  assert.deepEqual(apps, [
    { id: "firefox.desktop", name: "Firefox", isDefault: true },
    { id: "kde-okular.desktop", name: "Okular", isDefault: false },
    { id: "kate.desktop", name: "Kate (user)", isDefault: false },
  ]);
});

test("linux launches the chosen desktop entry with the file and rejects unknown apps", async () => {
  const { openWith, spawned } = linuxFixture();
  await openWith.openWith("/downloads/my page.html", "kate.desktop");
  assert.deepEqual(spawned.map(({ command, args }) => [command, ...args]), [["kate", "--user", "/downloads/my page.html"]]);
  assert.equal(spawned[0].options.detached, true);
  await assert.rejects(openWith.openWith("/downloads/my page.html", "vim.desktop"), /no longer open/);
  await assert.rejects(openWith.openWith("/downloads/my page.html", "../../bin/sh"), /no longer open/);
  assert.equal(spawned.length, 1);
});

test("desktop Exec values expand field codes and quoting", () => {
  assert.deepEqual(desktopExecArgv("/usr/lib/firefox/firefox %u", "/tmp/a b.html"), ["/usr/lib/firefox/firefox", "/tmp/a b.html"]);
  assert.deepEqual(desktopExecArgv("\"/opt/My App/app\" --icon %i %c %k %F", "/tmp/x"), ["/opt/My App/app", "--icon", "/tmp/x"]);
  assert.deepEqual(desktopExecArgv("flatpak run --file-forwarding org.gimp.GIMP @@ %F @@", "/tmp/x.png"), ["flatpak", "run", "--file-forwarding", "org.gimp.GIMP", "@@", "/tmp/x.png", "@@"]);
  assert.deepEqual(desktopExecArgv("viewer --percent=100%%", "/tmp/x"), ["viewer", "--percent=100%", "/tmp/x"]);
  assert.deepEqual(mimeCandidates("text/html"), ["text/html", "text/plain"]);
});

test("macOS asks NSWorkspace and opens with the chosen app bundle", async () => {
  const calls = [];
  const openWith = createOpenWith({
    platform: "darwin",
    selfExecutables: ["/Applications/Raticode.app/Contents/MacOS/Raticode"],
    execFile: async (command, args) => {
      calls.push([command, ...args]);
      if (command === "osascript") {
        return JSON.stringify({
          default: "/Applications/Safari.app",
          apps: [
            { path: "/Applications/TextEdit.app", name: "TextEdit" },
            { path: "/Applications/Safari.app", name: "Safari" },
            { path: "/Applications/Raticode.app", name: "Raticode" },
          ],
        });
      }
      return "";
    },
  });
  const apps = await openWith.listApps("/Users/me/Downloads/page.html");
  assert.deepEqual(apps.map((app) => [app.name, app.isDefault]), [["Safari", true], ["TextEdit", false]]);
  assert.equal(calls[0].at(-1), "/Users/me/Downloads/page.html");
  await openWith.openWith("/Users/me/Downloads/page.html", "/Applications/TextEdit.app");
  assert.deepEqual(calls.at(-1), ["open", "-a", "/Applications/TextEdit.app", "/Users/me/Downloads/page.html"]);
});

test("windows lists shell handlers and passes the file outside the script text", async () => {
  const calls = [];
  const spawned = [];
  const openWith = createOpenWith({
    platform: "win32",
    env: { SystemRoot: "C:\\Windows" },
    selfExecutables: [],
    execFile: async (command, args, options) => {
      calls.push({ command, args, options });
      if (options.env.RATICODE_OPEN_WITH_MODE === "list") {
        return JSON.stringify({ default: "C:\\Program Files\\App\\edge.exe", apps: [{ id: "C:\\Windows\\notepad.exe", name: "Notepad" }, { id: "C:\\Program Files\\App\\EDGE.exe", name: "Edge" }] });
      }
      return "";
    },
    spawn: (command, args) => { spawned.push([command, ...args]); return { on() {}, unref() {} }; },
  });
  const file = "C:\\Users\\me\\Downloads\\a'; Remove-Item x; '.html";
  const apps = await openWith.listApps(file);
  assert.deepEqual(apps.map((app) => [app.name, app.isDefault]), [["Edge", true], ["Notepad", false], ["Choose another app…", false]]);
  assert.equal(calls[0].command, "powershell.exe");
  assert.ok(!calls[0].args.join(" ").includes("Remove-Item"));
  assert.equal(calls[0].options.env.RATICODE_OPEN_WITH_PATH, file);
  await openWith.openWith(file, "C:\\Windows\\notepad.exe");
  assert.equal(calls.at(-1).options.env.RATICODE_OPEN_WITH_MODE, "open");
  assert.equal(calls.at(-1).options.env.RATICODE_OPEN_WITH_APP, "C:\\Windows\\notepad.exe");
  await openWith.openWith(file, "system-chooser");
  assert.deepEqual(spawned, [[path.join("C:\\Windows", "System32", "rundll32.exe"), "shell32.dll,OpenAs_RunDLL", file]]);
});

test("unsupported platforms list no apps", async () => {
  const openWith = createOpenWith({ platform: "aix" });
  assert.deepEqual(await openWith.listApps("/tmp/file"), []);
  await assert.rejects(openWith.openWith("/tmp/file", "x"), /not supported/);
});
