import { Download, FolderOpen, Loader2, RefreshCw, X } from "lucide-react";

import { Dialog } from "./Dialog.jsx";

// Workflow dialogs that open on demand. App.jsx loads them lazily to keep its
// startup chunk within budget.

export function WorkflowHistoryDialog({
  diff,
  error,
  loading,
  revisions,
  workflow,
  onClose,
  onPreview,
  onRefresh,
  onRestore,
}) {
  return (
    <Dialog
      description={`${workflow.name} revision history`}
      onClose={onClose}
      panelClassName="flex max-h-[86vh] w-full max-w-[920px] flex-col rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": loading || undefined }}
      title="Workflow history"
    >
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">Workflow history</h2>
            <p className="truncate text-xs text-muted">
              {workflow.name} · {workflow.id}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="grid h-8 w-8 place-items-center rounded-lg border border-line text-muted transition hover:bg-slate-50 hover:text-ink"
              title="Refresh history"
              type="button"
              onClick={onRefresh}
            >
              <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
              title="Close"
              type="button"
              onClick={onClose}
            >
              <X size={17} />
            </button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[340px_minmax(0,1fr)] overflow-hidden">
          <div className="workflow-scrollbar min-h-0 overflow-y-auto border-r border-line">
            {error ? (
              <div className="border-b border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
                {error}
              </div>
            ) : null}
            {loading && !revisions.length ? (
              <div className="px-4 py-6 text-sm text-muted">Loading history...</div>
            ) : null}
            {!loading && !revisions.length ? (
              <div className="px-4 py-6 text-sm text-muted">No revisions found.</div>
            ) : null}
            {revisions.map((revision) => (
              <div key={revision.revisionId} className="border-b border-line px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">
                      {formatRevisionDate(revision.createdAt)}
                    </p>
                    <p className="mt-0.5 text-xs text-muted">
                      {revision.source} · {revision.author}
                    </p>
                  </div>
                  <button
                    className="shrink-0 rounded-md border border-line px-2 py-1 text-[11px] font-medium text-muted transition hover:bg-slate-50 hover:text-ink"
                    type="button"
                    onClick={() => onPreview(revision.revisionId)}
                  >
                    Diff
                  </button>
                </div>
                <ul className="mt-2 space-y-1 text-xs text-slate-600">
                  {(revision.summary ?? []).slice(0, 4).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <div className="mt-3 flex gap-2">
                  <button
                    className="rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                    type="button"
                    onClick={() => onRestore(revision.revisionId)}
                  >
                    Restore
                  </button>
                  <button
                    className="rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                    type="button"
                    onClick={() => onRestore(revision.revisionId, { asCopy: true })}
                  >
                    Restore as copy
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="min-h-0 overflow-hidden">
            {diff ? (
              <div className="flex h-full flex-col">
                <div className="border-b border-line px-4 py-3">
                  <p className="text-sm font-semibold">Revision diff</p>
                  <p className="mt-1 text-xs text-muted">
                    {(diff.summary ?? []).join("; ") || "No material changes"}
                  </p>
                </div>
                <pre className="workflow-scrollbar min-h-0 flex-1 overflow-auto bg-[#0f172a] p-4 text-xs leading-5 text-slate-100">
                  {diff.tomlDiff || "No TOML diff."}
                </pre>
              </div>
            ) : (
              <div className="grid h-full place-items-center px-8 text-center text-sm text-muted">
                Select a revision diff to inspect TOML and graph-level changes.
              </div>
            )}
          </div>
        </div>
    </Dialog>
  );
}

function formatRevisionDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

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
  const rattishBundle = workflow?.sourceFormat === "rattish";

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
              {rattishBundle
                ? "Includes workflow files except those matched by .raticodeignore"
                : workflow?.name
                  ? `Create a portable bundle for ${workflow.name}`
                  : "Create a portable workflow bundle"}
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
