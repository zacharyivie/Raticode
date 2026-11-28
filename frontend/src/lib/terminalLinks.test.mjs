import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  createFileExistenceCheck,
  createTerminalHyperlinkHandler,
  createTerminalLinkProvider,
  fileLinkTargets,
  findFileLinkCandidates,
  findWebLinks,
  linkInsideProject,
  openTerminalFileLink,
} from "./terminalLinks.js";

const require = createRequire(import.meta.url);
// The desktop's own existence check, so "exists" means what it means in the app.
const { inspectPath, missingRecentFiles } = require("../../electron/path-info.cjs");
const { createIpcSecurity, realpathForContainment } = require("../../electron/security.cjs");

// The desktop's path info, as main.js answers it: symlinks followed, no grant made.
async function desktopPathInfo(targetPath) {
  return inspectPath(realpathForContainment(targetPath));
}

// A terminal buffer with one row per string, `cols` cells wide; a row that starts with
// "\u0000" continues the row before it (a wrapped line). By default every row fits, so a
// row holding a long temp folder path isn't cut off.
function fakeTerminal(rows, cols = Math.max(80, ...rows.map(row => row.length + 10))) {
  const lines = rows.map((row) => {
    const isWrapped = row.startsWith("\u0000");
    const text = (isWrapped ? row.slice(1) : row).padEnd(cols, " ");
    return {
      isWrapped,
      length: cols,
      getCell: (x) => ({ getChars: () => (text[x] === " " ? "" : text[x]), getWidth: () => 1 }),
    };
  });
  return { buffer: { active: { getLine: (y) => lines[y] } } };
}

function linksOn(provider, lineNumber) {
  return new Promise((resolve) => provider.provideLinks(lineNumber, links => resolve(links ?? [])));
}

function projectWithFile() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "raticode-terminal-links-")));
  fs.mkdirSync(path.join(root, "src"));
  fs.writeFileSync(path.join(root, "src", "a.py"), "one\ntwo\nthree\n");
  return root;
}

function linkProvider(terminal, root, calls, { platform = "Linux x86_64", cwd = root } = {}) {
  return createTerminalLinkProvider(terminal, {
    existingFiles: createFileExistenceCheck(missingRecentFiles),
    openFile: target => calls.push(["file", target]),
    openUrl: url => calls.push(["url", url]),
    place: () => ({ cwd, projectRoot: root }),
    platform,
  });
}

test("Ctrl+click opens a web link in the browser and a project file in the editor at its line; a plain click opens neither", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const terminal = fakeTerminal([
    "See https://example.com/x for details.",
    "Traceback: src/a.py:3 failed",
  ]);
  const calls = [];
  const provider = linkProvider(terminal, root, calls);

  const [web] = await linksOn(provider, 1);
  assert.equal(web.text, "https://example.com/x");
  assert.deepEqual(web.range, { start: { x: 5, y: 1 }, end: { x: 25, y: 1 } });
  const [file] = await linksOn(provider, 2);
  assert.equal(file.text, "src/a.py:3");
  assert.deepEqual(file.range, { start: { x: 12, y: 2 }, end: { x: 21, y: 2 } });

  web.activate({ ctrlKey: false, metaKey: false });
  file.activate({ ctrlKey: false, metaKey: false });
  assert.deepEqual(calls, [], "a plain click must not open anything");

  web.activate({ ctrlKey: true });
  file.activate({ ctrlKey: true });
  assert.deepEqual(calls, [
    ["url", "https://example.com/x"],
    ["file", { path: path.join(root, "src", "a.py"), projectRoot: root, lineNumber: 3 }],
  ]);
});

test("Cmd+click opens terminal links on macOS and Ctrl+click doesn't", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const provider = linkProvider(fakeTerminal(["open https://example.com/x"]), root, calls, { platform: "MacIntel" });
  const [web] = await linksOn(provider, 1);
  web.activate({ ctrlKey: true });
  assert.deepEqual(calls, []);
  web.activate({ metaKey: true });
  assert.deepEqual(calls, [["url", "https://example.com/x"]]);
});

test("paths in terminal output that don't exist or aren't files are not links", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const provider = linkProvider(fakeTerminal(["missing.py:4 src/nope.js:1:2 src/ and src/a.py"]), root, calls);
  const links = await linksOn(provider, 1);
  assert.deepEqual(links.map(link => link.text), ["src/a.py"]);
});

