const STORAGE_KEY = "taskurotta.workflow-runs.v1";
const ACTIVE = new Set(["submitting", "queued", "running", "stopping", "claimed", "cancel_requested"]);
const TERMINAL = new Set(["success", "error", "stopped", "cancelled"]);

export function normalizeWorkflowRunStatus(run) {
  const status = String(run.status ?? "").toLowerCase();
  if (["passed", "succeeded", "completed"].includes(status)) return "success";
  if (["failed", "failure"].includes(status)) return "error";
  if (status === "canceled") return "cancelled";
  if (status === "claimed") return "running";
  if (status === "cancel_requested") return "stopping";
  if (status) return status;
  return run.success === true ? "success" : run.success === false ? "error" : "unknown";
}

export function runRecordKey(record) {
  return JSON.stringify([
    record.projectPath ?? "", record.worktreePath ?? "",
    record.workflowId ?? "", record.runId ?? record.id ?? "",
  ]);
}

// Retain every run independently of tabs and current browsing project. Polling a
// reviewed result again must not turn its unread marker back on.
export function mergeWorkflowRunRecords(records, runs, context = {}) {
  const next = new Map(records.map((record) => [record.key, record]));
  for (const run of runs) {
    if (!run || typeof run !== "object") continue;
    const runId = run.runId ?? run.id;
    const workflowId = run.workflowId ?? context.workflowId;
    if (!runId || !workflowId) continue;
    const identity = { ...context, ...run, workflowId, runId };
    const key = runRecordKey(identity);
    const previous = next.get(key);
    const status = normalizeWorkflowRunStatus(run);
    // A late active response must not reverse an already observed completion.
    if (previous && TERMINAL.has(previous.status) && ACTIVE.has(status)) continue;
    next.set(key, {
      ...previous,
      key, runId, workflowId,
      workflowName: identity.workflowName ?? previous?.workflowName ?? workflowId,
      projectPath: identity.projectPath ?? previous?.projectPath ?? "",
      worktreePath: identity.worktreePath ?? previous?.worktreePath ?? "",
      status: previous?.status === "stopping" && ACTIVE.has(status) ? "stopping" : status,
      awaitingApproval: ACTIVE.has(status) && (run.awaitingApproval ?? previous?.awaitingApproval ?? false),
      queueRun: run.queueRun ?? previous?.queueRun ?? false,
      sourcePath: identity.sourcePath ?? previous?.sourcePath ?? "",
      triggerType: run.triggerType ?? run.trigger ?? previous?.triggerType ?? "",
      message: run.message ?? previous?.message ?? "",
      startedAt: run.startedAt ?? previous?.startedAt ?? null,
      finishedAt: run.finishedAt ?? previous?.finishedAt ?? null,
      logPath: run.logPath ?? previous?.logPath ?? null,
      unread: previous?.unread === true || (
        TERMINAL.has(status) && (!previous || !TERMINAL.has(previous.status))
      ),
      stale: false,
    });
  }
  return [...next.values()];
}

export function markWorkflowRunReviewed(records, key) {
  return records.map((record) => record.key === key ? { ...record, unread: false } : record);
}

export function disconnectWorkflowRunRecords(records, workflowId = null) {
  return records.map((record) => (
    (!workflowId || record.workflowId === workflowId) && ACTIVE.has(record.status)
      ? { ...record, previousStatus: record.status, status: "disconnected", stale: true }
      : record
  ));
}

export function workflowRunSummary(records, workflowId = null) {
  const distinct = distinctWorkflowRunRecords(records);
  const matching = workflowId ? distinct.filter((record) => record.workflowId === workflowId) : distinct;
  const active = matching.filter((record) => ACTIVE.has(record.status));
  const unread = matching.filter((record) => record.unread);
  return {
    active, unread,
    unreadFailures: unread.filter((record) => record.status === "error"),
    disconnected: matching.filter((record) => record.status === "disconnected"),
    latest: sortWorkflowRunsNewestFirst(matching)[0] ?? null,
  };
}

function runStartedAt(record) {
  const time = Date.parse(record?.startedAt ?? "");
  return Number.isFinite(time) ? time : null;
}

// Run logs are listed newest first, so record order is not run order. Runs
// without a start time keep their relative order after the timed ones.
export function sortWorkflowRunsNewestFirst(records) {
  return records
    .map((record, index) => ({ record, index, time: runStartedAt(record) }))
    .sort((left, right) => (
      left.time === null || right.time === null
        ? (left.time === null) - (right.time === null) || right.index - left.index
        : right.time - left.time || right.index - left.index
    ))
    .map(({ record }) => record);
}

// After a successful log scan, finished or disconnected runs the backend no
// longer lists were deleted by retention or with their workflow. A partial page only proves
// absence for runs newer than the oldest run it returned.
export function pruneUnlistedWorkflowRuns(records, workflowId, runs = [], { complete = true } = {}) {
  const listed = new Set(runs.map((run) => run.runId ?? run.id));
  const listedTimes = runs.map(runStartedAt).filter((time) => time !== null);
  const oldestListed = listedTimes.length ? Math.min(...listedTimes) : null;
  return records.filter((record) => {
    if (record.workflowId !== workflowId || record.queueRun || ACTIVE.has(record.status) || listed.has(record.runId)) return true;
    if (complete) return false;
    const started = runStartedAt(record);
    return oldestListed === null || started === null || started < oldestListed;
  });
}

const LIST_STATUS = {
  submitting: { label: "Starting", tone: "running" },
  queued: { label: "Queued", tone: "running" },
  running: { label: "Running", tone: "running" },
  stopping: { label: "Stopping", tone: "running" },
  success: { label: "Succeeded", tone: "success" },
  error: { label: "Failed", tone: "error" },
  stopped: { label: "Stopped", tone: "warning" },
  cancelled: { label: "Cancelled", tone: "warning" },
  disconnected: { label: "Disconnected", tone: "muted" },
};

