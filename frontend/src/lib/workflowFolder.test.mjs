import assert from "node:assert/strict";
import { test } from "node:test";
import { workflowFolderLabel, workflowFolderPath } from "./workflowFolder.js";

test("workflows outside .raticode are labelled with their project folder", () => {
  assert.equal(workflowFolderLabel({ managed: false, relativeFolder: "rattish/starters/jev-demo" }), "rattish/starters/jev-demo");
  assert.equal(workflowFolderLabel({ managed: false, relativeFolder: "." }), "Project folder");
  assert.equal(workflowFolderLabel({ managed: true, relativeFolder: ".raticode/review" }), "");
  assert.equal(workflowFolderLabel({ id: "older-payload" }), "");
  assert.equal(workflowFolderLabel(null), "");
});

test("the editor breadcrumb uses the real folder and falls back to .raticode", () => {
  assert.equal(workflowFolderPath({ id: "jev-demo", relativeFolder: "rattish/starters/jev-demo" }), "rattish/starters/jev-demo");
  assert.equal(workflowFolderPath({ id: "review", relativeFolder: ".raticode/review" }), ".raticode/review");
  assert.equal(workflowFolderPath({ id: "top", relativeFolder: "." }), "");
  assert.equal(workflowFolderPath({ id: "review" }), ".raticode/review");
});
