// The node inspector's form fields, path and text file dialogs, field diagnostics and
// approval reply fields.
import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, Eye, FilePenLine, FileText, FolderOpen, Loader2, X } from "lucide-react";
import { isUrlPath, joinPath, resolveDisplayPath } from "../lib/displayPaths.js";
import { isExactReference } from "../lib/rattishFieldEditors.js";
import { useConfirm } from "./ConfirmDialog.jsx";
import { Dialog } from "./Dialog.jsx";
import { PathNameDialog } from "./PathNameDialog.jsx";

export function fieldDiagnosticState(diagnostics = []) {
  const visibleDiagnostics = diagnostics.filter((diagnostic) =>
    diagnostic?.severity === "error" || diagnostic?.severity === "warning",
  );
  if (!visibleDiagnostics.length) {
    return { diagnostics: [], severity: null };
  }
  return {
    diagnostics: visibleDiagnostics,
    severity: visibleDiagnostics.some((diagnostic) => diagnostic.severity === "error")
      ? "error"
      : "warning",
  };
}

export function fieldBorderClass(diagnostics = [], base = "border-line focus:border-brand") {
  const { severity } = fieldDiagnosticState(diagnostics);
  if (severity === "error") return "border-red-300 focus:border-red-500 focus:ring-red-100";
  if (severity === "warning") return "border-amber-300 focus:border-amber-500 focus:ring-amber-100";
  return base;
}

export function FieldDiagnosticMessage({ diagnostics = [], id }) {
  const { diagnostics: visibleDiagnostics, severity } = fieldDiagnosticState(diagnostics);
  if (!visibleDiagnostics.length) return null;
  return (
    <p
      id={id}
      className={`mt-1 text-xs leading-5 ${
        severity === "error" ? "text-red-700" : "text-amber-800"
      }`}
    >
      {visibleDiagnostics[0].message}
    </p>
  );
}

export function InspectorSection({ children, className = "", title }) {
  return (
    <section className={`space-y-3 rounded-lg border border-line p-3 ${className}`}>
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">{title}</h3>
      {children}
    </section>
  );
}

// Each approvals poll returns new arrays, so reset drafts on a stable identity instead.
export function approvalIdentity(approval) {
  if (!approval) return "";
  return [approval.workflowId, approval.runId, approval.nodeId, approval.requestedAt].join("\n");
}

export function approvalReplyMode(approval) {
  return approval?.reply === "optional" || approval?.reply === "required" ? approval.reply : "none";
}

