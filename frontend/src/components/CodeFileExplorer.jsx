import { pathKey as normalizeWorkspacePath, samePath, pathWithin, displayPath, joinWorkspacePath, parentWorkspacePath, workspaceBasename } from "../lib/workspacePaths.js";
import { useSourceControl } from "../lib/useSourceControl.js";
import SourceControlPanel from "./SourceControlPanel.jsx";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { askRemAboutFiles, copyFilesForPaste } from "../lib/fileAttachments.js";
import { beginFileDrag, containsFileTransfer, droppedFileEntries, getFileClipboard, setFileClipboard, subscribeFileClipboard, transferName, clipboardEntries, topLevelTransferEntries } from "../lib/fileTransfers.js";
import {
  Check,
  ChevronDown,
  Copy,
  Scissors,
  ClipboardPaste,
  FileCode2,
  FileJson2,
  FilePlus2,
  FileText,
  FlaskConical,
  Folder,
  FolderOpen,
  FolderPlus,
  Loader2,
  Lock,
  LockOpen,
  PencilLine,
  Play,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import KanbanSidebar from "./KanbanSidebar.jsx";
import OrganizationSidebar from "./OrganizationSidebar.jsx";
import SwarmSidebar from "./SwarmSidebar.jsx";
import { PLUGIN_ICONS } from "./pluginIcons.js";
import { dropPlacement, enabledPlugins, movePlugin, PLUGIN_DRAG_TYPE, pluginEnabled, pluginHasPane, resolvePluginActivity, stepPlugin } from "../lib/plugins.js";
import ProjectSearch from "./ProjectSearch.jsx";
const SecondBrainSidebar = lazy(() => import("./SecondBrainSidebar.jsx"));
const RemHomeSidebar = lazy(() => import("./RemHomeSidebar.jsx"));
import { Dialog } from "./Dialog.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import RemActionIcon from "./RemActionIcon.jsx";
import { hasUnsavedCodeChanges } from "../lib/codeEditorSessions.js";
import { PathNameDialog } from "./PathNameDialog.jsx";
import { formatKeybinding, DEFAULT_APP_SETTINGS, matchesCommand } from "../lib/settings.js";

export default function CodeFileExplorer({
  activeFilePath = "",
  fileWorkflows = [],
  onRunWorkflowWithFile,
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
  onSettingsChange,
  onOpenSwarm,
  selectedSwarmId,
  onOpenOrganization,
  selectedOrganizationId,
  onOpenSecondBrain,
  onOpenKanban,
  onOpenPosts,
  kanbanOpen,
  secondBrainOpen = false,
  onOpenRemHome,
  remHomeOpen = false,
  onShowEditor,
  onOpenBrowser,
  remProjectOptions = [],
  onFilesystemChange,
  onCloseActiveFile,
  onOpenFile,
  onRemoveRecentProject,
  onSelectProject,
}) {
  const rootPath = workflow?.projectRoot ?? "";
  const [confirm, confirmDialog] = useConfirm();
  const lastNewFileRequestRef = useRef(newFileRequest);
  const recentMenuRef = useRef(null);
  const treeRef = useRef(null);
  const activeFileRowRef = useRef(null);
  const directoriesRef = useRef({});
  const revealRequestRef = useRef(0);
  const [directories, setDirectories] = useState({});
  const [expanded, setExpanded] = useState(() => new Set());
  const [loadingPaths, setLoadingPaths] = useState(() => new Set());
  const [selectedPath, updateSelectedPath] = useState(rootPath);
  const [selectedPaths, setSelectedPaths] = useState(() => new Set([rootPath]));
  const selectionAnchorRef = useRef(rootPath);
  const selectedPathsRef = useRef(selectedPaths);
  selectedPathsRef.current = selectedPaths;
  const pointerSelectingRef = useRef(false);
  function setSelectedPath(path) {
    updateSelectedPath(path);
    setSelectedPaths(new Set([path]));
    selectionAnchorRef.current = path;
  }
  const [duplicateRequest, setDuplicateRequest] = useState(null);
  const duplicateResolverRef = useRef(null);
  function resolveDuplicate(action) {
    const resolve = duplicateResolverRef.current;
    duplicateResolverRef.current = null;
    setDuplicateRequest(null);
    resolve?.({ action, applyAll: duplicateRequest?.applyAll || false });
  }
  useEffect(() => () => {
    duplicateResolverRef.current?.({ action: "cancel", applyAll: false });
    duplicateResolverRef.current = null;
  }, [rootPath]);
  useEffect(() => { setDuplicateRequest(null); }, [rootPath]);
  const [contextMenu, setContextMenu] = useState(null);
  // "Run workflow with this file": the file waiting for a workflow to be chosen.
  const [fileRunRequest, setFileRunRequest] = useState(null);
  const clipboardEntry = useSyncExternalStore(subscribeFileClipboard, getFileClipboard, getFileClipboard);
  const [dropDirectory, setDropDirectory] = useState("");
  const [transferBusy, setTransferBusy] = useState(false);
  const transferBusyRef = useRef(false);
  const [copiedPath, setCopiedPath] = useState("");
  const [nameRequest, setNameRequest] = useState(null);
  const [error, setError] = useState("");
  const [grantRequired, setGrantRequired] = useState(false);
  const [recentMenuOpen, setRecentMenuOpen] = useState(false);
  const currentRootRef = useRef(rootPath);
  currentRootRef.current = rootPath;
  const pluginSettings = settings?.plugins ?? DEFAULT_APP_SETTINGS.plugins;
  const railPlugins = enabledPlugins(pluginSettings).filter((plugin) => plugin.id !== "workflows" || workflowsContent !== undefined);
  const railIds = railPlugins.map((plugin) => plugin.id);
  const pluginsLocked = pluginSettings.locked || !onSettingsChange;
  const railRef = useRef(null);
  const [pluginDrag, setPluginDrag] = useState(null);
  function reorderPlugins(order) {
    if (order.some((id, index) => id !== pluginSettings.order?.[index])) onSettingsChange?.("plugins.order", order);
  }
  const [localSidebarView, setLocalSidebarView] = useState("files");
  const sidebarView = resolvePluginActivity(pluginSettings, controlledSidebarView || localSidebarView);
  // Rem's panel stays mounted once shown, so a task being edited there survives
  // switching to another plugin and back.
  const [remPanelMounted, setRemPanelMounted] = useState(false);
  if (sidebarView === "rem" && !remPanelMounted) setRemPanelMounted(true);
  const setSidebarView = useCallback((value) => { setLocalSidebarView(value); onSidebarViewChange?.(value); }, [onSidebarViewChange]);
  const [searchFocusRequest, setSearchFocusRequest] = useState(0);
  const searchEnabled = pluginEnabled(pluginSettings, "search");
  useEffect(() => {
    function openSearch(event) {
      if (!searchEnabled) return;
      if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.key.toLowerCase() !== "f") return;
      event.preventDefault();
      setSidebarView("search");
      setSearchFocusRequest((value) => value + 1);
    }
    window.addEventListener("keydown", openSearch);
    return () => window.removeEventListener("keydown", openSearch);
  }, [searchEnabled, setSidebarView]);
  const treeRefreshRef = useRef(null);
  const sourceControlState = useSourceControl({
    rootPath,
    sidebarView,
    confirm,
    refreshTree: () => treeRefreshRef.current(),
    setExplorerError: setError,
    onFilesystemChange,
    onOpenFile,
    onSelectProject,
    onRemoveRecentProject,
  });
  const { sourceControl, loadSourceControlStatus, clearSourceControl, gitBusy, setGitBusy } = sourceControlState;

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

  const refreshTree = useCallback(async () => {
    directoriesRef.current = {};
    setDirectories({});
    setExpanded(new Set(rootPath ? [rootPath] : []));
    // Keep the last Git snapshot visible while refreshing or switching worktrees.
    if (!rootPath) {
      clearSourceControl();
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
  }, [clearSourceControl, loadDirectory, loadSourceControlStatus, rootPath]);

  treeRefreshRef.current = refreshTree;

  useEffect(() => {
    setSelectedPath(rootPath);
    setContextMenu(null);
    setGrantRequired(false);
    void refreshTree();
  }, [refreshTree, rootPath]);

  useEffect(() => {
    const ancestorPaths = workspaceAncestorPaths(rootPath, activeFilePath);
    if (!ancestorPaths.length) return undefined;

    const request = revealRequestRef.current + 1;
    revealRequestRef.current = request;
    if (!selectedPathsRef.current.has(activeFilePath)) setSelectedPath(activeFilePath);
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
    if (!entry || !selectedPaths.has(entry.path)) setSelectedPath(entry?.path ?? rootPath);
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

  function selectedTransferEntries(entry = selectedEntry) {
    if (!entry || samePath(entry.path, rootPath)) return [];
    const entries = selectedPaths.has(entry.path)
      ? rows.filter(row => selectedPaths.has(row.entry.path)).map(row => row.entry)
      : [entry];
    return topLevelTransferEntries(entries.length ? entries : [entry]);
  }

  function selectTreeEntry(event, entry) {
    pointerSelectingRef.current = false;
    const anchor = rows.findIndex(row => samePath(row.entry.path, selectionAnchorRef.current));
    const target = rows.findIndex(row => samePath(row.entry.path, entry.path));
    if (event.shiftKey && anchor >= 0 && target >= 0) {
      setSelectedPaths(new Set(rows.slice(Math.min(anchor, target), Math.max(anchor, target) + 1).map(row => row.entry.path)));
      updateSelectedPath(entry.path);
    } else if (event.ctrlKey || event.metaKey) {
      setSelectedPaths(current => current.has(entry.path) ? withoutSetValue(current, entry.path) : withSetValue(current, entry.path));
      updateSelectedPath(entry.path);
      selectionAnchorRef.current = entry.path;
    } else setSelectedPath(entry.path);
  }

  function copyEntry(entry = selectedEntry, operation = "copy") {
    setContextMenu(null);
    const entries = selectedTransferEntries(entry);
    if (!entries.length) return;
    setFileClipboard({ ...entries[0], entries, operation });
    // Copied files also paste into Rem's chats as attachments.
    if (operation === "copy") void copyFilesForPaste(entries).catch(() => {});
    treeRef.current?.focus();
  }

  function askRemAboutEntry(entry) {
    setContextMenu(null);
    askRemAboutFiles(selectedTransferEntries(entry).filter(item => !item.isDirectory).map(item => item.path));
  }

  async function transferEntries(entries, directory, operation, onTransferred) {
    if (gitBusy || transferBusyRef.current || !directory) return false;
    const bridge = window.goferDesktop?.workspace;
    const transfer = operation === "move" ? bridge?.movePath : bridge?.copyPath;
    if (!transfer) throw new Error("Restart the desktop app to enable file transfers.");
    transferBusyRef.current = true;
    setTransferBusy(true);
    let completed = true;
    let batchChoice;
    try {
      const listing = await bridge.listDirectory({ currentPath: directory });
      const names = new Set((listing.entries || []).map(entry => entry.name));
      for (const entry of topLevelTransferEntries(entries)) {
        if (currentRootRef.current !== rootPath) { completed = false; break; }
        if (hasUnsavedCodeChanges(entry.path)) throw new Error(`Save editor changes in ${entry.name} before transferring it.`);
        const originalDestination = joinWorkspacePath(directory, entry.name);
        if (operation === "move" && samePath(entry.path, originalDestination)) continue;
        // Validate folder ancestry before opening a conflict prompt.
        transferName(entry, directory, new Set(), operation);
        let action = "keep-both";
        if (names.has(entry.name)) {
          const choice = batchChoice || await new Promise(resolve => {
            duplicateResolverRef.current = resolve;
            setDuplicateRequest({ entry, directory, applyAll: false });
          });
          if (choice.action === "cancel") { completed = false; break; }
          if (choice.applyAll) batchChoice = choice;
          action = choice.action;
        }
        const name = transferName(entry, directory, names, operation, action);
        if (!name) continue;
        const destinationPath = joinWorkspacePath(directory, name);
        if (action === "replace" && (samePath(entry.path, destinationPath) || hasUnsavedCodeChanges(destinationPath))) {
          throw new Error(samePath(entry.path, destinationPath) ? "A file cannot replace itself. Choose Keep both." : `Save editor changes in ${name} before replacing it.`);
        }
        try {
          await transfer({ sourcePath: entry.path, destinationPath, replace: action === "replace" });
        } catch (cause) {
          // Vite can update this dialog while Electron still has the old handler
          // or preload that drops the replacement flag.
          if (action === "replace" && /destination already exists/i.test(cause.message || "")) {
            throw new Error("Restart Raticode to load file replacement support, then try again.", { cause });
          }
          throw cause;
        }
        names.add(name);
        onTransferred?.(entry);
        if (action === "replace" && currentRootRef.current === rootPath) {
          discardDirectoryBranch(setDirectories, destinationPath);
          if (entry.isDirectory && expanded.has(destinationPath)) await loadDirectory(destinationPath, { clearError: false });
        }
        onFilesystemChange?.({ isDirectory: entry.isDirectory, kind: operation === "move" ? "rename" : "copy", path: destinationPath, sourcePath: entry.path, replaced: action === "replace" });
        if (operation === "move" && currentRootRef.current === rootPath) {
          discardDirectoryBranch(setDirectories, entry.path);
          const sourceDirectory = parentWorkspacePath(entry.path);
          if (pathWithin(sourceDirectory, rootPath)) await loadDirectory(sourceDirectory, { clearError: false });
        }
        if (currentRootRef.current === rootPath) setSelectedPath(destinationPath);
      }
      return completed;
    } finally {
      if (currentRootRef.current === rootPath) {
        setExpanded(current => withSetValue(current, directory));
        await loadDirectory(directory, { clearError: false });
        void loadSourceControlStatus();
      }
      transferBusyRef.current = false;
      setTransferBusy(false);
    }
  }

  async function pasteEntry(directory = selectedDirectory || rootPath) {
    setContextMenu(null);
    const entry = getFileClipboard();
    if (!entry || !directory || gitBusy || transferBusyRef.current) return;
    setError("");
    try {
      let remaining = clipboardEntries(entry);
      let currentClipboard = entry;
      await transferEntries(remaining, directory, entry.operation || "copy", moved => {
        if (entry.operation !== "move" || getFileClipboard() !== currentClipboard) return;
        remaining = remaining.filter(item => !samePath(item.path, moved.path));
        currentClipboard = remaining.length ? { ...remaining[0], entries: remaining, operation: "move" } : null;
        setFileClipboard(currentClipboard);
      });
    } catch (cause) { setError(cause.message || "Unable to paste file."); }
  }

  function dragOverDirectory(event, directory) {
    if (!containsFileTransfer(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setDropDirectory(directory);
    event.dataTransfer.dropEffect = Array.from(event.dataTransfer.types).includes("application/x-raticode-files") && !(event.ctrlKey || event.altKey || event.metaKey) ? "move" : "copy";
  }

  async function dropFiles(event, directory) {
    if (!containsFileTransfer(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    setDropDirectory("");
    setError("");
    const copy = event.ctrlKey || event.altKey || event.metaKey;
    try {
      const { entries, internal } = await droppedFileEntries(event.dataTransfer);
      await transferEntries(entries, directory, internal && !copy ? "move" : "copy");
    } catch (cause) { setError(cause.message || "Unable to drop files."); }
  }

  async function deleteEntry(entry = selectedEntry) {
    if (gitBusy || transferBusyRef.current) return;
    setContextMenu(null);
    const entries = selectedTransferEntries(entry);
    if (!entries.length) return;
    const kind = entry.isDirectory ? "folder" : "file";
    const message = entries.length > 1
      ? `Move ${entries.length} items to the trash? They can be restored from the operating system trash.`
      : `Move ${entry.name} to the trash? This ${kind} can be restored from the operating system trash.`;
    if (!await confirm({ message, confirmLabel: "Move to trash", danger: true }) || transferBusyRef.current || currentRootRef.current !== rootPath) return;
    setError("");
    const parents = new Set();
    transferBusyRef.current = true;
    setTransferBusy(true);
    try {
      const remove = window.goferDesktop?.workspace?.deletePath;
      if (!remove) throw new Error("Restart Raticode to enable file deletion.");
      for (const selected of entries) {
        if (currentRootRef.current !== rootPath) break;
        await remove(selected.path);
        parents.add(parentWorkspacePath(selected.path));
        if (currentRootRef.current === rootPath) discardDirectoryBranch(setDirectories, selected.path);
        onFilesystemChange?.({ isDirectory: selected.isDirectory, kind: "delete", path: selected.path });
      }
    } catch (deleteError) {
      if (currentRootRef.current === rootPath) setError(deleteError instanceof Error ? deleteError.message : "Unable to delete path");
    } finally {
      try {
        if (currentRootRef.current === rootPath && parents.size) {
          for (const parent of parents) await loadDirectory(parent, { clearError: false });
          setSelectedPath(parentWorkspacePath(entry.path));
          void loadSourceControlStatus();
        }
      } finally {
        transferBusyRef.current = false;
        setTransferBusy(false);
      }
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

  function handleTreeKeyDown(event) {
    if (event.target?.matches?.("input, textarea") || event.target?.closest?.('[role="menu"]')) return;
    if (["ArrowUp", "ArrowDown", "Home", "End", "ArrowLeft", "ArrowRight"].includes(event.key)) {
      const items = [...treeRef.current.querySelectorAll('[role="treeitem"]')];
      const index = Math.max(0, items.indexOf(event.target.closest?.('[role="treeitem"]')));
      const item = items[index];
      const row = rows.find(row => row.entry.path === item?.dataset?.path);
      event.preventDefault();
      if (event.key === "ArrowUp") items[Math.max(0, index - 1)]?.focus();
      if (event.key === "ArrowDown") items[Math.min(items.length - 1, index + 1)]?.focus();
      if (event.key === "Home") items[0]?.focus();
      if (event.key === "End") items.at(-1)?.focus();
      if (event.key === "ArrowRight") {
        if (row?.entry.isDirectory && !expanded.has(row.entry.path)) void toggleDirectory(row.entry);
        else if (!row || row.entry.isDirectory) items[index + 1]?.focus();
      }
      if (event.key === "ArrowLeft") {
        if (row?.entry.isDirectory && expanded.has(row.entry.path)) void toggleDirectory(row.entry);
        else {
          const level = Number(item?.getAttribute("aria-level") || 1);
          for (let parent = index - 1; parent >= 0; parent--) {
            if (Number(items[parent].getAttribute("aria-level")) < level) { items[parent].focus(); break; }
          }
        }
      }
      return;
    }
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === "c") {
      event.preventDefault();
      copyEntry();
    } else if (modifier && event.key.toLowerCase() === "x") {
      event.preventDefault();
      copyEntry(selectedEntry, "move");
    } else if (modifier && event.key.toLowerCase() === "v") {
      event.preventDefault();
      void pasteEntry();
    } else if (event.key === "F2") {
      event.preventDefault();
      requestRename();
    } else if (event.key === "Delete" || (event.key === "Backspace" && /Mac|iPhone|iPad/.test(navigator.platform))) {
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
  // A pane docked to the right keeps its activity bar on the window edge.
  const railOnRight = settings.layout?.projectPaneSide === "right";
  return (
    <div
      className={`flex h-full min-h-0 min-w-0 ${railOnRight ? "flex-row-reverse" : ""} ${paneVisible ? "" : "[&>[role=tabpanel]]:hidden [&>section]:hidden"}`}
      aria-label="Project sidebar"
      onKeyDownCapture={handleExplorerKeyDown}
    >
      <div className={`flex w-10 shrink-0 flex-col border-line ${railOnRight ? "border-l" : "border-r"}`}>
        <div ref={railRef} role="tablist" aria-label="Project sidebar views" aria-orientation="vertical" className="flex flex-col">
          {railPlugins.map(({ id, label, badge }) => {
            const Icon = PLUGIN_ICONS[id];
            const dropTarget = pluginDrag && pluginDrag.id !== id && pluginDrag.targetId === id ? pluginDrag.placement : "";
            return (
              <button
                key={id}
                id={`sidebar-tab-${id}`}
                role="tab"
                type="button"
                aria-label={label}
                aria-description={badge || undefined}
                title={`${label}${badge ? ` (${badge})` : ""}${pluginsLocked ? "" : " · Drag to reorder"}`}
                aria-selected={sidebarView === id}
                aria-controls={pluginHasPane(id) ? `sidebar-panel-${id}` : `main-panel-${id}`}
                tabIndex={sidebarView === id ? 0 : -1}
                draggable={!pluginsLocked}
                className={`relative grid h-10 w-full shrink-0 place-items-center ${railOnRight ? "border-r-2" : "border-l-2"} focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand ${sidebarView === id ? "border-brand text-ink" : "border-transparent text-muted hover:bg-slate-100 hover:text-ink"} ${pluginDrag?.id === id ? "opacity-40" : ""}`}
                onClick={() => { setSidebarView(id); if (id === "search") setSearchFocusRequest((value) => value + 1); if (id === "rem") onOpenRemHome?.(); if (id === "second-brain") onOpenSecondBrain?.(); if (id === "kanban") onOpenKanban?.(); if (id === "posts") onOpenPosts?.(); if (id === "files") onShowEditor?.(); if (id === "browser") onOpenBrowser?.(); setContextMenu(null); setRecentMenuOpen(false); }}
                onDragStart={(event) => {
                  if (pluginsLocked) { event.preventDefault(); return; }
                  event.dataTransfer?.setData(PLUGIN_DRAG_TYPE, id);
                  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
                  setPluginDrag({ id, targetId: "", placement: "before" });
                }}
                onDragOver={(event) => {
                  if (!pluginDrag) return;
                  event.preventDefault();
                  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                  const placement = dropPlacement(event);
                  setPluginDrag((current) => current && (current.targetId !== id || current.placement !== placement) ? { ...current, targetId: id, placement } : current);
                }}
                onDrop={(event) => {
                  if (!pluginDrag) return;
                  event.preventDefault();
                  reorderPlugins(movePlugin(pluginSettings.order, pluginDrag.id, id, dropPlacement(event)));
                  setPluginDrag(null);
                }}
                onDragEnd={() => setPluginDrag(null)}
                onKeyDown={(event) => {
                  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  // Alt+Arrow moves the focused plugin when the order is unlocked.
                  if (event.altKey) {
                    if (pluginsLocked || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
                    reorderPlugins(stepPlugin(pluginSettings.order, id, event.key === "ArrowUp" ? -1 : 1, railIds));
                    window.setTimeout(() => railRef.current?.querySelector?.(`#sidebar-tab-${id}`)?.focus(), 0);
                    return;
                  }
                  const next = event.key === "Home" ? railIds[0] : event.key === "End" ? railIds.at(-1) : railIds[(railIds.indexOf(id) + (event.key === "ArrowDown" ? 1 : railIds.length - 1)) % railIds.length];
                  setSidebarView(next);
                  setContextMenu(null);
                  setRecentMenuOpen(false);
                  event.currentTarget.parentElement.querySelector(`#sidebar-tab-${next}`)?.focus();
                }}
              >
                {dropTarget ? <span aria-hidden="true" className={`pointer-events-none absolute inset-x-1.5 h-0.5 rounded-full bg-brand ${dropTarget === "after" ? "bottom-0" : "top-0"}`} /> : null}
                <Icon aria-hidden="true" size={19} strokeWidth={1.5} />
                {badge ? <FlaskConical aria-hidden="true" data-plugin-badge={badge} size={10} strokeWidth={2} className="pointer-events-none absolute bottom-1.5 right-1.5 text-amber-600 dark:text-amber-400" /> : null}
              </button>
            );
          })}
        </div>
        {onSettingsChange ? (
          <button
            type="button"
            aria-label="Lock plugin order"
            aria-pressed={pluginSettings.locked}
            title={pluginSettings.locked ? "Plugin order is locked. Click to allow dragging." : "Lock plugin order"}
            className={`mt-auto grid h-8 w-full shrink-0 place-items-center text-muted transition hover:text-ink hover:opacity-100 focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand ${pluginSettings.locked ? "opacity-60" : "opacity-35"}`}
            onClick={() => onSettingsChange("plugins.locked", !pluginSettings.locked)}
          >{pluginSettings.locked ? <Lock aria-hidden="true" size={12} strokeWidth={1.75} /> : <LockOpen aria-hidden="true" size={12} strokeWidth={1.75} />}</button>
        ) : null}
      </div>
      {workflowsContent !== undefined && railIds.includes("workflows") ? (
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
      {remPanelMounted && railIds.includes("rem") ? <Suspense fallback={sidebarView === "rem" ? <section id="sidebar-panel-rem" role="tabpanel" aria-labelledby="sidebar-tab-rem" className="flex-1 p-3 text-xs text-muted">Loading Rem…</section> : null}><RemHomeSidebar active={sidebarView === "rem"} open={remHomeOpen} onOpen={() => onOpenRemHome?.()} projectOptions={remProjectOptions} projectRoot={rootPath} /></Suspense> : null}
      {railIds.includes("kanban") ? <KanbanSidebar active={sidebarView === "kanban"} open={kanbanOpen} onOpen={onOpenKanban} /> : null}
      {railIds.includes("organizations") ? <OrganizationSidebar active={sidebarView === "organizations"} selectedId={selectedOrganizationId} onSelect={onOpenOrganization} /> : null}
      {sidebarView === "second-brain" ? <Suspense fallback={<section id="sidebar-panel-second-brain" role="tabpanel" aria-labelledby="sidebar-tab-second-brain" className="flex-1 p-3 text-xs text-muted">Loading Second Brain…</section>}><SecondBrainSidebar active open={secondBrainOpen} onOpen={() => onOpenSecondBrain?.()} /></Suspense> : null}
      {railIds.includes("swarms") ? <SwarmSidebar key={`swarms:${rootPath}`} rootPath={rootPath} active={sidebarView === "swarms"} selectedId={selectedSwarmId} onSelect={onOpenSwarm} /> : null}
      {railIds.includes("search") ? <ProjectSearch key={rootPath} rootPath={rootPath} active={sidebarView === "search"} focusRequest={searchFocusRequest} onOpenFile={onOpenFile} disabled={gitBusy} onBusy={setGitBusy} onReplace={() => { void refreshTree(); onFilesystemChange?.({ type: "git", rootPath }); }} /> : null}
      <div id="sidebar-panel-files" role="tabpanel" aria-labelledby="sidebar-tab-files" hidden={sidebarView !== "files"} className={`min-h-0 min-w-0 flex-1 flex-col ${sidebarView === "files" ? "flex" : "hidden"}`}>
      <div className="flex h-7 items-center justify-between px-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Explorer</span>
        <div className="flex items-center">
          <button aria-label="Copy selected file" title="Copy selected file" type="button" disabled={!selectedEntry || samePath(selectedEntry.path, rootPath) || transferBusy} className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 disabled:opacity-40" onClick={() => copyEntry()}><Copy size={13} /></button>
          <button aria-label="Cut selected file" title="Cut selected file" type="button" disabled={!selectedEntry || samePath(selectedEntry.path, rootPath) || transferBusy} className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 disabled:opacity-40" onClick={() => copyEntry(selectedEntry, "move")}><Scissors size={13} /></button>
          <button aria-label="Paste files" title="Paste into selected folder" type="button" disabled={!clipboardEntry || transferBusy || gitBusy} className="grid h-6 w-6 place-items-center rounded-md text-muted hover:bg-slate-100 disabled:opacity-40" onClick={() => void pasteEntry()}><ClipboardPaste size={13} /></button>
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
        className={`flex min-h-0 flex-1 flex-col outline-none ${dropDirectory === rootPath ? "ring-2 ring-inset ring-brand" : ""}`}
        role="tree"
        aria-multiselectable="true"
        tabIndex={-1}
        onContextMenu={(event) => showContextMenu(event)}
        onKeyDown={handleTreeKeyDown}
        aria-busy={transferBusy}
        onCopy={event => { event.preventDefault(); copyEntry(); }}
        onCut={event => { event.preventDefault(); copyEntry(selectedEntry, "move"); }}
        onPaste={event => { event.preventDefault(); void pasteEntry(); }}
        onDragOver={event => dragOverDirectory(event, rootPath)}
        onDrop={event => void dropFiles(event, rootPath)}
        onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDropDirectory(""); }}
      >
        <div ref={recentMenuRef} className="relative z-10 shrink-0 bg-white">
          <button
            aria-expanded={hideProjectSelector ? undefined : recentMenuOpen}
            aria-haspopup={hideProjectSelector ? undefined : "menu"}
            aria-selected={selectedPath === rootPath}
            aria-level={1}
            data-path={rootPath}
            tabIndex={!rows.some(row => row.entry.path === selectedPath) ? 0 : -1}
            onFocus={() => setSelectedPath(rootPath)}
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
              const selected = selectedPaths.has(entry.path);
              const active = normalizeWorkspacePath(activeFilePath)
                === normalizeWorkspacePath(entry.path);
              return (
                <button
                  key={entry.path}
                  ref={active ? activeFileRowRef : undefined}
                  aria-current={active ? "page" : undefined}
                  aria-expanded={entry.isDirectory ? isExpanded : undefined}
                  aria-selected={selected}
                  aria-level={depth + 2}
                  data-path={entry.path}
                  tabIndex={samePath(selectedPath, entry.path) ? 0 : -1}
                  onPointerDown={() => { pointerSelectingRef.current = true; }}
                  onFocus={() => {
                    if (pointerSelectingRef.current || selectedPaths.has(entry.path)) updateSelectedPath(entry.path);
                    else setSelectedPath(entry.path);
                  }}
                  className={`flex h-7 w-full items-center gap-1.5 rounded-md pr-1.5 text-left text-xs ${dropDirectory === entry.path ? "ring-2 ring-inset ring-brand " : ""}${clipboardEntry?.operation === "move" && clipboardEntries(clipboardEntry).some(item => samePath(item.path, entry.path)) ? "opacity-50 " : ""}${selected ? "bg-indigo-100 font-medium text-indigo-700" : "text-ink hover:bg-slate-100"}`}
                  role="treeitem"
                  draggable={!transferBusy && !gitBusy}
                  onDragStart={event => {
                    pointerSelectingRef.current = false;
                    setContextMenu(null);
                    const entries = selectedTransferEntries(entry);
                    if (!selectedPaths.has(entry.path)) setSelectedPath(entry.path);
                    beginFileDrag(event, entries);
                  }}
                  onDragEnd={() => setDropDirectory("")}
                  onDragOver={event => dragOverDirectory(event, entry.isDirectory ? entry.path : parentWorkspacePath(entry.path))}
                  onDrop={event => void dropFiles(event, entry.isDirectory ? entry.path : parentWorkspacePath(entry.path))}
                  style={{ paddingLeft: `${6 + depth * 12}px` }}
                  title={entry.path}
                  type="button"
                  onClick={event => {
                    selectTreeEntry(event, entry);
                    if (event.shiftKey || event.ctrlKey || event.metaKey) return;
                    if (entry.isDirectory) {
                      void toggleDirectory(entry);
                      return;
                    }
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
          <span>{clipboardEntry.operation === "move" ? <Scissors size={11} /> : <Copy size={11} />}</span>
          <span className="min-w-0 flex-1 truncate" title={clipboardEntry.path}>{clipboardEntry.operation === "move" ? "Cut" : "Copied"} {clipboardEntries(clipboardEntry).length > 1 ? `${clipboardEntries(clipboardEntry).length} files` : clipboardEntry.name}</span>
          <button aria-label="Paste file into selected folder" disabled={transferBusy || gitBusy} type="button" onClick={() => void pasteEntry()}><ClipboardPaste size={13} /></button>
          <button aria-label="Clear copied file" type="button" onClick={() => setFileClipboard(null)}><X size={11} /></button>
        </div>
      ) : null}

      </div>
      <SourceControlPanel
        state={sourceControlState}
        rootPath={rootPath}
        activeFilePath={activeFilePath}
        sidebarView={sidebarView}
        confirm={confirm}
        onOpenFile={onOpenFile}
        onSelectProject={onSelectProject}
      />
      {contextMenu ? (
        <ExplorerContextMenu
          canPaste={Boolean(clipboardEntry) && !transferBusy && !gitBusy}
          entry={contextMenu.entry}
          pathCopied={copiedPath === (contextMenu.entry?.path ?? rootPath)}
          x={contextMenu.x}
          y={contextMenu.y}
          onAskRem={() => askRemAboutEntry(contextMenu.entry)}
          canRunWithFile={Boolean(onRunWorkflowWithFile) && fileWorkflows.length > 0}
          onRunWithFile={() => {
            const entry = contextMenu.entry;
            setContextMenu(null);
            if (entry && !entry.isDirectory) setFileRunRequest({ path: entry.path, name: entry.name });
          }}
          onCopy={() => copyEntry(contextMenu.entry)}
          onCut={() => copyEntry(contextMenu.entry, "move")}
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

      {duplicateRequest ? (
        <Dialog title={`${duplicateRequest.entry.name} already exists`} onClose={() => resolveDuplicate("cancel")} panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink shadow-panel">
          <h2 className="break-words text-sm font-semibold">{duplicateRequest.entry.name} already exists</h2>
          <p className="mt-3 text-xs text-muted">Choose what to do with the duplicate {duplicateRequest.entry.isDirectory ? "folder" : "file"} in <span className="break-all">{duplicateRequest.directory}</span>.</p>
          <label className="mt-4 flex items-center gap-2 text-xs">
            <input type="checkbox" checked={duplicateRequest.applyAll} onChange={event => setDuplicateRequest(current => ({ ...current, applyAll: event.target.checked }))} />
            Do this for all duplicate files
          </label>
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" onClick={() => resolveDuplicate("keep-both")}>Keep both</button>
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" onClick={() => resolveDuplicate("replace")}>{duplicateRequest.entry.isDirectory ? "Replace folder" : "Replace file"}</button>
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" onClick={() => resolveDuplicate("cancel")}>Cancel</button>
          </div>
        </Dialog>
      ) : null}

      {fileRunRequest ? (
        <Dialog
          title="Run workflow with this file"
          description={`Choose a workflow to run with ${fileRunRequest.name}.`}
          onClose={() => setFileRunRequest(null)}
          panelClassName="w-full max-w-md rounded-xl border border-line bg-white p-5 text-ink shadow-panel"
        >
          <h2 className="text-sm font-semibold">Run workflow with this file</h2>
          <p className="mt-1 break-all text-xs text-muted">{fileRunRequest.name}</p>
          {fileWorkflows.length ? (
            <ul aria-label="Workflows with a file input" className="mt-3 max-h-72 space-y-1 overflow-y-auto">
              {fileWorkflows.map(({ workflow: target, input }) => {
                const several = fileWorkflows.filter(item => item.workflow.id === target.id).length > 1;
                return (
                  <li key={`${target.id}:${input}`}>
                    <button
                      className="flex w-full flex-col rounded-md border border-line px-3 py-2 text-left text-xs hover:bg-slate-50 dark:hover:bg-zinc-800"
                      type="button"
                      onClick={() => {
                        const request = fileRunRequest;
                        setFileRunRequest(null);
                        onRunWorkflowWithFile?.(target, input, request.path);
                      }}
                    >
                      <span className="font-medium">{target.name || target.id}</span>
                      {several ? <span className="text-[11px] text-muted">as {target.inputs?.[input]?.label || input}</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-3 text-xs text-muted">No workflow in this project has a file input.</p>
          )}
          <p className="mt-3 text-[11px] text-muted">The Run dialog opens with the file filled in, so you can check the other inputs first.</p>
          <div className="mt-4 flex justify-end">
            <button type="button" className="rounded border border-line px-3 py-2 text-xs" onClick={() => setFileRunRequest(null)}>Cancel</button>
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
      {confirmDialog}
    </div>
  );
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
  canRunWithFile = false,
  entry,
  onAskRem,
  onRunWithFile,
  onCopy,
  onCut,
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
          {!entry.isDirectory ? <MenuButton icon={RemActionIcon} label="Ask Rem about this" onClick={onAskRem} /> : null}
          {!entry.isDirectory && canRunWithFile ? <MenuButton icon={Play} label="Run workflow with this file" onClick={onRunWithFile} /> : null}
          <div className="my-1 border-t border-line" />
          <MenuButton icon={PencilLine} label="Rename" shortcut="F2" onClick={onRename} />
          <MenuButton icon={Copy} label="Copy" shortcut={formatKeybinding("Mod+KeyC")} onClick={onCopy} />
          <MenuButton icon={Scissors} label="Cut" shortcut={formatKeybinding("Mod+KeyX")} onClick={onCut} />
          <MenuButton danger icon={Trash2} label="Delete" shortcut="Delete" onClick={onDelete} />
        </>
      ) : null}
      <div className="my-1 border-t border-line" />
      <MenuButton disabled={!canPaste} icon={Copy} label="Paste" shortcut={formatKeybinding("Mod+KeyV")} onClick={onPaste} />
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

// Keep the typed text until it is saved so commas and spaces are not rewritten while typing.

function visibleTreeRows(rootPath, directories, expanded, query = "", statuses = []) {
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

function explorerMenuPosition(clientX, clientY, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight) {
  const width = 224;
  const height = 324;
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

