const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createGitWatcher } = require("../git-watch.cjs");
const { readGitLocation, readGitWorktrees } = require("../git-status.cjs");

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], {
    encoding: "utf8",
    env: { ...process.env, GIT_AUTHOR_NAME: "Test", GIT_AUTHOR_EMAIL: "t@example.com", GIT_COMMITTER_NAME: "Test", GIT_COMMITTER_EMAIL: "t@example.com" },
  });
}

function repository(t) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "raticode-git-watch-")));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const root = path.join(base, "repo");
  fs.mkdirSync(root);
  git(root, "init", "-q", "-b", "main");
  fs.writeFileSync(path.join(root, "README.md"), "hello\n");
  git(root, "add", "README.md");
  git(root, "commit", "-q", "-m", "init");
  return { base, root };
}

function recorder(watcher, t) {
  const events = [];
  let waiting = null;
  t.after(() => watcher.closeAll());
  return {
    events,
    onChange: (kinds) => { events.push(kinds); waiting?.(); },
    async next(kind, timeout = 4000) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        const index = events.findIndex((kinds) => kinds.includes(kind));
        if (index >= 0) return events.splice(0, index + 1).at(-1);
        await new Promise((resolve) => { waiting = resolve; setTimeout(resolve, 50); });
      }
      assert.fail(`No ${kind} event. Saw ${JSON.stringify(events)}`);
    },
    async quiet(ms = 400) { await new Promise((resolve) => setTimeout(resolve, ms)); events.length = 0; },
  };
}

test("external git worktree add, remove and prune notify the source control view", async (t) => {
  const { base, root } = repository(t);
  const watcher = createGitWatcher({ locate: readGitLocation, debounceMs: 30, minIntervalMs: 0 });
  const events = recorder(watcher, t);
  const { mode } = await watcher.watch(root, { ownerId: 1, onChange: events.onChange });
  assert.equal(mode, "watch");

  // The first linked worktree creates .git/worktrees, which did not exist yet.
  git(root, "worktree", "add", "-q", "-b", "feature", path.join(base, "feature"));
  assert.ok((await events.next("worktrees")).includes("worktrees"));
  assert.deepEqual((await readGitWorktrees(root)).worktrees.map((item) => item.branch), ["main", "feature"]);

  await events.quiet();
  git(root, "worktree", "add", "-q", "-b", "second", path.join(base, "second"));
  await events.next("worktrees");

  await events.quiet();
  git(root, "worktree", "remove", path.join(base, "second"));
  await events.next("worktrees");
  assert.deepEqual((await readGitWorktrees(root)).worktrees.map((item) => item.branch), ["main", "feature"]);

  // A folder deleted outside Git disappears from Git's list when it is pruned.
  await events.quiet();
  fs.rmSync(path.join(base, "feature"), { recursive: true, force: true });
  git(root, "worktree", "prune");
  await events.next("worktrees");
  assert.deepEqual((await readGitWorktrees(root)).worktrees.map((item) => item.branch), ["main"]);
});

test("commits in another worktree and staging in this one notify without running git", async (t) => {
  const { base, root } = repository(t);
  const feature = path.join(base, "feature");
  git(root, "worktree", "add", "-q", "-b", "feature", feature);
  const watcher = createGitWatcher({ locate: readGitLocation, debounceMs: 30, minIntervalMs: 0 });
  const main = recorder(watcher, t);
  await watcher.watch(root, { ownerId: 1, onChange: main.onChange });
  const linked = recorder(watcher, t);
  await watcher.watch(feature, { ownerId: 1, onChange: linked.onChange });
  await main.quiet(); await linked.quiet();

  fs.writeFileSync(path.join(feature, "agent.txt"), "work\n");
  git(feature, "add", "agent.txt");
  await linked.next("index");
  git(feature, "commit", "-q", "-m", "agent work");
  // Branch refs are shared, so the main checkout's history and branches update too.
  await main.next("refs");

  await main.quiet(); await linked.quiet();
  fs.writeFileSync(path.join(root, "README.md"), "changed\n");
  git(root, "add", "README.md");
  await main.next("index");
  // The main checkout's index does not change a linked worktree's status.
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(linked.events.some((kinds) => kinds.includes("index")), false);
});

