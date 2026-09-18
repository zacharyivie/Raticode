import assert from "node:assert/strict";
import { test } from "node:test";
import { inspectThreadScopes, threadIsArchived, THREAD_IDLE_MS, threadScopeKey } from "./threadActivity.js";

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
    gitBranches: async root => { if (root === "/denied") throw Error("Permission denied"); return { active: true, branches: ["main"] }; },
  });
  assert.deepEqual(calls, ["/gone", "/denied", "/repo"]);
  assert.deepEqual([...result.missingRoots], ["/gone"]);
  assert.deepEqual([...result.branches], [["/repo", ["main"]]]);
});

test("unborn current branches and failed branch listings are not treated as deleted", async () => {
  const entries = [{ projectRoot: "/repo", projectBranch: "main", updatedAt: new Date(now).toISOString() }];
  for (const status of [{ active: true, branch: "main", branches: [] }, { active: true, branch: "other", branches: [], branchesUnavailable: true }]) {
    const result = await inspectThreadScopes(entries, { gitBranches: async () => status });
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

test("equivalent Windows scopes share inspection and match canonical missing roots", async () => {
  const entries = ["C:\\Repo", "c:/repo/"].map(projectRoot => ({ projectRoot, projectBranch: "old", updatedAt: new Date(now).toISOString() }));
  let checked;
  const scopes = await inspectThreadScopes(entries, {
    missingThreadRoots: async roots => { checked = roots; return ["c:/repo"]; },
    gitBranches: async () => { throw new Error("Missing root should not be inspected"); },
  });
  assert.deepEqual(checked, ["C:\\Repo"]);
  assert.equal(threadIsArchived(entries[0], scopes.missingRoots, scopes.branches, now), true);
  assert.equal(threadIsArchived(entries[0], new Set(), new Map([["c:/repo", ["main"]]]), now), true);
});


test("scope keys skip archived and pinned roots and ignore ordering and duplicate roots", () => {
  const active = { projectRoot: "/repo", projectBranch: "main", updatedAt: new Date(now).toISOString() };
  assert.equal(threadScopeKey([active, { ...active, projectRoot: "/archived", archived: true }, { ...active, projectRoot: "/pinned", pinned: true }, active], now), threadScopeKey([active], now));
});

test("scope inspection shares in-flight requests, caches remounts and bounds branch concurrency", async () => {
  let calls = 0, running = 0, maximum = 0;
  const entries = Array.from({ length: 12 }, (_, i) => ({ projectRoot: `/repo/${i}`, projectBranch: true }));
  const workspace = { gitStatus: () => assert.fail("must not scan working trees"), gitBranches: async () => {
    calls++; maximum = Math.max(maximum, ++running);
    await new Promise(resolve => setTimeout(resolve, 1)); running--;
    return { active: true, branches: ["main"] };
  } };
  await Promise.all([inspectThreadScopes(entries, workspace), inspectThreadScopes(entries, workspace)]);
  await inspectThreadScopes(entries, workspace);
  assert.equal(calls, 12);
  assert.equal(maximum, 4);
  await inspectThreadScopes(entries, workspace, { force: true });
  assert.equal(calls, 24);
});
