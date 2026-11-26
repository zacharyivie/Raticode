// Workflows found outside a project's .raticode folder show their folder, so two with
// the same name (a starter and its copy, say) can be told apart.
export function workflowFolderLabel(workflow) {
  if (workflow?.managed !== false) return "";
  const folder = String(workflow.relativeFolder ?? "").trim();
  return folder === "." ? "Project folder" : folder;
}

// How many of a Rattish workflow's run worktrees still wait to be landed or discarded.
// Deleting the workflow removes them, so the confirmation names the count. A failed
// lookup counts none rather than blocking the delete.
export async function pendingRunWorktreeCount(workflowId, { fetchImpl = fetch, url } = {}) {
  try {
    const response = await fetchImpl(url(`/workflows/${encodeURIComponent(workflowId)}/run-worktrees`));
    if (!response.ok) return 0;
    const payload = await response.json();
    const pending = Number(payload?.pending);
    return Number.isInteger(pending) && pending > 0 ? pending : 0;
  } catch {
    return 0;
  }
}

// The paragraph the delete confirmation adds for waiting run worktrees, or "".
export function runWorktreeDeleteNote(count) {
  if (!count) return "";
  const one = count === 1;
  return `\n\n${count} run worktree${one ? "" : "s"} still ${one ? "waits" : "wait"} to be landed or discarded. Deleting the workflow removes ${one ? "it and its branch" : "them and their branches"}, and their changes are lost.`;
}
