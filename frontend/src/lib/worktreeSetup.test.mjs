import assert from "node:assert/strict";
import test from "node:test";

import { needsReview, setupCandidate, setupTitle, setupTone, stepLabel } from "./worktreeSetup.js";

const file = { text: '{"setup": "npm ci"}', sha256: "a".repeat(64), effective: { setup: ["npm ci"], link: [], copy: [] } };

test("the review offers the version a new worktree found before the project's file", () => {
  const pending = { text: '{"setup": "make"}', sha256: "b".repeat(64) };
  assert.equal(setupCandidate({ state: "unapproved", file, pending }), pending);
  assert.equal(setupCandidate({ state: "unapproved", file, pending: null }), file);
  assert.equal(setupCandidate({ state: "changed", file, pending: null, approved: { sha256: "c" } }), file);
  // Nothing to approve: already approved, no file, or an invalid file.
  assert.equal(setupCandidate({ state: "approved", file, pending: null }), null);
  assert.equal(setupCandidate({ state: "none", file: null, pending: null }), null);
  assert.equal(setupCandidate({ state: "invalid", file: { ...file, error: "bad" }, pending: null }), null);
  assert.equal(setupCandidate(null), null);
});

test("a run's setup result says what happened and when it needs the user", () => {
  const waiting = { status: "needs_approval", summary: "Project setup didn't run" };
  const earlier = { status: "passed", version: "approved-earlier" };
  const passed = { status: "passed", version: "current" };
  const failed = { status: "failed", version: "current" };
  assert.deepEqual([waiting, earlier, passed, failed].map(setupTone), ["attention", "attention", "success", "danger"]);
  assert.deepEqual([waiting, earlier, passed, failed].map(needsReview), [true, true, false, false]);
  assert.equal(setupTitle(waiting), "Project setup is waiting for your approval");
  assert.equal(setupTitle(earlier), "Ran the earlier approved setup");
  assert.equal(setupTitle(failed), "Project setup failed");
  assert.equal(stepLabel({ kind: "link", path: "node_modules" }), "Link node_modules");
  assert.equal(stepLabel({ kind: "command", command: "npm ci" }), "npm ci");
});
