import assert from "node:assert/strict";
import { test } from "node:test";
import { searchMatchOffsets } from "./searchHighlight.js";

test("search highlighting treats query punctuation literally and finds repeated mixed-case matches", () => {
  const text = "Use [a+b] then [A+B], not aab";
  assert.deepEqual(searchMatchOffsets(text, " [a+b] ").map(([start, end]) => text.slice(start, end)), ["[a+b]", "[A+B]"]);
  assert.deepEqual(searchMatchOffsets(text, "  "), []);
});

test("normalized matches retain original Unicode offsets", () => {
  const text = "😀 ﬃ ＮＥＥＤＬＥ cafe\u0301";
  assert.deepEqual(searchMatchOffsets(text, "ffi"), [[3, 4]]);
  assert.deepEqual(searchMatchOffsets(text, "needle"), [[5, 11]]);
  assert.deepEqual(searchMatchOffsets(text, "café"), [[12, 17]]);
  assert.deepEqual(searchMatchOffsets("ΟΣ", "ος"), [[0, 2]]);
});
