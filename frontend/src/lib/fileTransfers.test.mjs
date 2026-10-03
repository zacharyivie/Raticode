import { test } from "node:test";
import assert from "node:assert/strict";
import { transferName, droppedFileEntries, containsFileTransfer, FILE_DRAG_TYPE, getFileClipboard, setFileClipboard, topLevelTransferEntries, beginFileDrag } from "./fileTransfers.js";

test("copies retain names unless occupied and folder names keep their dots", () => {
  const entry = { path: "/a/photo.png", name: "photo.png" };
  assert.equal(transferName(entry, "/b", new Set(), "copy"), "photo.png");
  assert.equal(transferName(entry, "/b", new Set(["photo.png", "photo copy.png"]), "copy"), "photo copy 2.png");
  assert.equal(transferName({ name: "my.folder", path: "/a/my.folder", isDirectory: true }, "/b", new Set(["my.folder"]), "copy"), "my.folder copy");
  assert.throws(() => transferName(entry, "/b", new Set(["photo.png"]), "move"), /already exists/);
  assert.equal(transferName(entry, "/a", new Set(["photo.png"]), "move"), null);
  assert.throws(() => transferName({ name: "a", path: "/a", isDirectory: true }, "/a/child", new Set(), "move"), /inside itself/);
});

test("native drops capture all paths before asynchronous inspection", async () => {
  let valid = true;
  const transfer = { types: ["Files"], files: [{ name: "one" }, { name: "two" }] };
  const desktop = { getDroppedFilePath: file => { assert.equal(valid, true); return `/outside/${file.name}`; }, workspace: { getPathInfo: async () => { valid = false; return { exists: true, isFile: true, isDirectory: false }; } } };
  assert.equal(containsFileTransfer(transfer), true);
  const { entries, internal } = await droppedFileEntries(transfer, desktop);
  assert.equal(internal, false); assert.deepEqual(entries.map(entry => entry.path), ["/outside/one", "/outside/two"]);
  assert.equal(containsFileTransfer({ types: ["text/x-raticode-tab"] }), false);
  assert.deepEqual(await droppedFileEntries({ getData: type => type === FILE_DRAG_TYPE ? JSON.stringify(entries) : "" }), { entries, internal: true });
});

test("the file clipboard survives changing project context and clears after a cut", () => {
  const entry = { path: "/project-a/file", name: "file", operation: "move" };
  setFileClipboard(entry);
  assert.equal(getFileClipboard(), entry);
  setFileClipboard(null); assert.equal(getFileClipboard(), null);
});

test("moves support keep both and replace and selected folders carry descendants once", () => {
  const entry = { path: "/a/photo.png", name: "photo.png" };
  assert.equal(transferName(entry, "/b", new Set(["photo.png"]), "move", "keep-both"), "photo copy.png");
  assert.equal(transferName(entry, "/b", new Set(["photo.png"]), "move", "replace"), "photo.png");
  const folder = { path: "/a/folder", name: "folder", isDirectory: true };
  assert.deepEqual(topLevelTransferEntries([entry, folder, { path: "/a/folder/file", name: "file" }, entry]), [entry, folder]);
  const values = new Map();
  beginFileDrag({ dataTransfer: { setData: (key, value) => values.set(key, value) } }, [entry, folder]);
  assert.deepEqual(JSON.parse(values.get(FILE_DRAG_TYPE)), [entry, folder]);
});
