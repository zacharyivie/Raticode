import { AlertCircle, Check, ChevronRight, Code2, FileArchive, FolderOpen, Globe2, Loader2, Plus, Upload, Waypoints, X } from "lucide-react";
import { useRef, useState } from "react";
import RaticodeMark from "./RaticodeMark.jsx";
import brandCompat from "../lib/brandCompat.js";
import { projectNameFromPath } from "../lib/projects.js";

export function EmptyWorkspace({
  error,
  loading,
  notice,
  onCreate,
  onImport,
  onOpenAssistant,
  onOpenProject,
  onOpenSettings,
  onRefresh,
  projectRoot,
}) {
  const importInputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const projectName = projectNameFromPath(projectRoot);

  async function startImport(file) {
    if (!file) return;
    if (!brandCompat.isWorkflowBundle(file)) {
      setImportError("Choose a .raticode bundle.");
      return;
    }
    setImportError("");
    setImporting(true);
    try {
      await onImport(file);
    } finally {
      setImporting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center bg-[#f9fbfd]">
        <div className="flex items-center gap-2 text-sm text-muted" role="status">
          <Loader2 aria-hidden="true" className="animate-spin" size={18} />
          Loading workflows
        </div>
      </div>
    );
  }
  return (
    <div className="empty-workspace relative flex min-h-0 min-w-0 flex-1 flex-col overflow-auto bg-[#f9fbfd]">
      <div aria-hidden="true" className="absolute inset-0 bg-[radial-gradient(circle_at_1px_1px,#dbe3ec_1px,transparent_0)] bg-[size:22px_22px] opacity-45" />
      <div aria-hidden="true" className="empty-workspace-decoration absolute right-[4%] top-1/2 hidden h-64 w-80 -translate-y-1/2">
        <div className="absolute left-4 top-24 h-px w-52 -rotate-[15deg] bg-indigo-200" />
        <div className="absolute left-24 top-24 h-px w-44 rotate-[24deg] bg-indigo-200" />
        <div className="absolute left-10 top-16 h-16 w-32 rounded-[14px] bg-white shadow-panel">
          <span className="absolute left-4 top-4 h-2 w-14 rounded-full bg-indigo-200" />
          <span className="absolute left-4 top-8 h-2 w-20 rounded-full bg-slate-200" />
        </div>
        <div className="absolute bottom-10 right-4 h-16 w-32 rounded-[14px] bg-white shadow-panel">
          <span className="absolute left-4 top-4 h-2 w-16 rounded-full bg-indigo-200" />
          <span className="absolute left-4 top-8 h-2 w-12 rounded-full bg-slate-200" />
        </div>
        <div className="absolute right-24 top-2 h-14 w-28 rounded-[14px] bg-white shadow-panel">
          <span className="absolute left-4 top-4 h-2 w-12 rounded-full bg-indigo-200" />
          <span className="absolute left-4 top-8 h-2 w-16 rounded-full bg-slate-200" />
        </div>
      </div>
      <div className="empty-workspace-panel relative z-10 my-auto w-full max-w-[800px] shrink-0 rounded-2xl">
        <div className="mb-5 grid h-11 w-11 place-items-center rounded-xl bg-indigo-50 text-brand">
          <Waypoints aria-hidden="true" size={24} />
        </div>
        <h2 className="max-w-lg text-[28px] font-semibold leading-[1.15] tracking-[-0.025em] text-ink">
          Build your first local workflow
        </h2>
        <p className="mt-3 max-w-[56ch] text-sm leading-6 text-slate-600">
          Connect commands, scripts, and agents on the graph. Your workflow stays in the project and runs on your machine.
        </p>
        {error ? (
          <div className="mt-5 flex max-w-lg items-start gap-2 rounded-[10px] bg-red-50 px-3 py-2.5 text-sm text-red-800" role="alert">
            <AlertCircle aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
            <span className="min-w-0 flex-1">{error}</span>
            <button className="shrink-0 font-medium underline underline-offset-2" type="button" onClick={onRefresh}>Retry</button>
          </div>
        ) : null}
        {!error && notice?.message ? (
          <div
            className={`mt-5 flex max-w-xl items-start gap-2 rounded-[10px] px-3 py-2.5 text-sm ${notice.type === "error" ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}
            role={notice.type === "error" ? "alert" : "status"}
          >
            {notice.type === "error" ? <AlertCircle aria-hidden="true" className="mt-0.5 shrink-0" size={16} /> : <Check aria-hidden="true" className="mt-0.5 shrink-0" size={16} />}
            <span>{notice.message}</span>
          </div>
        ) : null}
        <input
          ref={importInputRef}
          accept={brandCompat.BUNDLE_ACCEPT}
          className="hidden"
          type="file"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void startImport(file);
            event.target.value = "";
          }}
        />
        <section
          aria-busy={importing || undefined}
          className={`empty-import-zone mt-6 grid max-w-2xl items-center gap-4 rounded-xl border border-dashed p-4 ${dragActive ? "empty-import-zone--active" : ""}`}
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
            void startImport(event.dataTransfer.files?.[0]);
          }}
        >
          <span className="empty-import-icon grid h-12 w-12 shrink-0 place-items-center rounded-xl text-brand">
            <FileArchive aria-hidden="true" size={23} />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-ink">Bring in an existing workflow</h3>
            <p className="mt-1 text-xs leading-5 text-muted">
              {projectRoot
                ? <>The bundle will be added to <strong className="font-semibold text-ink">{projectName}</strong> under <code className="font-mono text-[11px]">.raticode</code>.</>
                : "Choose a bundle, then select the project folder where it should live."}
            </p>
            {importError ? <p className="mt-1 text-xs font-medium text-red-700" role="alert">{importError}</p> : null}
          </div>
          <button
            className="empty-import-button inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            disabled={importing}
            type="button"
            onClick={() => importInputRef.current?.click()}
          >
            {importing ? <Loader2 aria-hidden="true" className="animate-spin" size={16} /> : <Upload aria-hidden="true" size={16} />}
            {importing ? "Importing..." : "Import workflow"}
          </button>
        </section>
        <p className="mt-2 max-w-2xl text-center text-[11px] leading-4 text-muted">Drop a .raticode bundle here</p>
        <div className="empty-workspace-actions mt-5 flex flex-wrap items-center gap-2.5">
          <span className="mr-1 text-xs font-medium text-muted">Starting fresh?</span>
          <button
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3.5 text-sm font-medium text-ink transition hover:border-slate-300 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            type="button"
            onClick={onCreate}
          >
            <Plus aria-hidden="true" size={16} />
            New Workflow
          </button>
          <button
            className="inline-flex h-9 items-center gap-2 px-2 text-sm font-medium text-slate-600 transition hover:text-ink focus-visible:rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600"
            type="button"
            onClick={onOpenProject}
          >
            <FolderOpen aria-hidden="true" size={16} />
            Open project
          </button>
        </div>
        <button className="mt-3 text-xs text-muted underline-offset-2 hover:text-ink hover:underline" type="button" onClick={onOpenSettings}>Workspace settings</button>
        <div className="empty-workspace-features mt-6 grid gap-5 border-t border-line pt-5">
          <div className="flex items-start gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
              <RaticodeMark className="h-8 w-8" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">Rem</p>
              <p className="mt-1 text-xs leading-5 text-muted">
                Describe the automation you need. Installed Codex or Claude Code can use your existing subscription to build it.
              </p>
              <button
                className="mt-2 inline-flex h-7 items-center gap-1 rounded-md text-xs font-semibold text-indigo-700 transition hover:text-indigo-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600"
                type="button"
                onClick={onOpenAssistant}
              >
                Open Rem
                <ChevronRight aria-hidden="true" size={14} />
              </button>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
              <Code2 aria-hidden="true" size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">A full IDE, built in</p>
              <p className="mt-1 text-xs leading-5 text-muted">
                Edit project files, run commands in the terminal, and work with source control without switching tools.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
              <Globe2 aria-hidden="true" size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">Integrated browser</p>
              <p className="mt-1 text-xs leading-5 text-muted">
                Open web apps and documentation beside your project while you build and test workflows.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function WorkflowHealthPanel({ doctorState, workflow }) {
  const globalErrors = doctorState?.errors ?? [];
  const globalWarnings = doctorState?.warnings ?? [];
  const workflowErrors = workflow?.healthErrors ?? [];
  const workflowWarnings = workflow?.healthWarnings ?? [];
  const validationErrors = workflow?.validationErrors ?? [];
  const validationWarnings = workflow?.validationWarnings ?? [];
  const errors = [...globalErrors, ...workflowErrors, ...validationErrors];
  const warnings = [...globalWarnings, ...workflowWarnings, ...validationWarnings];
  const diagnostics = [...errors, ...warnings].filter((diagnostic) =>
    diagnostic?.severity === "error" || diagnostic?.severity === "warning",
  );
  const diagnosticKey = diagnostics
    .map((diagnostic) =>
      [
        diagnostic.id,
        diagnostic.subject ?? "",
        diagnostic.severity,
        diagnostic.message,
      ].join(":"),
    )
    .join("|");
  const [dismissedDiagnosticKey, setDismissedDiagnosticKey] = useState("");
  const [dismissedDoctorError, setDismissedDoctorError] = useState("");
  if (doctorState?.loading && !diagnostics.length) {
    return (
      <section className="border-b border-line bg-white px-5 py-2">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" />
          <span>Checking environment health...</span>
        </div>
      </section>
    );
  }
  if (doctorState?.error && !diagnostics.length) {
    if (dismissedDoctorError === doctorState.error) {
      return null;
    }
    return (
      <section className="border-b border-amber-200 bg-amber-50 px-5 py-2">
        <div className="flex items-center gap-2 text-sm text-amber-800">
          <AlertCircle size={15} className="shrink-0" />
          <span className="min-w-0 flex-1">{doctorState.error}</span>
          <button
            type="button"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-amber-800 transition hover:bg-amber-100 hover:text-amber-950"
            title="Hide environment warning"
            aria-label="Hide environment warning"
            onClick={() => setDismissedDoctorError(doctorState.error)}
          >
            <X size={16} />
          </button>
        </div>
      </section>
    );
  }
  if (!diagnostics.length) {
    return null;
  }
  if (diagnostics.length && dismissedDiagnosticKey === diagnosticKey) {
    return null;
  }

  const errorCount = errors.length;
  return (
    <section className="ui-notice ui-notice--banner px-5 py-3" data-tone={errorCount ? "danger" : "warning"}>
      <div className="flex items-start gap-3">
        <AlertCircle className="ui-notice-icon mt-0.5 shrink-0" size={17} />
        <div className="min-w-0 flex-1">
          <h2 className="ui-notice-title text-sm">
            {errorCount ? "Environment setup needs attention" : "Environment setup warnings"}
          </h2>
          <ul className="mt-1 space-y-1 text-sm leading-5">
            {diagnostics.slice(0, 3).map((diagnostic, index) => (
              <li key={`${diagnostic.id}-${diagnostic.subject ?? "workflow"}-${index}`}>
                {diagnostic.message}
              </li>
            ))}
          </ul>
          {diagnostics.length > 3 ? (
            <p className="mt-1 text-xs text-muted">
              {diagnostics.length - 3} more issue{diagnostics.length === 4 ? "" : "s"} shown in workflow settings.
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className="ui-icon-button ui-icon-button--sm"
          title="Hide environment warning"
          aria-label="Hide environment warning"
          onClick={() => setDismissedDiagnosticKey(diagnosticKey)}
        >
          <X size={16} />
        </button>
      </div>
    </section>
  );
}