test("terminal file links resolve against the terminal's current folder before the project folder", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const provider = linkProvider(fakeTerminal(["./a.py:10:5 and ../src/a.py"]), root, calls, { cwd: path.join(root, "src") });
  const links = await linksOn(provider, 1);
  assert.deepEqual(links.map(link => link.text), ["./a.py:10:5", "../src/a.py"]);
  links[0].activate({ ctrlKey: true });
  assert.deepEqual(calls, [["file", { path: path.join(root, "src", "a.py"), projectRoot: root, lineNumber: 10, column: 5 }]]);
  assert.deepEqual(fileLinkTargets("x.py", { cwd: "/w/sub", projectRoot: "/w" }), ["/w/sub/x.py", "/w/x.py"]);
  assert.deepEqual(fileLinkTargets("/etc/../w/x.py", {}), ["/w/x.py"]);
});

test("only http and https addresses become web links, without the punctuation around them", () => {
  assert.deepEqual(findWebLinks("(see https://example.com/a_(b)), javascript:alert(1) file:///etc/passwd ftp://x").map(link => link.url), ["https://example.com/a_(b)"]);
  assert.deepEqual(findWebLinks("Go to https://example.com/x."), [{ start: 6, end: 27, url: "https://example.com/x" }]);
  assert.deepEqual(findWebLinks("https:// nothing"), []);
  const web = findWebLinks("https://example.com/src/a.py:3");
  assert.deepEqual(findFileLinkCandidates("https://example.com/src/a.py:3", web), []);
  assert.deepEqual(findFileLinkCandidates("version 1.2.3 and done. Also lib/x.js."), [{ start: 29, end: 37, path: "lib/x.js" }]);
});

test("a web link wrapped onto the next row is one link across both rows", async () => {
  const terminal = fakeTerminal(["xx https://example.com/", "\u0000long/path ok"], 23);
  const provider = createTerminalLinkProvider(terminal, { openFile() {}, openUrl() {}, place: () => ({}) });
  const [link] = await linksOn(provider, 2);
  assert.equal(link.text, "https://example.com/long/path");
  assert.deepEqual(link.range, { start: { x: 4, y: 1 }, end: { x: 9, y: 2 } });
});

test("hovering a terminal link names its target and the modifier that opens it", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const tips = [];
  const provider = createTerminalLinkProvider(fakeTerminal(["src/a.py:3:7"]), {
    existingFiles: createFileExistenceCheck(missingRecentFiles),
    onHover: (_event, text) => tips.push(text),
    onLeave: () => tips.push(null),
    openFile() {},
    openUrl() {},
    place: () => ({ cwd: root, projectRoot: root }),
    platform: "MacIntel",
  });
  const [link] = await linksOn(provider, 1);
  link.hover({});
  link.leave({});
  assert.deepEqual(tips, [`${path.join(root, "src", "a.py")}, line 3, column 7\nCmd+click to open`, null]);
});

test("OSC 8 hyperlinks from a program open only http and https, and only on Ctrl+click", () => {
  const opened = [];
  const handler = createTerminalHyperlinkHandler({ openUrl: url => opened.push(url), platform: "Linux" });
  handler.activate({ ctrlKey: false }, "https://example.com/x");
  handler.activate({ ctrlKey: true }, "javascript:alert(1)");
  handler.activate({ ctrlKey: true }, "file:///etc/passwd");
  handler.activate({ ctrlKey: true }, "https://example.com/x");
  assert.deepEqual(opened, ["https://example.com/x"]);
});

test("a terminal link to a file outside the open project asks before opening it", async () => {
  const asked = [];
  const opened = [];
  const open = async (filePath, options) => { opened.push([filePath, options]); };
  const outside = { path: "/home/someone/.bashrc", lineNumber: 2, projectRoot: "/work/project" };

  assert.equal(await openTerminalFileLink(outside, { confirm: async (request) => { asked.push(request); return false; }, open, projectRoots: ["/work/project"] }), false);
  assert.equal(opened.length, 0);
  assert.match(asked[0].message, /outside the open project/);
  assert.equal(asked[0].confirmLabel, "Open file");

  assert.equal(await openTerminalFileLink(outside, { confirm: async () => true, open, projectRoots: ["/work/project"] }), true);
  assert.deepEqual(opened, [["/home/someone/.bashrc", { fromLink: true, resolved: true, lineNumber: 2 }]]);

  asked.length = 0;
  opened.length = 0;
  await openTerminalFileLink({ path: "/work/project/src/a.py", lineNumber: 3, projectRoot: "/work/project" }, { confirm: async (request) => { asked.push(request); return false; }, open, projectRoots: [] });
  assert.deepEqual(asked, []);
  assert.deepEqual(opened, [["/work/project/src/a.py", { fromLink: true, resolved: true, lineNumber: 3 }]]);
});