// A gate's typed reply: shown only when the workflow asks for one.
export function ApprovalReplyField({ approval, compact = false, onChange, value }) {
  const mode = approvalReplyMode(approval);
  if (mode === "none") return null;
  const label = mode === "required" ? "Reply (required)" : "Reply (optional)";
  return (
    <label className={`block space-y-1 font-medium text-slate-600 ${compact ? "text-[11px]" : "text-xs"}`}>
      <span>{label}</span>
      <textarea
        aria-required={mode === "required"}
        className={`w-full resize-y rounded-md border border-line bg-white text-ink outline-none transition placeholder:text-muted/70 focus:border-brand focus:ring-2 focus:ring-brand/15 ${
          compact ? "min-h-[56px] px-2 py-1.5 text-xs" : "min-h-[70px] px-3 py-2 text-sm"
        }`}
        placeholder="Type your reply to the workflow"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

// One button per option, in the order the workflow lists them, instead of Approve and Reject.
export function ApprovalOptionButtons({ approval, compact = false, disabled, onChoose }) {
  const rejecting = new Set(approval?.rejectOptions ?? []);
  return (
    <div
      aria-label="Answer options"
      className={compact ? "grid grid-cols-2 gap-2" : "grid gap-3 sm:grid-cols-2"}
      role="group"
    >
      {approval.options.map((option) => {
        const reject = rejecting.has(option);
        return (
          <button
            key={option}
            className={
              compact
                ? `h-8 min-w-0 truncate rounded-md border px-2 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    reject
                      ? "border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
                      : "border-line bg-white text-ink hover:border-brand hover:bg-brand/5"
                  }`
                : `flex min-h-12 min-w-0 items-center justify-center rounded-md border px-4 py-2 text-base font-semibold shadow-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    reject
                      ? "border-red-300 bg-red-50 text-red-700 hover:bg-red-100"
                      : "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                  }`
            }
            disabled={disabled}
            title={reject ? `Answer ${option} (counts as a rejection)` : `Answer ${option}`}
            type="button"
            onClick={() => onChoose(option)}
          >
            <span className="truncate">{option}</span>
          </button>
        );
      })}
    </div>
  );
}

// What the inspector is showing, such as "node:fetch". Fields keep their unsaved draft
// and error for one scope, so selecting another node never shows the last one's error.
const DraftScopeContext = createContext("");
export const InspectorDraftScope = DraftScopeContext.Provider;

export function useInspectorDraftScope() {
  return useContext(DraftScopeContext);
}

export function useCommittedDraft({ enterCommits = true, format, onCommit, parse, value }) {
  const committedText = format(value);
  const scope = useContext(DraftScopeContext);
  const scopeRef = useRef(scope);
  const committedRef = useRef(committedText);
  const draftRef = useRef(committedText);
  const focusedRef = useRef(false);
  const [draft, setDraft] = useState(committedText);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);

  // Before paint, so the other node's draft never flashes in the field.
  useLayoutEffect(() => {
    if (scopeRef.current === scope) return;
    scopeRef.current = scope;
    committedRef.current = committedText;
    draftRef.current = committedText;
    setDraft(committedText);
    setError("");
    setConflict(false);
  }, [committedText, scope]);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    const previousCommitted = committedRef.current;
    if (committedText === previousCommitted) return;

    committedRef.current = committedText;
    if (focusedRef.current && draftRef.current !== previousCommitted) {
      setConflict(true);
      return;
    }

    draftRef.current = committedText;
    setDraft(committedText);
    setError("");
    setConflict(false);
  }, [committedText]);

  function validate(text) {
    const parsed = parse(text);
    setError(parsed.ok ? "" : parsed.error);
    return parsed;
  }

  function commitText(text = draftRef.current) {
    const parsed = validate(text);
    if (!parsed.ok) return false;

    const normalized = format(parsed.value);
    const previous = committedRef.current;
    const changed = normalized !== previous;
    committedRef.current = normalized;
    draftRef.current = normalized;
    setDraft(normalized);
    setConflict(false);
    const commitScope = scopeRef.current;
    const result = changed ? onCommit(parsed.value) : undefined;
    // A refused save resolves to null or { error }. The field keeps what the user typed
    // and says why under it; Escape or "Use saved value" puts the saved value back.
    if (typeof result?.then === "function") {
      void result.then((saved) => {
        const refusal = refusedSaveMessage(saved);
        if (!refusal || scopeRef.current !== commitScope || committedRef.current !== normalized) return;
        committedRef.current = previous;
        if (draftRef.current !== normalized) return;
        setError(refusal);
      });
    }
    return true;
  }

  function restore() {
    draftRef.current = committedRef.current;
    setDraft(committedRef.current);
    setError("");
    setConflict(false);
  }

  return {
    conflict,
    draft,
    error,
    commitText,
    restore,
    onBlur() {
      focusedRef.current = false;
      commitText();
    },
    onChange(nextDraft) {
      draftRef.current = nextDraft;
      setDraft(nextDraft);
      validate(nextDraft);
    },
    onFocus() {
      focusedRef.current = true;
    },
    onKeyDown(event) {
      if (enterCommits && event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        commitText();
      } else if (event.key === "Escape") {
        event.preventDefault();
        restore();
      }
    },
  };
}

// What an onCommit result says about a refused save: null means refused with no reason,
// { error } carries the reason, anything else (a saved document, undefined) means saved.
export function refusedSaveMessage(result) {
  if (result === null) return "This change wasn't saved.";
  if (result && typeof result === "object" && typeof result.error === "string") {
    return result.error || "This change wasn't saved.";
  }
  return "";
}

export function DraftFieldMessage({ id, state }) {
  if (!state.error && !state.conflict) return null;
  return (
    <div id={id}>
      {state.error ? (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {state.error}
        </p>
      ) : null}
      {state.conflict ? (
        <p className="mt-1 text-xs text-amber-700" role="status">
          This value changed elsewhere. Your draft is preserved; press Escape to use the latest value.
        </p>
      ) : null}
      <button
        className="mt-1 text-xs font-medium text-muted underline underline-offset-2 transition hover:text-ink"
        type="button"
        // Keep focus in the field so its blur doesn't save the draft first.
        onMouseDown={(event) => event.preventDefault()}
        onClick={state.restore}
      >
        Use saved value
      </button>
    </div>
  );
}

