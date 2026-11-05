import { useEffect, useMemo, useRef, useState } from "react";
import { integrationOperations } from "./WorktreeContextMenu.jsx";
import { hasUnsavedCodeChanges } from "../lib/codeEditorSessions.js";
import RemActionIcon from "./RemActionIcon.jsx";
import BranchTargetPicker from "./BranchTargetPicker.jsx";

const emptyList = [];
const byName = (a, b) => a.localeCompare(b, "en", { numeric: true, sensitivity: "base" }) || (a < b ? -1 : a > b ? 1 : 0);
const remoteRebaseOnly = option => option.type === "remote" ? "Rebase only" : "";
const button = "rounded border border-line px-2 py-1.5 text-xs hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-40";

export default function GitIntegrationControls({ rootPath, sourceControl, worktrees, source, request, onSourceChange, onChanged, onSelectProject, onBusy, disabled }) {
  const [stashes, setStashes] = useState([]);
  const defaultTarget = () => (sourceControl.branch !== source ? sourceControl.branch : sourceControl.branches?.find(branch => branch !== source)) || "";
  const [target, setTarget] = useState(request?.target || defaultTarget());
  const [targetType, setTargetType] = useState(request?.target && request?.targetType === "remote" ? "remote" : "local");
  const [targets, setTargets] = useState(null);
  const [targetsRevision, setTargetsRevision] = useState(0);
  const [kind, setKind] = useState(request?.kind || "merge");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const integrationRef = useRef(null);
  const onBusyRef = useRef(onBusy);
  onBusyRef.current = onBusy;
  useEffect(() => {
    let current = true;
    window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, "stash-list", {}).then(result => {
      if (!current) return;
      if (result?.error) setError(result.error);
      else setStashes(result?.stashes || []);
    }).catch(cause => { if (current) setError(cause.message); });
    return () => { current = false; };
  }, [rootPath, sourceControl.stashCount, revision]);
  useEffect(() => {
    if (source) integrationRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [source]);
  // Remote-tracking branches and recommendations are read only while a branch is being integrated.
  const branchKey = (sourceControl.branches || []).join("\n");
  useEffect(() => {
    if (!source) return undefined;
    let current = true;
    window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, "integration-targets", { source }).then(result => {
      if (current && Array.isArray(result?.local) && Array.isArray(result?.remote)) setTargets(result);
    }).catch(() => {});
    return () => { current = false; };
  }, [rootPath, source, branchKey, revision, targetsRevision]);
  useEffect(() => {
    if (!source || !target) { setPreview(null); setPreviewing(false); return; }
    let current = true;
    const action = `${kind === "rebase" ? "rebase" : "merge"}-preview`;
    const value = { source, target, ...(targetType === "remote" ? { targetType } : {}), ...(kind !== "merge" && kind !== "rebase" ? { strategy: kind } : {}) };
    setPreview(null); setPreviewing(true); setError(""); onBusyRef.current?.(true);
    async function check() {
      try {
        const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, action, value);
        if (!current) return;
        if (!result) throw new Error("Restart the desktop app to enable Git actions.");
        if (result.error) throw new Error(result.error);
        setPreview({ ...result, action, value });
      } catch (cause) { if (current) setError(cause.message); }
      finally { if (current) { setPreviewing(false); onBusyRef.current?.(false); } }
    }
    void check();
    return () => { current = false; onBusyRef.current?.(false); };
  }, [rootPath, source, target, targetType, kind, revision]);

  async function run(action, value, isPreview = false, resolveWithRem = false) {
    if (busy || previewing || disabled) return;
    const destinationRoot = preview?.destinationRoot || rootPath;
    if (!isPreview && (hasUnsavedCodeChanges(rootPath) || hasUnsavedCodeChanges(destinationRoot) || worktrees.some(w => w.branch === source && hasUnsavedCodeChanges(w.path)))) {
      setError("Save your editor changes before changing the working tree."); return;
    }
    setBusy(true); onBusyRef.current?.(true); setError("");
    if (!isPreview) {
      for (const root of new Set([rootPath, destinationRoot])) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath: root, busy: true } }));
    }
    try {
      const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, action, value);
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (!isPreview) {
        await onChanged(result);
        setPreview(null);
        if (result.destinationRoot && result.destinationRoot !== rootPath) onSelectProject?.(result.destinationRoot);
      }
      if (result.error) throw new Error(result.error);
      if (isPreview) setPreview({ ...result, action, value });
      else {
        if (resolveWithRem && result.conflicts?.length) {
          window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
            mode: "conflicts", projectRoot: result.destinationRoot || destinationRoot, text: result.conflicts.join("\n"),
          } }));
        }
        if (action === "merge-branch" || action === "rebase-branch") onSourceChange("");
        else setRevision(n => n + 1);
      }
    } catch (cause) { setError(cause.message); }
    finally {
      setBusy(false); onBusyRef.current?.(false);
      if (!isPreview) for (const root of new Set([rootPath, destinationRoot])) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath: root, busy: false } }));
    }
  }
  const unavailable = busy || previewing || disabled;
  const localTargets = useMemo(() => targets?.local || [...(sourceControl.branches || [])].filter(branch => branch !== source).sort(byName), [targets, sourceControl.branches, source]);
  const recommendedTargets = useMemo(() => targets?.recommended?.filter(item => item && typeof item.name === "string") || (sourceControl.branch && sourceControl.branch !== source ? [{ name: sourceControl.branch, type: "local" }] : emptyList), [targets, sourceControl.branch, source]);
  const selectionDisabled = busy || (disabled && !previewing);
  const operationLabel = integrationOperations.find(([value]) => value === kind)?.[1] || "Merge";
  const branchPreview = preview && preview.action !== "stash-preview" && preview.value.source === source && preview.value.target === target && (preview.value.targetType || "local") === targetType && (preview.value.strategy || (preview.action.startsWith("rebase") ? "rebase" : "merge")) === kind ? preview : null;
  function integrate(resolveWithRem = false) {
    if (!branchPreview) return;
    void run(branchPreview.action.replace("preview", "branch"), { ...branchPreview.value, sourceHash: branchPreview.sourceHash, targetHash: branchPreview.targetHash }, false, resolveWithRem);
  }
  return <div className="space-y-4 text-xs">
    {source ? <section ref={integrationRef} aria-label="Integrate branch" className="mt-3 space-y-3 border-t border-line pt-3">
      <div className="flex items-center justify-between gap-2"><strong className="min-w-0 break-all">Integrate {source}</strong><button type="button" className={button} disabled={busy} onClick={() => onSourceChange("")}>Close</button></div>
      <label className="block">Operation<select aria-label="Integration operation" className="mt-1 w-full rounded border border-line bg-canvas p-2" value={kind} disabled={selectionDisabled} onChange={event => {
        setKind(event.target.value); setPreview(null);
        // Merges change their target, so a remote target only stays for a rebase.
        if (event.target.value !== "rebase" && targetType === "remote") { setTarget(defaultTarget()); setTargetType("local"); }
      }}>{integrationOperations.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <BranchTargetPicker label="Target branch" value={target ? { name: target, type: targetType } : null} disabled={selectionDisabled}
        recommended={recommendedTargets} local={localTargets} remote={targets?.remote || emptyList}
        unavailableReason={kind === "rebase" ? undefined : remoteRebaseOnly}
        onOpen={() => setTargetsRevision(n => n + 1)}
        onChange={choice => { setTarget(choice?.name || ""); setTargetType(choice?.type === "remote" ? "remote" : "local"); setPreview(null); }} />
      {targetType === "remote" && target ? <p className="text-muted">Uses {target} as of the last fetch.</p> : null}
      {previewing ? <p role="status">Checking for conflicts…</p> : branchPreview ? <p role="status" className={branchPreview.conflicts?.length || branchPreview.blocked ? "text-red-700 dark:text-red-300" : "text-muted"}>{branchPreview.blocked ? branchPreview.notice : branchPreview.conflicts?.length ? `Conflicts found in ${operationLabel.toLowerCase()} to ${target}. Continue anyway?` : `No conflicts, safe to ${operationLabel.toLowerCase()}.`}</p> : null}
      <div className="flex items-center gap-2">
        <button type="button" aria-label={operationLabel} className={button} disabled={unavailable || !branchPreview || branchPreview.blocked} onClick={() => integrate()}>{operationLabel}</button>
        {branchPreview?.conflicts?.length ? <button type="button" title="Resolve conflicts with Rem" aria-label="Resolve conflicts with Rem" className={`rem-action-button ${button}`} disabled={unavailable || branchPreview.blocked} onClick={() => integrate(true)}><RemActionIcon size={18} /></button> : null}
      </div>
    </section> : null}
    {stashes.length ? <section aria-label="Stashes" className="mt-4 border-t border-line pt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><strong>Stashes · {stashes.length}</strong><button type="button" className={button} disabled={unavailable} onClick={() => { if (window.confirm(`Permanently discard all ${stashes.length} stashes? This cannot be undone in Raticode.`)) void run("stash-clear", { hashes: stashes.map(s => s.hash) }); }}>Discard stashes</button></div>
      {stashes.map(stash => <div key={stash.hash} className="border-b border-line py-3"><p className="break-words">{stash.ref} · {stash.subject}</p><div className="mt-2 flex gap-2"><button type="button" className={button} disabled={unavailable} onClick={() => { setPreview(null); void run("stash-preview", { hash: stash.hash }, true); }}>Preview stash</button><button type="button" className={button} disabled={unavailable} onClick={() => { if (window.confirm(`Permanently discard ${stash.ref}: ${stash.subject}?`)) void run("stash-drop", { hash: stash.hash }); }}>Discard</button></div></div>)}
    </section> : null}
    {busy ? <p role="status">Checking Git…</p> : null}
    {error ? <p role="alert" className="whitespace-pre-wrap break-words text-red-700 dark:text-red-300">{error}</p> : null}
    {preview?.action === "stash-preview" ? <section aria-label="Git preview" className="space-y-3 border-t border-line pt-3">
      <strong>Stash preview</strong>
      <p role="status" className={preview.conflicts?.length || preview.blocked ? "text-red-700 dark:text-red-300" : "text-muted"}>{preview.notice}</p>
      {preview.conflicts?.length ? <ul className="space-y-1">{preview.conflicts.map(file => <li key={file} className="break-all">! {file}</li>)}</ul> : null}
      <div tabIndex={0} aria-label="Preview diff" className="max-h-80 overflow-auto rounded border border-line p-2 font-mono text-[11px]">{(preview.diff || "No content changes.").split("\n").map((line, i) => <div key={i} className={`whitespace-pre ${line.startsWith("+") ? "text-green-800 dark:text-green-300" : line.startsWith("-") ? "text-red-700 dark:text-red-300" : "text-muted"}`}>{line || " "}</div>)}</div>
      <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={unavailable || preview.blocked} onClick={() => { if (window.confirm("Apply this stash? The saved stash will be kept. Conflicts will pause for resolution.")) void run("stash-apply-selected", preview.value); }}>Apply stash</button><button type="button" className={button} disabled={unavailable} onClick={() => setPreview(null)}>Close preview</button></div>
      <p className="text-muted">Applying keeps the stash as a backup.</p>
    </section> : null}
  </div>;
}
