import { apiJson } from "./api.js";
import { dateTime, runLength } from "./timeText.js";

// Runs from every registered workflow, newest first: the Workflow runs view, the workflow
// line in Rem's "While you were away" card and the runs Quit and Restart would cut off.
// The backend reads only small run summaries, so this stays fast however many runs are kept.

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

// A run's length, or "Running" for one that hasn't reached its first second.
export function formatDuration(seconds, running = false) {
  return running && !Math.round(Number(seconds) || 0) ? "Running" : runLength(seconds);
}

// A run's tokens and estimated cost; runs saved before usage tracking show a dash.
export function usageCells(usage) {
  if (!usage) return { tokens: "–", cost: "–" };
  return { tokens: usage.tokensLabel || "–", cost: usage.costLabel || "–" };
}

// "Since Nov 14, 10:10 PM" for a range that starts at a moment rather than a span.
export function sinceLabel(value) {
  const when = dateTime(value);
  return when && `Since ${when}`;
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

export function fetchAllWorkflowRuns(filters = {}, { fetchImpl, signal } = {}) {
  return apiJson(workflowRunsQuery(filters), { fetchImpl, signal, failure: status => `Workflow runs returned ${status}` });
}

// How long Quit and Restart wait for the run list before going by unsaved files alone.
const LIFECYCLE_RUNS_WAIT_MS = 2000;

// The runs a quit or restart would cut off, or null when the backend is down or slow, so
// a backend that can't answer never keeps Raticode from quitting.
export async function runningRunsBeforeLifecycle() {
  const controller = new AbortController();
  let timer;
  const late = new Promise(resolve => {
    timer = window.setTimeout(() => { controller.abort(); resolve(null); }, LIFECYCLE_RUNS_WAIT_MS);
  });
  const listed = fetchAllWorkflowRuns({ status: "running", limit: 50 }, { signal: controller.signal })
    .then(data => (Array.isArray(data?.runs) ? data.runs : []).filter(run => run.status === "running").map(run => {
      const started = Date.parse(run.startedAt ?? "");
      const seconds = Number.isFinite(started) ? (Date.now() - started) / 1000 : run.durationSeconds;
      return { key: `${run.workflowId}:${run.id}`, workflowName: run.workflowName || run.workflowId, startedBy: startedByLabel(run.startedBy), length: runLength(seconds) };
    }), () => null);
  try { return await Promise.race([listed, late]); } finally { window.clearTimeout(timer); }
}

// "Quit anyway" for a quit, "Restart anyway" for an update or another restart.
export function lifecycleVerb(reason = "") {
  if (/^quit\b/.test(reason)) return "Quit";
  return /restart|update/.test(reason) ? "Restart" : "Continue";
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
