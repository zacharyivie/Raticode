import { useState } from "react";
import { Activity, AlertCircle, CheckCircle2, Circle, Square, Unplug, X } from "lucide-react";
import { distinctWorkflowRunRecords, exactWorkflowRunStopPath, workflowRunSummary } from "../lib/workflowRuns";

const LABELS = { running: "Running", queued: "Queued", submitting: "Submitting", stopping: "Stopping", success: "Succeeded", error: "Failed", stopped: "Stopped", cancelled: "Cancelled", disconnected: "Disconnected", unknown: "Unknown" };

export function RunStatus({ status }) {
  const Icon = status === "error" ? AlertCircle : status === "success" ? CheckCircle2 : status === "disconnected" ? Unplug : status === "running" ? Activity : Circle;
  return <span className={`inline-flex items-center gap-1.5 ${status === "error" ? "text-red-700 dark:text-red-300" : status === "success" ? "text-emerald-700 dark:text-emerald-300" : "text-zinc-600 dark:text-zinc-300"}`}><Icon size={13} aria-hidden="true" />{LABELS[status] ?? status}</span>;
}

export default function RunSummary({ embedded = false, records = [], projectPath = "", onReview, onStop, onClose, onRefresh, loading = false, connectionError = "" }) {
  const [scope, setScope] = useState("all");
  const [pending, setPending] = useState(new Set());
  const [error, setError] = useState("");
  const summary = workflowRunSummary(records);
  const distinct = distinctWorkflowRunRecords(records);
  const visible = (scope === "project" ? distinct.filter((record) => record.projectPath === projectPath) : distinct).slice().reverse();

  async function stop(record) {
    if (pending.has(record.key) || !exactWorkflowRunStopPath(record)) return;
    setPending((current) => new Set(current).add(record.key));
    setError("");
    try { await onStop?.(record); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to stop this run. Try again."); }
    finally { setPending((current) => { const next = new Set(current); next.delete(record.key); return next; }); }
  }

  return <section aria-label="Workflow runs" className={`flex flex-col bg-white text-xs dark:bg-zinc-900 ${embedded ? "h-full min-h-0" : "max-h-80 min-h-32 border-t border-zinc-300 dark:border-zinc-700"}`}>
    <div className="flex flex-wrap items-center gap-3 border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
      <h2 className="font-semibold">Runs</h2>
      {onRefresh ? <button type="button" className="rounded px-2 py-1 hover:bg-slate-100 focus-visible:outline" onClick={onRefresh}>Refresh</button> : null}
      <span role="status" className="text-zinc-600 dark:text-zinc-300">{summary.active.length} active · {summary.unread.length} unread</span>
      <select aria-label="Filter runs by project" value={scope} onChange={(event) => setScope(event.target.value)} className="ml-auto rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-600">
        <option value="all">All projects</option><option value="project">This project</option>
      </select>
      {onClose && <button type="button" aria-label="Close runs panel" onClick={onClose} className="rounded p-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 dark:hover:bg-zinc-800"><X size={15} /></button>}
    </div>
    {(error || connectionError) && <p role="alert" className="px-3 py-2 text-red-700 dark:text-red-300">{error || `${connectionError}. Run status may be out of date. Refresh to reconnect.`}</p>}
    {loading ? <p role="status" className="px-3 py-2 text-muted">Checking runs…</p> : null}
    <div className="overflow-auto">
      {!visible.length && <p className="px-4 py-6 text-zinc-600 dark:text-zinc-300">No runs recorded{scope === "project" ? " for this project" : ""}. Run a workflow to see its progress and results here.</p>}
      <ul>{visible.map((record) => <li key={record.key} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-zinc-200 px-3 py-2 last:border-0 dark:border-zinc-800">
        <div className="min-w-0 flex-1 basis-40"><p className="truncate font-medium">{record.workflowName}</p><p className="truncate text-[11px] text-zinc-600 dark:text-zinc-400" title={record.projectPath}>{record.projectPath || "Local workflows"} · {record.runId}</p></div>
        <RunStatus status={record.status} />
        {record.unread && <span className="text-indigo-700 dark:text-indigo-300">Unread result</span>}
        <button type="button" onClick={() => onReview?.(record)} className="rounded px-2 py-1 text-indigo-700 hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 dark:text-indigo-300 dark:hover:bg-zinc-800">{record.status === "error" ? "Review failure" : "Open run"}</button>
        {exactWorkflowRunStopPath(record) && <button type="button" disabled={pending.has(record.key)} onClick={() => void stop(record)} aria-label={`Stop run ${record.runId} of ${record.workflowName}`} className="inline-flex items-center gap-1 rounded border border-zinc-300 px-2 py-1 hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-800"><Square size={11} aria-hidden="true" />{pending.has(record.key) ? "Stopping…" : "Stop"}</button>}
      </li>)}</ul>
    </div>
  </section>;
}