test("bursts of Git metadata writes are coalesced and rate limited", async (t) => {
  const { root } = repository(t);
  let clock = 0;
  const timers = [];
  const fakeTimers = {
    setTimeout: (callback, delay) => { const timer = { callback, at: clock + delay }; timers.push(timer); return timer; },
    clearTimeout: (timer) => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1); },
  };
  const advance = (ms) => {
    clock += ms;
    for (const timer of timers.filter((item) => item.at <= clock)) { fakeTimers.clearTimeout(timer); timer.callback(); }
  };
  const listeners = [];
  const fsImpl = { watch: (_target, _options, listener) => { listeners.push(listener); return { on() {}, close() {} }; } };
  const events = [];
  const watcher = createGitWatcher({ locate: readGitLocation, fsImpl, timers: fakeTimers, now: () => clock });
  await watcher.watch(root, { ownerId: 1, onChange: (kinds) => events.push(kinds) });
  const commonDir = listeners[0];
  for (const name of ["index.lock", "index", "HEAD", "packed-refs", "objects", "logs"]) commonDir("rename", name);
  advance(249);
  assert.equal(events.length, 0);
  advance(1);
  assert.deepEqual(events, [["index", "refs"]]);
  commonDir("rename", "worktrees");
  advance(250);
  // At most one refresh per second while agents keep writing.
  assert.equal(events.length, 1);
  advance(750);
  assert.deepEqual(events, [["index", "refs"], ["worktrees"]]);
  commonDir("change", "index.lock");
  advance(5000);
  assert.equal(events.length, 2);
});

test("an unavailable file watch falls back to a bounded stat check that sees worktree changes", async (t) => {
  const { base, root } = repository(t);
  const fsImpl = { ...fs, watch: () => { const error = new Error("inotify limit"); error.code = "ENOSPC"; throw error; } };
  const watcher = createGitWatcher({ locate: readGitLocation, fsImpl, debounceMs: 10, minIntervalMs: 0, fallbackMs: 60 });
  const events = recorder(watcher, t);
  const { mode } = await watcher.watch(root, { ownerId: 1, onChange: events.onChange });
  assert.equal(mode, "poll");
  await events.quiet(200);
  git(root, "worktree", "add", "-q", "-b", "feature", path.join(base, "feature"));
  await events.next("worktrees");
  await events.quiet(200);
  git(root, "worktree", "remove", path.join(base, "feature"));
  await events.next("worktrees");
});

test("watches close with their view, their window, and a per-window limit", async (t) => {
  const { root } = repository(t);
  const closed = [];
  const fsImpl = { watch: (target) => ({ on() {}, close: () => closed.push(target) }) };
  const watcher = createGitWatcher({ locate: readGitLocation, fsImpl });
  t.after(() => watcher.closeAll());
  const first = await watcher.watch(root, { ownerId: 1, onChange() {} });
  const other = await watcher.watch(root, { ownerId: 2, onChange() {} });
  assert.equal(watcher.size, 2);
  // Another window cannot stop this window's watch.
  watcher.unwatch(first.watchId, 2);
  assert.equal(watcher.size, 2);
  watcher.unwatch(first.watchId, 1);
  assert.equal(watcher.size, 1);
  assert.ok(closed.length >= 2);
  for (let index = 0; index < 20; index += 1) await watcher.watch(root, { ownerId: 3, onChange() {} });
  assert.equal(watcher.size, 17);
  watcher.closeOwner(3);
  assert.equal(watcher.size, 1);
  watcher.unwatch(other.watchId, 2);
  assert.equal(watcher.size, 0);
});

test("Raticode's own worktree operations notify every view of the same repository", async (t) => {
  const { base, root } = repository(t);
  const feature = path.join(base, "feature");
  git(root, "worktree", "add", "-q", "-b", "feature", feature);
  const fsImpl = { watch: () => ({ on() {}, close() {} }) };
  const watcher = createGitWatcher({ locate: readGitLocation, fsImpl, debounceMs: 10, minIntervalMs: 0 });
  const main = recorder(watcher, t);
  const linked = recorder(watcher, t);
  const unrelated = recorder(watcher, t);
  await watcher.watch(root, { ownerId: 1, onChange: main.onChange });
  await watcher.watch(feature, { ownerId: 1, onChange: linked.onChange });
  const other = repository(t);
  await watcher.watch(other.root, { ownerId: 1, onChange: unrelated.onChange });
  await watcher.notify(feature, ["worktrees"]);
  await main.next("worktrees");
  await linked.next("worktrees");
  assert.equal(unrelated.events.length, 0);
});