export function TextField({
  commitOnBlur = false,
  diagnostics = [],
  label,
  onChange,
  pathBasePath = "",
  pathLink = false,
  pathPicker = false,
  placeholder,
  parseDraft = (text) => ({ ok: true, value: text }),
  readOnly = false,
  value,
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pathInfo, setPathInfo] = useState(null);
  const [textFileDialog, setTextFileDialog] = useState(null);
  const isPathField = pathPicker || pathLink;
  const canPickPath = pathPicker && !readOnly && typeof onChange === "function";
  const canDraftPath = isPathField && !readOnly && typeof onChange === "function";
  const canDraftText = commitOnBlur && !readOnly && typeof onChange === "function";
  const canDraft = canDraftPath || canDraftText;
  const draftState = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: (nextValue) => onChange?.(nextValue),
    parse: parseDraft,
    value,
  });
  const storedPath = canDraft ? draftState.draft : String(value ?? "");
  const displayValue = isPathField ? resolveDisplayPath(storedPath, pathBasePath) : value ?? "";
  const inputValue = canDraft ? draftState.draft : displayValue;
  const canOpenPath = isPathField && displayValue && !isUrlPath(displayValue);
  const canEditTextPath = canOpenPath && pathInfo?.isFile;
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    canDraft && (draftState.error || draftState.conflict) ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;

  useEffect(() => {
    let cancelled = false;

    async function loadPathInfo() {
      if (!canOpenPath) {
        setPathInfo(null);
        return;
      }
      try {
        const info = await window.goferDesktop?.workspace?.getPathInfo?.(displayValue);
        if (!cancelled) {
          setPathInfo(info ?? null);
        }
      } catch {
        if (!cancelled) {
          setPathInfo(null);
        }
      }
    }

    loadPathInfo();
    return () => {
      cancelled = true;
    };
  }, [canOpenPath, displayValue]);

  async function handlePathPick(event) {
    event.preventDefault();
    event.stopPropagation();

    if (window.goferDesktop?.workspace?.listDirectory) {
      setPickerOpen(true);
      return;
    }

    try {
      const selectedPath = await window.goferDesktop?.workspace?.selectPath?.({
        currentPath: displayValue,
      });
      if (selectedPath) {
        draftState.commitText(selectedPath);
      }
    } catch (error) {
      console.error("Failed to select path", error);
    }
  }

  async function handlePathOpen(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!displayValue) return;

    try {
      const info = await window.goferDesktop?.workspace?.getPathInfo?.(displayValue);
      if (info?.isDirectory) {
        await window.goferDesktop?.workspace?.openPath?.(displayValue);
      } else {
        await window.goferDesktop?.workspace?.revealPath?.(displayValue);
      }
    } catch (error) {
      console.error("Failed to reveal path", error);
    }
  }

  return (
    <>
      <label className="block">
        <span className="text-xs font-medium text-muted">{label}</span>
        <span className="relative mt-1 block">
          <input
            aria-describedby={describedBy}
            aria-invalid={
              Boolean(canDraft && draftState.error) || fieldDiagnosticState(diagnostics).severity === "error" || undefined
            }
            className={`h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition read-only:bg-slate-50 ${fieldBorderClass(diagnostics)} ${
              canDraft && draftState.error ? "border-red-400 focus:border-red-500" : ""
            } ${
              canPickPath && canOpenPath ? "pr-[4.5rem]" : canPickPath || canOpenPath ? "pr-10" : ""
            }`}
            placeholder={placeholder}
            readOnly={readOnly}
            title={displayValue}
            value={inputValue}
            onBlur={canDraft ? draftState.onBlur : undefined}
            onChange={
              canDraft
                ? (event) => draftState.onChange(event.target.value)
                : (event) => onChange?.(event.target.value)
            }
            onFocus={canDraft ? draftState.onFocus : undefined}
            onKeyDown={canDraft ? draftState.onKeyDown : undefined}
          />
          {canOpenPath ? (
            <button
              aria-label={`Open ${label?.toLowerCase?.() ?? "path"}`}
              className={`absolute top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-strong dark:hover:bg-[#2a2a2a] ${
                canPickPath ? "right-9" : "right-2"
              }`}
              title={`Open ${label?.toLowerCase?.() ?? "path"} in file browser`}
              type="button"
              onClick={handlePathOpen}
            >
              <ExternalLink size={16} strokeWidth={1.9} />
            </button>
          ) : null}
          {canPickPath ? (
            <button
              aria-label={`Choose ${label?.toLowerCase?.() ?? "path"}`}
              className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-strong dark:hover:bg-[#2a2a2a]"
              title={`Choose ${label?.toLowerCase?.() ?? "path"}`}
              type="button"
              onClick={handlePathPick}
            >
              <FolderOpen size={17} strokeWidth={1.9} />
            </button>
          ) : null}
        </span>
        <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
        {canDraft ? <DraftFieldMessage id={draftDiagnosticId} state={draftState} /> : null}
      </label>
      {canEditTextPath ? (
        <div className="mt-1 flex justify-end gap-2">
          <button
            className="inline-flex items-center gap-1 text-[11px] font-medium text-muted underline-offset-2 transition hover:text-ink hover:underline"
            type="button"
            onClick={() => setTextFileDialog({ mode: "edit", path: displayValue })}
          >
            <FilePenLine size={12} />
            edit
          </button>
          <button
            className="inline-flex items-center gap-1 text-[11px] font-medium text-muted underline-offset-2 transition hover:text-ink hover:underline"
            type="button"
            onClick={() => setTextFileDialog({ mode: "preview", path: displayValue })}
          >
            <Eye size={12} />
            preview
          </button>
        </div>
      ) : null}
      {pickerOpen ? (
        <PathPickerDialog
          currentPath={displayValue}
          label={label}
          onClose={() => setPickerOpen(false)}
          onSelect={(selectedPath) => {
            draftState.commitText(selectedPath);
            setPickerOpen(false);
          }}
        />
      ) : null}
      {textFileDialog ? (
        <TextFileDialog
          mode={textFileDialog.mode}
          path={textFileDialog.path}
          onClose={() => setTextFileDialog(null)}
        />
      ) : null}
    </>
  );
}