// What the workflow list shows for one workflow: compile state first, then
// active runs, then the newest finished run.
const LEGACY_WORKFLOW_STATUS = { Success: "success", Error: "error", Stopped: "stopped", Running: "running" };

export function workflowListStatus(workflow, records = [], { submitting = false } = {}) {
  if (workflow?.invalid) {
    return { key: "invalid", label: "Invalid", tone: "error", detail: workflow?.validationError || "This workflow has errors. Open it to see them.", unread: false };
  }
  const summary = workflowRunSummary(records, workflow?.id);
  const unread = summary.unread.length > 0;
  if (submitting && !summary.active.length) return { key: "submitting", ...LIST_STATUS.submitting, detail: "Waiting for the runner", unread };
  if (summary.active.length) {
    const [newest] = sortWorkflowRunsNewestFirst(summary.active);
    const status = newest.awaitingApproval && newest.status === "running"
      ? { label: "Awaiting approval", tone: "warning" }
      : LIST_STATUS[newest.status] ?? LIST_STATUS.running;
    return {
      key: newest.status, ...status, unread,
      detail: summary.active.length > 1 ? `${summary.active.length} runs in progress` : "",
      since: newest.startedAt ?? null,
    };
  }
  const latest = summary.latest;
  if (!latest) {
    // Legacy workflows report their newest run in the workflow payload before
    // the run registry has scanned their logs.
    const legacyStatus = LEGACY_WORKFLOW_STATUS[workflow?.status];
    if (legacyStatus) return { key: legacyStatus, ...LIST_STATUS[legacyStatus], detail: "", unread };
    return { key: "ready", label: "Ready", tone: "muted", detail: workflow?.status === "Scheduled" ? "Scheduled" : "Not run yet", unread };
  }
  const status = LIST_STATUS[latest.status] ?? { label: "Unknown", tone: "muted" };
  return {
    key: latest.status, ...status, unread,
    detail: latest.status === "disconnected" ? "Lost contact with this run" : "",
    since: latest.finishedAt ?? latest.startedAt ?? null,
  };
}

export function formatRunAge(value, now = Date.now()) {
  const time = Date.parse(value ?? "");
  if (!Number.isFinite(time)) return "";
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(time).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function distinctWorkflowRunRecords(records) {
  const logs = new Set(records.filter(record => !record.queueRun && record.logPath).map(record => JSON.stringify([record.workflowId, record.logPath])));
  return records.filter(record => !record.queueRun || !record.logPath || !logs.has(JSON.stringify([record.workflowId, record.logPath])));
}

export function exactWorkflowRunStopPath(record) {
  if (!record?.workflowId || !record?.runId || !["running", "queued"].includes(record.status) || record.stale) return null;
  if (record.queueRun) return `/queue/${encodeURIComponent(record.runId)}/cancel`;
  return `/workflows/${encodeURIComponent(record.workflowId)}/runs/${encodeURIComponent(record.runId)}/stop`;
}

export function restoreWorkflowRunRecords(serialized) {
  try {
    const data = typeof serialized === "string" ? JSON.parse(serialized) : serialized;
    if (data?.version !== 1 || !Array.isArray(data.records)) return [];
    const records = data.records.filter((record) => record && typeof record === "object" &&
      typeof record.workflowId === "string" && typeof record.runId === "string" &&
      record.workflowId && record.runId).map((record) => ({ ...record, key: runRecordKey(record) }));
    return disconnectWorkflowRunRecords(records);
  } catch { return []; }
}

export function readWorkflowRunRecords(storage = globalThis.localStorage) {
  try { return restoreWorkflowRunRecords(storage?.getItem(STORAGE_KEY)); }
  catch { return []; }
}

export function writeWorkflowRunRecords(records, storage = globalThis.localStorage) {
  try { storage?.setItem(STORAGE_KEY, JSON.stringify({ version: 1, records })); }
  catch { /* A storage quota error must not interrupt an active run. */ }
}

// Runtime failures belong beside compiler and preflight diagnostics in Problems.
export function workflowRunProblems(nodes = {}, events = []) {
  const failures = new Map();
  for (const event of events) {
    if (["error", "failed", "failure"].includes(event.status) && event.nodeId !== "workflow") {
      failures.set(event.nodeId, { nodeId: event.nodeId, message: event.message || "Node failed. Open the run log for details." });
    }
  }
  for (const [id, node] of Object.entries(nodes)) {
    if (!["error", "failed", "failure"].includes(node.status)) { failures.delete(id); continue; }
    failures.set(id, { nodeId: id, message: node.message || node.error?.message || (typeof node.error === "string" ? node.error : "") || failures.get(id)?.message || "Node failed. Open the run log for details.", code: node.error?.code || "RUN_NODE_FAILED" });
  }
  // Run-level failures such as max-runs, invalid inputs or finish: fail have no failing node.
  const workflowFailure = events.findLast(event => event.nodeId === "workflow" && event.status === "failed");
  const explained = workflowFailure?.errorCode
    ? [...failures.values()].some(failure => failure.code === workflowFailure.errorCode)
    : failures.size > 0;
  if (workflowFailure?.message && !explained) {
    failures.set("workflow", { nodeId: "workflow", message: workflowFailure.message, code: workflowFailure.errorCode || "RUN_WORKFLOW_FAILED" });
  }
  return [...failures.values()].map(failure => ({ ...failure, severity: "error", runtime: true, code: failure.code || "RUN_NODE_FAILED" }));
}
