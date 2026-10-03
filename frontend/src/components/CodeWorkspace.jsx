import { EDITOR_COMMAND_IDS, editorCommandMetadata, pasteIntoEditor } from "../lib/editorCommands.js";
import { containsFileTransfer, droppedFileEntries } from "../lib/fileTransfers.js";
import { useReducedMotion } from "../lib/useReducedMotion.js";
import { languageForPath } from "../lib/editorLanguage.js";
export { languageForPath } from "../lib/editorLanguage.js";
import { pathWithin, pathMatchesChange } from "../lib/workspacePaths.js";
import { resolveMarkdownLinkPath } from "../lib/fileLinks.js";
export { resolveMarkdownLinkPath, markdownFileLinkTarget, filePathFromMarkdownUrl, resolveMarkdownFileLinkTarget, resolveMarkdownFileTarget } from "../lib/fileLinks.js";
import { reconcileEditorLifetimes, closeEditorLifetimes, acceptsEditorState, retainOpenEditorStates } from "../lib/editorStateLifetime.js";
import { startPolling, shareInFlight } from "../lib/refresh.js";
import { parseCommitDiffPath } from "../lib/commitDiff.js";
import CommitDiff from "./CommitDiff.jsx";
import { installRemActions } from "../lib/editorRem.js";
import { installConflictControls } from "../lib/mergeConflicts.js";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Play,
  Square,
  Workflow,
  Eye,
  FileCode2,
  FileJson2,
  FileText,
  FolderOpen,
  Globe,
  GitCompareArrows,
  Loader2,
  PencilLine,
  Save,
  X,
} from "lucide-react";
import { diagnosticsToMarkers, diagnosticToMarker } from "../lib/rattishRanges.js";
import {
  formatKeybinding, DEFAULT_APP_SETTINGS,
  KEYBINDING_COMMANDS,
  matchesCommand,
  settingBinding,
} from "../lib/settings.js";
import raticodeIcon from "../assets/roundel.png";
import { Dialog } from "./Dialog.jsx";
import MarkdownContent from "./MarkdownContent.jsx";
import IntegratedBrowser, { HtmlModeToggle } from "./IntegratedBrowser.jsx";

import { textEditorSessions, discardedSessionPaths, codeCloseProtection } from "../lib/codeEditorSessions.js";
export { FILE_AUTOSAVE_DELAY_MS, codeCloseProtection, hasUnsavedCodeChanges, applyCodeFilesystemChange } from "../lib/codeEditorSessions.js";

