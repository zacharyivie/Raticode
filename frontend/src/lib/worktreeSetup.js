import { apiJson } from "./api.js";

// Project setup for new worktrees (backend: gofer/ui/worktree_setup.py).

const send = (path, options = {}) =>
  apiJson(`/worktree-setup${path}`, { ...options, failure: status => `Request failed (${status}).` });

export const worktreeSetupApi = {
  load: project => send(`?project=${encodeURIComponent(project)}`),
  pending: () => send("/pending"),
  approve: (project, sha256) => send("/approve", { method: "POST", body: { project, sha256 } }),
  revoke: project => send("/revoke", { method: "POST", body: { project } }),
};

// The setup version waiting for the user's decision, or null when nothing is waiting.
export function setupCandidate(status) {
  if (!status) return null;
  if (status.pending && !status.pending.error) return status.pending;
  const file = status.file;
  if (file && !file.error && status.state !== "approved") return file;
  return null;
}

export function setupTone(setup) {
  if (!setup) return null;
  if (setup.status === "passed") return setup.version === "approved-earlier" ? "attention" : "success";
  return setup.status === "needs_approval" ? "attention" : "danger";
}

export function setupTitle(setup) {
  if (!setup) return "";
  if (setup.status === "needs_approval") return "Project setup is waiting for your approval";
  if (setup.status === "failed") return "Project setup failed";
  return setup.version === "approved-earlier" ? "Ran the earlier approved setup" : "Project setup finished";
}

export function stepLabel(step) {
  if (step.kind === "command") return step.command;
  if (step.kind === "link") return `Link ${step.path}`;
  if (step.kind === "copy") return `Copy ${step.path}`;
  return step.detail || "Setup";
}

// Whether the run's result should offer the review: a version is waiting for approval.
export function needsReview(setup) {
  return Boolean(setup && (setup.status === "needs_approval" || setup.version === "approved-earlier"));
}
