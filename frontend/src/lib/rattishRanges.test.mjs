import assert from "node:assert/strict";
import { test } from "node:test";
import { diagnosticMessage, diagnosticToMarker } from "./rattishRanges.js";

const monaco = { MarkerSeverity: { Warning: 4, Info: 2, Error: 8 } };
const model = {
  getLineMaxColumn: () => 20,
  getPositionAt: (offset) => ({ lineNumber: 1, column: offset + 1 }),
};

test("editor markers show the compiler's fix hints under the error", () => {
  const marker = diagnosticToMarker(monaco, model, "type: write_file", {
    severity: "error",
    message: "Node type must be a Rattish identifier.",
    span: { start: { offset: 6 }, end: { offset: 16 } },
    suggestions: ["Did you mean 'write-file'? Rattish names use hyphens, not underscores."],
  });
  assert.equal(
    marker.message,
    "Node type must be a Rattish identifier.\nDid you mean 'write-file'? Rattish names use hyphens, not underscores.",
  );
  assert.equal(marker.severity, 8);
});

test("a diagnostic without hints keeps its message, and blank hints are dropped", () => {
  assert.equal(diagnosticMessage({ message: "Route target 'x' does not name a Node." }), "Route target 'x' does not name a Node.");
  assert.equal(diagnosticMessage({ message: "Bad.", suggestions: ["", "  ", 3] }), "Bad.");
  assert.equal(diagnosticMessage({}), "Rattish diagnostic");
});
