import { pathKey as normalizeWorkspacePath, samePath, pathWithin, displayPath } from "../lib/workspacePaths.js";
import { startPolling, shareInFlight } from "../lib/refresh.js";
import RemActionIcon from "./RemActionIcon.jsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  Waypoints,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  FileCode2,
  FileJson2,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GitBranch,
  GitMerge,
  GitCommitHorizontal,
  Loader2,
  PencilLine,
  Plus,
  Minus,
  MoreHorizontal,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import GitIntegrationControls from "./GitIntegrationControls.jsx";
import WorktreeContextMenu, { historyOperations, integrationOperations } from "./WorktreeContextMenu.jsx";
import SwarmSidebar from "./SwarmSidebar.jsx";
import RatSwarmIcon from "./RatSwarmIcon.jsx";
import ProjectSearch from "./ProjectSearch.jsx";
import { Dialog } from "./Dialog.jsx";
import { hasUnsavedCodeChanges } from "../lib/codeEditorSessions.js";
import { PathNameDialog } from "./PathNameDialog.jsx";
import { DEFAULT_APP_SETTINGS, matchesCommand } from "../lib/settings.js";

export default function CodeFileExplorer({
  activeFilePath = "",
  sidebarView: controlledSidebarView,
  onSidebarViewChange,
  workflowsContent,
  workflowsHeader,
  hideProjectSelector = false,
  paneVisible = true,
  newFileRequest = 0,
  query = "",
  recentProjects = [],
  settings = DEFAULT_APP_SETTINGS,
  workflow,
  onOpenSwarm,
  selectedSwarmId,
  onFilesystemChange,
  onCloseActiveFile,
  onOpenFile,
  onRemoveRecentProject,
  onSelectProject,
}) {
  const rootPath = workflow?.projectRoot ?? "";
  const gitPanelsLoadingRef = useRef(null);
  const gitStatusLoadingRef = useRef(false);
  const lastNewFileRequestRef = useRef(newFileRequest);
  const recentMenuRef = useRef(null);
  const treeRef = useRef(null);
  const activeFileRowRef = useRef(null);
  const directoriesRef = useRef({});
  const revealRequestRef = useRef(0);
  const [directories, setDirectories] = useState({});
  const [expanded, setExpanded] = useState(() => new Set());
  const [loadingPaths, setLoadingPaths] = useState(() => new Set());
  const [selectedPath, setSelectedPath] = useState(rootPath);
  const [contextMenu, setContextMenu] = useState(null);
  const [clipboardEntry, setClipboardEntry] = useState(null);
  const [copiedPath, setCopiedPath] = useState("");
  const [nameRequest, setNameRequest] = useState(null);
  const [error, setError] = useState("");
  const [grantRequired, setGrantRequired] = useState(false);
  const [recentMenuOpen, setRecentMenuOpen] = useState(false);
  const [sourceControl, setSourceControl] = useState({ active: false, entries: [] });
  const [sourceControlRoot, setSourceControlRoot] = useState(null);
  const [gitOperationBusy, setGitBusy] = useState(false);
  const [gitStaging, setGitStaging] = useState(false);
  const pendingStagingRef = useRef(null);
  const gitOperationRef = useRef(false);
  const currentRootRef = useRef(rootPath);
  currentRootRef.current = rootPath;
  const gitStatusPending = sourceControlRoot !== rootPath;
  const gitBusy = gitOperationBusy || gitStaging || gitStatusPending;
  const [branchRequest, setBranchRequest] = useState(null);
  const [branchDraft, setBranchDraft] = useState("");
  useEffect(() => { setHistoryMenu(null); setBranchRequest(null); setWorktreeStartPoint(""); setWorktreeFormOpen(false); setIntegrationRequest(null); setIntegrationSource(""); setWorktreeMenu(null); }, [rootPath]);
  const [historyMenu, setHistoryMenu] = useState(null);
  const [worktreeStartPoint, setWorktreeStartPoint] = useState("");
  const [generatingMessage, setGeneratingMessage] = useState(false);
  const generationRef = useRef(null);
  useEffect(() => () => generationRef.current?.abort(), [rootPath]);
  const [commitMessage, setCommitMessage] = useState("");
  const [gitNotice, setGitNotice] = useState("");
  const [blockedBranch, setBlockedBranch] = useState("");
  const [remote, setRemote] = useState("");
  const [gitError, setGitError] = useState("");
  useEffect(() => { setCommitMessage(""); setBlockedBranch(""); setGitError(""); setGitNotice(""); setRemote(""); }, [rootPath]);
  const [sourceTab, setSourceTab] = useState("changes");
  const stagedCount = sourceControl.entries.filter((entry) => entry.staged && entry.status !== "!").length;
  const [localSidebarView, setLocalSidebarView] = useState("files");
  const sidebarView = controlledSidebarView || localSidebarView;
  const setSidebarView = useCallback((value) => { setLocalSidebarView(value); onSidebarViewChange?.(value); }, [onSidebarViewChange]);
  const [searchFocusRequest, setSearchFocusRequest] = useState(0);
  useEffect(() => {
    function openSearch(event) {
      if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.key.toLowerCase() !== "f") return;
      event.preventDefault();
      setSidebarView("search");
      setSearchFocusRequest((value) => value + 1);
    }
    window.addEventListener("keydown", openSearch);
    return () => window.removeEventListener("keydown", openSearch);
  }, [setSidebarView]);
  const [gitHistory, setGitHistory] = useState({ active: false, commits: [], loading: false });
  const [expandedCommits, setExpandedCommits] = useState(() => new Set());
  const [copiedCommitHash, setCopiedCommitHash] = useState("");
  const [worktrees, setWorktrees] = useState({ active: false, items: [], loading: false });
  const [integrationSource, setIntegrationSource] = useState("");
  const [integrationRequest, setIntegrationRequest] = useState(null);
  const [worktreeMenu, setWorktreeMenu] = useState(null);
  const [worktreeRemoval, setWorktreeRemoval] = useState(null);
  const [worktreeRemovalError, setWorktreeRemovalError] = useState("");
  useEffect(() => { setWorktreeRemoval(null); setWorktreeRemovalError(""); }, [rootPath]);
  const [worktreeFormOpen, setWorktreeFormOpen] = useState(false);
  const [worktreeBranch, setWorktreeBranch] = useState("");
  const [worktreeFolder, setWorktreeFolder] = useState("");
  const [worktreeCreateBranch, setWorktreeCreateBranch] = useState(false);

  directoriesRef.current = directories;

  const loadDirectory = useCallback(async (directory, { clearError = true } = {}) => {
    if (!directory) return [];
    if (clearError) setError("");
    setLoadingPaths((current) => withSetValue(current, directory));
    try {
      const payload = await window.goferDesktop?.workspace?.listDirectory?.({
        currentPath: directory,
        create: false,
      });
      if (currentRootRef.current !== rootPath) return [];
      if (!payload) throw new Error("The desktop filesystem bridge is unavailable.");
      const entries = payload.entries ?? [];
      setDirectories((current) => {
        const next = { ...current, [directory]: entries };
        directoriesRef.current = next;
        return next;
      });
      if (directory === rootPath) setGrantRequired(false);
      return entries;
    } catch (loadError) {
      if (currentRootRef.current !== rootPath || loadError?.name === "AbortError") return [];
      const message = loadError instanceof Error ? loadError.message : String(loadError);
      setError(message);
      if (directory === rootPath && /approved|grant|outside/i.test(message)) {
        setGrantRequired(true);
      }
      return [];
    } finally {
      if (currentRootRef.current === rootPath) setLoadingPaths((current) => withoutSetValue(current, directory));
    }
  }, [rootPath]);

  const loadSourceControlStatus = useCallback(async () => {
    if (!rootPath || pendingStagingRef.current?.rootPath === rootPath || gitStatusLoadingRef.current?.rootPath === rootPath) return;
    const request = { rootPath };
    gitStatusLoadingRef.current = request;
    try {
      const payload = await shareInFlight(`git-status:${rootPath}`, () => window.goferDesktop?.workspace?.gitStatus?.(rootPath));
      if (gitStatusLoadingRef.current !== request || currentRootRef.current !== rootPath) return;
      if (payload?.error) throw new Error(payload.error);
      const next = payload?.active
        ? { ...payload, active: true, entries: payload.entries ?? [] }
        : { active: false, entries: [] };
      setSourceControl((current) => sourceControlSnapshotsEqual(current, next) ? current : next);
      setSourceControlRoot(rootPath);
    } catch (error) {
      if (gitStatusLoadingRef.current !== request || currentRootRef.current !== rootPath || error?.name === "AbortError") return;
      setGitError(error instanceof Error ? error.message : "Unable to load Git status");
      setSourceControl({ active: false, entries: [] });
      setSourceControlRoot(rootPath);
    } finally {
      if (gitStatusLoadingRef.current === request) gitStatusLoadingRef.current = null;
    }
  }, [rootPath]);

  function openHistoryMenu(event, commit) {
    event.preventDefault();
    if (gitBusy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setHistoryMenu({ source: commit.shortHash, hash: commit.hash, x: event.clientX ?? rect.left, y: event.clientY ?? rect.bottom, trigger: event.currentTarget.querySelector("button") });
  }

  async function historyAction(kind) {
    const { hash } = historyMenu;
    setHistoryMenu(null);
    if (kind === "worktree-commit") {
      setWorktreeStartPoint(hash); setWorktreeCreateBranch(true); setWorktreeBranch(""); setWorktreeFolder(""); setWorktreeFormOpen(true); setSourceTab("branches"); return;
    }
    if (kind === "branch-commit") {
      setBranchRequest({ hash }); setBranchDraft(""); return;
    } else if (!window.confirm(kind === "reset-hard" ? `Hard reset to ${hash.slice(0, 8)}? This moves the current branch and permanently discards tracked staged and unstaged changes. Untracked files obstructing checkout can also be removed.` : kind === "reset-soft" ? `Soft reset to ${hash.slice(0, 8)}? Move the current branch while keeping the index and working files.` : `Detach HEAD at ${hash.slice(0, 8)}? New commits will need a branch to keep them.`)) return;
    await changeSourceControl(kind, { hash });
  }

  async function generateCommitMessage() {
    if (generatingMessage || gitBusy || !stagedCount) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => { if (currentRootRef.current === rootPath) setGitError("Rem timed out. Try generating again."); controller.abort(); }, 180000);
    generationRef.current = controller;
    const draft = commitMessage;
    setGeneratingMessage(true); setGitError("");
    try {
      const snapshot = await window.goferDesktop.workspace.gitRepoAction(rootPath, "staged-diff");
      if (snapshot?.error || (!snapshot?.diff && !snapshot?.inspectStaged)) throw new Error(snapshot?.error || "No staged diff available.");
      if (controller.signal.aborted) return;
      const message = await new Promise((resolve, reject) => {
        const cancel = () => reject(new Error("Generation cancelled."));
        controller.signal.addEventListener("abort", cancel, { once: true });
        window.dispatchEvent(new CustomEvent("gofer:rem-commit-message", { detail: { projectRoot: rootPath, diff: snapshot.diff, inspectStaged: snapshot.inspectStaged, signal: controller.signal, resolve, reject } }));
      });
      if (controller.signal.aborted || currentRootRef.current !== rootPath) return;
      const current = await window.goferDesktop.workspace.gitRepoAction(rootPath, "staged-diff");
      if (controller.signal.aborted || currentRootRef.current !== rootPath) return;
      if (current?.error || current?.tree !== snapshot.tree) throw new Error("Staged changes changed. Generate a new message.");
      setCommitMessage(value => value === draft ? message : value);
      setGitNotice("Rem generated a message. Your edits to an existing draft are preserved.");
    } catch (cause) { if (!controller.signal.aborted && currentRootRef.current === rootPath) setGitError(cause.message); }
    finally { clearTimeout(timeout); if (generationRef.current === controller) { generationRef.current = null; setGeneratingMessage(false); } }
  }

  async function changeSourceControl(action, value) {
    if (gitStatusPending || gitOperationRef.current || gitOperationBusy || (pendingStagingRef.current && action !== "commit") || (action === "commit" && (!commitMessage.trim() || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")))) return;
    const changesWorkingTree = (["switch", "stash-switch", "stash-apply", "pull", "reset-soft", "reset-hard", "detach-commit", "branch-commit"].includes(action) || /^(merge|rebase)-/.test(action)) || action.startsWith("revert");
    if ((changesWorkingTree || action === "stage") && hasUnsavedCodeChanges(rootPath)) {
      setGitError("Save your unsaved editor changes before changing the Git working tree.");
      return;
    }
    const bulk = Array.isArray(value);
    if (bulk && !value.length) return;
    if (action === "stage" || action === "unstage") {
      const paths = bulk ? value : [value];
      const selected = new Set(paths);
      gitStatusLoadingRef.current = null;
      setGitStaging(true);
      setGitError("");
      setGitNotice("");
      setSourceControl((current) => ({ ...current, entries: current.entries.map((entry) => selected.has(entry.path)
        ? { ...entry, staged: action === "stage", unstaged: action === "unstage" }
        : entry) }));
      const pending = (async () => {
        try {
          let result;
          for (const filePath of paths) {
            result = await window.goferDesktop?.workspace?.gitFileAction?.(rootPath, filePath, action);
            if (!result) throw new Error("Restart the desktop app to enable Git actions.");
            if (result.error) throw new Error(result.error);
          }
          if (currentRootRef.current === rootPath) setSourceControl(result);
          return true;
        } catch (cause) {
          if (currentRootRef.current === rootPath) {
            setGitError(cause instanceof Error ? cause.message : String(cause));
            // A bulk operation may have partially succeeded. Read the actual index.
            try {
              const actual = await shareInFlight(`git-status:${rootPath}`, () => window.goferDesktop?.workspace?.gitStatus?.(rootPath));
              if (currentRootRef.current === rootPath) setSourceControl(actual ?? { active: false, entries: [] });
            } catch {
              if (currentRootRef.current === rootPath) setSourceControl({ active: false, entries: [] });
            }
          }
          return false;
        }
      })();
      pending.rootPath = rootPath;
      pendingStagingRef.current = pending;
      await pending;
      if (pendingStagingRef.current === pending) pendingStagingRef.current = null;
      setGitStaging(false);
      return;
    }
    if (bulk && action === "revert-staged" && sourceControl.entries.some((entry) => value.includes(entry.path) && entry.unstaged)) {
      setGitError("Some staged files also have unstaged edits. Unstage those files first, or discard their unstaged edits before discarding all staged changes.");
      return;
    }
    const discardTarget = bulk ? `all ${value.length} ${action === "revert-staged" ? "staged" : "unstaged"} files` : value;
    if (action.startsWith("revert") && !window.confirm(`Discard ${action === "revert-staged" ? "staged" : "unstaged"} changes to ${discardTarget}? New files will be moved to the trash.`)) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    if (changesWorkingTree) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: true } }));
    setGitError("");
    setGitNotice("");
    try {
      if (pendingStagingRef.current && !await pendingStagingRef.current) return;
      const bridge = window.goferDesktop?.workspace;
      let result;
      if (bulk) {
        for (const filePath of value) {
          result = await bridge?.gitFileAction?.(rootPath, filePath, action);
          if (!result) throw new Error("Restart the desktop app to enable Git actions.");
          if (result.error) throw new Error(result.error);
        }
      } else result = action === "switch"
        ? await bridge?.gitSwitchBranch?.(rootPath, value)
        : (["commit", "push", "pull", "publish", "stash-switch", "stash-apply", "reset-soft", "reset-hard", "detach-commit", "branch-commit", "branch-delete"].includes(action) || /^(merge|rebase)-/.test(action))
          ? await bridge?.gitRepoAction?.(rootPath, action, value)
          : await bridge?.gitFileAction?.(rootPath, value, action);
      if (!result) throw new Error("Restart the desktop app to enable Git actions.");
      if (action === "branch-delete" && result.branchDeleteUnmerged) {
        if (currentRootRef.current !== rootPath) return;
        if (!window.confirm(`Branch ${value} may have unmerged changes. Delete it anyway? Those commits may be lost.`)) return;
        result = await bridge.gitRepoAction(rootPath, "branch-delete-force", value);
        if (!result) throw new Error("Unable to delete branch.");
      }
      if (result.error) { if (result.active) setSourceControl(result); throw new Error(result.error); }
      setBlockedBranch(result.switchBlocked ? result.requestedBranch : "");
      setGitNotice(result.notice || (action === "commit" ? "Committed staged changes." : ""));
      if (result.switchBlocked) return;
      if (action === "commit") setCommitMessage("");
      setSourceControl(result);
      await Promise.all([refreshTree(), loadGitPanels()]);
      onFilesystemChange?.({ type: "git", rootPath });
      return true;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setGitError(message);
      if (action === "switch" && /would be overwritten/i.test(message)) setBlockedBranch(value);
      if (changesWorkingTree || bulk) { await refreshTree(); onFilesystemChange?.({ type: "git", rootPath }); }
    } finally {
      if (changesWorkingTree) window.dispatchEvent(new CustomEvent("gofer:git-working-tree-busy", { detail: { rootPath, busy: false } }));
      setGitBusy(false);
      gitOperationRef.current = false;
    }
  }

  const refreshTree = useCallback(async () => {
    directoriesRef.current = {};
    setDirectories({});
    setExpanded(new Set(rootPath ? [rootPath] : []));
    // Keep the last Git snapshot visible while refreshing or switching worktrees.
    if (!rootPath) {
      setSourceControl({ active: false, entries: [] });
      setSourceControlRoot(null);
      return;
    }
    try {
      await window.goferDesktop?.workspace?.trustProjectRoot?.(rootPath);
    } catch (trustError) {
      if (currentRootRef.current !== rootPath || trustError?.name === "AbortError") return;
      setError(trustError instanceof Error ? trustError.message : String(trustError));
      setGrantRequired(true);
      return;
    }
    if (currentRootRef.current !== rootPath) return;
    await Promise.all([loadDirectory(rootPath), loadSourceControlStatus()]);
  }, [loadDirectory, loadSourceControlStatus, rootPath]);

  const loadGitPanels = useCallback(async ({ includeHistory = true } = {}) => {
    if (!rootPath || gitPanelsLoadingRef.current?.rootPath === rootPath) return;
    const request = { rootPath };
    gitPanelsLoadingRef.current = request;
    if (includeHistory) setGitHistory((current) => ({ ...current, loading: true }));
    if (includeHistory) setWorktrees((current) => ({ ...current, loading: true }));
    try {
      const [historyPayload, worktreePayload] = await Promise.all([
        includeHistory ? window.goferDesktop?.workspace?.gitHistory?.(rootPath) : null,
        window.goferDesktop?.workspace?.gitWorktrees?.(rootPath),
      ]);
      if (gitPanelsLoadingRef.current !== request || currentRootRef.current !== rootPath) return;
      if (historyPayload?.error || worktreePayload?.error) throw new Error(historyPayload?.error || worktreePayload.error);
      if (includeHistory) setGitHistory({ active: Boolean(historyPayload?.active), commits: historyPayload?.commits ?? [], loading: false });
      setWorktrees({ active: Boolean(worktreePayload?.active), items: worktreePayload?.worktrees ?? [], loading: false });
    } catch (loadError) {
      if (gitPanelsLoadingRef.current !== request || currentRootRef.current !== rootPath || loadError?.name === "AbortError") return;
      setGitHistory((current) => ({ ...current, loading: false }));
      setWorktrees((current) => ({ ...current, loading: false }));
      setGitError(loadError instanceof Error ? loadError.message : "Unable to load Git information");
    } finally {
      if (gitPanelsLoadingRef.current === request) gitPanelsLoadingRef.current = null;
    }
  }, [rootPath]);

  useEffect(() => {
    if (sidebarView !== "source-control") return undefined;
    void loadGitPanels();
    return startPolling(() => loadGitPanels({ includeHistory: false }));
  }, [sidebarView, loadGitPanels]);

  useEffect(() => {
    setSelectedPath(rootPath);
    setExpandedCommits(new Set());
    setCopiedCommitHash("");
    setClipboardEntry(null);
    setContextMenu(null);
    setWorktreeMenu(null);
    setIntegrationSource("");
    setIntegrationRequest(null);
    setGrantRequired(false);
    void refreshTree();
  }, [refreshTree, rootPath]);

  useEffect(() => {
    const ancestorPaths = workspaceAncestorPaths(rootPath, activeFilePath);
    if (!ancestorPaths.length) return undefined;

    const request = revealRequestRef.current + 1;
    revealRequestRef.current = request;
    setSelectedPath(activeFilePath);
    setExpanded((current) => {
      const next = new Set(current);
      for (const directory of ancestorPaths) next.add(directory);
      return next;
    });

    async function revealActiveFile() {
      let index = 0;
      await Promise.all(Array.from({ length: Math.min(4, ancestorPaths.length) }, async () => {
        while (index < ancestorPaths.length && revealRequestRef.current === request) {
          const directory = ancestorPaths[index++];
          if (!directoriesRef.current[directory]) {
            await loadDirectory(directory, { clearError: false });
          }
        }
      }));
    }

    void revealActiveFile();
    return () => {
      if (revealRequestRef.current === request) revealRequestRef.current += 1;
    };
  }, [activeFilePath, loadDirectory, rootPath]);

  useEffect(() => {
    if (!rootPath) return undefined;
    return startPolling(loadSourceControlStatus);
  }, [loadSourceControlStatus, rootPath]);

  useEffect(() => {
    if (!contextMenu) return undefined;
    const close = () => setContextMenu(null);
    const closeOnEscape = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("blur", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("blur", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);

  const rows = useMemo(
    () => visibleTreeRows(rootPath, directories, expanded, query, sourceControl.entries),
    [directories, expanded, query, rootPath, sourceControl.entries],
  );
  const selectedEntry = useMemo(
    () => entryForPath(rootPath, directories, selectedPath)
      ?? rows.find(({ entry }) => samePath(entry.path, selectedPath))?.entry
      ?? null,
    [directories, rootPath, rows, selectedPath],
  );
  const selectedDirectory = selectedEntry?.isDirectory
    ? selectedEntry.path
    : parentWorkspacePath(selectedEntry?.path || selectedPath || rootPath);

  useEffect(() => {
    activeFileRowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activeFilePath, rows]);

  useEffect(() => {
    if (newFileRequest === lastNewFileRequestRef.current) return;
    lastNewFileRequestRef.current = newFileRequest;
    const directory = selectedDirectory || rootPath;
    if (directory) setNameRequest({ directory, kind: "file", mode: "create" });
  }, [newFileRequest, rootPath, selectedDirectory]);

  useEffect(() => {
    if (!recentMenuOpen) return undefined;
    function dismissRecentProjects(event) {
      if (!recentMenuRef.current?.contains(event.target)) setRecentMenuOpen(false);
    }
    function dismissRecentProjectsWithEscape(event) {
      if (event.key === "Escape") setRecentMenuOpen(false);
    }
    window.addEventListener("pointerdown", dismissRecentProjects);
    window.addEventListener("keydown", dismissRecentProjectsWithEscape);
    return () => {
      window.removeEventListener("pointerdown", dismissRecentProjects);
      window.removeEventListener("keydown", dismissRecentProjectsWithEscape);
    };
  }, [recentMenuOpen]);

  async function toggleDirectory(entry) {
    setSelectedPath(entry.path);
    if (expanded.has(entry.path)) {
      setExpanded((current) => withoutSetValue(current, entry.path));
      return;
    }
    setExpanded((current) => withSetValue(current, entry.path));
    if (!directories[entry.path]) {
      await loadDirectory(entry.path);
    }
  }

  function showContextMenu(event, entry = null) {
    event.preventDefault();
    event.stopPropagation();
    setSelectedPath(entry?.path ?? rootPath);
    setContextMenu({
      ...explorerMenuPosition(event.clientX, event.clientY),
      entry,
      directory: entry
        ? entry.isDirectory
          ? entry.path
          : parentWorkspacePath(entry.path)
        : rootPath,
    });
  }

  function requestCreate(kind, directory = selectedDirectory || rootPath) {
    setContextMenu(null);
    if (!directory) return;
    setNameRequest({ directory, kind, mode: "create" });
  }

  function requestRename(entry = selectedEntry) {
    setContextMenu(null);
    if (!entry || samePath(entry.path, rootPath)) return;
    setNameRequest({
      directory: parentWorkspacePath(entry.path),
      entry,
      initialName: entry.name,
      kind: entry.isDirectory ? "folder" : "file",
      mode: "rename",
    });
  }

  async function createChild(kind, directory, name) {
    if (gitBusy) return;
    setError("");
    try {
      const result = kind === "file"
        ? await window.goferDesktop?.workspace?.createFile?.({ directory, name })
        : await window.goferDesktop?.workspace?.createFolder?.({ directory, name });
      setExpanded((current) => withSetValue(current, directory));
      await loadDirectory(directory, { clearError: false });
      if (result?.path) setSelectedPath(result.path);
      setNameRequest(null);
      onFilesystemChange?.({
        isDirectory: kind === "folder",
        kind: "create",
        path: result?.path ?? joinWorkspacePath(directory, name),
      });
      if (kind === "file" && result?.path) onOpenFile?.(result.path);
      void loadSourceControlStatus();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : `Unable to create ${kind}`);
    }
  }

  async function renameEntry(entry, name) {
    if (gitBusy) return;
    setError("");
    try {
      const result = await window.goferDesktop?.workspace?.renamePath?.({
        sourcePath: entry.path,
        name,
      });
      const parent = parentWorkspacePath(entry.path);
      discardDirectoryBranch(setDirectories, entry.path);
      await loadDirectory(parent, { clearError: false });
      const destinationPath = result?.path ?? joinWorkspacePath(parent, name);
      setSelectedPath(destinationPath);
      setNameRequest(null);
      onFilesystemChange?.({
        isDirectory: entry.isDirectory,
        kind: "rename",
        path: destinationPath,
        sourcePath: entry.path,
      });
      void loadSourceControlStatus();
    } catch (renameError) {
      setError(renameError instanceof Error ? renameError.message : "Unable to rename path");
    }
  }

  function copyEntry(entry = selectedEntry) {
    setContextMenu(null);
    if (!entry || samePath(entry.path, rootPath)) return;
    setClipboardEntry(entry);
    treeRef.current?.focus();
  }

  async function pasteEntry(directory = selectedDirectory || rootPath) {
    if (gitBusy) return;
    setContextMenu(null);
    if (!clipboardEntry || !directory) return;
    setError("");
    try {
      const entries = directories[directory] ?? await loadDirectory(directory);
      const name = nextCopyName(clipboardEntry.name, new Set(entries.map((entry) => entry.name)));
      const destinationPath = joinWorkspacePath(directory, name);
      await window.goferDesktop?.workspace?.copyPath?.({
        sourcePath: clipboardEntry.path,
        destinationPath,
      });
      setExpanded((current) => withSetValue(current, directory));
      await loadDirectory(directory, { clearError: false });
      setSelectedPath(destinationPath);
      onFilesystemChange?.({
        isDirectory: clipboardEntry.isDirectory,
        kind: "copy",
        path: destinationPath,
        sourcePath: clipboardEntry.path,
      });
      void loadSourceControlStatus();
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to paste path");
    }
  }

  async function deleteEntry(entry = selectedEntry) {
    if (gitBusy) return;
    setContextMenu(null);
    if (!entry || samePath(entry.path, rootPath)) return;
    const kind = entry.isDirectory ? "folder" : "file";
    if (!window.confirm(`Move ${entry.name} to the trash? This ${kind} can be restored from the operating system trash.`)) return;
    setError("");
    try {
      await window.goferDesktop?.workspace?.deletePath?.(entry.path);
      const parent = parentWorkspacePath(entry.path);
      discardDirectoryBranch(setDirectories, entry.path);
      await loadDirectory(parent, { clearError: false });
      setSelectedPath(parent);
      onFilesystemChange?.({
        isDirectory: entry.isDirectory,
        kind: "delete",
        path: entry.path,
      });
      void loadSourceControlStatus();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete path");
    }
  }

  async function copyPath(entry = selectedEntry) {
    setContextMenu(null);
    const path = entry?.path ?? rootPath;
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      setCopiedPath(path);
      window.setTimeout(() => setCopiedPath(""), 1400);
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy path");
    }
  }

  async function copyCommitHash(commit) {
    try {
      await navigator.clipboard.writeText(commit.hash.slice(0, 8));
      setCopiedCommitHash(commit.hash);
      window.setTimeout(() => setCopiedCommitHash(""), 1400);
    } catch (copyError) {
      setError(copyError instanceof Error ? copyError.message : "Unable to copy commit ID");
    }
  }

  async function openInFileExplorer(entry = selectedEntry) {
    setContextMenu(null);
    const target = entry?.path ?? rootPath;
    if (!target) return;
    try {
      if (entry && !entry.isDirectory) {
        await window.goferDesktop?.workspace?.revealPath?.(target);
      } else {
        await window.goferDesktop?.workspace?.openPath?.(target);
      }
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Unable to open file explorer");
    }
  }

  async function authorizeProjectFolder() {
    try {
      const selected = await window.goferDesktop?.workspace?.selectPath?.({
        currentPath: rootPath,
        directoryOnly: true,
      });
      if (!selected) return;
      if (normalizeWorkspacePath(selected) !== normalizeWorkspacePath(rootPath)) {
        setError(`Choose the registered project folder: ${rootPath}`);
        return;
      }
      setGrantRequired(false);
      await refreshTree();
    } catch (grantError) {
      setError(grantError instanceof Error ? grantError.message : "Unable to authorize project folder");
    }
  }

  function openBranchMenu(event, branch) {
    event.preventDefault();
    event.stopPropagation();
    if (gitBusy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setWorktreeMenu({ source: branch, branchMenu: true, x: event.clientX || rect.left, y: event.clientY || rect.bottom, trigger: event.currentTarget });
  }

  async function deleteBranch(branch) {
    if (gitBusy || branch === sourceControl.branch || worktrees.items.some(item => item.branch === branch)) return;
    await changeSourceControl("branch-delete", branch);
  }

  async function createBranchWorktree(branch) {
    if (gitBusy || gitOperationRef.current) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    setGitError("");
    try {
      const bridge = window.goferDesktop?.workspace;
      if (!bridge?.selectPath || !bridge?.addWorktree) throw new Error("Restart the desktop app to enable worktree creation.");
      const targetPath = await bridge.selectPath({ currentPath: parentWorkspacePath(rootPath), directoryOnly: true });
      if (!targetPath || currentRootRef.current !== rootPath) return;
      const payload = await bridge.addWorktree({ projectRoot: rootPath, branch, createBranch: false, targetPath });
      if (currentRootRef.current !== rootPath) return;
      if (!payload || payload.error) throw new Error(payload?.error || "Unable to create worktree.");
      setWorktrees({ active: true, items: payload.worktrees ?? [], loading: false });
      setGitNotice(`Created worktree for ${branch}.`);
      onSelectProject?.(payload.createdPath || targetPath, { mainProjectRoot: mainWorktreePath(payload.worktrees, rootPath) });
    } catch (cause) {
      if (currentRootRef.current === rootPath) setGitError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      gitOperationRef.current = false;
      setGitBusy(false);
    }
  }

  async function chooseWorktreeFolder() {
    const selected = await window.goferDesktop?.workspace?.selectPath?.({
      currentPath: parentWorkspacePath(rootPath),
      directoryOnly: true,
    });
    if (selected) setWorktreeFolder(selected);
  }

  async function createWorktree(event) {
    event.preventDefault();
    if (gitBusy) return;
    setError("");
    try {
      const payload = await window.goferDesktop?.workspace?.addWorktree?.({
        branch: worktreeBranch,
        createBranch: worktreeCreateBranch,
        startPoint: worktreeStartPoint || undefined,
        projectRoot: rootPath,
        targetPath: worktreeFolder,
      });
      setWorktrees({ active: true, items: payload?.worktrees ?? [], loading: false });
      setWorktreeFormOpen(false);
      setWorktreeStartPoint("");
      setWorktreeBranch("");
      setWorktreeFolder("");
      onSelectProject?.(payload?.createdPath || worktreeFolder, {
        mainProjectRoot: mainWorktreePath(payload?.worktrees, rootPath),
      });
    } catch (worktreeError) {
      setError(worktreeError instanceof Error ? worktreeError.message : "Unable to add worktree");
    }
  }

  async function removeWorktree(worktree) {
    if (gitBusy || gitOperationRef.current) return;
    gitOperationRef.current = true;
    setGitBusy(true);
    setWorktreeRemovalError("");
    try {
      const remove = window.goferDesktop?.workspace?.removeWorktree;
      if (!remove) throw new Error("Worktree removal is only available in the desktop app.");
      const payload = await remove({ projectRoot: rootPath, targetPath: worktree.path, force: worktree.requiresForce === true });
      if (currentRootRef.current !== rootPath) return;
      if (payload?.requiresForce) {
        setWorktreeRemoval({ ...worktree, requiresForce: true });
        return;
      }
      setWorktrees({ active: true, items: payload?.worktrees ?? [], loading: false });
      setWorktreeRemoval(null);
      onRemoveRecentProject?.(worktree.path);
    } catch (worktreeError) {
      if (currentRootRef.current === rootPath) {
        setWorktreeRemovalError(worktreeError instanceof Error ? worktreeError.message : "Unable to remove worktree");
      }
    } finally {
      gitOperationRef.current = false;
      setGitBusy(false);
    }
  }

  function handleTreeKeyDown(event) {
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === "c") {
      event.preventDefault();
      copyEntry();
    } else if (modifier && event.key.toLowerCase() === "v") {
      event.preventDefault();
      void pasteEntry();
    } else if (event.key === "F2") {
      event.preventDefault();
      requestRename();
    } else if (event.key === "Delete") {
      event.preventDefault();
      void deleteEntry();
    } else if (event.key === "Enter" && selectedEntry?.isDirectory) {
      event.preventDefault();
      void toggleDirectory(selectedEntry);
    } else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      event.preventDefault();
      const bounds = treeRef.current?.getBoundingClientRect?.() ?? { left: 8, top: 8 };
      setContextMenu({
        ...explorerMenuPosition(bounds.left + 36, bounds.top + 36),
        entry: samePath(selectedEntry?.path, rootPath) ? null : selectedEntry,
        directory: selectedDirectory || rootPath,
      });
    }
  }

  function handleExplorerKeyDown(event) {
    const action = explorerShortcutAction(event, { activeFilePath, settings });
    if (!action) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "new") {
      requestCreate("file");
      return;
    }
    onCloseActiveFile?.(activeFilePath);
  }

  const rootLoading = loadingPaths.has(rootPath);
  return (
    <div
      className={`flex h-full min-h-0 min-w-0 ${paneVisible ? "" : "[&>[role=tabpanel]]:hidden [&>section]:hidden"}`}
      aria-label="Project sidebar"
      onKeyDownCapture={handleExplorerKeyDown}
    >
      <div role="tablist" aria-label="Project sidebar views" aria-orientation="vertical" className="flex w-10 shrink-0 flex-col border-r border-line">
        {[
          ...(workflowsContent !== undefined ? [{ id: "workflows", label: "Workflows", icon: Waypoints }] : []),
          { id: "files", label: "File explorer", icon: Folder },
          { id: "search", label: "Search", icon: Search },
          { id: "source-control", label: "Source control", icon: GitBranch },
          { id: "swarms", label: "Swarms", icon: RatSwarmIcon },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            id={`sidebar-tab-${id}`}
            role="tab"
            type="button"
            aria-label={label}
            title={label}
            aria-selected={sidebarView === id}
            aria-controls={`sidebar-panel-${id}`}
            tabIndex={sidebarView === id ? 0 : -1}
            className={`grid h-10 w-full shrink-0 place-items-center border-l-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand ${sidebarView === id ? "border-brand text-ink" : "border-transparent text-muted hover:bg-slate-100 hover:text-ink"}`}
            onClick={() => { setSidebarView(id); if (id === "search") setSearchFocusRequest((value) => value + 1); setContextMenu(null); setRecentMenuOpen(false); }}
            onKeyDown={(event) => {
              if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const views = [...(workflowsContent !== undefined ? ["workflows"] : []), "files", "search", "source-control", "swarms"];
              const next = event.key === "Home" ? views[0] : event.key === "End" ? views.at(-1) : views[(views.indexOf(id) + (event.key === "ArrowDown" ? 1 : views.length - 1)) % views.length];
              setSidebarView(next);
              setContextMenu(null);
              setRecentMenuOpen(false);
              event.currentTarget.parentElement.querySelector(`#sidebar-tab-${next}`)?.focus();
            }}
          ><Icon aria-hidden="true" size={19} strokeWidth={1.5} /></button>
        ))}
      </div>
      {workflowsContent !== undefined ? (
        <div
          id="sidebar-panel-workflows"
          role="tabpanel"
          aria-labelledby="sidebar-tab-workflows"
          hidden={sidebarView !== "workflows"}
          className={`min-h-0 min-w-0 flex-1 flex-col ${sidebarView === "workflows" ? "flex" : "hidden"}`}
        >
          {workflowsHeader ? <div className="shrink-0 px-2 pb-2">{workflowsHeader}</div> : null}
          <div className="min-h-0 flex-1 overflow-y-auto px-2">{workflowsContent}</div>
        </div>
      ) : null}
      <SwarmSidebar key={`swarms:${rootPath}`} rootPath={rootPath} active={sidebarView === "swarms"} selectedId={selectedSwarmId} onSelect={onOpenSwarm} />
      <ProjectSearch key={rootPath} rootPath={rootPath} active={sidebarView === "search"} focusRequest={searchFocusRequest} onOpenFile={onOpenFile} disabled={gitBusy} onBusy={setGitBusy} onReplace={() => { void refreshTree(); onFilesystemChange?.({ type: "git", rootPath }); }} />
      <div id="sidebar-panel-files" role="tabpanel" aria-labelledby="sidebar-tab-files" hidden={sidebarView !== "files"} className={`min-h-0 min-w-0 flex-1 flex-col ${sidebarView === "files" ? "flex" : "hidden"}`}>
      <div className="flex h-7 items-center justify-between px-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Explorer</span>
        <div className="flex items-center">
          <button
            aria-label="New file"
            className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
            title="New file"
            type="button"
            onClick={() => requestCreate("file")}
          ><FilePlus2 size={13} /></button>
          <button
            aria-label="New folder"
            className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
            title="New folder"
            type="button"
            onClick={() => requestCreate("folder")}
          ><FolderPlus size={13} /></button>
          <button
            aria-label="Refresh files"
            className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
            title="Refresh files"
            type="button"
            onClick={refreshTree}
          ><RefreshCw className={rootLoading ? "animate-spin" : ""} size={13} /></button>
        </div>
      </div>

      {error ? (
        <div className="mx-1.5 mb-1.5 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-[11px] leading-4 text-red-700" role="alert">
          <div className="flex items-start gap-1.5">
            <span className="min-w-0 flex-1 break-words">{error}</span>
            <button aria-label="Dismiss file explorer error" className="shrink-0" type="button" onClick={() => setError("")}><X size={12} /></button>
          </div>
          {grantRequired ? (
            <button className="mt-1 font-semibold underline underline-offset-2" type="button" onClick={authorizeProjectFolder}>Grant project access</button>
          ) : null}
        </div>
      ) : null}

      <div
        ref={treeRef}
        aria-label="Project files"
        className="flex min-h-0 flex-1 flex-col outline-none"
        role="tree"
        tabIndex={0}
        onContextMenu={(event) => showContextMenu(event)}
        onKeyDown={handleTreeKeyDown}
      >
        <div ref={recentMenuRef} className="relative z-10 shrink-0 bg-white">
          <button
            aria-expanded={hideProjectSelector ? undefined : recentMenuOpen}
            aria-haspopup={hideProjectSelector ? undefined : "menu"}
            aria-selected={selectedPath === rootPath}
            className={`flex h-7 w-full items-center gap-1.5 rounded-md px-1.5 text-left text-xs font-semibold ${selectedPath === rootPath ? "bg-indigo-100 text-indigo-700" : "text-ink hover:bg-slate-100"}`}
            role="treeitem"
            title={hideProjectSelector ? rootPath : `${rootPath}\nChoose a recent project`}
            type="button"
            onClick={() => {
              setSelectedPath(rootPath);
              if (!hideProjectSelector) setRecentMenuOpen((current) => !current);
            }}
            onContextMenu={(event) => showContextMenu(event)}
          >
            <ChevronDown className={`shrink-0 text-muted transition ${recentMenuOpen ? "" : "-rotate-90"}`} size={12} />
            <FolderOpen className="shrink-0 text-muted" size={13} />
            <span className="min-w-0 flex-1 truncate">{workflow?.projectName || workspaceBasename(rootPath) || "Project"}</span>
            <SourceControlDecoration
              directory
              path={rootPath}
              projectRoot={rootPath}
              statuses={sourceControl.entries}
            />
          </button>
          {!hideProjectSelector && recentMenuOpen ? (
            <div
              aria-label="Recent projects"
              className="absolute left-0 top-8 z-50 w-full min-w-56 rounded-lg border border-line bg-white p-1 shadow-panel"
              role="menu"
            >
              <p className="px-2 py-1 text-[10px] font-semibold text-muted">Recent projects</p>
              {recentProjects.length ? recentProjects.map((project) => (
                <div
                  key={project.root}
                  className={`group flex h-8 items-center rounded-md hover:bg-slate-50 focus-within:bg-slate-50 ${samePath(project.root, rootPath) ? "bg-indigo-50 font-semibold text-indigo-700" : "text-ink"}`}
                  role="none"
                >
                  <button
                    className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-left text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-indigo-600"
                    role="menuitem"
                    title={project.root}
                    type="button"
                    onClick={() => {
                      setRecentMenuOpen(false);
                      onSelectProject?.(project.root);
                    }}
                  >
                    <FolderOpen className="shrink-0 text-muted" size={13} />
                    <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    {samePath(project.root, rootPath) ? <Check className="shrink-0 group-hover:hidden group-focus-within:hidden" size={12} /> : null}
                  </button>
                  <button
                    aria-label={`Remove ${project.name} from recent projects`}
                    className="mr-1 grid h-6 w-6 shrink-0 place-items-center rounded text-muted opacity-0 transition hover:bg-slate-200 hover:text-ink focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 group-hover:opacity-100 group-focus-within:opacity-100 dark:hover:bg-white/10"
                    role="menuitem"
                    title="Remove from recent projects"
                    type="button"
                    onClick={() => onRemoveRecentProject?.(project.root)}
                  >
                    <X aria-hidden="true" size={13} />
                  </button>
                </div>
              )) : (
                <p className="px-2 py-2 text-xs text-muted">No recent projects</p>
              )}
            </div>
          ) : null}
        </div>

        <div aria-label="Project contents" className="min-h-0 flex-1 overflow-y-auto" role="group">
          {rootLoading && !directories[rootPath] ? (
            <div className="flex h-14 items-center justify-center gap-2 text-xs text-muted"><Loader2 className="animate-spin" size={13} />Loading files</div>
          ) : null}
          {!rootLoading && directories[rootPath]
            && directoryEntriesWithGitChanges(rootPath, rootPath, directories[rootPath], sourceControl.entries).length === 0 ? (
            <p className="px-6 py-3 text-xs text-muted">This project folder is empty.</p>
          ) : null}
          <div className="ml-2 border-l border-line pl-1">
            {rows.map(({ depth, entry }) => {
              const isExpanded = entry.isDirectory && expanded.has(entry.path);
              const isLoading = loadingPaths.has(entry.path);
              const selected = normalizeWorkspacePath(selectedPath)
                === normalizeWorkspacePath(entry.path);
              const active = normalizeWorkspacePath(activeFilePath)
                === normalizeWorkspacePath(entry.path);
              return (
                <button
                  key={entry.path}
                  ref={active ? activeFileRowRef : undefined}
                  aria-current={active ? "page" : undefined}
                  aria-expanded={entry.isDirectory ? isExpanded : undefined}
                  aria-selected={selected}
                  className={`flex h-7 w-full items-center gap-1.5 rounded-md pr-1.5 text-left text-xs ${selected ? "bg-indigo-100 font-medium text-indigo-700" : "text-ink hover:bg-slate-100"}`}
                  role="treeitem"
                  style={{ paddingLeft: `${6 + depth * 12}px` }}
                  title={entry.path}
                  type="button"
                  onClick={() => {
                    if (entry.isDirectory) {
                      void toggleDirectory(entry);
                      return;
                    }
                    setSelectedPath(entry.path);
                    onOpenFile?.(entry.path, { preview: true });
                  }}
                  onDoubleClick={() => !entry.isDirectory && onOpenFile?.(entry.path)}
                  onContextMenu={(event) => showContextMenu(event, entry)}
                >
                  {entry.isDirectory ? (
                    isLoading ? <Loader2 className="shrink-0 animate-spin text-muted" size={12} /> : <ChevronDown className={`shrink-0 text-muted transition ${isExpanded ? "" : "-rotate-90"}`} size={12} />
                  ) : <span className="w-3 shrink-0" />}
                  <ExplorerIcon entry={entry} expanded={isExpanded} />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  <SourceControlDecoration
                    directory={entry.isDirectory}
                    path={entry.path}
                    projectRoot={rootPath}
                    statuses={sourceControl.entries}
                  />
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {clipboardEntry ? (
        <div className="mx-1.5 mt-2 flex items-center gap-1.5 rounded-md bg-slate-100 px-2 py-1.5 text-[10px] text-muted">
          <Copy size={11} />
          <span className="min-w-0 flex-1 truncate" title={clipboardEntry.path}>Copied {clipboardEntry.name}</span>
          <button aria-label="Clear copied file" type="button" onClick={() => setClipboardEntry(null)}><X size={11} /></button>
        </div>
      ) : null}

      </div>
      <section id="sidebar-panel-source-control" role="tabpanel" aria-labelledby="sidebar-tab-source-control" hidden={sidebarView !== "source-control"} className={`scm-panel min-h-0 min-w-0 flex-1 flex-col bg-white text-ink ${sidebarView === "source-control" ? "flex" : "hidden"}`}>
        <div className="flex h-8 shrink-0 items-center justify-between px-3">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Source control</span>
          <div className="flex shrink-0 items-center gap-1">
            {sourceControl.active && (sourceControl.ahead != null || sourceControl.remotes?.length) ? <>
              <button aria-label="Pull" title="Pull" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy || sourceControl.ahead == null} type="button" onClick={() => void changeSourceControl("pull")}><ArrowDown aria-hidden="true" size={13} /></button>
              {sourceControl.ahead != null ? <button aria-label="Push" title="Push" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy} type="button" onClick={() => void changeSourceControl("push")}><ArrowUp aria-hidden="true" size={13} /></button> : null}
            </> : null}
          <button aria-label="Refresh source control" className="grid h-7 w-7 place-items-center rounded text-muted hover:bg-slate-100" disabled={gitBusy || gitHistory.loading || worktrees.loading} type="button" onClick={() => { void loadSourceControlStatus(); void loadGitPanels(); }}><RefreshCw size={13} /></button>
          </div>
        </div>
          {sourceControl.active ? <div className="shrink-0 space-y-2 border-b border-line px-3 pb-3">
            <p className="truncate text-[11px] text-muted" title={rootPath}>{workspaceBasename(rootPath)}</p>
                <p aria-label="Current branch" className="flex items-center gap-2 px-1 text-xs">
                  <GitBranch aria-hidden="true" size={13} />
                  <span className="truncate">{sourceControl.branch || "Detached HEAD"}</span>
                </p>
                <p className="px-1 text-[11px] text-muted">{sourceControl.ahead == null ? "Local branch" : `${sourceControl.ahead} ahead · ${sourceControl.behind} behind`}</p>
                {sourceControl.ahead == null && sourceControl.remotes?.length ? <div className="flex flex-wrap gap-1 px-1">
                    <select aria-label="Publish remote" className="min-w-0 flex-1 rounded border border-line bg-white text-[11px]" value={remote || sourceControl.remotes?.[0] || ""} onChange={(event) => setRemote(event.target.value)}>
                      {!sourceControl.remotes?.length ? <option value="">No remote configured</option> : sourceControl.remotes.map((name) => <option key={name}>{name}</option>)}
                    </select>
                    <button type="button" className="rounded border border-line px-2 py-1 text-[11px] disabled:opacity-40" disabled={gitBusy || !sourceControl.remotes?.length} onClick={() => void changeSourceControl("publish", remote || sourceControl.remotes[0])}>Publish</button>
                </div> : null}

          </div> : null}
          <div role="tablist" aria-label="Source control views" className="flex shrink-0 gap-3 border-b border-line px-3">
            {["changes", "history", "branches"].map((tab, index, tabs) => <button key={tab} id={`scm-tab-${tab}`} role="tab" aria-selected={sourceTab === tab} aria-controls="scm-content" tabIndex={sourceTab === tab ? 0 : -1} className={`min-w-0 border-b-2 py-2.5 text-[11px] ${sourceTab === tab ? "border-brand font-semibold text-ink" : "border-transparent text-muted hover:text-ink"}`} type="button" onClick={() => setSourceTab(tab)} onKeyDown={(event) => {
              const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
              if (next < 0) return;
              event.preventDefault(); setSourceTab(tabs[next]); document.getElementById(`scm-tab-${tabs[next]}`)?.focus();
            }}>{tab === "changes" ? "Changes" : tab === "history" ? "History" : "Branches"}</button>)}
          </div>
          <div id="scm-content" role="tabpanel" aria-labelledby={`scm-tab-${sourceTab}`} tabIndex={0} className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
            {gitError || blockedBranch ? <div role="alert" className="scm-notice mb-3 rounded p-3 text-xs">
              <div className="flex items-start gap-2"><strong className="min-w-0 flex-1">{blockedBranch ? "Changes would be overwritten" : sourceControlErrorSummary(gitError)}</strong><button aria-label="Dismiss Git error" type="button" onClick={() => { setGitError(""); setBlockedBranch(""); }}><X size={13} /></button></div>
              {blockedBranch ? <><p className="my-2 break-words">Commit your changes or stash them before switching to {blockedBranch}.</p><div className="flex flex-wrap gap-2"><button className="rounded border border-current px-2 py-1" disabled={gitBusy} type="button" onClick={() => void changeSourceControl("stash-switch", blockedBranch)}>Stash &amp; switch</button><button type="button" disabled={gitBusy} onClick={() => { setBlockedBranch(""); setGitError(""); setGitNotice(""); }}>Stay on {sourceControl.branch || "this branch"}</button></div></> : null}
              {gitError ? <details className="mt-2"><summary className="cursor-pointer">Technical details</summary><pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[11px]">{gitError}</pre></details> : null}
            </div> : null}
            {gitNotice && !blockedBranch ? <p role="status" className="mb-3 px-1 text-xs text-muted">{gitNotice}</p> : null}
            {sourceControl.operation || sourceControl.entries.some(entry => entry.status === "!") ? <section aria-label="Merge conflicts" className="mb-4 space-y-2 rounded border border-line p-3 text-xs">
              <strong>{sourceControl.operation ? `${sourceControl.operation === "rebase" ? "Rebase" : "Merge"} paused` : "Unresolved conflicts"}</strong>
              <p>The ! entry in Staged is an unresolved index conflict. Rem can resolve and stage the fixes, commit them, and continue until the rebase or merge is complete.</p>
              <div className="flex flex-wrap gap-2"><button className="rounded border border-line px-2 py-1.5" type="button" onClick={() => window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: { mode: "conflicts", projectRoot: rootPath, text: sourceControl.entries.filter(entry => entry.status === "!").map(entry => entry.path).join("\n") } }))}>Resolve conflicts with Rem</button>
              {sourceControl.operation ? <><button className="rounded border border-line px-2 py-1.5" type="button" disabled={gitBusy || sourceControl.entries.some(entry => entry.status === "!")} onClick={() => void changeSourceControl(`${sourceControl.operation}-continue`)}>Continue {sourceControl.operation}</button><button className="rounded border border-line px-2 py-1.5" type="button" disabled={gitBusy} onClick={() => { if (window.confirm(`Abort this ${sourceControl.operation}? Conflict resolution edits will be discarded.`)) void changeSourceControl(`${sourceControl.operation}-abort`); }}>Abort {sourceControl.operation}</button></> : null}</div>
            </section> : null}
            {!sourceControl.active ? <p className="px-2 py-6 text-xs text-muted">This project is not a Git repository.</p> : <>
              {sourceTab === "changes" ? sourceControl.entries.length ? <>
                {[["staged", "Staged"], ["unstaged", "Unstaged"]].map(([group, title]) => {
                  const entries = sourceControl.entries.filter((entry) => entry[group] || (group === "staged" && entry.status === "!"));
                  if (!entries.length) return null;
                  return <details key={group} open aria-label={title} className="mb-5">
                    <summary className="cursor-pointer px-1 py-1 text-[11px] font-semibold text-ink">
                      <span className="inline-flex w-[calc(100%-1rem)] items-center gap-1 align-middle">
                        <span className="min-w-0 flex-1">{title} · {entries.length}</span>
                        <button aria-label={group === "staged" ? "Unstage all changes" : "Stage all changes"} title={group === "staged" ? "Unstage all changes" : "Stage all changes"} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline disabled:opacity-40" disabled={gitBusy || !entries.some(entry => group !== "staged" || entry.status !== "!")} type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void changeSourceControl(group === "staged" ? "unstage" : "stage", entries.filter(entry => group !== "staged" || entry.status !== "!").map((entry) => entry.path)); }}>{group === "staged" ? <Minus size={12} /> : <Plus size={12} />}</button>
                        <button aria-label={`Discard all ${group} changes`} title={`Discard all ${group} changes`} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-red-700 focus-visible:outline disabled:opacity-40" disabled={gitBusy || !entries.length || entries.some(entry => entry.status === "!")} type="button" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void changeSourceControl(group === "staged" ? "revert-staged" : "revert", entries.filter(entry => group !== "staged" || entry.status !== "!").map((entry) => entry.path)); }}><Trash2 size={12} /></button>
                      </span>
                    </summary>
                    {entries.map((entry) => <div key={entry.path} className={`scm-file flex min-h-12 items-center gap-1 rounded px-2 ${samePath(activeFilePath, joinWorkspacePath(rootPath, entry.path)) ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
                      <span title={entry.status === "!" ? "Unresolved index conflict. Stage the working file to resolve." : undefined} className="w-3 shrink-0 text-[10px] text-muted">{entry.status}</span>
                      <button className="min-w-0 flex-1 truncate text-left text-[11px] text-ink" title={entry.path} type="button" onClick={() => onOpenFile?.(joinWorkspacePath(rootPath, entry.path), { diff: true, gitGroup: entry.status === "!" ? "unstaged" : group })}><span className="block truncate text-xs font-medium">{workspaceBasename(entry.path)}</span><span className="block truncate text-[11px] text-muted">{entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : ""}</span></button>
                      <button aria-label={`${group === "staged" ? "Unstage" : "Stage"} ${entry.path}`} title={group === "staged" ? "Unstage change" : "Stage change"} className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline disabled:opacity-40" disabled={gitBusy || (group === "staged" && entry.status === "!")} type="button" onClick={() => void changeSourceControl(group === "staged" ? "unstage" : "stage", entry.path)}>{group === "staged" ? <Minus size={12} /> : <Plus size={12} />}</button>
                      <button aria-label={`Discard ${group} changes to ${entry.path}`} title="Discard changes" className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-red-700 focus-visible:outline disabled:opacity-40" disabled={gitBusy || entry.status === "!"} type="button" onClick={() => void changeSourceControl(group === "staged" ? "revert-staged" : "revert", entry.path)}><Trash2 size={12} /></button>
                    </div>)}
                  </details>;
                })}
              </> : <div className="px-3 py-10 text-center"><Check className="mx-auto mb-3 text-muted" size={20} /><strong className="text-sm font-semibold">Working tree clean</strong><p className="mt-1 text-xs text-muted">No uncommitted changes.</p></div> : null}
              {sourceTab === "branches" ? <>
            <div className="flex h-7 items-center justify-between">
              <span className="text-[10px] font-semibold text-ink">Worktrees</span>
              <button aria-label="Add worktree" className="grid h-6 w-6 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink" type="button" onClick={() => { setWorktreeStartPoint(""); setWorktreeFormOpen((current) => !current); }}><FolderPlus size={12} /></button>
            </div>
            {worktreeFormOpen ? (
              <form className="mb-2 space-y-1.5 rounded-md bg-slate-50 p-2" onSubmit={createWorktree}>
                {worktreeStartPoint ? <p className="text-xs text-muted">Starting at {worktreeStartPoint.slice(0, 8)}</p> : null}
                <input aria-label="Worktree branch" className="h-7 w-full rounded border border-line bg-white px-2 text-[11px] outline-none focus:border-indigo-500" placeholder="Branch name" required value={worktreeBranch} onChange={(event) => setWorktreeBranch(event.target.value)} />
                <button className="flex h-7 w-full items-center gap-1.5 rounded border border-line bg-white px-2 text-left text-[11px] text-muted hover:text-ink" type="button" onClick={chooseWorktreeFolder}><FolderOpen size={12} /><span className="min-w-0 flex-1 truncate">{worktreeFolder || "Choose an empty folder"}</span></button>
                <label className="flex items-center gap-1.5 text-[10px] text-muted"><input checked={worktreeCreateBranch} disabled={Boolean(worktreeStartPoint)} type="checkbox" onChange={(event) => setWorktreeCreateBranch(event.target.checked)} />Create a new branch</label>
                <button className="h-7 w-full rounded bg-brand text-[11px] font-semibold text-white disabled:opacity-40" disabled={!worktreeBranch.trim() || !worktreeFolder} type="submit">Add worktree</button>
              </form>
            ) : null}
            {worktrees.loading && !worktrees.items.length ? <p className="py-2 text-[11px] text-muted">Loading worktrees...</p> : null}
            {worktrees.items.filter((worktree) => !worktree.missing && !worktree.prunable).map((worktree) => {
              const activeWorktree = normalizeWorkspacePath(worktree.path) === normalizeWorkspacePath(rootPath);
              return (
                <div
                  key={worktree.path}
                  className="group relative flex min-h-8 items-center gap-1 rounded px-1.5 hover:bg-slate-50"
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (gitBusy || !worktree.branch) return;
                    setWorktreeMenu({ source: worktree.branch, x: event.clientX, y: event.clientY, trigger: event.currentTarget.querySelector("button") });
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                    event.preventDefault();
                    event.stopPropagation();
                    if (gitBusy || !worktree.branch) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    setWorktreeMenu({ source: worktree.branch, x: rect.left, y: rect.bottom, trigger: event.target });
                  }}
                >
                  {activeWorktree ? <span aria-hidden="true" className="absolute inset-y-1 left-0 w-px bg-brand" /> : null}
                  <GitBranch className="shrink-0 text-muted" size={12} />
                  <button
                    aria-current={activeWorktree ? "page" : undefined}
                    className="min-w-0 flex-1 py-1 text-left"
                    title={worktree.path}
                    type="button"
                    onClick={() => onSelectProject?.(worktree.path, {
                      mainProjectRoot: mainWorktreePath(worktrees.items, rootPath),
                    })}
                  >
                    <span className="block truncate text-[11px] font-medium text-ink">{worktree.branch || "Detached HEAD"}</span>
                    <span className="block truncate text-[11px] text-muted">
                      {worktree.path}
                    </span>
                  </button>
                  {worktree.branch ? <button aria-label={`Integrate ${worktree.branch} worktree`} title="Merge or rebase worktree" className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-40" disabled={gitBusy} type="button" onClick={() => { setIntegrationRequest(null); setIntegrationSource(worktree.branch); }}><GitMerge aria-hidden="true" size={13} /></button> : null}
                  {activeWorktree ? <span className="text-[10px] text-muted">Current</span> : null}
                  {!activeWorktree ? <button aria-label={`Remove ${worktree.branch || "detached"} worktree`} className="grid h-6 w-6 place-items-center rounded text-muted opacity-0 hover:bg-red-50 hover:text-red-700 focus:opacity-100 group-hover:opacity-100" type="button" disabled={gitBusy} onClick={() => { setWorktreeRemovalError(""); setWorktreeRemoval(worktree); }}><Trash2 size={11} /></button> : null}
                </div>
              );
            })}

            <section aria-label="Branches" className="mt-4 border-t border-line pt-3">
              <h3 className="mb-1 text-[10px] font-semibold text-ink">Branches</h3>
              {!sourceControl.branches?.length ? <p className="py-2 text-[11px] text-muted">No local branches yet.</p> : null}
              {(sourceControl.branches || []).map(branch => {
                const current = branch === sourceControl.branch;
                const checkedOut = current || worktrees.items.some(item => item.branch === branch);
                return <div key={branch} aria-label={`Branch ${branch}`} tabIndex={0} className="relative flex min-h-8 items-center gap-1.5 rounded px-1.5 text-[11px] hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
                  onContextMenu={event => openBranchMenu(event, branch)}
                  onKeyDown={event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openBranchMenu(event, branch); }}>
                  <GitBranch aria-hidden="true" className="shrink-0 text-muted" size={12} />
                  {current ? <span aria-hidden="true" className="absolute inset-y-1 left-0 w-px bg-brand" /> : null}
                  <button type="button" aria-label={`Switch to branch ${branch}`} aria-current={current ? "page" : undefined} disabled={gitBusy || worktrees.loading || current} className="min-w-0 flex-1 truncate py-1 text-left text-ink focus-visible:outline disabled:cursor-default" title={branch} onClick={() => {
                    const worktree = worktrees.items.find(item => item.branch === branch && !item.missing && !item.prunable);
                    if (worktree) onSelectProject?.(worktree.path, { mainProjectRoot: mainWorktreePath(worktrees.items, rootPath) });
                    else void changeSourceControl("switch", branch);
                  }}>{branch}</button>
                  {checkedOut ? <span className="shrink-0 text-[10px] text-muted">{current ? "Current" : "Worktree"}</span> : null}
                  <button type="button" aria-label={`Actions for branch ${branch}`} title="Branch actions" disabled={gitBusy} className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline disabled:opacity-40" onClick={event => openBranchMenu(event, branch)}><MoreHorizontal size={13} /></button>
                  <button type="button" aria-label={`Delete branch ${branch}`} title={checkedOut ? "Cannot delete a checked-out branch" : `Delete branch ${branch}`} disabled={gitBusy || worktrees.loading || checkedOut} className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted hover:text-red-700 focus-visible:outline disabled:opacity-40" onClick={() => void deleteBranch(branch)}><Trash2 size={12} /></button>
                </div>;
              })}
            </section>

                <GitIntegrationControls key={rootPath} rootPath={rootPath} sourceControl={sourceControl} worktrees={worktrees.items} source={integrationSource} request={integrationRequest} onSourceChange={setIntegrationSource} onBusy={(busy) => { gitOperationRef.current = busy; setGitBusy(busy); }} disabled={gitBusy} onSelectProject={onSelectProject} onChanged={async (result) => {
                  if (currentRootRef.current !== rootPath) return;
                  if (result.active) setSourceControl(result);
                  setGitNotice(result.notice || "");
                  await refreshTree(); await loadGitPanels();
                  onFilesystemChange?.({ type: "git", rootPath });
                  if (result.destinationRoot && result.destinationRoot !== rootPath) onFilesystemChange?.({ type: "git", rootPath: result.destinationRoot });
                  if (result.conflicts?.length) setSourceTab("changes");
                }} />
              </> : null}
              {sourceTab === "history" ? <>
            <div className="flex h-7 items-center gap-1.5 pb-1 text-[10px] font-semibold text-ink">
              <GitCommitHorizontal size={12} />
              <span className="flex-1">Commit history</span>
              <button
                aria-label="Refresh commit history"
                className="grid h-6 w-6 place-items-center rounded text-muted outline-none hover:bg-slate-100 hover:text-ink focus-visible:bg-slate-100 focus-visible:text-ink disabled:cursor-default disabled:opacity-70"
                disabled={gitHistory.loading || worktrees.loading}
                title="Refresh commit history"
                type="button"
                onClick={() => void loadGitPanels()}
              >
                <RefreshCw className={gitHistory.loading || worktrees.loading ? "animate-spin" : ""} size={12} />
              </button>
            </div>
            {gitHistory.loading && !gitHistory.commits.length ? <p className="py-2 text-[11px] text-muted">Loading history...</p> : null}
            {!gitHistory.loading && !gitHistory.active ? <p className="py-2 text-[11px] text-muted">This project is not a Git repository.</p> : null}
            {!gitHistory.loading && gitHistory.active && !gitHistory.commits.length ? <p className="py-6 text-center text-xs text-muted">No commits yet.</p> : null}
            {gitHistory.commits.map((commit) => {
              const isExpanded = expandedCommits.has(commit.hash);
              const isCopied = copiedCommitHash === commit.hash;
              return (
                <div key={commit.hash} onContextMenu={event => openHistoryMenu(event, commit)} onKeyDown={event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openHistoryMenu(event, commit); }} className={`rounded-md transition-colors ${isExpanded ? "bg-slate-50" : "hover:bg-slate-50"}`}>
                  <div className="flex items-start rounded-md">
                    <button
                      aria-expanded={isExpanded}
                      className="flex min-w-0 flex-1 items-start gap-1.5 rounded-md px-1.5 py-1.5 text-left outline-none focus-visible:bg-slate-100"
                      type="button"
                      onClick={() => setExpandedCommits((current) => isExpanded
                        ? withoutSetValue(current, commit.hash)
                        : withSetValue(current, commit.hash))}
                    >
                      <ChevronDown className={`mt-0.5 shrink-0 text-muted transition-transform ${isExpanded ? "" : "-rotate-90"}`} size={10} />
                      <span className="min-w-0 flex-1">
                        <span className={`block text-[11px] font-medium leading-4 text-ink ${isExpanded ? "whitespace-pre-wrap break-words" : "truncate"}`}>{isExpanded ? (commit.message || commit.subject) : commit.subject}</span>
                        <span className="flex min-w-0 items-center gap-1.5 text-[9px] leading-3 text-muted">
                          <span className="truncate">{commit.author}</span>
                          <span aria-hidden="true">·</span>
                          <span className="shrink-0">{relativeCommitTime(commit.authoredAt)}</span>
                        </span>
                      </span>
                      <span className="mt-0.5 shrink-0 font-mono text-[9px] leading-3 text-muted">{commit.shortHash}</span>
                    </button>
                    <button
                      aria-label={isCopied ? `Copied commit ID ${commit.shortHash}` : `Copy commit ID ${commit.shortHash}`}
                      className="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded text-muted outline-none hover:bg-slate-200 hover:text-ink focus-visible:bg-slate-200 focus-visible:text-ink dark:hover:bg-white/10 dark:focus-visible:bg-white/10"
                      title={isCopied ? "Commit ID copied" : "Copy commit ID"}
                      type="button"
                      onClick={() => void copyCommitHash(commit)}
                    >
                      {isCopied ? <Check size={11} /> : <Copy size={11} />}
                    </button>
                  </div>
                  {isExpanded ? (
                    <div className="ml-5 border-t border-line/80 px-2 pb-2 pt-1.5 text-[10px]">
                      <div className="flex items-center gap-2 font-mono text-[9px]" aria-label={`${commit.insertions ?? 0} insertions, ${commit.deletions ?? 0} deletions${commit.binaryFiles ? `, ${commit.binaryFiles} binary files without line counts` : ""}`}>
                        {!commit.binaryFiles || commit.insertions || commit.deletions ? <><span className="font-medium text-emerald-600">+{commit.insertions ?? 0}</span><span className="font-medium text-red-600">-{commit.deletions ?? 0}</span></> : null}
                        {commit.binaryFiles ? <span className="text-muted">{commit.binaryFiles} binary {commit.binaryFiles === 1 ? "file" : "files"}</span> : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}

              </> : null}
            </>}
          </div>
          {sourceControl.active && sourceTab === "changes" && sourceControl.entries.length > 0 ? (
                <form className="scm-composer shrink-0 space-y-2 border-t border-line bg-slate-50 p-3" onSubmit={(event) => { event.preventDefault(); void changeSourceControl("commit", commitMessage); }}>
                  <label className="block text-xs font-semibold" htmlFor="scm-commit-message">Commit message</label>
                  <div className="relative">
                  <button type="button" aria-label="Generate commit message with Rem" aria-busy={generatingMessage} title={generatingMessage ? "Rem is drafting your commit message" : "Generate a commit message with Rem"} className="rem-action-button absolute right-1 top-1 grid h-8 w-8 place-items-center rounded border border-line bg-canvas text-ink hover:text-brand focus-visible:outline-brand disabled:opacity-40" disabled={gitBusy || generatingMessage || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")} onClick={() => void generateCommitMessage()}><RemActionIcon /><span className="sr-only">{generatingMessage ? "Generating…" : "Generate with Rem"}</span></button>
                  <textarea id="scm-commit-message" aria-label="Commit message" placeholder="Describe your changes…" rows={2} className="scm-commit-message w-full pr-11 resize-none rounded border border-line bg-white px-2 py-1 text-xs focus-visible:outline" value={commitMessage} onChange={(event) => setCommitMessage(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && commitMessage.trim() && !gitOperationBusy && stagedCount) { event.preventDefault(); void changeSourceControl("commit", commitMessage); } }} />
                  </div>
                  <button type="submit" className="h-8 w-full rounded bg-brand text-[11px] font-semibold text-white disabled:opacity-40" disabled={gitStatusPending || gitOperationBusy || !commitMessage.trim() || !stagedCount || sourceControl.entries.some(entry => entry.status === "!")}>{gitOperationBusy ? "Working…" : `Commit ${stagedCount} staged file${stagedCount === 1 ? "" : "s"}`}</button>
                  <p className="text-[11px] text-muted">Only staged files will be committed. Unresolved conflicts block commits.</p>
                </form>

          ) : null}
      </section>

      {worktreeMenu && sidebarView === "source-control" && sourceTab === "branches" ? <WorktreeContextMenu
        {...worktreeMenu}
        operations={worktreeMenu.branchMenu ? [...integrationOperations, ["worktree-branch", "Create worktree from branch", worktrees.loading || worktreeMenu.source === sourceControl.branch || worktrees.items.some(item => item.branch === worktreeMenu.source)], ["branch-delete", "Delete branch", worktrees.loading || worktreeMenu.source === sourceControl.branch || worktrees.items.some(item => item.branch === worktreeMenu.source)]] : integrationOperations}
        disabled={gitBusy}
        onClose={() => setWorktreeMenu(null)}
        onSelect={(kind, target) => {
          if (kind === "branch-delete" || kind === "worktree-branch") {
            const branch = worktreeMenu.source;
            setWorktreeMenu(null);
            if (kind === "branch-delete") void deleteBranch(branch);
            else void createBranchWorktree(branch);
            return;
          }
          setIntegrationSource(worktreeMenu.source);
          setIntegrationRequest({ kind, target });
          setWorktreeMenu(null);
        }}
      /> : null}
      {branchRequest ? <Dialog title="Checkout new branch" onClose={() => { if (!gitBusy) setBranchRequest(null); }} panelClassName="w-full max-w-sm rounded-lg border border-line bg-white p-4 shadow-panel">
        <form className="space-y-3" onSubmit={async event => { event.preventDefault(); if (!branchDraft.trim() || gitBusy) return; if (await changeSourceControl("branch-commit", { hash: branchRequest.hash, branch: branchDraft.trim() })) setBranchRequest(null); }}>
          <h3 className="text-sm font-semibold">Checkout new branch</h3>
          <p className="text-xs text-muted">Start at {branchRequest.hash.slice(0, 8)}</p>
          <label className="block text-xs">Branch name<input autoFocus aria-label="New branch name" className="mt-1 h-9 w-full rounded border border-line bg-canvas px-2 text-sm focus-visible:outline" value={branchDraft} onChange={event => setBranchDraft(event.target.value)} /></label>
          {gitError ? <p role="alert" className="text-xs text-red-700">{gitError}</p> : null}
          <div className="flex justify-end gap-2"><button type="button" disabled={gitBusy} onClick={() => setBranchRequest(null)} className="rounded border border-line px-3 py-2 text-xs">Cancel</button><button type="submit" disabled={gitBusy || !branchDraft.trim()} className="rounded bg-brand px-3 py-2 text-xs text-white disabled:opacity-40">Checkout branch</button></div>
        </form>
      </Dialog> : null}
      {historyMenu && sidebarView === "source-control" && sourceTab === "history" ? <WorktreeContextMenu {...historyMenu} operations={historyOperations} disabled={gitBusy} onClose={() => setHistoryMenu(null)} onSelect={kind => void historyAction(kind)} /> : null}
      {contextMenu ? (
        <ExplorerContextMenu
          canPaste={Boolean(clipboardEntry)}
          entry={contextMenu.entry}
          pathCopied={copiedPath === (contextMenu.entry?.path ?? rootPath)}
          x={contextMenu.x}
          y={contextMenu.y}
          onCopy={() => copyEntry(contextMenu.entry)}
          onCopyPath={() => copyPath(contextMenu.entry)}
          onCreateFile={() => requestCreate("file", contextMenu.directory)}
          onCreateFolder={() => requestCreate("folder", contextMenu.directory)}
          onDelete={() => deleteEntry(contextMenu.entry)}
          onOpen={() => openInFileExplorer(contextMenu.entry)}
          onPaste={() => pasteEntry(contextMenu.directory)}
          onRefresh={() => loadDirectory(contextMenu.directory || rootPath)}
          onRename={() => requestRename(contextMenu.entry)}
        />
      ) : null}

      {worktreeRemoval ? (
        <Dialog
          title={worktreeRemoval.requiresForce ? "Discard changes and remove worktree?" : "Remove worktree?"}
          onClose={() => { if (!gitOperationRef.current) setWorktreeRemoval(null); }}
          panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink"
        >
          <h2 className="text-sm font-semibold">{worktreeRemoval.requiresForce ? "Discard changes and remove worktree?" : "Remove worktree?"}</h2>
          <p className="mt-3 break-all text-xs text-muted">{worktreeRemoval.path}</p>
          <p className="mt-3 text-xs">{worktreeRemoval.requiresForce ? "This worktree has uncommitted changes. Removing it will permanently discard all uncommitted changes and untracked files, and delete its folder. The branch will be kept." : "This removes the worktree and its folder. The branch will be kept."}</p>
          {worktreeRemovalError ? <p role="alert" className="mt-3 break-words text-xs text-red-600">{worktreeRemovalError}</p> : null}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" disabled={gitOperationBusy} onClick={() => setWorktreeRemoval(null)}>Cancel</button>
            <button aria-label={worktreeRemoval.requiresForce ? "Discard changes and remove" : "Confirm worktree removal"} type="button" className="rounded bg-red-600 px-3 py-2 text-xs text-white disabled:opacity-50" disabled={gitBusy} onClick={() => removeWorktree(worktreeRemoval)}>{gitOperationBusy ? "Removing…" : worktreeRemoval.requiresForce ? "Discard changes and remove" : "Remove worktree"}</button>
          </div>
        </Dialog>
      ) : null}

      {nameRequest ? (
        <PathNameDialog
          directory={nameRequest.directory}
          initialName={nameRequest.initialName}
          kind={nameRequest.kind}
          mode={nameRequest.mode}
          onClose={() => setNameRequest(null)}
          onSubmit={(name) => nameRequest.mode === "rename"
            ? renameEntry(nameRequest.entry, name)
            : createChild(nameRequest.kind, nameRequest.directory, name)}
        />
      ) : null}
    </div>
  );
}

export function commitMessageBody(commit = {}) {
  const message = String(commit.message ?? "").trim();
  const subject = String(commit.subject ?? "").trim();
  if (!message || message === subject) return "";
  const [firstLine, ...remainingLines] = message.split("\n");
  return firstLine.trim() === subject
    ? remainingLines.join("\n").trim()
    : message;
}

export function relativeCommitTime(authoredAt, now = Date.now()) {
  const elapsed = Math.max(0, now - new Date(authoredAt).getTime());
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(authoredAt).toLocaleDateString();
}

export function explorerShortcutAction(event, options = {}) {
  if (event.repeat) return null;
  if (matchesCommand(event, options.settings, "file.new")) return "new";
  if (matchesCommand(event, options.settings, "file.close") && options.activeFilePath) return "close";
  return null;
}

function ExplorerIcon({ entry, expanded }) {
  if (entry.isDirectory) {
    return expanded
      ? <FolderOpen className="shrink-0 text-muted" size={13} />
      : <Folder className="shrink-0 text-muted" size={13} />;
  }
  const lower = entry.name.toLowerCase();
  if (lower.endsWith(".rattish")) return <FileCode2 className="shrink-0 text-brand" size={13} />;
  if (lower.endsWith(".json")) return <FileJson2 className="shrink-0 text-amber-600" size={13} />;
  return <FileText className="shrink-0 text-muted" size={13} />;
}

function SourceControlDecoration({ directory = false, path, projectRoot, statuses }) {
  const status = sourceControlStatusForPath(projectRoot, path, statuses, directory);
  if (!status) return null;
  if (directory) {
    return (
      <span
        aria-label={`${workspaceBasename(path) || "Project"} contains source control changes`}
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-600"
        title="Contains source control changes"
      />
    );
  }
  const presentation = {
    "!": { className: "text-red-700 dark:text-red-300", label: "Merge conflict" },
    A: { className: "source-control-status--added", label: "Added" },
    M: { className: "source-control-status--modified", label: "Modified" },
    U: { className: "source-control-status--untracked", label: "Untracked" },
  }[status];
  if (!presentation) return null;
  return (
    <span
      aria-label={`${workspaceBasename(path)}: ${presentation.label}`}
      className={`shrink-0 px-0.5 text-[11px] font-semibold ${presentation.className}`}
      title={presentation.label}
    >
      {status}
    </span>
  );
}

function ExplorerContextMenu({
  canPaste,
  entry,
  onCopy,
  onCopyPath,
  onCreateFile,
  onCreateFolder,
  onDelete,
  onOpen,
  onPaste,
  onRefresh,
  onRename,
  pathCopied,
  x,
  y,
}) {
  return (
    <div
      aria-label="File actions"
      className="fixed z-[90] w-56 rounded-lg border border-line bg-white p-1 text-xs shadow-panel"
      role="menu"
      style={{ left: x, top: y }}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <MenuButton icon={FolderOpen} label="Open in file explorer" onClick={onOpen} />
      <MenuButton icon={pathCopied ? Check : Copy} label={pathCopied ? "Path copied" : "Copy path"} onClick={onCopyPath} />
      {entry ? (
        <>
          <div className="my-1 border-t border-line" />
          <MenuButton icon={PencilLine} label="Rename" shortcut="F2" onClick={onRename} />
          <MenuButton icon={Copy} label="Copy" shortcut="Ctrl+C" onClick={onCopy} />
          <MenuButton danger icon={Trash2} label="Delete" shortcut="Delete" onClick={onDelete} />
        </>
      ) : null}
      <div className="my-1 border-t border-line" />
      <MenuButton disabled={!canPaste} icon={Copy} label="Paste" shortcut="Ctrl+V" onClick={onPaste} />
      <MenuButton icon={FilePlus2} label="New file" onClick={onCreateFile} />
      <MenuButton icon={FolderPlus} label="New folder" onClick={onCreateFolder} />
      <div className="my-1 border-t border-line" />
      <MenuButton icon={RefreshCw} label="Refresh" onClick={onRefresh} />
    </div>
  );
}

function MenuButton({ danger = false, disabled = false, icon: Icon, label, onClick, shortcut = "" }) {
  return (
    <button
      className={`flex h-8 w-full items-center gap-2 rounded-md px-2 text-left transition disabled:cursor-not-allowed disabled:opacity-40 ${danger ? "text-red-700 hover:bg-red-50" : "text-ink hover:bg-slate-50"}`}
      disabled={disabled}
      role="menuitem"
      type="button"
      onClick={onClick}
    >
      <Icon size={13} />
      <span className="flex-1">{label}</span>
      {shortcut ? <span className="text-[10px] text-muted">{shortcut}</span> : null}
    </button>
  );
}

export function visibleTreeRows(rootPath, directories, expanded, query = "", statuses = []) {
  const rows = [];
  const normalizedQuery = query.trim().toLowerCase();
  function visit(directory, depth) {
    const entries = directoryEntriesWithGitChanges(
      rootPath,
      directory,
      directories[directory] ?? [],
      statuses,
    );
    for (const entry of entries) {
      if (!normalizedQuery || entry.isDirectory || entry.name.toLowerCase().includes(normalizedQuery)) {
        rows.push({ depth, entry });
      }
      if (entry.isDirectory && expanded.has(entry.path)) visit(entry.path, depth + 1);
    }
  }
  if (rootPath) visit(rootPath, 0);
  return rows;
}

export function directoryEntriesWithGitChanges(rootPath, directory, entries = [], statuses = []) {
  const next = [...entries];
  const nameKey = name => normalizeWorkspacePath(joinWorkspacePath(directory, name));
  const names = new Set(next.map((entry) => nameKey(entry.name)));

  for (const change of statuses) {
    if (change.status === "D") continue;
    const changedPath = joinWorkspacePath(rootPath, change.path);
    if (!pathWithin(changedPath, directory)) continue;
    const remainder = workspaceRelativePath(directory, changedPath);
    if (!remainder || remainder.startsWith("../")) continue;
    const [name, ...rest] = remainder.split("/");
    if (!name || names.has(nameKey(name))) continue;
    const isDirectory = rest.length > 0;
    next.push({
      hidden: name.startsWith("."),
      isDirectory,
      isFile: !isDirectory,
      name,
      path: joinWorkspacePath(directory, name),
    });
    names.add(nameKey(name));
  }
  return next.sort((left, right) => {
    if (left.isDirectory !== right.isDirectory) return left.isDirectory ? -1 : 1;
    return left.name.localeCompare(right.name);
  });
}

export function sourceControlStatusForPath(rootPath, targetPath, statuses = [], directory = false) {
  if (directory) {
    return statuses.some(change => change.status !== "D"
      && pathWithin(joinWorkspacePath(rootPath, change.path), targetPath)) ? "changed" : "";
  }
  return statuses.find(change => samePath(joinWorkspacePath(rootPath, change.path), targetPath))?.status ?? "";
}

function sourceControlSnapshotsEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function nextCopyName(name = "copy", existingNames = new Set()) {
  const dotIndex = name.lastIndexOf(".");
  const base = dotIndex > 0 ? name.slice(0, dotIndex) : name;
  const extension = dotIndex > 0 ? name.slice(dotIndex) : "";
  let index = 1;
  let candidate = `${base} copy${extension}`;
  while (existingNames.has(candidate)) {
    index += 1;
    candidate = `${base} copy ${index}${extension}`;
  }
  return candidate;
}

export function joinWorkspacePath(directory = "", name = "") {
  const separator = directory.includes("\\") && !directory.includes("/") ? "\\" : "/";
  return `${directory.replace(/[\\/]+$/, "")}${separator}${name.replace(/^[\\/]+/, "")}`;
}

export function parentWorkspacePath(value = "") {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (index <= 0) return normalized.slice(0, Math.max(index, 1)) || normalized;
  return normalized.slice(0, index);
}

export function workspaceAncestorPaths(rootPath = "", targetPath = "") {
  if (!pathWithin(targetPath, rootPath) || samePath(targetPath, rootPath)) return [];
  const root = displayPath(rootPath);
  const relativePath = displayPath(targetPath).slice(root.endsWith("/") ? root.length : root.length + 1);
  const directoryNames = relativePath.split("/").filter(Boolean).slice(0, -1);
  const ancestors = [rootPath];
  for (const directoryName of directoryNames) {
    ancestors.push(joinWorkspacePath(ancestors.at(-1), directoryName));
  }
  return ancestors;
}

export function explorerMenuPosition(clientX, clientY, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight) {
  const width = 224;
  const height = 292;
  const availableWidth = Number.isFinite(viewportWidth) ? viewportWidth : 1024;
  const availableHeight = Number.isFinite(viewportHeight) ? viewportHeight : 768;
  return {
    x: Math.max(8, Math.min(Number.isFinite(clientX) ? clientX : 8, availableWidth - width - 8)),
    y: Math.max(8, Math.min(Number.isFinite(clientY) ? clientY : 8, availableHeight - height - 8)),
  };
}

function entryForPath(rootPath, directories, targetPath) {
  if (!targetPath) return null;
  if (samePath(targetPath, rootPath)) {
    return { isDirectory: true, isFile: false, name: workspaceBasename(rootPath), path: rootPath };
  }
  for (const entries of Object.values(directories)) {
    const match = entries.find((entry) => samePath(entry.path, targetPath));
    if (match) return match;
  }
  return null;
}

function workspaceBasename(value = "") {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  return normalized.slice(index + 1);
}

export function mainWorktreePath(worktrees = [], fallback = "") {
  return String(worktrees[0]?.path || fallback).trim();
}

function workspaceRelativePath(rootPath = "", targetPath = "") {
  const root = displayPath(rootPath);
  const target = displayPath(targetPath);
  if (samePath(target, root)) return "";
  if (!pathWithin(target, root)) return target;
  return target.slice(root.endsWith("/") ? root.length : root.length + 1);
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

function discardDirectoryBranch(setDirectories, branchPath) {
  setDirectories((current) => Object.fromEntries(
    Object.entries(current).filter(([directory]) =>
      !pathWithin(directory, branchPath)),
  ));
}

export function sourceControlErrorSummary(message) {
  if (/unsaved editor/i.test(message)) return "Save your editor changes and try again.";
  if (/conflict|unmerged/i.test(message)) return "Resolve the Git conflicts before continuing.";
  if (/authentication|permission denied|could not read Username/i.test(message)) return "Git authentication failed. Check your remote access and try again.";
  if (/restart the desktop/i.test(message)) return "Restart the desktop app to enable Git actions.";
  if (/network|could not resolve|unable to access/i.test(message)) return "Could not reach the remote. Check your connection and try again.";
  return "Git could not complete this action. Review the details and try again.";
}
