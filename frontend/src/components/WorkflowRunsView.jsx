import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { RunStatus } from "./RunSummary.jsx";
import { STATUS_FILTERS, TIME_RANGES, fetchAllWorkflowRuns, formatDuration, sinceLabel, startedByLabel, usageCells } from "../lib/allWorkflowRuns.js";
import { formatRunAge } from "../lib/workflowRuns.js";
import { startPolling } from "../lib/refresh.js";
import { pauseSummary, triggersApi } from "../lib/rattishTriggers.js";

const PAGE = 200;
// The most runs the backend returns in one list.
const MAX_SHOWN = 1000;
const SELECT = "rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink";

// One switch over every trigger on this computer. Pausing keeps each trigger's own
// on/off setting, so turning it off again brings back exactly the ones that were on.
export function TriggerPauseSwitch({ api = triggersApi, poll = startPolling }) {
  const [state, setState] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setState(await api.pauseState()); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load triggers"); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => poll(load, { interval: 15000, label: "trigger pause" }), [poll, load]);
  const paused = Boolean(state?.pause?.paused);
  const toggle = async (next) => {
    setSaving(true); setError("");
    try { setState(await api.setPaused(next)); } catch (saveError) { setError(saveError instanceof Error ? saveError.message : "Unable to change triggers"); }
    finally { setSaving(false); }
  };
  return <div className="flex min-w-0 max-w-sm items-start gap-2" data-testid="trigger-pause">
    <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-ink">
      <input type="checkbox" role="switch" aria-label="Pause all triggers" aria-describedby="trigger-pause-summary" className="peer sr-only" checked={paused} disabled={!state || saving} onChange={event => void toggle(event.target.checked)} />
      <span aria-hidden="true" className={`relative h-[18px] w-8 shrink-0 rounded-full border transition-[background-color,border-color] duration-150 peer-focus-visible:ring-2 peer-focus-visible:ring-brand/40 peer-disabled:opacity-50 ${paused ? "border-amber-500 bg-amber-500" : "border-line bg-slate-100"}`}>
        <span className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-150 ease-out motion-reduce:transition-none ${paused ? "translate-x-3.5" : "translate-x-0"}`} />
      </span>
      Pause all triggers
    </label>
    <span id="trigger-pause-summary" role={error ? "alert" : "status"} className={`min-w-0 text-[11px] leading-4 ${error ? "text-red-700 dark:text-red-300" : paused ? "text-amber-700 dark:text-amber-300" : "text-muted"}`}>{error || pauseSummary(state)}</span>
  </div>;
}

// Every registered workflow's runs in one list, newest first. Selecting a run opens it
// in the Studio. `request` carries filters from whoever opened the view, such as Rem's
// "While you were away" card with the moment the user left.
export default function WorkflowRunsView({ request = null, onOpenRun, onClose, poll = startPolling, load = fetchAllWorkflowRuns, triggers = triggersApi }) {
  const [filters, setFilters] = useState(() => ({ status: "", workflow: "", since: "24h", ...(request || {}) }));
  const [limit, setLimit] = useState(PAGE);
  const [state, setState] = useState({ loading: true, error: "", data: null });
  const requestRef = useRef(0);

  useEffect(() => {
    if (request) { setFilters(current => ({ ...current, ...request })); setLimit(PAGE); }
  }, [request]);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    const id = ++requestRef.current;
    if (!silent) setState(current => ({ ...current, loading: true, error: "" }));
    try {
      const data = await load({ ...filters, limit });
      if (id === requestRef.current) setState({ loading: false, error: "", data });
    } catch (error) {
      if (id === requestRef.current) setState(current => ({ ...current, loading: false, error: error instanceof Error ? error.message : "Unable to load workflow runs" }));
    }
  }, [filters, limit, load]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => poll(() => refresh({ silent: true }), { interval: 5000, label: "workflow runs" }), [poll, refresh]);

  const change = (name, value) => { setFilters(current => ({ ...current, [name]: value })); setLimit(PAGE); };
  const data = state.data;
  const runs = data?.runs || [];
  const total = data?.pagination?.total ?? runs.length;
  const custom = filters.since && !TIME_RANGES.some(([value]) => value === filters.since);

  return <section aria-label="Workflow runs" className="flex min-h-0 flex-1 flex-col bg-surface text-ink" data-testid="workflow-runs-view">
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <h1 className="text-base font-semibold">Workflow runs</h1>
        <p role="status" className="text-xs text-muted" data-testid="workflow-runs-summary">{state.loading && !data ? "Loading runs…" : data?.totals?.summary || "No workflow runs"}</p>
      </div>
      <TriggerPauseSwitch api={triggers} poll={poll} />
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <select aria-label="Filter by status" className={SELECT} value={filters.status} onChange={event => change("status", event.target.value)}>
          {STATUS_FILTERS.map(([value, label]) => <option key={value || "any"} value={value}>{label}</option>)}
        </select>
        <select aria-label="Filter by workflow" className={SELECT} value={filters.workflow} onChange={event => change("workflow", event.target.value)}>
          <option value="">All workflows</option>
          {(data?.workflows || []).map(workflow => <option key={workflow.id} value={workflow.id}>{workflow.name}{workflow.projectName ? ` · ${workflow.projectName}` : ""}</option>)}
        </select>
        <select aria-label="Time range" className={SELECT} value={filters.since} onChange={event => change("since", event.target.value)}>
          {custom ? <option value={filters.since}>{sinceLabel(filters.since) || filters.since}</option> : null}
          {TIME_RANGES.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}
        </select>
        <button type="button" aria-label="Refresh runs" title="Refresh" className="rounded-md p-1.5 text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline dark:hover:bg-zinc-800" onClick={() => void refresh()}><RefreshCw size={14} aria-hidden="true" /></button>
        {onClose ? <button type="button" aria-label="Close workflow runs" title="Close" className="rounded-md p-1.5 text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline dark:hover:bg-zinc-800" onClick={onClose}><X size={15} aria-hidden="true" /></button> : null}
      </div>
    </header>
    {state.error ? <p role="alert" className="border-b border-line px-4 py-2 text-xs text-red-700 dark:text-red-300">{state.error}</p> : null}
    <div className="min-h-0 flex-1 overflow-auto">
      {data && !runs.length ? <p className="px-4 py-8 text-sm text-muted">No runs match these filters. Runs from the Studio, triggers, your phone and Rem all appear here.</p> : null}
      {runs.length ? <table className="w-full border-collapse text-left text-xs">
        <thead className="sticky top-0 bg-surface text-[11px] uppercase tracking-wide text-muted">
          <tr className="border-b border-line">
            <th scope="col" className="px-4 py-2 font-medium">Workflow</th>
            <th scope="col" className="px-3 py-2 font-medium">Started by</th>
            <th scope="col" className="px-3 py-2 font-medium">Status</th>
            <th scope="col" className="px-3 py-2 font-medium">Started</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Duration</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Tokens</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(run => {
            const { tokens, cost } = usageCells(run.usage);
            const running = run.status === "running";
            return <tr key={`${run.workflowId}:${run.id}`} className="cursor-pointer border-b border-line last:border-0 hover:bg-slate-50 dark:hover:bg-zinc-800/60" onClick={() => onOpenRun?.(run)}>
              <th scope="row" className="max-w-[18rem] px-4 py-2 font-normal">
                <button type="button" className="block max-w-full truncate text-left font-medium text-ink hover:underline focus-visible:outline" title={`Open run ${run.id} in the Studio`} onClick={event => { event.stopPropagation(); onOpenRun?.(run); }}>{run.workflowName}</button>
                <span className="block truncate text-[11px] text-muted">{run.projectName}</span>
              </th>
              <td className="px-3 py-2">{startedByLabel(run.startedBy)}</td>
              <td className="px-3 py-2" title={run.error || undefined}>
                <RunStatus status={run.status} />
                {run.awaitingApproval ? <span className="ml-1.5 text-amber-700 dark:text-amber-300">· needs approval</span> : null}
                {run.fallbacks?.length ? <span className="ml-1.5 text-muted" data-testid="run-provider-switch" title={run.fallbacks.map(item => item.line).join("\n")}>· switched provider</span> : null}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-muted" title={run.startedAt ? new Date(run.startedAt).toLocaleString() : undefined}>{formatRunAge(run.startedAt)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{formatDuration(run.durationSeconds, running)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{tokens}</td>
              <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums">{cost}</td>
            </tr>;
          })}
        </tbody>
      </table> : null}
      {total > runs.length ? <div className="flex items-center justify-between px-4 py-3 text-xs text-muted">
        <span>Showing {runs.length} of {total} runs</span>
        {limit < MAX_SHOWN
          ? <button type="button" className="rounded-md border border-line px-2.5 py-1 text-ink hover:bg-slate-100 dark:hover:bg-zinc-800" onClick={() => setLimit(current => Math.min(MAX_SHOWN, current + PAGE))}>Show more</button>
          : <span>Narrow the filters to see older runs.</span>}
      </div> : null}
    </div>
  </section>;
}
