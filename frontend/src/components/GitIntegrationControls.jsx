import { useEffect, useRef, useState } from "react";
import { integrationOperations } from "./WorktreeContextMenu.jsx";
import { hasUnsavedCodeChanges } from "../lib/codeEditorSessions.js";

const button = "rounded border border-line px-2 py-1.5 text-xs hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-40";

export default function GitIntegrationControls({ rootPath, sourceControl, worktrees, source, request, onSourceChange, onChanged, onSelectProject, onBusy, disabled }) {
  const [stashes, setStashes] = useState([]);
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState("merge");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const integrationRef = useRef(null);
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
    setPreview(null);
    setTarget(request?.target || "");
    setKind(request?.kind || "merge");
    if (request) integrationRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [source, request]);

  async function run(action, value, isPreview = false) {
    if (busy || disabled) return;
    const destinationRoot = preview?.destinationRoot || rootPath;
    if (!isPreview && (hasUnsavedCodeChanges(rootPath) || hasUnsavedCodeChanges(destinationRoot) || worktrees.some(w => w.branch === source && hasUnsavedCodeChanges(w.path)))) {
      setError("Save your editor changes before changing the working tree."); return;
    }
    setBusy(true); onBusy?.(true); setError("");
    if (!isPreview) {
      for (const root of new Set([rootPath, destinationRoot])) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath: root, busy: true } }));
    }
    try {
      const result = await window.goferDesktop?.workspace?.gitRepoAction?.(rootPath, action, value);
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (!isPreview) {
        await onChanged(result);
        setRevision(n => n + 1); setPreview(null);
        if (result.destinationRoot && result.destinationRoot !== rootPath) onSelectProject?.(result.destinationRoot);
      }
      if (result.error) throw new Error(result.error);
      if (isPreview) setPreview({ ...result, action, value });
    } catch (cause) { setError(cause.message); }
    finally {
      setBusy(false); onBusy?.(false);
      if (!isPreview) for (const root of new Set([rootPath, destinationRoot])) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath: root, busy: false } }));
    }
  }
  const unavailable = busy || disabled;
  const previewLabel = preview?.action?.startsWith("rebase") ? "Rebase" : (integrationOperations.find(([value]) => value === (preview?.value?.strategy || "merge"))?.[1] || "Merge");
  return <div className="space-y-4 text-xs">
    {source ? <section ref={integrationRef} aria-label="Integrate branch" className="mt-3 space-y-3 border-t border-line pt-3">
      <div className="flex items-center justify-between gap-2"><strong className="min-w-0 break-all">Integrate {source}</strong><button type="button" className={button} disabled={unavailable} onClick={() => onSourceChange("")}>Close</button></div>
      <label className="block">Operation<select aria-label="Integration operation" className="mt-1 w-full rounded border border-line bg-canvas p-2" value={kind} disabled={unavailable} onChange={event => { setKind(event.target.value); setPreview(null); }}>{integrationOperations.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="block">Target branch<select aria-label="Target branch" className="mt-1 w-full rounded border border-line bg-canvas p-2" value={target} disabled={unavailable} onChange={event => { setTarget(event.target.value); setPreview(null); }}><option value="">Choose a branch</option>{sourceControl.branches?.filter(b => b !== source).map(b => <option key={b} value={b}>{b}</option>)}</select></label>
      <p className="text-muted">{kind !== "rebase" ? "Apply committed changes to the target branch. Squash merge leaves changes staged for you to commit. Fast-forward only refuses diverged branches." : "Rebase rewrites the source branch's commits. Avoid rebasing commits others depend on."}</p>
      <button type="button" className={button} disabled={unavailable || !target} onClick={() => { setPreview(null); void run(`${kind === "rebase" ? "rebase" : "merge"}-preview`, { source, target, ...(kind !== "merge" && kind !== "rebase" ? { strategy: kind } : {}) }, true); }}>Preview {kind}</button>
    </section> : null}
    {stashes.length ? <section aria-label="Stashes" className="mt-4 border-t border-line pt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><strong>Stashes · {stashes.length}</strong><button type="button" className={button} disabled={unavailable} onClick={() => { if (window.confirm(`Permanently discard all ${stashes.length} stashes? This cannot be undone in Raticode.`)) void run("stash-clear", { hashes: stashes.map(s => s.hash) }); }}>Discard stashes</button></div>
      {stashes.map(stash => <div key={stash.hash} className="border-b border-line py-3"><p className="break-words">{stash.ref} · {stash.subject}</p><div className="mt-2 flex gap-2"><button type="button" className={button} disabled={unavailable} onClick={() => { setPreview(null); void run("stash-preview", { hash: stash.hash }, true); }}>Preview stash</button><button type="button" className={button} disabled={unavailable} onClick={() => { if (window.confirm(`Permanently discard ${stash.ref}: ${stash.subject}?`)) void run("stash-drop", { hash: stash.hash }); }}>Discard</button></div></div>)}
    </section> : null}
    {busy ? <p role="status">Checking Git…</p> : null}
    {error ? <p role="alert" className="whitespace-pre-wrap break-words text-red-700 dark:text-red-300">{error}</p> : null}
    {preview ? <section aria-label="Git preview" className="space-y-3 border-t border-line pt-3">
      <strong>{preview.action === "stash-preview" ? "Stash preview" : `${preview.value.source} → ${preview.value.target}`}</strong>
      <p role="status" className={preview.conflicts?.length || preview.blocked ? "text-red-700 dark:text-red-300" : "text-muted"}>{preview.notice}</p>
      {preview.conflicts?.length ? <ul className="space-y-1">{preview.conflicts.map(file => <li key={file} className="break-all">! {file}</li>)}</ul> : null}
      <div tabIndex={0} aria-label="Preview diff" className="max-h-80 overflow-auto rounded border border-line p-2 font-mono text-[11px]">{(preview.diff || "No content changes.").split("\n").map((line, i) => <div key={i} className={`whitespace-pre ${line.startsWith("+") ? "text-green-800 dark:text-green-300" : line.startsWith("-") ? "text-red-700 dark:text-red-300" : "text-muted"}`}>{line || " "}</div>)}</div>
      <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={unavailable || preview.blocked} onClick={() => {
        const stash = preview.action === "stash-preview";
        if (window.confirm(stash ? "Apply this stash? The saved stash will be kept. Conflicts will pause for resolution." : `${previewLabel} ${preview.value.source} ${preview.action.startsWith("rebase") ? "onto" : "into"} ${preview.value.target}? Conflicts will pause for resolution.`)) void run(stash ? "stash-apply-selected" : preview.action.replace("preview", "branch"), { ...preview.value, sourceHash: preview.sourceHash, targetHash: preview.targetHash });
      }}>{preview.action === "stash-preview" ? "Apply stash" : `${previewLabel} branch`}</button><button type="button" className={button} disabled={unavailable} onClick={() => setPreview(null)}>Close preview</button></div>
      {preview.action === "stash-preview" ? <p className="text-muted">Applying keeps the stash as a backup.</p> : <p className="text-muted">{preview.value.strategy === "squash" ? "Review and commit the staged squash result before removing the source worktree." : "After a successful merge, you can delete the source branch when you no longer need it. Remove its worktree first if it has one."}</p>}
    </section> : null}
  </div>;
}
