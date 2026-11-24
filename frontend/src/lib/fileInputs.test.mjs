import assert from "node:assert/strict";
import test from "node:test";

import { describeFileInput, fileAccepted, fileInputNames, fileInputProblem, formatFileSize, workflowsForFile } from "./fileInputs.js";

const spec = { type: "file", file: { accept: [".pdf", "image/*"], maxSize: 2048 } };

test("file inputs are found on workflows in the same project", () => {
  const withFile = { id: "a", projectRoot: "/p/", inputs: { doc: spec, note: { type: "string" } } };
  const other = { id: "b", projectRoot: "/q", inputs: { doc: spec } };
  const none = { id: "c", projectRoot: "/p", inputs: { note: { type: "string" } } };
  assert.deepEqual(fileInputNames(withFile), ["doc"]);
  assert.deepEqual(workflowsForFile([withFile, other, none], "/p").map((item) => [item.workflow.id, item.input]), [["a", "doc"]]);
});

test("accept patterns and size limits match the backend's rules", () => {
  assert.ok(fileAccepted([".pdf", "image/*"], "Scan.PDF"));
  assert.ok(fileAccepted([".pdf", "image/*"], "photo", "image/png"));
  assert.ok(!fileAccepted([".pdf", "image/*"], "notes.txt", "text/plain"));
  assert.equal(fileInputProblem(spec, { name: "a.pdf", size: 10, type: "application/pdf" }), "");
  assert.match(fileInputProblem(spec, { name: "a.pdf", size: 4096 }), /4 KB, more than the 2 KB/);
  assert.match(fileInputProblem(spec, { name: "a.txt", size: 1, type: "text/plain" }), /isn't a kind of file/);
  assert.equal(fileInputProblem(spec, { name: "unknown", size: 1, type: "" }), "", "the run checks a type the window can't see");
  assert.equal(describeFileInput({ type: "file" }), "Any file up to 20 MB");
  assert.equal(formatFileSize(1536), "1.5 KB");
});