export function TextFileDialog({ mode, path, onClose }) {
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const readOnly = mode === "preview";

  useEffect(() => {
    let cancelled = false;

    async function loadFile() {
      setLoading(true);
      setError("");
      try {
        const payload = await window.goferDesktop?.textFiles?.read?.(path);
        if (!cancelled) {
          setContent(payload?.content ?? "");
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadFile();
    return () => {
      cancelled = true;
    };
  }, [path]);

  async function saveFile() {
    setSaving(true);
    setError("");
    try {
      await window.goferDesktop?.textFiles?.write?.({ targetPath: path, content });
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save file");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      description={path}
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[80] grid place-items-center ui-scrim px-4"
      panelClassName="flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": loading || saving || undefined }}
      title={readOnly ? "Preview file" : "Edit file"}
    >
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-strong">
              {readOnly ? "Preview file" : "Edit file"}
            </h2>
            <p className="mt-1 truncate text-xs text-muted" title={path}>
              {path}
            </p>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink dark:hover:bg-[#2a2a2a]"
            title="Close"
            type="button"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-[340px] flex-1 p-4">
          {loading ? (
            <div className="flex h-48 items-center justify-center text-sm text-muted">
              <Loader2 size={16} className="mr-2 animate-spin" />
              Loading file
            </div>
          ) : error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </div>
          ) : (
            <textarea
              className="h-[52vh] min-h-[320px] w-full resize-none rounded-lg border border-line bg-slate-50 p-3 font-mono text-xs leading-5 text-ink outline-none transition focus:border-brand read-only:bg-slate-50"
              readOnly={readOnly}
              spellCheck={false}
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onClose}
          >
            Close
          </button>
          {!readOnly ? (
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading || saving || Boolean(error)}
              type="button"
              onClick={saveFile}
            >
              {saving ? <Loader2 size={15} className="animate-spin" /> : <FilePenLine size={15} />}
              Save
            </button>
          ) : null}
        </div>
    </Dialog>
  );
}

export function PathPickerDialog({ currentPath, label, onClose, onSelect }) {
  const [confirm, confirmDialog] = useConfirm();
  const [contextMenu, setContextMenu] = useState(null);
  const [copiedEntry, setCopiedEntry] = useState(null);
  const [nameRequest, setNameRequest] = useState(null);
  const [directory, setDirectory] = useState("");
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [parent, setParent] = useState(null);
  const [pathCopied, setPathCopied] = useState(false);
  const [selectedPath, setSelectedPath] = useState(currentPath ?? "");
  const titleLabel = typeof label === "string" && label.trim() ? label : "path";

  useEffect(() => {
    loadDirectory(currentPath);
  }, [currentPath]);

  async function loadDirectory(nextPath) {
    setContextMenu(null);
    setLoading(true);
    setError("");
    try {
      const payload = await window.goferDesktop.workspace.listDirectory({
        currentPath: nextPath ?? "",
      });
      setDirectory(payload.directory);
      setParent(payload.parent);
      setEntries(payload.entries ?? []);
      setSelectedPath(payload.directory);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function copyCurrentPath() {
    if (!directory) return;

    try {
      await navigator.clipboard.writeText(directory);
      setPathCopied(true);
      window.setTimeout(() => setPathCopied(false), 1400);
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy path");
    }
  }

  async function openCurrentPath() {
    if (!directory) return;

    try {
      await window.goferDesktop?.workspace?.openPath?.(directory);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Unable to open path");
    }
  }

  function showContextMenu(event, entry = null) {
    event.preventDefault();
    event.stopPropagation();
    if (!directory) return;
    setContextMenu({
      entry,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function requestCreateChild(kind) {
    setContextMenu(null);
    setNameRequest({ kind, mode: "create" });
  }

  function requestRenameEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    setNameRequest({
      entry,
      initialName: entry.name,
      kind: entry.isDirectory ? "folder" : "file",
      mode: "rename",
    });
  }

  async function createChild(kind, name) {
    if (!name) return;

    try {
      let result;
      if (kind === "file") {
        result = await window.goferDesktop?.workspace?.createFile?.({ directory, name });
      } else {
        result = await window.goferDesktop?.workspace?.createFolder?.({ directory, name });
      }
      await loadDirectory(directory);
      if (result?.path) {
        setSelectedPath(result.path);
      }
      setNameRequest(null);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : `Unable to create ${kind}`);
    }
  }

  async function renameEntry(entry, name) {
    if (!entry || !name) return;

    try {
      const result = await window.goferDesktop?.workspace?.renamePath?.({
        sourcePath: entry.path,
        name,
      });
      await loadDirectory(directory);
      if (result?.path) {
        setSelectedPath(result.path);
      }
      setNameRequest(null);
    } catch (renameError) {
      setError(renameError instanceof Error ? renameError.message : "Unable to rename path");
    }
  }

  async function copyEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    setCopiedEntry(entry);
  }

  async function pasteEntry() {
    setContextMenu(null);
    if (!copiedEntry) return;

    const existingNames = new Set(entries.map((entry) => entry.name));
    const nextName = nextCopyName(copiedEntry.name, existingNames);
    try {
      await window.goferDesktop?.workspace?.copyPath?.({
        sourcePath: copiedEntry.path,
        destinationPath: joinPath(directory, nextName),
      });
      await loadDirectory(directory);
      setSelectedPath(joinPath(directory, nextName));
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy path");
    }
  }

  async function deleteEntry(entry) {
    setContextMenu(null);
    if (!entry) return;
    if (!await confirm({ confirmLabel: "Move to trash", danger: true, message: `Move ${entry.name} to the trash? This ${entry.isDirectory ? "folder" : "file"} can be restored from the operating system trash.` })) return;

    try {
      await window.goferDesktop?.workspace?.deletePath?.(entry.path);
      if (selectedPath === entry.path) {
        setSelectedPath(directory);
      }
      await loadDirectory(directory);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete path");
    }
  }

  return (
    <Dialog
      description="Browse the local workspace and select a path"
      onClose={onClose}
      overlayClassName="fixed inset-0 z-[70] grid place-items-center ui-scrim px-4"
      panelClassName="flex max-h-[78vh] w-full max-w-[680px] flex-col rounded-lg border border-line bg-white shadow-panel"
      panelProps={{ "aria-busy": loading || undefined }}
      title={`Choose ${titleLabel.toLowerCase()}`}
    >
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-strong">
              Choose {titleLabel.toLowerCase()}
            </h2>
            <div className="mt-1 flex min-w-0 items-center gap-1.5">
              <button
                className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-[#2a2a2a]"
                disabled={!directory}
                title={pathCopied ? "Copied" : "Copy path"}
                type="button"
                onClick={copyCurrentPath}
              >
                {pathCopied ? <Check size={13} /> : <Copy size={13} />}
              </button>
              <button
                className="min-w-0 truncate text-left text-xs text-indigo-700 underline-offset-2 transition hover:text-indigo-800 hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline"
                disabled={!directory}
                title={directory}
                type="button"
                onClick={openCurrentPath}
              >
                {directory || "Loading..."}
              </button>
            </div>
          </div>
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink dark:hover:bg-[#2a2a2a]"
            title="Close"
            type="button"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-line px-4 py-2">
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!parent || loading}
            type="button"
            onClick={() => loadDirectory(parent)}
          >
            Up
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={loading}
            type="button"
            onClick={async () => {
              const dataDir = await window.goferDesktop.getDataDir();
              loadDirectory(dataDir);
            }}
          >
            Gofer data
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory || loading}
            type="button"
            onClick={() => requestCreateChild("file")}
          >
            New file
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory || loading}
            type="button"
            onClick={() => requestCreateChild("folder")}
          >
            New folder
          </button>
          <button
            className="h-8 rounded-md border border-line bg-white px-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!directory}
            type="button"
            onClick={() => onSelect(directory)}
          >
            Choose current folder
          </button>
        </div>

        <div
          className="min-h-[260px] flex-1 overflow-y-auto p-2"
          onContextMenu={(event) => showContextMenu(event)}
        >
          {loading ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted">
              <Loader2 size={16} className="mr-2 animate-spin" />
              Loading folder
            </div>
          ) : null}
          {error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </div>
          ) : null}
          {!loading && !error && entries.length === 0 ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted">
              This folder is empty.
            </div>
          ) : null}
          {!loading && !error
            ? entries.map((entry) => (
                <button
                  key={entry.path}
                  className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition hover:bg-slate-50 ${
                    selectedPath === entry.path ? "bg-slate-100 text-strong" : "text-slate-700"
                  }`}
                  type="button"
                  onContextMenu={(event) => showContextMenu(event, entry)}
                  onClick={() =>
                    entry.isDirectory ? loadDirectory(entry.path) : setSelectedPath(entry.path)
                  }
                >
                  {entry.isDirectory ? (
                    <FolderOpen className="text-indigo-600" size={16} />
                  ) : (
                    <FileText className="text-muted" size={16} />
                  )}
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  {entry.hidden ? <span className="text-[11px] text-muted">hidden</span> : null}
                </button>
              ))
            : null}
        </div>
        {contextMenu ? (
          <PathContextMenu
            canPaste={Boolean(copiedEntry)}
            copiedName={copiedEntry?.name}
            entry={contextMenu.entry}
            x={contextMenu.x}
            y={contextMenu.y}
            onCopy={() => copyEntry(contextMenu.entry)}
            onCreateFile={() => requestCreateChild("file")}
            onCreateFolder={() => requestCreateChild("folder")}
            onDelete={() => deleteEntry(contextMenu.entry)}
            onPaste={pasteEntry}
            onRename={() => requestRenameEntry(contextMenu.entry)}
          />
        ) : null}
        {nameRequest ? (
          <PathNameDialog
            directory={directory}
            initialName={nameRequest.initialName}
            kind={nameRequest.kind}
            mode={nameRequest.mode}
            onClose={() => setNameRequest(null)}
            onSubmit={(name) =>
              nameRequest.mode === "rename"
                ? renameEntry(nameRequest.entry, name)
                : createChild(nameRequest.kind, name)
            }
          />
        ) : null}

        <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3">
          <p className="min-w-0 truncate text-xs text-muted">{selectedPath || directory}</p>
          <div className="flex shrink-0 items-center gap-2">
            <button
              className="h-9 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!selectedPath}
              type="button"
              onClick={() => onSelect(selectedPath)}
            >
              <Check size={15} />
              Choose
            </button>
          </div>
        </div>
        {confirmDialog}
    </Dialog>
  );
}

function PathContextMenu({
  canPaste,
  copiedName,
  entry,
  onCopy,
  onCreateFile,
  onCreateFolder,
  onDelete,
  onPaste,
  onRename,
  x,
  y,
}) {
  return (
    <div
      className="fixed z-[90] min-w-44 overflow-hidden rounded-lg border border-line bg-white py-1 text-sm shadow-panel"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {entry ? (
        <>
          <button
            className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onRename}
          >
            Rename
          </button>
          <button
            className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
            type="button"
            onClick={onCopy}
          >
            Copy
          </button>
          <button
            className="block w-full px-3 py-2 text-left text-red-600 transition hover:bg-red-50"
            type="button"
            onClick={onDelete}
          >
            Delete
          </button>
          <div className="my-1 border-t border-line" />
        </>
      ) : null}
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400 disabled:hover:bg-transparent"
        disabled={!canPaste}
        title={canPaste ? `Paste ${copiedName}` : "Copy a file or folder first"}
        type="button"
        onClick={onPaste}
      >
        Paste
      </button>
      <div className="my-1 border-t border-line" />
      <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
        Create new
      </div>
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
        type="button"
        onClick={onCreateFile}
      >
        File
      </button>
      <button
        className="block w-full px-3 py-2 text-left text-slate-700 transition hover:bg-slate-50"
        type="button"
        onClick={onCreateFolder}
      >
        Folder
      </button>
    </div>
  );
}

function nextCopyName(name = "", existingNames = new Set()) {
  let candidate = defaultCopyName(name);
  let index = 2;
  while (existingNames.has(candidate)) {
    candidate = defaultCopyName(name, index);
    index += 1;
  }
  return candidate;
}

function defaultCopyName(name = "", index = null) {
  const value = String(name || "copy");
  const dotIndex = value.lastIndexOf(".");
  const suffix = index ? ` copy ${index}` : " copy";
  if (dotIndex > 0) {
    return `${value.slice(0, dotIndex)}${suffix}${value.slice(dotIndex)}`;
  }
  return `${value}${suffix}`;
}

export function NumberField({
  allowRuntimeReference = false,
  diagnostics = [],
  label,
  min,
  onChange,
  placeholder,
  value,
}) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const draftState = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => parseNumberDraft(text, min, allowRuntimeReference),
    value,
  });
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    draftState.error || draftState.conflict ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <input
        aria-describedby={describedBy}
        aria-invalid={
          Boolean(draftState.error) || fieldDiagnosticState(diagnostics).severity === "error" || undefined
        }
        className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition ${fieldBorderClass(diagnostics)} ${
          draftState.error ? "border-red-400 focus:border-red-500" : ""
        }`}
        inputMode={allowRuntimeReference ? "text" : "decimal"}
        placeholder={placeholder}
        type="text"
        value={draftState.draft}
        onBlur={draftState.onBlur}
        onChange={(event) => draftState.onChange(event.target.value)}
        onFocus={draftState.onFocus}
        onKeyDown={draftState.onKeyDown}
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      <DraftFieldMessage id={draftDiagnosticId} state={draftState} />
    </label>
  );
}

export function parseNumberDraft(text, min, allowRuntimeReference = false) {
  const trimmed = String(text).trim();
  if (!trimmed) return { ok: true, value: "" };
  if (allowRuntimeReference && isExactReference(trimmed)) {
    return { ok: true, value: trimmed };
  }
  if (!/^[+-]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:[eE][+-]?\d+)?$/.test(trimmed)) {
    return {
      ok: false,
      error: allowRuntimeReference
        ? "Enter a complete number or an exact {{...}} reference."
        : "Enter a complete number.",
    };
  }

  const number = Number(trimmed);
  if (!Number.isFinite(number)) {
    return { ok: false, error: "Enter a finite number." };
  }
  if (min !== undefined && number < Number(min)) {
    return { ok: false, error: `Enter ${min} or greater.` };
  }
  return { ok: true, value: number };
}

function RuntimeReferenceField({ label, onChange, value }) {
  const state = useCommittedDraft({
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => {
      const trimmed = text.trim();
      return isExactReference(trimmed)
        ? { ok: true, value: trimmed }
        : { ok: false, error: "Enter an exact reference such as {{inputs.value}}." };
    },
    value,
  });
  const messageId = useId();
  return (
    <label className="mt-2 block">
      <span className="text-xs text-muted">{label} reference</span>
      <input
        aria-describedby={state.error || state.conflict ? messageId : undefined}
        aria-invalid={Boolean(state.error) || undefined}
        className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 font-mono text-sm outline-none transition ${
          state.error ? "border-red-400 focus:border-red-500" : "border-line focus:border-brand"
        }`}
        placeholder="{{inputs.value}}"
        type="text"
        value={state.draft}
        onBlur={state.onBlur}
        onChange={(event) => state.onChange(event.target.value)}
        onFocus={state.onFocus}
        onKeyDown={state.onKeyDown}
      />
      <DraftFieldMessage id={messageId} state={state} />
    </label>
  );
}

export function SelectField({
  allowRuntimeReference = false,
  diagnostics = [],
  label,
  onChange,
  options,
  value,
}) {
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const usesRuntimeReference = allowRuntimeReference && isExactReference(value);
  const selectedValue = usesRuntimeReference ? "__runtime_reference__" : (value ?? "");
  return (
    <div className="block">
      <label>
        <span className="text-xs font-medium text-muted">{label}</span>
        <select
          aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
          aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
          className={`mt-1 h-10 w-full rounded-lg border bg-white px-3 text-sm outline-none transition ${fieldBorderClass(diagnostics)}`}
          value={selectedValue}
          onChange={(event) =>
            onChange(
              event.target.value === "__runtime_reference__"
                ? (usesRuntimeReference ? value : "{{inputs.value}}")
                : event.target.value,
            )
          }
        >
          {options.map(([optionValue, labelText]) => (
            <option key={optionValue} value={optionValue}>
              {labelText}
            </option>
          ))}
          {allowRuntimeReference ? (
            <option value="__runtime_reference__">Exact runtime reference…</option>
          ) : null}
        </select>
      </label>
      {usesRuntimeReference ? (
        <RuntimeReferenceField label={label} value={value} onChange={onChange} />
      ) : null}
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

export function TextareaField({
  commitOnBlur = false,
  diagnostics = [],
  label,
  onChange,
  placeholder,
  rows = 3,
  value,
}) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const draftState = useCommittedDraft({
    enterCommits: false,
    format: (nextValue) => String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => ({ ok: true, value: text }),
    value,
  });
  const describedBy =
    [
      hasFieldDiagnostics ? diagnosticId : null,
      commitOnBlur && (draftState.error || draftState.conflict) ? draftDiagnosticId : null,
    ]
      .filter(Boolean)
      .join(" ") || undefined;
  const draftError = commitOnBlur && draftState.error;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <textarea
        aria-describedby={describedBy}
        aria-invalid={
          Boolean(draftError) || fieldDiagnosticState(diagnostics).severity === "error" || undefined
        }
        className={`mt-1 w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none transition ${fieldBorderClass(diagnostics)} ${
          draftError ? "border-red-400 focus:border-red-500" : ""
        }`}
        placeholder={placeholder}
        rows={rows}
        value={commitOnBlur ? draftState.draft : value ?? ""}
        onBlur={commitOnBlur ? draftState.onBlur : undefined}
        onChange={(event) =>
          commitOnBlur ? draftState.onChange(event.target.value) : onChange(event.target.value)
        }
        onFocus={commitOnBlur ? draftState.onFocus : undefined}
        onKeyDown={
          commitOnBlur
            ? (event) => {
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  draftState.commitText();
                  return;
                }
                draftState.onKeyDown(event);
              }
            : undefined
        }
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      {commitOnBlur ? <DraftFieldMessage id={draftDiagnosticId} state={draftState} /> : null}
    </label>
  );
}

export function ToggleField({
  allowRuntimeReference = false,
  checked,
  diagnostics = [],
  disabled = false,
  label,
  onChange,
}) {
  const diagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const usesRuntimeReference = allowRuntimeReference && isExactReference(checked);
  return (
    <div>
      <label
        className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 ${fieldBorderClass(diagnostics, "border-line")} ${
          disabled ? "cursor-not-allowed bg-slate-50 text-muted dark:bg-[#252526]" : ""
        }`}
      >
        <span className="text-sm font-medium text-slate-700">{label}</span>
        <input
          aria-describedby={hasFieldDiagnostics ? diagnosticId : undefined}
          aria-invalid={fieldDiagnosticState(diagnostics).severity === "error" || undefined}
          checked={usesRuntimeReference ? false : Boolean(checked)}
          className="h-4 w-4 accent-indigo-600"
          disabled={disabled}
          type="checkbox"
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
      {allowRuntimeReference && !disabled ? (
        <button
          className="mt-1 text-xs font-medium text-indigo-700 hover:text-indigo-800"
          type="button"
          onClick={() => onChange(usesRuntimeReference ? false : "{{inputs.value}}")}
        >
          {usesRuntimeReference ? "Use a literal toggle" : "Use an exact runtime reference"}
        </button>
      ) : null}
      {usesRuntimeReference ? (
        <RuntimeReferenceField label={label} value={checked} onChange={onChange} />
      ) : null}
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
    </div>
  );
}

