import { useEffect, useRef, useState } from "react";
import { Keyboard } from "lucide-react";

import { Dialog } from "./Dialog.jsx";
import { eventToKeybinding, formatKeybinding } from "../lib/settings.js";
import {
  bindingProblem,
  clashMessage,
  hasDesktopShortcuts,
  listWorkflowShortcuts,
  removeWorkflowShortcut,
  saveWorkflowShortcut,
  shortcutClash,
  suspendSystemShortcuts,
  workflowShortcutTarget,
} from "../lib/workflowShortcuts.js";

/**
 * Records one key combination for a system-wide shortcut. While it listens, Raticode's
 * own system-wide shortcuts let keys through. onRecord gets the binding
 * ("Ctrl+Alt+KeyR"); Escape stops listening without one.
 */
export function ShortcutKeyRecorder({ binding, label, onRecord, autoFocus = false }) {
  const [recording, setRecording] = useState(false);
  const buttonRef = useRef(null);
  const recordingRef = useRef(false);

  useEffect(() => {
    if (autoFocus) buttonRef.current?.focus();
  }, [autoFocus]);
  // Never leave the shortcuts suspended, even if the dialog closes mid-recording.
  useEffect(() => () => { if (recordingRef.current) suspendSystemShortcuts(false); }, []);

  function start() {
    if (recordingRef.current) return;
    recordingRef.current = true;
    suspendSystemShortcuts(true);
    setRecording(true);
  }

  function stop() {
    if (recordingRef.current) suspendSystemShortcuts(false);
    recordingRef.current = false;
    setRecording(false);
  }

  return (
    <button
      ref={buttonRef}
      aria-label={recording ? `Recording ${label}. Press the keys, or Escape to stop.` : `${label}: ${binding ? formatKeybinding(binding) : "none"}. Press to record a key.`}
      className={`flex h-9 min-w-44 items-center justify-center gap-2 rounded-md border px-3 font-mono text-xs transition ${
        recording ? "border-brand bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-200" : "border-line bg-white text-ink hover:bg-slate-50 dark:bg-transparent"
      }`}
      data-shortcut-recorder={recording ? "recording" : "idle"}
      type="button"
      onBlur={stop}
      onClick={start}
      onKeyDown={(event) => {
        if (!recording) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Escape") {
          stop();
          return;
        }
        const next = eventToKeybinding(event);
        if (!next) return;
        stop();
        onRecord(next);
      }}
    >
      <Keyboard aria-hidden="true" size={14} />
      {recording ? "Press keys…" : binding ? formatKeybinding(binding) : "Record a key"}
    </button>
  );
}

// "Add shortcut…" from a workflow's menu: record a system-wide key that runs this
// workflow from any app, see what pressing it will do, and save it.
export default function WorkflowShortcutDialog({ workflow, settings, onClose, onSaved }) {
  const [target, setTarget] = useState(null);
  const [existing, setExisting] = useState(null);
  const [others, setOthers] = useState([]);
  const [binding, setBinding] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const desktop = hasDesktopShortcuts();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([workflowShortcutTarget(workflow.id), listWorkflowShortcuts()]).then(([payload, list]) => {
      if (cancelled) return;
      setTarget(payload.target);
      setExisting(payload.shortcut);
      setBinding(payload.shortcut?.binding || "");
      setInput(payload.target?.input || "");
      setOthers(list);
    }, (failure) => { if (!cancelled) setError(failure.message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workflow.id]);

  const problem = binding ? bindingProblem(binding) : "";
  const clash = binding && !problem ? shortcutClash(binding, { settings, shortcuts: others, own: existing?.id }) : null;
  const blocked = !binding || Boolean(problem) || Boolean(clash) || target?.mode === "unavailable";
  const textInputs = target?.textInputs ?? [];
  const chosenLabel = textInputs.find((item) => item.name === input)?.label || target?.inputLabel || input;

  async function save() {
    if (blocked || saving) return;
    setSaving(true);
    setError("");
    try {
      const saved = await saveWorkflowShortcut({ workflowId: workflow.id, binding, input: target?.mode === "text" ? input : null });
      onSaved?.(saved);
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!existing || saving) return;
    setSaving(true);
    setError("");
    try {
      await removeWorkflowShortcut(existing.id);
      onSaved?.(null);
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      title={`Shortcut for ${workflow.name}`}
      description="Record a key that runs this workflow from any app while Raticode is running."
      onClose={onClose}
      panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink shadow-panel"
    >
      <div data-workflow-shortcut-dialog="">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{existing ? "Change shortcut" : "Add shortcut"}</p>
        <h2 className="mt-0.5 truncate text-base font-semibold" title={workflow.name}>{workflow.name}</h2>
        <p className="mt-2 text-xs leading-5 text-muted">
          Press it in any app while Raticode is running, even minimized.
        </p>
        {!desktop ? (
          <p role="status" className="mt-3 text-xs text-amber-700 dark:text-amber-300">Only the Raticode desktop app can listen for a key in other apps.</p>
        ) : null}
        {loading ? (
          <p role="status" className="mt-4 text-xs text-muted">Checking the workflow…</p>
        ) : target ? (
          <>
            <div className="mt-4 flex items-center gap-3">
              <ShortcutKeyRecorder binding={binding} label="Shortcut" autoFocus onRecord={(next) => { setBinding(next); setError(""); }} />
              {binding ? <span className="text-[11px] text-muted">Click to record another key.</span> : null}
            </div>
            {problem ? <p role="alert" className="mt-2 text-xs text-red-600">{problem}</p> : null}
            {clash ? <p role="alert" className="mt-2 text-xs text-red-600" data-shortcut-clash={clash.kind}>{clashMessage(clash, binding)}</p> : null}
            <div className="mt-4 rounded-lg border border-line px-3 py-2.5 text-xs leading-5" data-shortcut-mode={target.mode}>
              {target.mode === "run" ? (
                <p><span className="font-semibold">Runs straight away</span>, as if you pressed Run. A notice tells you when it finishes.</p>
              ) : target.mode === "text" ? (
                <>
                  <p><span className="font-semibold">Opens a small Run sheet</span> filled with the clipboard for {chosenLabel}. Read it, then press Enter to run. A notice tells you when it finishes.</p>
                  {textInputs.length > 1 ? (
                    <label className="mt-2 flex items-center gap-2">
                      <span className="text-muted">Clipboard goes to</span>
                      <select className="h-7 rounded-md border border-line bg-white px-1.5 text-xs dark:bg-transparent" value={input} onChange={(event) => setInput(event.target.value)}>
                        {textInputs.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}
                      </select>
                    </label>
                  ) : null}
                </>
              ) : (
                <p className="text-amber-700 dark:text-amber-300">{target.problem}</p>
              )}
            </div>
          </>
        ) : null}
        {error ? <p role="alert" className="mt-3 text-xs text-red-600">{error}</p> : null}
        <div className="mt-5 flex items-center gap-2">
          {existing ? (
            <button className="h-8 rounded-md px-2 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50 dark:text-red-300 dark:hover:bg-[#3a2424]" disabled={saving} type="button" onClick={() => void remove()}>Remove shortcut</button>
          ) : null}
          <span className="flex-1" />
          <button className="h-8 rounded-md border border-line px-3 text-xs" type="button" onClick={onClose}>Cancel</button>
          <button className="h-8 rounded-md bg-brand px-3 text-xs font-semibold text-white disabled:opacity-50" disabled={blocked || saving || loading} type="button" onClick={() => void save()}>
            {saving ? "Saving…" : "Save shortcut"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
