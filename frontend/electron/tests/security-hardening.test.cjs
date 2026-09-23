const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const fsp = fs.promises;
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { pathToFileURL } = require("node:url");
const { createIpcSecurity } = require("../security.cjs");
const safeFiles = require("../safe-files.cjs");
const policy = require("../studio-policy.cjs");
const corpus = require("../../../tests/fixtures/path-containment.json");
const mainSource = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");

function mainFunction(name, context) {
  if (name !== "resolveWorkflowSource" && context.resolveExactPath) {
    context.resolveWorkflowSource = mainFunction("resolveWorkflowSource", {
      resolveExactPath: context.resolveExactPath,
      migrateSource: require("../rattish-migration.cjs").migrateSource,
    });
  }
  const start = mainSource.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(start >= 0);
  const remainder = mainSource.slice(start);
  const end = remainder.search(/\n(?:async )?function /);
  return vm.runInNewContext(`(${remainder.slice(0, end < 0 ? undefined : end).trim()}\n)`, context);
}

async function fixture(t) {
  const base = await fsp.mkdtemp(path.join(os.tmpdir(), "raticode-security-"));
  t.after(() => fsp.rm(base, { recursive: true, force: true }));
  const inside = path.join(base, "inside");
  const outside = path.join(base, "outside");
  await fsp.mkdir(inside);
  await fsp.mkdir(outside);
  return { base, inside, outside, security: createIpcSecurity({ getDataDir: () => inside }) };
}

test("listing 100 worktrees never registers backend grants", async () => {
  const worktrees = Array.from({ length: 100 }, (_, i) => ({ path: `/project/tree-${i}` }));
  const list = mainFunction("gitWorktrees", {
    resolveGitProjectDirectory: async () => "/project",
    readGitWorktrees: async () => ({ active: true, worktrees }),
    entryPathHandle: target => ({ path: target, grantId: "existing-agent-grant" }),
    getIpcSecurity: () => ({ isUserGrant: () => false }),
    registerBackendPathGrant: () => assert.fail("Enumeration must not contact the backend"),
  });
  for (let i = 0; i < 3; i++) {
    const result = await list(null, { projectRoot: "/project" });
    assert.equal(result.worktrees.length, 100);
    assert.equal(result.worktrees[99].grantId, "existing-agent-grant");
  }
});

for (const entry of corpus.cases) {
  test(`shared containment: ${entry.name}`, async (t) => {
    const { base, security } = await fixture(t);
    for (const directory of entry.directories || []) await fsp.mkdir(path.join(base, directory), { recursive: true });
    for (const file of entry.files || []) {
      await fsp.mkdir(path.dirname(path.join(base, file)), { recursive: true });
      await fsp.writeFile(path.join(base, file), "fixture");
    }
    for (const link of entry.links || []) {
      await fsp.mkdir(path.dirname(path.join(base, link.path)), { recursive: true });
      try { await fsp.symlink(path.join(base, link.target), path.join(base, link.path)); }
      catch (error) { if (process.platform === "win32" && error.code === "EPERM") return t.skip("Windows requires symlink privileges"); throw error; }
    }
    const check = () => security.resolveAllowedPath(path.join(base, entry.candidate), { mustExist: entry.must_exist });
    if (entry.allowed) assert.doesNotThrow(check);
    else assert.throws(check);
  });
}

test("actual editor save rejects dangling and hard links without touching outside content", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const save = mainFunction("writeTextFile", {
    resolveExactPath: security.resolveAllowedPath, safeFiles,
    pathHandle: (target) => ({ path: target }),
  });
  const link = path.join(inside, "draft.txt");
  const target = path.join(outside, "new.txt");
  await fsp.symlink(target, link);
  await assert.rejects(save(null, { targetPath: link, content: "escaped" }));
  assert.equal(fs.existsSync(target), false);
  await fsp.unlink(link);
  await fsp.writeFile(target, "original");
  await fsp.link(target, link);
  await assert.rejects(save(null, { targetPath: link, content: "escaped" }), /linked/);
  assert.equal(await fsp.readFile(target, "utf8"), "original");
  const ordinary = path.join(inside, "ordinary.txt");
  await save(null, { targetPath: ordinary, content: "long text" });
  await save(null, { targetPath: ordinary, content: "short" });
  assert.equal(await fsp.readFile(ordinary, "utf8"), "short");
});

