require("./test-tmp.cjs");
// The local HTML preview's request guard (browser-preview-guard.cjs), run
// directly with fake profiles, tabs and store: which requests it lets through,
// how it counts blocked remote content, and what "Load remote content" does.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL, fileURLToPath } = require("node:url");
const { createPreviewGuard } = require("../browser-preview-guard.cjs");

const GUARDED_URLS = ["file://*/*", "http://*/*", "https://*/*", "ws://*/*", "wss://*/*"];

function localFilePath(value) {
  try {
    const url = new URL(String(value ?? ""));
    return url.protocol === "file:" ? fileURLToPath(url) : "";
  } catch {
    return "";
  }
}

let nextContentsId = 100;

// A tab whose page is at `url`. setUrl stands in for the page navigating.
function tab(fields, url) {
  let current = url;
  const contents = { id: nextContentsId++, getURL: () => current };
  return { kind: "preview", contents, setUrl: (value) => { current = value; }, ...fields };
}

// A guard with fake profiles that capture the request listener each one gets.
function makeGuard({ tabs = [], allowed = new Set(), profiles } = {}) {
  const sessions = new Map(tabs.map((item, index) => [`tab-${index}`, item]));
  const listeners = {};
  const registered = {};
  const calls = { emitState: [], reload: [], moveToProfile: [], setPreviewRemote: [] };
  const fakeProfiles = profiles || {
    preview: {
      webRequest: {
        onBeforeRequest(filter, listener) {
          registered.preview = filter;
          listeners.preview = listener;
        },
      },
    },
  };
  const store = {
    previewRemoteAllowed: (file) => allowed.has(file),
    setPreviewRemote: (file, value) => {
      calls.setPreviewRemote.push([file, value]);
      if (value) allowed.add(file);
      else allowed.delete(file);
    },
  };
  const guard = createPreviewGuard({
    profile: (kind) => fakeProfiles[kind],
    sessions,
    store,
    liveContents: (session) => session?.contents || null,
    emitState: (session) => calls.emitState.push(session),
    reload: (session, ...rest) => calls.reload.push([session, ...rest]),
    moveToProfile: (session, online) => calls.moveToProfile.push([session, online]),
    sessionState: (session) => ({ state: session.kind }),
    localFilePath,
    resolvePreviewPath: () => { throw new Error("no grant"); },
  });
  return { guard, sessions, listeners, registered, calls, allowed };
}

// What the listener answers for one request.
function decide(listener, details) {
  let answer;
  listener(details, (value) => { answer = value; });
  assert.ok(answer, "the listener answered");
  return answer;
}

