import { samePath, uniquePaths, pathKey } from "./workspacePaths.js";
export const READ_THREAD_LIMIT = 7;
export const ARCHIVE_RETENTION_OPTIONS = [["never", "Never"], ["7", "After 7 days"], ["30", "After 30 days"], ["90", "After 90 days"]];
const DAY_MS = 24 * 60 * 60 * 1000;

// Index entries carry every field needed to group threads without reading metadata.
export function threadIndexEntry(thread) {
  return { id: thread.id, updatedAt: thread.updatedAt, projectRoot: thread.projectRoot || "",
    projectBranch: thread.projectBranch, managedRole: thread.managedRole, archived: Boolean(thread.archived),
    pinned: Boolean(thread.pinned), unread: Boolean(thread.unread), readAt: thread.readAt, scopeIndexed: true };
}

export function threadReadTime(thread) {
  return String(thread.readAt || "") > String(thread.updatedAt || "") ? thread.readAt : thread.updatedAt || "";
}

const newestFirst = (key) => (a, b) => String(key(b) || "").localeCompare(String(key(a) || ""));

// A thread whose folder or saved branch is gone cannot continue, so it is archived even when pinned.
export function threadIsUnreachable(thread, missingRoots = new Set(), branches = new Map()) {
  if (!thread.projectRoot) return false;
  const projectBranches = [...branches].find(([root]) => samePath(root, thread.projectRoot))?.[1];
  return [...missingRoots].some(root => samePath(root, thread.projectRoot))
    || Boolean(thread.projectBranch && projectBranches && !projectBranches.includes(thread.projectBranch));
}

// Pinned, then unread, then the most recently read threads. Running threads
// leave those groups until their turn ends; older read threads are archived.
export function organizeThreads(entries, { running = new Set(), unread = new Set(), missingRoots, branches, readLimit = READ_THREAD_LIMIT } = {}) {
  const groups = { pinned: [], unread: [], read: [], running: [], archived: [] };
  const readCandidates = [];
  for (const entry of entries) {
    const isRunning = running.has(entry.id);
    if (entry.archived || (!isRunning && threadIsUnreachable(entry, missingRoots, branches))) groups.archived.push(entry);
    else if (entry.pinned) groups.pinned.push(entry);
    else if (isRunning) groups.running.push(entry);
    else if (entry.unread || unread.has(entry.id)) groups.unread.push(entry);
    else readCandidates.push(entry);
  }
  readCandidates.sort(newestFirst(threadReadTime));
  groups.read = readCandidates.slice(0, readLimit);
  groups.archived.push(...readCandidates.slice(readLimit));
  for (const name of ["pinned", "unread", "running"]) groups[name].sort(newestFirst(entry => entry.updatedAt));
  groups.archived.sort(newestFirst(threadReadTime));
  return groups;
}

export function threadIsArchived(thread, missingRoots, branches) {
  return Boolean(thread.archived) || threadIsUnreachable(thread, missingRoots, branches);
}

export function archiveRetentionDays(value) {
  const days = Number(value);
  return Number.isFinite(days) && days > 0 ? days : 0;
}

// Archived threads are disposed of after the configured time without activity.
export function expiredArchivedThreads(archived, retention, now = Date.now()) {
  const days = archiveRetentionDays(retention);
  if (!days) return [];
  return archived.filter(entry => {
    const timestamp = Date.parse(threadReadTime(entry));
    return Number.isFinite(timestamp) && now - timestamp >= days * DAY_MS;
  });
}

const scopeCaches = new WeakMap();
const emptyScopes = { missingRoots: new Set(), branches: new Map() };

export function cachedThreadScopes(workspace) {
  return workspace ? scopeCaches.get(workspace)?.result || emptyScopes : emptyScopes;
}

// Only threads that could be shown need a folder check. Extra read candidates
// let a thread fill the Read group when a newer one turns out to be unreachable.
export function threadScopeKey(entries, readLimit = READ_THREAD_LIMIT) {
  const { pinned, unread, read } = organizeThreads(entries, { readLimit: readLimit * 3 });
  const scopes = new Map();
  for (const entry of [...pinned, ...unread, ...read]) {
    if (!entry.projectRoot) continue;
    const key = pathKey(entry.projectRoot);
    const previous = scopes.get(key);
    scopes.set(key, { projectRoot: key, projectBranch: Boolean(entry.projectBranch || previous?.projectBranch) });
  }
  return JSON.stringify([...scopes.values()].sort((a, b) => a.projectRoot.localeCompare(b.projectRoot)));
}

async function boundedMap(items, callback) {
  let index = 0;
  await Promise.all(Array.from({ length: Math.min(4, items.length) }, async () => {
    while (index < items.length) await callback(items[index++]);
  }));
}

// Cache per desktop bridge, sharing in-flight work across list remounts.
export async function inspectThreadScopes(entries, workspace, { force = false } = {}) {
  if (!workspace) return emptyScopes;
  const key = JSON.stringify(entries);
  const cached = scopeCaches.get(workspace);
  if (cached?.key === key && (cached.pending || (!force && Date.now() - cached.at < 60000))) {
    return cached.pending || cached.result;
  }
  const record = { key, at: Date.now(), result: cached?.result || emptyScopes };
  record.pending = inspectScopes(entries, workspace, force).then(result => {
    record.result = result;
    record.pending = null;
    return result;
  });
  scopeCaches.set(workspace, record);
  return record.pending;
}

// A failed permission or Git request is not proof of deletion.
async function inspectScopes(entries, workspace, force) {
  const roots = uniquePaths(entries.map(entry => entry.projectRoot).filter(Boolean));
  const missingRoots = new Set();
  const branches = new Map();
  if (workspace.missingThreadRoots) {
    for (let offset = 0; offset < roots.length; offset += 100) {
      try {
        for (const root of await workspace.missingThreadRoots(roots.slice(offset, offset + 100))) missingRoots.add(root);
      } catch { /* Keep unknown folders active. */ }
    }
  } else if (workspace.getPathInfo) {
    await boundedMap(roots, async root => {
      try {
        const info = await workspace.getPathInfo(root);
        if (info?.exists === false) missingRoots.add(root);
      } catch { /* Keep unknown folders active. */ }
    });
  }
  const missingKeys = new Set([...missingRoots].map(pathKey));
  const branchRoots = new Set(entries.filter(entry => entry.projectBranch).map(entry => pathKey(entry.projectRoot)));
  await boundedMap(roots, async root => {
    if (missingKeys.has(pathKey(root)) || !branchRoots.has(pathKey(root))) return;
    try {
      const status = await workspace.gitBranches?.(root, force);
      if (status?.active && !status.branchesUnavailable && Array.isArray(status.branches)) branches.set(root, [...status.branches, ...(status.branch ? [status.branch] : [])]);
    } catch { /* Git may be unavailable or the folder may need approval. */ }
  });
  return { missingRoots, branches };
}