export function ListField({ diagnostics = [], label, onChange, placeholder, value }) {
  const state = useCommittedDraft({
    format: (nextValue) =>
      Array.isArray(nextValue) ? nextValue.join(", ") : String(nextValue ?? ""),
    onCommit: onChange,
    parse: (text) => {
      const trimmed = text.trim();
      if (isExactReference(trimmed)) return { ok: true, value: trimmed };
      return {
        ok: true,
        value: text
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      };
    },
    value,
  });
  return (
    <DraftTextareaField
      diagnostics={diagnostics}
      label={label}
      placeholder={placeholder}
      rows={2}
      state={state}
    />
  );
}

export function DraftTextareaField({ diagnostics, label, placeholder, rows, state }) {
  const diagnosticId = useId();
  const draftDiagnosticId = useId();
  const hasFieldDiagnostics = fieldDiagnosticState(diagnostics).diagnostics.length > 0;
  const describedBy = [
    hasFieldDiagnostics ? diagnosticId : null,
    state.error || state.conflict ? draftDiagnosticId : null,
  ]
    .filter(Boolean)
    .join(" ") || undefined;
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <textarea
        aria-describedby={describedBy}
        aria-invalid={
          Boolean(state.error) || fieldDiagnosticState(diagnostics).severity === "error" || undefined
        }
        className={`mt-1 w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none transition ${fieldBorderClass(diagnostics)} ${
          state.error ? "border-red-400 focus:border-red-500" : ""
        }`}
        placeholder={placeholder}
        rows={rows}
        value={state.draft}
        onBlur={state.onBlur}
        onChange={(event) => state.onChange(event.target.value)}
        onFocus={state.onFocus}
        onKeyDown={state.onKeyDown}
      />
      <FieldDiagnosticMessage diagnostics={diagnostics} id={diagnosticId} />
      <DraftFieldMessage id={draftDiagnosticId} state={state} />
    </label>
  );
}