test("parent replacement is rejected before a write and copy cannot install an escaping link", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const parent = path.join(inside, "parent");
  await fsp.mkdir(parent);
  let checks = 0;
  const authorize = (target) => {
    const result = security.resolveAllowedPath(target);
    if (++checks === 3) {
      fs.renameSync(parent, path.join(inside, "moved"));
      fs.symlinkSync(outside, parent, "dir");
    }
    return result;
  };
  await assert.rejects(safeFiles.writeFile(path.join(parent, "new.txt"), "escaped", { authorize }));
  assert.equal(fs.existsSync(path.join(outside, "new.txt")), false);
  const source = path.join(inside, "source");
  await fsp.mkdir(source);
  await fsp.writeFile(path.join(outside, "secret"), "original");
  await fsp.symlink(path.join(outside, "secret"), path.join(source, "link"));
  await assert.rejects(safeFiles.copyPath(source, path.join(inside, "copy"), {
    authorizeSource: security.resolveAllowedPath,
    authorizeDestination: security.resolveAllowedPath,
  }));
  assert.equal(fs.existsSync(path.join(inside, "copy", "link")), false);
});

test("project selection accepts arbitrary roots and persists access without a picker", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const registered = [];
  const common = { path, fs, getIpcSecurity: () => security,
    registerBackendPathGrant: async (handle) => registered.push(handle),
    restoreBackendTrustedRoots: async () => {},
  };
  const renew = mainFunction("grantPath", common);
  assert.equal((await renew(null, { targetPath: outside })).path, outside);
  const select = mainFunction("selectPath", {
    ...common, mainWindow: null, resolvePickerDefaultPath: () => inside,
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: [outside] }) },
  });
  const handle = await select(null, { directoryOnly: true });
  assert.equal(handle.path, outside);
  assert.equal((await renew(null, { targetPath: outside })).grantId, handle.grantId);
  const reopened = createIpcSecurity({ getDataDir: () => inside, trustedRoots: [outside] });
  assert.equal(reopened.renewPath(outside).path, outside);
  const failRenew = mainFunction("grantPath", { ...common, registerBackendPathGrant: async () => { throw new Error("registration failed"); } });
  await assert.rejects(failRenew(null, { targetPath: outside }), /registration failed/);
  assert.equal((await renew(null, { targetPath: path.dirname(outside) })).path, path.dirname(outside));
});

test("agent resolver requires an existing grant for every directory listing mode", async (t) => {
  const { outside, security } = await fixture(t);
  const list = mainFunction("listDirectory", {
    path, fs, resolveExactPath: security.resolveAllowedPath,
    pathHandle: (target) => security.renewPath(target),
    entryPathHandle: (target) => ({ path: target }),
  });
  for (const create of [false, true, undefined]) await assert.rejects(list(null, { currentPath: outside, create }), /outside/);
  const grant = security.trustPath(outside);
  assert.equal((await list(null, { currentPath: outside, grantId: grant.grantId })).directory, outside);
});

test("remote capabilities are denied while studio audio and clipboard writes remain available", () => {
  const fakeSession = () => ({
    setPermissionRequestHandler(fn) { this.request = fn; },
    setPermissionCheckHandler(fn) { this.check = fn; },
    setDevicePermissionHandler(fn) { this.device = fn; },
  });
  const remote = fakeSession();
  const studio = fakeSession();
  const indexPath = path.resolve(__dirname, "../../dist/index.html");
  const contents = { getURL: () => pathToFileURL(indexPath).href };
  const options = { indexPath, isProduction: true, devServerUrl: "http://127.0.0.1:5173" };
  policy.installPermissionPolicy(remote, studio, () => contents, options);
  for (const permission of ["media", "geolocation", "notifications", "clipboard-read", "display-capture"]) {
    let allowed;
    remote.request(contents, permission, (value) => { allowed = value; });
    assert.equal(allowed, false);
    assert.equal(remote.check(contents, permission), false);
  }
  assert.equal(studio.check(contents, "media", "", { mediaType: "audio" }), true);
  assert.equal(studio.check(contents, "media", "", { mediaType: "video" }), false);
  assert.equal(studio.check({}, "media", "", { mediaType: "audio" }), false);
  assert.equal(studio.check(contents, "clipboard-sanitized-write"), true);
  let microphone;
  studio.request(contents, "media", (value) => { microphone = value; }, { mediaTypes: ["audio"], requestingUrl: contents.getURL() });
  assert.equal(microphone, true);
  assert.equal(policy.isStudioDocument("https://attacker.invalid", options), false);
  assert.equal(policy.isStudioDocument(pathToFileURL(path.join(path.dirname(indexPath), "injected.html")).href, options), false);
  const csp = policy.studioCsp({ ...options, apiBaseUrl: "http://127.0.0.1:43817" });
  assert.match(csp, /connect-src 'self' http:\/\/127.0.0.1:43817;/);
  assert.match(csp, /worker-src 'self' blob:/);
  assert.doesNotMatch(csp, /unsafe-eval/);
  assert.match(csp, /script-src 'self';/);
});

