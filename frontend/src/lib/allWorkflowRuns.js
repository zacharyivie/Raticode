import { apiUrl } from "./api.js";

// Runs from every registered workflow, newest first: the Workflow runs view and the
// workflow line in Rem's "While you were away" card. The backend reads only small run
// summaries, so this stays fast however many runs are kept.

export const WORKFLOW_RUNS_EVENT = "gofer:workflow-runs-open";

export const STATUS_FILTERS = Object.freeze([
  ["", "Any status"],
  ["success", "Passed"],
  ["error", "Failed"],
  ["running", "Running"],
  ["stopped", "Stopped"],
  ["disconnected", "Interrupted"],
]);

export const TIME_RANGES = Object.freeze([
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["", "All time"],
]);

const STARTED_BY = Object.freeze({ manual: "Manual", trigger: "Trigger", phone: "Phone", rem: "Rem" });

export const startedByLabel = value => STARTED_BY[value] || "Manual";

export function formatDuration(seconds, running = false) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (running && !value) return "Running";
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
}

// A run's tokens and estimated cost; runs saved before usage tracking show a dash.
export function usageCells(usage) {
  if (!usage) return { tokens: "–", cost: "–" };
  return { tokens: usage.tokensLabel || "–", cost: usage.costLabel || "–" };
}

// "Since 14 Nov, 22:10" for a range that starts at a moment rather than a span.
export function sinceLabel(value) {
  const time = Date.parse(value ?? "");
  if (!Number.isFinite(time)) return "";
  return `Since ${new Date(time).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
}

export function workflowRunsQuery({ status = "", workflow = "", since = "", limit = 200, offset = 0 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (workflow) params.set("workflow", workflow);
  if (since) params.set("since", since);
  params.set("limit", String(limit));
  if (offset) params.set("offset", String(offset));
  return `/rattish/runs?${params}`;
}

export async function fetchAllWorkflowRuns(filters = {}, { fetchImpl = globalThis.fetch } = {}) {
  const response = await fetchImpl(apiUrl(workflowRunsQuery(filters)));
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Workflow runs returned ${response.status}`);
  return payload;
}

// Opens the Workflow runs view, optionally from a moment such as "while you were away".
export function openWorkflowRuns(detail = {}) {
  const target = globalThis.window ?? globalThis;
  target.dispatchEvent?.(new CustomEvent(WORKFLOW_RUNS_EVENT, { detail }));
}

// The record the Studio's run review takes, from a row of the list.
export function reviewRecord(run) {
  return {
    workflowId: run.workflowId,
    workflowName: run.workflowName,
    runId: run.id,
    status: run.status,
    projectPath: run.projectRoot,
    startedAt: run.startedAt,
    finishedAt: run.finishedAt,
  };
}
