const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { pathToFileURL } = require("node:url");
const { createTrustedProjectStore } = require("../trusted-projects.cjs");
const { createIpcSecurity } = require("../security.cjs");

function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-migration-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const project = path.join(base, "project");
  const other = path.join(base, "other");
  fs.mkdirSync(project);
  fs.mkdirSync(other);
  const file = path.join(base, "trusted-projects.json");
  return { base, project, other, file, store: createTrustedProjectStore(file) };
}

test("legacy registry and recent folders migrate atomically once and survive restart", (t) => {
  const { project, other, file, store } = fixture(t);
  fs.writeFileSync(file, JSON.stringify([other]));
  const offline = path.join(project, "offline");
  store.migrate(JSON.stringify([project, project, offline, "relative", null, 42]));
  assert.deepEqual(store.read(), { roots: [other, project, offline], legacyRecentProjectsMigrated: true });
  const restarted = createTrustedProjectStore(file);
  assert.deepEqual(restarted.migrate(JSON.stringify([path.dirname(project)])), []);
  restarted.add([other]);
  assert.deepEqual(restarted.read(), store.read());
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});

test("empty or malformed history seals migration without granting paths", (t) => {
  const { store } = fixture(t);
  store.migrate("{broken");
  assert.deepEqual(store.read(), { roots: [], legacyRecentProjectsMigrated: true });
  assert.deepEqual(store.migrate('["/later-injection"]'), []);
});

test("failed registry publication preserves old records and permits next-launch retry", (t) => {
  const { file, store, project, other } = fixture(t);
  store.add([other]);
  const rename = fs.renameSync;
  t.mock.method(fs, "renameSync", () => { throw new Error("disk failure"); });
  assert.throws(() => store.migrate(JSON.stringify([project])), /disk failure/);
  assert.deepEqual(store.read(), { roots: [other], legacyRecentProjectsMigrated: false });
  fs.renameSync = rename;
  store.migrate(JSON.stringify([project]));
  assert.equal(store.read().legacyRecentProjectsMigrated, true);
  fs.writeFileSync(file, "corrupted registry");
  assert.throws(() => store.read(), SyntaxError);
});

function migrationHandler(store, base) {
  const source = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const body = source.slice(source.indexOf("function migrateLegacyProjects("), source.indexOf("function readTrustedRoots("));
  const url = pathToFileURL(path.join(base, "index.html")).href;
  const frame = { url };
  const sender = { mainFrame: frame };
  const security = createIpcSecurity({ getDataDir: () => path.join(base, "data"),
    appRoot: base, getMainWebContents: () => sender, isProduction: true });
  const context = { legacyProjectMigrationToken: "launch-token", trustedProjectStore: () => store,
    getIpcSecurity: () => security, writeBackendLog: () => {} };
  const handler = vm.runInNewContext(`(${body})`, context);
  return { handler, security, event: () => ({ sender, senderFrame: frame }) };
}

test("migration rejects foreign senders, bad tokens, replay and unrelated renewal", (t) => {
  const { base, store, project, other } = fixture(t);
  const { handler, security, event } = migrationHandler(store, base);
  const payload = { token: "launch-token", recentProjects: JSON.stringify([project]) };
  handler({ ...event(), sender: {} }, payload);
  handler(event(), { ...payload, token: "wrong" });
  assert.equal(store.read().legacyRecentProjectsMigrated, false);
  const accepted = event();
  handler(accepted, payload);
  assert.equal(accepted.returnValue, true);
  assert.equal(security.renewPath(project).path, project);
  handler(event(), { ...payload, recentProjects: JSON.stringify([other]) });
  assert.throws(() => security.renewPath(other), /outside/);
  assert.deepEqual(store.read().roots, [project]);
});

test("offline migrated folders renew when they return without restarting", (t) => {
  const { base, store } = fixture(t);
  const offline = path.join(base, "offline");
  const { handler, security, event } = migrationHandler(store, base);
  handler(event(), { token: "launch-token", recentProjects: JSON.stringify([offline]) });
  assert.throws(() => security.renewPath(offline));
  fs.mkdirSync(offline);
  assert.equal(security.renewPath(offline).path, offline);
  assert.throws(() => security.renewPath(base), /outside/);
});

test("isolated preload imports saved history before exposing the desktop bridge", (t) => {
  const { base, store, project, other } = fixture(t);
  const { handler, security, event } = migrationHandler(store, base);
  const exposed = {};
  let history = JSON.stringify([project]);
  let captures = 0;
  const source = fs.readFileSync(path.join(__dirname, "../preload.cjs"), "utf8");
  const context = { URL, process: { argv: ["--gofer-legacy-project-migration=launch-token"] },
    window: { addEventListener() {}, localStorage: { getItem(key) { assert.equal(key, "gofer.recentProjects"); captures += 1; return history; } } },
    require: () => ({
      contextBridge: { exposeInMainWorld(key, value) {
        assert.equal(security.renewPath(project).path, project);
        history = JSON.stringify([other]);
        exposed[key] = value;
      } },
      ipcRenderer: { sendSync(channel, payload) {
        assert.equal(channel, "gofer:migrate-legacy-projects");
        const request = event(); handler(request, payload); return request.returnValue;
      }, on() {} }, webFrame: {}, webUtils: {},
    }),
  };
  vm.runInNewContext(source, context);
  assert.equal(captures, 1);
  assert.ok(exposed.goferDesktop.workspace.trustProjectRoot);
  assert.equal(JSON.stringify(exposed).includes("launch-token"), false);
  assert.throws(() => security.renewPath(other), /outside/);
  // A later navigation runs preload again with the original argument, but the
  // consumed main-process token and durable marker deny new renderer history.
  vm.runInNewContext(source, { ...context });
  assert.deepEqual(store.read().roots, [project]);
});
