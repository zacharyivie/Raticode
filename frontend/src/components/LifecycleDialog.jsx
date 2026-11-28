import { useId } from "react";
import { Dialog } from "./Dialog.jsx";
import { lifecycleVerb } from "../lib/allWorkflowRuns.js";

const SHOWN = 6;

function title(paths, runs) {
  if (!runs.length) return "Unsaved file changes";
  if (paths.length) return "Unsaved files and running workflows";
  return runs.length === 1 ? "A workflow run is still going" : `${runs.length} workflow runs are still going`;
}

function More({ count }) {
  return count > SHOWN ? <li className="text-muted">and {count - SHOWN} more</li> : null;
}

// What Quit, Restart to update and other backend restarts would lose: unsaved files and
// workflow runs that are still going. One dialog for the whole check; Cancel keeps
// Raticode open. prompt: { reason, paths, runs, busy, error }.
export function LifecycleDialog({ prompt, onCancel, onKeepDrafts, onSaveAll, onContinue }) {
  const messageId = useId();
  const { reason, paths, runs = [], busy, error } = prompt;
  const verb = lifecycleVerb(reason);
  const then = runs.length ? ` and ${verb.toLowerCase()}` : "";
  const heading = title(paths, runs);
  return (
    <Dialog
      title={heading}
      onClose={() => { if (!busy) onCancel(); }}
      panelClassName="ui-dialog w-full max-w-lg p-4"
      panelProps={{ "aria-describedby": messageId, "data-testid": "lifecycle-dialog" }}
    >
      <h2 className="text-sm font-semibold text-ink" aria-hidden="true">{heading}</h2>
      <div id={messageId} className="mt-2 space-y-3 text-sm text-strong">
        {paths.length ? <section>
          <p>Save your files before you {reason}, or keep drafts to recover when the files reopen.</p>
          <ul className="mt-1.5 space-y-0.5 text-xs" aria-label="Unsaved files">
            {paths.slice(0, SHOWN).map(path => <li key={path} className="truncate font-mono" title={path}>{path.split(/[\\/]/).at(-1) || path}</li>)}
            <More count={paths.length} />
          </ul>
        </section> : null}
        {runs.length ? <section>
          <ul className="space-y-1 text-xs" aria-label="Running workflow runs">
            {runs.slice(0, SHOWN).map(run => <li key={run.key}>
              <span className="font-medium text-ink">{run.workflowName}</span>
              <span className="text-muted"> · Started by {run.startedBy} · running {run.length}</span>
            </li>)}
            <More count={runs.length} />
          </ul>
          <p className="mt-1.5">If you {reason} now, {runs.length === 1 ? "this run stops" : "these runs stop"}. <strong>Resume</strong> in Runs starts {runs.length === 1 ? "it" : "each one"} again from the step it was on.</p>
        </section> : null}
      </div>
      {error ? <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p> : null}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button type="button" className="ui-button" data-dialog-initial-focus disabled={busy} onClick={onCancel}>Cancel</button>
        {paths.length ? <>
          <button type="button" className="ui-button" disabled={busy} onClick={onKeepDrafts}>Keep drafts{then}</button>
          <button type="button" className="ui-button ui-button--primary" disabled={busy} onClick={onSaveAll}>Save all{then}</button>
        </> : <button type="button" className="ui-button ui-button--danger" disabled={busy} onClick={onContinue}>{verb} anyway</button>}
      </div>
    </Dialog>
  );
}
