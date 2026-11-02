import { test } from "node:test";
import assert from "node:assert/strict";
import { askRemAboutFiles, copiedFilePaths, copyFilesForPaste, readFileAttachments, REM_ATTACH_FILES_EVENT } from "./fileAttachments.js";
import { getFileClipboard, setFileClipboard } from "./fileTransfers.js";

test("copied files paste as attachments only while their paths are still on the clipboard", async () => {
  const written = [];
  const entries = [{ path: "/p/a.png", name: "a.png", isFile: true }, { path: "/p/docs", name: "docs", isDirectory: true }];
  await copyFilesForPaste(entries, { writeText: async text => written.push(text) });
  assert.deepEqual(written, ["/p/a.png\n/p/docs"]);
  assert.equal(getFileClipboard().operation, "copy");
  // Folders can't be attached, and CRLF from Windows clipboards still matches.
  assert.deepEqual(copiedFilePaths("/p/a.png\r\n/p/docs"), ["/p/a.png"]);
  assert.deepEqual(copiedFilePaths("something copied later"), []);
  assert.deepEqual(copiedFilePaths(""), []);
  setFileClipboard({ ...getFileClipboard(), operation: "move" });
  assert.deepEqual(copiedFilePaths("/p/a.png\n/p/docs"), []);
  setFileClipboard(null);
});

test("files read from disk keep their bytes, name and type", async () => {
  const bytes = [137, 80, 78, 71, 0, 255];
  const desktop = { textFiles: { readAttachment: async path => ({ name: path.split("/").at(-1), type: "image/png", data: Buffer.from(bytes).toString("base64") }) } };
  const [file] = await readFileAttachments(["/p/a.png"], desktop);
  assert.equal(file.name, "a.png");
  assert.equal(file.type, "image/png");
  assert.deepEqual([...new Uint8Array(await file.arrayBuffer())], bytes);
  await assert.rejects(readFileAttachments(["/p/a.png"], {}), /Restart the desktop app/);
});

test("asking Rem about files sends their paths", () => {
  const previous = globalThis.window;
  const events = [];
  globalThis.window = { dispatchEvent: event => events.push(event) };
  try {
    askRemAboutFiles(["/p/a.py", ""]);
    askRemAboutFiles([]);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, REM_ATTACH_FILES_EVENT);
    assert.deepEqual(events[0].detail, { paths: ["/p/a.py"] });
  } finally { globalThis.window = previous; }
});