test("a project file that is a symlink to a file outside the project asks about the real file, and Cancel grants nothing", async (t) => {
  const root = projectWithFile();
  const outside = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "raticode-terminal-links-outside-")));
  t.after(() => {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  });
  fs.mkdirSync(path.join(outside, ".ssh"));
  const key = path.join(outside, ".ssh", "id_ed25519");
  fs.writeFileSync(key, "secret\n");
  fs.symlinkSync(key, path.join(root, "notes.txt"));

  // The link shows up on the project's own name: it is a file that exists.
  const targets = [];
  const provider = createTerminalLinkProvider(fakeTerminal(["see notes.txt:1"]), {
    existingFiles: createFileExistenceCheck(missingRecentFiles),
    openFile: target => targets.push(target),
    openUrl() {},
    place: () => ({ cwd: root, projectRoot: root }),
    platform: "Linux",
  });
  const [link] = await linksOn(provider, 1);
  link.activate({ ctrlKey: true, preventDefault() {} });
  assert.equal(targets[0].path, path.join(root, "notes.txt"));

  // Opening it is what would grant the window the key's folder; Cancel never gets there.
  const security = createIpcSecurity({ getDataDir: () => root });
  const asked = [];
  const opened = [];
  const options = {
    open: async (filePath, openOptions) => { opened.push([filePath, openOptions]); security.grantUserPath(filePath); },
    projectRoots: [root],
    resolve: async filePath => (await desktopPathInfo(filePath)).path,
  };
  assert.equal(await openTerminalFileLink(targets[0], { ...options, confirm: async (request) => { asked.push(request); return false; } }), false);
  assert.equal(asked.length, 1, "the outside-the-project question is asked");
  assert.ok(asked[0].message.startsWith(`Open "${key}"?`), asked[0].message);
  assert.match(asked[0].message, /notes\.txt" leads to this file, which is outside the open project/);
  assert.deepEqual(opened, []);
  assert.equal(security.userGrantForPath(key), "", "no folder grant for the key");

  // Agreeing opens the real file, and only then is its folder granted.
  assert.equal(await openTerminalFileLink(targets[0], { ...options, confirm: async () => true }), true);
  assert.deepEqual(opened, [[key, { fromLink: true, resolved: true, lineNumber: 1 }]]);
  assert.notEqual(security.userGrantForPath(key), "");
});

test("a symlink inside the project to another project file, or a project opened through a symlink, opens without asking", async (t) => {
  const root = projectWithFile();
  const alias = path.join(os.tmpdir(), `raticode-terminal-links-alias-${process.pid}-${Date.now()}`);
  fs.symlinkSync(root, alias);
  t.after(() => {
    fs.rmSync(alias, { force: true });
    fs.rmSync(root, { recursive: true, force: true });
  });
  fs.symlinkSync(path.join(root, "src", "a.py"), path.join(root, "shortcut.py"));
  const resolve = async filePath => (await desktopPathInfo(filePath)).path;
  const asked = [];
  const opened = [];
  const confirm = async (request) => { asked.push(request); return false; };
  const open = async (filePath) => { opened.push(filePath); };

  await openTerminalFileLink({ path: path.join(alias, "shortcut.py"), projectRoot: alias }, { confirm, open, projectRoots: [alias], resolve });
  assert.deepEqual(asked, []);
  assert.deepEqual(opened, [path.join(root, "src", "a.py")]);
  assert.equal(await linkInsideProject(path.join(root, "src", "a.py"), [alias], resolve), true);
  assert.equal(await linkInsideProject("/etc/passwd", [alias, ""], resolve), false);
});

test("Windows paths in terminal output, with a drive or either slash, become links to the file at its line", async () => {
  assert.deepEqual(findFileLinkCandidates("at C:\\proj\\src\\a.py:3:5 and src\\a.py:42 or .\\x.jsx:10").map(({ path: found, lineNumber, column }) => [found, lineNumber, column]), [
    ["C:\\proj\\src\\a.py", 3, 5],
    ["src\\a.py", 42, undefined],
    [".\\x.jsx", 10, undefined],
  ]);
  assert.deepEqual(findFileLinkCandidates("\\\\server\\share\\file.txt"), [], "network paths are not links");
  const place = { cwd: "C:\\proj\\src", projectRoot: "C:\\proj" };
  assert.deepEqual(fileLinkTargets("a.py", place), ["C:\\proj\\src\\a.py", "C:\\proj\\a.py"]);
  assert.deepEqual(fileLinkTargets("..\\src/a.py", place), ["C:\\proj\\src\\a.py", "C:\\src\\a.py"]);
  assert.deepEqual(fileLinkTargets("\\tools\\b.ps1", place), ["C:\\tools\\b.ps1"]);
  assert.deepEqual(fileLinkTargets("D:/other/../c.txt", place), ["D:\\c.txt"]);
  // Outside Windows a backslash is an escape in output, not a folder separator.
  assert.deepEqual(fileLinkTargets("src\\a.py", { cwd: "/w", projectRoot: "/w" }), []);

  const calls = [];
  const existing = new Set(["C:\\proj\\src\\a.py"]);
  const provider = createTerminalLinkProvider(fakeTerminal(["Error in src\\a.py:3 here"]), {
    existingFiles: createFileExistenceCheck(async paths => paths.filter(item => !existing.has(item))),
    openFile: target => calls.push(target),
    openUrl() {},
    place: () => ({ cwd: "C:\\proj", projectRoot: "C:\\proj" }),
    platform: "Win32",
  });
  const [link] = await linksOn(provider, 1);
  assert.equal(link.text, "src\\a.py:3");
  link.activate({ ctrlKey: false });
  link.activate({ ctrlKey: true });
  assert.deepEqual(calls, [{ path: "C:\\proj\\src\\a.py", projectRoot: "C:\\proj", lineNumber: 3 }]);

  const asked = [];
  await openTerminalFileLink(calls[0], { confirm: async (request) => { asked.push(request); return false; }, open: async () => {}, projectRoots: ["c:\\Proj"] });
  assert.deepEqual(asked, [], "a file in the project, whatever the drive letter's case, opens without asking");
});

