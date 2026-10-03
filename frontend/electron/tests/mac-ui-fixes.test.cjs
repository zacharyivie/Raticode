const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const { unixTerminalShell } = require("../terminal-shell.cjs");
const { scanProject } = require("../project-search.cjs");
const safeFiles = require("../safe-files.cjs");
const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");

test("canonical Mac installer URLs and legacy redirects pass, foreign or mismatched URLs fail", () => {
  const context = { process: { platform: "darwin", arch: "arm64" } };
  vm.runInNewContext(source.slice(source.indexOf("function macInstallerUrl("), source.indexOf("async function openPath(")), context);
  const name = "Raticode-0.3.8-arm64.dmg";
  for (const repo of ["Raticode", "gofer-flow"]) {
    const url = `https://github.com/zacharyivie/${repo}/releases/download/v0.3.8/${name}`;
    const release = { tag_name: "v0.3.8", assets: [{ name, browser_download_url: url }] };
    assert.equal(context.macInstallerUrl(release), url);
    for (const bad of [url.replace("zacharyivie", "attacker"), `${url}?download=other`, url.replace("v0.3.8", "v0.3.7")]) {
      release.assets[0].browser_download_url = bad; assert.equal(context.macInstallerUrl(release), "");
    }
  }
});

test("case-insensitive same-entry rename preserves requested casing, distinct entries and hardlinks are protected", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-case-"));
  try {
    const from = path.join(root, "readme.md"), to = path.join(root, "README.md");
    fs.writeFileSync(from, "unchanged");
    const module = { exports: {} };
    const promises = { ...fs.promises, lstat: target => fs.promises.lstat(target === to ? from : target) };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../safe-files.cjs"), "utf8"), {
      module, Buffer, process: { platform: "darwin" }, require: id => id === "node:fs" ? { ...fs, promises } : id === "./security.cjs" ? require("../security.cjs") : require(id),
    });
    await module.exports.renamePath(from, to, () => {});
    assert.equal(fs.readFileSync(to, "utf8"), "unchanged"); assert.ok(!fs.existsSync(from));
    fs.writeFileSync(from, "different");
    await assert.rejects(safeFiles.renamePath(from, to, () => {}), /already exists/);
    fs.unlinkSync(to); fs.linkSync(from, to);
    await assert.rejects(safeFiles.renamePath(from, to, () => {}), /already exists/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("explicit includes search generated folders but never .git or links", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-includes-"));
  try {
    for (const folder of ["dist", "build", ".git"]) { fs.mkdirSync(path.join(root, folder)); fs.writeFileSync(path.join(root, folder, "sample.txt"), "needle"); }
    fs.symlinkSync(path.join(root, "dist/sample.txt"), path.join(root, "linked.txt"));
    assert.equal((await scanProject(root, { query: "needle" })).count, 0);
    const included = await scanProject(root, { query: "needle", include: "dist/**,build/**,.git/**,linked.txt" });
    assert.deepEqual(included.files.map(file => file.relativePath), ["build/sample.txt", "dist/sample.txt"]);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("zsh integration uses the selected login shell, loads custom rc aliases, and reports cwd", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-zsh-"));
  try {
    const home = path.join(root, "dotfiles"); fs.mkdirSync(home);
    fs.writeFileSync(path.join(home, ".zshrc"), 'alias raticode_fixture_alias="echo shell-alias-loaded"\n');
    const shell = await unixTerminalShell({ userShell: "/usr/bin/zsh", directory: root, env: { ZDOTDIR: home }, platform: "darwin" });
    assert.equal(shell.command, "/usr/bin/zsh"); assert.equal(shell.label, "zsh");
    if (fs.existsSync(shell.command)) {
      const out = execFileSync(shell.command, [...shell.args, "-c", "raticode_fixture_alias; __raticode_report_cwd"], { env: { ...process.env, ...shell.env }, cwd: root, encoding: "utf8" });
      assert.match(out, /shell-alias-loaded/); assert.ok(out.includes(`Cwd=${root}`));
    }
    const bash = await unixTerminalShell({ choice: "bash", userShell: "/usr/bin/zsh", directory: root });
    assert.equal(bash.command, "/bin/bash");
    await assert.rejects(unixTerminalShell({ choice: "injected", directory: root }), /Choose/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("restart and quit share a renderer handshake; a foreign or cancelled response cannot destroy the window", async () => {
  let handler, sent, stopped = 0, destroyed = 0, quits = 0;
  const owner = { id: 7, send: (_name, request) => { sent = request; }, once() {}, removeListener() {} };
  const context = { crypto: { randomUUID: () => "request-1" }, mainWindow: { isDestroyed: () => false, webContents: owner, destroy: () => destroyed++ }, isSmokeTest: false,
    app: { on: (_event, fn) => { handler = fn; }, quit: () => quits++ }, Promise, archivesDrained: true, logsDrained: true, applicationLog: null,
    showMainWindow() {}, stopBackend: () => stopped++, closeAllBrowsers() {}, closeTerminalEditorServer() {}, closeAllTerminals() {}, backendErrorWindow: null,
    startBackend: async () => ({ apiBaseUrl: "fixture", apiToken: "token" }), createWindow() {}, createBackendErrorWindow: error => { throw error; } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("let lifecycleApproved = false;"), source.indexOf('app.on("window-all-closed"')), context);
  context.completeLifecycle({ sender: owner }, { requestId: "renderer-ready", ready: true });
  vm.runInContext(source.slice(source.indexOf("async function restartBackend()"), source.indexOf("function openLogsFolder()")), context);
  const restart = context.restartBackend();
  assert.equal(stopped, 0); assert.equal(destroyed, 0);
  assert.equal(context.completeLifecycle({ sender: { id: 99 } }, { requestId: sent.requestId, approved: true }), false);
  context.completeLifecycle({ sender: owner }, { requestId: sent.requestId, approved: false });
  await restart; assert.equal(destroyed, 0);
  const approved = context.restartBackend();
  context.completeLifecycle({ sender: owner }, { requestId: sent.requestId, approved: true });
  await approved; assert.equal(destroyed, 1); assert.equal(stopped, 1);
  const event = { preventDefault() {} };
  handler(event); context.completeLifecycle({ sender: owner }, { requestId: sent.requestId, approved: false });
  await new Promise(resolve => setImmediate(resolve)); assert.equal(quits, 0);
  handler(event); context.completeLifecycle({ sender: owner }, { requestId: sent.requestId, approved: true });
  await new Promise(resolve => setImmediate(resolve)); assert.equal(quits, 1);
});

test("Mac menus retain application, editing, services and window roles; other platforms retain the studio menus", () => {
  const start = source.indexOf("  Menu.setApplicationMenu(");
  const end = source.indexOf("  setupIpcHandlers();", start);
  for (const platform of ["darwin", "linux", "win32"]) {
    let template;
    vm.runInNewContext(source.slice(start, end), { process: { platform }, Menu: { buildFromTemplate: value => value, setApplicationMenu: value => { template = value; } } });
    if (platform === "darwin") assert.deepEqual(Array.from(template.filter(item => item.role), item => item.role), ["appMenu", "editMenu", "windowMenu"]);
    else assert.equal(template, null);
  }
});
