import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "./api.js";
import { startPolling } from "./refresh.js";
import { disconnectWorkflowRunRecords, exactWorkflowRunStopPath, markWorkflowRunReviewed, mergeWorkflowRunRecords, readWorkflowRunRecords, writeWorkflowRunRecords } from "./workflowRuns.js";

export function workflowRunContext(workflow) {
  return { workflowId: workflow.id, workflowName: workflow.name, projectPath: workflow.projectRoot || "", worktreePath: workflow.projectRoot || "", sourcePath: workflow.sourcePath || "" };
}

async function read(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(apiUrl(path), { signal: controller.signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Run monitoring returned ${response.status}`);
    return payload;
  } finally { clearTimeout(timeout); }
}

export function useWorkflowRunRegistry(workflows) {
  const [records, setRecords] = useState(readWorkflowRunRecords);
  const [connection, setConnection] = useState({ loading: true, error: "" });
  const knownRef = useRef(workflows);
  knownRef.current = workflows;
  const recordsRef = useRef(records);
  recordsRef.current = records;
  const pendingRef = useRef(null);
  const mounted = useRef(false);
  const stopping = useRef(new Set());
  const record = useCallback((workflow, run) => setRecords(current => mergeWorkflowRunRecords(current, [{ ...run, workflowId: workflow.id }], workflowRunContext(workflow))), []);
  const review = useCallback(key => setRecords(current => markWorkflowRunReviewed(current, key)), []);
  const refresh = useCallback(() => {
    if (pendingRef.current) return pendingRef.current;
    pendingRef.current = (async () => {
      const failures = [];
      const known = new Map(knownRef.current.filter(workflow => workflow.sourceFormat !== "project").map(workflow => [workflow.id, workflow]));
      const [discovered, queue] = await Promise.allSettled([read("/workflows"), read("/queue")]);
      if (discovered.status === "fulfilled") {
        for (const workflow of discovered.value.workflows || []) known.set(workflow.id, { ...known.get(workflow.id), ...workflow });
      } else failures.push(discovered.reason.message);
      for (const run of recordsRef.current) {
        if (!known.has(run.workflowId)) known.set(run.workflowId, { id: run.workflowId, name: run.workflowName, projectRoot: run.projectPath, sourcePath: run.sourcePath });
      }
      const remaining = [...known.values()];
      const updates = [];
      const failedIds = [];
      await Promise.all(Array.from({ length: Math.min(4, remaining.length) }, async () => {
        while (remaining.length) {
          const workflow = remaining.shift();
          try {
            // Drain both requests before reusing this worker, including on failure.
            const results = await Promise.allSettled([
              read(`/workflows/${encodeURIComponent(workflow.id)}/logs?limit=100`),
              read(`/workflows/${encodeURIComponent(workflow.id)}/logs?status=running`),
            ]);
            const failure = results.find(result => result.status === "rejected");
            if (failure) throw failure.reason;
            const [recent, active] = results.map(result => result.value);
            updates.push({ workflow, runs: [...(recent.runs || []), ...(active.runs || [])] });
          } catch (error) { failedIds.push(workflow.id); failures.push(error.message); }
        }
      }));
      if (queue.status === "fulfilled") {
        for (const run of queue.value.runs || []) {
          updates.push({ workflow: known.get(run.workflowId) || { id: run.workflowId }, runs: [{ ...run, logPath: run.runLogPath, queueRun: true }] });
        }
      } else failures.push(queue.reason.message);
      if (!mounted.current) return;
      setRecords(current => {
        const observed = new Set(updates.flatMap(({ workflow, runs }) => runs.map(run => JSON.stringify([workflow.id, run.runId || run.id]))));
        // A successful scan with a missing formerly active run cannot confirm
        // that it is still running, and cannot imply successful completion.
        let next = current.map(run => observed.has(JSON.stringify([run.workflowId, run.runId])) ? run : disconnectWorkflowRunRecords([run])[0]);
        for (const id of failedIds) next = disconnectWorkflowRunRecords(next, id);
        if (queue.status === "rejected") next = next.map(run => run.queueRun ? disconnectWorkflowRunRecords([run])[0] : run);
        for (const { workflow, runs } of updates) next = mergeWorkflowRunRecords(next, runs, workflowRunContext(workflow));
        return next;
      });
      setConnection({ loading: false, error: failures[0] || "" });
    })().catch(error => {
      if (mounted.current) { setRecords(disconnectWorkflowRunRecords); setConnection({ loading: false, error: error.message }); }
    }).finally(() => { pendingRef.current = null; });
    return pendingRef.current;
  }, []);
  useEffect(() => {
    mounted.current = true;
    const stop = startPolling(refresh, { interval: 5000, immediate: true });
    return () => { mounted.current = false; stop(); };
  }, [refresh]);
  useEffect(() => { writeWorkflowRunRecords(records); }, [records]);
  const stop = useCallback(async run => {
    const path = exactWorkflowRunStopPath(run);
    if (!path || stopping.current.has(run.key)) return;
    stopping.current.add(run.key);
    try {
      const response = await fetch(apiUrl(path), { method: "POST" });
      const payload = await response.json();
      if (!response.ok || payload.stopped === false) throw new Error(payload.error || payload.message || "The runner did not accept the stop request.");
      setRecords(current => current.map(item => item.key === run.key ? { ...item, status: "stopping" } : item));
      await refresh();
    } finally { stopping.current.delete(run.key); }
  }, [refresh]);
  return { records, ...connection, record, review, refresh, stop };
}
