const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { app, BrowserWindow, ipcMain, session } = require("electron");
const { createTrustedProjectStore } = require("../trusted-projects.cjs");
const { createIpcSecurity } = require("../security.cjs");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-dev-shm-usage");
app.commandLine.appendSwitch("no-sandbox");
app.on("window-all-closed", () => {});
let root;
let studio;
const deadline = setTimeout(() => finish(new Error("Legacy migration browser test timed out")), 20000);
function finish(error) {
  clearTimeout(deadline);
  for (const window of BrowserWindow.getAllWindows()) window.destroy();
  if (root) fs.rmSync(root, { recursive: true, force: true });
  if (error) console.error(error);
  else console.log("Real isolated preload migrated legacy history before page scripts; replay denied.");
  app.exit(error ? 1 : 0);
}

app.whenReady().then(async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-migration-browser-"));
  const project = path.join(root, "project");
  const injected = path.join(root, "injected");
  fs.mkdirSync(project);
  fs.mkdirSync(injected);
  const index = path.join(root, "index.html");
  fs.writeFileSync(index, `<!doctype html><script>localStorage.setItem('gofer.recentProjects',${JSON.stringify(JSON.stringify([injected]))})</script>`);
  const isolatedSession = session.fromPartition(`migration-${Date.now()}`);
  const seed = new BrowserWindow({ show: false, webPreferences: { session: isolatedSession, sandbox: true } });
  await seed.loadFile(index);
  await seed.webContents.executeJavaScript(`localStorage.setItem('gofer.recentProjects',${JSON.stringify(JSON.stringify([project]))})`);
  seed.destroy();
  const store = createTrustedProjectStore(path.join(root, "trusted-projects.json"));
  const security = createIpcSecurity({ getDataDir: () => path.join(root, "data"),
    appRoot: root, getMainWebContents: () => studio?.webContents, isProduction: true });
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const handler = source.slice(source.indexOf("function migrateLegacyProjects("), source.indexOf("function readTrustedRoots("));
  ipcMain.on("gofer:migrate-legacy-projects", vm.runInNewContext(`(${handler})`, {
    legacyProjectMigrationToken: "test-capability", trustedProjectStore: () => store,
    getIpcSecurity: () => security, writeBackendLog: () => {},
  }));
  studio = new BrowserWindow({ show: false, webPreferences: {
    session: isolatedSession, sandbox: true, contextIsolation: true, nodeIntegration: false,
    preload: path.join(__dirname, "../preload.cjs"),
    additionalArguments: ["--gofer-legacy-project-migration=test-capability"],
  } });
  await studio.loadFile(index);
  assert.deepEqual(store.read().roots, [project]);
  assert.equal(security.renewPath(project).path, project);
  assert.throws(() => security.renewPath(injected), /outside/);
  await studio.loadFile(index);
  assert.deepEqual(store.read().roots, [project]);
  assert.equal(await studio.webContents.executeJavaScript("typeof goferDesktop.workspace.trustProjectRoot"), "function");
  finish();
}).catch(finish);
