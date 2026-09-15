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
    latest: matching.at(-1) ?? null,
  };
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
