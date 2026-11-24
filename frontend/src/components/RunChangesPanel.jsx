import { useEffect, useState } from "react";
import { AlertTriangle, Check, GitBranch, GitMerge, RotateCcw, Trash2 } from "lucide-react";
import CommitDiff from "./CommitDiff.jsx";
import { WorktreeSetupNotice } from "./WorktreeSetup.jsx";
import { canDecide, decisionText, runChangesApi, worktreeStateText } from "../lib/runChanges.js";
import { mergeStatus, patchSize } from "../lib/ticketReview.js";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const BUTTON = "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50";
const TONES = {
  ok: "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
  done: "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
  bad: "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200",
  warn: "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200",
};

// The Changes tab of a workspace: worktree run: its patch in the commit diff view, and
// the user's Land (fast-forward or a conflict-checked merge, never pushed) or Discard.
export default function RunChangesPanel({ worktree, workflowId = "", theme = "light", api = runChangesApi, onChanged }) {
  const runId = worktree?.runId || "";
  const [patch, setPatch] = useState(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState("");
  const [outcome, setOutcome] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(""); setPatch(null); setConfirming("");
    if (!runId) return undefined;
    api.read(runId, workflowId).then(data => { if (!cancelled) setPatch(data); }, failure => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
    // Reread when the run or its state changes, so Land always names what's there now.
  }, [api, runId, workflowId, worktree?.state, worktree?.commit, reload]);

  useEffect(() => { setOutcome(null); }, [runId]);

  if (!worktree) return <p className="grid h-full place-items-center px-6 text-xs text-muted">This run didn&apos;t run in a worktree.</p>;
  const current = patch?.worktree || worktree;
  const decide = canDecide(current);
  const status = mergeStatus(patch);
  const size = patchSize(patch?.files || []);
  const conflicts = outcome?.conflict ? outcome.conflicts : status.conflicts;

  const act = async (action) => {
    setBusy(true); setError(""); setConfirming("");
    try {
      const result = action === "land" ? await api.land(runId, workflowId, patch?.tip) : await api.discard(runId, workflowId);
      setOutcome(result);
      setReload(value => value + 1);
      if (!result.conflict) onChanged?.(result);
    } catch (failure) {
      setError(failure.message);
    } finally { setBusy(false); }
  };

  return <section aria-label="Run changes" className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3 text-xs">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-[11px]"><GitBranch aria-hidden="true" size={13} /><span className="break-all">{current.branch}</span>{current.baseBranch ? <><span aria-hidden="true">→</span><span>{current.baseBranch}</span></> : null}</span>
      {patch?.files?.length ? <span className="text-muted">{plural(size.files, "file")}, <span className="text-emerald-700">+{size.added}</span> <span className="text-red-700">−{size.removed}</span>{patch.truncated ? " (first 300 files)" : ""}</span> : null}
      {decide && status.text ? <span role="status" className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONES[status.tone] || ""}`}>{status.tone === "bad" ? <AlertTriangle aria-hidden="true" size={12} /> : <Check aria-hidden="true" size={12} />}{status.text}</span> : null}
    </div>
    <p className="m-0 text-muted">{worktreeStateText(current)}</p>
    {current.message ? <p className="m-0 text-amber-800">{current.message}</p> : null}
    {current.setup ? <WorktreeSetupNotice setup={current.setup} /> : null}
    {outcome && !outcome.conflict ? <p role="status" className="m-0 rounded-md bg-emerald-50 px-2.5 py-1.5 font-semibold text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200">{decisionText(outcome)}</p> : null}
    {error ? <div role="alert" className="flex items-center gap-2 text-red-700"><span>{error}</span><button type="button" className={`${BUTTON} border-line`} onClick={() => setReload(value => value + 1)}><RotateCcw aria-hidden="true" size={12} />Try again</button></div> : null}
    {decide && conflicts?.length ? <div role="alert" className="rounded-md bg-red-50 px-3 py-2 text-ink dark:bg-red-900/30">
      <p className="m-0"><b>{current.branch}</b> conflicts with <b>{current.baseBranch}</b>, so it can&apos;t land yet. Nothing was changed. Merge it yourself, or discard it and run the workflow again.</p>
      <ul className="m-0 mt-1 pl-4 text-red-800">{conflicts.slice(0, 12).map(path => <li key={path}><code>{path}</code></li>)}{conflicts.length > 12 ? <li>and {conflicts.length - 12} more</li> : null}</ul>
    </div> : null}
    {decide ? <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Decide on the run's changes">
      {confirming === "land"
        ? <><span role="status">Land {current.branch} in {current.baseBranch}? Nothing is pushed.</span>
          <button type="button" className={`${BUTTON} border-brand bg-brand text-white`} disabled={busy} onClick={() => act("land")}><GitMerge aria-hidden="true" size={13} />Land it</button>
          <button type="button" className={`${BUTTON} border-line`} onClick={() => setConfirming("")}>Keep as is</button></>
        : confirming === "discard"
          ? <><span role="status">Discard these changes? Raticode removes the worktree and branch {current.branch}.</span>
            <button type="button" className={`${BUTTON} border-red-600 bg-red-600 text-white`} disabled={busy} onClick={() => act("discard")}><Trash2 aria-hidden="true" size={13} />Discard them</button>
            <button type="button" className={`${BUTTON} border-line`} onClick={() => setConfirming("")}>Keep as is</button></>
          : <><button type="button" className={`${BUTTON} border-brand bg-brand text-white`} disabled={busy || !patch || !status.canLand} title={!current.baseBranch ? "The project wasn't on a branch when the run started" : ""} onClick={() => setConfirming("land")}><GitMerge aria-hidden="true" size={13} />{current.baseBranch ? `Land in ${current.baseBranch}` : "Land"}</button>
            <button type="button" className={`${BUTTON} border-line text-red-700`} disabled={busy} onClick={() => setConfirming("discard")}><Trash2 aria-hidden="true" size={13} />Discard</button></>}
    </div> : null}
    {decide && !patch && !error ? <p role="status" className="m-0 text-muted">Reading the changes…</p> : null}
    {patch?.files?.length ? <div className="flex min-h-[280px] flex-1 overflow-hidden rounded-md border border-line">
      <CommitDiff projectRoot={patch.repository || ""} payload={patch} theme={theme} label={`Changes on ${current.branch}`} />
    </div> : null}
  </section>;
}
