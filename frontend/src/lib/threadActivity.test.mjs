import assert from "node:assert/strict";
import { test } from "node:test";
import { inspectThreadScopes, threadIsArchived, THREAD_IDLE_MS } from "./threadActivity.js";

const now = Date.parse("2026-09-15T12:00:00Z");
test("threads archive at ten days, or on workspace or saved branch deletion", () => {
  const thread = { updatedAt: new Date(now - THREAD_IDLE_MS + 1).toISOString(), projectRoot: "/repo", projectBranch: "feature" };
  assert.equal(threadIsArchived(thread, undefined, undefined, now), false);
  assert.equal(threadIsArchived(thread, undefined, undefined, now + 1), true);
  assert.equal(threadIsArchived(thread, new Set(["/repo"]), undefined, now), true);
  assert.equal(threadIsArchived(thread, undefined, new Map([["/repo", ["main"]]]), now), true);
  assert.equal(threadIsArchived(thread, undefined, new Map([["/repo", ["main", "feature"]]]), now), false);
  assert.equal(threadIsArchived({ ...thread, updatedAt: new Date(now).toISOString() }, undefined, undefined, now), false);
});

test("scope inspection deduplicates paths and does not treat denied access as deletion", async () => {
  const calls = [];
  const entries = ["/gone", "/denied", "/repo", "/repo"].map(projectRoot => ({ projectRoot, projectBranch: "old" }));
  const result = await inspectThreadScopes(entries, {
    getPathInfo: async root => { calls.push(root); if (root === "/denied") throw Error("Permission denied"); return { exists: root !== "/gone" }; },
    gitStatus: async root => { if (root === "/denied") throw Error("Permission denied"); return { active: true, branches: ["main"] }; },
  });
  assert.deepEqual(calls, ["/gone", "/denied", "/repo"]);
  assert.deepEqual([...result.missingRoots], ["/gone"]);
  assert.deepEqual([...result.branches], [["/repo", ["main"]]]);
});

test("unborn current branches and failed branch listings are not treated as deleted", async () => {
  const entries = [{ projectRoot: "/repo", projectBranch: "main", updatedAt: new Date(now).toISOString() }];
  for (const status of [{ active: true, branch: "main", branches: [] }, { active: true, branch: "other", branches: [], branchesUnavailable: true }]) {
    const result = await inspectThreadScopes(entries, { gitStatus: async () => status });
    assert.equal(threadIsArchived(entries[0], result.missingRoots, result.branches, now), false);
  }
});

test("pins bypass age, missing roots and deleted branches; explicit archives take precedence", () => {
  const thread = { pinned: true, updatedAt: "2020-01-01", projectRoot: "/gone", projectBranch: "deleted" };
  const missing = new Set(["/gone"]);
  const branches = new Map([["/gone", ["main"]]]);
  assert.equal(threadIsArchived(thread, missing, branches), false);
  assert.equal(threadIsArchived({ ...thread, pinned: false }, missing, branches), true);
  assert.equal(threadIsArchived({ ...thread, archived: true }, missing, branches), true);
  assert.equal(threadIsArchived({ updatedAt: new Date().toISOString(), archived: true }), true);
});
