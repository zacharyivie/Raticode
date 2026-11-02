const fs = require("node:fs");
const path = require("node:path");

// Git writes several metadata files per command. Coalesce them into one event,
// and keep busy agents from refreshing the panel more than once a second.
const DEBOUNCE_MS = 250;
const MIN_INTERVAL_MS = 1000;
// Used only when the platform refuses file watches, for example at the inotify limit.
const FALLBACK_INTERVAL_MS = 5000;
const MAX_WATCHES_PER_OWNER = 16;

// Only metadata names change what the panel shows. Objects, logs and locks do not.
function commonDirKind(name, ownsIndex) {
  if (name === "worktrees") return "worktrees";
  if (["HEAD", "packed-refs", "refs", "ORIG_HEAD", "MERGE_HEAD", "rebase-merge", "rebase-apply", "config"].includes(name)) return "refs";
  // A linked worktree's changes are in its own index, not the main checkout's.
  if (name === "index" && ownsIndex) return "index";
  return "";
}

function gitDirKind(name) {
  if (["HEAD", "ORIG_HEAD", "MERGE_HEAD", "rebase-merge", "rebase-apply"].includes(name)) return "refs";
  if (name === "index") return "index";
  return "";
}

function createGitWatcher({
  locate, fsImpl = fs, timers = globalThis, now = Date.now,
  debounceMs = DEBOUNCE_MS, minIntervalMs = MIN_INTERVAL_MS, fallbackMs = FALLBACK_INTERVAL_MS,
} = {}) {
  const subscriptions = new Map();
  let nextId = 0;

  function emit(subscription, kinds) {
    if (subscription.closed || !kinds.length) return;
    for (const kind of kinds) subscription.pending.add(kind);
    if (subscription.timer) return;
    const delay = Math.max(debounceMs, subscription.lastEmit + minIntervalMs - now());
    subscription.timer = timers.setTimeout(() => {
      subscription.timer = undefined;
      if (subscription.closed || !subscription.pending.size) return;
      const changed = [...subscription.pending].sort();
      subscription.pending.clear();
      subscription.lastEmit = now();
      try { subscription.onChange(changed, subscription.id); } catch { /* A closed window must not stop other listeners. */ }
    }, delay);
  }

  function watchPath(subscription, target, classify, options = {}) {
    const watcher = fsImpl.watch(target, { persistent: false, ...options }, (_type, filename) => {
      if (filename && String(filename).endsWith(".lock")) return;
      const name = filename ? String(filename).split(/[\\/]/)[0] : "";
      // Some platforms omit the name. Treat that as any metadata change.
      const kind = name ? classify(name) : "all";
      if (kind === "all") emit(subscription, ["index", "refs", "worktrees"]);
      else if (kind) emit(subscription, [kind]);
      if (name === "worktrees" || !name) armWorktrees(subscription);
    });
    watcher.on("error", () => fallBack(subscription));
    subscription.watchers.add(watcher);
    return watcher;
  }

  // Git creates .git/worktrees with the first linked worktree and prune may delete it.
  function armWorktrees(subscription) {
    if (subscription.closed || subscription.mode !== "watch") return;
    if (subscription.worktreesWatcher) {
      subscription.watchers.delete(subscription.worktreesWatcher);
      subscription.worktreesWatcher.close();
      subscription.worktreesWatcher = undefined;
    }
    try { subscription.worktreesWatcher = watchPath(subscription, path.join(subscription.commonDir, "worktrees"), () => "worktrees"); }
    catch (error) { if (error.code !== "ENOENT" && error.code !== "ENOTDIR") fallBack(subscription); }
  }

  function closeWatchers(subscription) {
    for (const watcher of subscription.watchers) {
      try { watcher.close(); } catch { /* Already closed. */ }
    }
    subscription.watchers.clear();
    subscription.worktreesWatcher = undefined;
  }

  async function signature(subscription) {
    const stat = async (target) => {
      try { const info = await fsImpl.promises.stat(target); return `${info.mtimeMs}:${info.size}`; }
      catch { return "-"; }
    };
    const list = async (directory, depth = 0) => {
      let entries;
      try { entries = await fsImpl.promises.readdir(directory, { withFileTypes: true }); }
      catch { return []; }
      const names = [];
      for (const entry of entries) {
        const target = path.join(directory, entry.name);
        names.push(`${entry.name}:${await stat(target)}`);
        if (entry.isDirectory() && depth < 4) names.push(...await list(target, depth + 1));
      }
      return names.sort();
    };
    const { commonDir, gitDir } = subscription;
    const worktrees = (await list(path.join(commonDir, "worktrees"))).filter((name) => !name.includes(".lock")).join("|");
    const refs = [
      await stat(path.join(commonDir, "HEAD")),
      await stat(path.join(commonDir, "packed-refs")),
      await stat(path.join(gitDir, "HEAD")),
      ...await list(path.join(commonDir, "refs", "heads")),
    ].join("|");
    return { worktrees, refs, index: await stat(path.join(gitDir, "index")) };
  }

  // A bounded stat check of a few metadata files. It never runs git.
  function fallBack(subscription) {
    if (subscription.closed || subscription.mode === "poll") return;
    closeWatchers(subscription);
    subscription.mode = "poll";
    let previous;
    const check = async () => {
      if (subscription.closed) return;
      const next = await signature(subscription);
      if (subscription.closed) return;
      if (previous) emit(subscription, Object.keys(next).filter((kind) => next[kind] !== previous[kind]));
      previous = next;
      subscription.poll = timers.setTimeout(check, fallbackMs);
    };
    void check();
  }

  function close(subscription) {
    subscription.closed = true;
    closeWatchers(subscription);
    timers.clearTimeout(subscription.timer);
    timers.clearTimeout(subscription.poll);
    subscriptions.delete(subscription.id);
  }

  return {
    async watch(projectRoot, { ownerId, onChange }) {
      const owned = [...subscriptions.values()].filter((item) => item.ownerId === ownerId);
      // A renderer that forgets to stop old watches loses its oldest one, not new ones.
      if (owned.length >= MAX_WATCHES_PER_OWNER) close(owned[0]);
      const { gitDir, commonDir } = await locate(projectRoot);
      const subscription = {
        id: `git-watch-${++nextId}`, ownerId, onChange, projectRoot, gitDir: path.resolve(gitDir), commonDir: path.resolve(commonDir),
        watchers: new Set(), pending: new Set(), lastEmit: -Infinity, mode: "watch", closed: false,
      };
      subscriptions.set(subscription.id, subscription);
      try {
        const ownsIndex = subscription.gitDir === subscription.commonDir;
        watchPath(subscription, subscription.commonDir, (name) => commonDirKind(name, ownsIndex));
        if (!ownsIndex) watchPath(subscription, subscription.gitDir, gitDirKind);
        const heads = path.join(subscription.commonDir, "refs", "heads");
        // Branch names with slashes are folders. Fall back to the top level where
        // recursive watches are unsupported; packed-refs and HEAD still report commits.
        try { watchPath(subscription, heads, () => "refs", { recursive: true }); }
        catch (error) { if (error.code !== "ENOENT") watchPath(subscription, heads, () => "refs"); }
        armWorktrees(subscription);
      } catch {
        fallBack(subscription);
      }
      return { watchId: subscription.id, mode: subscription.mode };
    },
    unwatch(watchId, ownerId) {
      const subscription = subscriptions.get(watchId);
      if (subscription && subscription.ownerId === ownerId) close(subscription);
    },
    closeOwner(ownerId) {
      for (const subscription of [...subscriptions.values()]) if (subscription.ownerId === ownerId) close(subscription);
    },
    closeAll() {
      for (const subscription of [...subscriptions.values()]) close(subscription);
    },
    // Raticode's own Git operations report changes directly, so every open
    // view of the repository updates even when file watching is unavailable.
    async notify(projectRoot, kinds) {
      let commonDir;
      try { commonDir = path.resolve((await locate(projectRoot)).commonDir); } catch { return; }
      for (const subscription of subscriptions.values()) if (subscription.commonDir === commonDir) emit(subscription, kinds);
    },
    get size() { return subscriptions.size; },
  };
}

module.exports = { createGitWatcher, DEBOUNCE_MS, FALLBACK_INTERVAL_MS, MIN_INTERVAL_MS };
