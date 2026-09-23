import assert from "node:assert/strict";
import test from "node:test";
import { createRecentProjectValidator, startWorkspacePolling } from "./projectRefresh.js";

const mainRoot = (_payload, root) => root;

test("switching among 15 recent projects reuses validation and checks only changed worktrees", async () => {
  let now = 0;
  const calls = { trust: 0, info: 0, git: 0 };
  const workspace = {
    trustProjectRoot: async () => { calls.trust++; },
    getPathInfo: async () => { calls.info++; return { isDirectory: true }; },
    gitWorktrees: async () => { calls.git++; return {}; },
  };
  const validator = createRecentProjectValidator({ now: () => now });
  const roots = Array.from({ length: 15 }, (_, i) => `/project-${i}`);
  const validateAll = (order) => Promise.all(order.map(root => validator.validate(root, root, workspace, mainRoot)));
  await validateAll(roots);
  assert.deepEqual(calls, { trust: 0, info: 15, git: 15 });
  for (const selected of [roots[0], roots[1], roots[0]]) {
    validator.remember(selected, selected);
    await validateAll([selected, ...roots.filter(root => root !== selected)]);
  }
  assert.equal(calls.git, 15, "A → B → A does not re-enumerate recent roots");
  await validator.validate(roots[0], "/new-worktree", workspace, mainRoot);
  assert.deepEqual(calls, { trust: 0, info: 61, git: 16 });
  now = 60_001;
  await validateAll(roots);
  assert.equal(calls.git, 31, "external changes are checked after the TTL");
});

test("validation shares pending requests, removes missing directories, and preserves transient errors", async () => {
  let calls = 0;
  const workspace = {
    getPathInfo: async root => {
      calls++;
      if (root === "/missing") throw new Error("No such file");
      if (root === "/busy") throw new Error("Temporary access failure");
      return { isDirectory: false };
    },
    gitWorktrees: () => { throw new Error("Git must not run before successful path validation"); },
  };
  const validator = createRecentProjectValidator();
  assert.deepEqual(await Promise.all([
    validator.validate("/missing", "/missing", workspace, mainRoot),
    validator.validate("/missing", "/missing", workspace, mainRoot),
  ]), [null, null]);
  assert.equal(calls, 1);
  assert.equal(await validator.validate("/file", "/file", workspace, mainRoot), null);
  assert.deepEqual(await validator.validate("/busy", "/busy", workspace, mainRoot), {
    mainProjectRoot: "/busy", selectedProjectRoot: "/busy",
  });
});

test("recent-project cache evicts old entries", async () => {
  let calls = 0;
  const workspace = { getPathInfo: async () => { calls++; return { isDirectory: true }; } };
  const validator = createRecentProjectValidator({ maxEntries: 2 });
  for (const root of ["/a", "/b", "/c", "/a"]) {
    await validator.validate(root, root, workspace, mainRoot);
  }
  assert.equal(calls, 4);
});

test("one idle minute keeps 30 live refreshes but only two discoveries and one doctor check", async () => {
  let now = 0;
  let id = 0;
  const timers = new Map();
  const target = new EventTarget();
  target.setTimeout = (fn, delay) => { timers.set(++id, { fn, due: now + delay }); return id; };
  target.clearTimeout = key => timers.delete(key);
  const documentTarget = new EventTarget();
  documentTarget.hidden = false;
  const calls = { live: 0, discover: 0, doctor: 0 };
  const originalNow = Date.now;
  Date.now = () => now;
  const stop = startWorkspacePolling({
    refreshLive: () => { calls.live++; },
    discover: () => { calls.discover++; },
    doctor: () => { calls.doctor++; },
  }, { target, documentTarget });
  try {
    while (true) {
      const next = [...timers.entries()].sort((a, b) => a[1].due - b[1].due)[0];
      if (!next || next[1].due > 60_000) break;
      now = next[1].due;
      timers.delete(next[0]);
      await next[1].fn();
    }
    assert.deepEqual(calls, { live: 30, discover: 2, doctor: 1 });
    target.dispatchEvent(new Event("focus"));
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls, { live: 31, discover: 3, doctor: 2 });
  } finally {
    stop();
    Date.now = originalNow;
  }
  assert.equal(timers.size, 0);
});


test("deleting a cached worktree falls back to main immediately, then removes a deleted project", async () => {
  const directories = new Set(["/main", "/feature"]);
  const gitCalls = [];
  const workspace = {
    trustProjectRoot: async root => {
      if (!directories.has(root)) throw new Error(`Path does not exist: ${root}`);
    },
    getPathInfo: async root => ({ isDirectory: directories.has(root) }),
    gitWorktrees: async root => { gitCalls.push(root); return {}; },
  };
  const validator = createRecentProjectValidator();
  validator.remember("/main", "/feature");
  directories.delete("/feature");
  assert.deepEqual(await validator.validate("/main", "/feature", workspace, mainRoot), {
    mainProjectRoot: "/main", selectedProjectRoot: "/main",
  });
  assert.deepEqual(gitCalls, ["/main"]);
  directories.delete("/main");
  assert.equal(await validator.validate("/main", "/main", workspace, mainRoot), null);
});

test("temporary failure does not forget the selected worktree", async () => {
  const validator = createRecentProjectValidator();
  assert.deepEqual(await validator.validate("/main", "/feature", {
    getPathInfo: async () => { throw new Error("Permission denied"); },
  }, mainRoot), { mainProjectRoot: "/main", selectedProjectRoot: "/feature" });
});


test("a deleted main folder is not retained by cached worktree metadata", async () => {
  const validator = createRecentProjectValidator();
  validator.remember("/main", "/feature");
  assert.deepEqual(await validator.validate("/main", "/feature", {
    getPathInfo: async root => ({ isDirectory: root === "/feature" }),
  }, mainRoot), { mainProjectRoot: "/feature", selectedProjectRoot: "/feature" });
});

test("equivalent Windows selections reuse remembered project metadata", async () => {
  const validator = createRecentProjectValidator();
  validator.remember("C:\\Repo", "C:\\Repo");
  let gitCalls = 0;
  const result = await validator.validate("c:/repo/", "c:/repo", {
    getPathInfo: async () => ({ isDirectory: true }),
    gitWorktrees: async () => { gitCalls++; return {}; },
  }, mainRoot);
  assert.equal(gitCalls, 0);
  assert.equal(result.mainProjectRoot, "C:\\Repo");
});
