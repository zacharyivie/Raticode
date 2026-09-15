export const THREAD_IDLE_MS = 10 * 24 * 60 * 60 * 1000;

export function threadIsArchived(thread, missingRoots = new Set(), branches = new Map(), now = Date.now()) {
  const timestamp = Date.parse(thread.updatedAt);
  return !Number.isFinite(timestamp) || now - timestamp >= THREAD_IDLE_MS
    || missingRoots.has(thread.projectRoot)
    || Boolean(thread.projectBranch && branches.has(thread.projectRoot)
      && !branches.get(thread.projectRoot).includes(thread.projectBranch));
}

// Check summaries only. A failed permission or Git request is not proof of deletion.
export async function inspectThreadScopes(entries, workspace) {
  const roots = [...new Set(entries.map(entry => entry.projectRoot).filter(Boolean))];
  const missingRoots = new Set();
  const branches = new Map();
  if (workspace?.missingThreadRoots) {
    for (let offset = 0; offset < roots.length; offset += 100) {
      try {
        for (const root of await workspace.missingThreadRoots(roots.slice(offset, offset + 100))) missingRoots.add(root);
      } catch { /* Keep unknown folders active. */ }
    }
  } else if (workspace?.getPathInfo) {
    for (const root of roots) {
      try {
        const info = await workspace.getPathInfo(root);
        if (info?.exists === false) missingRoots.add(root);
      } catch { /* Keep unknown folders active. */ }
    }
  }
  for (const root of roots) {
    if (missingRoots.has(root) || !entries.some(entry => entry.projectRoot === root && entry.projectBranch)) continue;
    try {
      const status = await workspace?.gitStatus?.(root);
      if (status?.active && !status.branchesUnavailable && Array.isArray(status.branches)) branches.set(root, [...status.branches, ...(status.branch ? [status.branch] : [])]);
    } catch { /* Git may be unavailable or the folder may need approval. */ }
  }
  return { missingRoots, branches };
}