test("Windows paths under a user folder keep their line, and a relative path joins the Windows folder with backslashes", () => {
  assert.deepEqual(findFileLinkCandidates("C:\\Users\\ana\\proj\\src\\app.py:42"), [
    { start: 0, end: 31, path: "C:\\Users\\ana\\proj\\src\\app.py", lineNumber: 42 },
  ]);
  const [relative] = findFileLinkCandidates("src\\app.py:42:5");
  assert.deepEqual(relative, { start: 0, end: 15, path: "src\\app.py", lineNumber: 42, column: 5 });
  assert.deepEqual(fileLinkTargets(relative.path, { cwd: "C:\\Users\\ana\\proj" }), ["C:\\Users\\ana\\proj\\src\\app.py"]);
  assert.deepEqual(fileLinkTargets(".\\src\\app.py", { projectRoot: "C:/Users/ana/proj" }), ["C:\\Users\\ana\\proj\\src\\app.py"]);
});

test("Python tracebacks and file(line,column) output give the line and column on every platform", () => {
  const found = text => findFileLinkCandidates(text).map(({ path: item, lineNumber, column }) => [item, lineNumber, column]);
  assert.deepEqual(found('  File "/home/ana/app.py", line 42, in main'), [["/home/ana/app.py", 42, undefined]]);
  assert.deepEqual(findFileLinkCandidates('  File "/home/ana/app.py", line 42, in main'), [
    { start: 2, end: 34, path: "/home/ana/app.py", lineNumber: 42 },
  ], "the whole File \"…\", line N phrase is one link");
  assert.deepEqual(found('File "/home/ana/my project/app.py", line 7'), [["/home/ana/my project/app.py", 7, undefined]]);
  assert.deepEqual(found('File "C:\\Users\\ana\\app.py", line 3, in <module>'), [["C:\\Users\\ana\\app.py", 3, undefined]]);
  assert.deepEqual(found('File "<frozen runpy>", line 198'), [], "Python's own placeholders are not files");
  assert.deepEqual(found("src/app.ts(42,5): error TS2322: Type 'string' is not assignable."), [["src/app.ts", 42, 5]]);
  assert.deepEqual(found("C:\\proj\\Program.cs(10, 3): warning CS0168"), [["C:\\proj\\Program.cs", 10, 3]]);
  assert.deepEqual(found("> /home/ana/app.py(42)main()"), [["/home/ana/app.py", 42, undefined]]);
  assert.deepEqual(findFileLinkCandidates("src/app.ts(42,5): error"), [{ start: 0, end: 16, path: "src/app.ts", lineNumber: 42, column: 5 }]);
  assert.deepEqual(found("call app.py(x) here"), [["app.py", undefined, undefined]], "brackets without a number are not a line");
});

test("Ctrl+click on a Python traceback line opens the file at that line", async (t) => {
  const root = projectWithFile();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const file = path.join(root, "src", "a.py");
  const provider = linkProvider(fakeTerminal([`  File "${file}", line 2, in main`, "src/a.py(3,4): error"]), root, calls);
  const [traceback] = await linksOn(provider, 1);
  assert.equal(traceback.text, `File "${file}", line 2`);
  const [compiler] = await linksOn(provider, 2);
  assert.equal(compiler.text, "src/a.py(3,4)");
  traceback.activate({ ctrlKey: true });
  compiler.activate({ ctrlKey: true });
  assert.deepEqual(calls, [
    ["file", { path: file, projectRoot: root, lineNumber: 2 }],
    ["file", { path: file, projectRoot: root, lineNumber: 3, column: 4 }],
  ]);
});
