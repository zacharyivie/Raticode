import assert from "node:assert/strict";
import test from "node:test";

import { canDecide, decisionText, worktreeStateText } from "./runChanges.js";

const worktree = { runId: "r1", branch: "raticode/run/fix-r1", baseBranch: "main", filesChanged: 2 };

test("a worktree run's Changes tab says where its changes stand", () => {
  assert.equal(worktreeStateText({ ...worktree, state: "pending" }), "2 files changed on raticode/run/fix-r1, waiting for you to land or discard them.");
  assert.equal(worktreeStateText({ ...worktree, state: "pending", filesChanged: 1 }), "1 file changed on raticode/run/fix-r1, waiting for you to land or discard it.");
  assert.equal(worktreeStateText({ ...worktree, state: "no_changes" }), "This run changed nothing, so Raticode removed its worktree and branch.");
  assert.equal(worktreeStateText({ ...worktree, state: "landed", landedCommit: "abcdef0123456789" }), "Landed in main as abcdef01. Its worktree and branch were removed.");
  assert.equal(worktreeStateText({ ...worktree, state: "active" }), "Running in its own worktree on raticode/run/fix-r1.");
  assert.equal(worktreeStateText(null), "");
  // Only waiting changes can be landed or discarded.
  assert.deepEqual(["pending", "active", "landed", "discarded", "no_changes"].map(state => canDecide({ state })), [true, false, false, false, false]);
});

test("Land and Discard report what they did", () => {
  assert.equal(decisionText({ landed: true, how: "fast_forward", branch: "b", baseBranch: "main" }), "Landed b in main (fast-forward). Nothing was pushed.");
  assert.equal(decisionText({ landed: true, how: "clean", branch: "b", baseBranch: "main" }), "Landed b in main with a merge commit. Nothing was pushed.");
  assert.equal(decisionText({ discarded: true, branch: "b" }), "Discarded b and its worktree.");
  assert.equal(decisionText({ conflict: true, message: "b conflicts" }), "b conflicts");
  assert.equal(decisionText(null), "");
});
