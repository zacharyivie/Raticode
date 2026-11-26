import { Download, FolderOpen, Loader2, X } from "lucide-react";

import { Dialog } from "./Dialog.jsx";

// The workflow export dialog opens on demand. App.jsx loads it lazily so it
// isn't part of the code that loads at startup.

export function ExportWorkflowDialog({
  directory,
  error,
  open,
  saving,
  filename,
  workflow,
  onClose,
  onChooseFolder,
  onExport,
}) {
  if (!open) return null;

  function handleSubmit(event) {
    event.preventDefault();
    onExport(directory);
  }

  return (
    <Dialog
      description={workflow?.name ? `Create a portable bundle for ${workflow.name}` : "Create a portable workflow bundle"}
      onClose={onClose}
      panelClassName="w-full max-w-[600px] rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": saving || undefined }}
      title="Export workflow bundle"
    >
      <form onSubmit={handleSubmit}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-base font-semibold">Export workflow bundle</h2>
            <p className="text-xs text-muted">
              Includes workflow files except those matched by .raticodeignore
            </p>
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

        <div className="space-y-4 px-5 py-5">
          <div>
            <span className="text-xs font-medium text-muted">Export folder</span>
            <button
              autoFocus
              className="mt-1 flex h-10 w-full min-w-0 items-center gap-2 rounded-lg border border-line bg-white px-3 text-left text-sm outline-none transition hover:border-slate-300 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/15 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={saving}
              title="Choose export folder"
              type="button"
              onClick={onChooseFolder}
            >
              <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={16} />
              <span className={`min-w-0 flex-1 truncate ${directory ? "text-ink" : "text-muted"}`}>
                {directory || "Choose a folder"}
              </span>
              <span className="shrink-0 text-xs font-medium text-brand">Choose</span>
            </button>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-[11px] font-medium text-muted">Bundle filename</div>
            <code className="mt-0.5 block truncate text-xs text-ink">
              {filename}
            </code>
          </div>
          {error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm leading-5 text-red-700" role="alert">
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-4">
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
            disabled={saving || !directory.trim()}
            title="Confirm workflow export"
            type="submit"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
            Export
          </button>
        </div>
      </form>
    </Dialog>
  );
}
