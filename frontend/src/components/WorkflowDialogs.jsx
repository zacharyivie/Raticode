import { Download, FileArchive, FolderOpen, Loader2, Plus, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Dialog } from "./Dialog.jsx";
import ProjectFolderField from "./ProjectFolderField.jsx";
import { providerStatus } from "./ProviderSettings.jsx";
import { apiUrl } from "../lib/api.js";
import brandCompat from "../lib/brandCompat.js";
import { useProviderSettingsCapabilities } from "../lib/useProviderSettingsCapabilities.js";
import { useRovingChoice } from "../lib/useRovingChoice.js";
import {
  GIT_FOR_WINDOWS_URL,
  missingAppLine,
  starterHint,
  starterOptionLabel,
  starterProviderChoice,
} from "../lib/workflowStarters.js";

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

const WORKFLOW_SOURCES = ["create", "import"];

// remProvider is Rem's app ID (settings.assistant.provider): the Provider choice starts on
// it when a starter's own app isn't installed.
export function CreateWorkflowDialog({
  defaultProjectRoot = "",
  error,
  open,
  remProvider = "",
  saving,
  onClose,
  onCreate,
  onImport,
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState("create");
  const [projectRoot, setProjectRoot] = useState("");
  const [projectError, setProjectError] = useState("");
  const [importFile, setImportFile] = useState(null);
  const [importError, setImportError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [starters, setStarters] = useState([]);
  const [startersError, setStartersError] = useState("");
  const [starterId, setStarterId] = useState("");
  const [providerApps, setProviderApps] = useState([]);
  const [providerPick, setProviderPick] = useState("");
  const importInputRef = useRef(null);
  const nameInputRef = useRef(null);
  const starter = starters.find((item) => item.id === starterId) ?? null;
  // Provider discovery runs only once a picked starter calls a model.
  const { capabilities } = useProviderSettingsCapabilities(
    open && mode === "create" && Boolean(starter?.providerApps?.length),
  );
  const providerChoice = starterProviderChoice(
    starter,
    capabilities.map((app) => ({ ...app, status: providerStatus(app) })),
    providerApps,
    remProvider,
  );
  const chosenProvider = providerChoice.options.some((app) => app.id === providerPick)
    ? providerPick
    : providerChoice.defaultId;
  const chooseMode = (next) => {
    setMode(next);
    if (next === "import") setProjectError("");
  };
  const sourceTab = useRovingChoice({ keys: WORKFLOW_SOURCES, selected: mode, onSelect: chooseMode, orientation: "horizontal" });

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setStartersError("");
    fetch(apiUrl("/rattish/starters"))
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Starter list returned ${response.status}`);
        if (cancelled) return;
        setStarters(Array.isArray(payload.starters) ? payload.starters : []);
        setProviderApps(Array.isArray(payload.providerApps) ? payload.providerApps : []);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setStarters([]);
          setStartersError(loadError instanceof Error ? loadError.message : "Couldn't load the starters.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      setName("");
      setStarterId("");
      setProviderPick("");
      setMode("create");
      setProjectRoot(defaultProjectRoot);
      setProjectError("");
      setImportFile(null);
      setImportError("");
      setDragActive(false);
    }
  }, [defaultProjectRoot, open]);

  // Name takes focus once per opening, not each time Create new is shown again: the arrow
  // keys keep focus on the source tabs. The dialog stays mounted, so a reopening can start
  // on Import; Name gets focus on the first render after the reset shows Create new.
  const focusNameRef = useRef(false);
  useEffect(() => {
    focusNameRef.current = open;
  }, [open]);
  useEffect(() => {
    if (!open || mode !== "create" || !focusNameRef.current) return;
    focusNameRef.current = false;
    nameInputRef.current?.focus();
  }, [open, mode]);

  if (!open) return null;

  function handleSubmit(event) {
    event.preventDefault();
    const selectedProject = projectRoot.trim();
    if (!selectedProject) {
      setProjectError("Choose the project folder that will own this workflow.");
      return;
    }
    if (mode === "import") {
      if (!importFile) {
        setImportError("Choose a .raticode bundle to import.");
        return;
      }
      onImport(importFile, selectedProject);
      return;
    }
    onCreate(name, {
      projectRoot: selectedProject,
      projectGrantId: window.goferDesktop?.workspace?.pathGrantForApi?.(selectedProject) ?? "",
      ...(starter ? { starter: starter.id } : {}),
      ...(starter && chosenProvider ? { provider: chosenProvider } : {}),
    });
  }

  function chooseImportFile(file) {
    if (!file) return;
    if (!brandCompat.isWorkflowBundle(file)) {
      setImportFile(null);
      setImportError("Choose a .raticode bundle.");
      return;
    }
    setImportFile(file);
    setImportError("");
  }

  return (
    <Dialog
      description="Create a new workflow or import an existing workflow bundle"
      onClose={onClose}
      panelClassName="w-full max-w-[560px] rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": saving || undefined }}
      title="New workflow"
    >
      <form onSubmit={handleSubmit}>
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-base font-semibold">New workflow</h2>
            <p className="text-xs text-muted">Start blank or from a starter, or import a bundle</p>
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
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1" role="tablist" aria-label="Workflow source">
            <button
              aria-selected={mode === "create"}
              className={`inline-flex h-9 items-center justify-center gap-2 rounded-md text-sm font-medium transition ${
                mode === "create" ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
              disabled={saving}
              role="tab"
              type="button"
              onClick={() => chooseMode("create")}
              {...sourceTab("create")}
            >
              <Plus aria-hidden="true" size={15} />
              Create new
            </button>
            <button
              aria-selected={mode === "import"}
              className={`inline-flex h-9 items-center justify-center gap-2 rounded-md text-sm font-medium transition ${
                mode === "import" ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
              disabled={saving}
              role="tab"
              type="button"
              onClick={() => chooseMode("import")}
              {...sourceTab("import")}
            >
              <Upload aria-hidden="true" size={15} />
              Import
            </button>
          </div>
          {mode === "create" ? (
            <>
              <label className="block">
                <span className="text-xs font-medium text-muted">Start from</span>
                <select
                  aria-describedby={providerChoice.missing.length ? "workflow-starter-hint workflow-starter-apps" : "workflow-starter-hint"}
                  className="mt-1 h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-indigo-500"
                  disabled={saving}
                  value={starterId}
                  onChange={(event) => {
                    setStarterId(event.target.value);
                    setProviderPick("");
                  }}
                >
                  <option value="">Blank workflow</option>
                  {starters.map((item) => (
                    <option key={item.id} value={item.id}>{starterOptionLabel(item)}</option>
                  ))}
                </select>
                <span className="mt-1 block text-xs text-muted" id="workflow-starter-hint">
                  {startersError
                    ? `Starters aren't available: ${startersError}`
                    : starterHint(starter)}
                </span>
                {providerChoice.missing.length ? (
                  <span className="mt-1 block text-xs font-medium text-amber-800" id="workflow-starter-apps">
                    {providerChoice.missing.map(missingAppLine).join(" ")}
                    {providerChoice.options.length ? "" : " No other app that runs its model steps is installed."}
                  </span>
                ) : null}
                {starter?.needsGitForWindows ? (
                  <a
                    className="mt-1 mr-3 inline-block text-xs font-medium text-amber-800 underline-offset-2 hover:underline"
                    href={GIT_FOR_WINDOWS_URL}
                    rel="noreferrer"
                    target="_blank"
                  >
                    Install Git for Windows for its shell steps
                  </a>
                ) : null}
                {starter?.docsUrl ? (
                  <a
                    className="mt-1 inline-block text-xs font-medium text-indigo-700 underline-offset-2 hover:underline"
                    href={starter.docsUrl}
                    rel="noreferrer"
                    target="_blank"
                  >
                    Read the {starter.name} guide
                  </a>
                ) : null}
              </label>
              {providerChoice.options.length ? (
                <label className="block">
                  <span className="text-xs font-medium text-muted">Provider</span>
                  <select
                    aria-describedby="workflow-provider-hint"
                    className="mt-1 h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-indigo-500"
                    disabled={saving}
                    value={chosenProvider}
                    onChange={(event) => setProviderPick(event.target.value)}
                  >
                    {providerChoice.options.map((app) => (
                      <option key={app.id} value={app.id}>{app.name}</option>
                    ))}
                  </select>
                  <span className="mt-1 block text-xs text-muted" id="workflow-provider-hint">
                    The starter's model steps call this app instead.
                  </span>
                </label>
              ) : null}
              <label className="block">
                <span className="text-xs font-medium text-muted">Name</span>
                <input
                  ref={nameInputRef}
                  className="mt-1 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none transition focus:border-indigo-500"
                  disabled={saving}
                  placeholder={starter ? starter.name : "Daily Analysis"}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
            </>
          ) : (
            <div>
              <span className="text-xs font-medium text-muted">Workflow bundle</span>
              <input
                ref={importInputRef}
                accept={brandCompat.BUNDLE_ACCEPT}
                className="hidden"
                type="file"
                onChange={(event) => {
                  chooseImportFile(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
              <button
                aria-describedby={importError ? "workflow-import-error" : "workflow-import-hint"}
                className={`mt-1 flex min-h-24 w-full items-center gap-3 rounded-lg border border-dashed px-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 ${dragActive ? "border-indigo-500 bg-indigo-50" : "border-indigo-200 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/60"}`}
                disabled={saving}
                type="button"
                onClick={() => importInputRef.current?.click()}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setDragActive(false);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "copy";
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragActive(false);
                  chooseImportFile(event.dataTransfer.files?.[0]);
                }}
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-indigo-100 text-indigo-700">
                  <FileArchive aria-hidden="true" size={19} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">
                    {importFile?.name || "Choose a .raticode bundle"}
                  </span>
                  <span className="mt-1 block text-xs text-muted">
                    {importFile ? "Ready to import" : "Browse or drop a bundle here"}
                  </span>
                </span>
                <span className="shrink-0 text-xs font-semibold text-indigo-700">
                  {importFile ? "Replace" : "Browse"}
                </span>
              </button>
              {importError ? (
                <p className="mt-1 text-xs text-rose-700" id="workflow-import-error" role="alert">
                  {importError}
                </p>
              ) : (
                <p className="mt-1 text-xs text-muted" id="workflow-import-hint">
                  Raticode checks the bundle before adding it to your project.
                </p>
              )}
            </div>
          )}
          <ProjectFolderField
            defaultProjectRoot={defaultProjectRoot}
            disabled={saving}
            error={projectError}
            hint={mode === "create"
              ? "Raticode will create .raticode/<workflow-id> inside this folder."
              : "Raticode will add the imported workflow under .raticode."}
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
            disabled={saving || !projectRoot.trim() || (mode === "create" ? !name.trim() && !starter : !importFile)}
            type="submit"
          >
            {saving ? (
              <Loader2 aria-hidden="true" size={15} className="animate-spin" />
            ) : mode === "create" ? (
              <Plus aria-hidden="true" size={15} />
            ) : (
              <Upload aria-hidden="true" size={15} />
            )}
            {saving ? (mode === "create" ? "Creating..." : "Importing...") : mode === "create" ? "Create" : "Import"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
