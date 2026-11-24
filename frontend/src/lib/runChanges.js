import { apiUrl } from "./api.js";

// A workspace: worktree run's changes (backend: gofer/rattish/run_worktrees.py).

async function send(runId, action, { method = "GET", workflowId = "", body } = {}) {
  const query = method === "GET" && workflowId ? `?workflowId=${encodeURIComponent(workflowId)}` : "";
  const response = await fetch(apiUrl(`/rattish/runs/${encodeURIComponent(runId)}/${action}${query}`), {
    method,
    ...(method === "GET" ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(workflowId ? { workflowId } : {}), ...(body || {}) }),
    }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

export const runChangesApi = {
  read: (runId, workflowId) => send(runId, "changes", { workflowId }),
  // Lands only if the branch is still the tip the user reviewed.
  land: (runId, workflowId, tip) => send(runId, "land", { method: "POST", workflowId, body: tip ? { expectedTip: tip } : {} }),
  discard: (runId, workflowId) => send(runId, "discard", { method: "POST", workflowId }),
};

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// One line about the run's worktree for its Changes tab.
export function worktreeStateText(worktree) {
  if (!worktree) return "";
  switch (worktree.state) {
    case "active": return `Running in its own worktree on ${worktree.branch}.`;
    case "pending": return worktree.filesChanged
      ? `${plural(worktree.filesChanged, "file")} changed on ${worktree.branch}, waiting for you to land or discard ${worktree.filesChanged === 1 ? "it" : "them"}.`
      : `Changes on ${worktree.branch} are waiting for you to land or discard them.`;
    case "no_changes": return "This run changed nothing, so Raticode removed its worktree and branch.";
    case "landed": return `Landed in ${worktree.baseBranch}${worktree.landedCommit ? ` as ${worktree.landedCommit.slice(0, 8)}` : ""}. Its worktree and branch were removed.`;
    case "discarded": return "Discarded. Its worktree and branch were removed.";
    default: return "";
  }
}

// Whether Land and Discard apply: only while the changes wait.
export function canDecide(worktree) {
  return worktree?.state === "pending";
}

// What Raticode says after Land or Discard.
export function decisionText(result) {
  if (!result) return "";
  if (result.conflict) return result.message || `${result.branch} conflicts with ${result.baseBranch}; nothing was landed.`;
  if (result.discarded) return `Discarded ${result.branch} and its worktree.`;
  if (result.how === "landed") return `${result.branch} was already in ${result.baseBranch}.`;
  return `Landed ${result.branch} in ${result.baseBranch}${result.how === "fast_forward" ? " (fast-forward)" : " with a merge commit"}. Nothing was pushed.`;
}
