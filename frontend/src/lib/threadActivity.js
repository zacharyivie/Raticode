import { samePath, uniquePaths, pathKey } from "./workspacePaths.js";
export const THREAD_IDLE_MS = 10 * 24 * 60 * 60 * 1000;

export function threadIsArchived(thread, missingRoots = new Set(), branches = new Map(), now = Date.now()) {
  if (thread.archived) return true;
  if (thread.pinned) return false;
  const timestamp = Date.parse(thread.updatedAt);
  const projectBranches = [...branches].find(([root]) => samePath(root, thread.projectRoot))?.[1];
  return !Number.isFinite(timestamp) || now - timestamp >= THREAD_IDLE_MS
    || [...missingRoots].some(root => samePath(root, thread.projectRoot))
    || Boolean(thread.projectBranch && projectBranches
      && !projectBranches.includes(thread.projectBranch));
}

const scopeCaches = new WeakMap();
const emptyScopes = { missingRoots: new Set(), branches: new Map() };

export function cachedThreadScopes(workspace) {
  return workspace ? scopeCaches.get(workspace)?.result || emptyScopes : emptyScopes;
}

export function threadScopeKey(entries, now = Date.now()) {
  const scopes = new Map();
  for (const entry of entries) {
    if (!entry.projectRoot || entry.pinned || threadIsArchived(entry, undefined, undefined, now)) continue;
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
