import assert from "node:assert/strict";
import { test } from "node:test";
import { expiredArchivedThreads, inspectThreadScopes, organizeThreads, threadIndexEntry, threadIsArchived, threadScopeKey } from "./threadActivity.js";

const now = Date.parse("2026-09-15T12:00:00Z");
const at = minutes => new Date(now - minutes * 60000).toISOString();
const ids = list => list.map(entry => entry.id);

test("threads group as pinned, unread, the seven most recently read, running and archived", () => {
  const read = Array.from({ length: 9 }, (_, i) => ({ id: `read-${i}`, updatedAt: at(100 + i) }));
  const entries = [
    ...read,
    { id: "reopened", updatedAt: at(500), readAt: at(1) },
    { id: "pin", pinned: true, updatedAt: at(900) },
    { id: "unread", unread: true, updatedAt: at(50) },
    { id: "dot", updatedAt: at(40) },
    { id: "busy", updatedAt: at(30), unread: true },
    { id: "shelved", archived: true, updatedAt: at(0) },
  ];
  const groups = organizeThreads(entries, { running: new Set(["busy"]), unread: new Set(["dot"]) });
  assert.deepEqual(ids(groups.pinned), ["pin"]);
  assert.deepEqual(ids(groups.unread), ["dot", "unread"]);
  assert.deepEqual(ids(groups.running), ["busy"]);
  assert.deepEqual(ids(groups.read), ["reopened", ...ids(read.slice(0, 6))]);
  assert.deepEqual(ids(groups.archived), ["shelved", "read-6", "read-7", "read-8"]);
});

test("reading another thread pushes the oldest read thread into the archive", () => {
  const entries = Array.from({ length: 7 }, (_, i) => ({ id: `t${i}`, updatedAt: at(10 + i) }));
  assert.equal(organizeThreads(entries).archived.length, 0);
  const next = [...entries, { id: "new", updatedAt: at(100), readAt: at(0) }];
  assert.deepEqual(ids(organizeThreads(next).archived), ["t6"]);
});

test("unreachable folders and deleted branches archive threads, including pinned and unread ones", () => {
  const thread = { id: "t", updatedAt: at(0), projectRoot: "/repo", projectBranch: "feature" };
  const group = (entry, scopes) => Object.entries(organizeThreads([entry], scopes)).find(([, list]) => list.length)[0];
  assert.equal(group(thread, {}), "read");
  assert.equal(group(thread, { missingRoots: new Set(["/repo"]) }), "archived");
  assert.equal(group({ ...thread, pinned: true }, { missingRoots: new Set(["/repo"]) }), "archived");
  assert.equal(group({ ...thread, unread: true }, { missingRoots: new Set(["/repo"]) }), "archived");
  assert.equal(group(thread, { branches: new Map([["/repo", ["main"]]]) }), "archived");
  assert.equal(group(thread, { branches: new Map([["/repo", ["main", "feature"]]]) }), "read");
  // A running turn keeps its thread visible until it finishes.
  assert.equal(group(thread, { running: new Set(["t"]), missingRoots: new Set(["/repo"]) }), "running");
  assert.equal(threadIsArchived(thread, new Set(["/repo"])), true);
  assert.equal(threadIsArchived({ id: "global", updatedAt: at(0) }, new Set([""])), false);
});

test("archived threads expire after the configured time without activity", () => {
  const archived = [{ id: "old", updatedAt: at(8 * 24 * 60) }, { id: "reread", updatedAt: at(8 * 24 * 60), readAt: at(60) }, { id: "undated" }];
  assert.deepEqual(expiredArchivedThreads(archived, "never", now), []);
  assert.deepEqual(ids(expiredArchivedThreads(archived, "7", now)), ["old"]);
  assert.deepEqual(expiredArchivedThreads(archived, "30", now), []);
});

test("index entries keep the fields needed to group threads without metadata reads", () => {
  assert.deepEqual(threadIndexEntry({ id: "t", title: "Hidden", updatedAt: "u", readAt: "r", unread: 1, pinned: 0, archived: true }), {
    id: "t", updatedAt: "u", projectRoot: "", projectBranch: undefined, managedRole: undefined,
    archived: true, pinned: false, unread: true, readAt: "r", scopeIndexed: true,
  });
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
    assert.equal(threadIsArchived(entries[0], result.missingRoots, result.branches), false);
  }
});

test("equivalent Windows scopes share inspection and match canonical missing roots", async () => {
  const entries = ["C:\\Repo", "c:/repo/"].map(projectRoot => ({ projectRoot, projectBranch: "old", updatedAt: new Date(now).toISOString() }));
  let checked;
  const scopes = await inspectThreadScopes(entries, {
    missingThreadRoots: async roots => { checked = roots; return ["c:/repo"]; },
    gitBranches: async () => { throw new Error("Missing root should not be inspected"); },
  });
  assert.deepEqual(checked, ["C:\\Repo"]);
  assert.equal(threadIsArchived(entries[0], scopes.missingRoots, scopes.branches), true);
  assert.equal(threadIsArchived(entries[0], new Set(), new Map([["c:/repo", ["main"]]])), true);
});

test("scope keys check pinned, unread and recent read roots but skip archived ones", () => {
  const recent = { id: "recent", projectRoot: "/repo", projectBranch: "main", updatedAt: at(0) };
  const key = threadScopeKey([recent, { ...recent, id: "a", projectRoot: "/archived", archived: true }, { ...recent, id: "r2" }]);
  assert.equal(key, threadScopeKey([recent]));
  const pinned = { id: "pin", projectRoot: "/pinned", pinned: true, updatedAt: at(9999) };
  assert.match(threadScopeKey([recent, pinned]), /\/pinned/);
  const old = Array.from({ length: 30 }, (_, i) => ({ id: `o${i}`, projectRoot: `/old/${i}`, updatedAt: at(10 + i) }));
  const checked = JSON.parse(threadScopeKey(old)).map(scope => scope.projectRoot);
  assert.equal(checked.length, 21);
  assert.equal(checked.includes("/old/29"), false);
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
