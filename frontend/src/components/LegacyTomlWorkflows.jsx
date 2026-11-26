import { AlertTriangle, Loader2, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Dialog } from "./Dialog.jsx";
import ProjectFolderField from "./ProjectFolderField.jsx";

// Old TOML workflow files left in the data folder. Raticode no longer loads or runs
// them, so the workflow list says so and offers to convert each one to Rattish.
export function LegacyTomlNotice({ files = [], onConvert }) {
  if (!files.length) return null;
  const count = files.length;
  return (
    <section
      aria-label="Old TOML workflows"
      className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
      data-legacy-toml-notice="true"
    >
      <p className="flex items-start gap-1.5 font-semibold">
        <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={13} />
        {count === 1 ? "1 old TOML workflow isn't loaded any more" : `${count} old TOML workflows aren't loaded any more`}
      </p>
      <p className="mt-0.5 text-amber-800 dark:text-amber-200">
        Convert each one to Rattish to keep using it. The .toml file is kept and not changed.
      </p>
      <ul className="mt-1.5 space-y-1">
        {files.map((file) => (
          <li key={file.path} className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate font-mono text-[11px]" title={file.path}>{file.name}</span>
            <button
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-amber-900 transition hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 dark:border-amber-800 dark:bg-transparent dark:text-amber-100 dark:hover:bg-amber-900/40"
              title={`Convert ${file.name} to a Rattish workflow`}
              type="button"
              onClick={() => onConvert?.(file)}
            >
              <RefreshCw aria-hidden="true" size={11} />
              Convert to Rattish
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

// Asks which project the converted workflow goes into, then shows what the conversion
// couldn't carry over, such as triggers to add with Raticode rattish triggers add.
export function ConvertTomlWorkflowDialog({
  defaultProjectRoot = "",
  error = "",
  file,
  result = null,
  saving = false,
  onClose,
  onConvert,
}) {
  const [projectRoot, setProjectRoot] = useState(defaultProjectRoot);
  const [projectError, setProjectError] = useState("");

  useEffect(() => {
    setProjectRoot(defaultProjectRoot);
    setProjectError("");
  }, [defaultProjectRoot, file?.path]);

  if (!file) return null;

  function handleSubmit(event) {
    event.preventDefault();
    const selected = projectRoot.trim();
    if (!selected) {
      setProjectError("Choose the project folder that will own this workflow.");
      return;
    }
    onConvert(file, selected);
  }

  return (
    <Dialog
      description={`Convert ${file.name} to a Rattish workflow`}
      onClose={() => { if (!saving) onClose(); }}
      panelClassName="w-full max-w-[560px] rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": saving || undefined }}
      title="Convert to Rattish"
    >
      <form onSubmit={handleSubmit}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">Convert to Rattish</h2>
            <p className="truncate text-xs text-muted" title={file.path}>{file.path}</p>
          </div>
          <button
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            disabled={saving}
            title="Close"
            type="button"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </div>

        {result ? (
          <div className="space-y-3 px-5 py-5 text-sm" data-convert-result="true">
            <p role="status">
              Converted to <span className="font-semibold">{result.workflow?.name || result.workflow?.id}</span>. The .toml file wasn&apos;t changed.
            </p>
            <ConversionList title="Still to do" items={result.notes} />
            <ConversionList title="Not converted" items={result.unsupported} />
          </div>
        ) : (
          <div className="space-y-4 px-5 py-5">
            <p className="text-xs leading-5 text-muted">
              Raticode writes a new workflow.rattish into this project. The .toml file stays where it is and isn&apos;t changed.
            </p>
            <ProjectFolderField
              defaultProjectRoot={defaultProjectRoot}
              disabled={saving}
              error={projectError}
              hint="Raticode will create .raticode/<workflow-id> inside this folder."
              value={projectRoot}
              onChange={setProjectRoot}
              onError={setProjectError}
            />
            {error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm leading-5 text-red-700" role="alert">
                {error}
              </div>
            ) : null}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-4">
          {result ? (
            <button
              className="h-9 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-indigo-700"
              type="button"
              onClick={onClose}
            >
              Done
            </button>
          ) : (
            <>
              <button
                className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={saving}
                type="button"
                onClick={onClose}
              >
                Cancel
              </button>
              <button
                className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={saving || !projectRoot.trim()}
                type="submit"
              >
                {saving ? <Loader2 aria-hidden="true" size={15} className="animate-spin" /> : <RefreshCw aria-hidden="true" size={15} />}
                {saving ? "Converting..." : "Convert"}
              </button>
            </>
          )}
        </div>
      </form>
    </Dialog>
  );
}

function ConversionList({ items = [], title }) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-muted">{title}</h3>
      <ul className="mt-1 space-y-1">
        {items.map((item, index) => (
          <li key={`${index}-${item}`} className="select-text whitespace-pre-wrap break-words rounded-md bg-slate-50 px-2 py-1 font-mono text-xs">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
