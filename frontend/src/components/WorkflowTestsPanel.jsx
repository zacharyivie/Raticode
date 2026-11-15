import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, CircleSlash, Clock, Loader2, Pencil, Play, Plus, Square, Trash2, TriangleAlert, X } from "lucide-react";
import {
  CASE_STATUS_LABELS,
  CHANGE_LABELS,
  CHECK_TYPES,
  EXPECT_STATUS,
  JSON_OPERATORS,
  TEXT_OPERATORS,
  caseChanges,
  caseFromDraft,
  checkSummary,
  comparisonSentence,
  costLabel,
  draftFromCase,
  formatUsd,
  isRunning,
  newCheckDraft,
  nextCaseId,
  shortRevision,
  starterInputs,
  workflowTestsApi,
} from "../lib/workflowTests.js";

const BUTTON =
  "inline-flex h-7 items-center gap-1 rounded-md border border-line bg-white px-2 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "inline-flex h-7 items-center gap-1 rounded-md bg-brand px-2.5 text-[11px] font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const INPUT =
  "h-7 w-full rounded-md border border-line bg-white px-2 text-[12px] text-ink outline-none focus:border-indigo-500";
const TEXTAREA =
  "w-full rounded-md border border-line bg-white px-2 py-1.5 font-mono text-[11px] text-ink outline-none focus:border-indigo-500";

const STATUS_TONE = {
  passed: "text-emerald-700 dark:text-emerald-300",
  failed: "text-red-700 dark:text-red-300",
  error: "text-amber-700 dark:text-amber-300",
  skipped: "text-muted",
  pending: "text-muted",
  running: "text-brand",
};
const CHANGE_TONE = {
  broke: "border-red-500/60 bg-red-500/10 text-red-700 dark:text-red-300",
  fixed: "border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  changed: "border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  new: "border-line bg-slate-100 text-slate-700",
};

function StatusIcon({ status, size = 13 }) {
  const label = CASE_STATUS_LABELS[status] ?? "Not run";
  const className = `shrink-0 ${STATUS_TONE[status] ?? "text-muted"}`;
  if (status === "passed") return <Check aria-label={label} className={className} size={size} />;
  if (status === "failed") return <X aria-label={label} className={className} size={size} />;
  if (status === "error") return <TriangleAlert aria-label={label} className={className} size={size} />;
  if (status === "running") return <Loader2 aria-label={label} className={`${className} animate-spin`} size={size} />;
  if (status === "skipped") return <CircleSlash aria-label={label} className={className} size={size} />;
  return <Clock aria-label={label} className={className} size={size} />;
}

