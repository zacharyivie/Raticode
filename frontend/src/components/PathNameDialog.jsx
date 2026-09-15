import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { Dialog } from "./Dialog.jsx";

export function PathNameDialog({ directory, initialName = "", kind, mode, onClose, onSubmit }) {
  const [name, setName] = useState(initialName);
  const [submitting, setSubmitting] = useState(false);
  const title =
    mode === "rename"
      ? `Rename ${kind}`
      : kind === "file"
        ? "Create file"
        : "Create folder";
  const action = mode === "rename" ? "Rename" : "Create";

  async function submit(event) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    setSubmitting(true);
    try {
      await onSubmit(trimmedName);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      description={directory}
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[95] grid place-items-center bg-slate-950/25 px-4"
      panelClassName="w-full max-w-sm rounded-lg border border-line bg-white p-4 shadow-panel"
      title={title}
    >
      <form onSubmit={submit}>
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-strong">{title}</h3>
          <p className="mt-1 truncate text-xs text-muted" title={directory}>
            {directory}
          </p>
        </div>
        <input
          autoFocus
          className="h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-teal-500"
          placeholder={kind === "file" ? "new-file.txt" : "new-folder"}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            disabled={submitting}
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={submitting || !name.trim()}
            type="submit"
          >
            {submitting ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            {action}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

