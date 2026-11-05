import assert from "node:assert/strict";
import test from "node:test";
import { fileKind, previewMode, previewText } from "./fileKinds.js";

test("attachments are grouped by type, with a short label for their tile", () => {
  assert.deepEqual(fileKind("Screen Shot.PNG"), { kind: "image", label: "PNG" });
  assert.deepEqual(fileKind("clip.mov"), { kind: "video", label: "MOV" });
  assert.deepEqual(fileKind("report.pdf"), { kind: "pdf", label: "PDF" });
  assert.deepEqual(fileKind("data.xlsx"), { kind: "sheet", label: "XLSX" });
  assert.deepEqual(fileKind("app.tsx"), { kind: "code", label: "TSX" });
  assert.deepEqual(fileKind("backup.tar.gz"), { kind: "archive", label: "GZ" });
  assert.deepEqual(fileKind("deck.pptx"), { kind: "document", label: "PPTX" });
  // Without a usable extension the media type decides; otherwise it's a plain file.
  assert.deepEqual(fileKind("image", "image/png"), { kind: "image", label: "IMAGE" });
  assert.deepEqual(fileKind("Makefile"), { kind: "file", label: "FILE" });
  assert.deepEqual(fileKind("archive.verylongext"), { kind: "file", label: "FILE" });
});

test("previews show media, text or a card", () => {
  assert.equal(previewMode("a.png"), "image");
  assert.equal(previewMode("a.mp3"), "audio");
  assert.equal(previewMode("notes.md"), "text");
  assert.equal(previewMode("rows.csv"), "text");
  assert.equal(previewMode("config.yaml"), "text");
  assert.equal(previewMode("readme", "text/plain"), "text");
  assert.equal(previewMode("rows.xlsx"), "other");
  assert.equal(previewMode("report.pdf"), "other");
  assert.equal(previewText(new TextEncoder().encode("hello")).text, "hello");
  assert.equal(previewText(new Uint8Array([104, 0, 105])), null);
  assert.equal(previewText(new TextEncoder().encode("abcdef"), 3).truncated, true);
});