const CodeWorkspace = forwardRef(function CodeWorkspace({
  active,
  emptyContent,
  activePath,
  openPaths,
  navigationRequest,
  previewPath,
  recentPaths = [],
  rattishDocument,
  rattishDirty = false,
  settings = DEFAULT_APP_SETTINGS,
  theme,
  workflow,
  onActivePathChange,
  onClosePath,
  onClosePaths,
  onBrowserStateChange,
  onDocumentStateChange,
  onActiveDocumentStateChange,
  onNewFile,
  onOpenMarkdownPath,
  onOpenBrowser,
  onOpenFile,
  onOpenProject,
  onOpenPath,
  onDropPaths,
  onOpenPathsChange,
  onPinPath,
  onRattishContentChange,
  onRattishDiscard,
  onRattishSaved,
  onSettingChange,
  saveBeforeClosePaths = [],
  browserTabs = {},
  workflowTabs = {},
  renderWorkflowTab,
  onWorkflowTabAction,
  onDuplicateWorkflowTab,
  onOpenGraph,
  onBeforeCloseWorkflowTabs,
  rattishDocuments = {},
  onSaveRattishDocument,
}, ref) {
  const textEditorRefs = useRef(new Map());
  const workspaceRef = useRef(null);
  const tabMenuRef = useRef(null);
  const tabMenuFirstActionRef = useRef(null);
  const draggedPathRef = useRef("");
  const documentPathOrderRef = useRef(openPaths);
  const [fileStates, setFileStates] = useState({});
  const [diffOnOpenPaths, setDiffOnOpenPaths] = useState(() => new Set(navigationRequest?.diff ? [navigationRequest.path] : []));
  const [appliedNavigationRequest, setAppliedNavigationRequest] = useState(null);
  const pendingDiffPath = navigationRequest !== appliedNavigationRequest && navigationRequest?.diff ? navigationRequest.path : null;
  // Git comparisons still need to show the original version of a deleted file.
  // Exclude new comparisons before the navigation effect records their mode.
  const physicalPathsKey = JSON.stringify(openPaths.filter(path => !browserTabs[path] && !workflowTabs[path] && !parseCommitDiffPath(path) && !diffOnOpenPaths.has(path) && path !== pendingDiffPath));
  const editorLifetimesRef = useRef(new Map());
  reconcileEditorLifetimes(editorLifetimesRef.current, openPaths);
  useEffect(() => {
    setFileStates((current) => retainOpenEditorStates(current, openPaths));
  }, [openPaths]);
  useEffect(() => {
    const inspect = window.goferDesktop?.workspace?.getPathInfo;
    if (!active || !inspect) return undefined;
    let disposed = false;
    const paths = JSON.parse(physicalPathsKey);
    const lifetimes = new Map(paths.map(path => [path, editorLifetimesRef.current.get(path)]));
    async function checkFiles() {
      const results = await Promise.all(paths.map(async path => {
        try { return { path, info: await inspect(path) }; }
        catch { return { path, info: null }; }
      }));
      if (disposed) return;
      for (const { path, info } of results) {
        if (info?.exists !== false || !acceptsEditorState(editorLifetimesRef.current, path, lifetimes.get(path))) continue;
        textEditorRefs.current.get(path)?.discard?.();
        textEditorSessions.delete(path);
      }
      setFileStates(current => {
        let next = current;
        for (const { path, info } of results) {
          if (!info || !acceptsEditorState(editorLifetimesRef.current, path, lifetimes.get(path))) continue;
          const missing = info.exists === false;
          if (missing ? current[path]?.missing : !current[path]?.missing) continue;
          if (next === current) next = { ...current };
          next[path] = { content: null, dirty: false, error: "", loading: !missing, saving: false, missing };
        }
        return next;
      });
    }
    const stop = startPolling(checkFiles, { immediate: true });
    window.addEventListener("gofer:git-files-changed", checkFiles);
    window.addEventListener("gofer:code-files-changed", checkFiles);
    return () => {
      disposed = true;
      stop();
      window.removeEventListener("gofer:git-files-changed", checkFiles);
      window.removeEventListener("gofer:code-files-changed", checkFiles);
    };
  }, [active, physicalPathsKey]);

  const [browserViewStates, setBrowserViewStates] = useState({});
  const [documentModes, setDocumentModes] = useState({});
  const [gitGroups, setGitGroups] = useState({});
  const [tabMenu, setTabMenu] = useState(null);
  const [unsavedClosePrompt, setUnsavedClosePrompt] = useState(null);
  const [draggedPath, setDraggedPath] = useState("");
  const [fileDropActive, setFileDropActive] = useState(false);
  const [fileDropError, setFileDropError] = useState("");
  const [splitGroup, setSplitGroup] = useState(null);
  const [primaryActivePath, setPrimaryActivePath] = useState("");
  const [splitActivePath, setSplitActivePath] = useState("");
  const sourcePath = workflow?.sourcePath ?? "";
  const currentPath = activePath || openPaths[0] || "";
  const localOpenPaths = openPaths.filter((path) => !browserTabs[path] && !workflowTabs[path] && !parseCommitDiffPath(path));
  const splitPaths = useMemo(
    () => (splitGroup ? splitGroup.paths.filter((path) => openPaths.includes(path)) : []),
    [openPaths, splitGroup],
  );
  const primaryGroupPaths = useMemo(
    () => openPaths.filter((path) => !splitPaths.includes(path)),
    [openPaths, splitPaths],
  );
  // Electron tears down a webview guest when its mounted ancestor moves in the DOM.
  // Keep document order stable while the tab strips follow the user's chosen order.
  const documentPaths = stableCodeDocumentPaths(documentPathOrderRef.current, openPaths);
  const primaryPath = primaryGroupPaths.includes(currentPath)
    ? currentPath
    : (primaryGroupPaths.includes(primaryActivePath) ? primaryActivePath : primaryGroupPaths[0] ?? "");
  const splitPath = splitPaths.includes(currentPath)
    ? currentPath
    : (splitPaths.includes(splitActivePath) ? splitActivePath : splitPaths[0] ?? "");

  useLayoutEffect(() => {
    documentPathOrderRef.current = documentPaths;
  }, [documentPaths]);

  function beginTabDrag(event, path) {
    draggedPathRef.current = path;
    setDraggedPath(path);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/x-raticode-tab", path);
    event.dataTransfer.setData("text/plain", path);
  }

  function endTabDrag() {
    draggedPathRef.current = "";
    setDraggedPath("");
  }

  async function openDroppedFiles(event) {
    if (!containsFileTransfer(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setFileDropActive(false);
    setFileDropError("");
    try {
      const { entries } = await droppedFileEntries(event.dataTransfer);
      const paths = entries.filter(entry => !entry.isDirectory).map(entry => entry.path);
      if (!paths.length) throw new Error("Drop files here to open editor tabs. Drop folders in the file pane to import them.");
      if (onDropPaths) onDropPaths(paths);
      else for (const path of paths) await onOpenPath?.(path);
    } catch (cause) { setFileDropError(cause.message || "Unable to open dropped files."); }
  }

  function draggedTabFrom(event) {
    return event.dataTransfer.getData("text/x-raticode-tab")
      || event.dataTransfer.getData("text/plain")
      || draggedPathRef.current;
  }

  useEffect(() => {
    if (primaryGroupPaths.includes(currentPath)) setPrimaryActivePath(currentPath);
  }, [currentPath, primaryGroupPaths]);

  useEffect(() => {
    if (splitPaths.includes(currentPath)) setSplitActivePath(currentPath);
  }, [currentPath, splitPaths]);

  useEffect(() => {
    if (!splitGroup) return;
    const remaining = splitGroup.paths.filter((path) => openPaths.includes(path));
    const primaryRemaining = openPaths.filter((path) => !remaining.includes(path));
    if (!remaining.length || !primaryRemaining.length) {
      setSplitGroup(null);
      return;
    }
    if (remaining.length !== splitGroup.paths.length) {
      setSplitGroup((current) => (current ? { ...current, paths: remaining } : null));
    }
  }, [openPaths, splitGroup]);

  function moveTab(source, target, targetIsSplit) {
    if (!source) return;
    const sourceIsSplit = splitPaths.includes(source);
    if (source !== target) onOpenPathsChange?.(reorderCodeTabs(openPaths, source, target));
    if (sourceIsSplit === targetIsSplit) return;
    if (targetIsSplit) {
      setSplitGroup((current) => current
        ? { ...current, paths: current.paths.includes(source) ? current.paths : [...current.paths, source] }
        : { paths: [source], side: "right" });
    } else {
      setSplitGroup((current) => {
        if (!current) return current;
        const paths = current.paths.filter((path) => path !== source);
        return paths.length ? { ...current, paths } : null;
      });
    }
  }

  function addToSplitGroup(path, side) {
    if (!path) return;
    setSplitGroup((current) => {
      if (!current) return { paths: [path], side };
      const paths = current.paths.includes(path) ? current.paths : [...current.paths, path];
      return { paths, side };
    });
  }

  async function splitEditorTab(path, side) {
    const nextPath = workflowTabs[path] ? await onDuplicateWorkflowTab?.(path) : path;
    if (!nextPath) return;
    addToSplitGroup(nextPath, side);
    onActivePathChange?.(nextPath);
    setTabMenu(null);
  }

  const currentVirtualDocument = Boolean(browserTabs[currentPath] || workflowTabs[currentPath] || parseCommitDiffPath(currentPath));
  useEffect(() => {
    if (!currentPath || currentVirtualDocument) {
      onActiveDocumentStateChange?.(null);
      return;
    }
    onActiveDocumentStateChange?.({
      content: null,
      dirty: false,
      error: "",
      loading: true,
      saving: false,
      ...fileStates[currentPath],
      path: currentPath,
    });
  }, [currentVirtualDocument, currentPath, fileStates, onActiveDocumentStateChange]);

  const finishClosingWorkspacePaths = useCallback((targets, discardRattish = false) => {
    closeEditorLifetimes(editorLifetimesRef.current, targets);
    setFileStates((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !targets.includes(path)),
    ));
    for (const path of targets) {
      const retainedGraph = Object.entries(workflowTabs).some(([tabPath, tab]) => tab.sourcePath === path && !targets.includes(tabPath));
      if (!retainedGraph) textEditorRefs.current.get(path)?.discard?.();
    }
    setBrowserViewStates((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !targets.includes(path)),
    ));
    setDocumentModes((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !targets.includes(path)),
    ));
    setGitGroups((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !targets.includes(path)),
    ));
    setDiffOnOpenPaths((current) => new Set([...current].filter((path) => !targets.includes(path))));
    if (onClosePaths) onClosePaths(targets);
    else for (const path of targets) onClosePath?.(path);
    if (discardRattish) onRattishDiscard?.();
    setTabMenu(null);
  }, [onClosePath, onClosePaths, onRattishDiscard, workflowTabs]);

  const closeWorkspacePaths = useCallback(async (paths) => {
    const targets = [...new Set(paths)].filter((path) => openPaths.includes(path));
    if (!targets.length) return;
    if (onBeforeCloseWorkflowTabs && await onBeforeCloseWorkflowTabs(targets) === false) return;
    const dirtyPaths = targets.filter((path) => {
      if (workflowTabs[path] || (onBeforeCloseWorkflowTabs && rattishDocuments[path])) return false;
      if (openPaths.some(candidate => !targets.includes(candidate) && workflowTabs[candidate]?.sourcePath === path)) return false;
      return rattishDocuments[path]?.document?.dirty ?? (path === sourcePath ? rattishDirty : Boolean(fileStates[path]?.dirty));
    });
    const requiredSavePaths = dirtyPaths.filter((path) => saveBeforeClosePaths.includes(path));
    if (requiredSavePaths.length) {
      const results = await Promise.all(requiredSavePaths.map(
        (path) => textEditorRefs.current.get(path)?.save?.() ?? false,
      ));
      if (results.some((result) => !result)) return;
    }
    const remainingDirtyPaths = dirtyPaths.filter((path) => !requiredSavePaths.includes(path));
    const protection = codeCloseProtection(remainingDirtyPaths, settings.general.autosave);
    if (protection === "prompt-to-save") {
      setTabMenu(null);
      setUnsavedClosePrompt({ dirtyPaths: remainingDirtyPaths, error: "", saving: false, targets });
      return;
    }
    if (protection === "confirm-discard") {
      const message = targets.length === 1
        ? `Close ${fileName(targets[0])} without saving your changes?`
        : `Close ${targets.length} files? Unsaved changes in ${remainingDirtyPaths.length} file${remainingDirtyPaths.length === 1 ? "" : "s"} will be discarded.`;
      if (!window.confirm(message)) return;
    }
    finishClosingWorkspacePaths(targets, remainingDirtyPaths.includes(sourcePath));
  }, [fileStates, finishClosingWorkspacePaths, workflowTabs, openPaths, rattishDirty, rattishDocuments, onBeforeCloseWorkflowTabs, saveBeforeClosePaths, settings.general.autosave, sourcePath]);

  const saveAndClosePromptedPaths = useCallback(async () => {
    if (!unsavedClosePrompt || unsavedClosePrompt.saving) return;
    const { dirtyPaths, targets } = unsavedClosePrompt;
    setUnsavedClosePrompt((current) => current ? { ...current, error: "", saving: true } : null);
    const results = await Promise.all(dirtyPaths.map(
      (path) => textEditorRefs.current.get(path)?.save?.() ?? null,
    ));
    if (results.some((result) => !result)) {
      setUnsavedClosePrompt((current) => current ? {
        ...current,
        error: "Raticode couldn't save every file. Fix the error in the editor, then try again.",
        saving: false,
      } : null);
      return;
    }
    setUnsavedClosePrompt(null);
    finishClosingWorkspacePaths(targets);
  }, [finishClosingWorkspacePaths, unsavedClosePrompt]);

  const closeWorkspacePath = useCallback((path) => {
    void closeWorkspacePaths([path]);
  }, [closeWorkspacePaths]);

  useEffect(() => {
    if (!tabMenu) return undefined;
    tabMenuFirstActionRef.current?.focus();
    function dismissMenu(event) {
      if (tabMenuRef.current?.contains(event.target)) return;
      setTabMenu(null);
    }
    function dismissWithEscape(event) {
      if (event.key === "Escape") setTabMenu(null);
    }
    window.addEventListener("pointerdown", dismissMenu);
    window.addEventListener("keydown", dismissWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismissMenu);
      window.removeEventListener("keydown", dismissWithEscape);
    };
  }, [tabMenu]);

  useEffect(() => {
    setAppliedNavigationRequest(navigationRequest);
    if (!navigationRequest?.path) return;
    if (!navigationRequest.diff) setDiffOnOpenPaths((current) => withoutSetValue(current, navigationRequest.path));
    if (!navigationRequest.lineNumber && !navigationRequest.diff) return;
    if (navigationRequest.diff) {
      setFileStates(current => current[navigationRequest.path]?.missing
        ? { ...current, [navigationRequest.path]: { content: null, dirty: false, error: "", loading: true, saving: false } } : current);
      setDiffOnOpenPaths((current) => withSetValue(current, navigationRequest.path));
      setGitGroups((current) => ({ ...current, [navigationRequest.path]: navigationRequest.gitGroup || "" }));
    }
    setDocumentModes((current) => current[navigationRequest.path] === "edit"
      ? current
      : { ...current, [navigationRequest.path]: "edit" });
  }, [navigationRequest]);

  useImperativeHandle(
    ref,
    () => ({
      acceptDocument: (document, path = sourcePath) =>
        textEditorRefs.current.get(path)?.acceptContent?.(document?.source ?? ""),
      savePath: (path) => textEditorRefs.current.get(path)?.save?.(),
      acceptPathDocument: (path, document) => textEditorRefs.current.get(path)?.acceptContent?.(document?.source ?? ""),
      getPathState: (path) => fileStates[path],
      closeActive: () => {
        closeWorkspacePath(currentPath);
      },
      revealDiagnostic: (diagnostic) =>
        textEditorRefs.current.get(sourcePath)?.revealDiagnostic?.(diagnostic),
      runCommand: (command) => {
        if (command === "edit.find" && browserViewStates[currentPath]?.ready) {
          window.dispatchEvent(new CustomEvent("gofer:find-page", { detail: { clientId: browserTabs[currentPath] ? currentPath : `html:${currentPath}` } }));
          return true;
        }
        return textEditorRefs.current.get(currentPath)?.runCommand?.(command);
      },
      save: () => textEditorRefs.current.get(sourcePath)?.save?.(),
      saveActive: () => workflowTabs[currentPath]
        ? onWorkflowTabAction?.(workflowTabs[currentPath], "save")
        : textEditorRefs.current.get(currentPath)?.save?.(),
    }),
    [closeWorkspacePath, currentPath, sourcePath, workflowTabs, onWorkflowTabAction, fileStates, browserViewStates, browserTabs],
  );

  useEffect(() => {
    if (!active) return undefined;
    window.addEventListener("keydown", handleWorkspaceKeyDown, true);
    return () => window.removeEventListener("keydown", handleWorkspaceKeyDown, true);
  });

  function handleWorkspaceKeyDown(event) {
    if (event.defaultPrevented) return;
    const target = event.target;
    const targetIsDocument = !target
      || target === window
      || target === document
      || target === document.body
      || target === document.documentElement;
    if (!targetIsDocument && !workspaceRef.current?.contains(target)) return;
    if (target?.closest?.("[role='dialog'], [role='menu']")) return;
    const action = codeWorkspaceShortcutAction(event, {
      active,
      browserActive: Boolean(browserTabs[currentPath]),
      currentPath,
      settings,
    });
    if (!action || (["find", "suppress-find"].includes(action) && !textEditorRefs.current.get(currentPath))) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "new") {
      onNewFile?.();
      return;
    }
    if (action === "save") {
      if (workflowTabs[currentPath]) void onWorkflowTabAction?.(workflowTabs[currentPath], "save");
      else void textEditorRefs.current.get(currentPath)?.save?.();
      return;
    }
    if (action === "suppress-find") return;
    if (action === "find") {
      textEditorRefs.current.get(currentPath)?.runCommand?.("edit.find");
      return;
    }
    if (action === "toggle-word-wrap") {
      onSettingChange?.("editor.wordWrap", !settings.editor.wordWrap);
      return;
    }
    if (action === "next-tab" || action === "previous-tab") {
      const groupPaths = splitPaths.includes(currentPath) ? splitPaths : primaryGroupPaths;
      const nextPath = adjacentCodeTab(
        groupPaths,
        currentPath,
        action === "previous-tab" ? -1 : 1,
      );
      if (nextPath) onActivePathChange?.(nextPath);
      return;
    }
    closeWorkspacePath(currentPath);
  }

  function openTabMenu(event, path, groupPaths) {
    event.preventDefault();
    event.stopPropagation();
    const bounds = event.currentTarget.getBoundingClientRect();
    const requestedLeft = event.clientX || bounds.left + 12;
    const requestedTop = event.clientY || bounds.bottom - 2;
    onActivePathChange?.(path);
    setTabMenu({
      groupPaths,
      left: Math.max(8, Math.min(requestedLeft, window.innerWidth - 184)),
      path,
      top: Math.max(8, Math.min(requestedTop, window.innerHeight - 276)),
    });
  }

  function renderTabStrip(paths, activeForGroup, isSplit, column) {
    const projectRoots = [...new Set([workflow?.projectRoot, ...Object.values(workflowTabs).map(item => item.projectRoot)].filter(Boolean))].sort((left, right) => right.length - left.length);
    return (
      <div
        className={`relative flex min-w-0 border-b border-line bg-slate-50 ${column === 2 ? "border-l" : ""}`}
        style={{ gridColumn: column, gridRow: 1 }}
      >
      <div
        aria-label={isSplit ? "Split editor tabs" : "Editor tabs"}
        className="tab-strip-scrollbar flex h-9 min-w-0 flex-1 items-center overflow-x-auto overflow-y-hidden"
        role="tablist"
        onDragOver={(event) => {
          if (!draggedPathRef.current) return;
          event.preventDefault();
        }}
        onDrop={(event) => {
          if (!draggedPathRef.current) return;
          event.preventDefault();
          const source = draggedTabFrom(event);
          const target = paths[paths.length - 1] ?? source;
          if (source) moveTab(source, target, isSplit);
          endTabDrag();
        }}
      >
        {paths.map((path) => {
          const graphTab = workflowTabs[path];
          const commitDiff = parseCommitDiffPath(path);
          const browserTab = browserTabs[path];
          const workflowTab = workflowTabs[path];
          const browserViewState = browserViewStates[path];
          const tabMetadata = browserViewTabMetadata(browserTab, browserViewState);
          const selected = path === activeForGroup;
          const isRattish = !browserTab && !graphTab && path === sourcePath;
          const dirty = workflowTab ? workflowTab.dirty : rattishDocuments[path] ? (rattishDocuments[path].dirty ?? rattishDocuments[path].document?.dirty) : isRattish ? rattishDirty : fileStates[path]?.dirty;
          const preview = !browserTab && !workflowTab && path === previewPath;
          const folderLabel = commitDiff ? fileName(commitDiff.projectRoot) : workflowTab ? workflowTab.contextLabel || fileName(workflowTab.projectRoot || "") : browserTab ? "" : duplicateTabFolder(path, localOpenPaths);
          const label = workflowTab?.name || codeTabLabel(path, tabMetadata);
          const title = commitDiff ? `${label}\n${commitDiff.projectRoot}\n${commitDiff.hash}` : workflowTab ? [label, workflowTab.sourcePath, workflowTab.statusLabel].filter(Boolean).join("\n") : tabMetadata
            ? [label, tabMetadata.url].filter(Boolean).join("\n")
            : path;
          return (
            <div
              key={path}
              className={`group relative flex h-full w-48 shrink-0 items-center border-r border-line text-xs ${selected ? "bg-white font-semibold text-ink" : "text-muted hover:bg-white/70 hover:text-ink"}`}
              title={title}
              onContextMenu={(event) => openTabMenu(event, path, paths)}
              onDragOver={(event) => {
                if (!draggedPathRef.current || draggedPathRef.current === path) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
              }}
              onDrop={(event) => {
                event.preventDefault();
                event.stopPropagation();
                const source = draggedTabFrom(event);
                if (source) moveTab(source, path, isSplit);
                endTabDrag();
              }}
            >
              <button
                aria-selected={selected}
                ref={(element) => { if (selected) element?.scrollIntoView?.({ block: "nearest", inline: "nearest" }); }}
                tabIndex={selected ? 0 : -1}
                onKeyDown={(event) => {
                  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const next = event.key === "Home" ? paths[0] : event.key === "End" ? paths.at(-1) : adjacentCodeTab(paths, path, event.key === "ArrowLeft" ? -1 : 1);
                  onActivePathChange?.(next);
                  const buttons = event.currentTarget.closest("[role=tablist]")?.querySelectorAll("[role=tab]");
                  buttons?.[paths.indexOf(next)]?.focus();
                }}
                className="flex h-full min-w-0 flex-1 items-center gap-2 py-0 pl-3 pr-2 text-left"
                draggable
                role="tab"
                type="button"
                onClick={() => onActivePathChange?.(path)}
                onDoubleClick={() => { if (!graphTab) onPinPath?.(path); }}
                onDragEnd={endTabDrag}
                onDragStart={(event) => beginTabDrag(event, path)}
              >
                {workflowTab ? <Workflow aria-hidden="true" className="shrink-0 text-brand" size={14} /> : <FileTypeIcon browserTab={tabMetadata} path={path} />}
                <span className={`flex min-w-0 flex-col justify-center ${preview ? "italic" : ""}`}>
                  {folderLabel ? (
                    <span className="max-w-full truncate text-[9px] font-normal leading-[11px] text-muted">
                      {folderLabel}
                    </span>
                  ) : null}
                  <span className="max-w-full truncate leading-[14px]">{label}</span>
                </span>
                {dirty ? <span aria-label="Unsaved changes" className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" /> : null}
              </button>
              {workflowTab ? <WorkflowTabControls tab={workflowTab} onAction={onWorkflowTabAction} /> : null}
              <button
                aria-label={`Close ${label}`}
                className="mr-1 grid h-5 w-5 shrink-0 place-items-center rounded text-muted opacity-0 hover:bg-slate-100 hover:text-ink focus:opacity-100 group-focus-within:opacity-100 group-hover:opacity-100"
                draggable={false}
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  closeWorkspacePath(path);
                }}
              ><X size={12} /></button>
              {selected ? <span className="pointer-events-none absolute inset-x-0 top-0 h-0.5 bg-brand" /> : null}
            </div>
          );
        })}
      </div>
      <EditorTabsMenu
        activePath={activeForGroup}
        label={isSplit ? "All split editor tabs" : "All editor tabs"}
        entries={paths.map((path) => {
          const tab = workflowTabs[path];
          const browser = browserTabs[path];
          const projectRoot = parseCommitDiffPath(path)?.projectRoot || tab?.projectRoot || rattishDocuments[path]?.document?.projectRoot || (!browser && projectRoots.find(root => pathWithin(path, root))) || "";
          return { path, tab, browser, projectRoot, label: tab?.name || codeTabLabel(path, browserViewTabMetadata(browser, browserViewStates[path])) };
        })}
        onActivate={onActivePathChange}
      />
      </div>
    );
  }

  function renderDocuments(paths) {
    return paths.map((path) => {
      const commitDiff = parseCommitDiffPath(path);
      const workflowTab = workflowTabs[path];
      const pathDocument = rattishDocuments[path]?.document ?? (path === sourcePath ? rattishDocument : null);
      const isRattishDocument = Boolean(rattishDocuments[path]) || path === sourcePath;
      const editorLifetime = editorLifetimesRef.current.get(path);
      const inSplitGroup = splitPaths.includes(path);
      const groupPaths = inSplitGroup ? splitPaths : primaryGroupPaths;
      const activeForGroup = inSplitGroup ? splitPath : primaryPath;
      const column = splitGroup?.side === "left"
        ? (inSplitGroup ? 1 : 2)
        : (inSplitGroup ? 2 : 1);
      const browserTab = browserTabs[path];
      const html = !browserTab && isHtmlPath(path);
      const image = !browserTab && isImagePath(path);
      const media = !browserTab && mediaKind(path);
      const pdf = !browserTab && isPdfPath(path);
      const svg = !browserTab && isSvgPath(path);
      const mode = codeDocumentMode(path, documentModes, settings.editor);
      const selected = activeForGroup === path;
      return (
        <div
          key={path}
          aria-hidden={!selected}
          inert={!selected ? "" : undefined}
          className={`flex min-h-0 min-w-0 overflow-hidden flex-col ${selected ? "visible z-10" : "invisible z-0 pointer-events-none"} ${splitGroup && column === 2 ? "border-l border-line" : ""}`}
          style={{ gridColumn: column, gridRow: 2, contentVisibility: selected ? "visible" : "hidden" }}
        >
          {fileStates[path]?.missing ? (
            <section aria-label={`${fileName(path)} unavailable`} className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 bg-white px-6 text-center dark:bg-[#19191b]">
              <p role="status" className="text-sm text-ink">This file doesn&apos;t exist anymore</p>
              <button type="button" className="rounded border border-line px-4 py-2 text-xs text-ink hover:bg-slate-100 focus-visible:outline-brand" onClick={() => finishClosingWorkspacePaths([path])}>Close Tab</button>
            </section>
          ) : commitDiff ? <CommitDiff {...commitDiff} theme={theme} editorSettings={settings.editor} /> : workflowTab ? renderWorkflowTab?.(workflowTab, { path, visible: active && selected, active: active && currentPath === path, pane: inSplitGroup ? "split" : "primary" }) : diffOnOpenPaths.has(path) && (image || pdf) ? <BinaryGitComparison path={path} group={gitGroups[path]} onClose={() => setDiffOnOpenPaths((current) => withoutSetValue(current, path))} /> : browserTab || pdf || (html && mode === "preview") ? (
            <PreviewBrowser
              active={active && currentPath === path}
              applicationKeybindings={settings.keybindings}
              clientId={browserTab ? path : `html:${path}`}
              dragActive={Boolean(draggedPath)}
              focusLocationOnCreate={browserTab?.focusLocation === true}
              initialUrl={browserTab?.url || "about:blank"}
              localPath={browserTab ? "" : path}
              openBrowserBinding={settingBinding(settings, "browser.open")}
              searchUrl={settings.browser.searchUrl}
              diffPath={html ? path : ""}
              showModeToggle={html}
              onClose={() => closeWorkspacePath(path)}
              onCycleTab={(direction) => {
                const nextPath = adjacentCodeTab(groupPaths, path, direction);
                if (nextPath) onActivePathChange?.(nextPath);
              }}
              onNewTab={() => onOpenBrowser?.({ newTab: true })}
              onModeChange={(nextMode) => {
                setDocumentModes((current) => ({ ...current, [path]: nextMode }));
                if (nextMode === "preview") {
                  setDiffOnOpenPaths((current) => withoutSetValue(current, path));
                }
                if (nextMode === "edit") {
                  setBrowserViewStates((current) => {
                    const next = { ...current };
                    delete next[path];
                    return next;
                  });
                  onPinPath?.(path);
                }
              }}
              onShowDiff={() => {
                setDiffOnOpenPaths((current) => withSetValue(current, path));
                setDocumentModes((current) => ({ ...current, [path]: "edit" }));
                onPinPath?.(path);
              }}
              onStateChange={(nextState) => {
                setBrowserViewStates((current) => ({
                  ...current,
                  [path]: { ...current[path], ...nextState },
                }));
                if (browserTab) onBrowserStateChange?.(path, nextState);
              }}
            />
          ) : media ? (
            <MediaPreview path={path} active={active && selected} />
          ) : (
          <TextCodeEditor
            active={active && currentPath === path}
            visible={active && selected}
            diagnostics={isRattishDocument
              ? [
                  ...(pathDocument?.diagnostics ?? []),
                  ...(pathDocument?.preflight?.diagnostics ?? []),
                ]
              : []}
            managedDocument={rattishDocuments[path]?.document}
            onSaveDocument={rattishDocuments[path] && onSaveRattishDocument ? (content) => onSaveRattishDocument(path, content) : undefined}
            editing={mode === "edit"}
            html={html}
            initialDiffMode={diffOnOpenPaths.has(path)}
            markdown={isMarkdownPath(path)}
            svg={svg}
            navigationRequest={navigationRequest?.path === path ? navigationRequest : null}
            path={path}
            autosaveEnabled={settings.general.autosave}
            editorSettings={settings.editor}
            ref={(editor) => {
              if (editor) textEditorRefs.current.set(path, editor);
              else textEditorRefs.current.delete(path);
            }}
            theme={theme}
            onModeChange={(mode) => {
              if (!acceptsEditorState(editorLifetimesRef.current, path, editorLifetime)) return;
              setDocumentModes((current) => ({ ...current, [path]: mode }));
              if (mode === "preview") {
                setDiffOnOpenPaths((current) => withoutSetValue(current, path));
              }
              if (mode === "edit") onPinPath?.(path);
            }}
            onOpenRelativeLink={(href) => {
              if (onOpenMarkdownPath) {
                onOpenMarkdownPath(href, path);
                return;
              }
              const targetPath = resolveMarkdownLinkPath(path, href);
              if (targetPath) onOpenPath?.(targetPath, { preview: true });
            }}
            onStateChange={(nextState) => {
              if (!acceptsEditorState(editorLifetimesRef.current, path, editorLifetime)) return;
              if (nextState.missing) textEditorRefs.current.get(path)?.discard?.();
              setFileStates((current) => acceptsEditorState(editorLifetimesRef.current, path, editorLifetime)
                ? { ...current, [path]: nextState } : current);
              if (nextState.dirty) onPinPath?.(path);
              if (isRattishDocument && !nextState.loading && nextState.content != null) {
                const currentDocument = pathDocument;
                const documentState = {
                  document: nextState.content == null
                    ? currentDocument
                    : {
                        ...(currentDocument ?? {}),
                        diagnostics: currentDocument?.diagnostics ?? [],
                        dirty: nextState.dirty,
                        preflight: currentDocument?.preflight ?? { diagnostics: [] },
                        source: nextState.content,
                      },
                  error: nextState.error,
                  loading: nextState.loading,
                  saving: nextState.saving,
                };
                onDocumentStateChange?.(documentState, path);
              }
            }}
            onSaved={isRattishDocument ? (...args) => onRattishSaved?.(...args, path) : undefined}
            onContentChange={isRattishDocument ? (...args) => onRattishContentChange?.(...args, path) : undefined}
          />
          )}
        </div>
      );
    });
  }

  return (
    <section
      ref={workspaceRef}
      className="code-workspace relative flex min-h-0 flex-1 flex-col bg-white"
      aria-label="Code workspace"
      onDragEnterCapture={event => { if (containsFileTransfer(event.dataTransfer)) { event.preventDefault(); setFileDropActive(true); } }}
      onDragOverCapture={event => { if (containsFileTransfer(event.dataTransfer)) { event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "copy"; } }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFileDropActive(false); }}
      onDropCapture={openDroppedFiles}
    >
      {fileDropActive ? <div aria-label="Open dropped files" className="absolute inset-0 z-50 grid place-items-center border-2 border-brand bg-canvas/90 text-sm text-ink">Drop files to open new tabs</div> : null}
      {fileDropError ? <div role="alert" className="flex items-center gap-2 border-b border-line px-3 py-2 text-xs text-red-700"><span className="flex-1">{fileDropError}</span><button type="button" aria-label="Dismiss file drop error" onClick={() => setFileDropError("")}><X size={13} /></button></div> : null}
      <div
        className="relative grid min-h-0 flex-1"
        style={{
          gridTemplateColumns: splitGroup && splitPaths.length
            ? "minmax(0, 1fr) minmax(0, 1fr)"
            : "minmax(0, 1fr)",
          gridTemplateRows: currentPath ? "2.25rem minmax(0, 1fr)" : "minmax(0, 1fr)",
        }}
      >
        {draggedPath && openPaths.length > 1 ? (
          <>
            <SplitDropZone side="left" onDrop={() => { addToSplitGroup(draggedPathRef.current, "left"); endTabDrag(); }} />
            <SplitDropZone side="right" onDrop={() => { addToSplitGroup(draggedPathRef.current, "right"); endTabDrag(); }} />
          </>
        ) : null}
        {primaryGroupPaths.length ? renderTabStrip(
          primaryGroupPaths,
          primaryPath,
          false,
          splitGroup?.side === "left" ? 2 : 1,
        ) : null}
        {splitGroup && splitPaths.length ? renderTabStrip(
          splitPaths,
          splitPath,
          true,
          splitGroup.side === "left" ? 1 : 2,
        ) : null}
        {renderDocuments(documentPaths)}
        {!currentPath ? emptyContent || (
          <div className="min-h-0 overflow-y-auto px-8 pb-10 pt-12" style={{ gridColumn: 1, gridRow: 1 }}>
            <div className="mx-auto w-full max-w-2xl">
              <div className="mx-auto max-w-sm">
                <p className="text-center text-sm font-semibold text-ink">Getting Started</p>
                <p className="mt-1 text-center text-xs leading-5 text-muted">
                  Open a project, a single file, or a browser tab.
                </p>
                <div className="mt-4 space-y-2">
                  <EmptyWorkspaceAction icon={FolderOpen} label="Open Project" shortcut={formatKeybinding(settingBinding(settings, "project.open"))} onClick={onOpenProject} />
                  <EmptyWorkspaceAction icon={FileText} label="Open File" shortcut={formatKeybinding(settingBinding(settings, "file.open"))} onClick={onOpenFile} />
                  <EmptyWorkspaceAction icon={Globe} label="Open Browser" shortcut={formatKeybinding(settingBinding(settings, "browser.open"))} onClick={() => onOpenBrowser?.()} />
                </div>
              </div>
              {recentPaths.length ? (
                <section aria-labelledby="recent-files-heading" className="mt-10">
                  <h2 id="recent-files-heading" className="mb-3 text-xs font-semibold text-ink">
                    Recent files
                  </h2>
                  <div className="grid grid-cols-2 gap-3">
                    {recentPaths.map((path) => (
                      <RecentFileCard key={path} path={path} onOpen={() => onOpenPath?.(path)} />
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
      {tabMenu ? (
        <div
          ref={tabMenuRef}
          aria-label={`${fileName(tabMenu.path)} tab actions`}
          className="fixed z-[100] w-44 rounded-md border border-line bg-white p-1 text-[11px] text-ink shadow-lg"
          role="menu"
          style={{ left: tabMenu.left, top: tabMenu.top }}
        >
          <button
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100 disabled:cursor-default disabled:text-muted disabled:hover:bg-transparent"
            disabled={Boolean(browserTabs[tabMenu.path] || workflowTabs[tabMenu.path]) || tabMenu.path !== previewPath}
            role="menuitem"
            type="button"
            onClick={() => {
              onPinPath?.(tabMenu.path);
              setTabMenu(null);
            }}
          >
            Pin file
          </button>
          {onOpenGraph && !workflowTabs[tabMenu.path] && fileName(tabMenu.path) === "workflow.rattish" ? <button type="button" role="menuitem" className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100" onClick={() => { onOpenGraph(tabMenu.path); setTabMenu(null); }}>Open graph</button> : null}
          {["left", "right"].map(side => <button
            key={side}
            type="button"
            role="menuitem"
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100 disabled:text-muted"
            disabled={workflowTabs[tabMenu.path] ? !onDuplicateWorkflowTab : openPaths.length < 2}
            onClick={() => void splitEditorTab(tabMenu.path, side)}
          >Split {side}</button>)}
          <div className="my-1 border-t border-line" role="separator" />
          <button
            ref={tabMenuFirstActionRef}
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100"
            role="menuitem"
            type="button"
            onClick={() => closeWorkspacePaths(fileTabCloseTargets(tabMenu.groupPaths, tabMenu.path, "close"))}
          >
            Close
          </button>
          <button
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100 disabled:cursor-default disabled:text-muted disabled:hover:bg-transparent"
            disabled={tabMenu.groupPaths.length < 2}
            role="menuitem"
            type="button"
            onClick={() => closeWorkspacePaths(fileTabCloseTargets(tabMenu.groupPaths, tabMenu.path, "others"))}
          >
            Close others
          </button>
          <button
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100 disabled:cursor-default disabled:text-muted disabled:hover:bg-transparent"
            disabled={tabMenu.groupPaths.indexOf(tabMenu.path) >= tabMenu.groupPaths.length - 1}
            role="menuitem"
            type="button"
            onClick={() => closeWorkspacePaths(fileTabCloseTargets(tabMenu.groupPaths, tabMenu.path, "right"))}
          >
            Close to the right
          </button>
          <button
            className="flex h-7 w-full items-center rounded px-2 text-left hover:bg-slate-100"
            role="menuitem"
            type="button"
            onClick={() => closeWorkspacePaths(fileTabCloseTargets(tabMenu.groupPaths, tabMenu.path, "all"))}
          >
            Close all
          </button>
        </div>
      ) : null}
      {unsavedClosePrompt ? (
        <UnsavedChangesDialog
          dirtyPaths={unsavedClosePrompt.dirtyPaths}
          error={unsavedClosePrompt.error}
          saving={unsavedClosePrompt.saving}
          onCancel={() => {
            if (!unsavedClosePrompt.saving) setUnsavedClosePrompt(null);
          }}
          onDiscard={() => {
            if (unsavedClosePrompt.saving) return;
            const { dirtyPaths, targets } = unsavedClosePrompt;
            setUnsavedClosePrompt(null);
            finishClosingWorkspacePaths(targets, dirtyPaths.includes(sourcePath));
          }}
          onSave={() => void saveAndClosePromptedPaths()}
        />
      ) : null}
    </section>
  );
});

export function UnsavedChangesDialog({
  dirtyPaths,
  error = "",
  onCancel,
  onDiscard,
  onSave,
  saving = false,
}) {
  const fileCount = dirtyPaths.length;
  const title = fileCount === 1
    ? `Save changes to ${fileName(dirtyPaths[0])}?`
    : `Save changes to ${fileCount} files?`;
  const description = fileCount === 1
    ? "Your changes will be lost if you don't save them."
    : `Changes in ${fileCount} files will be lost if you don't save them.`;
  return (
    <Dialog
      description={description}
      onClose={onCancel}
      panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink shadow-xl"
      title={title}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
          <AlertTriangle aria-hidden="true" size={17} />
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold leading-5">{title}</h2>
          <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
        </div>
      </div>
      {error ? (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs leading-5 text-red-700 dark:bg-red-950/40 dark:text-red-300" role="alert">
          {error}
        </p>
      ) : null}
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button
          className="h-8 rounded-md px-3 text-xs font-medium text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-50"
          disabled={saving}
          type="button"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          className="h-8 rounded-md px-3 text-xs font-medium text-red-700 hover:bg-red-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-600 disabled:opacity-50 dark:text-red-300 dark:hover:bg-red-950/40"
          disabled={saving}
          type="button"
          onClick={onDiscard}
        >
          Discard changes
        </button>
        <button
          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-brand px-3 text-xs font-semibold text-white hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:opacity-60"
          data-dialog-initial-focus
          disabled={saving}
          type="button"
          onClick={onSave}
        >
          {saving ? <Loader2 aria-hidden="true" className="animate-spin" size={13} /> : <Save aria-hidden="true" size={13} />}
          {saving ? "Saving" : fileCount === 1 ? "Save" : "Save all"}
        </button>
      </div>
    </Dialog>
  );
}

function BinaryGitComparison({ path, group, onClose }) {
  const [baseline, setBaseline] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setBaseline(null); setError("");
    window.goferDesktop?.workspace?.gitFileBaseline?.(path, group).then((value) => { if (!disposed) setBaseline(value); }).catch((cause) => { if (!disposed) setError(cause.message); });
    return () => { disposed = true; };
  }, [path, group]);
  const extension = path.split(".").at(-1)?.toLowerCase();
  const mime = { avif: "image/avif", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", ico: "image/x-icon", bmp: "image/bmp" }[extension];
  return <section className="flex min-h-0 flex-1 flex-col" aria-label="Binary file comparison">
    <div className="flex h-9 items-center justify-between border-b border-line px-3 text-xs"><span>{group === "staged" ? "HEAD → Staged" : "Staged → Working file"}</span><button type="button" className="rounded px-2 py-1 hover:bg-slate-100" onClick={onClose}>Close diff</button></div>
    {error ? <p role="alert" className="p-3 text-xs text-red-700">{error}</p> : null}
    {!baseline ? <p className="p-3 text-xs text-muted">Loading comparison…</p> : <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-line overflow-auto">{[["Original", "original"], ["Changed", "modified"]].map(([label, key]) => <div key={key} className="min-w-0 p-3"><p className="mb-3 text-xs text-muted">{label} · {baseline[`${key}Bytes`] || 0} bytes</p>{mime && baseline[`${key}Data`] ? <img alt={`${label} version`} className="max-w-full object-contain" src={`data:${mime};base64,${baseline[`${key}Data`]}`} /> : <p className="text-xs text-muted">{baseline[`${key}Bytes`] ? "Binary content. Text diff is unavailable." : "File does not exist in this version."}</p>}</div>)}</div>}
  </section>;
}

function PreviewBrowser({ diffPath = "", ...props }) {
  const [diffAvailable, setDiffAvailable] = useState(false);
  useEffect(() => {
    if (!diffPath) {
      setDiffAvailable(false);
      return undefined;
    }
    let disposed = false;
    const readBaseline = window.goferDesktop?.workspace?.gitFileBaseline;
    if (!readBaseline) return undefined;
    readBaseline(diffPath).then((baseline) => {
      if (!disposed) setDiffAvailable(Boolean(baseline?.tracked && baseline?.changed));
    }).catch(() => {
      if (!disposed) setDiffAvailable(false);
    });
    return () => {
      disposed = true;
    };
  }, [diffPath]);
  return <IntegratedBrowser {...props} showDiffButton={diffAvailable} />;
}

function EmptyWorkspaceAction({ icon: Icon, label, onClick, shortcut }) {
  return (
    <button
      className="flex h-10 w-full items-center gap-2 rounded-md border border-line bg-white px-3 text-left text-xs font-medium text-ink transition hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
      type="button"
      onClick={onClick}
    >
      <Icon aria-hidden="true" className="text-muted" size={15} />
      <span className="flex-1">{label}</span>
      <span className="text-[10px] font-normal text-muted">{shortcut}</span>
    </button>
  );
}

function RecentFileCard({ onOpen, path }) {
  const parentPath = String(path ?? "").replace(/[\\/][^\\/]+$/, "");
  return (
    <button
      className="group flex min-w-0 items-center gap-3 rounded-lg border border-line bg-white px-3 py-3 text-left transition hover:border-slate-300 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand dark:hover:bg-[#252526]"
      title={path}
      type="button"
      onClick={onOpen}
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-slate-100 text-muted transition group-hover:text-ink dark:bg-[#252526]">
        <FileTypeIcon path={path} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-xs font-semibold text-ink">{fileName(path)}</span>
        <span className="mt-0.5 block truncate text-[10px] leading-4 text-muted">{parentPath}</span>
      </span>
    </button>
  );
}

function SplitDropZone({ onDrop, side }) {
  return (
    <div
      aria-label={`Split editor ${side}`}
      className={`z-40 m-2 flex w-[22%] items-center justify-center rounded-md border border-indigo-400 bg-indigo-100/80 text-xs font-semibold text-indigo-700 ${side === "left" ? "justify-self-start" : "justify-self-end"}`}
      style={{ gridColumn: "1 / -1", gridRow: 2 }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onDrop();
      }}
    >
      Split {side}
    </div>
  );
}


const TextCodeEditor = forwardRef(function TextCodeEditor({
  active,
  visible = active,
  autosaveEnabled = true,
  diagnostics = [],
  editing = true,
  editorSettings = DEFAULT_APP_SETTINGS.editor,
  html = false,
  initialDiffMode = false,
  markdown = false,
  svg = false,
  navigationRequest = null,
  path,
  theme,
  onModeChange,
  onOpenRelativeLink,
  onSaved,
  onContentChange,
  onStateChange,
  managedDocument,
  onSaveDocument,
}, ref) {
  const containerRef = useRef(null);
  const editorRef = useRef(null);
  const diffEditorRef = useRef(null);
  const decorationIdsRef = useRef([]);
  const monacoRef = useRef(null);
  const modelRef = useRef(null);
  const originalModelRef = useRef(null);
  const savedContentRef = useRef("");
  const savedVersionRef = useRef(null);
  const savingRef = useRef(false);
  const recoveryConflictRef = useRef(false);
  const autosaveTimerRef = useRef(null);
  const scheduleAutosaveRef = useRef(() => {});
  const editableRef = useRef(false);
  const [commands, setCommands] = useState({});
  const diagnosticsRef = useRef(diagnostics);
  const reduceMotion = useReducedMotion();
  const reduceMotionRef = useRef(reduceMotion);
  reduceMotionRef.current = reduceMotion;
  const editorSettingsRef = useRef(editorSettings);
  const gitBaselineRef = useRef(null);
  const onContentChangeRef = useRef(onContentChange);
  const onSavedRef = useRef(onSaved);
  const managedDocumentRef = useRef(managedDocument);
  const onSaveDocumentRef = useRef(onSaveDocument);
  managedDocumentRef.current = managedDocument;
  onSaveDocumentRef.current = onSaveDocument;
  const onStateChangeRef = useRef(onStateChange);
  const navigationRequestRef = useRef(navigationRequest);
  const [diffMode, setDiffMode] = useState(initialDiffMode || navigationRequest?.diff === true);
  const [gitBaseline, setGitBaseline] = useState(null);
  const [gitGroup, setGitGroup] = useState(navigationRequest?.gitGroup || "");
  const [diskRevision, setDiskRevision] = useState(0);
  const gitOperationRef = useRef(false);
  useEffect(() => {
    if (navigationRequest) { setGitGroup(navigationRequest.diff ? navigationRequest.gitGroup || "" : ""); setDiffMode(navigationRequest.diff === true); }
  }, [navigationRequest]);
  const [state, setState] = useState({
    content: null,
    dirty: false,
    error: "",
    loading: true,
    saving: false,
  });

  useEffect(() => {
    function gitChanged(event) {
      if (!pathMatchesChange(path, event.detail?.rootPath || event.detail?.path, true)) return;
      const model = modelRef.current;
      if (model && editableRef.current && model.getValue() !== savedContentRef.current) {
        setState((current) => ({ ...current, error: "This file changed on disk. Your unsaved edits have been kept." }));
        return;
      }
      discardedSessionPaths.add(path);
      setDiskRevision((value) => value + 1);
    }
    function gitBusy(event) {
      if (!pathMatchesChange(path, event.detail?.rootPath, true)) return;
      gitOperationRef.current = event.detail.busy;
      editorRef.current?.updateOptions({ readOnly: event.detail.busy || !editableRef.current });
    }
    window.addEventListener("gofer:git-files-changed", gitChanged);
    window.addEventListener("gofer:code-files-changed", gitChanged);
    window.addEventListener("gofer:git-working-tree-busy", gitBusy);
    return () => {
      window.removeEventListener("gofer:git-files-changed", gitChanged);
      window.removeEventListener("gofer:code-files-changed", gitChanged);
      window.removeEventListener("gofer:git-working-tree-busy", gitBusy);
    };
  }, [path]);

  const refreshGitBaseline = useCallback(async () => {
    const readBaseline = window.goferDesktop?.workspace?.gitFileBaseline;
    if (!readBaseline) {
      setGitBaseline(null);
      return null;
    }
    try {
      const baseline = await shareInFlight(`git-baseline:${path}:${gitGroup}`, () => readBaseline(path, gitGroup));
      setGitBaseline(baseline?.tracked ? baseline : null);
      if (!baseline?.changed) setDiffMode(false);
      return baseline;
    } catch {
      setGitBaseline(null);
      setDiffMode(false);
      return null;
    }
  }, [path, gitGroup]);

  useEffect(() => {
    if (!visible) return undefined;
    return startPolling(refreshGitBaseline, { immediate: true });
  }, [visible, refreshGitBaseline]);

  useEffect(() => {
    gitBaselineRef.current = gitBaseline;
  }, [gitBaseline]);

  useEffect(() => {
    if (!editing && modelRef.current) {
      const content = modelRef.current.getValue();
      setState((current) => current.content === content ? current : { ...current, content });
    }
  }, [editing]);

  const save = useCallback(async () => {
    const model = modelRef.current;
    if (!model || !editableRef.current || savingRef.current) return null;
    const content = model.getValue();
    const savingVersion = model.getAlternativeVersionId();
    if (content === savedContentRef.current) return true;
    if (recoveryConflictRef.current && !window.confirm("The file changed on disk while Raticode was closed. Replace it with your recovered draft?")) return null;
    let fileWritten = false;
    window.clearTimeout(autosaveTimerRef.current);
    autosaveTimerRef.current = null;
    savingRef.current = true;
    setState((current) => ({ ...current, error: "", saving: true }));
    try {
      if (onSaveDocumentRef.current) {
        const result = await onSaveDocumentRef.current(content);
        if (!result) throw new Error("Unable to save workflow. Review its diagnostics and try again.");
      } else {
        const writeTextFile = window.goferDesktop?.textFiles?.write;
        if (!writeTextFile) throw new Error("The desktop file editor is unavailable.");
        await writeTextFile({ targetPath: path, content });
      }
      fileWritten = true;
      recoveryConflictRef.current = false;
      savedContentRef.current = content;
      savedVersionRef.current = savingVersion;
      const currentContent = model.getValue();
      textEditorSessions.set(path, {
        content: currentContent,
        savedContent: content,
        viewState: editorRef.current?.saveViewState() ?? null,
      });
      const savedResult = onSaveDocumentRef.current ? true : await onSavedRef.current?.(content);
      void refreshGitBaseline();
      setState({
        content: currentContent,
        dirty: currentContent !== content,
        error: "",
        loading: false,
        saving: false,
      });
      return savedResult ?? true;
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to save file",
        saving: false,
      }));
      return null;
    } finally {
      savingRef.current = false;
      if (fileWritten && model.getValue() !== savedContentRef.current) {
        scheduleAutosaveRef.current();
      }
    }
  }, [path, refreshGitBaseline]);

  useEffect(() => {
    scheduleAutosaveRef.current = () => {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
      if (!autosaveEnabled || recoveryConflictRef.current) return;
      autosaveTimerRef.current = window.setTimeout(() => {
        autosaveTimerRef.current = null;
        if (savingRef.current) {
          scheduleAutosaveRef.current();
          return;
        }
        void save();
      }, editorSettings.autosaveDelay);
    };
    if (!autosaveEnabled) {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
  }, [autosaveEnabled, editorSettings.autosaveDelay, save]);

  useEffect(() => {
    const model = modelRef.current;
    if (!model || !managedDocument || managedDocument.source == null || !editableRef.current) return;
    if (!managedDocument.dirty) savedContentRef.current = managedDocument.source;
    else if (managedDocument.savedSource != null) savedContentRef.current = managedDocument.savedSource;
    if (model.getValue() !== managedDocument.source) {
      editableRef.current = false;
      replaceEditorModelContent(model, managedDocument.source);
      editableRef.current = true;
    }
    const content = model.getValue();
    textEditorSessions.set(path, { content, savedContent: savedContentRef.current, viewState: editorRef.current?.saveViewState() ?? null });
    setState((current) => current.content === content && current.dirty === Boolean(managedDocument.dirty) ? current : { ...current, content, dirty: Boolean(managedDocument.dirty) });
  }, [managedDocument, path]);

  useEffect(() => {
    onSavedRef.current = onSaved;
  }, [onSaved]);

  useEffect(() => {
    onContentChangeRef.current = onContentChange;
  }, [onContentChange]);

  useEffect(() => {
    diagnosticsRef.current = diagnostics;
  }, [diagnostics]);

  useEffect(() => {
    onStateChangeRef.current = onStateChange;
  }, [onStateChange]);

  useEffect(() => {
    navigationRequestRef.current = navigationRequest;
    revealEditorLocation(editorRef.current, navigationRequest);
  }, [navigationRequest]);

  useEffect(() => {
    onStateChangeRef.current?.({ ...state, commands });
  }, [state, commands]);

  useImperativeHandle(ref, () => ({
    acceptContent: (content) => {
      const model = modelRef.current;
      if (!model) return;
      savedContentRef.current = content;
      const wasEditable = editableRef.current;
      editableRef.current = false;
      replaceEditorModelContent(model, content);
      savedVersionRef.current = model.getAlternativeVersionId();
      editableRef.current = wasEditable;
      textEditorSessions.set(path, {
        content,
        savedContent: content,
        viewState: editorRef.current?.saveViewState() ?? null,
      });
      setState({ content, dirty: false, error: "", loading: false, saving: false });
    },
    discard: () => {
      discardedSessionPaths.add(path);
      textEditorSessions.delete(path);
    },
    revealDiagnostic: (diagnostic) => {
      const monaco = monacoRef.current;
      const model = modelRef.current;
      const editor = editorRef.current;
      if (!monaco || !model || !editor) return;
      const marker = diagnosticToMarker(monaco, model, model.getValue(), diagnostic);
      editor.revealRangeInCenter(marker);
      editor.setPosition({ lineNumber: marker.startLineNumber, column: marker.startColumn });
      editor.focus();
    },
    runCommand: (command) => {
      const editor = editorRef.current;
      if (!editor) return false;

      const commandId = EDITOR_COMMAND_IDS[command];
      if (!commandId) return false;
      if (command === "edit.paste") {
        void pasteIntoEditor(editor, window.goferDesktop?.clipboard?.readText, editor.getOption(monacoRef.current.editor.EditorOption.readOnly)).catch(error => setState(current => ({ ...current, error: error.message })));
        return true;
      }
      const action = editor.getAction(commandId);
      if (action && !action.isSupported()) { setState(current => ({ ...current, error: "This command is unavailable for the active document." })); return false; }
      editor.focus();
      editor.trigger("raticode-menu", commandId, null);
      return true;
    },
    save,
  }), [path, save]);

  useEffect(() => {
    let disposed = false;
    let contentListener;
    let commandContextListener;
    let remActions;
    let originalRemActions;
    let conflictControls;
    let resizeObserver;
    import("../lib/monaco.js").then(async ({ loadRattishMonaco, prepareLanguageServices }) => {
      if (disposed || !containerRef.current) return;
      const monaco = loadRattishMonaco();
      monacoRef.current = monaco;
      const cachedSession = textEditorSessions.get(path);
      const managed = managedDocumentRef.current;
      const session = managed?.source != null ? {
        content: managed.source,
        savedContent: managed.dirty ? (managed.savedSource ?? cachedSession?.savedContent ?? null) : managed.source,
        viewState: cachedSession?.viewState ?? null,
      } : cachedSession;
      editableRef.current = false;
      const initialGitBaseline = await refreshGitBaseline();
      if (disposed || !containerRef.current) return;
      const canRestoreSession = session && !initialGitBaseline?.deleted && !initialGitBaseline?.binary && gitGroup !== "staged";
      const comparisonOnly = (initialGitBaseline?.deleted && diffMode) || gitGroup === "staged";
      const readTextFile = window.goferDesktop?.textFiles?.read;
      let initialRead;
      recoveryConflictRef.current = false;
      if (canRestoreSession && session.recovered && readTextFile) {
        try { const disk = await readTextFile(path); recoveryConflictRef.current = Boolean(disk?.missing || disk.content !== session.savedContent); }
        catch { recoveryConflictRef.current = true; }
        if (disposed) return;
      }
      if (!canRestoreSession && readTextFile) {
        try {
          initialRead = initialGitBaseline?.binary ? { content: "Binary file changed. Text comparison is unavailable." }
            : comparisonOnly ? { content: initialGitBaseline?.modifiedContent || "" } : await readTextFile(path);
        } catch (error) {
          if (disposed) return;
          setState({ content: null, dirty: false, error: isMissingFileError(error) ? "" : error.message || "Unable to open file", missing: isMissingFileError(error), loading: false, saving: false });
          return;
        }
        if (disposed || !containerRef.current) return;
        // Missing files never create an editor or retain their old text model.
        if (initialRead?.missing) {
          textEditorSessions.delete(path);
          setState({ content: null, dirty: false, error: "", missing: true, loading: false, saving: false });
          return;
        }
      }
      const model = monaco.editor.createModel(
        session?.content ?? "",
        languageForPath(path),
        monaco.Uri.file(path),
      );
      modelRef.current = model;
      savedVersionRef.current = session?.content === session?.savedContent ? model.getAlternativeVersionId() : null;
      monaco.editor.setModelMarkers(
        model,
        "rattish",
        diagnosticsToMarkers(monaco, model, model.getValue(), diagnosticsRef.current),
      );
      const initialEditorSettings = editorSettingsRef.current;
      const editorOptions = {
        automaticLayout: false,
        cursorBlinking: reduceMotionRef.current ? "solid" : "smooth",
        fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
        fontSize: initialEditorSettings.fontSize,
        folding: true,
        glyphMargin: true,
        lineHeight: initialEditorSettings.lineHeight,
        minimap: { enabled: initialEditorSettings.minimap, maxColumn: 80, renderCharacters: false },
        padding: { top: 12, bottom: 20 },
        scrollBeyondLastLine: false,
        smoothScrolling: !reduceMotionRef.current,
        tabSize: initialEditorSettings.tabSize,
        theme: theme === "dark" ? "gofer-rattish-dark" : "gofer-rattish-light",
        wordWrap: initialEditorSettings.wordWrap ? "on" : "off",
      };
      let editor;
      if (diffMode && initialGitBaseline?.changed) {
        const originalModel = monaco.editor.createModel(
          initialGitBaseline.content ?? "",
          languageForPath(path),
          monaco.Uri.parse(`git-head://${encodeURI(path)}`),
        );
        originalModelRef.current = originalModel;
        const diffEditor = monaco.editor.createDiffEditor(
          containerRef.current,
          codeDiffEditorOptions(editorOptions),
        );
        diffEditor.setModel({ modified: model, original: originalModel });
        diffEditorRef.current = diffEditor;
        editor = diffEditor.getModifiedEditor();
      } else {
        editor = monaco.editor.create(containerRef.current, { ...editorOptions, model });
      }
      editorRef.current = editor;
      const publishCommands = () => {
        const next = editorCommandMetadata(editor);
        setCommands(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
      };
      commandContextListener = editor._contextKeyService?.onDidChangeContext(publishCommands);
      // Attaching the editor activates the lazy language provider before the
      // worker and command availability are queried.
      try { await prepareLanguageServices(model); } catch { /* Syntax editing remains available without a worker. */ }
      if (disposed) return;
      setCommands(editorCommandMetadata(editor));
      remActions = installRemActions(editor, () => ({ path }));
      if (diffEditorRef.current) originalRemActions = installRemActions(diffEditorRef.current.getOriginalEditor(), () => ({ path, version: "Original Git version" }));
      conflictControls = installConflictControls(monaco, editor, model);
      decorationIdsRef.current = editor.deltaDecorations(
        [],
        trackedChangeDecorations(initialGitBaseline, diffMode),
      );
      contentListener = model.onDidChangeContent(() => {
        const publishContent = Boolean(onContentChangeRef.current);
        const content = publishContent ? model.getValue() : undefined;
        const dirty = editableRef.current && model.getAlternativeVersionId() !== savedVersionRef.current
          && (model.getValueLength() !== savedContentRef.current.length || model.getValue() !== savedContentRef.current);
        if (editableRef.current) textEditorSessions.set(path, {
          // Materialize only when session persistence or a Git operation asks.
          get content() { return model.getValue(); },
          savedContent: savedContentRef.current,
          viewState: null,
        });
        setState((current) => {
          if (!publishContent && current.dirty === dirty && !current.error) return current;
          return { ...current, ...(publishContent ? { content } : {}), dirty, error: recoveryConflictRef.current ? current.error : "" };
        });
        if (editableRef.current) {
          if (publishContent) onContentChangeRef.current(content);
          scheduleAutosaveRef.current();
        }
      });
      resizeObserver = new ResizeObserver(() => (diffEditorRef.current ?? editor).layout());
      resizeObserver.observe(containerRef.current);

      if (canRestoreSession) {
        editableRef.current = true;
        savedContentRef.current = session.savedContent;
        savedVersionRef.current = session.content === session.savedContent ? model.getAlternativeVersionId() : null;
        editor.restoreViewState(session.viewState);
        setState({
          content: session.content,
          dirty: session.content !== session.savedContent,
          error: recoveryConflictRef.current ? "Recovered draft: the file changed on disk. Autosave is paused. Save explicitly to replace the disk version." : "",
          loading: false,
          saving: false,
        });
        setCommands(editorCommandMetadata(editor));
        revealEditorLocation(editor, navigationRequestRef.current);
        return;
      }
      if (!readTextFile) {
        editableRef.current = false;
        editor.updateOptions({ readOnly: true });
        setState({
          content: null,
          dirty: false,
          error: "The desktop file editor is unavailable.",
          loading: false,
          saving: false,
        });
        return;
      }
      const content = initialRead?.content ?? "";
      discardedSessionPaths.delete(path);
      editableRef.current = !comparisonOnly && !initialGitBaseline?.binary;
      editor.updateOptions({ readOnly: Boolean(comparisonOnly || initialGitBaseline?.binary || gitOperationRef.current) });
      savedContentRef.current = content;
      model.setValue(content);
      savedVersionRef.current = model.getAlternativeVersionId();
      if (!comparisonOnly && !initialGitBaseline?.binary) textEditorSessions.set(path, { content, savedContent: content, viewState: null });
      setState({ content, dirty: false, error: "", loading: false, saving: false });
      setCommands(editorCommandMetadata(editor));
      revealEditorLocation(editor, navigationRequestRef.current);
    }).catch(error => {
      if (disposed) return;
      editableRef.current = false;
      editorRef.current?.updateOptions({ readOnly: true });
      setState({ content: null, dirty: false, error: isMissingFileError(error) ? "" : error.message || "Unable to open file", missing: isMissingFileError(error), loading: false, saving: false });
    });
    return () => {
      disposed = true;
      window.clearTimeout(autosaveTimerRef.current);
      if (modelRef.current && editableRef.current) {
        if (discardedSessionPaths.has(path)) {
          discardedSessionPaths.delete(path);
        } else {
          textEditorSessions.set(path, {
            content: modelRef.current.getValue(),
            savedContent: savedContentRef.current,
            viewState: editorRef.current?.saveViewState() ?? null,
          });
        }
      }
      remActions?.dispose();
      originalRemActions?.dispose();
      conflictControls?.dispose();
      commandContextListener?.dispose();
      contentListener?.dispose();
      resizeObserver?.disconnect();
      decorationIdsRef.current = [];
      if (diffEditorRef.current) diffEditorRef.current.dispose();
      else editorRef.current?.dispose();
      originalModelRef.current?.dispose();
      modelRef.current?.dispose();
      editorRef.current = null;
      diffEditorRef.current = null;
      modelRef.current = null;
      monacoRef.current = null;
      originalModelRef.current = null;
    };
  }, [diffMode, gitGroup, diskRevision, path, refreshGitBaseline, save, theme]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (originalModelRef.current && gitBaseline) originalModelRef.current.setValue(gitBaseline.content || "");
    if (gitGroup === "staged" && modelRef.current && gitBaseline) modelRef.current.setValue(gitBaseline.modifiedContent || "");
    decorationIdsRef.current = editor.deltaDecorations(
      decorationIdsRef.current,
      trackedChangeDecorations(gitBaseline, diffMode),
    );
  }, [diffMode, gitBaseline, gitGroup]);

  useEffect(() => {
    editorSettingsRef.current = editorSettings;
    editorRef.current?.updateOptions({
      smoothScrolling: !reduceMotion,
      cursorBlinking: reduceMotion ? "solid" : "smooth",
      fontSize: editorSettings.fontSize,
      lineHeight: editorSettings.lineHeight,
      minimap: { enabled: editorSettings.minimap, maxColumn: 80, renderCharacters: false },
      tabSize: editorSettings.tabSize,
      wordWrap: editorSettings.wordWrap ? "on" : "off",
    });
  }, [editorSettings, reduceMotion]);

  useEffect(() => {
    const monaco = monacoRef.current;
    const model = modelRef.current;
    if (!monaco || !model) return;
    monaco.editor.setModelMarkers(
      model,
      "rattish",
      diagnosticsToMarkers(monaco, model, model.getValue(), diagnostics),
    );
  }, [diagnostics]);

  useEffect(() => {
    const monacoTheme = theme === "dark" ? "gofer-rattish-dark" : "gofer-rattish-light";
    import("../lib/monaco.js").then(({ loadRattishMonaco }) => loadRattishMonaco().editor.setTheme(monacoTheme));
  }, [theme]);

  useEffect(() => {
    if (!editing) {
      if (editorRef.current?.hasTextFocus()) document.activeElement?.blur?.();
      return;
    }
    if (!active) return;
    window.requestAnimationFrame(() => {
      editorRef.current?.layout();
      editorRef.current?.focus();
    });
  }, [active, editing]);

  const diffButton = gitBaseline?.changed ? (
    <button
      aria-label={diffMode ? "Hide file diff" : "Show file diff"}
      aria-pressed={diffMode}
      className={`z-20 inline-flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-[11px] font-semibold shadow-sm transition ${
        html ? "" : `absolute top-3 ${markdown || svg ? "right-20" : "right-4"}`
      } ${
        diffMode
          ? "bg-brand text-white"
          : "bg-white text-ink hover:bg-slate-50 dark:bg-[#252526] dark:hover:bg-[#333337]"
      }`}
      title={diffMode ? "Hide file diff" : "Compare with HEAD"}
      type="button"
      onClick={() => {
        if (!diffMode && !editing) onModeChange?.("edit");
        setDiffMode((current) => !current);
      }}
    >
      <GitCompareArrows aria-hidden="true" size={13} />
      Diff
    </button>
  ) : null;

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white" aria-label={`${fileName(path)} editor`}>
      <div className="relative min-h-0 flex-1">
        <div
          ref={containerRef}
          aria-hidden={!editing}
          className={`absolute inset-0 ${editing ? "visible" : "invisible pointer-events-none"}`}
        />
        {markdown && !editing && !state.loading && state.content != null ? (
          <MarkdownPreview
            content={state.content ?? ""}
            path={path}
            onEdit={() => onModeChange?.("edit")}
            onOpenRelativeLink={onOpenRelativeLink}
          />
        ) : null}
        {svg && !editing && !state.loading && state.content != null ? (
          <SvgPreview content={state.content} path={path} onEdit={() => onModeChange?.("edit")} />
        ) : null}
        {markdown ? (
          <MarkdownModeToggle
            disabled={state.loading || state.content == null}
            editing={editing}
            onModeChange={onModeChange}
          />
        ) : null}
        {html ? (
          // The labelled HTML toggle is wider than the icon toggles, so Diff flows beside it.
          <div className="absolute right-4 top-3 z-20 flex items-center gap-2">
            {diffButton}
            <HtmlModeToggle editing={editing} onModeChange={onModeChange} />
          </div>
        ) : null}
        {svg ? (
          <DocumentModeToggle
            disabled={state.loading || state.content == null}
            editing={editing}
            label="SVG"
            onModeChange={onModeChange}
          />
        ) : null}
        {html ? null : diffButton}
        {state.loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-white/90 text-sm text-muted dark:bg-[#19191b]/90">
            <span className="flex items-center gap-2"><Loader2 className="animate-spin" size={16} />Opening {fileName(path)}</span>
          </div>
        ) : null}
        {state.error ? (
          <div className="absolute bottom-3 left-3 right-3 z-10 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
            <AlertTriangle className="shrink-0" size={14} />
            <span className="min-w-0 flex-1 break-words">{state.error}</span>
          </div>
        ) : null}
      </div>
      <div className="flex h-6 shrink-0 items-center justify-end gap-3 border-t border-line bg-white px-3 text-[10px] text-muted">
        <span>{languageLabel(languageForPath(path))}</span>
        {!["typescript", "javascript", "json", "css", "scss", "less", "html", "rattish"].includes(languageForPath(path)) ? <span title="Syntax coloring and word suggestions are available. This language has no semantic completion, definitions, diagnostics, or formatter.">Syntax editing</span> : null}
        <span>Spaces: {editorSettings.tabSize}</span>
      </div>
    </section>
  );
});

export function MarkdownPreview({ content, path, onEdit, onOpenRelativeLink }) {
  return (
    <article
      aria-label={`${fileName(path)} Markdown preview`}
      className="workflow-scrollbar absolute inset-0 overflow-auto bg-white px-8 pb-16 pt-12 text-sm text-slate-700 dark:bg-[#19191b] dark:text-[#d4d4d4]"
      title="Double-click to edit"
      onDoubleClick={(event) => {
        if (event.target.closest?.("a, button, input")) return;
        onEdit?.();
      }}
    >
      <MarkdownContent
        className="mx-auto w-full max-w-[76ch]"
        sourcePath={path}
        value={content}
        onOpenRelativeLink={onOpenRelativeLink}
      />
    </article>
  );
}

export function MarkdownModeToggle({ disabled = false, editing, onModeChange }) {
  return (
    <div
      aria-label="Markdown view mode"
      className="absolute right-4 top-3 z-20 flex items-center rounded-lg border border-line bg-white p-0.5 shadow-sm dark:bg-[#252526]"
      role="group"
    >
      <button
        aria-label="Preview Markdown"
        aria-pressed={!editing}
        className={`grid h-7 w-7 place-items-center rounded-md transition ${
          !editing ? "bg-slate-100 text-ink dark:bg-[#333337]" : "text-muted hover:text-ink"
        }`}
        disabled={disabled}
        title="Preview Markdown"
        type="button"
        onClick={() => onModeChange?.("preview")}
      >
        <Eye size={14} />
      </button>
      <button
        aria-label="Edit Markdown"
        aria-pressed={editing}
        className={`grid h-7 w-7 place-items-center rounded-md transition ${
          editing ? "bg-slate-100 text-ink dark:bg-[#333337]" : "text-muted hover:text-ink"
        }`}
        disabled={disabled}
        title="Edit Markdown"
        type="button"
        onClick={() => onModeChange?.("edit")}
      >
        <PencilLine size={14} />
      </button>
    </div>
  );
}

export function DocumentModeToggle({ disabled = false, editing, label, onModeChange }) {
  return (
    <div
      aria-label={`${label} view mode`}
      className="absolute right-4 top-3 z-20 flex items-center rounded-lg border border-line bg-white p-0.5 shadow-sm dark:bg-[#252526]"
      role="group"
    >
      <button
        aria-label={`Preview ${label}`}
        aria-pressed={!editing}
        className={`grid h-7 w-7 place-items-center rounded-md transition ${!editing ? "bg-slate-100 text-ink dark:bg-[#333337]" : "text-muted hover:text-ink"}`}
        disabled={disabled}
        title={`Preview ${label}`}
        type="button"
        onClick={() => onModeChange?.("preview")}
      ><Eye size={14} /></button>
      <button
        aria-label={`Edit ${label}`}
        aria-pressed={editing}
        className={`grid h-7 w-7 place-items-center rounded-md transition ${editing ? "bg-slate-100 text-ink dark:bg-[#333337]" : "text-muted hover:text-ink"}`}
        disabled={disabled}
        title={`Edit ${label}`}
        type="button"
        onClick={() => onModeChange?.("edit")}
      ><PencilLine size={14} /></button>
    </div>
  );
}

export function SvgPreview({ content, path, onEdit }) {
  const source = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(content ?? "")}`;
  return (
    <div
      aria-label={`${fileName(path)} SVG preview`}
      className="absolute inset-0 grid place-items-center overflow-auto bg-[linear-gradient(45deg,#f4f4f5_25%,transparent_25%),linear-gradient(-45deg,#f4f4f5_25%,transparent_25%),linear-gradient(45deg,transparent_75%,#f4f4f5_75%),linear-gradient(-45deg,transparent_75%,#f4f4f5_75%)] bg-[length:20px_20px] bg-[position:0_0,0_10px,10px_-10px,-10px_0] p-12 dark:bg-[#19191b]"
      title="Double-click to edit"
      onDoubleClick={onEdit}
    >
      <img alt={`Preview of ${fileName(path)}`} className="max-h-full max-w-full" src={source} />
    </div>
  );
}

export function ImagePreview({ path }) { return <MediaPreview path={path} />; }

export function MediaPreview({ path, active = true }) {
  const kind = mediaKind(path) || "image";
  const mediaRef = useRef(null);
  const [state, setState] = useState({ url: "", error: "", loading: true });
  useEffect(() => {
    if (!active) mediaRef.current?.pause?.();
  }, [active]);
  useEffect(() => {
    let disposed = false;
    let previewId = "";
    const bridge = window.goferDesktop?.textFiles;
    const releasePreview = id => { if (id) void Promise.resolve(bridge?.closePreview?.(id)).catch(() => {}); };
    const readPreview = bridge?.openPreview || (kind === "image" ? bridge?.readPreview : null);
    setState({ url: "", error: "", loading: true });
    if (!readPreview) {
      setState({ url: "", error: "Media preview is unavailable. Restart the desktop app.", loading: false });
      return undefined;
    }
    readPreview(path).then((payload) => {
      previewId = payload?.id || "";
      if (disposed) { releasePreview(previewId); return; }
      setState({ url: payload?.url || payload?.dataUrl || "", error: "", loading: false });
    }).catch((error) => {
      if (!disposed) setState({
        url: "",
        error: error instanceof Error ? error.message : "Unable to preview media",
        loading: false,
      });
    });
    return () => { disposed = true; releasePreview(previewId); };
  }, [path, kind]);
  function playbackError() {
    setState(current => ({ ...current, error: kind === "image" ? "This image could not be displayed." : "This file could not be played. Its format or codec may not be supported." }));
  }
  return (
    <section className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-4 overflow-auto bg-slate-50 p-8 dark:bg-[#19191b]" aria-label={`${fileName(path)} ${kind} preview`}>
      {state.loading ? <span className="flex items-center gap-2 text-sm text-muted"><Loader2 className="animate-spin" size={16} />Opening {fileName(path)}</span> : null}
      {state.error ? <div className="flex flex-col items-center gap-3 text-sm text-red-700" role="alert"><p className="flex items-center gap-2"><AlertTriangle size={16} />{state.error}</p><button type="button" className="rounded border border-line px-3 py-2 text-xs text-ink" onClick={() => window.goferDesktop?.workspace?.openPath?.(path).catch(error => setState(current => ({ ...current, error: error.message })))}>Open in default app</button></div> : null}
      {state.url && !state.error ? kind === "video" ? <video ref={mediaRef} aria-label={`Play ${fileName(path)}`} controls playsInline preload="metadata" className="min-h-0 max-h-full max-w-full" src={state.url} onError={playbackError} /> : kind === "audio" ? <><p className="text-sm text-ink">{fileName(path)}</p><audio ref={mediaRef} aria-label={`Play ${fileName(path)}`} controls preload="metadata" className="w-full max-w-lg" src={state.url} onError={playbackError} /></> : <img alt={`Preview of ${fileName(path)}`} className="min-h-0 max-h-full max-w-full object-contain" src={state.url} onError={playbackError} /> : null}
    </section>
  );
}

// Older desktop builds may still reject the read instead of returning missing: true.
export function isMissingFileError(error) {
  return ["ENOENT", "ENOTDIR"].includes(error?.code || error?.cause?.code)
    || /\b(?:ENOENT|ENOTDIR)\b/.test(error?.message || "");
}

export function replaceEditorModelContent(model, content) {
  if (model.getValue() === content) return;
  if (model.pushEditOperations && model.getFullModelRange) {
    model.pushStackElement?.();
    model.pushEditOperations([], [{ range: model.getFullModelRange(), text: content }], () => null);
    model.pushStackElement?.();
  } else model.setValue(content);
}

export function codeWorkspaceShortcutAction(event, options = {}) {
  if (!options.active || event.repeat) return null;
  if (
    event.ctrlKey
    && !event.altKey
    && !event.metaKey
    && String(event.key ?? "").toLowerCase() === "tab"
    && options.currentPath
  ) return event.shiftKey ? "previous-tab" : "next-tab";
  if (!options.browserActive && options.currentPath
    && matchesCommand(event, options.settings, "editor.find", options.platform)) return "find";
  if (matchesCommand(event, options.settings, "file.new")) return "new";
  if (matchesCommand(event, options.settings, "file.save") && options.currentPath) return "save";
  if (matchesCommand(event, options.settings, "file.close") && options.currentPath) return "close";
  if (matchesCommand(event, options.settings, "editor.toggleWordWrap")) return "toggle-word-wrap";
  // Monaco still owns its original binding. Respect a changed or cleared setting,
  // while leaving shortcuts assigned to other Raticode commands available.
  if (!options.browserActive && options.currentPath
    && matchesCommand(event, DEFAULT_APP_SETTINGS, "editor.find", options.platform)
    && !KEYBINDING_COMMANDS.some(command => command.id !== "editor.find"
      && matchesCommand(event, options.settings, command.id, options.platform))) return "suppress-find";
  return null;
}

export function trackedChangeDecorations(baseline, diffMode = false) {
  if (diffMode || !baseline?.changed) return [];
  return (baseline.hunks ?? []).map((hunk) => ({
    options: {
      description: "Git tracked change",
      isWholeLine: true,
      linesDecorationsClassName: "tracked-change-line",
    },
    range: {
      endColumn: 1,
      endLineNumber: hunk.endLine,
      startColumn: 1,
      startLineNumber: hunk.startLine,
    },
  }));
}

export function codeDiffEditorOptions(editorOptions = {}) {
  return {
    ...editorOptions,
    diffAlgorithm: "advanced",
    diffCodeLens: true,
    enableSplitViewResizing: true,
    ignoreTrimWhitespace: false,
    originalEditable: false,
    renderSideBySide: true,
    renderWhitespace: "all",
  };
}

export function fileTabCloseTargets(openPaths, path, action) {
  const index = openPaths.indexOf(path);
  if (index < 0) return [];
  if (action === "others") return openPaths.filter((candidate) => candidate !== path);
  if (action === "right") return openPaths.slice(index + 1);
  if (action === "all") return [...openPaths];
  return [path];
}

export function reorderCodeTabs(openPaths, sourcePath, targetPath) {
  const sourceIndex = openPaths.indexOf(sourcePath);
  const targetIndex = openPaths.indexOf(targetPath);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return [...openPaths];
  const next = [...openPaths];
  const [source] = next.splice(sourceIndex, 1);
  next.splice(targetIndex, 0, source);
  return next;
}

export function stableCodeDocumentPaths(previousPaths, openPaths) {
  const openPathSet = new Set(openPaths);
  const previousPathSet = new Set(previousPaths);
  return [
    ...previousPaths.filter((path) => openPathSet.has(path)),
    ...openPaths.filter((path) => !previousPathSet.has(path)),
  ];
}

export function adjacentCodeTab(openPaths, currentPath, direction = 1) {
  if (!openPaths.length) return "";
  const currentIndex = openPaths.indexOf(currentPath);
  if (currentIndex < 0) return openPaths[0];
  return openPaths[(currentIndex + direction + openPaths.length) % openPaths.length];
}

const ACTIVE_TAB_RUN_STATES = new Set(["running", "starting", "submitting", "queued", "stopping"]);

export function EditorTabsMenu({ entries, activePath, label = "All editor tabs", onActivate }) {
  const detailsRef = useRef(null);
  const summaryRef = useRef(null);
  const [project, setProject] = useState("");
  const [runFilter, setRunFilter] = useState("");
  const projectKey = (entry) => entry.projectRoot || (entry.browser ? "browser" : "unassigned");
  const projects = [...new Set(entries.map(projectKey))];
  const selectedProject = projects.includes(project) ? project : "";
  const matching = entries.filter((entry) => {
    if (selectedProject && projectKey(entry) !== selectedProject) return false;
    const status = String(entry.tab?.status || "").toLowerCase();
    if (runFilter === "active") return ACTIVE_TAB_RUN_STATES.has(status);
    if (runFilter === "unread") return Boolean(entry.tab?.unread || entry.tab?.unreadFailure);
    if (runFilter === "failed") return ["failed", "error"].includes(status) || Boolean(entry.tab?.unreadFailure);
    return true;
  });
  useEffect(() => {
    const outside = (event) => {
      if (detailsRef.current?.open && !detailsRef.current.contains(event.target)) detailsRef.current.open = false;
    };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, []);
  function close(restoreFocus = false) {
    detailsRef.current.open = false;
    if (restoreFocus) summaryRef.current?.focus();
  }
  return <details ref={detailsRef} className="group/tabs relative shrink-0" onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
  }}>
    <summary ref={summaryRef} aria-label={label} title="All tabs" className="grid h-9 w-8 cursor-pointer list-none place-items-center text-muted hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onKeyDown={(event) => {
      if (event.key !== "ArrowDown") return;
      event.preventDefault();
      detailsRef.current.open = true;
      detailsRef.current.querySelector("[data-open-tab]")?.focus();
    }}><ChevronDown aria-hidden="true" size={14} /></summary>
    <div className="absolute right-0 top-full z-50 w-80 max-w-[calc(100vw-3rem)] rounded-b-md border border-line bg-white p-2 shadow-lg" aria-label="Open tabs">
      <div className="mb-2 grid grid-cols-2 gap-2">
        <label className="min-w-0 text-[10px] text-muted">Project
          <select aria-label="Filter tabs by project" className="mt-1 h-7 w-full min-w-0 rounded border border-line bg-white px-1 text-xs text-ink" value={selectedProject} onChange={event => setProject(event.target.value)}>
            <option value="">All projects</option>
            {projects.map(root => <option key={root} value={root}>{root === "browser" ? "Browser tabs" : root === "unassigned" ? "Other files" : root}</option>)}
          </select>
        </label>
        <label className="min-w-0 text-[10px] text-muted">Run status
          <select aria-label="Filter tabs by run status" className="mt-1 h-7 w-full rounded border border-line bg-white px-1 text-xs text-ink" value={runFilter} onChange={event => setRunFilter(event.target.value)}>
            <option value="">All statuses</option><option value="active">Active runs</option><option value="unread">Unread results</option><option value="failed">Failures</option>
          </select>
        </label>
      </div>
      <div className="workflow-scrollbar max-h-64 overflow-y-auto" onKeyDown={(event) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        const buttons = [...event.currentTarget.querySelectorAll("[data-open-tab]")];
        const index = buttons.indexOf(document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length;
        buttons[next]?.focus();
      }}>
        {matching.map(({ path, label: title, projectRoot, browser, tab }) => <button key={path} data-open-tab type="button" aria-current={path === activePath ? "page" : undefined} className="flex w-full items-center gap-2 rounded px-2 py-2 text-left text-xs hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand" onClick={() => { onActivate?.(path); close(true); }}>
          {tab ? <Workflow aria-hidden="true" size={14} className="shrink-0 text-brand" /> : <FileTypeIcon browserTab={browser} path={path} />}
          <span className="min-w-0 flex-1"><span className="block truncate">{title}</span><span className="block truncate text-[10px] text-muted">{tab?.contextLabel || projectRoot || path}</span></span>
          {tab?.statusLabel ? <span className="max-w-24 shrink-0 truncate text-[10px] text-muted" title={tab.statusLabel}>{tab.statusLabel}</span> : null}
        </button>)}
      </div>
      <p aria-live="polite" className="px-2 pt-2 text-[10px] text-muted">{matching.length ? `${matching.length} of ${entries.length} tabs` : "No tabs match these filters."}</p>
    </div>
  </details>;
}

export function WorkflowTabControls({ tab, onAction }) {
  const status = tab.status || "ready";
  const StatusIcon = ["running", "starting", "submitting", "queued", "stopping"].includes(status) ? Loader2 : ["failed", "error", "disconnected"].includes(status) ? AlertTriangle : ["success", "succeeded", "completed"].includes(status) ? Check : tab.unreadFailure ? AlertTriangle : null;
  return <>
    {StatusIcon ? <button type="button" aria-label={`${tab.name}: ${tab.statusLabel || status}. Review runs`} title={tab.statusLabel || status} className={`relative grid h-7 w-6 shrink-0 place-items-center rounded hover:bg-slate-100 ${["failed", "error"].includes(status) ? "text-red-600" : "text-muted"}`} onClick={() => onAction?.(tab, "review")}>
      <StatusIcon aria-hidden="true" size={13} className={status === "running" ? "motion-safe:animate-spin" : ""} />
      {tab.unread || tab.unreadFailure ? <span aria-label="Unread run result" className="absolute right-0 top-1 h-1.5 w-1.5 rounded-full bg-red-600" /> : null}
    </button> : null}
    {tab.action ? <button type="button" aria-label={tab.actionLabel || `${tab.action === "stop" ? "Stop" : tab.action === "review" ? "Review runs for" : tab.dirty ? "Save and run" : "Run"} ${tab.name}`} title={tab.actionLabel || (tab.action === "stop" ? "Stop run" : tab.action === "review" ? "Review runs" : tab.dirty ? "Save and run" : "Run workflow")} disabled={tab.actionDisabled} className="grid h-7 w-6 shrink-0 place-items-center rounded text-muted opacity-0 hover:bg-slate-100 hover:text-ink group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 disabled:opacity-40" onClick={() => onAction?.(tab, tab.action)}>
      {tab.action === "stop" ? <Square aria-hidden="true" size={12} /> : tab.action === "review" ? <Eye aria-hidden="true" size={12} /> : <Play aria-hidden="true" size={12} />}
    </button> : null}
  </>;
}

function FileTypeIcon({ browserTab, path }) {
  const favicon = browserTabFavicon(browserTab);
  const [faviconFailed, setFaviconFailed] = useState(false);
  useEffect(() => setFaviconFailed(false), [favicon]);
  if (parseCommitDiffPath(path)) return <GitCompareArrows aria-hidden="true" className="shrink-0 text-brand" size={14} />;
  if (browserTab && favicon && !faviconFailed) {
    return (
      <img
        alt=""
        className="h-3.5 w-3.5 shrink-0 object-contain"
        draggable={false}
        src={favicon}
        onError={() => setFaviconFailed(true)}
      />
    );
  }
  if (browserTab) return <Globe aria-hidden="true" className="shrink-0 text-brand" size={14} />;
  if (path.toLowerCase().endsWith(".json")) return <FileJson2 aria-hidden="true" className="shrink-0 text-amber-600" size={14} />;
  if (languageForPath(path) !== "plaintext") return <FileCode2 aria-hidden="true" className="shrink-0 text-brand" size={14} />;
  return <FileText aria-hidden="true" className="shrink-0 text-muted" size={14} />;
}


export function isMarkdownPath(path) {
  return languageForPath(path) === "markdown";
}

export function isHtmlPath(path) {
  return [".htm", ".html"].some((extension) => String(path ?? "").toLowerCase().endsWith(extension));
}

export function isSvgPath(path) {
  return String(path ?? "").toLowerCase().endsWith(".svg");
}

export function isPdfPath(path) {
  return String(path ?? "").toLowerCase().endsWith(".pdf");
}

export function isImagePath(path) {
  return [".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp"]
    .some((extension) => String(path ?? "").toLowerCase().endsWith(extension));
}

export function mediaKind(path) {
  if (isImagePath(path)) return "image";
  const extension = String(path || "").split(".").at(-1).toLowerCase();
  if (["mp4", "m4v", "mov", "webm", "ogv", "mkv", "avi"].includes(extension)) return "video";
  if (["mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "aif", "aiff"].includes(extension)) return "audio";
  return "";
}

export function codeDocumentMode(
  path,
  documentModes = {},
  editorSettings = DEFAULT_APP_SETTINGS.editor,
) {
  if (!isMarkdownPath(path) && !isHtmlPath(path) && !isSvgPath(path)) return "edit";
  if (documentModes[path] === "edit" || documentModes[path] === "preview") {
    return documentModes[path];
  }
  if (isMarkdownPath(path)) return editorSettings.markdownDefault;
  if (isHtmlPath(path)) return editorSettings.htmlDefault;
  return "preview";
}

export function browserTabLabel(tab = {}) {
  const title = String(tab.title ?? "").trim();
  if (title) return title;
  const url = String(tab.url ?? "").trim();
  if (!url || url === "about:blank") return "New Tab";
  if (url === "raticode://home") return "Raticode";
  try {
    return new URL(url).hostname.replace(/^www\./i, "") || url;
  } catch {
    return url;
  }
}

export function browserTabFavicon(tab = {}) {
  const favicon = String(tab?.favicon ?? "").trim();
  if (/^(?:https?:|file:|data:image\/)/i.test(favicon)) return favicon;
  return String(tab?.url ?? "").trim() === "raticode://home" ? raticodeIcon : "";
}

export function browserViewTabMetadata(browserTab, viewState) {
  if (browserTab) return viewState ? { ...browserTab, ...viewState } : browserTab;
  const title = String(viewState?.title ?? "").trim();
  const url = String(viewState?.url ?? "").trim();
  return title || /^https?:/i.test(url) ? viewState : null;
}

export function codeTabLabel(path, browserTab) {
  const commitDiff = parseCommitDiffPath(path);
  if (commitDiff) return `Diff ${commitDiff.hash.slice(0, 8)}`;
  return browserTab ? browserTabLabel(browserTab) : fileName(path);
}

export function revealEditorLocation(editor, target) {
  if (!editor || !Number.isInteger(target?.lineNumber) || target.lineNumber < 1) return false;
  const position = {
    column: Number.isInteger(target.column) && target.column > 0 ? target.column : 1,
    lineNumber: target.lineNumber,
  };
  editor.revealPositionInCenter(position);
  editor.setPosition(position);
  editor.focus();
  return true;
}

function languageLabel(language) {
  return language === "plaintext" ? "Plain text" : language;
}

function fileName(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).at(-1) ?? "Untitled";
}

export function duplicateTabFolder(path, openPaths) {
  const name = fileName(path);
  if (openPaths.filter((candidate) => fileName(candidate) === name).length < 2) return "";
  return String(path ?? "").split(/[\\/]/).filter(Boolean).at(-2) ?? "";
}

function withSetValue(current, value) {
  const next = new Set(current);
  next.add(value);
  return next;
}

function withoutSetValue(current, value) {
  const next = new Set(current);
  next.delete(value);
  return next;
}

export default CodeWorkspace;