function ChangeBadge({ change }) {
  if (!change || change === "same") return null;
  return (
    <span className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-semibold ${CHANGE_TONE[change] ?? CHANGE_TONE.new}`}>
      {CHANGE_LABELS[change] ?? change}
    </span>
  );
}

function JsonBlock({ value }) {
  return (
    <pre className="max-h-32 overflow-auto rounded-md bg-slate-50 p-2 font-mono text-[11px] leading-4 text-ink">
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

export default function WorkflowTestsPanel({ active = true, workflowId = "", workflowName = "", refreshKey = "", dirty = false }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [editing, setEditing] = useState(null);
  const [skipJudge, setSkipJudge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [rawDraft, setRawDraft] = useState(null);

  const load = useCallback(async () => {
    if (!workflowId) return;
    setLoading(true);
    try {
      const payload = await workflowTestsApi.load(workflowId);
      setData(payload);
      setError("");
      setResult((current) => (isRunning(current) && current?.id === payload.activeResultId ? current : payload.latest));
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    setData(null);
    setResult(null);
    setSelectedId("");
    setEditing(null);
    setRawDraft(null);
  }, [workflowId]);

  useEffect(() => {
    if (active) void load();
  }, [active, load, refreshKey]);

  const runningId = isRunning(result) ? result.id : data?.activeResultId;
  useEffect(() => {
    if (!runningId || !workflowId) return undefined;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const payload = await workflowTestsApi.result(workflowId, runningId);
        if (cancelled) return;
        setResult(payload.result);
        if (!isRunning(payload.result)) {
          clearInterval(timer);
          void load();
        }
      } catch (pollError) {
        if (!cancelled) setError(pollError.message);
      }
    }, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load, runningId, workflowId]);

  const cases = data?.testSet?.cases ?? [];
  const resultCases = useMemo(
    () => Object.fromEntries((result?.cases ?? []).map((item) => [item.id, item])),
    [result],
  );
  const changes = caseChanges(result?.comparison);
  const selected = cases.find((item) => item.id === selectedId) ?? cases[0] ?? null;
  const estimateTotals = skipJudge ? data?.judgeEstimateSkipped : data?.estimate?.totals;
  const staleResult = result && data?.revision && result.revision !== data.revision;

  async function saveCases(nextCases, nextSelected) {
    setBusy(true);
    try {
      const payload = await workflowTestsApi.save(workflowId, { ...(data?.testSet ?? { testSetVersion: 1 }), testSetVersion: 1, cases: nextCases });
      setData(payload);
      setError("");
      setEditing(null);
      if (nextSelected !== undefined) setSelectedId(nextSelected);
      return true;
    } catch (saveError) {
      setError(saveError.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function run(caseIds) {
    setBusy(true);
    setError("");
    try {
      const payload = await workflowTestsApi.run(workflowId, {
        caseIds,
        skipJudge,
        expectedRevision: data?.revision ?? undefined,
      });
      setResult(payload.result);
      setData((current) => (current ? { ...current, activeResultId: payload.result.id } : current));
    } catch (runError) {
      setError(runError.message);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!runningId) return;
    try {
      await workflowTestsApi.stop(workflowId, runningId);
    } catch (stopError) {
      setError(stopError.message);
    }
  }

  function addCase() {
    const id = nextCaseId(cases);
    setEditing({ originalId: null, draft: draftFromCase({ id, inputs: starterInputs(selected), checks: [] }), error: "" });
  }

  if (!workflowId) {
    return <div className="grid h-full place-items-center px-6 text-xs text-muted">Open a Rattish workflow to see its tests.</div>;
  }
  if (!data) {
    return (
      <div className="grid h-full place-items-center px-6 text-xs text-muted" role="status">
        {error || (loading ? "Loading tests..." : "")}
      </div>
    );
  }

  if (data.raw != null || (data.error && !data.testSet)) {
    const raw = rawDraft ?? data.raw ?? "";
    return (
      <div className="workflow-scrollbar flex h-full flex-col gap-2 overflow-auto p-3 text-xs">
        <p className="text-amber-700 dark:text-amber-300" role="alert">{data.error}</p>
        <textarea aria-label="Test set JSON" className={`${TEXTAREA} min-h-24 flex-1`} value={raw} onChange={(event) => setRawDraft(event.target.value)} />
        <div className="flex gap-2">
          <button className={PRIMARY} disabled={busy} type="button" onClick={async () => {
            let parsed;
            try {
              parsed = JSON.parse(raw);
            } catch (parseError) {
              setError(`Not valid JSON: ${parseError.message}`);
              return;
            }
            setBusy(true);
            try {
              setData(await workflowTestsApi.save(workflowId, parsed));
              setRawDraft(null);
              setError("");
            } catch (saveError) {
              setError(saveError.message);
            } finally {
              setBusy(false);
            }
          }}>Save test set</button>
          {error ? <span className="text-red-700">{error}</span> : null}
        </div>
      </div>
    );
  }

  const totals = result?.totals;
  return (
    <div className="flex h-full min-h-0 flex-col text-xs" aria-label={`Tests for ${workflowName || workflowId}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-1.5">
        <span className="font-semibold text-ink">{cases.length} {cases.length === 1 ? "case" : "cases"}</span>
        {data.estimate ? (
          <span className="min-w-0 flex-1 truncate text-muted" title={(skipJudge ? "" : data.estimate.summary) || ""}>
            Expected cost: <span className="font-medium text-ink">{costLabel(estimateTotals)}</span>
            {estimateTotals ? ` · ${estimateTotals.modelCallsExact ? "" : "about "}${estimateTotals.modelCalls} model calls` : ""}
          </span>
        ) : <span className="min-w-0 flex-1 truncate text-amber-700 dark:text-amber-300">{data.error}</span>}
        <label className="flex items-center gap-1 text-muted" title="Model-judgment checks are skipped and cost nothing.">
          <input checked={skipJudge} type="checkbox" onChange={(event) => setSkipJudge(event.target.checked)} />
          Skip model judgment
        </label>
        {runningId ? (
          <button className={BUTTON} type="button" onClick={stop}><Square size={12} /> Stop</button>
        ) : (
          <button
            className={PRIMARY}
            disabled={busy || !cases.length || !data.estimate}
            title={data.estimate?.summary}
            type="button"
            onClick={() => run()}
          >
            <Play size={12} /> Run all · {costLabel(estimateTotals)}
          </button>
        )}
      </div>
      {dirty ? <p className="border-b border-line bg-amber-500/10 px-3 py-1 text-amber-800 dark:text-amber-200">Unsaved workflow changes aren&apos;t tested. Tests run the saved revision.</p> : null}
      {error ? <p className="border-b border-line bg-red-500/10 px-3 py-1 text-red-700 dark:text-red-300" role="alert">{error}</p> : null}
      {result ? (
        <div className="border-b border-line px-3 py-1 text-muted">
          <span className={STATUS_TONE[result.status] ?? ""}>
            {isRunning(result) ? "Running" : result.status === "passed" ? "All passed" : result.status === "stopped" ? "Stopped" : result.status === "interrupted" ? "Interrupted" : "Some cases failed"}
          </span>
          {totals?.cases != null && !isRunning(result) ? ` · ${totals.passed}/${totals.cases} passed · ${totals.modelCalls + totals.judgeCalls} model calls · ${totals.costComplete ? "" : "at least "}${formatUsd(totals.costUsd)}` : ""}
          {` · revision ${shortRevision(result.revision)}`}
          {staleResult ? <span className="text-amber-700 dark:text-amber-300"> · from an earlier revision. Run to compare.</span> : null}
          {result.comparison ? (
            <>
              {" · "}{comparisonSentence(result.comparison)}{" "}
              <button className="text-brand hover:underline" type="button" onClick={() => setShowDiff((value) => !value)}>
                {showDiff ? "Hide source change" : "Show source change"}
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      {showDiff && result?.comparison ? (
        <pre aria-label="Source change since the compared revision" className="workflow-scrollbar max-h-40 shrink-0 overflow-auto border-b border-line bg-slate-50 px-3 py-2 font-mono text-[11px] leading-4">
          {(result.comparison.sourceDiff || "The workflow source is the same.").split("\n").map((line, index) => (
            <span key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "block text-emerald-700 dark:text-emerald-300" : line.startsWith("-") && !line.startsWith("---") ? "block text-red-700 dark:text-red-300" : "block text-muted"}>{line || " "}</span>
          ))}
        </pre>
      ) : null}
      <div className="flex min-h-0 flex-1">
        <div className="workflow-scrollbar flex w-60 shrink-0 flex-col overflow-y-auto border-r border-line" role="list" aria-label="Test cases">
          {cases.map((item) => {
            const outcome = resultCases[item.id];
            const chosen = selected?.id === item.id && !editing;
            return (
              <button
                key={item.id}
                aria-current={chosen ? "true" : undefined}
                className={`flex items-center gap-2 px-3 py-1.5 text-left transition ${chosen ? "bg-slate-100" : "hover:bg-slate-50"}`}
                role="listitem"
                type="button"
                onClick={() => {
                  setSelectedId(item.id);
                  setEditing(null);
                }}
              >
                <StatusIcon status={outcome?.status} />
                <span className="min-w-0 flex-1 truncate text-ink">{item.name || item.id}</span>
                <ChangeBadge change={changes[item.id]} />
              </button>
            );
          })}
          {!cases.length ? <p className="px-3 py-2 leading-5 text-muted">No cases yet. Add one to describe an input and what the output should look like.</p> : null}
          <button className="flex items-center gap-1.5 px-3 py-1.5 text-left font-medium text-brand hover:bg-slate-50" type="button" onClick={addCase}>
            <Plus size={12} /> Add case
          </button>
        </div>
        <div className="workflow-scrollbar min-w-0 flex-1 overflow-y-auto p-3">
          {editing ? (
            <CaseEditor
              busy={busy}
              editing={editing}
              onCancel={() => setEditing(null)}
              onChange={(draft) => setEditing((current) => ({ ...current, draft, error: "" }))}
              onSave={async () => {
                const otherIds = cases.map((item) => item.id).filter((id) => id !== editing.originalId);
                const converted = caseFromDraft(editing.draft, otherIds);
                if (converted.error) {
                  setEditing((current) => ({ ...current, error: converted.error }));
                  return;
                }
                const nextCases = editing.originalId == null
                  ? [...cases, converted.case]
                  : cases.map((item) => (item.id === editing.originalId ? converted.case : item));
                await saveCases(nextCases, converted.case.id);
              }}
            />
          ) : selected ? (
            <CaseDetail
              busy={busy || Boolean(runningId)}
              change={changes[selected.id]}
              outcome={resultCases[selected.id]}
              testCase={selected}
              onDelete={() => saveCases(cases.filter((item) => item.id !== selected.id), "")}
              onEdit={() => setEditing({ originalId: selected.id, draft: draftFromCase(selected), error: "" })}
              onRun={() => run([selected.id])}
            />
          ) : (
            <p className="text-muted">Each case runs the workflow once with its inputs, then checks the run: match text, compare a JSON field, run a command, or ask a model to judge the output.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function CaseDetail({ busy, change, onDelete, onEdit, onRun, outcome, testCase }) {
  const checks = outcome?.checks?.length ? outcome.checks : (testCase.checks ?? []).map((check, index) => ({ index, label: checkSummary(check), status: undefined, type: check.type }));
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusIcon size={15} status={outcome?.status} />
        <h3 className="text-[13px] font-semibold text-ink">{testCase.name || testCase.id}</h3>
        <span className="font-mono text-[10px] text-muted">{testCase.id}</span>
        <ChangeBadge change={change} />
        <div className="flex-1" />
        <button className={BUTTON} disabled={busy} type="button" onClick={onRun}><Play size={12} /> Run case</button>
        <button className={BUTTON} type="button" onClick={onEdit}><Pencil size={12} /> Edit</button>
        <button aria-label={`Delete case ${testCase.id}`} className={BUTTON} disabled={busy} type="button" onClick={onDelete}><Trash2 size={12} /></button>
      </div>
      {outcome?.message && outcome.status !== "passed" ? <p className={STATUS_TONE[outcome.status]}>{outcome.message}</p> : null}
      <div>
        <p className="mb-1 font-medium text-muted">Inputs</p>
        <JsonBlock value={testCase.inputs} />
      </div>
      <div>
        <p className="mb-1 font-medium text-muted">Checks</p>
        {checks.length ? (
          <ul className="space-y-1">
            {checks.map((check) => (
              <li key={`${check.type}-${check.index}`} className="flex items-start gap-2">
                <span className="mt-0.5"><StatusIcon status={check.status} /></span>
                <span className="min-w-0 flex-1">
                  <span className="text-ink">{check.label}</span>
                  {check.message ? <span className="block text-muted">{check.message}</span> : null}
                  {check.detail && check.status !== "passed" && (check.detail.stderr || check.detail.stdout) ? (
                    <pre className="mt-1 max-h-24 overflow-auto rounded bg-slate-50 p-1.5 font-mono text-[10px]">{check.detail.stderr || check.detail.stdout}</pre>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-muted">No checks. The case passes when the run passes.</p>}
      </div>
      {outcome?.runId ? (
        <p className="text-[11px] text-muted">
          Run {outcome.runId} · {((outcome.durationMs ?? 0) / 1000).toFixed(1)}s · {outcome.usage?.modelCalls ?? 0} model calls
          {outcome.usage?.judgeCalls ? ` + ${outcome.usage.judgeCalls} judge` : ""} · {formatUsd((outcome.usage?.costUsd ?? NaN) + (outcome.usage?.judgeCostUsd ?? 0))}
        </p>
      ) : null}
    </div>
  );
}

function CaseEditor({ busy, editing, onCancel, onChange, onSave }) {
  const { draft } = editing;
  const update = (patch) => onChange({ ...draft, ...patch });
  const updateCheck = (index, patch) => update({ checks: draft.checks.map((check, at) => (at === index ? { ...check, ...patch } : check)) });
  return (
    <form className="space-y-2.5" onSubmit={(event) => { event.preventDefault(); void onSave(); }}>
      <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
        <label className="block"><span className="text-muted">Name</span><input className={INPUT} value={draft.name} onChange={(event) => update({ name: event.target.value })} /></label>
        <label className="block"><span className="text-muted">Case ID</span><input className={`${INPUT} font-mono`} value={draft.id} onChange={(event) => update({ id: event.target.value })} /></label>
        <label className="block"><span className="text-muted">Expect</span>
          <select className={INPUT} value={draft.expectStatus} onChange={(event) => update({ expectStatus: event.target.value })}>
            {EXPECT_STATUS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>
      <label className="block"><span className="text-muted">Inputs (JSON object)</span>
        <textarea className={TEXTAREA} rows={3} value={draft.inputs} onChange={(event) => update({ inputs: event.target.value })} />
      </label>
      <div className="space-y-2">
        <p className="font-medium text-muted">Checks</p>
        {draft.checks.map((check, index) => (
          <CheckEditor key={index} check={check} index={index} onChange={(patch) => updateCheck(index, patch)} onRemove={() => update({ checks: draft.checks.filter((_, at) => at !== index) })} />
        ))}
        <div className="flex flex-wrap gap-1.5">
          {CHECK_TYPES.map(([type, label]) => (
            <button key={type} className={BUTTON} type="button" onClick={() => update({ checks: [...draft.checks, newCheckDraft(type)] })}>
              <Plus size={12} /> {label}
            </button>
          ))}
        </div>
      </div>
      {editing.error ? <p className="text-red-700 dark:text-red-300" role="alert">{editing.error}</p> : null}
      <div className="flex gap-2">
        <button className={PRIMARY} disabled={busy} type="button" onClick={() => void onSave()}>Save case</button>
        <button className={BUTTON} type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function CheckEditor({ check, index, onChange, onRemove }) {
  const typeLabel = CHECK_TYPES.find(([type]) => type === check.type)?.[1] ?? check.type;
  const target = (
    <label className="block min-w-0 flex-1"><span className="text-muted">Looks at</span>
      <input className={`${INPUT} font-mono`} placeholder="outputs.answer" title="status, error, outputs.NAME or nodes.NODE-ID, with .key and [0] steps" value={check.target} onChange={(event) => onChange({ target: event.target.value })} />
    </label>
  );
  return (
    <fieldset className="space-y-1.5 rounded-md border border-line p-2">
      <legend className="flex items-center gap-2 px-1 font-medium text-ink">
        {index + 1}. {typeLabel}
        <button aria-label={`Remove check ${index + 1}`} className="text-muted hover:text-ink" type="button" onClick={onRemove}><X size={12} /></button>
      </legend>
      {check.type === "text" ? (
        <div className="flex flex-wrap items-end gap-2">
          {target}
          <select aria-label="Comparison" className={`${INPUT} w-auto`} value={check.operator} onChange={(event) => onChange({ operator: event.target.value })}>
            {TEXT_OPERATORS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <input aria-label="Expected text" className={`${INPUT} min-w-0 flex-1`} value={check.value} onChange={(event) => onChange({ value: event.target.value })} />
          <label className="flex h-7 items-center gap-1 text-muted"><input checked={check.ignoreCase} type="checkbox" onChange={(event) => onChange({ ignoreCase: event.target.checked })} />Ignore case</label>
        </div>
      ) : null}
      {check.type === "json" ? (
        <div className="flex flex-wrap items-end gap-2">
          {target}
          <select aria-label="Comparison" className={`${INPUT} w-auto`} value={check.operator} onChange={(event) => onChange({ operator: event.target.value })}>
            {JSON_OPERATORS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          {check.operator === "exists" || check.operator === "missing" ? null : (
            <input aria-label="Expected JSON value" className={`${INPUT} min-w-0 flex-1 font-mono`} placeholder={check.operator === "oneOf" ? "[\"a\", \"b\"]" : "\"ok\""} value={check.value} onChange={(event) => onChange({ value: event.target.value })} />
          )}
        </div>
      ) : null}
      {check.type === "command" ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="block min-w-0 flex-[2]"><span className="text-muted">Command (gets the value on standard input)</span>
            <input className={`${INPUT} font-mono`} placeholder="python3 checks/valid.py" value={check.command} onChange={(event) => onChange({ command: event.target.value })} />
          </label>
          {target}
          <label className="block w-20"><span className="text-muted">Exit code</span>
            <input className={INPUT} inputMode="numeric" value={check.exitCode} onChange={(event) => onChange({ exitCode: event.target.value })} />
          </label>
        </div>
      ) : null}
      {check.type === "judge" ? (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-end gap-2">
            {target}
            <label className="block w-36"><span className="text-muted">Provider</span>
              <input className={INPUT} placeholder="workflow's" value={check.provider} onChange={(event) => onChange({ provider: event.target.value })} />
            </label>
            <label className="block w-40"><span className="text-muted">Model</span>
              <input className={INPUT} placeholder="default" value={check.model} onChange={(event) => onChange({ model: event.target.value })} />
            </label>
          </div>
          <label className="block"><span className="text-muted">The model passes the case when the value meets these criteria</span>
            <textarea className={TEXTAREA} rows={2} value={check.criteria} onChange={(event) => onChange({ criteria: event.target.value })} />
          </label>
          <p className="text-[11px] text-muted">Costs one model call per run of this case.</p>
        </div>
      ) : null}
      <label className="block"><span className="text-muted">Label (optional)</span>
        <input className={INPUT} value={check.label} onChange={(event) => onChange({ label: event.target.value })} />
      </label>
    </fieldset>
  );
}
