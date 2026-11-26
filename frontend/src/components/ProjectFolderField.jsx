import { FolderOpen, Loader2 } from "lucide-react";
import { useState } from "react";

// The project folder a new, imported or converted workflow goes into: a path field and
// a Browse button that opens the desktop folder picker.
export default function ProjectFolderField({
  defaultProjectRoot = "",
  disabled = false,
  error = "",
  hint,
  value,
  onChange,
  onError,
}) {
  const [picking, setPicking] = useState(false);

  async function pickProjectFolder() {
    if (!window.goferDesktop?.workspace?.selectPath) {
      onError("Native folder selection is available in the desktop app.");
      return;
    }
    setPicking(true);
    onError("");
    try {
      const selected = await window.goferDesktop.workspace.selectPath({
        currentPath: value || defaultProjectRoot,
        directoryOnly: true,
      });
      if (selected) onChange(selected);
    } catch (selectionError) {
      onError(
        selectionError instanceof Error
          ? selectionError.message
          : "Unable to open the project folder picker.",
      );
    } finally {
      setPicking(false);
    }
  }

  return (
    <div>
      <span className="text-xs font-medium text-muted">Project folder</span>
      <div className="mt-1 flex gap-2">
        <input
          aria-describedby={error ? "project-folder-error" : "project-folder-hint"}
          aria-label="Project folder"
          className="h-10 min-w-0 flex-1 rounded-lg border border-line px-3 font-mono text-xs outline-none transition focus:border-indigo-500"
          disabled={disabled || picking}
          placeholder="Choose a repository or project folder"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            onError("");
          }}
        />
        <button
          className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled || picking}
          type="button"
          onClick={pickProjectFolder}
        >
          {picking ? <Loader2 size={15} className="animate-spin" /> : <FolderOpen size={15} />}
          Browse
        </button>
      </div>
      {error ? (
        <p className="mt-1 text-xs text-rose-700" id="project-folder-error" role="alert">
          {error}
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted" id="project-folder-hint">
          {hint}
        </p>
      )}
    </div>
  );
}
