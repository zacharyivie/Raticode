import { useCallback, useEffect, useMemo, useState } from "react";

import { ChangesReview } from "./RattishTriggersSection.jsx";
import { ShortcutKeyRecorder } from "./WorkflowShortcutDialog.jsx";
import { formatKeybinding } from "../lib/settings.js";
import {
  WORKFLOW_SHORTCUTS_CHANGED,
  WORKFLOW_SHORTCUT_REVIEW,
  bindingProblem,
  clashMessage,
  hasDesktopShortcuts,
  listWorkflowShortcuts,
  needsReview,
  removeWorkflowShortcut,
  reviewLabel,
  saveWorkflowShortcut,
  shortcutClash,
  shortcutReviewSource,
  shortcutStateLine,
  syncDesktopShortcuts,
  takeShortcutReview,
  useWorkflowShortcutStatus,
} from "../lib/workflowShortcuts.js";

// Review changes for one shortcut. Kept per ID so the source stays the same across renders.
function ShortcutReview({ entry, onDone }) {
  const source = useMemo(() => shortcutReviewSource(entry.id), [entry.id]);
  return (
    <div className="mt-2">
      <ChangesReview source={source} subject="shortcut" onApproved={onDone} onClose={onDone} />
    </div>
  );
}

// Settings > Devices: every workflow shortcut, what pressing it does, whether its key
// works in other apps on this desktop, and Change key / Remove. Shortcuts are added from
// a workflow's menu (Add shortcut…). A shortcut whose workflow changed since its version
// was approved shows Changed: review, which opens Review changes; a changed workflow's
// desktop notice opens it too.
export default function WorkflowShortcutsSettings({ settings }) {
  const [shortcuts, setShortcuts] = useState(null);
  const [error, setError] = useState("");
  const [rowError, setRowError] = useState({});
  const [copied, setCopied] = useState("");
  // The shortcut whose Review changes is open.
  const [reviewing, setReviewing] = useState(() => takeShortcutReview());
  const status = useWorkflowShortcutStatus();
  const desktop = hasDesktopShortcuts();

  const load = useCallback(async () => {
    try {
      setShortcuts(await listWorkflowShortcuts({ details: true }));
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }, []);

  useEffect(() => {
    void load();
    void syncDesktopShortcuts();
    const reload = () => void load();
    const review = () => {
      const id = takeShortcutReview();
      if (id) setReviewing(id);
      void load();
    };
    window.addEventListener(WORKFLOW_SHORTCUTS_CHANGED, reload);
    window.addEventListener(WORKFLOW_SHORTCUT_REVIEW, review);
    return () => {
      window.removeEventListener(WORKFLOW_SHORTCUTS_CHANGED, reload);
      window.removeEventListener(WORKFLOW_SHORTCUT_REVIEW, review);
    };
  }, [load]);

  const closeReview = () => {
    setReviewing("");
    void load();
  };

  async function changeKey(entry, binding) {
    const problem = bindingProblem(binding);
    const clash = problem ? null : shortcutClash(binding, { settings, shortcuts: shortcuts ?? [], own: entry.id });
    if (problem || clash) {
      setRowError((current) => ({ ...current, [entry.id]: problem || clashMessage(clash, binding) }));
      return;
    }
    try {
      await saveWorkflowShortcut({ workflowId: entry.workflowId, binding, input: entry.input });
      setRowError((current) => ({ ...current, [entry.id]: "" }));
    } catch (failure) {
      setRowError((current) => ({ ...current, [entry.id]: failure.message }));
    }
  }

  async function remove(entry) {
    try {
      await removeWorkflowShortcut(entry.id);
    } catch (failure) {
      setRowError((current) => ({ ...current, [entry.id]: failure.message }));
    }
  }

  async function copy(command) {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(command);
    } catch { setCopied(""); }
  }

  const byId = new Map((status?.shortcuts ?? []).map((item) => [item.id, item]));
  return (
    <div className="py-3" data-workflow-shortcuts-settings="">
      <p className="text-xs font-semibold text-ink">Workflow shortcuts</p>
      <p className="mt-1 max-w-[60ch] text-[10px] leading-4 text-muted">
        Keys that run a workflow from any app while Raticode is running. A workflow with no inputs runs straight away; one with a text input opens a small Run sheet filled from the clipboard. A notice reports the result, and clicking it opens the run. Add one from a workflow&apos;s menu: Add shortcut…
      </p>
      {!desktop ? <p role="status" className="mt-2 text-[11px] text-amber-700 dark:text-amber-300">Only the Raticode desktop app can listen for a key in other apps.</p> : null}
      {error ? <p role="alert" className="mt-2 text-[11px] text-red-600">{error}</p> : null}
      {shortcuts === null && !error ? <p role="status" className="mt-2 text-[11px] text-muted">Loading shortcuts…</p> : null}
      {shortcuts?.length === 0 ? <p className="mt-2 text-[11px] text-muted" data-workflow-shortcuts-empty="">No workflow shortcuts yet.</p> : null}
      {shortcuts?.length ? (
        <ul aria-label="Workflow shortcuts" className="mt-3 divide-y divide-line rounded-lg border border-line">
          {shortcuts.map((entry) => {
            const state = byId.get(entry.id);
            const line = shortcutStateLine(entry, state);
            return (
              <li key={entry.id} className="px-3 py-2.5" data-workflow-shortcut={entry.id}>
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-semibold text-ink" title={entry.sourcePath || entry.workflowName}>{entry.workflowName}</p>
                    <p className="mt-0.5 text-[10px] leading-4 text-muted">{entry.target?.summary}</p>
                  </div>
                  {needsReview(entry) ? (
                    <button
                      aria-expanded={reviewing === entry.id}
                      aria-label={`${reviewLabel(entry)} the changes to ${entry.workflowName}`}
                      className="h-7 shrink-0 rounded-md border border-amber-300 bg-amber-50 px-2 text-[11px] font-semibold text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
                      data-shortcut-review={entry.id}
                      type="button"
                      onClick={() => setReviewing((current) => (current === entry.id ? "" : entry.id))}
                    >
                      {reviewLabel(entry)}
                    </button>
                  ) : null}
                  <ShortcutKeyRecorder binding={entry.binding} label={`Shortcut for ${entry.workflowName}`} onRecord={(binding) => void changeKey(entry, binding)} />
                  <button className="h-7 shrink-0 rounded-md border border-line px-2 text-[11px] text-ink hover:bg-slate-50" type="button" aria-label={`Remove the ${formatKeybinding(entry.binding)} shortcut for ${entry.workflowName}`} onClick={() => void remove(entry)}>Remove</button>
                </div>
                {needsReview(entry) && reviewing !== entry.id ? (
                  <p className="mt-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                    {entry.approval.state === "changed"
                      ? "The workflow or a file it runs changed since you approved it. The shortcut won't run it until you review the change."
                      : "The shortcut won't run this workflow until you approve its current version."}
                  </p>
                ) : null}
                {reviewing === entry.id ? <ShortcutReview entry={entry} onDone={closeReview} /> : null}
                {rowError[entry.id] ? <p role="alert" className="mt-1.5 text-[11px] text-red-600">{rowError[entry.id]}</p> : null}
                {line.text ? <p role="status" className={`mt-1.5 text-[11px] ${line.tone === "warn" ? "text-amber-700 dark:text-amber-300" : "text-muted"}`}>{line.text}</p> : null}
                {state?.command ? (
                  <div className="mt-1.5 flex items-start gap-2">
                    <code className="min-w-0 flex-1 select-all break-all rounded-md border border-line bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink dark:bg-slate-900">{state.command}</code>
                    <button className="h-7 shrink-0 rounded-md border border-line px-2 text-[11px] text-ink hover:bg-slate-50" type="button" onClick={() => void copy(state.command)}>{copied === state.command ? "Copied" : "Copy"}</button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