for (const entry of corpus.lexicalCases) {
  test(`shared grant matching: ${entry.platform} ${entry.candidate}`, () => {
    const source = fs.readFileSync(path.join(__dirname, "../preload.cjs"), "utf8");
    const start = source.indexOf("function grantForPath(");
    const end = source.indexOf("async function invokeDesktop", start);
    const matcher = vm.runInNewContext(`${source.slice(start, end)}; grantForPath`, {
      process: { platform: entry.platform },
      pathGrants: new Map([[entry.root, "fixture-grant"]]),
    });
    assert.equal(Boolean(matcher(entry.candidate)), entry.allowed);
    const implementation = entry.platform === "win32" ? path.win32 : path.posix;
    const relative = implementation.relative(entry.root, entry.candidate);
    const contained = relative === "" || relative !== ".." && !relative.startsWith(`..${implementation.sep}`) && !implementation.isAbsolute(relative);
    assert.equal(contained, entry.allowed);
  });
}

test("replacing an approved root with an outside symlink does not transfer its grant", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const granted = path.join(inside, "project");
  await fsp.mkdir(granted);
  const handle = security.trustPath(granted);
  await fsp.rename(granted, path.join(inside, "old-project"));
  await fsp.symlink(outside, granted, "dir");
  assert.throws(() => security.resolveAllowedPath(path.join(granted, "escape.txt"), { grantId: handle.grantId }));
});