function withFolder(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "preview-guard-"));
  try {
    const folder = path.join(root, "report");
    fs.mkdirSync(folder);
    fs.writeFileSync(path.join(folder, "index.html"), "<p>report</p>");
    fs.writeFileSync(path.join(folder, "style.css"), "p {}");
    return run(fs.realpathSync(folder), root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("installPreviewRequestGuard registers the five guarded URL patterns", () => {
  const { guard, registered, listeners } = makeGuard();
  guard.installPreviewRequestGuard("preview");
  assert.deepEqual(registered.preview, { urls: GUARDED_URLS });
  assert.equal(typeof listeners.preview, "function");
});

test("installPreviewRequestGuard does nothing for a profile without webRequest", () => {
  const { guard } = makeGuard({ profiles: { preview: {}, other: { webRequest: {} } } });
  assert.doesNotThrow(() => guard.installPreviewRequestGuard("preview"));
  assert.doesNotThrow(() => guard.installPreviewRequestGuard("other"));
  assert.doesNotThrow(() => guard.installPreviewRequestGuard("missing"));
});

test("the listener allows a file in the preview's folder and refuses one outside it or from no open preview", () => {
  withFolder((folder) => {
    const page = pathToFileURL(path.join(folder, "index.html")).href;
    const preview = tab({ previewFolder: folder }, page);
    const { guard, listeners } = makeGuard({ tabs: [preview] });
    guard.installPreviewRequestGuard("preview");
    const listener = listeners.preview;
    const id = preview.contents.id;
    assert.deepEqual(decide(listener, { url: page, webContentsId: id }), { cancel: false }, "the page itself");
    assert.deepEqual(decide(listener, { url: pathToFileURL(path.join(folder, "style.css")).href, webContentsId: id }), { cancel: false });
    assert.deepEqual(decide(listener, { url: "file:///etc/passwd", webContentsId: id }), { cancel: true });
    assert.deepEqual(decide(listener, { url: page, webContentsId: id + 9999 }), { cancel: true }, "no open preview has this id");
    assert.deepEqual(decide(listener, { url: page }), { cancel: true }, "a request with no webContentsId");
    assert.deepEqual(decide(listener, { url: "https://example.com/", webContentsId: id + 9999 }), { cancel: true }, "remote, from no open preview");
  });
});

test("an offline preview's remote requests are cancelled, counted, and announced once per page", () => {
  withFolder((folder) => {
    const page = pathToFileURL(path.join(folder, "index.html")).href;
    const preview = tab({ previewFolder: folder, online: false }, page);
    const { guard, listeners, calls } = makeGuard({ tabs: [preview] });
    guard.installPreviewRequestGuard("preview");
    const id = preview.contents.id;
    assert.equal(guard.remoteBlockedCount(preview), 0);
    for (const url of ["https://example.com/a.js", "http://example.com/b.png", "wss://example.com/socket"]) {
      assert.deepEqual(decide(listeners.preview, { url, webContentsId: id }), { cancel: true }, url);
    }
    assert.equal(guard.remoteBlockedCount(preview), 3);
    assert.deepEqual(calls.emitState, [preview], "emitState fires on the first blocked request only");

    preview.setUrl(pathToFileURL(path.join(folder, "style.css")).href);
    assert.equal(guard.remoteBlockedCount(preview), 0, "a new page starts at 0");
    decide(listeners.preview, { url: "ws://example.com/socket", webContentsId: id });
    assert.equal(guard.remoteBlockedCount(preview), 1);
    assert.equal(calls.emitState.length, 2, "the new page's first blocked request announces again");
  });
});

test("an online project preview of an allowed file loads remote content", () => {
  withFolder((folder) => {
    const file = path.join(folder, "index.html");
    const preview = tab({ previewFolder: folder, online: true }, pathToFileURL(file).href);
    const { guard, listeners, calls } = makeGuard({ tabs: [preview], allowed: new Set([file]) });
    guard.installPreviewRequestGuard("preview");
    assert.equal(guard.remoteContentAllowed(preview), true);
    assert.deepEqual(decide(listeners.preview, { url: "https://example.com/", webContentsId: preview.contents.id }), { cancel: false });
    assert.equal(guard.remoteBlockedCount(preview), 0);
    assert.deepEqual(calls.emitState, []);
  });
});

test("remoteContentAllowed is false for an external preview even when the store allows the file", () => {
  withFolder((folder) => {
    const file = path.join(folder, "index.html");
    const preview = tab({ previewFolder: folder, online: true, external: true }, pathToFileURL(file).href);
    const { guard, listeners } = makeGuard({ tabs: [preview], allowed: new Set([file]) });
    assert.equal(guard.remoteContentAllowed(preview), false);
    assert.equal(guard.previewBelongsOnline(preview, file), false);
    guard.installPreviewRequestGuard("preview");
    assert.deepEqual(decide(listeners.preview, { url: "https://example.com/", webContentsId: preview.contents.id }), { cancel: true });
    // Neither an offline tab nor a non-preview tab counts as allowed.
    assert.equal(guard.remoteContentAllowed({ ...preview, external: false, online: false }), false);
    assert.equal(guard.remoteContentAllowed({ ...preview, external: false, kind: "user" }), false);
  });
});

test("setPreviewRemote refuses tabs that aren't project previews of a local file", () => {
  const { guard, calls } = makeGuard();
  assert.throws(() => guard.setPreviewRemote(tab({ kind: "user" }, "https://example.com/"), true), { message: "Only local HTML previews block remote content." });
  assert.throws(() => guard.setPreviewRemote(tab({ external: true }, "file:///home/me/page.html"), true), { message: "Files opened from another app never load remote content." });
  assert.throws(() => guard.setPreviewRemote(tab({}, "https://example.com/"), true), { message: "This tab isn't showing a local file." });
  assert.deepEqual(calls.setPreviewRemote, [], "nothing was remembered");
});

test("allowing remote content moves previews of the file to the online profile and reloads the rest", () => {
  withFolder((folder) => {
    const file = path.join(folder, "index.html");
    const page = pathToFileURL(file).href;
    const offline = tab({ previewFolder: folder, online: false, remoteBlocked: { url: page, count: 4 } }, page);
    const alreadyOnline = tab({ previewFolder: folder, online: true, remoteBlocked: { url: page, count: 1 } }, page);
    const otherFile = tab({ previewFolder: folder, online: false, remoteBlocked: { url: "x", count: 2 } }, pathToFileURL(path.join(folder, "style.css")).href);
    const external = tab({ previewFolder: folder, online: false, external: true, remoteBlocked: { url: page, count: 3 } }, page);
    const { guard, calls } = makeGuard({ tabs: [offline, alreadyOnline, otherFile, external] });

    assert.deepEqual(guard.setPreviewRemote(offline, true), { state: "preview" });
    assert.deepEqual(calls.setPreviewRemote, [[file, true]]);
    assert.deepEqual(calls.moveToProfile, [[offline, true]], "only the preview in the wrong profile moves");
    assert.deepEqual(calls.reload, [[alreadyOnline, false]], "the one already online loads the file again");
    assert.deepEqual(calls.emitState, [offline, alreadyOnline]);
    assert.equal(offline.remoteBlocked, null);
    assert.equal(alreadyOnline.remoteBlocked, null);
    assert.deepEqual(otherFile.remoteBlocked, { url: "x", count: 2 }, "a preview of another file is left alone");
    assert.deepEqual(external.remoteBlocked, { url: page, count: 3 }, "a file from another app is left alone");

    // Turning it off again moves the online one back and reloads the offline one.
    alreadyOnline.online = true;
    offline.online = false;
    guard.setPreviewRemote(alreadyOnline, false);
    assert.deepEqual(calls.setPreviewRemote.at(-1), [file, false]);
    assert.deepEqual(calls.moveToProfile.at(-1), [alreadyOnline, false]);
    assert.deepEqual(calls.reload.at(-1), [offline, false]);
  });
});

test("previewMayRead allows files in the preview's folder and refuses a symlink that leads out of it", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "preview-may-read-"));
  try {
    const folder = path.join(root, "report");
    const outside = path.join(root, "outside");
    fs.mkdirSync(folder);
    fs.mkdirSync(outside);
    fs.writeFileSync(path.join(folder, "index.html"), "<p>report</p>");
    fs.writeFileSync(path.join(outside, "secret.txt"), "private");
    fs.symlinkSync(path.join(outside, "secret.txt"), path.join(folder, "secret-link.txt"));
    fs.symlinkSync(outside, path.join(folder, "outside-link"));
    fs.symlinkSync(path.join(folder, "index.html"), path.join(folder, "inside-link.html"));
    const grantChecks = [];
    const { previewMayRead } = createPreviewGuard({
      profile: () => null,
      sessions: new Map(),
      store: {},
      localFilePath: () => "",
      resolvePreviewPath: (target, options) => { grantChecks.push(options); throw new Error(`outside the grant: ${target}`); },
    });
    const session = { kind: "preview", previewFolder: fs.realpathSync(folder) };
    assert.equal(previewMayRead(session, path.join(folder, "index.html")), true);
    assert.equal(previewMayRead(session, folder), true, "the preview's own folder");
    assert.equal(previewMayRead(session, path.join(folder, "inside-link.html")), true, "a link that stays inside");
    assert.equal(previewMayRead(session, path.join(folder, "secret-link.txt")), false, "a file link that leads out");
    assert.equal(previewMayRead(session, path.join(folder, "outside-link", "secret.txt")), false, "a folder link that leads out");
    assert.equal(previewMayRead(session, path.join(outside, "secret.txt")), false);
    assert.equal(previewMayRead(session, path.join(folder, "missing.html")), false, "a file that isn't there");
    assert.equal(previewMayRead({ kind: "preview" }, path.join(folder, "index.html")), false, "no grant and no folder reads nothing");
    // A grant is asked first; when it refuses, the folder rule still applies.
    assert.equal(previewMayRead({ ...session, grantId: "grant-1" }, path.join(folder, "secret-link.txt")), false);
    assert.deepEqual(grantChecks, [{ grantId: "grant-1", mustExist: true }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
