// The run timeline and usage strips in the bottom panel's Runs view, and the run labels
// the Studio's run menu shares with it. UnifiedBottomPanel loads this file lazily.
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Repeat2, Square, Terminal, Trash2 } from "lucide-react";
import { timelineRowCanAskRem } from "../lib/askRemAboutFailure.js";
import { usageLimitWaitText } from "../lib/workflowRuns.js";
import AskRemButton from "./AskRemButton.jsx";
import { RunErrorReason, RunStatus } from "./RunSummary.jsx";
import { useOffReason } from "./OffReason.jsx";
import { dateTime } from "../lib/timeText.js";

const DEFAULT_RETENTION_SETTINGS = {
  keepDays: 14,
  keepFailedDays: 30,
  keepLast: 100,
};

export function RunTimelinePanel({
  collapsed,
  embedded = false,
  error,
  height,
  loading,
  logPath,
  runEvents = [],
  runs = [],
  selectedRunId,
  onAskRem,
  onResumeRun,
  onPruneRuns,
  onRetentionSettingsChange,
  onResizeStart,
  onResizeKeyDown,
  onSelectRun,
  onShowLatest,
  onStopRun,
  onToggle,
  retentionSettings = DEFAULT_RETENTION_SETTINGS,
  retentionWarning = null,
  text,
  title,
  usageSummary,
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [retentionOpen, setRetentionOpen] = useState(false);
  const [retentionDraft, setRetentionDraft] = useState(() => ({
    ...DEFAULT_RETENTION_SETTINGS,
    ...(retentionSettings ?? {}),
  }));
  const [expandedRowIds, setExpandedRowIds] = useState({});
  const [filters, setFilters] = useState({
    attempt: "",
    datetime: "",
    fanOut: "",
    message: "",
    node: "",
    status: "",
  });
  const historyRef = useRef(null);
  const displayText = error
    ? error
    : loading
      ? "Loading log..."
      : text?.trim()
        ? text.trim()
        : "No run log available.";
  const timelineRows = useMemo(() => parseTimelineRows(runEvents), [runEvents]);
  const logRows = useMemo(
    () => (timelineRows.length ? timelineRows : parseLogRows(displayText)),
    [displayText, timelineRows],
  );
  const resolvedRetentionSettings = retentionDraft;
  // Each field keeps its typed text while focused and saves on blur or Enter. Saving on
  // every keystroke stored passing values such as 2 on the way to 200, and clearing a
  // field saved 0, and the automatic clean-up after each run deletes by those values.
  const [retentionEdits, setRetentionEdits] = useState({});
  const retentionFieldValue = (key) =>
    retentionEdits[key] ?? String(resolvedRetentionSettings[key]);
  const editRetentionSetting = (key, value) =>
    setRetentionEdits((current) => ({ ...current, [key]: value }));
  const commitRetentionSetting = (key) => {
    const typed = retentionEdits[key];
    setRetentionEdits((current) => {
      const rest = { ...current };
      delete rest[key];
      return rest;
    });
    if (typed === undefined) return;
    const parsed = Number.parseInt(typed, 10);
    // An empty or unreadable field goes back to the saved value instead of 0.
    if (Number.isNaN(parsed)) return;
    const value = Math.max(0, parsed);
    if (value === resolvedRetentionSettings[key]) return;
    const nextSettings = { ...resolvedRetentionSettings, [key]: value };
    setRetentionDraft(nextSettings);
    onRetentionSettingsChange?.(nextSettings);
  };
  const retentionFieldProps = (key) => ({
    value: retentionFieldValue(key),
    onChange: (event) => editRetentionSetting(key, event.target.value),
    onBlur: () => commitRetentionSetting(key),
    onKeyDown: (event) => {
      if (event.key === "Enter") commitRetentionSetting(key);
    },
  });
  const pruneWithRetention = (dryRun) => {
    setRetentionOpen(false);
    onPruneRuns?.({
      dryRun,
      keepDays: resolvedRetentionSettings.keepDays,
      keepFailedDays: resolvedRetentionSettings.keepFailedDays,
      keepLast: resolvedRetentionSettings.keepLast,
    });
  };
  const usingTimeline = timelineRows.length > 0;
  const filteredRows = useMemo(() => {
    return logRows.filter((row) => {
      const attempt = filters.attempt.trim().toLowerCase();
      const datetime = filters.datetime.trim().toLowerCase();
      const fanOut = filters.fanOut.trim().toLowerCase();
      const node = filters.node.trim().toLowerCase();
      const message = filters.message.trim().toLowerCase();
      const status = filters.status.trim().toLowerCase();
      return (
        (!attempt || row.attempt.toLowerCase().includes(attempt)) &&
        (!datetime || row.datetime.toLowerCase().includes(datetime)) &&
        (!fanOut || row.fanOut.toLowerCase().includes(fanOut)) &&
        (!node || row.node.toLowerCase().includes(node)) &&
        (!message || row.message.toLowerCase().includes(message)) &&
        (!status || row.status.toLowerCase().includes(status))
      );
    });
  }, [filters, logRows]);

  useEffect(() => {
    setExpandedRowIds({});
  }, [displayText]);

  useEffect(() => {
    setRetentionDraft({
      ...DEFAULT_RETENTION_SETTINGS,
      ...(retentionSettings ?? {}),
    });
  }, [retentionSettings]);

  useEffect(() => {
    if (!historyOpen) return undefined;

    function handlePointerDown(event) {
      if (historyRef.current?.contains(event.target)) return;
      setHistoryOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [historyOpen]);

  return (
    <section
      className={embedded
        ? "flex h-full min-h-0 flex-col overflow-hidden bg-white text-ink"
        : "relative z-30 shrink-0 overflow-hidden border-t border-line bg-white text-ink shadow-[0_-12px_30px_rgba(15,23,42,0.08)] transition-[height]"}
      style={embedded ? undefined : { height: collapsed ? 44 : height }}
    >
      {!embedded && !collapsed ? (
        <div
          aria-label="Resize workflow log"
          aria-orientation="horizontal"
          aria-valuemax={420}
          aria-valuemin={140}
          aria-valuenow={height}
          aria-valuetext={`${height} pixels high`}
          className="absolute left-0 top-[-3px] z-20 h-1.5 w-full cursor-row-resize transition hover:bg-brand/40"
          role="separator"
          tabIndex={0}
          title="Resize workflow log"
          onKeyDown={onResizeKeyDown}
          onPointerDown={onResizeStart}
        />
      ) : null}
      <div
        className="flex h-11 w-full shrink-0 items-center justify-between border-b border-line bg-[#f9fbfd] px-4 text-left"
        role={embedded ? undefined : "button"}
        tabIndex={embedded ? undefined : 0}
        title={embedded ? undefined : collapsed ? "Expand log" : "Collapse log"}
        onClick={embedded ? undefined : onToggle}
        onKeyDown={(event) => {
          if (embedded) return;
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-indigo-100 bg-indigo-50 text-indigo-700">
            <Terminal size={15} />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold">{usingTimeline ? "Run timeline" : title}</h2>
            {logPath ? <p className="truncate text-[11px] text-muted">{logPath}</p> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!collapsed || embedded ? (
            <div
              ref={historyRef}
              className="relative flex items-center gap-1"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                className={`h-7 rounded-md border px-2 text-[11px] font-medium transition ${
                  selectedRunId
                    ? "border-line bg-white text-muted hover:bg-slate-50"
                    : "border-indigo-200 bg-indigo-50 text-indigo-700"
                }`}
                type="button"
                onClick={() => {
                  setHistoryOpen(false);
                  onShowLatest?.();
                }}
              >
                Latest run
              </button>
              <button
                className={`h-7 rounded-md border px-2 text-[11px] font-medium transition ${
                  historyOpen
                    ? "border-slate-300 bg-white text-ink"
                    : "border-line bg-white text-muted hover:bg-slate-50"
                }`}
                type="button"
                onClick={() => setHistoryOpen((current) => !current)}
              >
                All runs
              </button>
              {historyOpen ? (
                <div className="absolute right-0 top-9 z-50 max-h-72 w-[310px] overflow-hidden rounded-lg border border-line bg-white shadow-panel">
                  <div className="border-b border-line px-3 py-2 text-xs font-semibold text-muted">
                    Previous runs
                  </div>
                  <div className="workflow-scrollbar max-h-60 overflow-y-auto">
                    {runs.length ? (
                      runs.map((run) => (
                        <PreviousRunRow
                          key={run.id}
                          run={run}
                          selected={selectedRunId === run.id}
                          onResumeRun={onResumeRun}
                          onSelect={() => {
                            setHistoryOpen(false);
                            onSelectRun?.(run.id);
                          }}
                          onStopRun={onStopRun}
                        />
                      ))
                    ) : (
                      <div className="px-3 py-4 text-xs text-muted">No previous runs.</div>
                    )}
                  </div>
                </div>
              ) : null}
              <div className="relative">
                <button
                  className={`grid h-7 w-7 place-items-center rounded-md border text-muted transition ${
                    retentionOpen
                      ? "border-slate-300 bg-white text-ink"
                      : "border-line bg-white hover:bg-slate-50"
                  }`}
                  title="Run retention settings"
                  type="button"
                  onClick={() => setRetentionOpen((current) => !current)}
                >
                  <Trash2 size={13} />
                </button>
                {retentionOpen ? (
                  <div className="absolute right-0 top-9 z-50 w-[280px] rounded-lg border border-line bg-white p-3 text-xs shadow-panel">
                    <div className="font-semibold text-ink">Retention</div>
                    <div className="mt-1 text-muted">
                      Older runs are removed after each run. Preview or prune now.
                    </div>
                    {retentionWarning ? (
                      <div
                        className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                        role="status"
                      >
                        {retentionWarning}
                      </div>
                    ) : null}
                    <div className="mt-3 grid gap-2">
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep latest runs</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-brand"
                          min="0"
                          type="number"
                          {...retentionFieldProps("keepLast")}
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep runs for days</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-brand"
                          min="0"
                          type="number"
                          {...retentionFieldProps("keepDays")}
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="font-medium text-muted">Keep failed runs for days</span>
                        <input
                          className="h-8 rounded-md border border-line px-2 text-ink outline-none focus:border-brand"
                          min="0"
                          type="number"
                          {...retentionFieldProps("keepFailedDays")}
                        />
                      </label>
                    </div>
                    <div className="mt-3 flex items-center justify-end gap-2">
                      <button
                        className="h-7 rounded-md border border-line px-2 font-medium text-muted transition hover:bg-slate-50"
                        type="button"
                        onClick={() => pruneWithRetention(true)}
                      >
                        Preview
                      </button>
                      <button
                        className="h-7 rounded-md border border-red-200 bg-red-50 px-2 font-medium text-red-700 transition hover:bg-red-100"
                        type="button"
                        onClick={() => pruneWithRetention(false)}
                      >
                        Prune
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          {!embedded ? (
            <span className="grid h-8 w-8 place-items-center rounded-md text-muted">
              {collapsed ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </span>
          ) : null}
        </div>
      </div>
      <div
        className={`workflow-scrollbar overflow-auto bg-white dark:bg-[#1e1e1e] ${embedded ? "min-h-0 flex-1" : ""}`}
        style={embedded ? undefined : { height: Math.max(0, height - 44) }}
      >
        <UsageSummaryStrip summary={usageSummary} />
        <table className="w-full table-fixed border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 border-b border-line bg-[#f9fbfd] dark:bg-[#252526]">
            <tr className="text-[11px] uppercase tracking-wide text-muted">
              <th className="w-[180px] px-3 pb-1 pt-2 font-semibold">Datetime</th>
              <th className="w-[140px] px-3 pb-1 pt-2 font-semibold">Node</th>
              {usingTimeline ? (
                <>
                  <th className="w-[105px] px-3 pb-1 pt-2 font-semibold">Status</th>
                  <th className="w-[90px] px-3 pb-1 pt-2 font-semibold">Attempt</th>
                  <th className="w-[110px] px-3 pb-1 pt-2 font-semibold">Fan-out</th>
                </>
              ) : null}
              <th className="px-3 pb-1 pt-2 font-semibold">Message</th>
            </tr>
            <tr className="border-t border-line/70">
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter datetime"
                  value={filters.datetime}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, datetime: value }))
                  }
                />
              </th>
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter node"
                  value={filters.node}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, node: value }))
                  }
                />
              </th>
              {usingTimeline ? (
                <>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter status"
                      value={filters.status}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, status: value }))
                      }
                    />
                  </th>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter attempt"
                      value={filters.attempt}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, attempt: value }))
                      }
                    />
                  </th>
                  <th className="px-3 pb-2 pt-1">
                    <LogFilterInput
                      label="Filter item"
                      value={filters.fanOut}
                      onChange={(value) =>
                        setFilters((current) => ({ ...current, fanOut: value }))
                      }
                    />
                  </th>
                </>
              ) : null}
              <th className="px-3 pb-2 pt-1">
                <LogFilterInput
                  label="Filter message"
                  value={filters.message}
                  onChange={(value) =>
                    setFilters((current) => ({ ...current, message: value }))
                  }
                />
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/70">
            {filteredRows.map((row) => {
              const expanded = Boolean(expandedRowIds[row.id]);
              return (
                <tr
                  key={row.id}
                  className="h-10 cursor-pointer align-top text-slate-700 transition hover:bg-slate-50 dark:text-[#cccccc] dark:hover:bg-[#2a2d2e]"
                  title={expanded ? "Collapse log row" : "Expand log row"}
                  onClick={() =>
                    setExpandedRowIds((current) => ({
                      ...current,
                      [row.id]: !current[row.id],
                    }))
                  }
                >
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">
                    <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                      {row.datetime || "-"}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">
                    <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                      {row.node || "-"}
                    </div>
                  </td>
                  {usingTimeline ? (
                    <>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.reused ? (
                            <span
                              className="rounded border border-teal-200 bg-teal-50 px-1.5 py-0.5 font-sans text-[10px] font-semibold text-teal-700 dark:border-teal-800 dark:bg-teal-950/40 dark:text-teal-300"
                              title="Reused this step's saved result from an earlier run: not run again, no model call, $0"
                            >
                              Reused
                            </span>
                          ) : row.cached ? (
                            <span
                              className="rounded border border-sky-200 bg-sky-50 px-1.5 py-0.5 font-sans text-[10px] font-semibold text-sky-700 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-300"
                              title="Reused a saved answer from the result cache: no model call, 0 tokens, $0"
                            >
                              Cached
                            </span>
                          ) : (
                            row.status || "-"
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.attempt || "-"}
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">
                        <div className={expanded ? "whitespace-pre-wrap" : "truncate"}>
                          {row.fanOut || "-"}
                        </div>
                      </td>
                    </>
                  ) : null}
                  <td className="px-3 py-2 font-mono text-[11px] leading-5">
                    <div className="flex min-w-0 items-start gap-2">
                    <div
                      className={`min-w-0 flex-1 ${
                        expanded
                          ? "whitespace-pre-wrap break-words"
                          : "truncate whitespace-nowrap"
                      }`}
                    >
                      {row.message || "-"}
                    </div>
                    {onAskRem && usingTimeline && timelineRowCanAskRem(row) ? (
                      <AskRemButton
                        label={`Ask Rem about this failure: ${row.node || "the run"}`}
                        onAsk={() => onAskRem(row)}
                      />
                    ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
            {!filteredRows.length ? (
              <tr>
                <td className="px-4 py-6 text-center text-xs text-muted" colSpan={usingTimeline ? 6 : 3}>
                  {logRows.length ? "No log rows match the current filters." : displayText}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function UsageSummaryStrip({ summary }) {
  if (summary && typeof summary.tokensLabel === "string") {
    return <RunUsageStrip usage={summary} />;
  }
  const totals = summary?.totals;
  const calls = Number(totals?.agent_calls ?? 0);
  if (!summary || !totals || !calls) return null;

  const mostExpensive = firstUsageNode(summary.most_expensive_nodes);
  const slowest = firstUsageNode(summary.slowest_nodes);
  return (
    <div className="border-b border-line bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:bg-[#252526] dark:text-[#cccccc]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold text-ink dark:text-white">LLM usage</span>
        <span>{calls} calls</span>
        <span>{formatInteger(totals.total_tokens)} tokens</span>
        <span>cost~{formatCurrency(totals.estimated_cost)}</span>
        <span>{formatUsageSeconds(totals.agent_time_seconds)} agent time</span>
        {mostExpensive ? (
          <span>
            Most expensive: {mostExpensive.node_id} ({formatCurrency(mostExpensive.estimated_cost)})
          </span>
        ) : null}
        {slowest ? (
          <span>
            Slowest: {slowest.node_id} ({formatUsageSeconds(slowest.duration_seconds)})
          </span>
        ) : null}
      </div>
    </div>
  );
}

// A Rattish run's tokens and estimated cost. Labels come from the backend so that a value
// no provider reported reads "unknown" everywhere, never $0.
export function RunUsageStrip({ usage }) {
  if (!usage?.calls && !usage?.cachedResults) return null;
  const limits = [
    usage.limits?.maxTokens != null ? `max-tokens ${formatInteger(usage.limits.maxTokens)}` : "",
    usage.limits?.maxCostUsd != null ? `max-cost-usd ${usage.limits.maxCostUsd}` : "",
  ].filter(Boolean);
  const nodes = Object.entries(usage.nodes ?? {}).filter(([, item]) => item?.calls);
  return (
    <div
      className="border-b border-line bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:bg-[#252526] dark:text-[#cccccc]"
      data-testid="run-usage"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold text-ink dark:text-white">Model usage</span>
        <span>{usage.calls} {usage.calls === 1 ? "call" : "calls"}</span>
        {usage.cachedResults ? (
          <span title="Answers reused from the result cache. They made no model call and count as 0 tokens and $0.">
            {usage.cachedResults} cached
          </span>
        ) : null}
        <span>Tokens {usage.tokensLabel}</span>
        <span title="As reported by the provider. Unknown when the provider doesn't report cost.">
          Estimated cost {usage.costLabel}
        </span>
        {limits.length ? <span className="text-muted">Limits: {limits.join(", ")}</span> : null}
      </div>
      {nodes.length > 1 ? (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
          {nodes.map(([nodeId, item]) => (
            <span key={nodeId}>
              <span className="font-medium text-slate-700 dark:text-[#cccccc]">{nodeId}</span>
              {": "}
              tokens {item.tokensLabel}, cost {item.costLabel}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function firstUsageNode(nodes) {
  return Array.isArray(nodes) && nodes.length ? nodes[0] : null;
}

function formatCurrency(value) {
  const number = Number(value ?? 0);
  return `$${number.toFixed(number >= 1 ? 2 : 6)}`;
}

function formatInteger(value) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Number(value ?? 0));
}

function formatUsageSeconds(value) {
  return `${Number(value ?? 0).toFixed(2)}s`;
}

function LogFilterInput({ label, onChange, value }) {
  return (
    <input
      aria-label={label}
      className="h-7 w-full rounded-md border border-line bg-white px-2 text-[11px] font-normal text-ink outline-none transition placeholder:text-muted/70 focus:border-brand focus:ring-2 focus:ring-brand/15 dark:bg-[#1e1e1e]"
      placeholder={label}
      type="text"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

function parseLogRows(logText) {
  if (!logText?.trim()) return [];
  const rows = [];
  const timestampPattern =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+-\s+(.*)$/;

  function pushRow(row) {
    if (!row) return;
    rows.push({
      attempt: "",
      fanOut: "",
      ...row,
      message: row.message.trimEnd(),
      status: "",
    });
  }

  let current = null;
  logText.split("\n").forEach((line, index) => {
    const match = line.match(timestampPattern);
    if (match) {
      pushRow(current);
      const parsed = parseLogPayload(match[2]);
      current = {
        id: `log-row-${index}`,
        datetime: match[1],
        node: parsed.node,
        message: parsed.message,
      };
      return;
    }

    if (current) {
      current.message = current.message ? `${current.message}\n${line}` : line;
    } else if (line.trim()) {
      current = {
        id: `log-row-${index}`,
        datetime: "",
        node: "",
        message: line,
      };
    }
  });

  pushRow(current);
  return rows;
}

function parseTimelineRows(events = []) {
  if (!Array.isArray(events) || !events.length) return [];
  return events.map((event, index) => {
    const fanOut = event?.fanOutItem?.index ?? event?.fanOutItem?.file_name ?? "";
    return {
      id: `timeline-row-${index}`,
      attempt: event?.attempt == null ? "" : String(event.attempt),
      datetime: event?.occurredAt ?? "",
      fanOut: fanOut === "" ? "" : String(fanOut),
      message: event?.message ?? "",
      node: event?.nodeId ?? "",
      cached: event?.status === "cached",
      // Copied from an earlier run by Resume or Run from here.
      reused: event?.status === "reused",
      status: String(event?.status ?? "").replaceAll("_", " "),
    };
  });
}

function parseLogPayload(payload) {
  const nodeMatch = payload.match(/^NODE\s+-\s+(.+?)\s+-\s+(.*)$/);
  if (nodeMatch) {
    return {
      node: nodeMatch[1],
      message: nodeMatch[2],
    };
  }

  const levelMatch = payload.match(/^(INFO|ERROR|WARN|WARNING|DEBUG)\s+-\s+(.*)$/);
  if (levelMatch) {
    return {
      node: levelMatch[1],
      message: levelMatch[2],
    };
  }

  return {
    node: "workflow",
    message: payload,
  };
}

// One run in the Studio's run menu and All runs list: its date and status word, why
// it failed if it did, its ID and anything the list adds below.
export function RunPickerLabel({ run, children = null }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-medium text-ink">{formatRunLabel(run)}</span>
        <RunStatus status={run.status} className="shrink-0 text-[11px]" />
      </div>
      <RunErrorReason status={run.status} error={run.error} />
      <div className="truncate text-[11px] text-muted">{run.id}</div>
      <UsageLimitWaitLine run={run} />
      {children}
    </div>
  );
}

function UsageLimitWaitLine({ run }) {
  const text = usageLimitWaitText(run);
  if (!text) return null;
  return (
    <div className="truncate text-[11px] text-amber-700 dark:text-amber-300" data-testid="run-usage-limit-wait" title={text}>
      {text}
    </div>
  );
}

// Shown in place of Stop until the run ends. Its owner may be another process,
// such as the background trigger scheduler, which stops it on its next check.
// One run in the Previous runs list, with its Stop or Resume button. An off
// Resume keeps its name and says why in a line under the row.
function PreviousRunRow({ onResumeRun, onSelect, onStopRun, run, selected }) {
  const running = run.status === "running";
  const resumeOff = useOffReason(
    !running && onResumeRun && run.resume && !run.resume.allowed ? run.resume.reason || "This run can't resume." : "",
  );
  return (
    <div className={`px-3 py-2 text-xs transition hover:bg-slate-50 ${selected ? "bg-indigo-50" : ""}`}>
      <div className="flex w-full items-center gap-2 text-left">
        <button className="flex min-w-0 flex-1 items-center gap-2 text-left" type="button" onClick={onSelect}>
          <RunPickerLabel run={run}>
            {run.usage?.calls ? (
              <div className="truncate text-[11px] text-muted">
                Tokens {run.usage.tokensLabel} · est. cost {run.usage.costLabel}
              </div>
            ) : null}
          </RunPickerLabel>
        </button>
        {running && run.stopRequested ? (
          <StopRequestedLabel />
        ) : running ? (
          <button
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-red-600 transition hover:border-red-200 hover:bg-red-50"
            title="Stop this run"
            type="button"
            onClick={() => onStopRun?.(run.id)}
          >
            <Square size={12} fill="currentColor" strokeWidth={1.7} />
          </button>
        ) : onResumeRun ? (
          <button
            {...resumeOff.props}
            aria-label="Resume this run"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-white text-indigo-700 transition hover:border-indigo-200 hover:bg-indigo-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-disabled:hover:border-line aria-disabled:hover:bg-white"
            title="Resume this run"
            type="button"
            onClick={resumeOff.guard(() => onResumeRun(run.id, {}))}
          >
            <Repeat2 aria-hidden="true" size={13} />
          </button>
        ) : null}
      </div>
      {resumeOff.line ? <div className="mt-1">{resumeOff.line}</div> : null}
    </div>
  );
}

export function StopRequestedLabel() {
  return (
    <span className="shrink-0 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-800" role="status">
      Stop requested
    </span>
  );
}

export function RunStatusDot({ status }) {
  if (["running", "started", "retried"].includes(status)) {
    return <Loader2 className="shrink-0 animate-spin text-blue-500 motion-reduce:animate-none" size={13} />;
  }
  if (status === "queued") {
    return <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-blue-400 bg-blue-50" />;
  }
  const color =
    status === "success"
      ? "bg-emerald-500"
      : status === "reused"
        ? "bg-teal-500"
      : status === "cached"
        ? "bg-sky-500"
      : status === "error"
        ? "bg-red-500"
        : status === "allowed_failure"
          ? "bg-orange-400"
        : status === "stopped" || status === "truncated"
          ? "bg-amber-500"
          : "bg-slate-400";
  return <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />;
}

export function formatRunLabel(run) {
  if (!run?.startedAt) return "Run";
  const date = new Date(run.startedAt);
  if (Number.isNaN(date.getTime())) return run.startedAt;
  return dateTime(date);
}