test("desktop grant registration renews an expired grant through the real Python HTTP boundary", { timeout: 15000 }, async (t) => {
  const { spawn } = require("node:child_process");
  const { createInterface } = require("node:readline");
  const { base, inside, outside, security } = await fixture(t);
  const python = path.resolve(__dirname, "../../../.venv/bin/python");
  if (!fs.existsSync(python)) return t.skip("Run with the project Python development environment installed");
  const script = `
import json, sys, threading
from dataclasses import replace
from pathlib import Path
from gofer.ui import server
server.ensure_local_gofer_cli = lambda directory: Path(sys.executable)
http = server.GoferUiServer(("127.0.0.1", 0), Path(sys.argv[1]), api_token="fixture-token")
threading.Thread(target=http.serve_forever, daemon=True).start()
print(json.dumps({"port": http.server_address[1]}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("expire"):
        grant_id = request["grantId"]
        http.path_grants._grants[grant_id] = replace(http.path_grants._grants[grant_id], expires_at=0)
    print(json.dumps({"allowed": http.path_grants.covers(Path(request["path"]), request["grantId"])}), flush=True)
http.shutdown()
http.server_close()
`;
  const child = spawn(python, ["-u", "-c", script, path.join(base, "backend")], {
    cwd: path.resolve(__dirname, "../../.."),
    env: { ...process.env, GOFER_DESKTOP_GRANT_SECRET: "fixture-secret" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => child.kill());
  child.stderr.resume();
  const lines = createInterface({ input: child.stdout });
  const iterator = lines[Symbol.asyncIterator]();
  const ready = JSON.parse((await iterator.next()).value);
  const registrationContext = {
    getIpcSecurity: () => security,
    backendReady: Promise.resolve(),
    Date, fetch, AbortSignal, activeApiBaseUrl: `http://127.0.0.1:${ready.port}`,
    activeUiApiToken: "fixture-token", desktopGrantSecret: "fixture-secret", writeBackendLog: () => {},
  };
  let grantTime = 0;
  registrationContext.backendPathGrants = require("../path-grant-queue.cjs").createPathGrantQueue({ now: () => grantTime });
  registrationContext.sendBackendPathGrant = mainFunction("sendBackendPathGrant", registrationContext);
  const register = mainFunction("registerBackendPathGrant", registrationContext);
  const allowed = async (handle, expire = false) => {
    child.stdin.write(`${JSON.stringify({ ...handle, expire })}\n`);
    return JSON.parse((await iterator.next()).value).allowed;
  };
  const userFile = path.join(base, "user-note.md");
  await fsp.writeFile(userFile, "Local note");
  const userHandle = security.grantUserPath(userFile);
  await assert.rejects(register(userHandle), /does not grant agent access/);
  assert.equal(await allowed(userHandle), false);
  assert.throws(() => security.renewPath(userFile), /outside/);
  const handle = security.trustPath(outside);
  await register(handle);
  assert.equal(await allowed(handle), true);
  assert.equal(await allowed(handle, true), false);
  grantTime += 15 * 60 * 1000;
  await register(security.renewPath(outside));
  assert.equal(await allowed(handle), true);
  assert.throws(() => security.renewPath(path.dirname(inside)), /outside/);
  lines.close();
  child.stdin.end();
});

test("failed creation cannot redirect cleanup through a replaced parent on the portable path", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const parent = path.join(inside, "parent");
  await fsp.mkdir(parent);
  await fsp.writeFile(path.join(outside, "draft.txt"), "outside sentinel");
  const safeSource = fs.readFileSync(path.join(__dirname, "../safe-files.cjs"), "utf8");
  const module = { exports: {} };
  const fakeFs = { ...fs, promises: { ...fsp, async open(target, ...args) {
    const handle = await fsp.open(target, ...args);
    if (target !== path.join(parent, "draft.txt")) return handle;
    let replaced = false;
    return {
      async stat() {
        const result = await handle.stat();
        if (!replaced) {
          replaced = true;
          await fsp.rename(parent, path.join(inside, "moved"));
          await fsp.symlink(outside, parent, "dir");
        }
        return result;
      },
      close: () => handle.close(),
    };
  } } };
  vm.runInNewContext(safeSource, { module, process: { platform: "darwin" }, Buffer,
    require: (name) => name === "node:fs" ? fakeFs : name === "./security.cjs" ? require("../security.cjs") : require(name),
  });
  await assert.rejects(module.exports.writeFile(path.join(parent, "draft.txt"), "new", { authorize: security.resolveAllowedPath }), /replaced|Directory changed/);
  assert.equal(await fsp.readFile(path.join(outside, "draft.txt"), "utf8"), "outside sentinel");
});


test("grant renewal returns missing without rejecting IPC after a trusted folder is deleted", async (t) => {
  const { inside, outside, security } = await fixture(t);
  security.trustPath(outside);
  await fsp.rm(outside, { recursive: true });
  let registrations = 0;
  const grant = mainFunction("grantPath", {
    restoreBackendTrustedRoots: async () => {},
    getIpcSecurity: () => security,
    registerBackendPathGrant: async () => { registrations++; },
  });
  assert.equal((await grant(null, { targetPath: outside })).missing, true);
  assert.equal(registrations, 0);
  await fsp.mkdir(outside);
  assert.equal((await grant(null, { targetPath: outside })).path, outside);
  const untrusted = path.join(path.dirname(inside), "untrusted");
  await fsp.mkdir(untrusted);
  assert.equal((await grant(null, { targetPath: untrusted })).path, untrusted);
});

test("injected agent resolver distinguishes missing folders from access violations", async (t) => {
  const { outside, security } = await fixture(t);
  const handle = security.trustPath(outside);
  await fsp.rm(outside, { recursive: true });
  for (const name of ["gitStatus", "gitBranches", "gitHistory", "gitWorktrees", "listDirectory"]) {
    const read = mainFunction(name, {
      resolveExactPath: security.resolveAllowedPath,
      resolveGitProjectDirectory: async options => security.resolveAllowedPath(options.projectRoot, { grantId: options.grantId, mustExist: true }),
    });
    assert.equal((await read(null, { currentPath: outside, projectRoot: outside, grantId: handle.grantId })).missing, true);
    await fsp.mkdir(outside);
    await assert.rejects(read(null, { currentPath: outside, projectRoot: outside }), /outside/);
    await fsp.rm(outside, { recursive: true });
  }
});

test("user file navigation opens outside roots without granting agent access", async (t) => {
  const { inside, outside, security } = await fixture(t);
  const file = path.join(outside, "My notes.md");
  await fsp.writeFile(file, "# Local notes");
  const grant = mainFunction("grantUserPath", { getIpcSecurity: () => security });
  const handle = grant(null, { targetPath: file });
  assert.equal(handle.path, file);
  assert.equal(security.grantForPath(file), "");
  assert.throws(() => security.renewPath(file), /outside/);
  assert.equal(security.resolveAllowedPath(outside, { grantId: handle.grantId }), outside);
  assert.throws(() => security.resolveAllowedPath(path.dirname(outside), { grantId: handle.grantId }), /outside/);
  const pathHandle = mainFunction("pathHandle", { getIpcSecurity: () => security });
  const read = mainFunction("readTextFile", { fs, resolveExactPath: security.resolveAllowedPath, pathHandle });
  assert.equal((await read(null, { targetPath: file, grantId: handle.grantId })).content, "# Local notes");
  assert.equal(pathHandle(file).grantId, handle.grantId);
  const save = mainFunction("writeTextFile", { safeFiles, resolveExactPath: security.resolveAllowedPath, pathHandle });
  await save(null, { targetPath: file, grantId: handle.grantId, content: "Edited by the user" });
  assert.equal(await fsp.readFile(file, "utf8"), "Edited by the user");
  assert.throws(() => grant(null, { targetPath: path.join(outside, "missing") }), /does not exist/);
  assert.throws(() => grant(null, {}), /path is required/);
  // The desktop capability does not follow a link outside its directory.
  await fsp.unlink(file);
  const other = path.join(inside, "other.md");
  await fsp.writeFile(other, "other");
  await fsp.symlink(other, file);
  await assert.rejects(read(null, { targetPath: file, grantId: handle.grantId }), /outside/);
});

test("Rem, Markdown and HTML shared file opener requests a user grant and uses its canonical path", async (t) => {
  const { outside, security } = await fixture(t);
  const file = path.join(outside, "report.html");
  await fsp.writeFile(file, "<h1>Report</h1>");
  const link = path.join(outside, "shortcut.html");
  await fsp.symlink(file, link);
  const appSource = await fsp.readFile(path.join(__dirname, "../../src/pages/App.jsx"), "utf8");
  const start = appSource.indexOf("  async function openLinkedCodeFile(");
  const end = appSource.indexOf("  openLinkedCodeFileRef.current", start);
  const opened = [];
  const notices = [];
  const open = vm.runInNewContext(`(${appSource.slice(start, end).trim()})`, {
    window: { goferDesktop: { workspace: {
      grantUserPath: target => security.grantUserPath(target),
      trustProjectRoot: () => { throw new Error("must not grant agent access"); },
      getPathInfo: target => {
        security.resolveAllowedPath(target, { grantId: security.userGrantForPath(target), mustExist: true });
        return { path: target, isFile: true };
      },
    } } },
    openCodeFile: (...args) => opened.push(args),
    setTopBarNotice: notice => notices.push(notice),
  });
  await open(link, { lineNumber: 12, column: 3, preview: true });
  assert.equal(opened.length, 1);
  assert.equal(opened[0][0], file);
  assert.equal(opened[0][1].lineNumber, 12);
  assert.equal(notices.length, 0);
  assert.throws(() => security.renewPath(file), /outside/);
  await open(path.join(outside, "missing.md"));
  assert.equal(notices.length, 1);
  assert.equal(opened.length, 1);
  const recentStart = appSource.indexOf("  async function openRecentCodeFile(");
  const recentEnd = appSource.indexOf("  async function openAssistantFile(", recentStart);
  const recent = vm.runInNewContext(`(${appSource.slice(recentStart, recentEnd).trim()})`, { openLinkedCodeFile: open });
  await recent(file);
  assert.equal(opened.length, 2);
  assert.equal(opened[1][1].preview, false);
});

test("desktop Git and files work outside agent roots with absent or stale grants", async (t) => {
  const { outside, security } = await fixture(t);
  const { execFileSync } = require("node:child_process");
  const git = require("../git-status.cjs");
  execFileSync("git", ["init", "-b", "main", outside]);
  execFileSync("git", ["-C", outside, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "Desktop history"]);
  const file = path.join(outside, "draft.txt");
  await fsp.writeFile(file, "draft");
  const context = { fs, path, getIpcSecurity: () => security, ...git };
  context.resolveExactPath = mainFunction("resolveExactPath", context);
  context.resolveGitProjectDirectory = mainFunction("resolveGitProjectDirectory", context);
  context.pathHandle = mainFunction("pathHandle", context);
  context.entryPathHandle = mainFunction("entryPathHandle", context);
  context.registerBackendPathGrant = () => assert.fail("Desktop navigation must not grant agent access");
  for (const grantId of [undefined, "expired-grant"]) {
    const options = { projectRoot: outside, currentPath: outside, targetPath: file, grantId };
    const status = await mainFunction("gitStatus", context)(null, options);
    assert.equal(status.active, true);
    assert.equal(status.entries[0].path, "draft.txt");
    const history = await mainFunction("gitHistory", context)(null, options);
    assert.equal(history.active, true);
    assert.equal(history.commits.length, 1);
    assert.equal((await mainFunction("gitWorktrees", context)(null, options)).worktrees.length, 1);
    assert.equal((await mainFunction("listDirectory", context)(null, options)).directory, outside);
    assert.equal((await mainFunction("readTextFile", context)(null, options)).content, "draft");
  }
  assert.equal(security.grantForPath(outside), "");
  assert.throws(() => security.renewPath(outside), /outside/);
  assert.throws(() => security.resolveAllowedPath(outside), /outside/);
  await fsp.rm(outside, { recursive: true });
  for (const name of ["gitStatus", "gitBranches", "gitHistory", "gitWorktrees", "listDirectory"]) {
    assert.equal((await mainFunction(name, context)(null, { projectRoot: outside, currentPath: outside })).missing, true);
  }
});

test("desktop child paths retain name validation without requiring agent grants", async (t) => {
  const { outside, security } = await fixture(t);
  const resolve = mainFunction("resolveNewChildPath", { getIpcSecurity: () => security });
  assert.equal(resolve(outside, "new.txt", "expired"), path.join(outside, "new.txt"));
  assert.throws(() => resolve(outside, "../escape"), /plain file or folder name/);
  assert.equal(security.grantForPath(outside), "");
});


test("native selection, file project lookup and settings do not wait for backend grants", async (t) => {
  const { inside, outside, security } = await fixture(t);
  // A formerly trusted root must behave just like a newly visited directory.
  security.trustPath(outside);
  await fsp.mkdir(path.join(outside, ".git"));
  const file = path.join(outside, "readme.md");
  await fsp.writeFile(file, "hello");
  const context = {
    fs, path, LEGACY_BRANDS: [], getIpcSecurity: () => security,
    registerBackendPathGrant: () => assert.fail("User actions must not contact the backend"),
    mainWindow: null, resolvePickerDefaultPath: () => inside,
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: [file] }) },
    remSettings: () => ({}), app: { getPath: () => inside },
  };
  for (const name of ["resolveExactPath", "pathHandle", "nearestProjectRoot"]) context[name] = mainFunction(name, context);
  assert.equal((await mainFunction("selectPath", context)(null)).path, file);
  assert.equal((await mainFunction("grantDroppedPath", context)(null, { targetPath: file })).path, file);
  assert.equal((await mainFunction("resolveProjectFile", context)(null, { selectedPath: file })).directory, outside);
  for (const key of ["archiveFolder", "secondBrainRoot"]) {
    assert.equal((await mainFunction("configureRem", context)(null, { key, value: outside, grantId: "expired" }))[key], outside);
  }
});