export function formatJsonBodyEditorValue(value) {
  if (value === null || value === undefined || value === "") return "";
  return JSON.stringify(value, null, 2);
}

export function parseJsonBodyEditorValue(text) {
  if (!text.trim()) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Invalid JSON",
    };
  }
}

export function JsonBodyField({ label, onChange, value }) {
  const draftState = useCommittedDraft({
    format: formatJsonBodyEditorValue,
    onCommit: onChange,
    parse: parseJsonBodyEditorValue,
    value,
  });
  const draftDiagnosticId = useId();

  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600">
      {label}
      <textarea
        aria-describedby={
          draftState.error || draftState.conflict ? draftDiagnosticId : undefined
        }
        aria-invalid={Boolean(draftState.error) || undefined}
        className={`min-h-[8rem] rounded-lg border bg-white px-2 py-1.5 font-mono text-xs text-slate-900 outline-none transition focus:ring-2 focus:ring-brand/20 ${
          draftState.error
            ? "border-red-300 focus:border-red-400"
            : "border-line focus:border-brand"
        }`}
        rows={6}
        value={draftState.draft}
        onBlur={draftState.onBlur}
        onChange={(event) => draftState.onChange(event.target.value)}
        onFocus={draftState.onFocus}
        onKeyDown={draftState.onKeyDown}
      />
      <DraftFieldMessage id={draftDiagnosticId} state={draftState} />
    </label>
  );
}
