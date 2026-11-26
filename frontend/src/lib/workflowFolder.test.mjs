import assert from "node:assert/strict";
import { test } from "node:test";
import { pendingRunWorktreeCount, runWorktreeDeleteNote, workflowFolderLabel } from "./workflowFolder.js";

test("workflows outside .raticode are labelled with their project folder", () => {
  assert.equal(workflowFolderLabel({ managed: false, relativeFolder: "rattish/starters/jev-demo" }), "rattish/starters/jev-demo");
  assert.equal(workflowFolderLabel({ managed: false, relativeFolder: "." }), "Project folder");
  assert.equal(workflowFolderLabel({ managed: true, relativeFolder: ".raticode/review" }), "");
  assert.equal(workflowFolderLabel({ id: "older-payload" }), "");
  assert.equal(workflowFolderLabel(null), "");
});

test("the delete confirmation names waiting run worktrees", async () => {
  const asked = [];
  const fetchImpl = async (address) => {
    asked.push(address);
    return { ok: true, json: async () => ({ pending: 2 }) };
  };
  const count = await pendingRunWorktreeCount("nightly review", { fetchImpl, url: (path) => `/api${path}` });
  assert.equal(count, 2);
  assert.deepEqual(asked, ["/api/workflows/nightly%20review/run-worktrees"]);
  assert.match(runWorktreeDeleteNote(2), /2 run worktrees still wait .* removes them and their branches/);
  assert.match(runWorktreeDeleteNote(1), /1 run worktree still waits .* removes it and its branch/);
  assert.equal(runWorktreeDeleteNote(0), "");
});

test("a failed run worktree lookup doesn't block the delete", async () => {
  const url = (path) => path;
  assert.equal(await pendingRunWorktreeCount("x", { url, fetchImpl: async () => ({ ok: false }) }), 0);
  assert.equal(await pendingRunWorktreeCount("x", { url, fetchImpl: async () => { throw new Error("offline"); } }), 0);
  assert.equal(await pendingRunWorktreeCount("x", { url, fetchImpl: async () => ({ ok: true, json: async () => ({}) }) }), 0);
});