test("a sibling worktree can be created, selected and checked out without prior approval", async (t) => {
  const { base, outside, security } = await fixture(t);
  const { execFileSync } = require("node:child_process");
  const git = require("../git-status.cjs");
  execFileSync("git", ["init", "-b", "main", outside]);
  execFileSync("git", ["-C", outside, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "Initial"]);
  execFileSync("git", ["-C", outside, "branch", "other"]);
  security.trustPath(outside);
  const sibling = path.join(base, "sibling-worktree");
  await fsp.mkdir(sibling);
  const context = { fs, path, ...git, getIpcSecurity: () => security,
    registerBackendPathGrant: () => assert.fail("Creating a worktree must not need the backend"),
  };
  for (const name of ["resolveExactPath", "resolveGitProjectDirectory", "pathHandle"]) context[name] = mainFunction(name, context);
  const created = await mainFunction("gitWorktreeAdd", context)(null, {
    projectRoot: outside, targetPath: sibling, branch: "feature", createBranch: true, grantId: "expired",
  });
  assert.equal(created.createdPath, sibling);
  assert.equal(security.isUserGrant(created.grantId), true);
  const registered = [];
  context.registerBackendPathGrant = async handle => registered.push(handle);
  const selected = await mainFunction("grantPath", context)(null, { targetPath: sibling });
  assert.equal(selected.path, sibling);
  assert.equal(registered.length, 1);
  assert.equal(security.isUserGrant(selected.grantId), false);
  await mainFunction("gitSwitchBranch", context)(null, { projectRoot: sibling, branch: "other", grantId: "expired" });
  assert.equal(execFileSync("git", ["-C", sibling, "branch", "--show-current"], { encoding: "utf8" }).trim(), "other");
});
