import { fetchChatTurn } from "../lib/chatTransport.js";
import { pathKey, samePath, pathWithin, uniquePaths, pathValue, withPathValue, replacePathPrefix, pathMatchesChange } from "../lib/workspacePaths.js";
import { threadIsArchived, inspectThreadScopes, cachedThreadScopes, threadScopeKey } from "../lib/threadActivity.js";
import { loadEditorSession, saveEditorSession, loadWorkflowDraft, saveWorkflowDraft } from "../lib/editorSession.js";
import brandCompat from "../lib/brandCompat.js";
import { createConversationCache } from "../lib/conversationCache.js";
import { loadConversationMessages } from "../lib/conversationStorage.js";
import { CONVERSATION_PAGE_SIZE, conversationRepository } from "../lib/conversationRepository.js";
import ThreadSearch from "../components/ThreadSearch.jsx";
import ThreadHistoryMatch from "../components/ThreadHistoryMatch.jsx";
import { createConversationArchiveScheduler } from "../lib/conversationArchive.js";
import { startPolling, shareInFlight } from "../lib/refresh.js";
import { createRecentProjectValidator, startWorkspacePolling } from "../lib/projectRefresh.js";
import { equalJson } from "../lib/jsonValue.js";
import { providerPermissionDefault, providerPermissionOptions } from "../lib/providerPermissions.js";
import { generateConventionalCommit } from "../lib/commit-message.js";
import RemResources, { DEFAULT_REM_RESOURCES, remResourceError } from "../components/RemResources.jsx";
import RemAvatar from "../components/RemAvatar.jsx";
import { lazy, Suspense, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Code2,
  Copy,
  Download,
  FileArchive,
  FileDiff,
  FolderOpen,
  GitBranch,
  Globe2,
  History,
  Loader2,
  Moon,
  MoreVertical,
  Paperclip,
  Plus,
  PencilLine,
  Play,
  RefreshCw,
  Redo2,
  Search,
  Settings as SettingsIcon,
  Sun,
  Trash2,
  Undo2,
  Upload,
  Waypoints,
  X,
} from "lucide-react";
import { autoLayoutWorkflow } from "../lib/workflowLayout.js";
const DagCanvas = lazy(() => import("../components/DagCanvas.jsx"));
import { Dialog } from "../components/Dialog.jsx";
const SwarmWorkspace = lazy(() => import("../components/SwarmWorkspace.jsx"));
const CodeFileExplorer = lazy(() => import("../components/CodeFileExplorer.jsx"));
const CodeWorkspace = lazy(() => import("../components/CodeWorkspace.jsx"));
import { applyCodeFilesystemChange } from "../lib/codeEditorSessions.js";
import { resolveMarkdownFileLinkTarget, markdownFileLinkTarget } from "../lib/fileLinks.js";
import MarkdownContent from "../components/MarkdownContent.jsx";
import ChatComposer, { MessageAttachments } from "../components/ChatComposer.jsx";
const SettingsPopover = lazy(() => import("../components/SettingsPopover.jsx"));
import { defaultSettingsSnapshot } from "../lib/settings.js";
import RaticodeMark from "../components/RaticodeMark.jsx";
import UnifiedBottomPanel from "../components/UnifiedBottomPanel.jsx";
import RunSummary from "../components/RunSummary.jsx";
import { useWorkflowRunRegistry } from "../lib/useWorkflowRunRegistry.js";
import { exactWorkflowRunStopPath, workflowRunSummary } from "../lib/workflowRuns.js";
import {
  ProviderModelEffortFields,
  useProviderCapabilities,
} from "../components/ProviderModelEffortFields.jsx";
import { apiUrl } from "../lib/api.js";
import {
  chatMessageForRequest,
  clipboardAttachmentFiles,
  largePasteFile,
  readChatAttachments,
  transferContainsFiles,
  uploadChatAttachments,
} from "../lib/chatAttachments.js";
import {
  DEFAULT_APP_SETTINGS,
  formatKeybinding,
  loadAppSettings,
  matchesCommand,
  matchesKeybinding,
  reducedMotionEnabled,
  resolvedTheme,
  saveAppSettings,
  settingBinding,
  updateSetting,
} from "../lib/settings.js";

const RETENTION_STORAGE_KEY = "gofer.retentionSettings";
const PROJECT_LABELS_STORAGE_KEY = "gofer.projectLabels";
const RECENT_PROJECTS_STORAGE_KEY = "gofer.recentProjects";
export const RECENT_FILES_STORAGE_KEY = "gofer.recentFiles";
const LAST_WORKTREE_STORAGE_KEY = "gofer.lastWorktreeByProject";
export const STUDIO_SESSION_STORAGE_KEY = "raticode.studioSession.v1";
export const TEXT_ZOOM_STORAGE_KEY = "raticode.textZoom.v1";
export const TEXT_ZOOM_MIN = 80;
export const TEXT_ZOOM_MAX = 150;
export const TEXT_ZOOM_STEP = 10;
const DEFAULT_RETENTION_SETTINGS = {
  keepDays: 14,
  keepFailedDays: 30,
  keepLast: 100,
};
const RUN_LOG_TAIL_BYTES = 64 * 1024;
const RATTISH_ANALYSIS_DELAY_MS = 300;
let browserTabSequence = 0;

export function prefersReducedMotion() {
  if (typeof document !== "undefined" && document.documentElement?.dataset?.reducedMotion) {
    return document.documentElement?.dataset?.reducedMotion === "true";
  }
  return typeof window !== "undefined" &&
    (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
}

function loadRetentionSettings() {
  if (typeof window === "undefined") return DEFAULT_RETENTION_SETTINGS;
  try {
    const stored = window.localStorage?.getItem(RETENTION_STORAGE_KEY);
    if (!stored) return DEFAULT_RETENTION_SETTINGS;
    const parsed = JSON.parse(stored);
    if (!parsed || typeof parsed !== "object") return DEFAULT_RETENTION_SETTINGS;
    return {
      keepDays: Number.isFinite(parsed.keepDays)
        ? parsed.keepDays
        : DEFAULT_RETENTION_SETTINGS.keepDays,
      keepFailedDays: Number.isFinite(parsed.keepFailedDays)
        ? parsed.keepFailedDays
        : DEFAULT_RETENTION_SETTINGS.keepFailedDays,
      keepLast: Number.isFinite(parsed.keepLast)
        ? parsed.keepLast
        : DEFAULT_RETENTION_SETTINGS.keepLast,
    };
  } catch {
    return DEFAULT_RETENTION_SETTINGS;
  }
}

function isBundleFile(file) {
  const name = file?.name?.toLowerCase?.() ?? "";
  return name.endsWith(".zip") || name.endsWith(".gof");
}

function isRaticodeFile(file) {
  return brandCompat.isWorkflowBundle(file);
}

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return window.btoa(binary);
}

function formatBundleImportPreview(plan) {
  const manifest = plan.manifest ?? {};
  const promptPaths = (manifest.includedPaths ?? [])
    .filter((item) => item.kind === "prompt" || item.kind === "prompt_template")
    .map((item) => `${item.path}${item.kind === "prompt_template" ? " (template)" : ""}`);
  const lines = [
    `Import bundle "${plan.workflowName}" as ${plan.workflowId}?`,
    "",
    "Files to create:",
    ...previewLines(plan.filesToCreate),
    "",
    "Files to overwrite:",
    ...previewLines(plan.filesToOverwrite),
    "",
    "Agents and providers:",
    ...previewProviderLines(manifest.providerAssumptions),
    "",
    "Prompts:",
    ...previewLines(promptPaths),
    "",
    "Triggers:",
    ...previewTriggerLines(manifest.triggers),
  ];
  if (plan.conflicts?.length) {
    lines.push("", "Conflicts:", ...plan.conflicts.map((item) => `- ${item.path}: ${item.action}`));
  }
  if (plan.requiredSecrets?.length) {
    lines.push("", "Required secrets:", ...plan.requiredSecrets.map((item) => `- ${item.name}`));
  }
  if (plan.externalRequirements?.length) {
    lines.push(
      "",
      "External requirements:",
      ...plan.externalRequirements.map((item) => `- ${item.path}: ${item.reason}`),
    );
  }
  return lines.join("\n");
}

function previewLines(items = []) {
  return items.length ? items.map((item) => `- ${item}`) : ["- None"];
}

function previewProviderLines(items = []) {
  if (!items.length) return ["- None"];
  return items.map((item) => {
    const details = [item.subscription, item.profile && `profile ${item.profile}`, item.model].filter(
      Boolean,
    );
    return `- ${item.agentId}: ${details.join(", ")}`;
  });
}

function previewTriggerLines(items = []) {
  if (!items.length) return ["- None"];
  return items.map((item) => {
    if (item.type === "schedule") {
      return `- schedule: ${item.cron} (${item.timezone})`;
    }
    if (item.type === "watch") {
      return `- watch: ${item.path} ${item.glob} (${item.mode})`;
    }
    if (item.type === "webhook") {
      const details = [item.source, item.enabled === "true" ? "enabled" : "disabled"];
      if (item.tokenEnv) details.push(`secret ${item.tokenEnv}`);
      if (item.fanoutPath) details.push(`fanout ${item.fanoutPath}`);
      return `- webhook ${item.id}: ${details.join(", ")}`;
    }
    return `- ${item.type ?? "trigger"}`;
  });
}

export function workflowBundleFilename(workflow) {
  const extension = workflow?.sourceFormat === "rattish" ? ".raticode" : ".gof.zip";
  return `${workflow?.id || "workflow"}${extension}`;
}

export function workflowBundlePath(directory, workflow) {
  const value = String(directory ?? "").trim();
  if (!value) return workflowBundleFilename(workflow);
  const separator = value.includes("\\") && !value.includes("/") ? "\\" : "/";
  const trimmedDirectory = value.replace(/[\\/]+$/, "");
  return `${trimmedDirectory || separator}${trimmedDirectory ? separator : ""}${workflowBundleFilename(workflow)}`;
}

export function workflowExportEndpoint(workflow) {
  return workflow?.sourceFormat === "rattish"
    ? `/rattish/workflows/${encodeURIComponent(workflow.id)}/export`
    : `/workflows/${encodeURIComponent(workflow.id)}/export`;
}

export async function withProjectOpenTimeout(operation, timeoutMs = 30000) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Opening the project timed out. Try opening the folder again.")), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function discoverProjectWorkflows(projectRoot, { signal, onResolvedRoot, onTrustedRoot } = {}) {
  const normalizedRoot = String(projectRoot ?? "").trim();
  if (!normalizedRoot) return [];

  const trustedRoot = await window.goferDesktop?.workspace?.trustProjectRoot?.(normalizedRoot);
  signal?.throwIfAborted();
  // Desktop validation completes before the backend discovers and compiles workflows.
  if (typeof trustedRoot === "string" && trustedRoot) onTrustedRoot?.(trustedRoot);
  const projectGrantId =
    window.goferDesktop?.workspace?.pathGrantForApi?.(normalizedRoot) ?? "";
  const response = await fetch(apiUrl("/projects/open"), {
    signal,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      projectGrantId: projectGrantId || undefined,
      projectRoot: normalizedRoot,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Project API returned ${response.status}`);
  }
  if (typeof payload.projectRoot === "string" && payload.projectRoot) onResolvedRoot?.(payload.projectRoot);
  return Array.isArray(payload.workflows) ? payload.workflows : [];
}

export default function App() {
  const [settings, setSettings] = useState(loadAppSettings);
  const [initialStudioSession] = useState(loadStudioSession);
  const [initialEditorSession] = useState(loadEditorSession);
  const [textZoom, setTextZoom] = useState(loadTextZoom);
  useEffect(() => {
    const bridge = window.goferDesktop?.rem;
    if (!bridge) return;
    void bridge.settings().then((memory) => {
      setSettings((current) => ({ ...current, memory: { ...current.memory, ...memory } }));
    }).catch(reportArchiveError);
  }, []);
  useEffect(() => {
    function archiveError(event) { setTopBarNotice({ type: "error", message: `Conversation archive: ${event.detail}. Rem still keeps its local history.` }); }
    window.addEventListener("gofer:archive-error", archiveError);
    return () => window.removeEventListener("gofer:archive-error", archiveError);
  }, []);
  useEffect(() => {
    if (settings.memory.archiveFolder) void archiveAllConversations();
  }, [settings.memory.archiveFolder]);
  useEffect(() => {
    if (settings.memory.secondBrainEnabled && settings.memory.secondBrainRoot) {
      setRecentProjectRoots((current) => mergeRecentProjects([settings.memory.secondBrainRoot], current));
    }
  }, [settings.memory.secondBrainEnabled, settings.memory.secondBrainRoot]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState("general");
  useEffect(() => {
    const openProviders = () => { setSettingsCategory("providers"); setSettingsOpen(true); };
    window.addEventListener("raticode:open-provider-settings", openProviders);
    return () => window.removeEventListener("raticode:open-provider-settings", openProviders);
  }, []);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );
  const [workflows, setWorkflows] = useState([]);
  const [promptAgentIds, setPromptAgentIds] = useState([]);
  const [activeWorkflowId, setActiveWorkflowId] = useState(initialStudioSession.workflowId || undefined);
  const activeWorkflowIdRef = useRef(activeWorkflowId);
  activeWorkflowIdRef.current = activeWorkflowId;
  const [openingProjectRoot, setOpeningProjectRoot] = useState("");
  const [projectError, setProjectError] = useState("");
  const [activeProjectRoot, setActiveProjectRoot] = useState(initialStudioSession.projectRoot);
  const [selectedSwarm, setSelectedSwarm] = useState(null);
  const [studioView, setStudioView] = useState(initialStudioSession.view || settings.general.defaultView);
  const [, setCodeEditorOpened] = useState(
    (initialStudioSession.view || settings.general.defaultView) === "code",
  );
  const [codeOpenPaths, setCodeOpenPaths] = useState(initialEditorSession?.paths || []);
  const [activeCodePath, setActiveCodePath] = useState(initialEditorSession?.activePath || "");
  const [previewCodePath, setPreviewCodePath] = useState("");
  const [codeNavigationRequest, setCodeNavigationRequest] = useState(null);
  const [browserTabs, setBrowserTabs] = useState(initialEditorSession?.browserTabs || {});
  const [newCodeFileRequest, setNewCodeFileRequest] = useState(0);
  const [recentCodePaths, setRecentCodePaths] = useState(loadRecentCodePaths);
  const [recentProjectRoots, setRecentProjectRoots] = useState(loadRecentProjectRoots);
  const [lastWorktreeByProject, setLastWorktreeByProject] = useState(loadLastWorktreeByProject);
  const [rattishSessions, setRattishSessions] = useState({});
  const documentSessionsRef = useRef(new Map());
  const documentSession = useCallback((workflowId) => {
    if (!documentSessionsRef.current.has(workflowId)) documentSessionsRef.current.set(workflowId, {
      state: { current: null }, metadataTimer: { current: null }, metadataPending: { current: false },
      analysisTimer: { current: null }, analysisRequest: { current: 0 },
      writes: { current: Promise.resolve() }, metadataSaving: { current: null },
    });
    const session = documentSessionsRef.current.get(workflowId);
    return {
      documentWritesRef: session.writes,
      rattishMetadataSavingRef: session.metadataSaving,
      rattishEditorStateRef: session.state,
      rattishMetadataSaveTimerRef: session.metadataTimer,
      rattishMetadataPendingRef: session.metadataPending,
      rattishAnalysisTimerRef: session.analysisTimer,
      rattishAnalysisRequestRef: session.analysisRequest,
      setRattishEditorState(update) {
        const next = typeof update === "function" ? update(session.state.current) : update;
        const draftSaved = saveWorkflowDraft(next?.document?.sourcePath || workflows.find(item => item.id === workflowId)?.sourcePath, next?.document);
        const recoveryWarning = draftSaved ? "" : next?.document?.dirty
          ? "Recovery storage is unavailable. Keep this tab open and save workflow.rattish before restarting the app."
          : "Recovery storage could not be cleared. An older draft may appear after restarting the app.";
        const state = next ? { ...next, recoveryWarning } : next;
        session.state.current = state;
        setRattishSessions(current => ({ ...current, [workflowId]: state }));
      },
    };
  }, [workflows]);
  const { rattishEditorStateRef, setRattishEditorState } = documentSession(activeWorkflowId);
  const rattishEditorState = rattishSessions[activeWorkflowId] ?? null;
  const [workflowTabs, setWorkflowTabs] = useState(initialEditorSession?.workflowTabs || {});
  const [sidebarActivity, setSidebarActivity] = useState(initialEditorSession?.activity || (initialStudioSession.view === "code" ? "files" : settings.general.initialActivity || "workflows"));

  const [workflowClosePrompt, setWorkflowClosePrompt] = useState(null);
  const [activeCodeDocumentState, setActiveCodeDocumentState] = useState(null);
  const { projectPaneVisible, setProjectPaneVisible, assistantPaneVisible, setAssistantPaneVisible, closeCompactPane } = useResponsivePanes();
  const [assistantFocusRequest, setAssistantFocusRequest] = useState(0);
  useEffect(() => {
    const openRem = () => { setAssistantPaneVisible(true); setAssistantFocusRequest(current => current + 1); };
    window.addEventListener("gofer:rem-context", openRem);
    return () => window.removeEventListener("gofer:rem-context", openRem);
  }, [setAssistantPaneVisible]);
  const [dataDir, setDataDir] = useState("");
  const [loadState, setLoadState] = useState({ loading: true, error: "" });
  const [doctorState, setDoctorState] = useState({
    loading: true,
    error: "",
    errors: [],
    warnings: [],
  });
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [createState, setCreateState] = useState({ saving: false, error: "" });
  const [exportDialog, setExportDialog] = useState({
    directory: "",
    error: "",
    grantId: "",
    saving: false,
    workflow: null,
  });
  const [historyState, setHistoryState] = useState({
    diff: null,
    error: "",
    loading: false,
    open: false,
    revisions: [],
  });
  const [dirtyWorkflowsById, setDirtyWorkflowsById] = useState({});
  const [saveStatesByWorkflowId, setSaveStatesByWorkflowId] = useState({});
  const [topBarNotice, setTopBarNotice] = useState({ type: "", message: "" });
  const [runPreview, setRunPreview] = useState(null);
  const [queueState, setQueueState] = useState({ runners: [], runs: [], error: "" });
  const [retentionSettings, setRetentionSettings] = useState(loadRetentionSettings);
  const [updateState, setUpdateState] = useState({
    available: false,
    checking: false,
    error: "",
    info: null,
  });
  const [runState, setLatestRunState] = useState({ running: false, error: "", result: null });
  const [runStatesById, setRunStatesById] = useState({});
  const runStatesRef = useRef({});
  const latestRunStateRef = useRef(runState);
  const runRegistry = useWorkflowRunRegistry(workflows);
  const recordWorkflowRun = runRegistry.record;
  const [runsOpen, setRunsOpen] = useState(false);
  const [pinnedRun, setPinnedRun] = useState(initialEditorSession?.pinnedRun || null);
  useEffect(() => {
    saveEditorSession({ paths: codeOpenPaths, activePath: activeCodePath, workflowTabs, browserTabs, activity: sidebarActivity, pinnedRun });
  }, [codeOpenPaths, activeCodePath, workflowTabs, browserTabs, sidebarActivity, pinnedRun]);
  const pinnedRunRef = useRef(pinnedRun);
  pinnedRunRef.current = pinnedRun;
  const setRunState = useCallback((update) => {
    const next = typeof update === "function" ? update(latestRunStateRef.current) : update;
    latestRunStateRef.current = next;
    if (next.workflowId) {
      runStatesRef.current = { ...runStatesRef.current, [next.workflowId]: next };
      setRunStatesById(runStatesRef.current);
    }
    setLatestRunState(next);
  }, []);
  useEffect(() => {
    for (const [id, state] of Object.entries(runStatesById)) {
      if (state.result) {
        const workflow = workflows.find(item => item.id === id);
        if (workflow) recordWorkflowRun(workflow, state.result);
      }
    }
  }, [runStatesById, workflows, recordWorkflowRun]);
  const [logState, setLogState] = useState({
    loading: false,
    error: "",
    text: "",
    path: null,
    nodeOutputs: null,
    nodeOutputsTruncated: false,
    nodeOutputsMaxBytes: null,
    usageSummary: null,
    runEvents: [],
    runNodes: {},
    runs: [],
    selectedRunId: null,
  });
  const [approvalState, setApprovalState] = useState({
    approvals: [],
    error: "",
    loading: false,
  });
  const workflowRevisionsRef = useRef(new Map());
  const dirtyWorkflowsRef = useRef(new Map());
  const saveTimersRef = useRef(new Map());
  const inFlightSavesRef = useRef(new Map());
  const deletedWorkflowIdsRef = useRef(new Set());
  const logRequestRef = useRef(0);
  const logListRequestRef = useRef(0);
  const approvalRequestRef = useRef(0);
  const rattishEditorRef = useRef(null);
  const previewCodePathRef = useRef("");
  const codeNavigationSequenceRef = useRef(0);
  const pendingProjectFileRef = useRef(null);
  const workflowLoadRequestRef = useRef(0);
  const projectOpenRequestRef = useRef(0);
  const projectOpenAbortRef = useRef(null);
  const recentProjectValidatorRef = useRef(null);
  if (!recentProjectValidatorRef.current) recentProjectValidatorRef.current = createRecentProjectValidator();
  useEffect(() => () => projectOpenAbortRef.current?.abort(), []);
  const openProjectFolderRef = useRef(null);
  const openFileRef = useRef(null);
  const chordPendingRef = useRef(null);
  const openIntegratedBrowserRef = useRef(null);
  const openLinkedCodeFileRef = useRef(null);
  const terminalEditorRequestsRef = useRef(new Map());
  const changeStudioViewRef = useRef(null);
  const runWorkflowNowRef = useRef(null);
  const theme = resolvedTheme(settings, systemDark);
  const workflowPaneWidth = settings.layout.workflowPaneWidth;
  const chatPaneWidth = settings.layout.assistantPaneWidth;
  const executionMode = settings.general.executionMode;
  const activeWorkflow = workflows.find(item => item.id === activeWorkflowId);
  const browsingWorkspace = activeWorkspaceForProject(workflows, undefined, activeProjectRoot) ?? projectWorkspace(activeProjectRoot);
  const swarmProjectRoot = browsingWorkspace?.projectRoot || activeProjectRoot || "";
  const codeWorkspaceWorkflow = workflows.find(item => samePath(item.sourcePath, activeCodePath)) ?? activeWorkflow ?? browsingWorkspace;


  const changeSetting = useCallback((path, value) => {
    setSettings((current) => updateSetting(current, path, value));
  }, []);
  changeStudioViewRef.current = changeStudioView;
  runWorkflowNowRef.current = runWorkflowNow;

  useEffect(() => {
    rattishEditorStateRef.current = rattishEditorState;
  }, [rattishEditorState, rattishEditorStateRef]);

  useEffect(() => {
    previewCodePathRef.current = previewCodePath;
  }, [previewCodePath]);

  useEffect(() => {
    const timer = window.setTimeout(() => saveAppSettings(settings), 120);
    return () => window.clearTimeout(timer);
  }, [settings]);

  useLayoutEffect(() => {
    const zoomFactor = textZoom / 100;
    const setNativeZoom = window.goferDesktop?.appearance?.setZoomFactor;
    if (setNativeZoom) {
      setNativeZoom(zoomFactor);
      document.documentElement.style.fontSize = "";
      return;
    }
    document.documentElement.style.fontSize = `${textZoom}%`;
  }, [textZoom]);

  useEffect(() => () => {
    window.goferDesktop?.appearance?.setZoomFactor?.(1);
    document.documentElement.style.fontSize = "";
  }, []);

  useEffect(() => {
    try {
      window.localStorage?.setItem(TEXT_ZOOM_STORAGE_KEY, String(textZoom));
    } catch {
      // Text zoom remains available for this session when storage is unavailable.
    }
  }, [textZoom]);

  useEffect(() => {
    function handleTextZoomKeydown(event) {
      const direction = textZoomDirection(event);
      if (!direction) return;
      event.preventDefault();
      if (eventTargetsGraphVisualization(event)) return;
      setTextZoom((current) => nextTextZoom(current, direction));
    }

    function handleTextZoomWheel(event) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.deltaY === 0) return;
      event.preventDefault();
      if (eventTargetsGraphVisualization(event)) return;
      setTextZoom((current) => nextTextZoom(current, event.deltaY < 0 ? 1 : -1));
    }

    window.addEventListener("keydown", handleTextZoomKeydown, true);
    window.addEventListener("wheel", handleTextZoomWheel, { capture: true, passive: false });
    return () => {
      window.removeEventListener("keydown", handleTextZoomKeydown, true);
      window.removeEventListener("wheel", handleTextZoomWheel, true);
    };
  }, []);

  useEffect(() => {
    const colorScheme = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!colorScheme) return undefined;
    const updateSystemTheme = (event) => setSystemDark(event.matches);
    colorScheme.addEventListener?.("change", updateSystemTheme);
    return () => colorScheme.removeEventListener?.("change", updateSystemTheme);
  }, []);

  useEffect(() => {
    const reduceMotion = reducedMotionEnabled(
      settings,
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    );
    document.documentElement.classList?.toggle?.("dark", theme === "dark");
    if (document.documentElement.dataset) {
      document.documentElement.dataset.reducedMotion = String(reduceMotion);
    }
    try {
      window.localStorage?.setItem("gofer-ui-theme", theme);
    } catch {
      // The selected theme still applies for this session.
    }
  }, [settings, theme]);

  useEffect(() => {
    try {
      window.localStorage?.setItem(
        RECENT_PROJECTS_STORAGE_KEY,
        JSON.stringify(recentProjectRoots),
      );
    } catch {
      // Recent projects remain available for this session when storage is unavailable.
    }
  }, [recentProjectRoots]);

  useEffect(() => {
    try {
      window.localStorage?.setItem(
        RECENT_FILES_STORAGE_KEY,
        JSON.stringify(recentCodePaths),
      );
    } catch {
      // Recent files remain available for this session when storage is unavailable.
    }
  }, [recentCodePaths]);

  useEffect(() => {
    const inspect = window.goferDesktop?.workspace?.missingRecentFiles;
    if (activeCodePath || !recentCodePaths.length || !inspect) return undefined;
    let cancelled = false;
    const stop = startPolling(async () => {
      const missing = new Set(await inspect(recentCodePaths));
      if (!cancelled && missing.size) {
        setRecentCodePaths(current => current.filter(path => !missing.has(path)));
      }
    }, { immediate: true, interval: 5000 });
    return () => { cancelled = true; stop(); };
  }, [activeCodePath, recentCodePaths]);

  useEffect(() => {
    if (!activeCodePath || activeCodePath.startsWith("workflow-graph:") || activeCodePath.startsWith("browser:") || browserTabs[activeCodePath]) return;
    setRecentCodePaths((current) => rememberRecentFile(current, activeCodePath));
  }, [activeCodePath, browserTabs]);

  useEffect(() => {
    try {
      window.localStorage?.setItem(
        LAST_WORKTREE_STORAGE_KEY,
        JSON.stringify(lastWorktreeByProject),
      );
    } catch {
      // Worktree selection remains available for this session when storage is unavailable.
    }
  }, [lastWorktreeByProject]);

  useEffect(() => {
    saveStudioSession({
      projectRoot: activeProjectRoot,
      view: studioView,
      workflowId: activeWorkflowId,
    });
  }, [activeProjectRoot, activeWorkflowId, studioView]);

  useEffect(() => {
    const getPathInfo = window.goferDesktop?.workspace?.getPathInfo;
    const roots = [...new Set([...recentProjectRoots, activeProjectRoot].filter(Boolean))];
    if (!getPathInfo || !roots.length) return undefined;
    let cancelled = false;
    const stop = startPolling(() => Promise.all(roots.map((projectRoot) =>
      recentProjectValidatorRef.current.validate(
        projectRoot,
        pathValue(lastWorktreeByProject, projectRoot) || projectRoot,
        window.goferDesktop.workspace,
        mainWorktreeRoot,
      ),
    )).then((projects) => {
      if (cancelled) return;
      const existingProjects = projects.filter((project, index) => project && recentProjectRoots.some(root => samePath(root, roots[index])));
      const missingRoots = roots.flatMap((root, index) => {
        const selected = pathValue(lastWorktreeByProject, root) || root;
        return !projects[index] || projects[index].selectedProjectRoot !== selected ? [selected] : [];
      });
      if (missingRoots.length) {
        setWorkflows((current) => current.filter((workflow) => !missingRoots.some(root => samePath(root, workflow.projectRoot))));
        if (missingRoots.some(root => samePath(root, activeProjectRoot))) {
          const replacementIndex = roots.findIndex((root) => samePath(pathValue(lastWorktreeByProject, root), activeProjectRoot));
          const replacement = projects[replacementIndex]?.selectedProjectRoot || "";
          setActiveProjectRoot(replacement === activeProjectRoot ? "" : replacement);
          // Open documents keep their original project context.

        }
      }
      const nextRoots = mergeRecentProjects(
        [],
        existingProjects.map((project) => project.mainProjectRoot),
      );
      const selections = Object.fromEntries(existingProjects.map((project) => [
        project.mainProjectRoot,
        project.selectedProjectRoot,
      ]));
      setLastWorktreeByProject((current) => (
        Object.keys(current).length === Object.keys(selections).length
          && Object.entries(selections).every(([root, selected]) => current[root] === selected)
          ? current : selections
      ));
      setRecentProjectRoots((current) => (
        current.length === nextRoots.length
          && current.every((root, index) => root === nextRoots[index])
          ? current
          : nextRoots
      ));
    }), { immediate: true });
    return () => {
      cancelled = true;
      stop();
    };
  }, [activeProjectRoot, lastWorktreeByProject, recentProjectRoots]);

  useEffect(() => {
    function runShortcutAction(action) {
      if (action === "settings.open") setSettingsOpen(true);
      if (action === "file.open") void openFileRef.current?.();
      if (action === "project.open") void openProjectFolderRef.current?.();
      if (action === "browser.open") openIntegratedBrowserRef.current?.(undefined, { newTab: true });
      if (action === "view.graph") changeStudioViewRef.current?.("graph");
      if (action === "view.code") changeStudioViewRef.current?.("code");
      if (action === "view.toggleProjectPane") setProjectPaneVisible((current) => !current);
      if (action === "view.toggleAssistantPane") setAssistantPaneVisible((current) => !current);
      if (action === "terminal.new") window.dispatchEvent(new CustomEvent("gofer:new-terminal"));
      if (action === "panel.toggle") window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel"));
      if (action === "workflow.run" && activeWorkflow && !runState.running
        && (workflowTabs[activeCodePath]?.workflowId === activeWorkflow.id || activeCodePath === activeWorkflow.sourcePath)) {
        void runWorkflowNowRef.current?.(activeWorkflow);
      }
    }
    function clearChordPending() {
      window.clearTimeout(chordPendingRef.current?.timeoutId);
      chordPendingRef.current = null;
    }
    function handleApplicationShortcut(event) {
      if (settingsOpen) return;
      const pending = chordPendingRef.current;
      if (pending) {
        clearChordPending();
        if (matchesKeybinding(event, pending.secondSegment)) {
          event.preventDefault();
          event.stopPropagation();
          runShortcutAction(pending.commandId);
          return;
        }
        // Not the expected second key: fall through and evaluate this keydown normally.
      }
      let action = "";
      for (const commandId of [
        "settings.open",
        "file.open",
        "project.open",
        "browser.open",
        "view.graph",
        "view.code",
        "view.toggleProjectPane",
        "view.toggleAssistantPane",
        "workflow.run",
      ]) {
        const segments = settingBinding(settings, commandId).split(" ").filter(Boolean);
        if (segments.length > 1) {
          if (matchesKeybinding(event, segments[0])) {
            event.preventDefault();
            event.stopPropagation();
            chordPendingRef.current = {
              commandId,
              secondSegment: segments[1],
              timeoutId: window.setTimeout(clearChordPending, 1500),
            };
            return;
          }
          continue;
        }
        if (matchesCommand(event, settings, commandId)) {
          action = commandId;
          break;
        }
      }
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      runShortcutAction(action);
    }
    const unsubscribeCommand = window.goferBrowser?.onCommand?.((command) => {
      if (command?.action === "application-shortcut" && command.commandId) {
        runShortcutAction(command.commandId);
      }
      if (command?.action === "text-zoom" && Number.isFinite(command.direction)) {
        setTextZoom((current) => nextTextZoom(current, command.direction));
      }
      if (command?.action === "text-zoom" && command.reset === true) {
        setTextZoom(100);
      }
      if (command?.action === "open-browser") runShortcutAction("browser.open");
      if (command?.action === "settings-open") runShortcutAction("settings.open");
      if (command?.action === "file-open") runShortcutAction("file.open");
      if (command?.action === "project-open") runShortcutAction("project.open");
      if (command?.action === "panel-toggle") runShortcutAction("panel.toggle");
      if (command?.action === "project-pane-toggle") runShortcutAction("view.toggleProjectPane");
      if (command?.action === "assistant-pane-toggle") runShortcutAction("view.toggleAssistantPane");
    });
    const unsubscribeOpenTab = window.goferBrowser?.onOpenTab?.((request) => {
      if (request?.url) openIntegratedBrowserRef.current?.(request.url, { newTab: true });
    });
    const unsubscribeOpenFile = window.goferBrowser?.onOpenFile?.((request) => {
      const target = request?.href ? markdownFileLinkTarget("", request.href) : request;
      if (target?.path) void openLinkedCodeFileRef.current?.(target.path, { ...target, preview: true });
    });
    const unsubscribeTerminalEditor = window.goferTerminal?.onOpenEditor?.((request) => {
      if (!request?.path || !request?.requestId) return;
      const requests = terminalEditorRequestsRef.current.get(request.path) ?? new Set();
      requests.add(request.requestId);
      terminalEditorRequestsRef.current.set(request.path, requests);
      void openLinkedCodeFileRef.current?.(request.path);
    });
    window.addEventListener("keydown", handleApplicationShortcut, true);
    return () => {
      clearChordPending();
      unsubscribeCommand?.();
      unsubscribeOpenFile?.();
      unsubscribeOpenTab?.();
      unsubscribeTerminalEditor?.();
      window.removeEventListener("keydown", handleApplicationShortcut, true);
    };
  }, [activeWorkflow, activeCodePath, workflowTabs, runState.running, settings, settingsOpen, setAssistantPaneVisible, setProjectPaneVisible]);

  useEffect(() => () => {
    for (const session of documentSessionsRef.current.values()) {
      window.clearTimeout(session.analysisTimer.current);
      window.clearTimeout(session.metadataTimer.current);
      session.analysisRequest.current += 1;
    }
  }, []);

  useEffect(() => {
    const pending = pendingProjectFileRef.current;
    const pendingPath = pendingCodePathForWorkflow(pending, activeWorkflow?.id);
    if (!pendingPath) return;
    setCodeOpenPaths((current) => mergeCodeOpenPaths(current, [pendingPath]));
    setActiveCodePath(pendingPath);
    pendingProjectFileRef.current = null;
  }, [activeWorkflow?.id]);

  const scheduleRattishAnalysisRef = useRef(null);
  scheduleRattishAnalysisRef.current = scheduleRattishAnalysis;
  useEffect(() => {
    const targets = workflows.filter(item => item.sourceFormat === "rattish" &&
      (item.id === activeWorkflowId || codeOpenPaths.includes(item.sourcePath) || Object.values(workflowTabs).some(tab => tab.workflowId === item.id)));
    for (const target of targets) {
      const session = documentSession(target.id);
      if (session.rattishEditorStateRef.current) continue;
      session.setRattishEditorState({ document: null, loading: true, error: "", saving: false });
      fetch(apiUrl(`/workflows/${encodeURIComponent(target.id)}/document`))
        .then(async response => {
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || `Editor API returned ${response.status}`);
          const draft = loadWorkflowDraft(target.sourcePath);
          const document = draft && draft.source !== payload.document.source
            ? { ...payload.document, source: draft.source, savedSource: payload.document.source, savedRevision: draft.savedRevision, dirty: true, runnable: false }
            : payload.document;
          session.setRattishEditorState({ document, error: "", loading: false, saving: false });
          if (document.dirty) scheduleRattishAnalysisRef.current(document.source, target.sourcePath);
        }).catch(error => session.setRattishEditorState({ document: null, error: error.message, loading: false, saving: false }));
    }
  }, [activeWorkflowId, workflows, codeOpenPaths, workflowTabs, documentSession]);

  function openWorkflowGraph(target) {
    if (!target?.id) return;
    setSelectedSwarm(null);
    const path = `workflow-graph:${encodeURIComponent(target.id)}`;
    setWorkflowTabs(current => ({ ...current, [path]: { kind: "workflow", workflowId: target.id,
      name: target.name, sourcePath: target.sourcePath, projectRoot: target.projectRoot, contextLabel: target.projectRoot } }));
    setCodeOpenPaths(current => mergeCodeOpenPaths(current, [path]));
    setActiveCodePath(path);
    setActiveWorkflowId(target.id);
    setCodeEditorOpened(true);
    setStudioView("graph");
  }

  const workflowViewCounterRef = useRef(0);
  function duplicateWorkflowTab(path) {
    const tab = workflowTabs[path];
    if (!tab) return "";
    const viewPath = `workflow-graph:${encodeURIComponent(tab.workflowId)}:view:${Date.now()}-${++workflowViewCounterRef.current}`;
    setWorkflowTabs(current => ({ ...current, [viewPath]: { ...tab } }));
    setCodeOpenPaths(current => mergeCodeOpenPaths(current, [viewPath]));
    return viewPath;
  }

  const initializedGraphRef = useRef(Boolean(initialEditorSession));
  useEffect(() => {
    if (initializedGraphRef.current || loadState.loading) return;
    initializedGraphRef.current = true;
    if ((initialStudioSession.view || settings.general.defaultView) === "graph" && activeWorkflow) openWorkflowGraph(activeWorkflow);
  }, [loadState.loading, activeWorkflow, initialStudioSession.view, settings.general.defaultView]);

  useEffect(() => {
    const tab = workflowTabs[activeCodePath];
    const target = tab ? workflows.find(item => item.id === tab.workflowId) : workflows.find(item => samePath(item.sourcePath, activeCodePath));
    if (target && target.id !== activeWorkflowId) setActiveWorkflowId(target.id);
    if (activeCodePath) setStudioView(tab ? "graph" : "code");
  }, [activeCodePath, activeWorkflowId, workflowTabs, workflows]);

  function activateEditorPath(path) {
    setSelectedSwarm(null);
    setActiveCodePath(path);
    const target = workflowTabs[path] ? workflows.find(item => item.id === workflowTabs[path].workflowId)
      : workflows.find(item => samePath(item.sourcePath, path));
    if (target) setActiveWorkflowId(target.id);
    setStudioView(workflowTabs[path] ? "graph" : "code");
  }

  function changeStudioView(nextView) {
    if (nextView === "graph") { openWorkflowGraph(activeWorkflow); return; }
    setStudioView("code");
    setCodeEditorOpened(true);
    if (workflowTabs[activeCodePath] && activeWorkflow?.sourcePath) openCodeFile(activeWorkflow.sourcePath);
  }

  function openCodeFile(path, options = {}) {
    if (!path) return;
    setSelectedSwarm(null);
    const currentPreview = previewCodePathRef.current;
    const next = nextCodeFileOpenState(
      codeOpenPaths,
      currentPreview,
      path,
      options.preview === true,
    );
    setCodeOpenPaths(next.openPaths);
    previewCodePathRef.current = next.previewPath;
    setPreviewCodePath(next.previewPath);
    setActiveCodePath(path);
    if (options.diff || (Number.isInteger(options.lineNumber) && options.lineNumber > 0)) {
      codeNavigationSequenceRef.current += 1;
      setCodeNavigationRequest({
        column: Number.isInteger(options.column) && options.column > 0 ? options.column : 1,
        diff: options.diff === true,
        gitGroup: options.gitGroup,
        lineNumber: options.lineNumber || 1,
        path,
        requestId: codeNavigationSequenceRef.current,
      });
    } else {
      codeNavigationSequenceRef.current += 1;
      setCodeNavigationRequest({ path, requestId: codeNavigationSequenceRef.current, diff: false });
    }
    setCodeEditorOpened(true);
    setStudioView("code");
  }

  function openIntegratedBrowser(initialUrl, options = {}) {
    const requestedUrl = initialUrl || settings.browser.homepage || "about:blank";
    const openBrowserPaths = codeOpenPaths.filter((path) => Boolean(browserTabs[path]));
    if (!options.newTab && openBrowserPaths.length) {
      const targetPath = browserTabs[activeCodePath]
        ? activeCodePath
        : openBrowserPaths.at(-1);
      setActiveCodePath(targetPath);
      setCodeEditorOpened(true);
      setStudioView("code");
      return targetPath;
    }
    const path = createBrowserTabPath();
    setBrowserTabs((current) => ({
      ...current,
      [path]: {
        error: "",
        focusLocation: options.focusLocation ?? !initialUrl,
        loading: true,
        title: "",
        url: requestedUrl,
      },
    }));
    setCodeOpenPaths((current) => mergeCodeOpenPaths(current, [path]));
    setActiveCodePath(path);
    setCodeEditorOpened(true);
    setStudioView("code");
    return path;
  }

  openIntegratedBrowserRef.current = openIntegratedBrowser;

  function updateBrowserTab(path, nextState) {
    setBrowserTabs((current) => current[path]
      ? { ...current, [path]: { ...current[path], ...nextState } }
      : current);
  }

  async function openMarkdownFileLink(href, sourcePath) {
    try {
      const target = await resolveMarkdownFileLinkTarget(
        sourcePath,
        href,
      );
      if (target?.path) await openLinkedCodeFile(target.path, { ...target, preview: true });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Could not open the linked file",
      });
    }
  }

  async function openLinkedCodeFile(path, options = {}) {
    if (!path) return;
    try {
      const selected = await window.goferDesktop?.workspace?.grantUserPath?.(path);
      path = selected?.path || path;
      const info = await window.goferDesktop?.workspace?.getPathInfo?.(path);
      if (info && !info.isFile) throw new Error(`The link does not point to a file: ${path}`);
      openCodeFile(info?.path || path, options);
    } catch (error) {
      if (options.recent) {
        try {
          const missing = await window.goferDesktop?.workspace?.missingRecentFiles?.([path]);
          if (missing?.includes(path)) {
            setRecentCodePaths(current => current.filter(candidate => candidate !== path));
            setTopBarNotice({ type: "error", message: `File no longer exists. Removed from recent files: ${path}` });
            return;
          }
        } catch { /* Keep history when existence cannot be checked. */ }
      }
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Could not open the linked file",
      });
    }
  }

  openLinkedCodeFileRef.current = openLinkedCodeFile;

  async function openRecentCodeFile(path) {
    await openLinkedCodeFile(path, { preview: false, recent: true });
  }

  async function openAssistantFile(path, projectRoot) {
    const resolvedPath = resolveDisplayPath(path, projectRoot);
    if (!resolvedPath) return;
    try {
      await openLinkedCodeFile(resolvedPath);
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Could not open the linked file",
      });
    }
  }

  function pinCodeFile(path) {
    if (!path || previewCodePathRef.current !== path) return;
    previewCodePathRef.current = "";
    setPreviewCodePath("");
  }

  function closeCodeFile(path) {
    closeCodeFiles([path]);
  }

  function closeCodeFiles(paths) {
    const closing = new Set(paths.filter(Boolean));
    if (!closing.size) return;
    for (const path of closing) {
      const requests = terminalEditorRequestsRef.current.get(path);
      if (!requests) continue;
      terminalEditorRequestsRef.current.delete(path);
      for (const requestId of requests) {
        void window.goferTerminal?.completeEditor?.(requestId).catch(() => {});
      }
    }
    setCodeOpenPaths((current) => {
      const next = current.filter((candidate) => !closing.has(candidate));
      setActiveCodePath((currentActive) => {
        if (!closing.has(currentActive)) return currentActive;
        const index = current.indexOf(currentActive);
        return next[Math.min(index, next.length - 1)] ?? next.at(-1) ?? "";
      });
      return next;
    });
    setPreviewCodePath((current) => {
      if (!closing.has(current)) return current;
      previewCodePathRef.current = "";
      return "";
    });
    setBrowserTabs((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !closing.has(path)),
    ));
    setWorkflowTabs(current => Object.fromEntries(Object.entries(current).filter(([path]) => !closing.has(path))));
  }

  function closeActiveCodeFile() {
    rattishEditorRef.current?.closeActive?.();
  }

  function editWorkflowFile(workflow) {
    if (!workflow?.sourcePath || workflow.sourceFormat !== "rattish") return;
    if (workflow.id === activeWorkflow?.id) {
      openCodeFile(workflow.sourcePath);
      return;
    }
    pendingProjectFileRef.current = { path: workflow.sourcePath, workflowId: workflow.id };
    setActiveWorkflowId(workflow.id);
    setCodeEditorOpened(true);
    setStudioView("code");
  }

  async function reloadActiveRattishDocument(sourcePath, targetWorkflow = workflows.find(item => samePath(item.sourcePath, sourcePath)) ?? activeWorkflow) {
    const activeWorkflow = targetWorkflow;
    const { rattishEditorStateRef, setRattishEditorState } = documentSession(activeWorkflow?.id);
    if (activeWorkflow?.sourceFormat !== "rattish") return;
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(activeWorkflow.id)}/document`),
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Editor API returned ${response.status}`);
      const nextState = { document: payload.document, error: "", loading: false, saving: false };
      rattishEditorStateRef.current = nextState;
      setRattishEditorState(nextState);
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to reload workflow.rattish",
      });
    }
  }

  function scheduleRattishAnalysis(source, sourcePath) {
    const activeWorkflow = workflows.find(item => samePath(item.sourcePath, sourcePath)) ?? workflows.find(item => item.id === activeWorkflowId);
    const { rattishEditorStateRef, rattishAnalysisTimerRef, rattishAnalysisRequestRef, setRattishEditorState } = documentSession(activeWorkflow?.id);
    if (activeWorkflow?.sourceFormat !== "rattish") return;
    const workflowId = activeWorkflow.id;
    const requestId = ++rattishAnalysisRequestRef.current;
    window.clearTimeout(rattishAnalysisTimerRef.current);
    rattishAnalysisTimerRef.current = window.setTimeout(async () => {
      try {
        const response = await fetch(
          apiUrl(`/workflows/${encodeURIComponent(workflowId)}/document/analyze`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source }),
          },
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Analysis returned ${response.status}`);
        if (requestId !== rattishAnalysisRequestRef.current) return;
        const nextState = mergeRattishAnalysisState(
          rattishEditorStateRef.current,
          payload.document,
          source,
        );
        if (nextState === rattishEditorStateRef.current) return;
        rattishEditorStateRef.current = nextState;
        setRattishEditorState(nextState);
      } catch (error) {
        if (requestId !== rattishAnalysisRequestRef.current) return;
        const currentState = rattishEditorStateRef.current;
        if (currentState?.document?.source !== source) return;
        const nextState = {
          ...currentState,
          error: error instanceof Error ? error.message : "Unable to analyze Rattish source",
          loading: false,
        };
        rattishEditorStateRef.current = nextState;
        setRattishEditorState(nextState);
      }
    }, RATTISH_ANALYSIS_DELAY_MS);
  }

  async function refreshRattishAfterFileSave(savedSource, sourcePath) {
    const activeWorkflow = workflows.find(item => samePath(item.sourcePath, sourcePath)) ?? workflows.find(item => item.id === activeWorkflowId);
    const { rattishEditorStateRef, setRattishEditorState } = documentSession(activeWorkflow?.id);
    if (activeWorkflow?.sourceFormat !== "rattish") return null;
    const response = await fetch(
      apiUrl(`/workflows/${encodeURIComponent(activeWorkflow.id)}/document`),
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Editor API returned ${response.status}`);
    const currentState = rattishEditorStateRef.current;
    if (currentState?.document?.source === savedSource) {
      const nextState = { document: payload.document, error: "", loading: false, saving: false };
      rattishEditorStateRef.current = nextState;
      setRattishEditorState(nextState);
    }
    void loadWorkflows({ silent: true });
    return payload.document;
  }

  async function openProjectAtPath(projectRoot, { rememberProject = false, focusPath = "" } = {}) {
    const requestId = projectOpenRequestRef.current + 1;
    projectOpenRequestRef.current = requestId;
    projectOpenAbortRef.current?.abort();
    const controller = new AbortController();
    projectOpenAbortRef.current = controller;
    setOpeningProjectRoot(projectRoot);
    setProjectError("");
    try {
      const discoveredPayloads = await withProjectOpenTimeout(discoverProjectWorkflows(projectRoot, {
        signal: controller.signal,
        onTrustedRoot: root => {
          if (projectOpenRequestRef.current !== requestId) return;
          setActiveProjectRoot(root);
          setCodeEditorOpened(true);
        },
        onResolvedRoot: root => { projectRoot = root; },
      }));
      if (projectOpenRequestRef.current !== requestId) return null;
      // Recent-project identity is optional metadata, not a prerequisite for navigation.
      if (rememberProject) {
        const openedRoot = projectRoot;
        void withProjectOpenTimeout(Promise.resolve().then(() => window.goferDesktop?.workspace?.gitWorktrees?.(openedRoot)), 3000)
          .catch(() => null).then(payload => {
            if (projectOpenRequestRef.current !== requestId) return;
            const mainProjectRoot = mainWorktreeRoot(payload, openedRoot);
            recentProjectValidatorRef.current.remember(mainProjectRoot, openedRoot);
            setRecentProjectRoots(current => rememberRecentProject(current, mainProjectRoot));
            setLastWorktreeByProject(current => withPathValue(current, mainProjectRoot, openedRoot));
          });
      }
      const discovered = discoveredPayloads.map((workflow) =>
        summarizeWorkflow(workflow, dataDir));
      setActiveProjectRoot(projectRoot);
      if (!discovered.length) {
        if (focusPath) {
          setCodeOpenPaths((current) => mergeCodeOpenPaths(current, [focusPath]));
          setActiveCodePath(focusPath);
        }
        setCodeEditorOpened(true);
        if (focusPath) setStudioView("code");
        return { discovered, projectRoot };
      }
      for (const workflow of discovered) deletedWorkflowIdsRef.current.delete(workflow.id);
      setWorkflows((current) => {
        const discoveredIds = new Set(discovered.map((workflow) => workflow.id));
        return [
          ...current.filter((workflow) => !discoveredIds.has(workflow.id)),
          ...discovered,
        ];
      });
      const selectedWorkflow = discovered.find((workflow) => samePath(workflow.sourcePath, focusPath))
        ?? discovered[0];
      if (focusPath) {
        setCodeOpenPaths((current) => mergeCodeOpenPaths(current, [focusPath]));
        setActiveCodePath(focusPath);
        pinCodeFile(focusPath);
      }
      if (focusPath && activeWorkflow?.id !== selectedWorkflow.id) {
        pendingProjectFileRef.current = focusPath ? {
          path: focusPath,
          workflowId: selectedWorkflow.id,
        } : null;
        setActiveWorkflowId(selectedWorkflow.id);
      }
      setCodeEditorOpened(true);
      if (focusPath) setStudioView("code");
      return { discovered, projectRoot };
    } catch (error) {
      if (projectOpenRequestRef.current !== requestId) return null;
      setProjectError(error instanceof Error ? error.message : "Unable to open project");
      throw error;
    } finally {
      controller.abort();
      if (projectOpenRequestRef.current === requestId) setOpeningProjectRoot("");
    }
  }

  async function openFile() {
    if (!window.goferDesktop?.workspace?.selectPath) {
      setTopBarNotice({ type: "error", message: "File selection is unavailable." });
      return;
    }
    try {
      const selectedPath = await window.goferDesktop.workspace.selectPath({
        currentPath: activeWorkflow?.projectRoot ?? "",
        fileOnly: true,
      });
      if (!selectedPath) return;
      if (activeWorkflow?.sourceFormat === "rattish") {
        openCodeFile(selectedPath);
        return;
      }
      if (!window.goferDesktop.workspace.resolveProjectFile) {
        setTopBarNotice({ type: "error", message: "Project file selection is unavailable." });
        return;
      }
      const resolved = await window.goferDesktop.workspace.resolveProjectFile(selectedPath);
      const projectRoot = resolved?.directory ?? "";
      if (!projectRoot) throw new Error("Could not determine the selected file's project folder.");
      const result = await openProjectAtPath(projectRoot, {
        rememberProject: false,
        focusPath: selectedPath,
      });
      if (result) {
        setTopBarNotice({
          type: "success",
          message: result.discovered.length
            ? `Opened ${projectNameFromPath(projectRoot)} and registered ${result.discovered.length} workflow${result.discovered.length === 1 ? "" : "s"}.`
            : `Opened ${projectNameFromPath(projectRoot)}. No workflows found yet.`,
        });
      }
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to open file",
      });
    }
  }

  openFileRef.current = openFile;

  async function openProjectFolder() {
    if (!window.goferDesktop?.workspace?.selectPath) {
      setTopBarNotice({ type: "error", message: "Folder selection is unavailable." });
      return;
    }
    try {
      const projectRoot = await window.goferDesktop.workspace.selectPath({
        currentPath: activeWorkflow?.projectRoot ?? "",
        directoryOnly: true,
      });
      if (!projectRoot) return;
      const result = await openProjectAtPath(projectRoot, { rememberProject: true });
      if (result) {
        setTopBarNotice({
          type: "success",
          message: `Opened ${projectNameFromPath(projectRoot)} and registered ${result.discovered.length} workflow${result.discovered.length === 1 ? "" : "s"}.`,
        });
      }
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to open project folder",
      });
    }
  }

  openProjectFolderRef.current = openProjectFolder;

  function selectRecentProject(projectRoot, options = {}) {
    const mainProjectRoot = options.mainProjectRoot || projectRoot;
    const selectedProjectRoot = options.mainProjectRoot
      ? projectRoot
      : pathValue(lastWorktreeByProject, mainProjectRoot) || projectRoot;
    if (options.missing) {
      setTopBarNotice({ type: "error", message: `Worktree folder is missing: ${selectedProjectRoot}` });
      return;
    }
    void openProjectAtPath(selectedProjectRoot, { rememberProject: true }).catch((error) => {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to open project",
      });
    });
  }

  function removeRecentProject(projectRoot) {
    setRecentProjectRoots((current) => current.filter((root) => !samePath(root, projectRoot)));
  }

  function handleCodeFilesystemChange(change) {
    applyCodeFilesystemChange(change);
    void loadWorkflows({ discoverProject: true, silent: true });
    if (!change?.path) return;
    if (change.kind === "rename" && change.sourcePath) {
      setRecentCodePaths(current => [...new Set(current.map(path =>
        replacePathPrefix(path, change.sourcePath, change.path, change.isDirectory),
      ))]);
      setCodeOpenPaths((current) => current.map((path) =>
        replacePathPrefix(path, change.sourcePath, change.path, change.isDirectory),
      ));
      setActiveCodePath((current) =>
        replacePathPrefix(current, change.sourcePath, change.path, change.isDirectory));
      setPreviewCodePath((current) => {
        const next = replacePathPrefix(
          current,
          change.sourcePath,
          change.path,
          change.isDirectory,
        );
        previewCodePathRef.current = next;
        return next;
      });
    }
    if (change.kind === "delete") {
      setRecentCodePaths(current => current.filter(path => !pathMatchesChange(path, change.path, change.isDirectory)));
      setCodeOpenPaths((current) => {
        const next = current.filter((path) => !pathMatchesChange(path, change.path, change.isDirectory));
        if (pathMatchesChange(activeCodePath, change.path, change.isDirectory)) {
          setActiveCodePath(next.at(-1) ?? activeWorkflow?.sourcePath ?? "");
        }
        return next;
      });
      if (pathMatchesChange(
        previewCodePathRef.current,
        change.path,
        change.isDirectory,
      )) {
        previewCodePathRef.current = "";
        setPreviewCodePath("");
      }
    }
  }

  function queueWorkflowDocumentWrite(target, action) {
    const session = documentSession(target?.id);
    const write = session.documentWritesRef.current.catch(() => {}).then(action);
    session.documentWritesRef.current = write;
    return write;
  }

  function acceptWorkflowWrite(target, submittedDocument, savedDocument) {
    const session = documentSession(target.id);
    const currentState = session.rattishEditorStateRef.current;
    const newerDraft = currentState?.document?.source !== submittedDocument.source;
    if (currentState?.document?.metadata !== submittedDocument.metadata) {
      savedDocument = { ...savedDocument, metadata: currentState.document.metadata, metadataRevision: currentState.document.metadataRevision };
    }
    window.clearTimeout(session.rattishAnalysisTimerRef.current);
    session.rattishAnalysisRequestRef.current += 1;
    if (newerDraft) {
      session.setRattishEditorState({
        ...currentState,
        document: {
          ...currentState.document,
          savedRevision: savedDocument.savedRevision,
          savedSource: savedDocument.source,
          dirty: true,
        },
        error: "Workflow changed while saving. Your latest edits are still unsaved.",
        saving: false,
      });
      scheduleRattishAnalysis(currentState.document.source, target.sourcePath);
      return null;
    }
    session.setRattishEditorState({ document: savedDocument, error: "", loading: false, saving: false });
    rattishEditorRef.current?.acceptDocument?.(savedDocument, target.sourcePath);
    return savedDocument;
  }

  function saveWorkflowDocument(target) {
    return queueWorkflowDocumentWrite(target, () => saveWorkflowDocumentNow(target));
  }

  async function saveWorkflowDocumentNow(target) {
    const session = documentSession(target?.id);
    const document = session.rattishEditorStateRef.current?.document;
    if (!document) return null;
    if (!document.dirty) return document;
    const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(target.id)}/document/save`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: document.source, expectedRevision: document.savedRevision }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to save workflow.rattish");
    const savedDocument = acceptWorkflowWrite(target, document, payload.document);
    if (!savedDocument) throw new Error("Workflow changed during save. Save the latest edits before continuing.");
    return savedDocument;
  }

  async function mutateActiveRattish(mutations, targetWorkflow = activeWorkflow) {
    if (targetWorkflow?.sourceFormat !== "rattish" || !mutations?.length) return null;
    return queueWorkflowDocumentWrite(targetWorkflow, async () => {
      const session = documentSession(targetWorkflow.id);
      try {
        if (session.rattishMetadataPendingRef.current || session.rattishMetadataSavingRef.current) {
          const metadataSaved = await saveRattishMetadataNow(targetWorkflow.id);
          if (!metadataSaved) return null;
        }
        const currentDocument = await saveWorkflowDocumentNow(targetWorkflow);
        if (!currentDocument) return null;
        const response = await fetch(
          apiUrl(`/workflows/${encodeURIComponent(targetWorkflow.id)}/document/mutate`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mutations, expectedRevision: currentDocument.savedRevision }),
          },
        );
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(response.status === 409
            ? "workflow.rattish changed on disk. Reload it before editing the graph."
            : payload.error || `Graph edit returned ${response.status}`);
        }
        const savedDocument = acceptWorkflowWrite(targetWorkflow, currentDocument, payload.document);
        if (!savedDocument) {
          setTopBarNotice({ type: "error", message: "Graph changes were saved. Your newer source edits are still unsaved." });
          return null;
        }
        setTopBarNotice({ type: "success", message: "Updated workflow.rattish" });
        void loadWorkflows({ silent: true });
        return savedDocument;
      } catch (error) {
        setTopBarNotice({ type: "error", message: error instanceof Error ? error.message : "Unable to edit workflow.rattish" });
        return null;
      }
    });
  }

  function updateRattishGraphMetadata(nextWorkflow, targetWorkflow = activeWorkflow) {
    const activeWorkflow = targetWorkflow;
    const { rattishEditorStateRef, rattishMetadataSaveTimerRef, rattishMetadataPendingRef, setRattishEditorState } = documentSession(activeWorkflow?.id);
    const currentState = rattishEditorStateRef.current;
    const document = currentState?.document;
    if (!document || activeWorkflow?.sourceFormat !== "rattish") return;
    const nodes = Object.fromEntries(
      (nextWorkflow.nodes ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
    );
    const metadata = {
      ...document.metadata,
      canvas: {
        ...document.metadata.canvas,
        nodes,
      },
    };
    const nextState = {
      ...currentState,
      document: { ...document, metadata },
    };
    rattishEditorStateRef.current = nextState;
    setRattishEditorState(nextState);
    rattishMetadataPendingRef.current = true;
    window.clearTimeout(rattishMetadataSaveTimerRef.current);
    rattishMetadataSaveTimerRef.current = window.setTimeout(
      () => void saveRattishMetadataNow(activeWorkflow.id),
      250,
    );
  }

  async function saveRattishMetadataNow(workflowId) {
    const session = documentSession(workflowId);
    const { rattishEditorStateRef, rattishMetadataSaveTimerRef, rattishMetadataPendingRef, rattishMetadataSavingRef, setRattishEditorState } = session;
    window.clearTimeout(rattishMetadataSaveTimerRef.current);
    if (rattishMetadataSavingRef.current) return rattishMetadataSavingRef.current;
    if (!rattishEditorStateRef.current?.document || !rattishMetadataPendingRef.current) return true;
    rattishMetadataSavingRef.current = (async () => {
      try {
        while (rattishMetadataPendingRef.current) {
          const latest = rattishEditorStateRef.current?.document;
          if (!latest) return false;
          rattishMetadataPendingRef.current = false;
          const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflowId)}/metadata`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ metadata: latest.metadata, expectedRevision: latest.metadataRevision }),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || `Metadata save returned ${response.status}`);
          setRattishEditorState(state => !state?.document ? state : {
            ...state,
            document: {
              ...state.document,
              metadata: rattishMetadataPendingRef.current ? state.document.metadata : payload.metadata,
              metadataRevision: payload.metadataRevision,
            },
          });
        }
        return true;
      } catch (error) {
        rattishMetadataPendingRef.current = true;
        setTopBarNotice({ type: "error", message: error instanceof Error ? error.message : "Unable to save graph layout" });
        return false;
      } finally {
        rattishMetadataSavingRef.current = null;
      }
    })();
    return rattishMetadataSavingRef.current;
  }

  const loadWorkflows = useCallback(async ({
    discoverProject = false,
    projectRoot = activeProjectRoot,
    silent = false,
  } = {}) => {
    if (!silent) {
      setLoadState({ loading: true, error: "" });
    }
    const requestId = ++workflowLoadRequestRef.current;
    try {
      if (discoverProject && projectRoot) {
        await shareInFlight(`discover-project:${projectRoot}`, () => discoverProjectWorkflows(projectRoot));
      }
      const payload = await shareInFlight("workflow-list", async () => {
        const response = await fetch(apiUrl("/workflows"));
        if (!response.ok) throw new Error(`Workflow API returned ${response.status}`);
        return response.json();
      });
      if (requestId !== workflowLoadRequestRef.current) return;
      const payloadDataDir = payload.dataDir ?? "";
      setPromptAgentIds(payload.promptAgentIds ?? []);
      const listedWorkflows = payload.workflows ?? [];
      const nextWorkflows = listedWorkflows
        .filter(
          (workflow) =>
            payload.authoringLanguage !== "rattish" || workflow.sourceFormat === "rattish",
        )
        .filter((workflow) => !deletedWorkflowIdsRef.current.has(workflow.id))
        .map((workflow) => summarizeWorkflow(workflow, payloadDataDir));
      setWorkflows((current) => {
        const refreshedWorkflows = nextWorkflows.map((workflow) => {
          const localWorkflow = current.find((candidate) => candidate.id === workflow.id);
          return localWorkflow
            ? summarizeWorkflow(mergeSavedWorkflow(localWorkflow, workflow), payloadDataDir)
            : workflow;
        });
        const mergedWorkflows = silent
          ? [...dirtyWorkflowsRef.current.keys()].reduce((workflowsToMerge, workflowId) => {
              if (deletedWorkflowIdsRef.current.has(workflowId)) return workflowsToMerge;
              const localDirtyWorkflow = current.find((workflow) => workflow.id === workflowId);
              return localDirtyWorkflow
                ? preserveLocalWorkflow(workflowsToMerge, localDirtyWorkflow, payloadDataDir)
                : workflowsToMerge;
            }, refreshedWorkflows)
          : refreshedWorkflows;

        return silent && equalJson(current, mergedWorkflows)
          ? current
          : mergedWorkflows;
      });
      setDataDir(payload.dataDir ?? "");
      setActiveWorkflowId((currentId) => {
        if (nextWorkflows.some((workflow) => workflow.id === currentId)) {
          return currentId;
        }
        const projectWorkflow = nextWorkflows.find(
          (workflow) => samePath(workflow.projectRoot, initialStudioSession.projectRoot),
        );
        if (projectWorkflow) return projectWorkflow.id;
        return nextWorkflows[0]?.id;
      });
      setLoadState({ loading: false, error: "" });
    } catch (error) {
      if (requestId !== workflowLoadRequestRef.current) return;
      if (!silent) {
        setLoadState({
          loading: false,
          error: error instanceof Error ? error.message : "Unable to load workflows",
        });
      }
    }
  }, [activeProjectRoot, initialStudioSession.projectRoot]);

  const loadDoctor = useCallback(async ({ silent = false } = {}) => {
    if (!silent) {
      setDoctorState((current) => ({ ...current, loading: true, error: "" }));
    }
    try {
      const response = await fetch(apiUrl("/doctor"));
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Doctor API returned ${response.status}`);
      }
      setDoctorState({
        loading: false,
        error: "",
        errors: payload.errors ?? [],
        warnings: payload.warnings ?? [],
      });
    } catch (error) {
      if (!silent) {
        setDoctorState({
          loading: false,
          error: error instanceof Error ? error.message : "Unable to load health checks",
          errors: [],
          warnings: [],
        });
      }
    }
  }, []);

  const loadQueue = useCallback(async ({ silent = false } = {}) => {
    try {
      const response = await fetch(apiUrl("/queue"));
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Queue API returned ${response.status}`);
      }
      setQueueState({
        runners: payload.runners ?? [],
        runs: payload.runs ?? [],
        error: "",
      });
    } catch (error) {
      if (!silent) {
        setQueueState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Unable to load runners",
        }));
      }
    }
  }, []);

  useEffect(() => {
    loadWorkflows();
  }, [loadWorkflows]);

  useEffect(() => {
    const trustProjectRoot = window.goferDesktop?.workspace?.trustProjectRoot;
    if (!trustProjectRoot) return;
    const projectRoots = new Set(
      workflows.map((workflow) => workflow.projectRoot).filter(Boolean),
    );
    for (const projectRoot of projectRoots) {
      void trustProjectRoot(projectRoot).catch(() => {});
    }
  }, [workflows]);

  useEffect(() => {
    loadDoctor();
    loadQueue();
  }, [loadDoctor, loadQueue]);

  useEffect(() => {
    const projectRoot = activeProjectRoot || activeWorkflow?.projectRoot || "";
    return startWorkspacePolling({
      refreshLive: () => Promise.allSettled([
        loadWorkflows({ silent: true }),
        loadQueue({ silent: true }),
      ]),
      discover: () => loadWorkflows({ discoverProject: Boolean(projectRoot), projectRoot, silent: true }),
      doctor: () => loadDoctor({ silent: true }),
    });
  }, [activeProjectRoot, activeWorkflow?.projectRoot, loadDoctor, loadQueue, loadWorkflows]);

  const loadRetentionSettingsForWorkflow = useCallback(async (workflowId) => {
    if (!workflowId) return;
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/retention`),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (payload.settings) {
        setRetentionSettings(payload.settings);
        window.localStorage?.setItem(RETENTION_STORAGE_KEY, JSON.stringify(payload.settings));
      }
    } catch {
      setRetentionSettings(loadRetentionSettings());
    }
  }, []);

  const saveRetentionSettingsForWorkflow = useCallback(async (workflowId, nextSettings) => {
    setRetentionSettings(nextSettings);
    window.localStorage?.setItem(RETENTION_STORAGE_KEY, JSON.stringify(nextSettings));
    if (!workflowId) return;
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/retention`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(nextSettings),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (payload.settings) {
        setRetentionSettings(payload.settings);
        window.localStorage?.setItem(RETENTION_STORAGE_KEY, JSON.stringify(payload.settings));
      }
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message:
          error instanceof Error ? error.message : "Unable to save retention settings",
      });
    }
  }, []);

  const checkForUpdates = useCallback(async ({ silent = false } = {}) => {
    if (!window.goferUpdates?.check) return;
    setUpdateState((current) => ({
      ...current,
      checking: true,
      error: silent ? current.error : "",
    }));
    try {
      const info = await window.goferUpdates.check();
      setUpdateState({
        available: Boolean(info?.available),
        checking: false,
        error: "",
        info,
      });
      if (!silent) {
        setTopBarNotice({
          type: info?.available ? "success" : "success",
          message: info?.available
            ? `Raticode ${info.info?.version ?? "update"} is available`
            : info?.info?.noReleases
              ? "No published Raticode releases yet"
            : "Raticode is up to date",
        });
      }
    } catch (error) {
      setUpdateState((current) => ({
        ...current,
        checking: false,
        error: error instanceof Error ? error.message : "Unable to check for updates",
      }));
      if (!silent) {
        setTopBarNotice({
          type: "error",
          message: error instanceof Error ? error.message : "Unable to check for updates",
        });
      }
    }
  }, []);

  useEffect(() => {
    if (!window.goferUpdates?.onState) return undefined;
    const unsubscribe = window.goferUpdates.onState((nextState) => {
      setUpdateState((current) => ({ ...current, ...nextState }));
    });
    window.goferUpdates.getState?.().then((nextState) => {
      setUpdateState((current) => ({ ...current, ...nextState }));
    }).catch(() => {});
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (settings.general.checkForUpdates) checkForUpdates({ silent: true });
  }, [checkForUpdates, settings.general.checkForUpdates]);

  async function applyUpdate(update) {
    if (!window.goferUpdates) return;
    try {
      const nextState = update.downloaded
        ? await window.goferUpdates.installDownloaded()
        : await window.goferUpdates.downloadAndInstall();
      setUpdateState((current) => ({ ...current, ...nextState }));
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to apply update",
      });
    }
  }

  useEffect(() => {
    if (!topBarNotice?.message) return undefined;

    const timeoutId = window.setTimeout(() => {
      setTopBarNotice({ type: "", message: "" });
    }, 3500);

    return () => window.clearTimeout(timeoutId);
  }, [topBarNotice?.message]);

  const loadLatestLog = useCallback(async (workflowId, { silent = false } = {}) => {
    if (pinnedRunRef.current) return;
    const requestId = logRequestRef.current + 1;
    logRequestRef.current = requestId;
    if (!silent) {
      setLogState((current) => ({ ...current, loading: true, error: "" }));
    }
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/logs/latest`),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (requestId !== logRequestRef.current) return;
      const revision = response.headers?.get("ETag") || "";
      const nextText = payload.log?.logText ?? "";
      const nextPath = payload.log?.logPath ?? null;
      const nextNodeOutputs = payload.log?.nodeOutputs ?? null;
      const nextUsageSummary = payload.log?.usageSummary ?? null;
      const nextRunEvents = payload.log?.runEvents ?? [];
      const nextRunNodes = payload.log?.runNodes ?? {};
      setLogState((current) => {
        if (
          current.text === nextText &&
          current.path === nextPath &&
          (revision ? current.revision === revision : (
            equalJson(current.nodeOutputs ?? null, nextNodeOutputs) &&
            equalJson(current.usageSummary ?? null, nextUsageSummary) &&
            equalJson(current.runEvents ?? [], nextRunEvents) &&
            equalJson(current.runNodes ?? {}, nextRunNodes)
          )) &&
          current.error === "" &&
          current.loading === false
        ) {
          return current;
        }
        return {
          loading: false,
          error: "",
          revision,
          text: nextText,
          path: nextPath,
          workflowId,
          graphSnapshot: payload.log?.graphSnapshot ?? null,
          nodeOutputs: nextNodeOutputs,
          nodeOutputsTruncated: Boolean(payload.log?.nodeOutputsTruncated),
          nodeOutputsMaxBytes: payload.log?.nodeOutputsMaxBytes ?? null,
          usageSummary: nextUsageSummary,
          runEvents: nextRunEvents,
          runNodes: nextRunNodes,
          runs: current.runs,
          selectedRunId: null,
        };
      });
    } catch (error) {
      if (requestId !== logRequestRef.current) return;
      if (!silent) {
        setLogState((current) => ({
          ...current,
          loading: false,
          error: error instanceof Error ? error.message : "Unable to load workflow log",
        }));
      }
    }
  }, []);

  const loadRunLogs = useCallback(async (workflowId, { silent = false } = {}) => {
    if (pinnedRunRef.current && pinnedRunRef.current.workflowId !== workflowId) return;
    const requestId = ++logListRequestRef.current;
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/logs?limit=100`),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (requestId !== logListRequestRef.current) return;
      setLogState((current) => {
        const nextRuns = payload.runs ?? [];
        if (silent && equalJson(current.runs, nextRuns)) {
          return current;
        }
        return { ...current, runs: nextRuns };
      });
    } catch (error) {
      if (requestId !== logListRequestRef.current) return;
      if (!silent) {
        setLogState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Unable to load workflow runs",
        }));
      }
    }
  }, []);

  const loadRunLog = useCallback(async (workflowId, runId, { silent = false } = {}) => {
    const requestId = logRequestRef.current + 1;
    logRequestRef.current = requestId;
    if (!silent) {
      setLogState((current) => ({
        ...current,
        loading: true,
        error: "",
        selectedRunId: runId,
      }));
    }
    try {
      const params = new URLSearchParams({
        tailBytes: String(RUN_LOG_TAIL_BYTES),
        details: silent ? "0" : "1",
      });
      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflowId)}/logs/${encodeURIComponent(runId)}?${params}`,
        ),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (requestId !== logRequestRef.current) return;
      setLogState((current) => ({
        ...current,
        loading: false,
        error: "",
        text: payload.log?.logText ?? "",
        path: payload.log?.logPath ?? null,
        nodeOutputs: silent ? current.nodeOutputs : (payload.log?.nodeOutputs ?? null),
        nodeOutputsTruncated: silent
          ? current.nodeOutputsTruncated
          : Boolean(payload.log?.nodeOutputsTruncated),
        nodeOutputsMaxBytes: silent
          ? current.nodeOutputsMaxBytes
          : (payload.log?.nodeOutputsMaxBytes ?? null),
        usageSummary: silent ? current.usageSummary : (payload.log?.usageSummary ?? null),
        runEvents: silent ? current.runEvents : (payload.log?.runEvents ?? []),
        runNodes: silent ? current.runNodes : (payload.log?.runNodes ?? {}),
        selectedRunId: runId,
        workflowId,
        graphSnapshot: silent ? current.graphSnapshot : payload.log?.graphSnapshot ?? null,
      }));
      return true;
    } catch (error) {
      if (requestId !== logRequestRef.current) return;
      if (!silent) {
        setLogState((current) => ({
          ...current,
          loading: false,
          error: error instanceof Error ? error.message : "Unable to load workflow run",
        }));
      }
      return false;
    }
  }, []);

  const loadApprovals = useCallback(async (workflowId, { silent = false } = {}) => {
    const requestId = ++approvalRequestRef.current;
    if (!silent) {
      setApprovalState((current) => ({ ...current, loading: true, error: "" }));
    }
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/approvals`),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (requestId !== approvalRequestRef.current) return;
      setApprovalState({
        approvals: payload.approvals ?? [],
        error: "",
        loading: false,
      });
    } catch (error) {
      if (requestId !== approvalRequestRef.current) return;
      if (!silent) {
        setApprovalState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Unable to load approvals",
          loading: false,
        }));
      }
    }
  }, []);

  useEffect(() => {
    if (!activeWorkflow?.id && !pinnedRun) {
      setLogState({
        loading: false,
        error: "",
        text: "",
        path: null,
        nodeOutputs: null,
        nodeOutputsTruncated: false,
        nodeOutputsMaxBytes: null,
        usageSummary: null,
        runEvents: [],
        runNodes: {},
        runs: [],
        selectedRunId: null,
      });
      setApprovalState({ approvals: [], error: "", loading: false });
      return;
    }

    if (pinnedRun) {
      loadRunLog(pinnedRun.workflowId, pinnedRun.runId);
      loadRunLogs(pinnedRun.workflowId);
      return;
    }
    loadLatestLog(activeWorkflow.id, { silent: true });
    loadRunLogs(activeWorkflow.id);
    loadApprovals(activeWorkflow.id);
    loadRetentionSettingsForWorkflow(activeWorkflow.id);
  }, [
    activeWorkflow?.id,
    pinnedRun,
    loadRunLog,
    loadApprovals,
    loadLatestLog,
    loadRetentionSettingsForWorkflow,
    loadRunLogs,
  ]);

  useEffect(() => {
    if (!activeWorkflow?.id && !pinnedRun) return undefined;
    const monitoredWorkflowId = pinnedRun?.workflowId || activeWorkflow.id;
    return startPolling(() => Promise.allSettled([
      (pinnedRun?.runId || logState.selectedRunId)
        ? loadRunLog(monitoredWorkflowId, pinnedRun?.runId || logState.selectedRunId, { silent: true })
        : loadLatestLog(monitoredWorkflowId, { silent: true }),
      loadRunLogs(monitoredWorkflowId, { silent: true }),
      ...(activeWorkflow?.id ? [loadApprovals(activeWorkflow.id, { silent: true })] : []),
    ]));
  }, [
    activeWorkflow?.id,
    loadApprovals,
    loadLatestLog,
    loadRunLog,
    loadRunLogs,
    logState.selectedRunId,
    pinnedRun,
  ]);

  useEffect(() => {
    const saveTimers = saveTimersRef.current;
    let pendingEditsPreserved = false;

    function preservePendingEdits() {
      if (pendingEditsPreserved) return;
      pendingEditsPreserved = true;
      for (const { workflow } of dirtyWorkflowsRef.current.values()) {
        void fetch(apiUrl(`/workflows/${encodeURIComponent(workflow.id)}`), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(workflowPayloadForSave(workflow)),
          keepalive: true,
        }).catch(() => {});
      }
    }

    window.addEventListener("beforeunload", preservePendingEdits);
    window.addEventListener("pagehide", preservePendingEdits);
    return () => {
      window.removeEventListener("beforeunload", preservePendingEdits);
      window.removeEventListener("pagehide", preservePendingEdits);
      for (const timerId of saveTimers.values()) {
        window.clearTimeout(timerId);
      }
      saveTimers.clear();
    };
  }, []);

  const usedAgentIds = useMemo(() => {
    return [
      ...new Set(
        [
          ...promptAgentIds,
          ...workflows.flatMap((workflow) => [
            ...Object.keys(workflow.agents ?? {}),
            ...(workflow.nodes ?? [])
              .map((node) => node.operation?.agent_id)
              .filter(Boolean),
          ]),
        ],
      ),
    ];
  }, [promptAgentIds, workflows]);

  function updateActiveWorkflow(nextWorkflow) {
    const summarizedWorkflow = summarizeWorkflow(nextWorkflow, dataDir);
    setWorkflows((current) =>
      current.map((workflow) =>
        workflow.id === summarizedWorkflow.id ? summarizedWorkflow : workflow,
      ),
    );
    const revision = (workflowRevisionsRef.current.get(summarizedWorkflow.id) ?? 0) + 1;
    workflowRevisionsRef.current.set(summarizedWorkflow.id, revision);
    dirtyWorkflowsRef.current.set(summarizedWorkflow.id, {
      revision,
      workflow: summarizedWorkflow,
    });
    setDirtyWorkflowsById((current) => ({
      ...current,
      [summarizedWorkflow.id]: { revision },
    }));
    setSaveStatesByWorkflowId((current) => ({
      ...current,
      [summarizedWorkflow.id]: { status: "saving", error: "" },
    }));

    const previousTimerId = saveTimersRef.current.get(summarizedWorkflow.id);
    if (previousTimerId) window.clearTimeout(previousTimerId);
    const timerId = window.setTimeout(() => {
      saveTimersRef.current.delete(summarizedWorkflow.id);
      void saveWorkflow(summarizedWorkflow, revision);
    }, 650);
    saveTimersRef.current.set(summarizedWorkflow.id, timerId);
  }

  async function saveWorkflow(workflow, revision) {
    const workflowId = workflow.id;
    const pendingSave = inFlightSavesRef.current.get(workflowId);
    if (pendingSave) {
      try {
        await pendingSave;
      } catch {
        // A newer revision is still allowed to save after an older request fails.
      }
    }

    if (dirtyWorkflowsRef.current.get(workflowId)?.revision !== revision) {
      return undefined;
    }
    if (deletedWorkflowIdsRef.current.has(workflowId)) return undefined;

    setSaveStatesByWorkflowId((current) => ({
      ...current,
      [workflowId]: { status: "saving", error: "" },
    }));
    const saveRequest = persistWorkflow(workflow);
    inFlightSavesRef.current.set(workflowId, saveRequest);
    try {
      const savedWorkflow = await saveRequest;

      if (dirtyWorkflowsRef.current.get(workflowId)?.revision === revision) {
        setWorkflows((current) =>
          current.map((candidate) =>
            candidate.id === savedWorkflow.id
              ? summarizeWorkflow(mergeSavedWorkflow(candidate, savedWorkflow), dataDir)
              : candidate,
          ),
        );
        dirtyWorkflowsRef.current.delete(workflowId);
        setDirtyWorkflowsById((current) => withoutKey(current, workflowId));
        setSaveStatesByWorkflowId((current) => ({
          ...current,
          [workflowId]: { status: "saved", error: "" },
        }));
        return savedWorkflow;
      }
      return undefined;
    } catch (error) {
      if (dirtyWorkflowsRef.current.get(workflowId)?.revision === revision) {
        setSaveStatesByWorkflowId((current) => ({
          ...current,
          [workflowId]: {
            status: "error",
            error: error instanceof Error ? error.message : "Unable to save workflow",
          },
        }));
      }
      return undefined;
    } finally {
      if (inFlightSavesRef.current.get(workflowId) === saveRequest) {
        inFlightSavesRef.current.delete(workflowId);
      }
    }
  }

  function retryWorkflowSave(workflowId) {
    const dirtyWorkflow = dirtyWorkflowsRef.current.get(workflowId);
    if (!dirtyWorkflow) return;
    void saveWorkflow(dirtyWorkflow.workflow, dirtyWorkflow.revision);
  }

  async function persistWorkflow(workflow) {
    const response = await fetch(
      apiUrl(`/workflows/${encodeURIComponent(workflow.id)}`),
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(workflowPayloadForSave(workflow)),
      },
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `Workflow API returned ${response.status}`);
    }
    return payload.workflow;
  }

  async function runWorkflowNow(workflow) {
    if (runStatesRef.current[workflow.id]?.running) return;
    const workflowToRun = summarizeWorkflow(workflow, dataDir);
    const dirtyWorkflow = dirtyWorkflowsRef.current.get(workflowToRun.id);
    const pendingTimerId = saveTimersRef.current.get(workflowToRun.id);
    if (pendingTimerId) {
      window.clearTimeout(pendingTimerId);
      saveTimersRef.current.delete(workflowToRun.id);
    }
    setRunState({ running: true, workflowId: workflowToRun.id, error: "", result: null });
    if (!pinnedRun && activeWorkflowIdRef.current === workflowToRun.id) setLogState((current) => ({
      ...current,
      loading: true,
      error: "",
      selectedRunId: null,
    }));
    if (dirtyWorkflow) {
      setSaveStatesByWorkflowId((current) => ({
        ...current,
        [workflowToRun.id]: { status: "saving", error: "" },
      }));
    }

    try {
      let savedWorkflow;
      if (workflowToRun.sourceFormat === "rattish") {
        const session = documentSession(workflowToRun.id);
        if (!session.rattishEditorStateRef.current?.document) {
          const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflowToRun.id)}/document`));
          const payload = await response.json();
          if (!response.ok || !payload.document) throw new Error(payload.error || "Unable to load workflow.rattish before running.");
          session.setRattishEditorState({ document: payload.document, error: "", loading: false, saving: false });
        }
        if (session.rattishEditorStateRef.current?.document?.dirty) {
          const savedDocument = await saveWorkflowDocument(workflowToRun);
          if (!savedDocument) {
            throw new Error("Save workflow.rattish before running the workflow.");
          }
        }
        savedWorkflow = { ...workflowToRun, expectedSourceRevision: documentSession(workflowToRun.id).rattishEditorStateRef.current?.document?.savedRevision };
      } else {
        savedWorkflow = dirtyWorkflow
          ? await saveWorkflow(dirtyWorkflow.workflow, dirtyWorkflow.revision)
          : await persistWorkflow(workflowToRun);
      }
      if (!savedWorkflow) {
        throw new Error("Unable to save workflow before running");
      }
      if (
        workflowToRun.sourceFormat !== "rattish" &&
        (!dirtyWorkflow ||
          dirtyWorkflowsRef.current.get(workflowToRun.id)?.revision === dirtyWorkflow.revision)
      ) {
        setWorkflows((current) =>
          current.map((candidate) =>
            candidate.id === savedWorkflow.id
              ? summarizeWorkflow(mergeSavedWorkflow(candidate, savedWorkflow), dataDir)
              : candidate,
          ),
        );
        if (dirtyWorkflow) {
          dirtyWorkflowsRef.current.delete(workflowToRun.id);
          setDirtyWorkflowsById((current) => withoutKey(current, workflowToRun.id));
          setSaveStatesByWorkflowId((current) => ({
            ...current,
            [workflowToRun.id]: { status: "saved", error: "" },
          }));
        }
      }
      const externalAccessWarnings = agentExternalAccessWarnings(savedWorkflow);
      if (externalAccessWarnings.length > 0) {
        const confirmed = window.confirm(
          [
            "Agent filesystem access outside working_dir:",
            "",
            ...externalAccessWarnings.map((warning) => `- ${warning}`),
            "",
            "Run this workflow?",
          ].join("\n"),
        );
        if (!confirmed) {
          setRunState({
            running: false,
            workflowId: savedWorkflow.id,
            error: "",
            result: null,
          });
          setLogState((current) => ({ ...current, loading: false }));
          return;
        }
      }

      const triggerContext = buildRunPreviewTriggerContext(savedWorkflow);
      const initialParameters = initialWorkflowParameters(savedWorkflow);
      const previewRequest = workflowPlanRequest(savedWorkflow.id, triggerContext, initialParameters);
      const previewResponse = await fetch(previewRequest.url, previewRequest.options);
      const previewPayload = await previewResponse.json();
      if (!previewResponse.ok) {
        throw new Error(previewPayload.error || `Workflow API returned ${previewResponse.status}`);
      }
      setRunState({ running: false, workflowId: savedWorkflow.id, error: "", result: null });
      setLogState((current) => ({ ...current, loading: false }));
      setRunPreview({
        workflow: {
          ...savedWorkflow,
          inputs: previewPayload.plan?.inputs ?? savedWorkflow.inputs,
        },
        plan: previewPayload.plan,
        triggerContext,
        parameters: initialParameters,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to run workflow";
      setRunState({ running: false, workflowId: workflowToRun.id, error: message, result: null });
      loadLatestLog(workflowToRun.id, { silent: true });
      loadRunLogs(workflowToRun.id, { silent: true });
    }
  }

  async function executeWorkflowRun(workflow, triggerContext = {}, parameters = {}) {
    if (runStatesRef.current[workflow.id]?.running) return;
    setRunPreview(null);
    setRunState({ running: true, workflowId: workflow.id, error: "", result: null });
    setLogState((current) => ({
      ...current,
      loading: true,
      error: "",
      selectedRunId: null,
    }));
    try {
      if (executionMode === "remote") {
        const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/queue`), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            trigger: "ui",
            parameters:
              Object.keys(parameters ?? {}).length > 0
                ? { triggerContext, workflowInputs: parameters }
                : { triggerContext },
          }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `Queue API returned ${response.status}`);
        }
        setRunState({ running: false, workflowId: workflow.id, error: "", result: payload.run });
        setLogState((current) => ({ ...current, loading: false }));
        setTopBarNotice({
          type: "success",
          message: `Queued ${workflow.name} for remote execution`,
        });
        loadQueue({ silent: true });
        return;
      }
      const runRequest = workflowRunRequest(workflow.id, {
        ...(workflow.sourceFormat === "rattish" ? { background: true, expectedRevision: workflow.expectedSourceRevision } : {}),
        dryRun: false,
        triggerContext,
        parameters,
      });
      const response = await fetch(runRequest.url, runRequest.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setRunState({ running: false, workflowId: workflow.id, error: "", result: payload.run });
      if (["running", "queued"].includes(payload.run?.status)) {
        recordWorkflowRun(workflow, payload.run);
        void runRegistry.refresh();
        void loadRunLogs(workflow.id);
        setLogState(current => ({ ...current, loading: false }));
        return;
      }
      const nextRunStatus =
        payload.run?.status === "stopped"
          ? "Stopped"
          : payload.run?.success
            ? "Success"
            : "Error";
      const nextRunTag =
        payload.run?.status === "stopped" ? "stopped" : payload.run?.success ? "success" : "error";
      setWorkflows((current) =>
        current.map((candidate) =>
          candidate.id === workflow.id
            ? {
                ...candidate,
                status: nextRunStatus,
                tags: [nextRunTag, ...(candidate.tags ?? []).slice(1)],
              }
            : candidate,
        ),
      );
      if (!pinnedRunRef.current && activeWorkflowIdRef.current === workflow.id) setLogState({
        loading: false,
        error: "",
        text: payload.run?.logText ?? "",
        path: payload.run?.logPath ?? null,
        nodeOutputs: payload.run?.nodeOutputs ?? null,
        nodeOutputsTruncated: Boolean(payload.run?.nodeOutputsTruncated),
        nodeOutputsMaxBytes: payload.run?.nodeOutputsMaxBytes ?? null,
        usageSummary: payload.run?.usageSummary ?? null,
        runEvents: payload.run?.runEvents ?? [],
        runNodes: payload.run?.runNodes ?? {},
        runs: logState.runs,
        selectedRunId: null,
      });
      if (!payload.run?.success) {
        setTopBarNotice({
          type: "error",
          message: workflowRunFailureMessage(payload.run),
        });
      }
      loadRunLogs(workflow.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to run workflow";
      setRunState({ running: false, workflowId: workflow.id, error: message, result: null });
      loadLatestLog(workflow.id, { silent: true });
      loadRunLogs(workflow.id, { silent: true });
    }
  }

  async function decideApproval(workflow, approval, decision, notes = "", by = "ui") {
    try {
      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflow.id)}/approvals/${encodeURIComponent(
            approval.runId,
          )}/${encodeURIComponent(approval.nodeId)}/${decision === "approved" ? "approve" : "reject"}`,
        ),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ by, notes }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setTopBarNotice({
        type: "success",
        message: decision === "approved" ? "Approval recorded" : "Rejection recorded",
      });
      setApprovalState((current) => ({
        ...current,
        approvals: current.approvals.map((candidate) =>
          candidate.runId === approval.runId && candidate.nodeId === approval.nodeId
            ? (payload.approval ?? {
                ...candidate,
                status: "decided",
                decision: { decision, decidedBy: by, notes },
              })
            : candidate,
        ),
      }));
      loadApprovals(workflow.id, { silent: true });
      loadLatestLog(workflow.id, { silent: true });
      loadRunLogs(workflow.id, { silent: true });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to record approval",
      });
    }
  }

  async function stopWorkflowRunLog(workflowId, runId) {
    if (!workflowId || !runId) return;

    try {
      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflowId)}/runs/${encodeURIComponent(runId)}/stop`,
        ),
        { method: "POST" },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setTopBarNotice({
        type: payload.stopped ? "success" : "error",
        message: payload.stopped ? "Stopping workflow run..." : payload.message || "No active run",
      });
      loadRunLogs(workflowId, { silent: true });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to stop workflow run",
      });
    }
  }

  async function resumeWorkflowRunLog(workflowId, runId, options = {}) {
    if (!workflowId || !runId) return;

    setRunState({ running: true, workflowId, error: "", result: null, resumingRunId: runId });
    setLogState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const request = workflowResumeRequest(workflowId, runId, options);
      const response = await fetch(request.url, request.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setRunState({ running: false, workflowId, error: "", result: payload.run });
      setLogState({
        loading: false,
        error: "",
        text: payload.run?.logText ?? "",
        path: payload.run?.logPath ?? null,
        nodeOutputs: payload.run?.nodeOutputs ?? null,
        nodeOutputsTruncated: Boolean(payload.run?.nodeOutputsTruncated),
        nodeOutputsMaxBytes: payload.run?.nodeOutputsMaxBytes ?? null,
        usageSummary: payload.run?.usageSummary ?? null,
        runEvents: payload.run?.runEvents ?? [],
        runNodes: payload.run?.runNodes ?? {},
        runs: logState.runs,
        selectedRunId: null,
      });
      setTopBarNotice({ type: "success", message: "Workflow run resumed" });
      loadWorkflows({ silent: true });
      loadRunLogs(workflowId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to resume workflow run";
      setRunState({ running: false, workflowId, error: message, result: null });
      setLogState((current) => ({ ...current, loading: false, error: message }));
      loadLatestLog(workflowId, { silent: true });
      loadRunLogs(workflowId, { silent: true });
    }
  }

  async function replayWorkflowTriggerLog(workflowId, runId, triggerId = null) {
    if (!workflowId || !runId) return;

    setRunState({ running: true, workflowId, error: "", result: null, resumingRunId: runId });
    setLogState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const request = workflowReplayTriggerRequest(workflowId, runId, triggerId);
      const response = await fetch(request.url, request.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      const runPayload = payload.trigger?.run ?? payload.run ?? {};
      setRunState({ running: false, workflowId, error: "", result: runPayload });
      setLogState({
        loading: false,
        error: "",
        text: runPayload.logText ?? "",
        path: runPayload.logPath ?? null,
        nodeOutputs: runPayload.nodeOutputs ?? null,
        nodeOutputsTruncated: Boolean(runPayload.nodeOutputsTruncated),
        nodeOutputsMaxBytes: runPayload.nodeOutputsMaxBytes ?? null,
        usageSummary: runPayload.usageSummary ?? null,
        runEvents: runPayload.runEvents ?? [],
        runNodes: runPayload.runNodes ?? {},
        runs: logState.runs,
        selectedRunId: null,
      });
      setTopBarNotice({ type: "success", message: "Webhook payload replayed" });
      loadWorkflows({ silent: true });
      loadRunLogs(workflowId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to replay webhook payload";
      setRunState({ running: false, workflowId, error: message, result: null });
      setLogState((current) => ({ ...current, loading: false, error: message }));
      loadRunLogs(workflowId, { silent: true });
    }
  }

  async function pruneWorkflowRunLogs(workflowId, options = {}) {
    if (!workflowId) return;
    const dryRun = options.dryRun !== false;
    try {
      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflowId)}/logs/prune`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dryRun,
            keepLast: options.keepLast ?? retentionSettings.keepLast,
            keepDays: options.keepDays ?? retentionSettings.keepDays,
            keepFailedDays: options.keepFailedDays ?? retentionSettings.keepFailedDays,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      const count = payload.runs?.length ?? 0;
      setTopBarNotice({
        type: dryRun ? "info" : "success",
        message: dryRun
          ? `Retention preview: ${count} run${count === 1 ? "" : "s"} would be removed`
          : `Retention cleanup removed ${count} run${count === 1 ? "" : "s"}`,
      });
      loadRunLogs(workflowId, { silent: true });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to prune workflow runs",
      });
    }
  }

  async function createWorkflow(name, options = {}) {
    setCreateState({ saving: true, error: "" });
    try {
      const response = await fetch(apiUrl("/workflows"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name,
          template: options.template || undefined,
          projectRoot: options.projectRoot,
          projectGrantId: options.projectGrantId || undefined,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

      const nextWorkflow = summarizeWorkflow(payload.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(nextWorkflow.id);
      setWorkflows((current) => [...current, nextWorkflow]);
      openWorkflowGraph(nextWorkflow);
      setCreateDialogOpen(false);
      setCreateState({ saving: false, error: "" });
    } catch (error) {
      setCreateState({
        saving: false,
        error: error instanceof Error ? error.message : "Unable to create workflow",
      });
    }
  }

  async function validateWorkflow(workflow) {
    try {
      if (workflow?.sourceFormat === "rattish") {
        const session = documentSession(workflow.id);
        let document = session.rattishEditorStateRef.current?.document;
        if (!document) {
          const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/document`));
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || `Editor API returned ${response.status}`);
          document = session.rattishEditorStateRef.current?.document;
          if (!document) {
            const draft = loadWorkflowDraft(workflow.sourcePath);
            document = draft ? { ...payload.document, source: draft.source, savedRevision: draft.savedRevision, savedSource: draft.savedSource, dirty: true } : payload.document;
            session.setRattishEditorState({ document, error: "", loading: false, saving: false });
          }
        }
        const source = document.source;
        const requestId = ++session.rattishAnalysisRequestRef.current;
        window.clearTimeout(session.rattishAnalysisTimerRef.current);
        const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/document/analyze`), {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source }),
        });
        const payload = await response.json();
        if (requestId !== session.rattishAnalysisRequestRef.current || session.rattishEditorStateRef.current?.document?.source !== source) return;
        if (!response.ok) throw new Error(payload.error || `Analysis returned ${response.status}`);
        session.setRattishEditorState(mergeRattishAnalysisState(session.rattishEditorStateRef.current, payload.document, source));
        const diagnostics = [...(payload.document.diagnostics ?? []), ...(payload.document.preflight?.diagnostics ?? [])];
        const errors = diagnostics.filter(item => item.severity === "error");
        const warnings = diagnostics.filter(item => item.severity === "warning");
        if (errors.length) {
          setTopBarNotice({ type: "error", message: `${errors.length} validation error${errors.length === 1 ? "" : "s"}: ${errors[0].message}` });
        } else if (!payload.document.runnable) {
          setTopBarNotice({ type: "error", message: "Workflow is not runnable. Review Problems and add a runnable node if the graph is empty." });
        } else if (warnings.length) {
          setTopBarNotice({ type: "warning", message: `Workflow is valid with ${warnings.length} warning${warnings.length === 1 ? "" : "s"}. Review Problems.` });
        } else {
          setTopBarNotice({ type: "success", message: "Workflow is valid" });
        }
        return;
      }
      await persistWorkflow(summarizeWorkflow(workflow, dataDir));
      setTopBarNotice({ type: "success", message: "Workflow is valid" });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Workflow validation failed",
      });
    }
  }

  async function loadWorkflowHistory(workflowId) {
    setHistoryState((current) => ({ ...current, error: "", loading: true }));
    try {
      const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflowId)}/history`));
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setHistoryState((current) => ({
        ...current,
        error: "",
        loading: false,
        revisions: payload.revisions ?? [],
      }));
    } catch (error) {
      setHistoryState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to load workflow history",
        loading: false,
      }));
    }
  }

  async function openWorkflowHistory(workflow) {
    if (!workflow?.id) return;
    setHistoryState({
      diff: null,
      error: "",
      loading: true,
      open: true,
      revisions: [],
    });
    await loadWorkflowHistory(workflow.id);
  }

  async function previewWorkflowRevision(workflowId, revisionId) {
    setHistoryState((current) => ({ ...current, error: "" }));
    try {
      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflowId)}/history/${encodeURIComponent(revisionId)}/diff`,
        ),
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setHistoryState((current) => ({ ...current, diff: payload }));
    } catch (error) {
      setHistoryState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to load revision diff",
      }));
    }
  }

  async function restoreWorkflowRevision(workflowId, revisionId, { asCopy = false } = {}) {
    const action = asCopy ? "restore this revision as a copy" : "restore this revision";
    if (!window.confirm(`Are you sure you want to ${action}?`)) return;
    setHistoryState((current) => ({ ...current, error: "", loading: true }));
    try {
      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflowId)}/history/${encodeURIComponent(revisionId)}/restore`,
        ),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asCopy }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      const restored = summarizeWorkflow(payload.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(restored.id);
      setWorkflows((current) => {
        const withoutRestored = current.filter((candidate) => candidate.id !== restored.id);
        return [...withoutRestored, restored];
      });
      openWorkflowGraph(restored);
      setHistoryState((current) => ({ ...current, loading: false, open: false }));
      setTopBarNotice({
        type: "success",
        message: asCopy ? `Restored ${restored.name} as a copy` : `Restored ${restored.name}`,
      });
      loadWorkflows({ silent: true });
    } catch (error) {
      setHistoryState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to restore workflow revision",
        loading: false,
      }));
    }
  }

  async function importWorkflow(file, options = {}) {
    if (!file) return false;
    try {
      if (isRaticodeFile(file)) {
        let projectRoot = options.projectRoot?.trim()
          || activeWorkflow?.projectRoot
          || activeProjectRoot;
        if (!projectRoot) {
          projectRoot = await window.goferDesktop?.workspace?.selectPath?.({
            directoryOnly: true,
          });
        }
        if (!projectRoot) {
          throw new Error("Choose a project folder for the imported workflow.");
        }
        const bundlePath = await window.goferDesktop?.grantDroppedPath?.(file);
        const bundleRequest = bundlePath
          ? {
            bundlePath,
            grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(bundlePath) || undefined,
          }
          : { bundleContent: await fileToBase64(file), filename: file.name };
        const previewResponse = await fetch(apiUrl("/rattish/workflows/import/preview"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(bundleRequest),
        });
        const previewPayload = await previewResponse.json();
        if (!previewResponse.ok) {
          throw new Error(previewPayload.error || `Workflow API returned ${previewResponse.status}`);
        }
        const preview = previewPayload.bundle;
        if (!window.confirm(
          `Import "${preview.workflowName}" into ${projectNameFromPath(projectRoot)}?\n\n`
          + `${preview.files.length} workflow file${preview.files.length === 1 ? "" : "s"} will be added under .raticode.`,
        )) return false;
        const projectGrantId = window.goferDesktop?.workspace?.pathGrantForApi?.(projectRoot) || undefined;
        const importResponse = await fetch(apiUrl("/rattish/workflows/import"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...bundleRequest, projectRoot, projectGrantId }),
        });
        const importPayload = await importResponse.json();
        if (!importResponse.ok) {
          throw new Error(importPayload.error || `Workflow API returned ${importResponse.status}`);
        }
        const imported = summarizeWorkflow(importPayload.workflow, dataDir);
        deletedWorkflowIdsRef.current.delete(imported.id);
        setWorkflows((current) => [
          ...current.filter((candidate) => candidate.id !== imported.id),
          imported,
        ]);
        setActiveProjectRoot(projectRoot);
        setRecentProjectRoots((current) => rememberRecentProject(current, projectRoot));
        openWorkflowGraph(imported);
        setStudioView("graph");
        setTopBarNotice({ type: "success", message: `Imported ${imported.name}` });
        return true;
      }
      if (isBundleFile(file)) {
        const bundleContent = await fileToBase64(file);
        const previewResponse = await fetch(apiUrl("/workflows/import/preview"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ bundleContent, filename: file.name }),
        });
        const previewPayload = await previewResponse.json();
        if (!previewResponse.ok) {
          throw new Error(previewPayload.error || `Workflow API returned ${previewResponse.status}`);
        }
        const plan = previewPayload.import;
        if (!window.confirm(formatBundleImportPreview(plan))) {
          return;
        }
        const importResponse = await fetch(apiUrl("/workflows/import"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ bundleContent, filename: file.name }),
        });
        const importPayload = await importResponse.json();
        if (!importResponse.ok) {
          throw new Error(importPayload.error || `Workflow API returned ${importResponse.status}`);
        }
        const imported = importPayload.import;
        deletedWorkflowIdsRef.current.delete(imported.workflowId);
        await loadWorkflows({ silent: true });
        setActiveWorkflowId(imported.workflowId);
        setTopBarNotice({ type: "success", message: `Imported ${imported.workflowName}` });
        return true;
      }

      const content = await file.text();
      const response = await fetch(apiUrl("/workflows/import"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content, filename: file.name }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

      const nextWorkflow = summarizeWorkflow(payload.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(nextWorkflow.id);
      setWorkflows((current) => [...current, nextWorkflow]);
      openWorkflowGraph(nextWorkflow);
      setTopBarNotice({ type: "success", message: `Imported ${nextWorkflow.name}` });
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to import workflow";
      setTopBarNotice({
        type: "error",
        message,
      });
      if (options.reportDialogError) {
        setCreateState({ saving: false, error: message });
      }
      return false;
    }
  }

  async function importWorkflowFromDialog(file, projectRoot) {
    setCreateState({ saving: true, error: "" });
    const imported = await importWorkflow(file, {
      projectRoot,
      reportDialogError: true,
    });
    if (imported) {
      setCreateDialogOpen(false);
      setCreateState({ saving: false, error: "" });
    } else {
      setCreateState((current) => ({ ...current, saving: false }));
    }
  }

  async function exportWorkflow(workflow) {
    if (!workflow) return;
    const defaultDirectory = workflow.sourceFormat === "rattish" ? workflow.projectRoot : dataDir;
    setExportDialog({
      directory: defaultDirectory || "",
      error: "",
      grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(defaultDirectory) || "",
      saving: false,
      workflow,
    });
  }

  async function chooseExportDirectory() {
    const selectPath = window.goferDesktop?.workspace?.selectPath;
    if (!selectPath) {
      setExportDialog((current) => ({ ...current, error: "Folder selection is unavailable." }));
      return;
    }
    try {
      const directory = await selectPath({
        currentPath: exportDialog.directory || exportDialog.workflow?.projectRoot || dataDir,
        directoryOnly: true,
      });
      if (!directory) return;
      setExportDialog((current) => ({
        ...current,
        directory,
        error: "",
        grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(directory) || "",
      }));
    } catch (error) {
      setExportDialog((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to choose an export folder",
      }));
    }
  }

  async function confirmExportWorkflow(directory) {
    const workflow = exportDialog.workflow;
    if (!workflow || !directory.trim()) return;
    setExportDialog((current) => ({ ...current, error: "", saving: true }));
    try {
      if (workflow.sourceFormat === "rattish" && workflow.projectRoot) {
        await discoverProjectWorkflows(workflow.projectRoot);
      }
      const outputPath = workflowBundlePath(directory, workflow);
      const response = await fetch(
        apiUrl(workflowExportEndpoint(workflow)),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            outputPath,
            grantId: exportDialog.grantId || undefined,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setExportDialog({ directory: "", error: "", grantId: "", saving: false, workflow: null });
      setTopBarNotice({ type: "success", message: `Exported bundle to ${payload.bundlePath}` });
    } catch (error) {
      setExportDialog((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to export workflow bundle",
        saving: false,
      }));
    }
  }

  async function deleteWorkflow(workflow) {
    if (!workflow) return;
    if (!window.confirm(`Delete workflow "${workflow.name}"?`)) return;

    try {
      setCreateState({ saving: false, error: "" });
      deletedWorkflowIdsRef.current.add(workflow.id);
      const pendingTimerId = saveTimersRef.current.get(workflow.id);
      if (pendingTimerId) {
        window.clearTimeout(pendingTimerId);
        saveTimersRef.current.delete(workflow.id);
      }
      dirtyWorkflowsRef.current.delete(workflow.id);
      workflowRevisionsRef.current.delete(workflow.id);
      setDirtyWorkflowsById((current) => withoutKey(current, workflow.id));
      setSaveStatesByWorkflowId((current) => withoutKey(current, workflow.id));
      setRunState((current) =>
        current.workflowId === workflow.id
          ? { running: false, error: "", result: null }
          : current,
      );
      setLogState((current) =>
        activeWorkflow?.id === workflow.id
          ? {
              loading: false,
              error: "",
              text: "",
              path: null,
              nodeOutputs: null,
              nodeOutputsTruncated: false,
              nodeOutputsMaxBytes: null,
              usageSummary: null,
              runEvents: [],
              runNodes: {},
              runs: [],
              selectedRunId: null,
            }
          : current,
      );

      const pendingSave = inFlightSavesRef.current.get(workflow.id);
      if (pendingSave) {
        try {
          await pendingSave;
        } catch {
          // Deletion still proceeds after a failed save.
        }
      }

      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflow.id)}?sourceFormat=${encodeURIComponent(workflow.sourceFormat || "toml")}`,
        ),
        {
          method: "DELETE",
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

      const remainingWorkflows = workflows.filter((candidate) => candidate.id !== workflow.id);
      closeCodeFiles([workflow.sourcePath, ...Object.entries(workflowTabs).filter(([, tab]) => tab.workflowId === workflow.id).map(([path]) => path)]);
      saveWorkflowDraft(workflow.sourcePath, { dirty: false });
      const deletedSession = documentSessionsRef.current.get(workflow.id);
      if (deletedSession) { window.clearTimeout(deletedSession.analysisTimer.current); window.clearTimeout(deletedSession.metadataTimer.current); deletedSession.analysisRequest.current += 1; }
      documentSessionsRef.current.delete(workflow.id);
      setRattishSessions(current => withoutKey(current, workflow.id));
      setRecentCodePaths((current) => removeCodePath(current, workflow.sourcePath));
      setCodeNavigationRequest((current) =>
        current?.path === workflow.sourcePath ? null : current,
      );
      if (
        pendingProjectFileRef.current?.workflowId === workflow.id
        || pendingProjectFileRef.current?.path === workflow.sourcePath
      ) {
        pendingProjectFileRef.current = null;
      }
      if (activeWorkflow?.id === workflow.id) {
        rattishEditorStateRef.current = null;
        setRattishEditorState(null);
      }
      setWorkflows((current) => current.filter((candidate) => candidate.id !== workflow.id));
      setActiveWorkflowId((currentId) =>
        currentId === workflow.id ? remainingWorkflows[0]?.id : currentId,
      );
      setTopBarNotice({ type: "success", message: `Deleted ${workflow.name}` });
    } catch (error) {
      deletedWorkflowIdsRef.current.delete(workflow.id);
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to delete workflow",
      });
    }
  }

  async function renameWorkflow(workflow, nextName) {
    if (!workflow) return;
    if (!nextName || nextName.trim() === workflow.name) return;

    try {
      const dirtyWorkflow = dirtyWorkflowsRef.current.get(workflow.id);
      if (dirtyWorkflow) {
        const savedWorkflow = await saveWorkflow(dirtyWorkflow.workflow, dirtyWorkflow.revision);
        if (!savedWorkflow) return;
      }

      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/rename`),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ name: nextName.trim() }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

      const renamed = summarizeWorkflow(payload.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(renamed.id);
      setWorkflows((current) =>
        current.map((candidate) =>
          candidate.id === workflow.id ? renamed : candidate,
        ),
      );
      setActiveWorkflowId((currentId) =>
        currentId === workflow.id ? renamed.id : currentId,
      );
      setTopBarNotice({ type: "success", message: `Renamed to ${renamed.name}` });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to rename workflow",
      });
    }
  }

  async function duplicateWorkflow(workflow) {
    if (!workflow) return;

    try {
      const dirtyWorkflow = dirtyWorkflowsRef.current.get(workflow.id);
      if (dirtyWorkflow) {
        const savedWorkflow = await saveWorkflow(dirtyWorkflow.workflow, dirtyWorkflow.revision);
        if (!savedWorkflow) return;
      }

      const response = await fetch(
        apiUrl(`/workflows/${encodeURIComponent(workflow.id)}/duplicate`),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({}),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

      const duplicated = summarizeWorkflow(payload.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(duplicated.id);
      setWorkflows((current) => [...current, duplicated]);
      openWorkflowGraph(duplicated);
      setTopBarNotice({ type: "success", message: `Duplicated ${workflow.name}` });
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to duplicate workflow",
      });
    }
  }

  async function chooseApplicationDataDirectory() {
    const choose = window.goferDesktop?.dataDirectory?.choose;
    if (!choose) return;
    try {
      const result = await choose({ currentPath: dataDir });
      if (!result?.dataDir) return;
      setDataDir(result.dataDir);
      setTopBarNotice({ type: "success", message: "Application data directory changed" });
      await loadWorkflows();
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to change the application data directory",
      });
    }
  }

  const panelDocument = rattishEditorState?.document;
  const panelDiagnostics = [
    ...(panelDocument?.diagnostics ?? []),
    ...(panelDocument?.preflight?.diagnostics ?? []),
  ];
  const panelRunResult = runState?.result?.workflowId === activeWorkflow?.id
    ? runState.result
    : null;
  const panelRunEvents = logState.runEvents?.length
    ? logState.runEvents
    : panelRunResult?.runEvents ?? [];
  const panelWorkflowId = pinnedRun?.workflowId || (activeWorkflow?.sourceFormat === "project"
    ? ""
    : activeWorkflow?.id ?? "");
  const panelProjectRoot = activeProjectRoot || "";
  const visibleRunRecords = [...runRegistry.records, ...Object.entries(runStatesById)
    .filter(([id, state]) => state.running && !workflowRunSummary(runRegistry.records, id).active.length)
    .map(([id]) => {
      const workflow = workflows.find(item => item.id === id);
      return { key: `submitting:${id}`, workflowId: id, workflowName: workflow?.name || id,
        projectPath: workflow?.projectRoot || "", runId: "Awaiting runner acknowledgement", status: "submitting", unread: false };
    })];

  const editorWorkflowTabs = Object.fromEntries(Object.entries(workflowTabs).map(([path, tab]) => {
    const target = workflows.find(item => item.id === tab.workflowId);
    const dirty = Boolean(rattishSessions[tab.workflowId]?.document?.dirty);
    const summary = workflowRunSummary(runRegistry.records, tab.workflowId);
    const submitting = Boolean(runStatesById[tab.workflowId]?.running) && !summary.active.length;
    const single = summary.active.length === 1 ? summary.active[0] : null;
    const status = submitting ? "submitting" : summary.active[0]?.status || summary.latest?.status || "ready";
    const action = submitting ? null : single && exactWorkflowRunStopPath(single) ? "stop" : summary.active.length || summary.unread.length ? "review" : "run";
    return [path, { ...tab, name: target?.name ?? tab.name, dirty, status,
      unread: summary.unread.length > 0, unreadFailure: summary.unreadFailures.length > 0,
      statusLabel: `${status}${summary.active.length > 1 ? ` · ${summary.active.length} runs` : ""}${summary.unreadFailures.length ? " · Unread failure" : ""}`,
      action, actionLabel: action === "stop" ? `Stop run ${single.runId}` : action === "review" ? "Review workflow runs" : dirty ? "Save and run" : "Run workflow" }];
  }));

  async function reviewWorkflowRun(record) {
    let target = workflows.find(item => item.id === record.workflowId);
    if (!target && record.projectPath) {
      const payload = await openProjectAtPath(record.projectPath);
      target = payload?.discovered?.find(item => item.id === record.workflowId);
    }
    if (!target) { setTopBarNotice({ type: "error", message: "This workflow is unavailable. Reopen its project to review the run." }); return; }
    openWorkflowGraph(target);
    if (record.status === "submitting") return;
    if (record.queueRun) { runRegistry.review(record.key); return; }
    setPinnedRun(record);
    if (await loadRunLog(record.workflowId, record.runId)) runRegistry.review(record.key);
    window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", { detail: { tab: "timeline" } }));
  }

  async function stopExactWorkflowRun(workflow) {
    const active = workflowRunSummary(runRegistry.records, workflow.id).active;
    if (active.length !== 1 || !exactWorkflowRunStopPath(active[0])) { setRunsOpen(true); return; }
    try { await runRegistry.stop(active[0]); }
    catch (error) { setTopBarNotice({ type: "error", message: error.message }); }
  }

  async function handleWorkflowTabAction(tab, action) {
    const target = workflows.find(item => item.id === tab.workflowId);
    if (!target) return;
    try {
      if (action === "save") await saveWorkflowDocument(target);
      if (action === "run") await runWorkflowNow(target);
      if (action === "stop") await stopExactWorkflowRun(target);
      if (action === "review") setRunsOpen(true);
    } catch (error) { setTopBarNotice({ type: "error", message: error.message }); }
  }

  async function beforeCloseWorkflowViews(paths) {
    for (const target of workflows) {
      const views = codeOpenPaths.filter(path => path === target.sourcePath || workflowTabs[path]?.workflowId === target.id);
      if (!views.some(path => paths.includes(path)) || views.some(path => !paths.includes(path))) continue;
      if (documentSession(target.id).rattishEditorStateRef.current?.document?.dirty) {
        const accepted = await new Promise(resolve => setWorkflowClosePrompt({ resolve, workflow: target, busy: false, error: "" }));
        if (!accepted) return false;
      }
      const session = documentSession(target.id);
      if ((session.rattishMetadataPendingRef.current || session.rattishMetadataSavingRef.current) && !await saveRattishMetadataNow(target.id)) return false;
    }
    return true;
  }

  function renderWorkflowGraph(tab, { visible, active }) {
    const target = workflows.find(item => item.id === tab.workflowId);
    const state = rattishSessions[tab.workflowId];
    if (!target) return <div className="p-6" role="status">This workflow is unavailable. Open its project or refresh workflow discovery.<button type="button" onClick={() => loadWorkflows({ discoverProject: true, projectRoot: tab.projectRoot })}>Retry</button></div>;
    const document = state?.document;
    const reviewingRun = pinnedRun?.workflowId === target.id;
    const snapshot = reviewingRun && logState.workflowId === target.id && logState.selectedRunId === pinnedRun.runId ? logState.graphSnapshot : null;
    const currentSourceMatchesRun = target.sourceFormat !== "rattish" || Boolean(
      document && !document.dirty && logState.workflowId === target.id
      && logState.graphSnapshot?.sourceFingerprint === document.sourceRevision,
    );
    const graphRunState = runStatesById[target.id] || { running: false, error: "", result: null };
    return <WorkflowGraphPane
      active={active} visible={visible} workflow={target}
      saveState={saveStatesByWorkflowId[target.id] || (dirtyWorkflowsById[target.id] ? { status: "saving", error: "" } : undefined)}
      onRetrySave={() => retryWorkflowSave(target.id)}
      onSource={() => editWorkflowFile(target)}
      onReveal={() => selectRecentProject(target.projectRoot)}
    >{toolbarTarget => <>
      {reviewingRun ? <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-xs" role="status">
        <span>{snapshot ? `Run snapshot · ${pinnedRun.runId}` : "This run has no saved graph snapshot. The current graph is shown without historical node status."}</span>
        <button type="button" className="ml-auto rounded px-2 py-1 text-brand hover:bg-slate-100" onClick={() => setPinnedRun(null)}>Current graph</button>
      </div> : <WorkflowHealthPanel doctorState={doctorState} workflow={target} />}
      {state?.loading ? <div className="p-4" role="status">Loading workflow…</div> : null}
      {state?.error ? <div className="p-3 text-red-700" role="alert">{state.error}<button type="button" onClick={() => reloadActiveRattishDocument(target.sourcePath, target)}>Retry</button></div> : null}
      <Suspense fallback={<p role="status" className="p-4 text-sm">Loading graph...</p>}><DagCanvas
        dataDir={dataDir} settings={settings} usedAgentIds={usedAgentIds}
        workflow={snapshot ? autoLayoutWorkflow({ ...target, ...snapshot }) : rattishGraphWorkflow(target, document)} rattishDocument={snapshot ? null : document}
        active={active}
        readOnly={reviewingRun || (target.sourceFormat === "rattish" && !rattishGraphIsValid(document))}
        toolbarTarget={toolbarTarget}
        canvasOverlay={document && !rattishGraphIsValid(document) && !reviewingRun ? (
          <div className="w-full max-w-md rounded-xl bg-white p-6 text-center text-ink shadow-panel" role="alert">
            <AlertCircle size={24} className="mx-auto mb-3 text-amber-600 dark:text-amber-400" aria-hidden="true" />
            <p className="text-base font-semibold">Unable to render the graph</p>
            <p className="mt-2 text-sm text-muted">The workflow definition is invalid. Open the workflow to correct it, or ask Rem to help.</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              <button type="button" className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand" onClick={() => editWorkflowFile(target)}>Open workflow</button>
              <button type="button" className="rounded-md border border-line px-4 py-2 text-sm font-medium hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand" onClick={() => window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
                mode: "ask", autoSend: true, projectRoot: target.projectRoot, path: target.sourcePath,
                version: "Current workflow source, including unsaved edits",
                endLine: (document.source || "").split("\n").length,
                text: `${document.source || ""}\n\nValidation diagnostics:\n${JSON.stringify(document.diagnostics || [], null, 2)}`,
                draft: `Fix the invalid workflow definition in ${target.sourcePath} so the graph can render. Use the attached current source and validation diagnostics, preserve the workflow's intent, and validate the changes. Do not execute the workflow.`,
              } }))}>Ask Rem to fix</button>
            </div>
          </div>
        ) : null}
        logState={(reviewingRun ? snapshot : currentSourceMatchesRun) && target.id === (pinnedRun?.workflowId || activeWorkflow?.id) ? logState : { runs: logState.workflowId === target.id ? logState.runs : [], runEvents: [], runNodes: {} }}
        approvalState={target.id === activeWorkflow?.id ? approvalState : { approvals: [] }}
        notice={target.id === activeWorkflow?.id ? (runState.error ? { type: "error", message: runState.error } : topBarNotice) : {}}
        runState={reviewingRun ? { running: false, error: "", result: null } : currentSourceMatchesRun ? graphRunState : { ...graphRunState, result: null }}
        stopDisabled={workflowRunSummary(runRegistry.records, target.id).active.length !== 1 || !exactWorkflowRunStopPath(workflowRunSummary(runRegistry.records, target.id).active[0])}
        stopTitle="Stop this run"
        onLoadLatestLog={() => loadLatestLog(target.id)}
        onSelectRunLog={(runId) => { setPinnedRun({ workflowId: target.id, runId }); void loadRunLog(target.id, runId); }}
        onStopRunLog={(runId) => stopWorkflowRunLog(target.id, runId)}
        onResumeRunLog={(runId, options) => resumeWorkflowRunLog(target.id, runId, options)}
        onReplayRunLog={(runId, triggerId) => replayWorkflowTriggerLog(target.id, runId, triggerId)}
        onSettingChange={changeSetting} onImportWorkflow={importWorkflow}
        onExportWorkflow={() => exportWorkflow(target)} onRunWorkflow={() => runWorkflowNow(target)}
        onValidateWorkflow={() => validateWorkflow(target)} onStopWorkflow={() => stopExactWorkflowRun(target)}
        onDecideApproval={(approval, decision, notes, by) => decideApproval(target, approval, decision, notes, by)}
        onRattishMutation={(mutations) => mutateActiveRattish(mutations, target)}
        onWorkflowChange={(next) => target.sourceFormat === "rattish" ? updateRattishGraphMetadata(next, target) : updateActiveWorkflow(next)}
      /></Suspense>
    </>}</WorkflowGraphPane>;
  }

  function revealPanelDiagnostic(diagnostic) {
    if (!activeWorkflow || activeWorkflow.sourceFormat !== "rattish") return;
    setCodeOpenPaths((current) => current.includes(activeWorkflow.sourcePath)
      ? current
      : [...current, activeWorkflow.sourcePath]);
    setActiveCodePath(activeWorkflow.sourcePath);
    setCodeEditorOpened(true);
    setStudioView("code");
    window.requestAnimationFrame(() => {
      rattishEditorRef.current?.revealDiagnostic?.(diagnostic);
    });
  }

  function runGlobalMenuAction(action) {
    if (action.startsWith("edit.") || action.startsWith("selection.")) {
      rattishEditorRef.current?.runCommand?.(action);
      return;
    }
    if (action === "file.new") {
      changeStudioView("code");
      setNewCodeFileRequest((current) => current + 1);
    }
    if (action === "file.open") void openFile();
    if (action === "file.openFolder") void openProjectFolder();
    if (action === "file.save") void rattishEditorRef.current?.saveActive?.();
    if (action === "file.close") closeActiveCodeFile();
    if (action === "view.graph") changeStudioView("graph");
    if (action === "view.code") changeStudioView("code");
    if (action === "view.projectPane") setProjectPaneVisible((current) => !current);
    if (action === "view.assistantPane") setAssistantPaneVisible((current) => !current);
    if (action === "view.panel") {
      window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel"));
    }
    if (action === "terminal.toggle") {
      window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", {
        detail: { tab: "terminal" },
      }));
    }
    if (action === "view.zoomIn") setTextZoom((current) => nextTextZoom(current, 1));
    if (action === "view.zoomOut") setTextZoom((current) => nextTextZoom(current, -1));
    if (action === "view.resetZoom") setTextZoom(100);
    if (action === "help.updates") void checkForUpdates();
    if (action === "help.settings") setSettingsOpen(true);
  }

  return (
    <main
      aria-busy={runState.running || logState.loading || undefined}
      className={`flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-canvas text-ink ${theme}`}
    >
      <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {!runState.running && runState.result
          ? runState.result.status === "queued"
            ? "Workflow run queued."
            : `Workflow run completed: ${runState.result.status ?? (runState.result.success ? "success" : "error")}`
          : ""}
      </div>
      <div aria-atomic="true" aria-live="assertive" className="sr-only" role="alert">
        {runState.error}
      </div>
      {workflowClosePrompt ? <Dialog title="Save workflow changes?" onClose={() => {
        if (workflowClosePrompt.busy) return;
        workflowClosePrompt.resolve(false); setWorkflowClosePrompt(null);
      }}>
        <p>{workflowClosePrompt.workflow.name} has unsaved source changes.</p>
        {workflowClosePrompt.error ? <p role="alert" className="mt-2 text-sm text-red-700">{workflowClosePrompt.error}</p> : null}
        <div className="mt-4 flex gap-3">
          <button type="button" disabled={workflowClosePrompt.busy} onClick={() => { workflowClosePrompt.resolve(false); setWorkflowClosePrompt(null); }}>Cancel</button>
          <button type="button" disabled={workflowClosePrompt.busy} onClick={async () => {
            const prompt = workflowClosePrompt;
            setWorkflowClosePrompt(current => ({ ...current, busy: true, error: "" }));
            await reloadActiveRattishDocument(prompt.workflow.sourcePath, prompt.workflow);
            if (documentSession(prompt.workflow.id).rattishEditorStateRef.current?.document?.dirty !== false) {
              setWorkflowClosePrompt(current => ({ ...current, busy: false, error: "Unable to reload workflow.rattish. Your changes are still open." }));
              return;
            }
            setWorkflowClosePrompt(null);
            prompt.resolve(true);
          }}>Discard</button>
          <button type="button" disabled={workflowClosePrompt.busy} onClick={async () => {
            const prompt = workflowClosePrompt;
            setWorkflowClosePrompt(current => ({ ...current, busy: true, error: "" }));
            try {
              const saved = await saveWorkflowDocument(prompt.workflow);
              if (!saved) throw new Error("Unable to save workflow.rattish. Your changes are still open.");
              setWorkflowClosePrompt(null); prompt.resolve(true);
            } catch (error) { setWorkflowClosePrompt(current => ({ ...current, busy: false, error: error.message })); }
          }}>Save</button>
        </div>
      </Dialog> : null}
      <GlobalToolbar
        projectError={projectError}
        onOpenRuns={() => setRunsOpen(current => !current)}
        runSummary={workflowRunSummary(visibleRunRecords)}
        projectRoot={activeProjectRoot}
        activeCodeDocument={activeCodeDocumentState}
        activeCodePath={activeCodePath}
        assistantPaneVisible={assistantPaneVisible}
        projectPaneVisible={projectPaneVisible}
        recentProjectRoots={recentProjectRoots}
        settings={settings}
        settingsOpen={settingsOpen}
        theme={theme}
        updateState={updateState}
        workflow={activeWorkflow}
        view={studioView}
        onApplyUpdate={() => applyUpdate(updateState)}
        onCheckForUpdates={() => checkForUpdates()}
        onOpenHistory={() => activeWorkflow && openWorkflowHistory(activeWorkflow)}
        onSelectProject={selectRecentProject}
        onToggleSettings={() => setSettingsOpen((current) => !current)}
        onToggleTheme={() => changeSetting("appearance.theme", theme === "dark" ? "light" : "dark")}
        onMenuAction={runGlobalMenuAction}
      />
      <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden">
      {<WorkflowSidebar
        activity={sidebarActivity}
        paneVisible={projectPaneVisible}
        activeWorkflow={browsingWorkspace}
        activeWorkflowId={activeWorkflow?.id}
        loading={loadState.loading}
        openingProjectRoot={openingProjectRoot}
        runState={runState}
        settings={settings}
        workflows={workflows}
        view={studioView}
        width={workflowPaneWidth}
        newFileRequest={newCodeFileRequest}
        recentProjectRoots={recentProjectRoots}
        onOpenProject={() => runGlobalMenuAction("file.openFolder")}
        onCreate={() => {
          setCreateState({ saving: false, error: "" });
          setCreateDialogOpen(true);
        }}
        onDeleteWorkflow={deleteWorkflow}
        onDuplicateWorkflow={duplicateWorkflow}
        onCodeFileOpen={(...args) => { openCodeFile(...args); closeCompactPane(); }}
        selectedSwarmId={samePath(selectedSwarm?.projectRoot, swarmProjectRoot) ? selectedSwarm.id : null}
        onOpenSwarm={(id, agentId) => { setSelectedSwarm({ id, agentId, projectRoot: swarmProjectRoot }); closeCompactPane(); }}
        activeCodePath={activeCodePath}
        onCloseCodeFile={closeActiveCodeFile}
        onCodeFilesystemChange={handleCodeFilesystemChange}
        onRefresh={() => loadWorkflows({ discoverProject: true })}
        onRenameWorkflow={renameWorkflow}
        onEditWorkflowFile={editWorkflowFile}
        onSelectProject={selectRecentProject}
        onRemoveRecentProject={removeRecentProject}
        onRunWorkflow={runWorkflowNow}
        onResizeStart={(event) =>
          startPaneResize(event, {
            max: 420,
            min: 240,
            side: "right",
            width: workflowPaneWidth,
            onResize: (width) => changeSetting("layout.workflowPaneWidth", width),
          })
        }
        onResizeKeyDown={(event) =>
          handlePaneResizeKeyDown(event, {
            defaultValue: 272,
            max: 420,
            min: 240,
            onResize: (width) => changeSetting("layout.workflowPaneWidth", width),
            width: workflowPaneWidth,
          })
        }
        onSelect={(id) => { openWorkflowGraph(workflows.find(item => item.id === id)); closeCompactPane(); }}
        onActivityChange={(next) => { setSidebarActivity(next); setProjectPaneVisible(true); }}
        onViewChange={changeStudioView}
      />}

      <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-x border-line bg-[#f9fbfd]">
            {(workflowTabs[activeCodePath] || activeCodePath === activeWorkflow?.sourcePath) && rattishEditorState?.recoveryWarning ? (
              <div role="status" className="shrink-0 border-b border-line bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:bg-amber-950 dark:text-amber-100">
                <strong>Draft recovery: </strong>{rattishEditorState.recoveryWarning}
              </div>
            ) : null}
        {samePath(selectedSwarm?.projectRoot, swarmProjectRoot) ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading swarm...</p>}><SwarmWorkspace selectedAgentId={selectedSwarm.agentId} defaults={settings.assistant} key={`${swarmProjectRoot}:${selectedSwarm.id}`} rootPath={swarmProjectRoot} swarmId={selectedSwarm.id} onSelect={(id) => setSelectedSwarm({ id, projectRoot: swarmProjectRoot })} onClose={() => setSelectedSwarm(null)} /></Suspense> : null}
        <div className={`${samePath(selectedSwarm?.projectRoot, swarmProjectRoot) ? "hidden" : "flex"} min-h-0 flex-1 flex-col`}>
            {!workflowTabs[activeCodePath] && (activeCodePath || sidebarActivity !== "workflows") && topBarNotice?.message ? (
              <div role={topBarNotice.type === "error" ? "alert" : "status"} className="shrink-0 border-b border-line bg-surface px-3 py-2 text-xs text-ink break-words">
                {topBarNotice.message}
              </div>
            ) : null}
            <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading editor...</p>}>
            <CodeWorkspace
              active={!samePath(selectedSwarm?.projectRoot, swarmProjectRoot)}
              activePath={activeCodePath}
              browserTabs={browserTabs}
              navigationRequest={codeNavigationRequest}
              ref={rattishEditorRef}
              openPaths={codeOpenPaths}
              previewPath={previewCodePath}
              recentPaths={recentCodePaths}
              emptyContent={sidebarActivity === "workflows" ? <EmptyWorkspace
                error={loadState.error} loading={loadState.loading} notice={topBarNotice} projectRoot={activeProjectRoot}
                onCreate={() => { setCreateState({ saving: false, error: "" }); setCreateDialogOpen(true); }}
                onImport={importWorkflow} onOpenAssistant={() => { setAssistantPaneVisible(true); setAssistantFocusRequest(value => value + 1); }}
                onOpenProject={() => void openProjectFolder()} onOpenSettings={() => setSettingsOpen(true)}
                onRefresh={() => loadWorkflows({ discoverProject: true })}
              /> : undefined}
              onOpenGraph={(path) => openWorkflowGraph(workflows.find(item => samePath(item.sourcePath, path)))}
              workflowTabs={editorWorkflowTabs}
              renderWorkflowTab={renderWorkflowGraph}
              onWorkflowTabAction={handleWorkflowTabAction}
              onDuplicateWorkflowTab={duplicateWorkflowTab}
              onBeforeCloseWorkflowTabs={beforeCloseWorkflowViews}
              rattishDocuments={Object.fromEntries(workflows.filter(item => item.sourcePath && rattishSessions[item.id]).map(item => [item.sourcePath, rattishSessions[item.id]]))}
              onSaveRattishDocument={(path) => saveWorkflowDocument(workflows.find(item => samePath(item.sourcePath, path)))}
              rattishDocument={rattishEditorState?.document}
              rattishDirty={Boolean(rattishEditorState?.document?.dirty)}
              theme={theme}
              settings={settings}
              workflow={codeWorkspaceWorkflow}
              onActivePathChange={activateEditorPath}
              onActiveDocumentStateChange={setActiveCodeDocumentState}
              onBrowserStateChange={updateBrowserTab}
              onClosePath={closeCodeFile}
              onClosePaths={closeCodeFiles}
              saveBeforeClosePaths={[...terminalEditorRequestsRef.current.keys()]}
              onDocumentStateChange={(nextState, path) => {
                const target = workflows.find(item => samePath(item.sourcePath, path)) ?? codeWorkspaceWorkflow;
                documentSession(target.id).setRattishEditorState(nextState);
                if (nextState?.document?.dirty) pinCodeFile(target.sourcePath);
              }}
              onNewFile={() => setNewCodeFileRequest((current) => current + 1)}
              onOpenBrowser={(options) => openIntegratedBrowser(undefined, options)}
              onOpenFile={() => void openFile()}
              onOpenMarkdownPath={openMarkdownFileLink}
              onOpenProject={() => void openProjectFolder()}
              onOpenPath={(path) => void openRecentCodeFile(path)}
              onOpenPathsChange={setCodeOpenPaths}
              onPinPath={pinCodeFile}
              onRattishContentChange={scheduleRattishAnalysis}
              onRattishDiscard={reloadActiveRattishDocument}
              onRattishSaved={refreshRattishAfterFileSave}
              onSettingChange={changeSetting}
            />
            </Suspense>
        </div>
        {runsOpen ? <RunSummary records={visibleRunRecords} projectPath={activeProjectRoot} onReview={reviewWorkflowRun} onStop={runRegistry.stop} onClose={() => setRunsOpen(false)} onRefresh={runRegistry.refresh} loading={runRegistry.loading} connectionError={runRegistry.error} /> : null}
        {pinnedRun ? <div className="flex items-center justify-between border-t border-line px-3 py-1 text-[11px] text-muted"><span className="truncate">Run logs · {pinnedRun.workflowName} · {pinnedRun.runId}</span><button type="button" onClick={() => setPinnedRun(null)}>Unpin logs</button></div> : null}
        <UnifiedBottomPanel
          diagnostics={panelDiagnostics}
          onSettingChange={changeSetting}
          projectRoot={panelProjectRoot}
          settings={settings}
          theme={theme}
          onRevealDiagnostic={revealPanelDiagnostic}
          timelineProps={{
            error: logState.error,
            loading: logState.loading,
            logPath: logState.path,
            onPruneRuns: panelWorkflowId
              ? (options) => pruneWorkflowRunLogs(panelWorkflowId, options)
              : undefined,
            onReplayRun: panelWorkflowId
              ? (runId, triggerId) => replayWorkflowTriggerLog(panelWorkflowId, runId, triggerId)
              : undefined,
            onResumeRun: panelWorkflowId
              ? (runId, options) => resumeWorkflowRunLog(panelWorkflowId, runId, options)
              : undefined,
            onRetentionSettingsChange: panelWorkflowId
              ? (nextSettings) => saveRetentionSettingsForWorkflow(panelWorkflowId, nextSettings)
              : undefined,
            onSelectRun: panelWorkflowId
              ? (runId) => loadRunLog(panelWorkflowId, runId)
              : undefined,
            onShowLatest: panelWorkflowId
              ? () => loadLatestLog(panelWorkflowId)
              : undefined,
            onStopRun: panelWorkflowId
              ? (runId) => stopWorkflowRunLog(panelWorkflowId, runId)
              : undefined,
            retentionSettings,
            runEvents: panelRunEvents,
            runs: logState.runs ?? [],
            selectedRunId: logState.selectedRunId,
            text: logState.text || panelRunResult?.logText || "",
            title: "Workflow log",
            usageSummary: logState.usageSummary ?? panelRunResult?.usageSummary ?? null,
          }}
        />
      </section>

      {settingsOpen ? (
        <Suspense fallback={<p role="status">Loading settings...</p>}>
        <SettingsPopover
          initialCategory={settingsCategory}
          dataDir={dataDir}
          open
          settings={settings}
          onChange={changeSetting}
          onChooseDataDirectory={chooseApplicationDataDirectory}
          onClose={() => { setSettingsOpen(false); setSettingsCategory("general"); }}
          onResetAll={async () => {
            try { await window.goferDesktop?.rem?.configure?.("reset", true); setSettings(defaultSettingsSnapshot()); }
            catch (error) { reportArchiveError(error); }
          }}
        />
        </Suspense>
      ) : null}

      <div className={assistantPaneVisible ? "contents" : "hidden"} aria-hidden={!assistantPaneVisible}>
        <ChatPane
          visible={assistantPaneVisible}
          reducedMotion={settings.appearance.reducedMotion}
          composerFocusRequest={assistantFocusRequest}
          memorySettings={settings.memory}
          assistantDefaults={settings.assistant}
          audioInputDeviceId={settings.devices.audioInputId}
          recentProjectRoots={recentProjectRoots}
          width={chatPaneWidth}
          activeWorkflowId={activeWorkflow?.id}
          activeProjectRoot={activeProjectRoot}
          workflow={activeWorkflow}
          workflows={workflows}
          openFiles={editorFileReferences(codeOpenPaths, workflowTabs)}
          onOpenMarkdownLink={(href, projectRoot) => openMarkdownFileLink(
            href,
            assistantMarkdownSourcePath(projectRoot),
          )}
          onOpenFile={openAssistantFile}
          onResponseComplete={(projectRoot) => loadWorkflows({
            discoverProject: true,
            projectRoot,
            silent: true,
          })}
          onResizeStart={(event) =>
            startPaneResize(event, {
              max: 520,
              min: 300,
              side: "left",
              width: chatPaneWidth,
              onResize: (width) => changeSetting("layout.assistantPaneWidth", width),
            })
          }
          onResizeKeyDown={(event) =>
            handlePaneResizeKeyDown(event, {
              defaultValue: 380,
              max: 520,
              min: 300,
              onResize: (width) => changeSetting("layout.assistantPaneWidth", width),
              width: chatPaneWidth,
            })
          }
        />
      </div>
      </div>
      {runPreview ? (
        <RunPreviewDialog
          plan={runPreview.plan}
          workflow={runPreview.workflow}
          onCancel={() => setRunPreview(null)}
          initialParameters={runPreview.parameters}
          onRun={(parameters) =>
            executeWorkflowRun(runPreview.workflow, runPreview.triggerContext, parameters)
          }
          executionMode={executionMode}
          onExecutionModeChange={(mode) => changeSetting("general.executionMode", mode)}
          queueState={queueState}
        />
      ) : null}
      <CreateWorkflowDialog
        defaultProjectRoot={panelProjectRoot}
        error={createState.error}
        open={createDialogOpen}
        saving={createState.saving}
        onClose={() => {
          if (!createState.saving) {
            setCreateDialogOpen(false);
            setCreateState({ saving: false, error: "" });
          }
        }}
        onCreate={createWorkflow}
        onImport={importWorkflowFromDialog}
      />

      <ExportWorkflowDialog
        directory={exportDialog.directory}
        error={exportDialog.error}
        open={Boolean(exportDialog.workflow)}
        saving={exportDialog.saving}
        workflow={exportDialog.workflow}
        onClose={() => {
          if (!exportDialog.saving) {
            setExportDialog({ directory: "", error: "", grantId: "", saving: false, workflow: null });
          }
        }}
        onChooseFolder={chooseExportDirectory}
        onExport={confirmExportWorkflow}
      />

      {historyState.open && activeWorkflow ? (
        <WorkflowHistoryDialog
          diff={historyState.diff}
          error={historyState.error}
          loading={historyState.loading}
          revisions={historyState.revisions}
          workflow={activeWorkflow}
          onClose={() => setHistoryState((current) => ({ ...current, open: false }))}
          onRefresh={() => loadWorkflowHistory(activeWorkflow.id)}
          onPreview={(revisionId) => previewWorkflowRevision(activeWorkflow.id, revisionId)}
          onRestore={(revisionId, options) =>
            restoreWorkflowRevision(activeWorkflow.id, revisionId, options)
          }
        />
      ) : null}
      <TextZoomBar value={textZoom} />
    </main>
  );
}

export function loadTextZoom(storage = globalThis.window?.localStorage) {
  try {
    const stored = Number(brandCompat.readBrandedStorage(storage, TEXT_ZOOM_STORAGE_KEY));
    return Number.isFinite(stored) && stored
      ? clampNumber(stored, TEXT_ZOOM_MIN, TEXT_ZOOM_MAX)
      : 100;
  } catch {
    return 100;
  }
}

export function nextTextZoom(current, direction) {
  const value = Number(current);
  const normalized = Number.isFinite(value)
    ? Math.round(value / TEXT_ZOOM_STEP) * TEXT_ZOOM_STEP
    : 100;
  return clampNumber(
    normalized + Math.sign(direction) * TEXT_ZOOM_STEP,
    TEXT_ZOOM_MIN,
    TEXT_ZOOM_MAX,
  );
}

export function textZoomDirection(event) {
  if (
    !(event.ctrlKey || event.metaKey)
    || event.altKey
    || (event.shiftKey && event.key !== "+")
  ) {
    return 0;
  }
  const key = String(event.key ?? "");
  const code = String(event.code ?? "");
  if (key === "+" || key === "=" || code === "Equal" || code === "NumpadAdd") return 1;
  if (key === "-" || key === "_" || code === "Minus" || code === "NumpadSubtract") return -1;
  return 0;
}

export function eventTargetsGraphVisualization(event) {
  let target = event?.target;
  while (target) {
    if (target.getAttribute?.("data-graph-visualization") === "true") return true;
    target = target.parentNode;
  }
  return false;
}

export function TextZoomBar({ value = 100 }) {
  const progress = ((value - TEXT_ZOOM_MIN) / (TEXT_ZOOM_MAX - TEXT_ZOOM_MIN)) * 100;
  const description = `App zoom ${value}%.`;
  return (
    <div
      aria-label={description}
      aria-valuemax={TEXT_ZOOM_MAX}
      aria-valuemin={TEXT_ZOOM_MIN}
      aria-valuenow={value}
      className="pointer-events-none fixed bottom-1 right-3 z-[100] flex items-center gap-1.5 text-[9px] tabular-nums text-muted opacity-60"
      data-text-zoom
      role="meter"
      title={description}
    >
      <span>Zoom {value}%</span>
      <span aria-hidden="true" className="relative h-px w-14 overflow-hidden bg-line">
        <span
          className="absolute inset-y-0 left-0 bg-current"
          style={{ width: `${progress}%` }}
        />
      </span>
    </div>
  );
}

export function workflowRunFailureMessage(run) {
  const directError = typeof run?.error === "string" ? run.error : run?.error?.message;
  if (directError) return directError;
  const failedNode = Object.values(run?.runNodes ?? {}).find(
    (node) => node?.status === "error" || node?.status === "failed",
  );
  const nodeError = typeof failedNode?.error === "string"
    ? failedNode.error
    : failedNode?.error?.message ?? failedNode?.message;
  if (nodeError) return nodeError;
  return "Workflow failed. Select the failed node to inspect its output.";
}

function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function paneWidthForKey(
  key,
  width,
  { defaultValue, max, min, shiftKey = false, step = 10 },
) {
  if (key === "Enter") return clampNumber(defaultValue, min, max);
  if (key === "Home") return min;
  if (key === "End") return max;
  const amount = shiftKey ? step * 4 : step;
  if (key === "ArrowLeft") return clampNumber(width - amount, min, max);
  if (key === "ArrowRight") return clampNumber(width + amount, min, max);
  return null;
}

function handlePaneResizeKeyDown(event, options) {
  const nextWidth = paneWidthForKey(event.key, options.width, {
    ...options,
    shiftKey: event.shiftKey,
  });
  if (nextWidth === null) return;
  event.preventDefault();
  options.onResize(nextWidth);
}

function startPaneResize(event, { max, min, onResize, side, width }) {
  event.preventDefault();
  event.stopPropagation();

  const startX = event.clientX;
  const startWidth = width;
  const previousCursor = document.body.style.cursor;
  const previousUserSelect = document.body.style.userSelect;

  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";

  function handlePointerMove(moveEvent) {
    const delta = moveEvent.clientX - startX;
    const nextWidth = side === "left" ? startWidth - delta : startWidth + delta;
    onResize(clampNumber(nextWidth, min, max));
  }

  function handlePointerUp() {
    document.body.style.cursor = previousCursor;
    document.body.style.userSelect = previousUserSelect;
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", handlePointerUp);
  }

  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", handlePointerUp);
}

export function summarizeWorkflow(workflow, dataDir = "") {
  if (workflow.invalid) {
    return {
      ...workflow,
      agents: workflow.agents ?? {},
      nodes: workflow.nodes ?? [],
      edges: workflow.edges ?? [],
      description: workflow.description || `Invalid ${workflow.sourceFormat === "rattish" ? "Rattish" : "workflow TOML"}: ${workflow.validationError}`,
      status: "Error",
      tags: ["error", "invalid"],
    };
  }
  const agentCount = agentIdsForWorkflow(workflow).length;
  const operationTypes = [...new Set((workflow.nodes ?? []).map((node) => node.type))].sort();
  const status = workflow.status ?? "Ready";
  const watchPath = workflow.watch?.path
    ? resolveDisplayPath(workflow.watch.path, dataDir)
    : "";
  return {
    ...workflow,
    description: `${workflow.nodes.length} nodes, ${workflow.edges.length} edges, ${agentCount} agents.${
      workflow.schedule ? ` Scheduled with ${workflow.schedule.cron_expression}.` : ""
    }${workflow.watch ? ` Watching ${watchPath}.` : ""
    }${Object.values(workflow.webhooks ?? {}).some((config) => config?.enabled) ? " API trigger enabled." : ""
    }`,
    status,
    tags: [status.toLowerCase(), ...operationTypes.slice(0, 2)],
  };
}

export function rattishGraphWorkflow(workflow, document) {
  if (
    !workflow ||
    workflow.sourceFormat !== "rattish" ||
    !document?.graph ||
    document.workflowId !== workflow.id
  ) {
    return workflow;
  }
  const positions = document.metadata?.canvas?.nodes ?? {};
  const nodes = (document.graph.nodes ?? []).map((node) => {
    const type = String(node.type || "unknown").replaceAll("-", "_");
    const execution = node.execution ?? {};
    return {
      id: node.id,
      label: node.label || node.id,
      type,
      operation: { type, ...(node.configuration ?? {}) },
      settings: {
        allowFailure: Boolean(execution.allow_fail),
        awaitAllInputs: true,
        failFast: false,
        forEach: "",
        maxConcurrency: execution.max_concurrency ?? 1,
        pipeOutput: false,
        retryCount: execution.retry_count ?? 0,
        retryDelaySeconds: (execution.retry_delay_ms ?? 0) / 1000,
        timeoutSeconds:
          execution.timeout_ms === null || execution.timeout_ms === undefined
            ? ""
            : execution.timeout_ms / 1000,
      },
      x: positions[node.id]?.x ?? 0,
      y: positions[node.id]?.y ?? 0,
      rattishStatus: node.status,
      rattish: node,
    };
  });
  const edges = (document.graph.edges ?? []).map((edge) => ({
    id: edge.id,
    from: edge.from,
    to: edge.to,
    condition: edge.mode === "when" ? "output_field" : "always",
    displayLabel:
      edge.mode === "when"
        ? edge.predicateSource
          ? `when ${edge.predicateSource}`
          : "when"
        : edge.mode === "unconditional"
          ? "always"
          : edge.mode,
    mode: edge.mode,
    predicate: edge.predicate,
    predicateSource: edge.predicateSource,
    sourceSpan: edge.sourceSpan,
  }));
  const validationDiagnostics = [
    ...(document.diagnostics ?? []).map((diagnostic) => ({
      ...diagnostic,
      id: diagnostic.code,
      subject: "workflow",
      targetId: workflow.id,
      targetType: "workflow",
    })),
    ...(document.graph.nodes ?? []).flatMap((node) =>
      (node.diagnostics ?? []).map((diagnostic) => ({
        ...diagnostic,
        id: diagnostic.code,
        subject: `node:${node.id}`,
        targetId: node.id,
        targetType: "node",
      })),
    ),
    ...(document.graph.edges ?? [])
      .filter((edge) => edge.status !== "valid")
      .map((edge) => ({
        id: "RATTISH_ROUTE_UNRESOLVED",
        message: `Route target ${edge.to} is unresolved.`,
        severity: "error",
        subject: `edge:${edge.id}`,
        targetId: edge.id,
        targetType: "edge",
      })),
  ];
  const laidOut = autoLayoutWorkflow({ ...workflow, nodes, edges });
  return {
    ...workflow,
    ...laidOut,
    invalid: false,
    name: document.workflow?.name || workflow.name,
    nodes: laidOut.nodes.map((node) =>
      positions[node.id]
        ? { ...node, x: positions[node.id].x, y: positions[node.id].y }
        : node,
    ),
    validationDiagnostics,
  };
}

function agentIdsForWorkflow(workflow) {
  return [
    ...new Set(
      (workflow.nodes ?? [])
        .filter((node) => node.type === "agent")
        .map((node) => node.operation?.agent_id)
        .filter(Boolean),
    ),
  ];
}

export function mergeSavedWorkflow(localWorkflow, savedWorkflow) {
  const localNodesById = Object.fromEntries(
    (localWorkflow.nodes ?? []).map((node) => [node.id, node]),
  );
  return {
    ...localWorkflow,
    ...savedWorkflow,
    nodes: (savedWorkflow.nodes ?? []).map((node) => ({
      ...node,
      x: localNodesById[node.id]?.x ?? node.x,
      y: localNodesById[node.id]?.y ?? node.y,
      label: localNodesById[node.id]?.label ?? node.label,
    })),
  };
}

export function preserveLocalWorkflow(remoteWorkflows, localWorkflow, dataDir = "") {
  const foundWorkflow = remoteWorkflows.some((workflow) => workflow.id === localWorkflow.id);
  if (!foundWorkflow) {
    return [...remoteWorkflows, localWorkflow];
  }
  return remoteWorkflows.map((workflow) =>
    workflow.id === localWorkflow.id
      ? summarizeWorkflow({
          ...localWorkflow,
          sourcePath: workflow.sourcePath ?? localWorkflow.sourcePath,
          sourceFormat: workflow.sourceFormat ?? localWorkflow.sourceFormat,
          status: workflow.status ?? localWorkflow.status,
          updatedAt: workflow.updatedAt ?? localWorkflow.updatedAt,
          projectRoot: workflow.projectRoot ?? localWorkflow.projectRoot,
          projectName: workflow.projectRoot
            ? projectNameFromPath(workflow.projectRoot)
            : localWorkflow.projectName,
          workflowRoot: workflow.workflowRoot ?? localWorkflow.workflowRoot,
        }, dataDir)
      : workflow,
  );
}

export function workflowPayloadForSave(workflow) {
  const { parameters, ...canonicalWorkflow } = workflow;
  return {
    ...canonicalWorkflow,
    inputs: workflow.inputs ?? parameters ?? {},
    filesystemAccess: normalizeWorkflowFilesystemAccess(workflow.filesystemAccess),
    nodes: (workflow.nodes ?? []).map((node) => ({
      ...node,
      x: node.x ?? 0,
      y: node.y ?? 0,
    })),
    edges: workflow.edges ?? [],
    agents: workflow.agents ?? {},
  };
}

export function normalizeWorkflowFilesystemAccess(entries = []) {
  const seen = new Set();
  return (entries ?? [])
    .map((entry) => ({
      path: String(entry?.path ?? "").trim(),
      read: entry?.read ?? true,
      write: entry?.write ?? true,
      execute: entry?.execute ?? false,
    }))
    .filter((entry) => {
      const key = entry.path.replace(/\\/g, "/").replace(/\/+$/, "");
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

export function workflowPlanRequest(workflowId, triggerContext = {}, parameters = {}) {
  const body = { triggerContext };
  if (Object.keys(parameters ?? {}).length > 0) {
    body.inputs = parameters;
  }
  return {
    url: apiUrl(`/workflows/${encodeURIComponent(workflowId)}/plan`),
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  };
}

export function workflowRunRequest(
  workflowId,
  { dryRun = false, triggerContext = {}, parameters = {}, background = false, expectedRevision } = {},
) {
  const body = { dryRun, triggerContext };
  if (background) body.background = true;
  if (expectedRevision) body.expectedRevision = expectedRevision;
  if (Object.keys(parameters ?? {}).length > 0) {
    body.inputs = parameters;
  }
  return {
    url: apiUrl(`/workflows/${encodeURIComponent(workflowId)}/run`),
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  };
}

export function workflowResumeRequest(
  workflowId,
  runId,
  { force = false, fromNode = null, onlyNode = null, skipCache = false, triggerContext = {} } = {},
) {
  return {
    url: apiUrl(
      `/workflows/${encodeURIComponent(workflowId)}/runs/${encodeURIComponent(runId)}/resume`,
    ),
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ force, fromNode, onlyNode, skipCache, triggerContext }),
    },
  };
}

export function workflowReplayTriggerRequest(workflowId, runId, triggerId = null) {
  const encodedWorkflowId = encodeURIComponent(workflowId);
  const encodedTriggerId = encodeURIComponent(triggerId || "default");
  return {
    url: apiUrl(
      `/workflows/${encodedWorkflowId}/webhooks/${encodedTriggerId}/replay`,
    ),
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ runId }),
    },
  };
}

export function workflowLogUrls(workflowId, runId = null) {
  const encodedWorkflowId = encodeURIComponent(workflowId);
  const selectedParams = new URLSearchParams({
    tailBytes: String(RUN_LOG_TAIL_BYTES),
    details: "0",
  });
  return {
    latest: apiUrl(`/workflows/${encodedWorkflowId}/logs/latest`),
    runs: apiUrl(`/workflows/${encodedWorkflowId}/logs`),
    selected: runId
      ? `${apiUrl(
          `/workflows/${encodedWorkflowId}/logs/${encodeURIComponent(runId)}`,
        )}?${selectedParams}`
      : null,
  };
}

export function chatStreamRequestBody({ effort, provider, model, messages, workflow, permissionMode, conversationId, turnId }) {
  return {
    provider,
    model,
    ...(conversationId && turnId ? { conversationId, turnId } : {}),
    ...(effort ? { effort } : {}),
    ...(permissionMode ? { permissionMode } : {}),
    messages,
    workflow,
  };
}

export function workflowIdsAfterDelete(workflows, deletedWorkflowId) {
  return workflows
    .filter((workflow) => workflow.id !== deletedWorkflowId)
    .map((workflow) => workflow.id);
}

export function nextActiveWorkflowIdAfterDelete(workflows, activeWorkflowId, deletedWorkflowId) {
  if (activeWorkflowId !== deletedWorkflowId) return activeWorkflowId;
  return workflows.find((workflow) => workflow.id !== deletedWorkflowId)?.id;
}

function withoutKey(record, key) {
  const nextRecord = { ...record };
  delete nextRecord[key];
  return nextRecord;
}

function isUrlPath(pathValue = "") {
  return /^[a-z][a-z0-9+.-]*:/i.test(String(pathValue));
}

function isAbsolutePath(pathValue = "") {
  const value = String(pathValue);
  return (
    value.startsWith("/") ||
    value.startsWith("\\\\") ||
    /^[A-Za-z]:[\\/]/.test(value)
  );
}

function resolveDisplayPath(pathValue = "", basePath = "") {
  const value = String(pathValue ?? "").trim();
  if (!value || isUrlPath(value) || isAbsolutePath(value)) {
    return value;
  }
  if (!basePath) return value;
  if (value === ".") return basePath;
  const separator = String(basePath).includes("\\") && !String(basePath).includes("/") ? "\\" : "/";
  return `${String(basePath).replace(/[\\/]+$/, "")}${separator}${value.replace(/^[\\/]+/, "")}`;
}

export function useResponsivePanes() {
  const [compact, setCompact] = useState(() => (globalThis.window?.innerWidth ?? 1280) < 1000);
  const compactRef = useRef(compact);
  compactRef.current = compact;
  const [desktopProjectVisible, setDesktopProjectVisible] = useState(true);
  const [desktopAssistantVisible, setDesktopAssistantVisible] = useState(true);
  const [compactPane, setCompactPane] = useState("");
  useEffect(() => {
    const resize = () => setCompact(window.innerWidth < 1000);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => setCompactPane(""), [compact]);
  const setProjectPaneVisible = useCallback((next) => {
    if (!compactRef.current) { setDesktopProjectVisible(next); return; }
    setCompactPane((current) => {
      const visible = typeof next === "function" ? next(current === "project") : next;
      return visible ? "project" : current === "project" ? "" : current;
    });
  }, []);
  const setAssistantPaneVisible = useCallback((next) => {
    if (!compactRef.current) { setDesktopAssistantVisible(next); return; }
    setCompactPane((current) => {
      const visible = typeof next === "function" ? next(current === "assistant") : next;
      return visible ? "assistant" : current === "assistant" ? "" : current;
    });
  }, []);
  return {
    projectPaneVisible: compact ? compactPane === "project" : desktopProjectVisible,
    assistantPaneVisible: compact ? compactPane === "assistant" : desktopAssistantVisible,
    setProjectPaneVisible,
    setAssistantPaneVisible,
    closeCompactPane: () => setCompactPane(""),
  };
}

export function WorkflowGraphPane({ workflow, active, visible, saveState, onRetrySave, onSource, onReveal, children }) {
  const [toolbarTarget, setToolbarTarget] = useState(null);
  return <section className="flex min-h-0 flex-1 flex-col overflow-hidden" aria-label={`${workflow.name} graph`} data-graph-active={active} data-graph-visible={visible}>
    <TopBar workflow={workflow} view="graph" saveState={saveState} onGraphToolbarTargetChange={setToolbarTarget} onRetrySave={onRetrySave} />
    <div className="flex min-w-0 shrink-0 items-center gap-3 border-b border-line px-3 py-1.5 text-xs [&>button]:shrink-0 [&>button]:whitespace-nowrap">
      <span className="min-w-0 flex-1 truncate" title={workflow.sourcePath}>{workflow.sourcePath}</span>
      <button type="button" onClick={onSource}>Source</button>
      <button type="button" onClick={onReveal}>Reveal in project</button>
    </div>
    {children(toolbarTarget)}
  </section>;
}

export function WorkflowSidebar({
  openingProjectRoot = "",
  paneVisible = true,
  activity,
  onActivityChange,
  activeCodePath,
  activeWorkflow,
  activeWorkflowId,
  loading,
  runState,
  settings,
  workflows,
  view = "graph",
  newFileRequest,
  recentProjectRoots,
  onCodeFileOpen,
  onOpenSwarm,
  selectedSwarmId,
  onCloseCodeFile,
  onCodeFilesystemChange,
  onCreate,
  onOpenProject,
  onDeleteWorkflow,
  onDuplicateWorkflow,
  onEditWorkflowFile,
  onRefresh,
  onRenameWorkflow,
  onSelectProject,
  onRemoveRecentProject,
  onResizeKeyDown,
  onResizeStart,
  onRunWorkflow,
  onSelect,
  width,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [projectMenu, setProjectMenu] = useState(null);
  const [copiedProjectRoot, setCopiedProjectRoot] = useState("");
  const [projectLabels, setProjectLabels] = useState(loadProjectLabels);
  const [renamingProjectRoot, setRenamingProjectRoot] = useState("");
  const [projectLabelDraft, setProjectLabelDraft] = useState("");
  const workflowGroups = useMemo(
    () => groupWorkflowsByProject((workflows ?? []).filter((item) => !activeWorkflow?.projectRoot || samePath(item.projectRoot, activeWorkflow.projectRoot)), projectLabels),
    [activeWorkflow?.projectRoot, projectLabels, workflows],
  );
  const recentProjects = useMemo(
    () => mergeRecentProjects([], recentProjectRoots ?? []).map((root) => ({
      name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
      root,
    })),
    [projectLabels, recentProjectRoots],
  );
  const [localActivity, setLocalActivity] = useState(() => settings?.general?.initialActivity || (view === "code" ? "files" : "workflows"));
  const selectedActivity = activity || localActivity;
  const selectActivity = (next) => { setLocalActivity(next); onActivityChange?.(next); };

  useEffect(() => {
    try {
      window.localStorage?.setItem(PROJECT_LABELS_STORAGE_KEY, JSON.stringify(projectLabels));
    } catch {
      // Labels still work for this session when browser storage is unavailable.
    }
  }, [projectLabels]);

  async function copyProjectRoot(root) {
    try {
      await navigator.clipboard.writeText(root);
      setCopiedProjectRoot(root);
      window.setTimeout(() => setCopiedProjectRoot(""), 1400);
    } catch {
      // The menu retains the path as a title so it can still be copied manually.
      return;
    }
    setProjectMenu(null);
  }

  async function openProjectRoot(root) {
    await window.goferDesktop?.workspace?.openPath?.(root);
    setProjectMenu(null);
  }

  useEffect(() => {
    if (!projectMenu) return undefined;
    const close = () => setProjectMenu(null);
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setProjectMenu(null);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("blur", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("blur", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [projectMenu]);

  function showProjectMenu(event, group) {
    event.preventDefault();
    event.stopPropagation();
    setProjectMenu({
      ...projectMenuPosition(event.clientX, event.clientY),
      name: group.name,
      root: group.root,
    });
  }

  function startProjectRename(group) {
    setProjectLabelDraft(group.name);
    setRenamingProjectRoot(group.root);
    setProjectMenu(null);
  }

  function commitProjectRename(root, nextLabel = projectLabelDraft) {
    const label = nextLabel.trim();
    const folderName = projectNameFromPath(root);
    setProjectLabels((current) => {
      return withPathValue(current, root, !label || label === folderName ? undefined : label);
    });
    setRenamingProjectRoot("");
    setProjectLabelDraft("");
  }

  function cancelProjectRename() {
    setRenamingProjectRoot("");
    setProjectLabelDraft("");
  }

  return (
    <aside
      className="studio-sidebar relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-r border-line bg-white"
      style={{ width: paneVisible ? width : 41 }}
    >
      <div
        hidden={!paneVisible}
        aria-label="Resize workflows pane"
        aria-orientation="vertical"
        aria-valuemax={420}
        aria-valuemin={240}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels wide`}
        className="absolute right-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
        role="separator"
        tabIndex={paneVisible ? 0 : -1}
        title="Resize workflows pane"
        onKeyDown={onResizeKeyDown}
        onPointerDown={onResizeStart}
      />
      <div className={`px-3.5 pb-2 pt-3.5 ${paneVisible ? "" : "hidden"}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <RaticodeMark className="h-10 w-10" />
            <div>
              <h1 className="text-[13px] font-semibold leading-tight">Raticode</h1>
              <p className="text-[11px] leading-tight text-muted">
                Project workspace
              </p>
            </div>
          </div>
          <button
            className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Refresh workflows"
            type="button"
            onClick={onRefresh}
          >
            {loading ? <Loader2 size={18} className="animate-spin" /> : <RefreshCw size={18} />}
          </button>
        </div>


      </div>

      {paneVisible ? <div className="relative z-30 px-3.5 pb-2">
        <RecentProjectSelector
          projectRoot={activeWorkflow?.projectRoot || ""}
          openingProjectRoot={openingProjectRoot}
          recentProjectRoots={recentProjectRoots}
          onSelectProject={onSelectProject}
          onRemoveRecentProject={onRemoveRecentProject}
          onOpenProject={onOpenProject}
        />
      </div> : null}

      {paneVisible && openingProjectRoot ? (
        <div role="status" className="flex items-center gap-2 px-3.5 py-2 text-xs text-muted">
          <Loader2 aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" size={14} />
          <span className="truncate" title={openingProjectRoot}>Opening {projectNameFromPath(openingProjectRoot)}...</span>
        </div>
      ) : null}
      <div aria-busy={Boolean(openingProjectRoot)} className="workflow-scrollbar relative min-h-0 flex-1 overflow-hidden pb-3 pt-1">
        <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading explorer...</p>}>
        <CodeFileExplorer
            activeFilePath={activeCodePath}
            newFileRequest={newFileRequest}
            recentProjects={recentProjects}
            settings={settings}
            workflow={activeWorkflow}
            sidebarView={selectedActivity}
            onSidebarViewChange={selectActivity}
            paneVisible={paneVisible}
            hideProjectSelector
            onFilesystemChange={onCodeFilesystemChange}
            onCloseActiveFile={onCloseCodeFile}
            onOpenFile={onCodeFileOpen}
            onOpenSwarm={onOpenSwarm}
            selectedSwarmId={selectedSwarmId}
            onSelectProject={onSelectProject}
            onRemoveRecentProject={onRemoveRecentProject}
            workflowsHeader={
              <button
                className="flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 text-xs font-semibold text-white transition hover:bg-indigo-700"
                title="New Workflow"
                type="button"
                onClick={onCreate}
              >
                <Plus size={15} />
                New Workflow
              </button>
            }
            workflowsContent={workflowGroups.length ? (
          workflowGroups.map(({ id, name, items, root }) => {
            const collapsed = Boolean(collapsedGroups[id]);
            return (
              <section
                key={id}
                className="mb-1 rounded-lg"
                aria-label={`${name} workflows`}
                onContextMenu={(event) => showProjectMenu(event, { name, root })}
              >
                <div className="group/folder flex h-8 items-center rounded-lg px-1.5 transition hover:bg-slate-100">
                  <button
                    aria-expanded={!collapsed}
                    className="flex shrink-0 items-center gap-1.5 text-left text-xs font-semibold text-ink"
                    type="button"
                    onClick={() =>
                      setCollapsedGroups((current) => ({ ...current, [id]: !current[id] }))
                    }
                  >
                    <ChevronDown
                      aria-hidden="true"
                      className={`shrink-0 text-muted transition ${collapsed ? "-rotate-90" : ""}`}
                      size={13}
                    />
                    <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={13} />
                  </button>
                  {renamingProjectRoot === root ? (
                    <input
                      autoFocus
                      aria-label={`Project label for ${projectNameFromPath(root)}`}
                      className="ml-1 h-6 min-w-0 flex-1 rounded-md border border-indigo-300 bg-white px-1.5 text-xs font-semibold text-ink outline-none ring-2 ring-indigo-100"
                      maxLength={120}
                      value={projectLabelDraft}
                      onBlur={(event) => commitProjectRename(root, event.currentTarget.value)}
                      onChange={(event) => setProjectLabelDraft(event.target.value)}
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") event.currentTarget.blur();
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelProjectRename();
                        }
                      }}
                    />
                  ) : (
                    <button
                      className="min-w-0 flex-1 truncate pl-1 text-left text-xs font-semibold text-ink"
                      title={`${name}\n${root}`}
                      type="button"
                      onClick={() =>
                        setCollapsedGroups((current) => ({ ...current, [id]: !current[id] }))
                      }
                    >
                      {name}
                    </button>
                  )}
                  <span className="text-[10px] font-medium text-muted">{items.length}</span>
                  <button
                    aria-haspopup="menu"
                    className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted opacity-0 transition hover:bg-white hover:text-ink group-hover/folder:opacity-100 focus:opacity-100"
                    title={`${name} project actions`}
                    type="button"
                    onClick={(event) => {
                      const bounds = event.currentTarget.getBoundingClientRect();
                      event.stopPropagation();
                      setProjectMenu({
                        ...projectMenuPosition(bounds.right, bounds.bottom),
                        name,
                        root,
                      });
                    }}
                  >
                    <MoreVertical size={13} />
                  </button>
                </div>
                {!collapsed ? (
                  <div className="ml-3 space-y-0.5 border-l border-line py-0.5 pl-1.5">
                    {items.map((workflow) => (
                      <WorkflowListItem
                        key={workflow.id}
                        active={workflow.id === activeWorkflowId}
                        status={
                          runState?.running && runState.workflowId === workflow.id
                            ? "Running"
                            : workflow.status
                        }
                        workflow={workflow}
                        onDelete={() => onDeleteWorkflow(workflow)}
                        onDuplicate={() => onDuplicateWorkflow(workflow)}
                        onEditFile={() => onEditWorkflowFile(workflow)}
                        onRename={(name) => onRenameWorkflow(workflow, name)}
                        onRun={() => onRunWorkflow(workflow)}
                        onSelect={() => onSelect(workflow.id)}
                      />
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })
        ) : (
          <div className="rounded-[10px] border border-dashed border-line bg-slate-50 p-4 text-xs leading-5 text-muted">
            {loading ? "Loading workflows..." : "No workflows found."}
          </div>
        )}
          />
        </Suspense>
        {projectMenu ? (
          <div
            aria-label={`${projectMenu.name} project actions`}
            className="fixed z-[80] w-52 rounded-lg border border-line bg-white p-1 shadow-panel"
            role="menu"
            style={{ left: projectMenu.x, top: projectMenu.y }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => startProjectRename(projectMenu)}
            >
              <PencilLine size={14} /> Rename
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => copyProjectRoot(projectMenu.root)}
            >
              {copiedProjectRoot === projectMenu.root ? <Check size={14} /> : <Copy size={14} />}
              {copiedProjectRoot === projectMenu.root ? "Path copied" : "Copy path"}
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-ink hover:bg-slate-50"
              role="menuitem"
              type="button"
              onClick={() => openProjectRoot(projectMenu.root)}
            >
              <FolderOpen size={14} /> Open in file explorer
            </button>
            <p className="truncate px-2.5 pb-1.5 pt-1 font-mono text-[10px] text-muted" title={projectMenu.root}>
              {projectMenu.root}
            </p>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

export function projectMenuPosition(clientX, clientY, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight) {
  const menuWidth = 208;
  const menuHeight = 154;
  const requestedX = Number.isFinite(clientX) ? clientX : 8;
  const requestedY = Number.isFinite(clientY) ? clientY : 8;
  const availableWidth = Number.isFinite(viewportWidth) ? viewportWidth : 1024;
  const availableHeight = Number.isFinite(viewportHeight) ? viewportHeight : 768;
  return {
    x: Math.max(8, Math.min(requestedX, availableWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(requestedY, availableHeight - menuHeight - 8)),
  };
}

export function groupWorkflowsByProject(workflows, projectLabels = {}) {
  const groups = new Map();
  for (const workflow of workflows ?? []) {
    const root = workflow.projectRoot || workflow.sourcePath || "Unregistered";
    const id = `project:${pathKey(root)}`;
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
        defaultName: projectNameFromPath(root),
        root,
        items: [],
      });
    }
    groups.get(id).items.push(workflow);
  }
  return [...groups.values()].sort((left, right) =>
    left.name.localeCompare(right.name) || left.root.localeCompare(right.root));
}

export function isOpenProjectShortcut(event) {
  return !event.repeat
    && !event.altKey
    && !event.shiftKey
    && (event.ctrlKey || event.metaKey)
    && String(event.key ?? "").toLowerCase() === "o";
}

export function nextCodeFileOpenState(openPaths, previewPath, path, preview) {
  const alreadyOpen = openPaths.includes(path);
  const willPreview = preview && (!alreadyOpen || previewPath === path);
  const withoutReplacedPreview = willPreview && previewPath && previewPath !== path
    ? openPaths.filter((candidate) => candidate !== previewPath)
    : openPaths;
  return {
    openPaths: withoutReplacedPreview.includes(path)
      ? withoutReplacedPreview
      : [...withoutReplacedPreview, path],
    previewPath: willPreview ? path : previewPath === path ? "" : previewPath,
  };
}

export function createBrowserTabPath() {
  browserTabSequence += 1;
  return `raticode-browser:${Date.now().toString(36)}:${browserTabSequence.toString(36)}`;
}

export function mergeCodeOpenPaths(current = [], additions = []) {
  return [...new Set([...current, ...additions].filter(Boolean))];
}

export function pendingCodePathForWorkflow(pending, workflowId) {
  return pending && pending.workflowId === workflowId ? pending.path ?? "" : "";
}

export function loadRecentProjectRoots() {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage?.getItem(RECENT_PROJECTS_STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? mergeRecentProjects([], parsed.filter((root) => typeof root === "string" && root.trim()))
      : [];
  } catch {
    return [];
  }
}

export function loadRecentCodePaths(storage = globalThis.window?.localStorage) {
  try {
    const parsed = JSON.parse(storage?.getItem(RECENT_FILES_STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.reduceRight((paths, path) => rememberRecentFile(paths, path), [])
      : [];
  } catch {
    return [];
  }
}

export function rememberRecentFile(current = [], path = "") {
  const nextPath = typeof path === "string" ? path.trim() : "";
  if (!nextPath || nextPath.startsWith("raticode-browser:")) return current;
  return [nextPath, ...current.filter((candidate) => candidate !== nextPath)].slice(0, 8);
}

export function removeCodePath(current = [], path = "") {
  if (!path) return current;
  return current.filter((candidate) => candidate !== path);
}

export function loadLastWorktreeByProject() {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(
      window.localStorage?.getItem(LAST_WORKTREE_STORAGE_KEY) ?? "{}",
    );
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
    return Object.fromEntries(Object.entries(parsed).filter(
      ([projectRoot, worktreeRoot]) => (
        typeof projectRoot === "string"
        && projectRoot.trim()
        && typeof worktreeRoot === "string"
        && worktreeRoot.trim()
      ),
    ));
  } catch {
    return {};
  }
}

export function mainWorktreeRoot(payload, fallback = "") {
  const worktrees = Array.isArray(payload?.worktrees) ? payload.worktrees : [];
  return String(worktrees[0]?.path || payload?.root || fallback).trim();
}

export function loadStudioSession(storage = globalThis.window?.localStorage) {
  try {
    const parsed = JSON.parse(brandCompat.readBrandedStorage(storage, STUDIO_SESSION_STORAGE_KEY) ?? "{}");
    return {
      projectRoot: typeof parsed.projectRoot === "string" ? parsed.projectRoot.trim() : "",
      view: ["graph", "code"].includes(parsed.view) ? parsed.view : "",
      workflowId: typeof parsed.workflowId === "string" ? parsed.workflowId.trim() : "",
    };
  } catch {
    return { projectRoot: "", view: "", workflowId: "" };
  }
}

export function saveStudioSession(session, storage = globalThis.window?.localStorage) {
  const normalized = {
    projectRoot: typeof session?.projectRoot === "string" ? session.projectRoot.trim() : "",
    view: ["graph", "code"].includes(session?.view) ? session.view : "",
    workflowId: typeof session?.workflowId === "string" ? session.workflowId.trim() : "",
  };
  try {
    storage?.setItem(STUDIO_SESSION_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // The current studio session still works when persistent storage is unavailable.
  }
  return normalized;
}

export function mergeRecentProjects(current = [], additions = []) {
  return uniquePaths([...current, ...additions].map((root) => String(root).trim()).filter(Boolean));
}

export function rememberRecentProject(current = [], projectRoot = "") {
  const root = String(projectRoot).trim();
  return root ? [root, ...current.filter((candidate) => !samePath(candidate, root))] : current;
}

export function projectWorkspace(projectRoot = "") {
  const root = String(projectRoot).trim();
  return {
    agents: {},
    description: "Project without a registered workflow",
    edges: [],
    id: `project:${root}`,
    name: projectNameFromPath(root),
    nodes: [],
    projectName: projectNameFromPath(root),
    projectRoot: root,
    sourceFormat: "project",
    sourcePath: "",
    status: "Project",
    tags: [],
  };
}

export function activeWorkspaceForProject(workflows = [], activeWorkflowId, activeProjectRoot = "") {
  const projectRoot = String(activeProjectRoot).trim();
  const matchedWorkflow = workflows.find((workflow) => (
    workflow.id === activeWorkflowId
    && (!projectRoot || samePath(workflow.projectRoot, projectRoot))
  ));
  const projectWorkflow = projectRoot
    ? workflows.find((workflow) => samePath(workflow.projectRoot, projectRoot))
    : workflows[0];
  return matchedWorkflow ?? projectWorkflow ?? (projectRoot ? projectWorkspace(projectRoot) : undefined);
}

export function activeWorkspaceForView(
  workflows = [],
  activeWorkflowId,
  activeProjectRoot = "",
  view = "graph",
) {
  if (view === "graph") {
    return workflows.find((workflow) => workflow.id === activeWorkflowId) ?? workflows[0];
  }
  return activeWorkspaceForProject(workflows, activeWorkflowId, activeProjectRoot)
    ?? projectWorkspace("");
}

export function codeWorkspaceAvailable(workflow) {
  return Boolean(String(workflow?.projectRoot ?? "").trim());
}

export function scopeChatThreadToProject(
  thread,
  projectRoot,
  _workflows = [],
  _preferredWorkflowId = null,
  projectName = "",
) {
  // Retain the call signature while ignoring historical workflow selection.
  void _workflows;
  void _preferredWorkflowId;
  const root = String(projectRoot ?? "").trim();
  return {
    ...thread,
    projectRoot: root,
    ...(!samePath(thread.projectRoot, root) ? { projectBranch: undefined } : {}),
    projectName: String(projectName || (root ? projectNameFromPath(root) : "No project")),
    selectedWorkflowId: null,
  };
}

export function editorFileReferences(paths = [], workflowTabs = {}) {
  return [...new Set(paths.map((path) => workflowTabs[path]?.sourcePath || path)
    .filter((path) => path && !/^(workflow-graph:|raticode-browser:|browser:)/.test(path)))];
}

export function chatWorkflowContextForThread(thread, workflows = [], openFiles = []) {
  const projectRoot = String(thread?.projectRoot ?? "").trim();
  return {
    projectName: String(thread?.projectName || (projectRoot ? projectNameFromPath(projectRoot) : "No project")),
    projectRoot,
    selectedWorkflowId: null,
    openFiles: [...new Set(openFiles)],
    workflows: workflows.filter((workflow) => projectRoot && samePath(workflow?.projectRoot, projectRoot))
      .map(({ id, name, sourcePath }) => ({ id, name, sourcePath })),
  };
}

export function rattishGraphIsValid(document) {
  return document?.compilation?.state === "valid";
}

export function mergeRattishAnalysisState(currentState, analyzedDocument, source) {
  if (!currentState?.document || currentState.document.source !== source) return currentState;
  const currentDocument = currentState.document;
  const graph = rattishGraphIsValid(analyzedDocument)
    ? analyzedDocument.graph
    : currentDocument.lastValidGraph || currentDocument.graph || analyzedDocument.graph;
  return {
    ...currentState,
    document: {
      ...analyzedDocument,
      source,
      dirty: currentDocument.dirty,
      savedRevision: currentDocument.savedRevision ?? analyzedDocument.savedRevision,
      savedSource: currentDocument.savedSource,
      metadata: currentDocument.metadata ?? analyzedDocument.metadata,
      metadataRevision: currentDocument.metadataRevision ?? analyzedDocument.metadataRevision,
      graph,
      lastValidGraph: graph,
    },
    error: "",
    loading: false,
    saving: currentState.saving,
  };
}

export function loadProjectLabels() {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(window.localStorage?.getItem(PROJECT_LABELS_STORAGE_KEY) ?? "{}");
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([root, label]) => root.trim() && typeof label === "string" && label.trim(),
      ),
    );
  } catch {
    return {};
  }
}

function projectNameFromPath(pathValue) {
  const parts = String(pathValue).replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.at(-1) || "Unregistered";
}

export function assistantMarkdownSourcePath(projectRoot) {
  const root = String(projectRoot ?? "").replace(/[\\/]+$/, "");
  if (!root) return "";
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return `${root}${separator}.raticode-assistant.md`;
}

function WorkflowListItem({
  active,
  onDelete,
  onDuplicate,
  onEditFile,
  onRename,
  onRun,
  onSelect,
  status,
  workflow,
}) {
  const menuRef = useRef(null);
  const nameInputRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(workflow.name);

  useEffect(() => {
    if (!menuOpen) return undefined;

    function handlePointerDown(event) {
      if (menuRef.current?.contains(event.target)) return;
      setMenuOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [menuOpen]);

  useEffect(() => {
    setDraftName(workflow.name);
  }, [workflow.name]);

  useEffect(() => {
    if (!renaming) return;
    nameInputRef.current?.focus();
    nameInputRef.current?.select();
  }, [renaming]);

  function commitRename() {
    const nextName = draftName.trim();
    setRenaming(false);
    if (!nextName) {
      setDraftName(workflow.name);
      return;
    }
    if (nextName !== workflow.name) {
      onRename(nextName);
    }
  }

  function cancelRename() {
    setRenaming(false);
    setDraftName(workflow.name);
  }

  return (
    <div
      className={`group relative w-full rounded-lg text-left transition ${
        active
          ? "bg-indigo-50"
          : "bg-transparent hover:bg-slate-100"
      }`}
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setMenuOpen(true);
      }}
    >
      <div
        role="button"
        tabIndex={0}
        className="w-full rounded-lg px-2 py-2 pr-8 text-left"
        onClick={() => {
          if (!renaming) {
            onSelect();
          }
        }}
        onKeyDown={(event) => {
          if (!renaming && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            onSelect();
          }
        }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {renaming ? (
              <input
                ref={nameInputRef}
                className="w-full rounded-md border border-teal-300 bg-white px-2 py-1 text-sm font-semibold text-ink outline-none ring-2 ring-teal-100"
                value={draftName}
                onBlur={commitRename}
                onChange={(event) => setDraftName(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    event.currentTarget.blur();
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    cancelRename();
                  }
                }}
              />
            ) : (
              <p className={`truncate text-xs font-medium ${active ? "text-indigo-700" : "text-ink"}`}>{workflow.name}</p>
            )}
            <p className="mt-0.5 truncate text-[10px] leading-4 text-muted">{workflow.status ?? "Ready"}</p>
          </div>
          <StatusDot status={status} />
        </div>
      </div>
      <div ref={menuRef} className="absolute right-1 top-1.5">
        <button
          className="grid h-7 w-7 place-items-center rounded-md text-muted opacity-70 transition hover:bg-slate-100 hover:text-ink group-hover:opacity-100 dark:hover:bg-[#2a2a2a]"
          title="Workflow actions"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            setMenuOpen((current) => !current);
          }}
        >
          <MoreVertical size={14} />
        </button>
        {menuOpen ? (
          <div className="absolute right-0 top-8 z-40 w-48 rounded-lg border border-line bg-white p-1 shadow-panel" role="menu">
            <button
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-ink dark:hover:bg-[#2a2a2a]"
              role="menuitem"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMenuOpen(false);
                onEditFile();
              }}
            >
              <Code2 size={15} />
              Edit workflow file
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-ink dark:hover:bg-[#2a2a2a]"
              role="menuitem"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMenuOpen(false);
                onRun();
              }}
            >
              <Play size={15} />
              Run workflow
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-ink dark:hover:bg-[#2a2a2a]"
              role="menuitem"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMenuOpen(false);
                setRenaming(true);
              }}
            >
              <PencilLine size={15} />
              Rename workflow
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-ink dark:hover:bg-[#2a2a2a]"
              role="menuitem"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMenuOpen(false);
                onDuplicate();
              }}
            >
              <Copy size={15} />
              Duplicate workflow
            </button>
            <div className="my-1 border-t border-line" />
            <button
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-red-700 transition hover:bg-red-50 dark:hover:bg-[#3a2424]"
              role="menuitem"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setMenuOpen(false);
                onDelete();
              }}
            >
              <Trash2 size={15} />
              Delete workflow
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function TopBar({
  activeCodePath = "",
  saveState,
  workflow,
  view = "graph",
  onGraphToolbarTargetChange,
  onRetrySave,
}) {
  const label = !workflow || (view === "code" && !activeCodePath)
    ? null
    : topBarLabelParts(workflow, view, activeCodePath);
  if (view === "graph" && workflow) {
    return <header className="studio-topbar min-w-0 shrink-0 border-b border-line bg-white px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink" title={label?.fullPath}>{workflow.name}</h2>
        <WorkflowSaveStatus saveState={saveState} onRetry={onRetrySave} />
      </div>
      <div
        className="mt-1.5 flex min-w-0 items-center [&>[data-toolbar]]:min-w-0 [&>[data-toolbar]]:max-w-full [&>[data-toolbar]]:flex-wrap [&_[data-toolbar-row=secondary]]:flex-wrap"
        data-graph-toolbar-target="true"
        ref={onGraphToolbarTargetChange}
      />
    </header>;
  }
  return (
    <header className="studio-topbar flex h-[54px] shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-4">
      <div
        className={`flex min-w-0 flex-1 items-baseline overflow-hidden ${
          view === "graph" ? "gap-1" : ""
        }`}
        title={label?.fullPath || undefined}
      >
        {label ? (
          <>
            {label.path ? (
              <span
                className={`flex min-w-0 items-baseline text-muted ${
                  view === "graph"
                    ? "shrink-0 text-[11px] leading-4"
                    : "text-xs"
                }`}
              >
                <span className="truncate">{label.path}</span>
                <span className="shrink-0">{label.separator}</span>
              </span>
            ) : null}
            <h2
              className={`min-w-0 truncate font-semibold text-ink dark:text-white ${
                view === "graph"
                  ? "text-xl leading-6"
                  : "max-w-[55%] shrink-0 text-[15px]"
              }`}
            >
              {label.name}
            </h2>
          </>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {view === "graph" && workflow ? <WorkflowSaveStatus saveState={saveState} onRetry={onRetrySave} /> : null}
        {view === "graph" && workflow ? (
          <>
            <div
              className="flex shrink-0 items-center gap-2"
              data-graph-toolbar-target="true"
              ref={onGraphToolbarTargetChange}
            />
            <span aria-hidden="true" className="h-5 w-px shrink-0 bg-line" />
          </>
        ) : null}
      </div>
    </header>
  );
}

export function RecentProjectSelector({ projectRoot = "", openingProjectRoot = "", recentProjectRoots = [], onSelectProject, onRemoveRecentProject, onOpenProject }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const projects = mergeRecentProjects(projectRoot ? [projectRoot] : [], recentProjectRoots);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); } };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", escape); };
  }, [open]);
  return <div ref={rootRef} className="relative w-full" aria-busy={Boolean(openingProjectRoot)}>
    <button ref={triggerRef} type="button" aria-label="Recent projects" aria-expanded={open} aria-haspopup="menu" title={projectRoot || "Open a project"} className="flex h-9 w-full min-w-0 items-center gap-2 rounded-lg border border-line px-3 text-xs text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => setOpen(value => !value)} onKeyDown={event => { if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); requestAnimationFrame(() => rootRef.current?.querySelector('[role="menuitem"]')?.focus()); } }}>
      {openingProjectRoot ? <Loader2 aria-hidden="true" size={14} className="shrink-0 animate-spin motion-reduce:animate-none" /> : <FolderOpen aria-hidden="true" size={14} className="shrink-0 text-muted" />}
      <span className="min-w-0 flex-1 truncate text-left">{openingProjectRoot ? `Opening ${projectNameFromPath(openingProjectRoot)}...` : projectNameFromPath(projectRoot) || "Open a project"}</span><ChevronDown aria-hidden="true" size={12} />
    </button>
    {open ? <div role="menu" aria-label="Recent projects" className="absolute left-0 top-full z-[90] mt-1 max-h-[min(20rem,calc(100vh-160px))] w-full overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-panel" onKeyDown={event => { if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return; event.preventDefault(); const items = [...event.currentTarget.querySelectorAll('[role="menuitem"]')]; const index = items.indexOf(document.activeElement); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus(); }}>
      <p className="px-2 py-1.5 text-[10px] font-semibold text-muted">Recent projects</p>
      {projects.map(root => <div className="group flex items-center rounded-md hover:bg-slate-50" key={root}>
        <button type="button" role="menuitem" title={root} className="min-w-0 flex-1 rounded-md px-2 py-2 text-left text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => { setOpen(false); onSelectProject?.(root); }}><span className="flex items-center gap-2 font-medium text-ink"><span className="truncate">{projectNameFromPath(root)}</span>{samePath(root, projectRoot) ? <Check aria-hidden="true" size={12} /> : null}</span><span className="block truncate pt-0.5 text-[10px] text-muted">{root}</span></button>
        {onRemoveRecentProject ? <button type="button" role="menuitem" aria-label={`Remove ${projectNameFromPath(root)} from recent projects`} className="m-1 grid h-7 w-7 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 focus-visible:outline" onClick={() => onRemoveRecentProject(root)}><X aria-hidden="true" size={13} /></button> : null}
      </div>)}
      {!projects.length ? <p className="px-2 py-2 text-xs text-muted">No recent projects</p> : null}
      {onOpenProject ? <button type="button" role="menuitem" className="mt-1 w-full rounded-md border-t border-line px-2 py-2 text-left text-xs text-ink hover:bg-slate-50 focus-visible:outline" onClick={() => { setOpen(false); onOpenProject(); }}>Open project...</button> : null}
    </div> : null}
  </div>;
}

export function GlobalToolbar({
  onOpenRuns,
  runSummary,
  projectRoot = "",
  projectError = "",
  activeCodeDocument,
  activeCodePath = "",
  assistantPaneVisible = true,
  projectPaneVisible = true,
  recentProjectRoots = [],
  settings = DEFAULT_APP_SETTINGS,
  settingsOpen = false,
  theme,
  updateState,
  workflow,
  view = "graph",
  onApplyUpdate,
  onCheckForUpdates,
  onOpenHistory,
  onSelectProject,
  onToggleSettings,
  onToggleTheme,
  onMenuAction,
}) {
  const [projectWorktrees, setProjectWorktrees] = useState({ root: "", items: [], error: "" });
  useEffect(() => {
    let cancelled = false;
    if (!projectRoot || !window.goferDesktop?.workspace?.gitWorktrees) return undefined;
    Promise.resolve(window.goferDesktop.workspace.gitWorktrees(projectRoot)).then(payload => {
      if (!cancelled) setProjectWorktrees({ root: projectRoot, items: (payload?.worktrees ?? []).filter(item => !item.missing && !item.prunable), error: payload?.error || "" });
    }).catch(error => { if (!cancelled) setProjectWorktrees({ root: projectRoot, items: [], error: error.message || "Unable to load worktrees" }); });
    return () => { cancelled = true; };
  }, [projectRoot]);
  const hasUpdateBridge = Boolean(window.goferUpdates?.check);
  const buttonClass = "studio-icon-button grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink";
  return (
    <header className="global-toolbar flex h-8 shrink-0 items-center justify-between border-b border-line bg-white px-2" aria-label="Application toolbar">
      <ApplicationMenus
        activeCodeDocument={activeCodeDocument}
        activeCodePath={activeCodePath}
        assistantPaneVisible={assistantPaneVisible}
        projectPaneVisible={projectPaneVisible}
        recentProjectRoots={recentProjectRoots}
        settings={settings}
        view={view}
        onAction={onMenuAction}
        onSelectProject={onSelectProject}
      />
      <div className="flex min-w-0 flex-1 items-center gap-2 px-3">
        {projectError ? <div role="alert" className="flex min-w-0 items-center gap-2 text-xs text-red-700 dark:text-red-300"><span className="truncate" title={projectError}>{projectError}</span><button type="button" className="shrink-0 underline" onClick={() => onMenuAction?.("file.openFolder")}>Open project</button></div> : null}
        {samePath(projectWorktrees.root, projectRoot) && projectWorktrees.items.length > 1 ? <label className="flex min-w-0 max-w-48 items-center gap-1 text-muted" title="Browsing worktree"><GitBranch aria-hidden="true" size={13} /><select aria-label="Browsing worktree" className="h-7 min-w-0 rounded bg-white text-xs text-ink focus-visible:outline" value={projectWorktrees.items.find(item => samePath(item.path, projectRoot))?.path || projectRoot} onChange={event => onSelectProject?.(event.target.value, { mainProjectRoot: projectWorktrees.items[0]?.path || projectRoot })}>{projectWorktrees.items.map(item => <option key={item.path} value={item.path}>{item.branch || "Detached HEAD"}</option>)}</select></label> : null}
      </div>
      <div className="flex items-center gap-1">
        {onOpenRuns ? <button type="button" onClick={onOpenRuns} aria-label={`Open runs, ${runSummary?.active.length || 0} active, ${runSummary?.unread.length || 0} unread`} className="flex h-7 items-center gap-1.5 rounded px-2 text-xs text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"><History aria-hidden="true" size={14} />Runs{runSummary?.active.length ? <span>{runSummary.active.length} active</span> : null}{runSummary?.unread.length ? <span className="text-brand">· {runSummary.unread.length} unread</span> : null}{runSummary?.disconnected.length ? <span title="Some run status is out of date">!</span> : null}</button> : null}
        {hasUpdateBridge ? updateState?.available ? (
          <button className="inline-flex h-8 items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70" disabled={Boolean(updateState.downloading)} title={updateButtonTitle(updateState)} type="button" onClick={onApplyUpdate}>
            {updateState.downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
            {updateButtonLabel(updateState)}
          </button>
        ) : (
          <button className={buttonClass} title={updateState?.error ? `Update check failed: ${updateState.error}` : "Check for updates"} type="button" onClick={onCheckForUpdates}>
            <RefreshCw size={16} className={updateState?.checking ? "animate-spin" : ""} />
          </button>
        ) : null}
        <button aria-label="Open settings" aria-expanded={settingsOpen} className={`${buttonClass} ${settingsOpen ? "bg-slate-100 text-ink" : ""}`} title={`Settings (${formatKeybinding(settingBinding(settings, "settings.open"))})`} type="button" onClick={onToggleSettings}>
          <SettingsIcon size={16} />
        </button>
        <button className={`${buttonClass} disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted`} disabled={!workflow} title={workflow ? "Workflow history" : "Workflow history is available after you create a workflow"} type="button" onClick={onOpenHistory}>
          <History size={16} />
        </button>
        <button className={buttonClass} title={theme === "dark" ? "Light mode" : "Dark mode"} type="button" onClick={onToggleTheme}>
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}

const APPLICATION_MENUS = [
  { label: "File", items: [
    ["file.new", "New File", "Ctrl+N"],
    ["file.open", "Open File...", "Ctrl+O"],
    ["file.openFolder", "Open Project...", "project.open"],
    ["file.recentProjects", "Recent Projects"],
    null,
    ["file.save", "Save", "Ctrl+S", "code-document"],
    ["file.close", "Close Editor", "Ctrl+W", "code-path"],
  ] },
  { label: "Edit", items: [
    ["edit.undo", "Undo", "Ctrl+Z", "code-document"],
    ["edit.redo", "Redo", "Ctrl+Shift+Z", "code-document"],
    null,
    ["edit.cut", "Cut", "Ctrl+X", "code-document"],
    ["edit.copy", "Copy", "Ctrl+C", "code-document"],
    ["edit.paste", "Paste", "Ctrl+V", "code-document"],
    null,
    ["edit.find", "Find", "Ctrl+F", "code-document"],
    ["edit.replace", "Replace", "Ctrl+H", "code-document"],
    ["edit.toggleLineComment", "Toggle Line Comment", "Ctrl+/", "code-document"],
    ["edit.formatDocument", "Format Document", "Shift+Alt+F", "code-document"],
  ] },
  { label: "Selection", items: [
    ["selection.selectAll", "Select All", "Ctrl+A", "code-document"],
    ["selection.expand", "Expand Selection", "Shift+Alt+Right", "code-document"],
    ["selection.shrink", "Shrink Selection", "Shift+Alt+Left", "code-document"],
    null,
    ["selection.copyLineUp", "Copy Line Up", "Shift+Alt+Up", "code-document"],
    ["selection.copyLineDown", "Copy Line Down", "Shift+Alt+Down", "code-document"],
    ["selection.moveLineUp", "Move Line Up", "Alt+Up", "code-document"],
    ["selection.moveLineDown", "Move Line Down", "Alt+Down", "code-document"],
    null,
    ["selection.addCursorAbove", "Add Cursor Above", "Ctrl+Alt+Up", "code-document"],
    ["selection.addCursorBelow", "Add Cursor Below", "Ctrl+Alt+Down", "code-document"],
  ] },
  { label: "View", items: [
    ["view.graph", "Workflows", ""],
    ["view.code", "Files", ""],
    null,
    ["view.projectPane", "Project Files", "Ctrl+B", "project-check"],
    ["view.assistantPane", "Rem", "", "assistant-check"],
    ["view.panel", "Bottom Panel", "panel.toggle"],
    null,
    ["view.zoomIn", "Zoom In", "Ctrl++"],
    ["view.zoomOut", "Zoom Out", "Ctrl+-"],
    ["view.resetZoom", "Reset Zoom", "Ctrl+0"],
  ] },
  { label: "Terminal", items: [["terminal.toggle", "Toggle Terminal", "panel.toggle"]] },
  { label: "Help", items: [
    ["help.updates", "Check for Updates...", ""],
    ["help.settings", "Settings", ""],
  ] },
];

export function ApplicationMenus({
  activeCodeDocument,
  activeCodePath,
  assistantPaneVisible,
  projectPaneVisible,
  recentProjectRoots = [],
  settings,
  view,
  onAction,
  onSelectProject,
}) {
  const [openMenu, setOpenMenu] = useState("");
  const containerRef = useRef(null);
  useEffect(() => {
    if (!openMenu) return undefined;
    const dismiss = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpenMenu("");
    };
    const escape = (event) => {
      if (event.key === "Escape") setOpenMenu("");
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", escape);
    };
  }, [openMenu]);
  const context = { activeCodeDocument, activeCodePath, assistantPaneVisible, projectPaneVisible, view };
  return (
    <nav aria-label="Application menu" className="flex h-full items-center gap-0.5" ref={containerRef}>
      {APPLICATION_MENUS.map((menu) => (
        <div className="relative" key={menu.label}>
          <button
            aria-expanded={openMenu === menu.label}
            aria-haspopup="menu"
            className={`rounded px-2 py-1 text-[13px] text-ink outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand ${openMenu === menu.label ? "bg-slate-100" : ""}`}
            type="button"
            onClick={() => setOpenMenu((current) => current === menu.label ? "" : menu.label)}
            onPointerEnter={() => openMenu && setOpenMenu(menu.label)}
          >
            {menu.label}
          </button>
          {openMenu === menu.label ? (
            <div className="absolute left-0 top-[calc(100%+7px)] z-[100] min-w-[250px] rounded-md border border-line bg-white p-1.5 text-ink shadow-[0_10px_28px_rgba(15,23,42,0.18)]" role="menu">
              {menu.items.map((item, index) => item === null ? (
                <div aria-hidden="true" className="my-1 border-t border-line" key={`separator-${index}`} />
              ) : item[0] === "file.recentProjects" ? (
                <RecentProjectsMenuItem
                  key={item[0]}
                  recentProjectRoots={recentProjectRoots}
                  onSelect={(root) => {
                    setOpenMenu("");
                    onSelectProject?.(root);
                  }}
                />
              ) : (
                <ApplicationMenuItem
                  context={context}
                  item={item}
                  key={item[0]}
                  settings={settings}
                  onSelect={() => {
                    setOpenMenu("");
                    onAction?.(item[0]);
                  }}
                />
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </nav>
  );
}

const APPLICATION_MENU_BINDINGS = {
  "file.close": "file.close",
  "file.new": "file.new",
  "file.open": "file.open",
  "file.openFolder": "project.open",
  "file.save": "file.save",
  "help.settings": "settings.open",
  "terminal.toggle": "panel.toggle",
  "view.assistantPane": "view.toggleAssistantPane",
  "view.code": "view.code",
  "view.graph": "view.graph",
  "view.panel": "panel.toggle",
  "view.projectPane": "view.toggleProjectPane",
};

function ApplicationMenuItem({ context, item, onSelect, settings }) {
  const [action, label, fallbackShortcut, condition] = item;
  const bindingId = APPLICATION_MENU_BINDINGS[action];
  const shortcut = bindingId
    ? formatKeybinding(settingBinding(settings, bindingId))
    : fallbackShortcut;
  const enabled = !condition || condition.endsWith("-check")
    || (condition === "code" && context.view === "code")
    || (condition === "code-path" && context.view === "code" && Boolean(context.activeCodePath))
    || (condition === "code-document" && context.view === "code" && Boolean(context.activeCodeDocument));
  const checked = condition === "graph-check" && context.view === "graph"
    || condition === "code-check" && context.view === "code"
    || condition === "project-check" && context.projectPaneVisible
    || condition === "assistant-check" && context.assistantPaneVisible;
  return (
    <button
      className="flex h-8 w-full items-center gap-3 rounded px-2 text-left text-[12px] hover:bg-slate-100 disabled:text-muted disabled:opacity-45 disabled:hover:bg-transparent"
      disabled={!enabled}
      role="menuitem"
      type="button"
      onClick={onSelect}
    >
      <span aria-hidden="true" className="w-3 text-center">{checked ? "✓" : ""}</span>
      <span className="flex-1">{label}</span>
      {shortcut ? <span className="text-[11px] text-muted">{shortcut}</span> : null}
    </button>
  );
}

function RecentProjectsMenuItem({ recentProjectRoots, onSelect }) {
  const [open, setOpen] = useState(false);
  const labels = loadProjectLabels();
  const projects = mergeRecentProjects([], recentProjectRoots).map((root) => ({
    name: pathValue(labels, root)?.trim() || projectNameFromPath(root),
    root,
  }));
  return (
    <div className="relative" onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex h-8 w-full items-center gap-3 rounded px-2 text-left text-[12px] hover:bg-slate-100"
        role="menuitem"
        type="button"
        onClick={() => setOpen((current) => !current)}
      >
        <span aria-hidden="true" className="w-3" />
        <span className="flex-1">Recent Projects</span>
        <ChevronRight aria-hidden="true" size={13} />
      </button>
      {open ? (
        <div aria-label="Recent projects" className="absolute left-full top-[-6px] z-[101] min-w-[260px] rounded-md border border-line bg-white p-1.5 shadow-[0_10px_28px_rgba(15,23,42,0.18)]" role="menu">
          {projects.length ? projects.map((project) => (
            <button className="flex h-8 w-full items-center gap-2 rounded px-2 text-left text-xs hover:bg-slate-100" key={project.root} role="menuitem" title={project.root} type="button" onClick={() => onSelect(project.root)}>
              <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={13} />
              <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    {project.branch ? <span className="max-w-24 truncate text-[10px] text-muted">{project.branch}</span> : null}
            </button>
          )) : <p className="px-2 py-2 text-xs text-muted">No recent projects</p>}
        </div>
      ) : null}
    </div>
  );
}

export function topBarProjectName(workflow) {
  const projectName = String(workflow?.projectName ?? "").trim();
  if (projectName) return projectName;
  const projectRoot = String(workflow?.projectRoot ?? "").trim();
  return projectRoot ? projectNameFromPath(projectRoot) : "Unfiled project";
}

export function topBarLabelParts(workflow, view = "graph", activeCodePath = "") {
  if (view === "code") {
    return splitTopBarPath(activeCodePath, "No file open");
  }
  const projectRoot = String(workflow?.projectRoot ?? "").replace(/[\\/]+$/, "");
  const projectPath = projectRoot
    ? projectNameFromPath(projectRoot)
    : String(workflow?.projectName || "Unfiled project");
  const workflowTitle = String(workflow?.name || workflow?.id || "Untitled workflow");
  const separator = projectPath.includes("\\") && !projectPath.includes("/") ? "\\" : "/";
  return {
    fullPath: `${projectPath}${separator}${workflowTitle}`,
    name: workflowTitle,
    path: projectPath,
    separator,
  };
}

function splitTopBarPath(path, fallbackName) {
  const fullPath = String(path ?? "").trim();
  if (!fullPath) return { fullPath: fallbackName, name: fallbackName, path: "", separator: "" };
  const separatorIndex = Math.max(fullPath.lastIndexOf("/"), fullPath.lastIndexOf("\\"));
  if (separatorIndex < 0) {
    return { fullPath, name: fullPath, path: "", separator: "" };
  }
  return {
    fullPath,
    name: fullPath.slice(separatorIndex + 1) || fallbackName,
    path: fullPath.slice(0, separatorIndex),
    separator: fullPath[separatorIndex],
  };
}

function WorkflowSaveStatus({ saveState, onRetry }) {
  if (!saveState?.status) return null;

  if (saveState.status === "error") {
    return (
      <div
        aria-atomic="true"
        aria-live="assertive"
        className="inline-flex h-9 items-center gap-2 rounded-lg bg-red-50 px-3 text-xs font-medium text-red-800 dark:bg-red-950/40 dark:text-red-200"
        role="alert"
        title={saveState.error || "Unable to save workflow"}
      >
        <AlertCircle aria-hidden="true" size={15} />
        <span>Couldn&apos;t save</span>
        {saveState.error ? (
          <span className="max-w-32 truncate text-red-700 dark:text-red-300">
            {saveState.error}
          </span>
        ) : null}
        <span aria-hidden="true">—</span>
        <button
          className="font-semibold underline underline-offset-2 hover:no-underline"
          type="button"
          onClick={onRetry}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div
      aria-atomic="true"
      aria-busy={saveState.status === "saving" || undefined}
      aria-live="polite"
      className="inline-flex h-9 items-center gap-2 px-2 text-xs font-medium text-muted"
      role="status"
    >
      {saveState.status === "saving" ? (
        <>
          <Loader2 aria-hidden="true" size={15} className="animate-spin" />
          Saving…
        </>
      ) : (
        <>
          <Check aria-hidden="true" size={15} />
          Saved
        </>
      )}
    </div>
  );
}

function updateButtonLabel(updateState) {
  if (updateState?.downloaded) return "Restart to update";
  if (updateState?.downloading) {
    const percent = Math.max(0, Math.min(100, updateState.progress?.percent ?? 0));
    return `Downloading ${Math.round(percent)}%`;
  }
  return `Update ${updateState?.info?.version ?? "available"}`;
}

function updateButtonTitle(updateState) {
  if (updateState?.downloaded) return "Restart Raticode and apply the downloaded update";
  if (updateState?.downloading) return "Downloading update";
  return "Download, install, and restart Raticode";
}

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

export function ChatPane({
  visible = true,
  reducedMotion = "system",
  activeWorkflowId,
  activeProjectRoot,
  assistantDefaults = {},
  memorySettings = DEFAULT_APP_SETTINGS.memory,
  audioInputDeviceId = "default",
  composerFocusRequest = 0,
  onOpenMarkdownLink,
  onOpenFile,
  onResponseComplete,
  onResizeKeyDown,
  onResizeStart,
  recentProjectRoots = [],
  width,
  workflow,
  workflows = [],
  openFiles = [],
}) {
  const prospectiveProjectRoot = String(activeProjectRoot ?? workflow?.projectRoot ?? "").trim();
  const chatScrollRef = useRef(null);
  const chatPinnedToBottomRef = useRef(true);
  const renderedConversationRef = useRef({ threadId: null, textKey: "" });
  const conversationMenuRef = useRef(null);
  const dragDepthRef = useRef(0);
  const scopeMenuRef = useRef(null);
  const [draft, setDraft] = useState("");
  const draftsByThreadRef = useRef({});
  const [attachments, setAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [contextSendThread, setContextSendThread] = useState(null);
  const [pendingRemContext, setPendingRemContext] = useState(null);
  const [contextFocusRequest, setContextFocusRequest] = useState(0);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [providerId, setProviderId] = useState(assistantDefaults.provider || "codex");
  const [permissionsByProvider, setPermissionsByProvider] = useState({});
  const [model, setModel] = useState(assistantDefaults.model || "");
  const [effort, setEffort] = useState(assistantDefaults.effort || "");
  const {
    capabilities: providers,
    error: providerDiscoveryError,
    loading: providersLoading,
    refresh: refreshProviders,
  } = useProviderCapabilities();
  const providerCapability = providers.find(item => item.id === providerId);
  const permissionOptions = providerPermissionOptions(providerId, providerCapability);
  const [threads, setThreads] = useState([]);
  const [activeThreadId, setActiveThreadId] = useState(null);
  const [messagesByThread, setMessagesByThread] = useState({});
  const conversationCacheRef = useRef(null);
  const repository = conversationRepository();
  const chatStorageErrorsRef = useRef({});
  const [historyState, setHistoryState] = useState({ threadId: null, loading: false, hasMore: false, before: Infinity, error: "" });
  const historyRequestRef = useRef(0);
  const historyLoadingRef = useRef(false);
  const prependScrollRef = useRef(null);
  const [threadSearchOpen, setThreadSearchOpen] = useState(false);
  const [searchTarget, setSearchTarget] = useState(null);
  const activeSearchTarget = searchTarget?.threadId === activeThreadId ? searchTarget : null;
  const threadSearchButtonRef = useRef(null);
  const reportStorageFailure = (id, error) => {
    const message = `Conversation could not be saved: ${error instanceof Error ? error.message : String(error)}`;
    chatStorageErrorsRef.current[id] = message;
    setChatStateByThread((current) => ({ ...current, [id]: { ...current[id], error: message } }));
    return false;
  };
  if (!conversationCacheRef.current) conversationCacheRef.current = createConversationCache({
    load: (id) => repository.durable ? [] : loadChatMessages(chatStorageKeyFor(id)),
    save: (id, history, previous, persisted) => {
      const saved = () => {
        delete chatStorageErrorsRef.current[id];
        setThreads((current) => bumpChatThread(current, id));
        archiveThreadFromStorage(id, () => repository.all(id));
        return true;
      };
      try {
        const result = repository.save(id, history, previous, persisted);
        return result?.then ? result.then(saved).catch(error => reportStorageFailure(id, error)) : saved();
      } catch (error) { return reportStorageFailure(id, error); }
    },
    changed: setMessagesByThread,
  });
  useEffect(() => { persistChatThreads(threads); }, [threads]);
  const [chatStateByThread, setChatStateByThread] = useState({});
  const [chatAnnouncementByThread, setChatAnnouncementByThread] = useState({});
  const [backgroundChatAnnouncement, setBackgroundChatAnnouncement] = useState("");
  const [liveTurnByThread, setLiveTurnByThread] = useState({});
  const [expandedThoughtGroups, setExpandedThoughtGroups] = useState({});
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false);
  const [scopeMenuOpen, setScopeMenuOpen] = useState(false);
  const [resourcesOpen, setResourcesOpen] = useState(false);
  const [homeProjectRoot, setHomeProjectRoot] = useState(prospectiveProjectRoot);
  const chatAbortControllersRef = useRef({});
  const activeChatTurnsRef = useRef({});
  const steeringRequestsRef = useRef({});
  const [, setSteeringByThread] = useState({});
  const [steeringBusy, setSteeringBusy] = useState({});
  const [steeringErrors, setSteeringErrors] = useState({});

  function recordSteering(threadId, receipt) {
    if (!receipt?.requestId || receipt.conversationId !== threadId) return;
    setSteeringByThread(current => {
      const receipts = current[threadId] || [];
      const previous = receipts.find(item => item.requestId === receipt.requestId);
      // A slow HTTP acceptance response must not overwrite a terminal stream receipt.
      if (previous && previous.status !== "interrupting" && receipt.status === "interrupting") return current;
      return { ...current, [threadId]: previous ? receipts.map(item => item.requestId === receipt.requestId ? receipt : item) : [...receipts, receipt] };
    });
  }

  async function recoverSteering(threadId) {
    try {
      const response = await fetch(apiUrl(`/chat/steering?conversationId=${encodeURIComponent(threadId)}`));
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not recover steering receipts");
      for (const receipt of payload.receipts || []) recordSteering(threadId, receipt);
    } catch (error) {
      setSteeringErrors(current => ({ ...current, [threadId]: error.message }));
    }
  }

  useEffect(() => {
    if (activeThreadId) void recoverSteering(activeThreadId);
    // Recovery is scoped to the selected conversation, never an automatic replay.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThreadId]);
  const deletedChatThreadIdsRef = useRef(new Set());
  const activeThreadIdRef = useRef(null);
  const activeThread = threads.find((thread) => thread.id === activeThreadId);
  const savedPermission = (activeThread ? activeThread.permissionsByProvider || {} : permissionsByProvider)[providerId];
  const permissionMode = permissionOptions.some(([id]) => id === savedPermission)
    ? savedPermission : providerPermissionDefault(providerId, providerCapability);
  const requiresPermissionChoice = ["grok", "antigravity"].includes(providerId) && permissionMode !== "cli-managed";
  function selectPermission(value) {
    const next = { ...(activeThread ? activeThread.permissionsByProvider || {} : permissionsByProvider), [providerId]: value };
    if (activeThread) updateThreadConfig({ permissionsByProvider: next });
    else setPermissionsByProvider(next);
  }

  const projectLabels = loadProjectLabels();
  const scopedProjectRoot = String(
    activeThread?.projectRoot ?? homeProjectRoot ?? prospectiveProjectRoot,
  ).trim();
  const scopedProjectName = pathValue(projectLabels, scopedProjectRoot)?.trim()
    || activeThread?.projectName
    || (scopedProjectRoot ? projectNameFromPath(scopedProjectRoot) : "No project");
  const [scopeWorktrees, setScopeWorktrees] = useState([]);
  const [scopeAvailableRoots, setScopeAvailableRoots] = useState([]);
  const [scopeLoading, setScopeLoading] = useState(false);
  const scopeRoots = mergeRecentProjects(
    scopedProjectRoot ? [scopedProjectRoot] : [],
    mergeRecentProjects(
      recentProjectRoots,
      workflows.map((item) => item.projectRoot).filter(Boolean),
    ),
  );
  const scopeRootsKey = JSON.stringify(scopeRoots);
  useEffect(() => {
    if (!scopeMenuOpen) return;
    let cancelled = false;
    setScopeWorktrees([]);
    setScopeAvailableRoots([]);
    setScopeLoading(true);
    const roots = JSON.parse(scopeRootsKey);
    const workspace = window.goferDesktop?.workspace;
    const folderChecks = new Map();
    const isAvailableFolder = root => {
      if (!folderChecks.has(root)) {
        folderChecks.set(root, workspace?.getPathInfo
          ? Promise.resolve().then(() => workspace.getPathInfo(root))
            .then(info => Boolean(info?.isDirectory)).catch(() => false)
          : Promise.resolve(true));
      }
      return folderChecks.get(root);
    };
    Promise.allSettled(roots.map(async root => {
      if (!(await isAvailableFolder(root))) return { missing: true };
      return workspace?.gitWorktrees?.(root);
    }))
      .then(async results => {
        const worktrees = results.flatMap(result => result.status === "fulfilled"
          ? (result.value?.worktrees || []).filter(item => item.path && !item.missing && !item.prunable)
          : []);
        const available = await Promise.all(worktrees.map(item => isAvailableFolder(item.path)));
        if (cancelled) return;
        // Saved roots and Git worktree records can outlive their folders.
        setScopeAvailableRoots(roots.filter((_, index) => {
          const result = results[index];
          return result.status === "fulfilled" && !result.value?.missing && !result.value?.error;
        }));
        setScopeWorktrees(worktrees.filter((_, index) => available[index]));
        setScopeLoading(false);
      });
    return () => { cancelled = true; };
  }, [scopeMenuOpen, scopeRootsKey]);
  const scopeProjects = mergeRecentProjects(scopeAvailableRoots, scopeWorktrees.map(item => item.path)).map(root => {
    const worktree = scopeWorktrees.find(item => item.path === root);
    return {
      root,
      name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
      branch: worktree?.branch,
    };
  });
  const messages = useMemo(
    () =>
      activeThreadId
        ? messagesByThread[activeThreadId] ?? []
        : [],
    [activeThreadId, messagesByThread],
  );
  const chatState = activeThreadId
    ? chatStateByThread[activeThreadId] ?? { sending: false, error: "" }
    : { sending: false, error: "" };
  const chatAnnouncement = activeThreadId
    ? chatAnnouncementByThread[activeThreadId] ?? ""
    : "";
  const liveTurn = activeThreadId ? liveTurnByThread[activeThreadId] : null;
  const visibleMessages = useMemo(() => {
    const start = historyState.threadId === activeThreadId && historyState.firstMessageId
      ? messages.findIndex(message => message.id === historyState.firstMessageId) : -1;
    const emptyThreadOpened = historyState.threadId === activeThreadId && !historyState.loading && !historyState.firstMessageId;
    return repository.durable ? messages.slice(start >= 0 ? start : emptyThreadOpened ? 0 : -CONVERSATION_PAGE_SIZE) : messages;
  }, [messages, historyState.threadId, historyState.firstMessageId, historyState.loading, activeThreadId, repository.durable]);
  const chatItems = useMemo(() => buildChatItems(visibleMessages), [visibleMessages]);
  const conversationItems = useMemo(() => {
    if (!liveTurn || chatItems.some((item) => item.message?.id === liveTurn.id)) return chatItems;
    return [...chatItems, { type: "message", message: liveTurn }];
  }, [chatItems, liveTurn]);
  const latestUserMessageId = useMemo(
    () => messages.findLast((message) => message.role === "user")?.id ?? null,
    [messages],
  );
  // Immutable references are layout revisions; do not copy entire bodies into a key.
  const conversationTextKey = useMemo(() => ({ messages, changes: liveTurn?.changes, id: liveTurn?.id }),
    [messages, liveTurn?.changes, liveTurn?.id]);

  const loadHistoryPage = useCallback(async (threadId, before) => {
    if (historyLoadingRef.current) return;
    historyLoadingRef.current = true;
    const generation = ++historyRequestRef.current;
    setHistoryState(current => ({ ...current, threadId, loading: true, error: "" }));
    try {
      const page = await repository.page(threadId, before);
      if (generation !== historyRequestRef.current || deletedChatThreadIdsRef.current.has(threadId)) return;
      if (before !== Infinity && chatScrollRef.current) {
        const scroll = chatScrollRef.current;
        const viewportTop = scroll.getBoundingClientRect().top;
        const element = [...scroll.querySelectorAll("[data-history-anchor]")].find(node => node.getBoundingClientRect().bottom > viewportTop);
        prependScrollRef.current = { threadId, top: scroll.scrollTop, height: scroll.scrollHeight, id: element?.dataset.historyAnchor, offset: element?.getBoundingClientRect().top };
        chatPinnedToBottomRef.current = false;
      }
      conversationCacheRef.current.hydrate(threadId, page.messages, { recent: before === Infinity });
      setHistoryState({ threadId, loading: false, hasMore: page.hasMore, before: page.before, firstMessageId: page.messages[0]?.id, error: "" });
    } catch (error) {
      if (generation === historyRequestRef.current) setHistoryState(current => ({ ...current, loading: false, error: error.message || "History could not be loaded." }));
    } finally { if (generation === historyRequestRef.current) historyLoadingRef.current = false; }
  }, [repository]);

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
  }, [activeThreadId]);

  useLayoutEffect(() => {
    if (activeSearchTarget) { chatPinnedToBottomRef.current = false; return; }
    const previous = renderedConversationRef.current;
    const threadChanged = previous.threadId !== activeThreadId;
    const textChanged = previous.textKey !== conversationTextKey;
    renderedConversationRef.current = {
      threadId: activeThreadId,
      textKey: conversationTextKey,
    };

    if (!activeThreadId) {
      if (threadChanged && chatScrollRef.current) chatScrollRef.current.scrollTop = 0;
      return;
    }
    if (prependScrollRef.current?.threadId === activeThreadId && chatScrollRef.current) {
      const anchor = prependScrollRef.current;
      const element = [...chatScrollRef.current.querySelectorAll("[data-history-anchor]")].find(node => node.dataset.historyAnchor === anchor.id);
      chatScrollRef.current.scrollTop = element
        ? chatScrollRef.current.scrollTop + element.getBoundingClientRect().top - anchor.offset
        : anchor.top + chatScrollRef.current.scrollHeight - anchor.height;
      prependScrollRef.current = null;
      return;
    }
    if (threadChanged) chatPinnedToBottomRef.current = true;
    if (threadChanged || (textChanged && chatPinnedToBottomRef.current)) {
      scrollConversationToBottom(chatScrollRef.current);
    }
  }, [activeThreadId, conversationTextKey, activeSearchTarget]);

  useEffect(() => {
    if (!activeThreadId) setHomeProjectRoot(prospectiveProjectRoot);
  }, [activeThreadId, prospectiveProjectRoot]);

  useEffect(() => {
    const current = providers.find((provider) => provider.id === providerId);
    const nextProvider = current;
    if (!nextProvider?.available || nextProvider.discoveryStatus !== "ready" || chatState.sending) return;
    const nextModel =
      nextProvider.models?.find((item) => item.id === model) ??
      nextProvider.models?.find((item) => item.id === nextProvider.defaultModel) ??
      nextProvider.models?.[0];
    if (!nextModel) return;
    if (!model) setModel(nextModel.id);
    if (effort && !nextModel.efforts?.some((item) => item.id === effort)) {
      setEffort(nextModel.defaultEffort ?? "");
    }
  }, [effort, model, providerId, providers, chatState.sending]);

  useEffect(() => {
    if (!activeThreadId) {
      historyRequestRef.current += 1;
      historyLoadingRef.current = false;
      setDraft(draftsByThreadRef.current[activeThreadId || "new-thread"] || "");
      setAttachments([]);
      setAttachmentError("");
      dragDepthRef.current = 0;
      setDraggingFiles(false);
      setConversationMenuOpen(false);
      setScopeMenuOpen(false);
      return;
    }

    historyRequestRef.current += 1;
    historyLoadingRef.current = false;
    prependScrollRef.current = null;
    setHistoryState({ threadId: activeThreadId, loading: repository.durable, hasMore: false, before: Infinity, error: "" });
    if (repository.durable) void loadHistoryPage(activeThreadId, Infinity);
    if (repository.durable && !chatAbortControllersRef.current[activeThreadId]) conversationCacheRef.current.trim(activeThreadId, CONVERSATION_PAGE_SIZE);
    conversationCacheRef.current.get(activeThreadId);
    conversationCacheRef.current.activate([activeThreadId, ...Object.keys(chatAbortControllersRef.current)]);
    setDraft(draftsByThreadRef.current[activeThreadId || "new-thread"] || "");
    setAttachments([]);
    setAttachmentError("");
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    setExpandedThoughtGroups({});
    setConversationMenuOpen(false);
    setScopeMenuOpen(false);
  }, [activeThreadId, loadHistoryPage, repository.durable]);

  useEffect(() => {
    conversationCacheRef.current.activate([
      activeThreadId,
      ...Object.keys(chatStateByThread).filter((id) => chatStateByThread[id]?.sending),
    ]);
  }, [activeThreadId, chatStateByThread]);


  function closeThreadSearch() {
    setThreadSearchOpen(false);
    threadSearchButtonRef.current?.focus();
  }

  function addAttachments(files) {
    const result = readChatAttachments(files, attachments);
    setAttachments(result.attachments);
    setAttachmentError(result.error);
  }

  function handleFileDragEnter(event) {
    if (!transferContainsFiles(event.dataTransfer)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDraggingFiles(true);
  }

  function handleFileDragOver(event) {
    if (!transferContainsFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleFileDragLeave(event) {
    if (!transferContainsFiles(event.dataTransfer)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (!dragDepthRef.current) setDraggingFiles(false);
  }

  function handleFileDrop(event) {
    if (!transferContainsFiles(event.dataTransfer)) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    addAttachments(event.dataTransfer.files);
  }

  function handleClipboardPaste(event) {
    const files = clipboardAttachmentFiles(event.clipboardData);
    if (chatState.sending && !files.length) return;
    const pastedText = event.clipboardData?.getData?.("text/plain") || "";
    const textFile = largePasteFile(pastedText);
    if (!files.length && !textFile) return;
    event.preventDefault();
    addAttachments(textFile ? [...files, textFile] : files);
  }

  const openMarkdownLinkRef = useRef(onOpenMarkdownLink);
  openMarkdownLinkRef.current = onOpenMarkdownLink;
  const openScopedMarkdownLink = useCallback((href) => {
    openMarkdownLinkRef.current?.(href, scopedProjectRoot);
  }, [scopedProjectRoot]);

  const openFileRef = useRef(onOpenFile);
  openFileRef.current = onOpenFile;
  const openScopedFile = useCallback(path => {
    openFileRef.current?.(path, scopedProjectRoot);
  }, [scopedProjectRoot]);
  const toggleThoughtGroup = useCallback(id => {
    setExpandedThoughtGroups(current => ({ ...current, [id]: current[id] === false }));
  }, []);

  useEffect(() => {
    if (!conversationMenuOpen) return undefined;

    function handlePointerDown(event) {
      if (conversationMenuRef.current?.contains(event.target)) return;
      setConversationMenuOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [conversationMenuOpen]);

  useEffect(() => {
    if (!scopeMenuOpen) return undefined;

    function handlePointerDown(event) {
      if (scopeMenuRef.current?.contains(event.target)) return;
      setScopeMenuOpen(false);
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") setScopeMenuOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [scopeMenuOpen]);

  async function sendMessage(editedMessage = null) {
    const editedMessageIndex = editedMessage
      ? messages.findIndex((message) => message.id === editedMessage.id && message.role === "user")
      : -1;
    const originalMessage = editedMessageIndex >= 0 ? messages[editedMessageIndex] : null;
    const text = originalMessage ? String(editedMessage.body ?? "").trim() : draft.trim();
    const selectedAttachments = originalMessage ? [] : attachments;
    const hasMessageAttachments = Boolean(
      originalMessage?.attachments?.length || selectedAttachments.length,
    );
    if ((!text && !hasMessageAttachments) || chatState.sending || historyLoadingRef.current || historyState.error) return;
    if (requiresPermissionChoice) return;
    const resourceError = remResourceError(activeThread?.resources || assistantDefaults.resources || DEFAULT_REM_RESOURCES);
    if (resourceError) { setAttachmentError(resourceError); return; }
    returnToLatestMessages();
    const clientTurnStartedAt = Date.now();
    const turnSummaryId = uniqueClientId();
    const targetThread = activeThread ?? createThread();
    const targetThreadId = targetThread.id;
    const workflowContext = chatWorkflowContextForThread(targetThread, workflows, openFiles);
    setChatStateByThread((current) => ({
      ...current,
      [targetThreadId]: { sending: true, error: "", hasNewResponse: false },
    }));
    setLiveTurnByThread((current) => ({
      ...current,
      [targetThreadId]: {
        id: turnSummaryId,
        role: "assistant",
        kind: "turn-summary",
        running: true,
        startedAt: clientTurnStartedAt,
        changes: null,
      },
    }));
    let messageAttachments;
    try {
      messageAttachments = originalMessage
        ? originalMessage.attachments ?? []
        : await uploadChatAttachments(selectedAttachments, targetThreadId);
    } catch (error) {
      setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
      setChatStateByThread((current) => ({
        ...current,
        [targetThreadId]: {
          sending: false,
          error: error instanceof Error ? error.message : "The attached files could not be uploaded.",
          hasNewResponse: false,
        },
      }));
      return;
    }
    const titleSource = text || `Attached ${messageAttachments.map((item) => item.name).join(", ")}`;
    const targetThreadTitle =
      activeThread?.title && activeThread.title !== "New thread"
        ? activeThread.title
        : threadTitleFromMessage(titleSource);
    deletedChatThreadIdsRef.current.delete(targetThreadId);

    const userMessage = originalMessage
      ? { ...originalMessage, body: text }
      : {
          id: uniqueClientId(),
          role: "user",
          body: text,
          attachments: messageAttachments,
        };
    const nextMessages = originalMessage
      ? [...messages.slice(0, editedMessageIndex), userMessage]
      : [...messages, userMessage];
    updateThreadMessages(targetThreadId, nextMessages);
    updateThreadTitleFromMessage(targetThreadId, titleSource);
    setDraft("");
    delete draftsByThreadRef.current[activeThreadId || "new-thread"];
    setAttachments([]);
    if (originalMessage) setExpandedThoughtGroups({});
    setBackgroundChatAnnouncement("");
    setChatAnnouncementByThread((current) => ({ ...current, [targetThreadId]: "" }));
    let contextBoundaryId = userMessage.id;
    const thoughtGroupId = uniqueClientId();
    let turnSummaryReceived = false;
    function appendAssistantMessage(body, kind = "final", extra = {}) {
      if (deletedChatThreadIdsRef.current.has(targetThreadId)) return;
      const assistantMessageId = uniqueClientId();
      updateThreadMessages(targetThreadId, (current) => {
        const previous = current.at(-1);
        if (kind === "thought" && extra.deltaStreamId &&
            previous?.kind === "thought" && previous.groupId === extra.groupId &&
            previous.deltaStreamId === extra.deltaStreamId) {
          return [...current.slice(0, -1), { ...previous, body: previous.body + body }];
        }
        const currentMessages = kind === "final"
          ? removeTrailingDuplicateOutputThought(current, body, thoughtGroupId)
          : current;
        return [
          ...currentMessages,
          {
            id: assistantMessageId,
            role: "assistant",
            kind,
            body,
            ...extra,
          },
        ];
      });
    }

    function appendTurnSummary(event) {
      if (!event?.completedAt && event?.durationMs == null && !event?.changes) return;
      turnSummaryReceived = true;
      appendAssistantMessage("", "turn-summary", {
        id: turnSummaryId,
        completedAt: event.completedAt,
        durationMs: event.durationMs,
        changes: event.changes,
      });
    }

    const abortController = new AbortController();
    const activeTurn = { turnId: uniqueClientId(), generation: 0, ready: false };
    activeChatTurnsRef.current[targetThreadId] = activeTurn;
    chatAbortControllersRef.current[targetThreadId] = abortController;
    try {
      if (assistantDefaults.swarmAccessEnabled !== false && workflowContext.projectRoot) {
        await window.goferDesktop?.workspace?.trustProjectRoot?.(workflowContext.projectRoot);
      }
      if (memorySettings.secondBrainEnabled) await window.goferDesktop?.workspace?.trustProjectRoot?.(memorySettings.secondBrainRoot);
      const requestMessages = await repository.context(targetThreadId, nextMessages, Boolean(originalMessage));
      if (activeTurn.stopRequested) throw new DOMException("Rem stopped", "AbortError");
      const response = await fetchChatTurn(chatStreamRequestBody({
          conversationId: targetThreadId,
          turnId: activeTurn.turnId,
          permissionMode,
          provider: providerId,
          model,
          effort: effort || undefined,
          messages: requestMessages
            .filter((message) => !["turn-summary", "error"].includes(message.kind))
            .map(chatMessageForRequest),
          workflow: {
            ...workflowContext,
            remSwarmAccess: {
              enabled: assistantDefaults.swarmAccessEnabled !== false,
              grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(workflowContext.projectRoot),
            },
            remSecondBrain: { enabled: memorySettings.secondBrainEnabled, root: memorySettings.secondBrainRoot, format: memorySettings.secondBrainFormat, theme: memorySettings.secondBrainTheme, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(memorySettings.secondBrainRoot) },
            remResources: targetThread.resources || assistantDefaults.resources || DEFAULT_REM_RESOURCES,
            id: `workflow-assistant:${targetThreadId}`,
            chatThreadId: targetThreadId,
          },
        }), { signal: abortController.signal });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.error || `Chat API returned ${response.status}`);
      }
      if (!response.body) {
        throw new Error("Chat API did not provide a response stream");
      }

      let finalReceived = false;
      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (value || done) {
          buffer += value ? decoder.decode(value, { stream: !done }) : decoder.decode();
          if (done && buffer && !buffer.endsWith("\n")) buffer += "\n";
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          conversationCacheRef.current.batch(() => {
            for (const line of lines) {
              const event = parseChatStreamEvent(line);
              if (!event) continue;
              if (event.turnId && event.turnId !== activeTurn.turnId) continue;
              if (event.type === "turn") {
                if (event.generation < activeTurn.generation) continue;
                activeTurn.generation = event.generation;
                activeTurn.ready = true;
                if (activeTurn.stopRequested) void stopAssistant(targetThreadId);
                continue;
              }
              if (event.type === "steering") {
                recordSteering(targetThreadId, event.receipt);
                continue;
              }
              if (event.generation != null && event.generation !== activeTurn.generation) continue;
              if (event.type === "interrupted") {
                activeTurn.generation += 1;
                const transcript = Array.isArray(event.messages) ? event.messages : [];
                const start = transcript.findLastIndex(message => message.role === "system");
                const additions = transcript.slice(start < 0 ? transcript.length : start)
                  .map(message => ({ ...message, id: uniqueClientId(), kind: message.role === "system" ? "continuation-context" : undefined }));
                if (event.partial?.changes) appendAssistantMessage("", "turn-summary", {
                  changes: event.partial.changes, completedAt: event.partial.completedAt, durationMs: event.partial.durationMs,
                });
                if (additions.length) {
                  updateThreadMessages(targetThreadId, current => [...current, ...additions]);
                  contextBoundaryId = additions.at(-1).id;
                  void repository.checkpoint(targetThreadId, transcript, contextBoundaryId)
                    .catch(error => reportStorageFailure(targetThreadId, error));
                }
                continue;
              }
              if (event.type === "stopped") throw new DOMException("Rem stopped", "AbortError");

              if (event.type === "thought") {
                const deltaStreamId = typeof event.deltaStreamId === "string" ? event.deltaStreamId : "";
                const thought = deltaStreamId ? String(event.text ?? "") : String(event.text ?? "").trim();
                if (!thought) continue;
                appendAssistantMessage(thought, "thought", {
                  groupId: thoughtGroupId,
                  deltaStreamId: deltaStreamId || undefined,
                  trace: event.trace && typeof event.trace === "object" ? event.trace : undefined,
              });
            } else if (event.type === "compaction") {
              const compactedMessages = Array.isArray(event.messages)
                ? event.messages
                : null;
              if (compactedMessages) {
                void repository.checkpoint(targetThreadId, compactedMessages, contextBoundaryId)
                  .catch(error => reportStorageFailure(targetThreadId, error));
                appendAssistantMessage(event.message || "Rem context compacted. Earlier messages are saved.", "system", { role: "system" });
              } else {
                appendAssistantMessage(
                  event.message || "Compacting Rem context",
                  "system",
                  { role: "system" },
                );
              }
            } else if (event.type === "final") {
              finalReceived = true;
              const body = event.message?.body ?? "";
              if (body.trim()) {
                appendAssistantMessage(body, "final");
              }
              appendTurnSummary(event);
              setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
            } else if (event.type === "changes") {
              setLiveTurnByThread((current) => ({
                ...current,
                [targetThreadId]: {
                  ...(current[targetThreadId] ?? {}),
                  id: turnSummaryId,
                  role: "assistant",
                  kind: "turn-summary",
                  running: true,
                  startedAt: current[targetThreadId]?.startedAt ?? clientTurnStartedAt,
                  changes: event.changes,
                },
              }));
            } else if (event.type === "error") {
              appendTurnSummary(event);
              setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
              throw new Error(event.error || "Rem failed");
            }
          }
          });
        }
        if (done) break;
      }

      if (!finalReceived) {
        throw new Error("Rem stream ended without a final response");
      }
      if (deletedChatThreadIdsRef.current.has(targetThreadId)) return;
      setChatStateByThread((current) => ({
        ...current,
        [targetThreadId]: {
          sending: false,
          error: chatStorageErrorsRef.current[targetThreadId] || "",
          hasNewResponse: activeThreadIdRef.current !== targetThreadId,
        },
      }));
      if (activeThreadIdRef.current !== targetThreadId) {
        setBackgroundChatAnnouncement(`Rem response complete in ${targetThreadTitle}.`);
      }
      setChatAnnouncementByThread((current) => ({
        ...current,
        [targetThreadId]: "Rem response complete.",
      }));
      void onResponseComplete?.(workflowContext.projectRoot);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        if (deletedChatThreadIdsRef.current.has(targetThreadId)) return;
        appendAssistantMessage("Rem stopped.", "final");
        if (!turnSummaryReceived) {
          appendTurnSummary({
            completedAt: new Date().toISOString(),
            durationMs: Date.now() - clientTurnStartedAt,
          });
        }
        setChatStateByThread((current) => ({
          ...current,
          [targetThreadId]: { sending: false, error: chatStorageErrorsRef.current[targetThreadId] || "", hasNewResponse: false },
        }));
        setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
        setChatAnnouncementByThread((current) => ({
          ...current,
          [targetThreadId]: "Rem stopped.",
        }));
        return;
      }
      if (deletedChatThreadIdsRef.current.has(targetThreadId)) return;
      setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
      if (!turnSummaryReceived) {
        appendTurnSummary({
          completedAt: new Date().toISOString(),
          durationMs: Date.now() - clientTurnStartedAt,
        });
      }
      appendAssistantMessage(error instanceof Error ? error.message : "Unable to send message", "error", { role: "system" });
      setChatStateByThread((current) => ({
        ...current,
        [targetThreadId]: {
          sending: false,
          hasNewResponse: false,
          error: chatStorageErrorsRef.current[targetThreadId] || "",
        },
      }));
    } finally {
      if (chatAbortControllersRef.current[targetThreadId] === abortController) {
        delete chatAbortControllersRef.current[targetThreadId];
        delete activeChatTurnsRef.current[targetThreadId];
        void recoverSteering(targetThreadId);
      }
    }
  }

  async function steerAssistant() {
    const threadId = activeThreadId;
    const turn = activeChatTurnsRef.current[threadId];
    if (!turn || (!draft.trim() && !attachments.length) || steeringRequestsRef.current[threadId]?.pending) return;
    const text = draft;
    const selectedAttachments = attachments;
    const attachmentKey = JSON.stringify(selectedAttachments.map(item => item.id));
    const previous = steeringRequestsRef.current[threadId];
    const request = previous?.text === text && previous.turnId === turn.turnId && previous.attachmentKey === attachmentKey
      ? { ...previous }
      : { conversationId: threadId, turnId: turn.turnId, requestId: uniqueClientId(), text, attachmentKey };
    steeringRequestsRef.current[threadId] = { ...request, pending: true };
    setSteeringBusy(current => ({ ...current, [threadId]: true }));
    setSteeringErrors(current => ({ ...current, [threadId]: "" }));
    try {
      if (!request.attachments) request.attachments = await uploadChatAttachments(selectedAttachments, threadId);
      steeringRequestsRef.current[threadId] = { ...request, pending: true };
      const response = await fetch(apiUrl("/chat/steer"), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
      });
      const payload = await response.json();
      if (!response.ok || !payload.receipt) throw new Error(payload.error || "Steering was not confirmed. Retry to check the same instruction.");
      recordSteering(threadId, payload.receipt);
      if (draftsByThreadRef.current[threadId] === text) delete draftsByThreadRef.current[threadId];
      if (activeThreadIdRef.current === threadId) setDraft(current => current === text ? "" : current);
      if (activeThreadIdRef.current === threadId) {
        const submitted = new Set(selectedAttachments.map(item => item.id));
        setAttachments(current => current.filter(item => !submitted.has(item.id)));
      }
      delete steeringRequestsRef.current[threadId];
    } catch (error) {
      setSteeringErrors(current => ({ ...current, [threadId]: error.message }));
    } finally {
      if (steeringRequestsRef.current[threadId]) steeringRequestsRef.current[threadId].pending = false;
      setSteeringBusy(current => ({ ...current, [threadId]: false }));
    }
  }

  async function stopAssistant(threadId) {
    const turn = activeChatTurnsRef.current[threadId];
    if (!turn) return;
    turn.stopRequested = true;
    if (!turn.ready) return;
    try {
      const response = await fetch(apiUrl("/chat/stop"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: threadId, turnId: turn.turnId }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not stop Rem");
    } catch (error) {
      setSteeringErrors(current => ({ ...current, [threadId]: error.message }));
    }
  }

  async function toggleAssistantChanges(threadId, messageId, changeSetId, redo) {
    if (!threadId || !changeSetId) return;
    const updateChangeState = (patch) => {
      updateThreadMessages(threadId, (current) => current.map((message) =>
        message.id === messageId
          ? { ...message, changes: { ...message.changes, ...patch } }
          : message
      ));
    };
    const action = redo ? "redo" : "undo";
    updateChangeState({ changing: true, changeError: "" });
    try {
      const response = await fetch(apiUrl(`/chat/changes/${action}`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changeSetId }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `${redo ? "Redo" : "Undo"} API returned ${response.status}`);
      }
      updateChangeState({ changing: false, undone: Boolean(payload.undone), changeError: "" });
      setChatAnnouncementByThread((current) => ({
        ...current,
        [threadId]: redo
          ? "Rem changes reapplied."
          : "Rem changes undone.",
      }));
    } catch (error) {
      updateChangeState({
        changing: false,
        changeError: error instanceof Error
          ? error.message
          : `The changes could not be ${redo ? "reapplied" : "undone"}.`,
      });
    }
  }

  function updateThreadMessages(threadId, nextValue) {
    if (deletedChatThreadIdsRef.current.has(threadId)) return;
    conversationCacheRef.current.update(threadId, nextValue);
  }

  const messageActionsRef = useRef(null);
  messageActionsRef.current = { editUserMessage, toggleAssistantChanges, activeThreadId };
  const editMessageAction = useCallback((id, body) => messageActionsRef.current.editUserMessage(id, body), []);
  const undoMessageAction = useCallback((message) => {
    const actions = messageActionsRef.current;
    return actions.toggleAssistantChanges(actions.activeThreadId, message.id, message.changes?.id, Boolean(message.changes?.undone));
  }, []);

  function editUserMessage(messageId, body) {
    if (!activeThreadId || chatState.sending) return;
    void sendMessage({ id: messageId, body });
  }

  function createThread(projectRoot = scopedProjectRoot) {
    const now = new Date().toISOString();
    const root = String(projectRoot ?? "").trim();
    const thread = scopeChatThreadToProject(
      {
        id: uniqueClientId(),
        title: "New thread",
        provider: providerId, model, effort,
        resources: structuredClone(assistantDefaults.resources || DEFAULT_REM_RESOURCES),
        permissionsByProvider: { ...permissionsByProvider, [providerId]: permissionMode },
        createdAt: now,
        updatedAt: now,
      },
      root,
      workflows,
      activeWorkflowId,
      pathValue(projectLabels, root)?.trim() || (root ? projectNameFromPath(root) : "No project"),
    );
    const nextThreads = [thread, ...threads];
    persistChatThreads(nextThreads);
    setThreads(nextThreads);
    activeThreadIdRef.current = thread.id;
    setActiveThreadId(thread.id);
    setDraft("");
    setScopeMenuOpen(false);
    return thread;
  }

  // Context events and deferred sends use the current thread settings and draft.
  const remContextActionsRef = useRef(null);
  remContextActionsRef.current = { createThread, sendMessage };

  useEffect(() => {
    let active = true;
    const receive = async (event) => {
      const context = event.detail || {};
      let root = context.projectRoot;
      if (!root && context.path) {
        const candidates = [...recentProjectRoots, ...workflows.map(item => item.projectRoot)].filter(item => typeof item === "string" && pathWithin(context.path, item));
        root = candidates.sort((a, b) => b.length - a.length)[0];
        if (!root) {
          try { root = (await window.goferDesktop?.workspace?.gitStatus?.(context.path.replace(/[/\\][^/\\]+$/, "")))?.root; } catch { /* Non-Git files still carry their exact path. */ }
        }
      }
      if (!active) return;
      const thread = remContextActionsRef.current.createThread(root || "");
      const body = context.mode === "conflicts"
        ? `Resolve the Git conflicts in this project. Inspect the current and incoming changes, preserve the intended behavior, and edit the conflicted files. Leave the results for me to review before staging or continuing the merge or rebase.\n\nConflicted files:\n${context.text}`
        : `File: ${context.path || "workflow.rattish"}\nProject: ${root || "No project"}\nLines: ${context.startLine || 1}-${context.endLine || context.startLine || 1}\n${context.version || "Editor selection"}\n\n${context.text || ""}`;
      const file = new File([body], context.mode === "conflicts" ? "merge-conflicts.txt" : "editor-selection.txt", { type: "text/plain" });
      const result = readChatAttachments([file], []);
      setPendingRemContext({ threadId: thread.id, attachments: result.attachments, error: result.error,
        draft: context.mode === "ask" ? (context.draft || "") : context.mode === "conflicts" ? body : "Explain the highlighted text in the attached file selection, using its file and project context.",
        send: context.autoSend === true || context.mode !== "ask" });
    };
    window.addEventListener("gofer:rem-context", receive);
    return () => { active = false; window.removeEventListener("gofer:rem-context", receive); };
  }, [workflows, recentProjectRoots]);

  useEffect(() => {
    const receive = event => {
      const request = event.detail;
      if (!request || request.signal?.aborted) return;
      generateConventionalCommit({ provider: providerId, model, effort, diff: request.diff, projectRoot: request.projectRoot, inspectStaged: request.inspectStaged, signal: request.signal }).then(request.resolve, request.reject);
    };
    window.addEventListener("gofer:rem-commit-message", receive);
    return () => window.removeEventListener("gofer:rem-commit-message", receive);
  }, [providerId, model, effort]);

  // Apply context after the thread activation effect clears the previous draft.
  useEffect(() => {
    if (!pendingRemContext || activeThreadId !== pendingRemContext.threadId) return;
    setAttachments(pendingRemContext.attachments); setAttachmentError(pendingRemContext.error);
    setDraft(pendingRemContext.draft);
    setContextSendThread(pendingRemContext.send ? pendingRemContext.threadId : null);
    setContextFocusRequest(current => current + 1);
    setPendingRemContext(null);
  }, [pendingRemContext, activeThreadId]);

  useEffect(() => {
    if (!contextSendThread || activeThreadId !== contextSendThread || !draft || !attachments.length
      || historyState.loading || historyState.error || chatState.sending) return;
    setContextSendThread(null);
    void remContextActionsRef.current.sendMessage();
  }, [contextSendThread, activeThreadId, draft, attachments, historyState.loading, historyState.error, chatState.sending]);

  function openThread(threadId, match) {
    setSearchTarget(match?.messageId != null ? { threadId, ...match, requestId: uniqueClientId() } : null);
    if (match?.messageId != null) setExpandedThoughtGroups({});
    const thread = threads.find((candidate) => candidate.id === threadId) || loadChatThread(threadId);
    setThreadSearchOpen(false);
    if (thread && !thread.projectRoot) {
      const scopedThread = scopeChatThreadToProject(
        thread,
        scopedProjectRoot,
        workflows,
        activeWorkflowId,
        pathValue(projectLabels, scopedProjectRoot)?.trim()
          || (scopedProjectRoot ? projectNameFromPath(scopedProjectRoot) : "No project"),
      );
      setThreads(current => [scopedThread, ...current.filter(candidate => candidate.id !== threadId)]);
    } else if (thread && !threads.some(candidate => candidate.id === threadId)) {
      setThreads(current => [...current, thread]);
    }
    if (thread) {
      setProviderId(thread.provider || assistantDefaults.provider || "codex");
      setModel(thread.model || assistantDefaults.model || "");
      setEffort(thread.effort || assistantDefaults.effort || "");
    }
    activeThreadIdRef.current = threadId;
    setActiveThreadId(threadId);
    setChatStateByThread((current) => {
      const threadState = current[threadId];
      if (!threadState?.hasNewResponse) return current;
      return {
        ...current,
        [threadId]: { ...threadState, hasNewResponse: false },
      };
    });
  }

  function renderSearchMessages(history, matchId, showSource) {
    // A query can match Markdown source, hidden metadata, or a deduplicated
    // thought. Reveal that saved body in place when its rendered form omits it.
    const displayHistory = showSource ? history.map(message => String(message.id) === String(matchId)
      ? { ...message, kind: "search-source" } : message) : history;
    return buildChatItems(displayHistory).map(item => {
      if (item.type === "thought-group") {
        return <ThoughtGroup
          key={item.id}
          expanded={expandedThoughtGroups[item.id] !== false}
          groupId={item.id}
          onToggle={toggleThoughtGroup}
          searchMessageId={matchId}
          thoughts={item.thoughts}
          onOpenLink={openScopedMarkdownLink}
          sourcePath={assistantMarkdownSourcePath(scopedProjectRoot)}
          onOpenFile={openScopedFile}
        />;
      }
      const isMatch = String(item.message.id) === String(matchId);
      return <div key={item.message.id} data-thread-search-match={isMatch || undefined} tabIndex={isMatch ? -1 : undefined}>
        <ChatMessageBubble
          message={item.message}
          onOpenLink={openScopedMarkdownLink}
          sourcePath={assistantMarkdownSourcePath(scopedProjectRoot)}
          onUndoChanges={undoMessageAction}
        />
      </div>;
    });
  }

  function returnToLatestMessages() {
    setSearchTarget(null);
    prependScrollRef.current = null;
    chatPinnedToBottomRef.current = true;
    renderedConversationRef.current = { threadId: null, textKey: null };
  }

  function changeThreadProjectScope(projectRoot) {
    const root = String(projectRoot ?? "").trim();
    if (!root || samePath(root, scopedProjectRoot)) {
      setScopeMenuOpen(false);
      return;
    }
    if (!activeThreadId) {
      setHomeProjectRoot(root);
      setScopeMenuOpen(false);
      return;
    }
    setThreads((currentThreads) => {
      const nextThreads = currentThreads.map((thread) =>
        thread.id === activeThreadId
          ? scopeChatThreadToProject(
              thread,
              root,
              workflows,
              activeWorkflowId,
              pathValue(projectLabels, root)?.trim() || projectNameFromPath(root),
            )
          : thread,
      );
      return nextThreads;
    });
    setHomeProjectRoot(root);
    setScopeMenuOpen(false);
  }

  useEffect(() => {
    if (!activeThread?.projectRoot || activeThread.projectBranch !== undefined) return;
    let cancelled = false;
    const { id, projectRoot } = activeThread;
    window.goferDesktop?.workspace?.gitStatus?.(projectRoot)?.then(snapshot => {
      if (cancelled || !snapshot || snapshot.error) return;
      setThreads(current => current.map(thread => thread.id === id && samePath(thread.projectRoot, projectRoot)
        ? { ...thread, projectBranch: snapshot.active ? snapshot.branch || "" : "" } : thread));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [activeThread]);

  function updateThreadConfig(patch) {
    if (!activeThreadId) return;
    setThreads((current) => {
      const next = current.map((thread) => thread.id === activeThreadId ? { ...thread, ...patch } : thread);
      return next;
    });
  }

  function showThreadList() {
    activeThreadIdRef.current = null;
    setActiveThreadId(null);
    setConversationMenuOpen(false);
    setScopeMenuOpen(false);
  }

  function updateThreadTitleFromMessage(threadId, message) {
    setThreads((currentThreads) => {
      const nextThreads = currentThreads.map((thread) =>
        thread.id === threadId
          ? {
              ...thread,
              title: thread.title === "New thread" ? threadTitleFromMessage(message) : thread.title,
              updatedAt: new Date().toISOString(),
            }
          : thread,
      );
      return nextThreads;
    });
  }

  function updateThreadOrganization(threadId, patch) {
    setThreads(current => {
      const thread = current.find(item => item.id === threadId) || loadChatThread(threadId);
      if (!thread) return current;
      return [...current.filter(item => item.id !== threadId), { ...thread, ...patch }];
    });
  }

  function archiveThread(threadId) {
    updateThreadOrganization(threadId, { archived: true, pinned: false });
  }

  function pinThread(threadId, pinned) {
    updateThreadOrganization(threadId, { pinned, archived: false });
  }

  async function deleteThread(threadId) {
    const thread = threads.find(item => item.id === threadId) || loadChatThread(threadId);
    if (!window.confirm(`Delete thread "${thread?.title || "Untitled"}"? This cannot be undone.`)) return;
    deletedChatThreadIdsRef.current.add(threadId);
    await stopAssistant(threadId);
    const nextThreads = threads.filter((thread) => thread.id !== threadId);
    const archived = await archiveThreadFromStorage(threadId, () => repository.all(threadId), true);
    if (!archived) {
      deletedChatThreadIdsRef.current.delete(threadId);
      setChatStateByThread((current) => ({ ...current, [threadId]: { sending: false, error: "The archive could not be saved. Reconnect the archive folder, or stop archiving in Settings > Memory before deleting this thread." } }));
      return;
    }
    try { await repository.remove(threadId); } catch (error) {
      deletedChatThreadIdsRef.current.delete(threadId);
      reportStorageFailure(threadId, error);
      return;
    }
    deleteStoredChatThread(threadId);
    persistChatThreads(nextThreads);
    setThreads(nextThreads);
    conversationCacheRef.current.remove(threadId);
    delete chatStorageErrorsRef.current[threadId];
    setChatStateByThread((current) => {
      const next = { ...current };
      delete next[threadId];
      return next;
    });
    setLiveTurnByThread((current) => {
      const next = { ...current };
      delete next[threadId];
      return next;
    });
    if (activeThreadId === threadId) {
      activeThreadIdRef.current = null;
      setActiveThreadId(null);
    }
    setExpandedThoughtGroups({});
    setConversationMenuOpen(false);

    try {
      const response = await fetch(
        apiUrl(`/chat/threads/${encodeURIComponent(threadId)}`),
        { method: "DELETE" },
      );
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.error || `Chat API returned ${response.status}`);
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unable to delete chat handoff file";
      if (!deletedChatThreadIdsRef.current.has(threadId) && activeThreadId === threadId) {
        setChatStateByThread((current) => ({
          ...current,
          [threadId]: { sending: false, error: message },
        }));
      }
    }
  }

  return (
    <aside
      aria-busy={chatState.sending || undefined}
      className="studio-chat relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-l border-line bg-white"
      data-chat-pane="true"
      style={{ width }}
      onDragEnter={handleFileDragEnter}
      onDragLeave={handleFileDragLeave}
      onDragOver={handleFileDragOver}
      onDrop={handleFileDrop}
      onPaste={handleClipboardPaste}
    >
      {draggingFiles ? (
        <div
          aria-live="polite"
          className="pointer-events-none absolute inset-2 z-[70] grid place-items-center rounded-[14px] border-2 border-dashed border-brand bg-indigo-50/95 text-indigo-700 dark:bg-[#252526]/95 dark:text-indigo-300"
          role="status"
        >
          <div className="flex flex-col items-center gap-2 text-center">
            <Paperclip aria-hidden="true" size={20} />
            <span className="text-xs font-semibold">Drop files to attach</span>
          </div>
        </div>
      ) : null}
      <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {chatAnnouncement}
      </div>
      <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {backgroundChatAnnouncement}
      </div>
      <div aria-atomic="true" aria-live="assertive" className="sr-only" role="alert">
        {chatState.error}
      </div>
      <div
        aria-label="Resize chat pane"
        aria-orientation="vertical"
        aria-valuemax={520}
        aria-valuemin={300}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels wide`}
        className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
        role="separator"
        tabIndex={0}
        title="Resize chat pane"
        onKeyDown={onResizeKeyDown}
        onPointerDown={onResizeStart}
      />
      <div className="flex h-[54px] shrink-0 items-center justify-between border-b border-line px-3.5">
        <div className="flex min-w-0 items-center gap-1 text-xs font-semibold text-muted">
          {activeThread ? (
            <button
              aria-label="Back to active threads"
              className="studio-icon-button -ml-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
              title="Back to active threads"
              type="button"
              onClick={showThreadList}
            >
              <ArrowLeft aria-hidden="true" size={16} />
            </button>
          ) : null}
          <div ref={scopeMenuRef} className="relative min-w-0">
            <button
              aria-expanded={scopeMenuOpen}
              aria-haspopup="menu"
              aria-label={`Scoped to ${scopedProjectName}. Change project scope`}
              className="flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
              disabled={chatState.sending || !scopeRoots.length}
              title={chatState.sending ? "Project scope cannot change while Rem is running" : scopedProjectRoot}
              type="button"
              onClick={() => {
                setConversationMenuOpen(false);
                setScopeAvailableRoots([]);
                setScopeWorktrees([]);
                setScopeLoading(true);
                setScopeMenuOpen((current) => !current);
              }}
            >
              <GitBranch aria-hidden="true" className="shrink-0" size={14} />
              <span className="min-w-0 truncate">Scoped to {scopedProjectName}</span>
              <ChevronDown
                aria-hidden="true"
                className={`shrink-0 transition ${scopeMenuOpen ? "rotate-180" : ""}`}
                size={12}
              />
            </button>
            {scopeMenuOpen ? (
              <div
                aria-label="Rem project scope"
                className="absolute left-0 top-9 z-50 max-h-72 w-72 overflow-y-auto rounded-[14px] border border-line bg-white p-1.5 shadow-panel"
                role="menu"
              >
                <p className="px-2 py-1 text-[10px] font-semibold text-muted">Projects and worktrees</p>
                {scopeLoading ? <p role="status" className="px-2 py-1 text-xs text-muted">Checking workspace folders...</p> : null}
                {!scopeLoading && !scopeProjects.length ? <p role="status" className="px-2 py-1 text-xs text-muted">No workspace folders available.</p> : null}
                {scopeProjects.map((project) => (
                  <button
                    key={project.root}
                    className={`flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition hover:bg-slate-50 ${
                      samePath(project.root, scopedProjectRoot)
                        ? "bg-indigo-50 font-semibold text-indigo-700"
                        : "text-ink"
                    }`}
                    role="menuitem"
                    title={project.root}
                    type="button"
                    onClick={() => changeThreadProjectScope(project.root)}
                  >
                    <FolderOpen aria-hidden="true" className="shrink-0 text-muted" size={13} />
                    <span className="min-w-0 flex-1 truncate">{project.name}</span>
                    {samePath(project.root, scopedProjectRoot) ? (
                      <Check aria-hidden="true" className="shrink-0" size={12} />
                    ) : null}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
        <div ref={conversationMenuRef} className="relative flex items-center gap-1">
          <button
            aria-label="New thread"
            className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="New thread"
            type="button"
            onClick={() => createThread()}
          >
            <Plus aria-hidden="true" size={17} />
          </button>
          <button ref={threadSearchButtonRef} aria-label="Search threads" aria-expanded={threadSearchOpen}
            className="studio-icon-button grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Search threads" type="button" onClick={() => { setThreadSearchOpen(current => !current); setConversationMenuOpen(false); setScopeMenuOpen(false); }}>
            <Search aria-hidden="true" size={16} />
          </button>
          <button
            aria-expanded={conversationMenuOpen}
            aria-label="Active threads"
            className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Active threads"
            type="button"
            onClick={() => {
              setScopeMenuOpen(false);
              setConversationMenuOpen((current) => !current);
            }}
          >
            <History aria-hidden="true" size={16} />
          </button>
          {conversationMenuOpen ? (
            <div className="absolute right-0 top-9 z-50 max-h-80 w-72 overflow-y-auto rounded-[14px] border border-line bg-white p-1.5 shadow-panel">
              <ThreadSections
                activityByThread={chatStateByThread}
                threads={threads}
                activeThreadId={activeThreadId}
                onArchive={archiveThread}
                onPin={pinThread}
                onDelete={deleteThread}
                onOpen={openThread}
              />
            </div>
          ) : null}
        </div>
      </div>

      {threadSearchOpen ? <ThreadSearch repository={repository} loadThreads={loadAllChatThreads} onOpen={openThread} onClose={closeThreadSearch} /> : null}

      {activeSearchTarget ? <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3.5 py-2 text-xs text-muted">
        <span className="min-w-0 truncate" role="status">Match for &quot;{activeSearchTarget.query}&quot;</span>
        <button type="button" className="shrink-0 rounded px-2 py-1 text-brand focus-visible:outline-brand" onClick={returnToLatestMessages}>Latest messages</button>
      </div> : null}

      <div
        ref={chatScrollRef}
        data-chat-scroll="true"
        style={{ overflowAnchor: "none" }}
        className="workflow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto px-3.5 py-4"
        onScroll={(event) => {
          if (activeSearchTarget) return;
          chatPinnedToBottomRef.current = conversationIsAtBottom(event.currentTarget);
          if (event.currentTarget.scrollTop < 80 && historyState.threadId === activeThreadId && historyState.hasMore && !historyState.error) void loadHistoryPage(activeThreadId, historyState.before);
        }}
      >
        {!activeThread ? (
          <div className="min-h-full" data-assistant-home>
            <div className="mx-auto max-w-[340px] px-2 pb-4 pt-1 text-center">
              {assistantDefaults.avatarEnabled !== false ? (
                <RemAvatar visible={visible} animated={assistantDefaults.avatarAnimated !== false} reducedMotion={reducedMotion} />
              ) : null}
              <h2 className="mt-2 text-lg font-semibold tracking-[-0.02em] text-ink">I&apos;m Rem</h2>
              <p className="mt-1 text-xs leading-5 text-muted">Your coding agent in Raticode.</p>
              <p className="mt-2 text-xs leading-5 text-muted">I can build workflows, change code, and help you understand your project.</p>
              <p className="mt-2 text-xs font-medium leading-5 text-ink">What would you like to work on?</p>
            </div>
            <section aria-label="Threads" className="border-t border-line pt-3">
              <ThreadSections
                activityByThread={chatStateByThread}
                threads={threads}
                activeThreadId={activeThreadId}
                onArchive={archiveThread}
                onPin={pinThread}
                onDelete={deleteThread}
                onOpen={openThread}
              />
            </section>
          </div>
        ) : activeSearchTarget ? (
          <ThreadHistoryMatch
            key={activeSearchTarget.requestId}
            repository={repository}
            target={activeSearchTarget}
            scrollRef={chatScrollRef}
            renderMessages={renderSearchMessages}
          />
        ) : (
          <>
            {historyState.threadId === activeThreadId && (historyState.loading || historyState.hasMore || historyState.error) ? (
              <div className="rem-history-loader flex min-h-8 items-center justify-center gap-2 text-[11px] text-muted" aria-live="polite">
                {historyState.loading ? <><Loader2 aria-hidden="true" className="motion-safe:animate-spin" size={14} /><span role="status">Loading earlier messages…</span></> : historyState.error ? <><span role="alert">{historyState.error}</span><button className="rounded px-2 py-1 text-brand focus-visible:outline-brand" type="button" onClick={() => loadHistoryPage(activeThreadId, historyState.before)}>Retry</button></> : <button className="rounded px-2 py-1 hover:text-ink focus-visible:outline-brand" type="button" onClick={() => loadHistoryPage(activeThreadId, historyState.before)}>Load earlier messages</button>}
              </div>
            ) : null}
            {!messages.length && !historyState.loading ? <p className="text-sm leading-6 text-muted">I&apos;m Rem, your coding agent in Raticode. I can build workflows, change code, and help you understand your project. What would you like to work on?</p> : null}
            {conversationItems.map((item) =>
              item.type === "thought-group" ? (
                <ThoughtGroup
                  key={item.id}
                  expanded={expandedThoughtGroups[item.id] !== false}
                  onOpenLink={openScopedMarkdownLink}
                  sourcePath={assistantMarkdownSourcePath(scopedProjectRoot)}
                  onOpenFile={openScopedFile}
                  thoughts={item.thoughts}
                  groupId={item.id}
                  onToggle={toggleThoughtGroup}
                />
              ) : (
                <ChatMessageBubble
                  key={item.message.id}
                  canEdit={!chatState.sending && item.message.id === latestUserMessageId}
                  message={item.message}
                  onEdit={editMessageAction}
                  onOpenLink={openScopedMarkdownLink}
                  sourcePath={assistantMarkdownSourcePath(scopedProjectRoot)}
                  onUndoChanges={undoMessageAction}
                />
              ),
            )}
            {chatState.error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm leading-5 text-red-700">
                {chatState.error}
              </div>
            ) : null}
          </>
        )}
      </div>

      <div className="relative shrink-0 border-t border-line p-3">
          {activeThread ? <div className="mb-2">
            <button aria-expanded={resourcesOpen} className="rounded px-1 py-1 text-xs text-muted hover:bg-slate-50" type="button" onClick={() => setResourcesOpen((open) => !open)}>Thread tools, skills & MCP</button>
            {resourcesOpen ? <div className="max-h-64 overflow-y-auto border-t border-line py-2"><RemResources key={activeThreadId} value={activeThread.resources || assistantDefaults.resources || DEFAULT_REM_RESOURCES} onChange={(resources) => updateThreadConfig({ resources })} /></div> : null}
          </div> : null}
          <ProviderModelEffortFields
            capabilities={providers}
            loading={providersLoading}
            className="mb-2"
            disabled={providersLoading || chatState.sending}
            effort={effort}
            model={model}
            provider={providerId}
            onChange={(patch) => {
              updateThreadConfig(patch);
              if (patch.provider !== undefined) setProviderId(patch.provider);
              if (patch.model !== undefined) setModel(patch.model);
              if (patch.effort !== undefined) setEffort(patch.effort);
            }}
            onRefresh={refreshProviders}
          />
          {providerDiscoveryError ? (
            <p className="mb-2 text-xs text-red-600">{providerDiscoveryError}</p>
          ) : null}
          {steeringErrors[activeThreadId] ? <p role="alert" className="mb-2 text-xs text-red-600">{steeringErrors[activeThreadId]}</p> : null}
          {requiresPermissionChoice ? <div role="alert" className="mb-2 rounded-lg border border-line p-3 text-xs">
            <p>{providerCapability?.displayName || providerId} needs CLI-managed permissions to send messages. Raticode&apos;s tool restrictions are unsupported.</p>
            <button type="button" className="mt-2 font-semibold underline" disabled={chatState.sending} onClick={() => selectPermission("cli-managed")}>Use CLI-managed permissions</button>
          </div> : null}
          <ChatComposer
            onSteer={steerAssistant}
            steeringPending={Boolean(steeringBusy[activeThreadId])}
            provider={providerId}
            permissionMode={permissionMode}
            permissionOptions={permissionOptions}
            onPermissionModeChange={selectPermission}
            attachments={attachments}
            attachmentError={attachmentError}
            audioInputDeviceId={audioInputDeviceId}
            contextKey={activeThreadId ?? "new-thread"}
            draft={draft}
            focusRequest={composerFocusRequest + contextFocusRequest}
            sending={chatState.sending}
            sendDisabled={requiresPermissionChoice || (historyState.threadId === activeThreadId && Boolean(historyState.loading || historyState.error))}
            onAddAttachments={addAttachments}
            onAttachmentErrorChange={setAttachmentError}
            onAttachmentsChange={setAttachments}
            onDraftChange={value => {
              draftsByThreadRef.current[activeThreadId || "new-thread"] = value;
              setDraft(value);
            }}
            onSend={sendMessage}
            onStop={() => activeThreadId && stopAssistant(activeThreadId)}
          />
      </div>
    </aside>
  );
}

export function ThreadSections({ threads, ...props }) {
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveCount, setArchiveCount] = useState(10);
  const [scopeState, setScopeState] = useState(() => cachedThreadScopes(window.goferDesktop?.workspace));
  const [now, setNow] = useState(Date.now);
  const entries = useMemo(() => [...new Map([...chatThreadIndex(), ...threads.map(threadIndexEntry)].map(entry => [entry.id, entry])).values()], [threads]);
  const scopeKey = threadScopeKey(entries, now);
  useEffect(() => {
    let cancelled = false;
    let running = false;
    const refresh = async (force = false) => {
      if (running) return;
      running = true;
      const result = await inspectThreadScopes(JSON.parse(scopeKey), window.goferDesktop?.workspace, { force: Boolean(force) });
      running = false;
      if (!cancelled) { setScopeState(result); setNow(Date.now()); }
    };
    void refresh();
    const timer = window.setInterval(() => refresh(true), 60000);
    window.addEventListener("focus", refresh);
    window.addEventListener("gofer:git-files-changed", refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("gofer:git-files-changed", refresh);
    };
  }, [scopeKey]);
  const active = [], archived = [], pinned = [];
  for (const entry of entries) {
    (threadIsArchived(entry, scopeState?.missingRoots, scopeState?.branches, now) ? archived : entry.pinned ? pinned : active).push(entry);
  }
  const loaded = useMemo(() => new Map(threads.map(thread => [thread.id, thread])), [threads]);
  const rowCache = useRef(new Map());
  const loadThread = entry => {
    if (loaded.has(entry.id)) return loaded.get(entry.id);
    const cached = rowCache.current.get(entry.id);
    if (cached?.entry === entry) return cached.thread;
    const thread = loadChatThread(entry.id);
    rowCache.current.set(entry.id, { entry, thread });
    return thread;
  };
  const sortedArchived = archived.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
  const sectionHeadingClass = "mb-2 px-3 py-1 text-xs font-semibold text-muted";
  return <>
    {pinned.length ? <section aria-label="Pinned threads">
      <h3 className={sectionHeadingClass}>Pinned threads</h3>
      <ThreadList {...props} threads={pinned} loadThread={loadThread} />
    </section> : null}
    <section aria-label="Active threads" className={pinned.length ? "mt-4 border-t border-line pt-3" : undefined}>
      <h3 className={sectionHeadingClass}>Active threads</h3>
      <ThreadList {...props} threads={active} loadThread={loadThread} />
    </section>
    <section className="mt-4 border-t border-line pt-3">
      <button type="button" aria-expanded={archiveOpen} className={`${sectionHeadingClass} flex w-full items-center gap-2 text-left`}
        onClick={() => { setArchiveOpen(open => !open); setArchiveCount(10); }}>
        <ChevronRight aria-hidden="true" size={13} className={archiveOpen ? "rotate-90" : ""} />Archived threads
      </button>
      {archiveOpen ? <ThreadList {...props} archived key="archive" threads={sortedArchived.slice(0, archiveCount)} loadThread={loadThread}
        pageSize={10} totalCount={archived.length} onLoadOlder={setArchiveCount} /> : null}
    </section>
  </>;
}

function ThreadActions({ thread, onPin, onDelete }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const trigger = useRef(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", dismiss); window.removeEventListener("keydown", escape); };
  }, [open]);
  return <div ref={root} className="relative" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button ref={trigger} title="Thread options" aria-label={`Thread options for ${thread.title}`} aria-expanded={open}
      className="grid h-8 w-8 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
      type="button" onClick={() => setOpen(value => !value)}><MoreVertical aria-hidden="true" size={15} /></button>
    {open ? <div aria-label={`Actions for ${thread.title}`} className="absolute right-0 top-8 z-50 w-36 rounded-lg border border-line bg-white p-1 shadow-panel">
      <button type="button" className="w-full rounded px-2 py-2 text-left text-xs text-ink hover:bg-slate-50"
        onClick={() => { setOpen(false); trigger.current?.focus(); onPin?.(thread.id, !thread.pinned); }}>{thread.pinned ? "Unpin thread" : "Pin thread"}</button>
      <button type="button" className="w-full rounded px-2 py-2 text-left text-xs text-red-600 hover:bg-red-50"
        onClick={() => { setOpen(false); trigger.current?.focus(); onDelete(thread.id); }}>Delete thread</button>
    </div> : null}
  </div>;
}

export function ThreadList({ activeThreadId, activityByThread = {}, onArchive, onPin, onDelete, onOpen, archived = false, threads, totalCount = threads.length, onLoadOlder, pageSize = 15, loadThread = entry => entry }) {
  const [visibleCount, setVisibleCount] = useState(pageSize);
  const sortedThreads = [...threads].sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
  if (threads.length) {
    return (
      <div className="space-y-1">
          {sortedThreads.slice(0, visibleCount).map(loadThread).filter(Boolean).map((thread) => (
            <div
              key={thread.id}
              className={`group flex items-center gap-1 rounded-lg px-1 transition ${
                thread.id === activeThreadId ? "bg-indigo-50" : "hover:bg-slate-50"
              }`}
            >
              <button
                className="min-w-0 flex-1 px-2 py-1.5 text-left"
                type="button"
                onClick={() => onOpen(thread.id)}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <div className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
                    {thread.title}
                  </div>
                  <ThreadActivityIndicator state={activityByThread[thread.id]} />
                </div>
                <div className="mt-0.5 text-[10px] text-muted">{formatThreadDate(thread.updatedAt)}</div>
              </button>
              {archived ? <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-red-50 hover:text-red-600"
                title="Delete thread" aria-label={`Delete thread ${thread.title}`} type="button"
                onClick={() => onDelete(thread.id)}>
                <Trash2 aria-hidden="true" size={15} />
              </button> : <button
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
                title="Archive thread" aria-label={`Archive thread ${thread.title}`} type="button"
                onClick={() => onArchive?.(thread.id)}>
                <Archive aria-hidden="true" size={15} />
              </button>}
              <ThreadActions thread={thread} onPin={onPin} onDelete={onDelete} />
            </div>
          ))}
          {totalCount > visibleCount ? (
            <button className="w-full rounded-md px-2 py-2 text-left text-xs text-muted hover:bg-slate-50 focus-visible:outline" type="button" onClick={() => { const nextCount = visibleCount + pageSize; onLoadOlder?.(nextCount); setVisibleCount(nextCount); }}>
              Show older threads ({totalCount - visibleCount})
            </button>
          ) : null}
          {visibleCount > pageSize ? <button className="px-2 py-2 text-xs text-muted" type="button" onClick={() => setVisibleCount(pageSize)}>Collapse older threads</button> : null}
      </div>
    );
  }

  return (
    <p className="px-2 py-4 text-center text-xs text-muted">No thread history yet.</p>
  );
}

function ThreadActivityIndicator({ state }) {
  if (state?.sending) {
    return (
      <span
        className="grid h-4 w-4 shrink-0 place-items-center text-brand"
        title="Rem response running"
      >
        <Loader2 aria-hidden="true" className="animate-spin" size={13} />
        <span className="sr-only">Running</span>
      </span>
    );
  }

  if (state?.hasNewResponse) {
    return (
      <span
        className="grid h-4 w-4 shrink-0 place-items-center"
        title="Rem response complete"
      >
        <span aria-hidden="true" className="h-2 w-2 rounded-full bg-blue-500" />
        <span className="sr-only">Completed</span>
      </span>
    );
  }

  return null;
}

export function conversationIsAtBottom(element, tolerance = 4) {
  if (!element) return false;
  return element.scrollHeight - element.scrollTop - element.clientHeight <= tolerance;
}

export function scrollConversationToBottom(element) {
  if (!element) return;
  element.scrollTop = element.scrollHeight;
}

const ChatMessageBubble = memo(function ChatMessageBubble({ canEdit = false, message, onEdit, onOpenLink, onUndoChanges, sourcePath }) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState(message.body);
  const copyResetTimerRef = useRef(null);

  useEffect(() => () => window.clearTimeout(copyResetTimerRef.current), []);

  if (message.kind === "turn-summary") {
    return <TurnSummaryCard message={message} onUndo={() => onUndoChanges(message)} />;
  }
  if (message.kind === "error") {
    return <div data-message-id={message.id} data-history-anchor={message.id}
      role="alert" aria-live="assertive"
      className="whitespace-pre-wrap break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
      {message.body}
    </div>;
  }
  const isSystem = message.role === "system" || message.kind === "system";
  const isUser = message.role === "user";

  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(message.body);
      setCopied(true);
      window.clearTimeout(copyResetTimerRef.current);
      copyResetTimerRef.current = window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function beginEditing() {
    setEditDraft(message.body);
    setEditing(true);
  }

  function cancelEditing() {
    setEditDraft(message.body);
    setEditing(false);
  }

  function saveEdit() {
    const body = editDraft.trim();
    if (!body && !message.attachments?.length) return;
    onEdit?.(message.id, body);
    setEditing(false);
  }

  return (
    <div
      data-message-id={message.id}
      data-history-anchor={message.id}
      className={`flex ${
        isSystem ? "justify-center" : message.role === "user" ? "justify-end" : "justify-start"
      }`}
    >
      <div
        className={`${isSystem ? "max-w-[86%]" : "w-full min-w-0"} rounded-lg px-3 py-2 text-sm leading-6 ${
          isSystem
            ? "border border-line bg-slate-50 text-xs font-medium text-muted"
            : message.role === "user"
            ? "bg-brand text-white"
            : "border border-line bg-white text-slate-700 shadow-sm"
        }`}
      >
        {isSystem ? (
          <span className="whitespace-pre-wrap break-words">{message.body}</span>
        ) : (
          <>
            {isUser ? (
              <div className="mb-1 flex h-5 items-center justify-end gap-0.5">
                {canEdit ? (
                  <button
                    aria-label="Edit message"
                    className="grid h-5 w-5 place-items-center rounded text-indigo-100 outline-none transition hover:bg-white/15 hover:text-white focus-visible:ring-2 focus-visible:ring-white/70"
                    title="Edit message"
                    type="button"
                    onClick={beginEditing}
                  >
                    <PencilLine aria-hidden="true" size={11} />
                  </button>
                ) : null}
                <button
                  aria-label={copied ? "Message copied" : "Copy message"}
                  className="grid h-5 w-5 place-items-center rounded text-indigo-100 outline-none transition hover:bg-white/15 hover:text-white focus-visible:ring-2 focus-visible:ring-white/70"
                  title={copied ? "Message copied" : "Copy message"}
                  type="button"
                  onClick={() => void copyMessage()}
                >
                  {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
                </button>
              </div>
            ) : null}
            {isUser ? <MessageAttachments attachments={message.attachments} inverse /> : null}
            {isUser && editing ? (
              <div className="space-y-2">
                <textarea
                  aria-label="Edit message text"
                  autoFocus
                  className="workflow-scrollbar max-h-48 min-h-20 w-full resize-y rounded-md border border-white/35 bg-white/10 px-2.5 py-2 text-sm leading-5 text-white outline-none placeholder:text-indigo-200 focus:border-white/70 focus:ring-2 focus:ring-white/25"
                  value={editDraft}
                  onChange={(event) => setEditDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") cancelEditing();
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) saveEdit();
                  }}
                />
                <div className="flex justify-end gap-1.5">
                  <button
                    className="h-7 rounded-md px-2 text-xs font-medium text-indigo-100 outline-none transition hover:bg-white/15 hover:text-white focus-visible:ring-2 focus-visible:ring-white/70"
                    type="button"
                    onClick={cancelEditing}
                  >
                    Cancel
                  </button>
                  <button
                    className="h-7 rounded-md bg-white px-2.5 text-xs font-semibold text-brand outline-none transition hover:bg-indigo-50 focus-visible:ring-2 focus-visible:ring-white/70 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!editDraft.trim() && !message.attachments?.length}
                    type="button"
                    onClick={saveEdit}
                  >
                    Send again
                  </button>
                </div>
              </div>
            ) : message.body ? (
              <>
                {message.kind === "search-source" ? <div className="whitespace-pre-wrap break-words">{message.body}</div>
                  : <MarkdownMessage inverse={isUser} sourcePath={sourcePath} onOpenLink={onOpenLink} value={message.body} />}
                {!isUser ? (
                  <div className="mt-1 flex justify-end border-t border-line/70 pt-1">
                    <button
                      aria-label={copied ? "Response copied" : "Copy response as Markdown"}
                      className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-[10px] leading-none text-muted outline-none transition hover:bg-slate-50 hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
                      title={copied ? "Response copied" : "Copy response as Markdown"}
                      type="button"
                      onClick={() => void copyMessage()}
                    >
                      {copied ? <Check aria-hidden="true" size={11} /> : <Copy aria-hidden="true" size={11} />}
                      <span>{copied ? "Copied" : "Copy Markdown"}</span>
                    </button>
                  </div>
                ) : null}
              </>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
});

function TurnSummaryCard({ message, onUndo }) {
  const [reviewing, setReviewing] = useState(false);
  const [showAllFiles, setShowAllFiles] = useState(false);
  const changes = message.changes;
  const files = Array.isArray(changes?.files) ? changes.files : [];
  const visibleFiles = showAllFiles ? files : files.slice(0, 3);
  const liveDurationMs = useLiveDuration(message.startedAt, message.running);
  const timing = message.running
    ? `Running for ${formatAssistantDuration(liveDurationMs)}`
    : formatAssistantTurnTiming(message.completedAt, message.durationMs);

  if (!changes) {
    return (
      <div
        aria-label="Rem running"
        className="flex items-center gap-1.5 px-1 text-[10px] text-muted"
        data-message-id={message.id}
      data-history-anchor={message.id}
      >
        {message.running ? <Loader2 aria-hidden="true" className="animate-spin" size={11} /> : null}
        <span>{timing}</span>
      </div>
    );
  }

  return (
    <div className="space-y-1.5" data-message-id={message.id}>
      <section
        aria-label="Rem file changes"
        className="overflow-hidden rounded-lg border border-line bg-white dark:bg-[#181818]"
      >
        <div className="flex min-h-12 items-center gap-2.5 px-3 py-2">
          <FileDiff aria-hidden="true" className="shrink-0 text-muted" size={16} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                {message.running ? <Loader2 aria-hidden="true" className="animate-spin text-brand" size={12} /> : null}
                {message.running ? assistantLiveChangeLabel(files) : assistantChangeLabel(files)}
              </span>
              {files.some(file => !file.binary) ? <><span className="font-medium text-emerald-600">+{changes.additions ?? 0}</span><span className="font-medium text-red-500">-{changes.deletions ?? 0}</span></> : null}
              {files.some(file => file.binary) ? <span className="text-muted">{files.filter(file => file.binary).length} without line counts</span> : null}
            </div>
          </div>
          <button
            className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted outline-none transition hover:bg-slate-50 hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#242426]"
            disabled={message.running || !changes.undoable || changes.changing}
            title={!changes.undoable ? changes.undoUnavailableReason : undefined}
            type="button"
            onClick={onUndo}
          >
            {changes.changing ? (
              <Loader2 aria-hidden="true" className="animate-spin" size={13} />
            ) : changes.undone ? (
              <Redo2 aria-hidden="true" size={13} />
            ) : (
              <Undo2 aria-hidden="true" size={13} />
            )}
            {changes.changing ? (changes.undone ? "Redoing" : "Undoing") : changes.undone ? "Redo" : "Undo"}
          </button>
          <button
            aria-expanded={reviewing}
            className="h-8 rounded-md border border-line px-2.5 text-xs font-medium text-ink outline-none transition hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
            type="button"
            onClick={() => setReviewing((current) => !current)}
          >
            {reviewing ? "Close" : "Review"}
          </button>
        </div>
        <div className="border-t border-line px-3 py-2">
          <div className="space-y-1.5">
            {visibleFiles.map((file) => (
              <div className="flex min-w-0 items-center gap-2 text-[11px]" key={file.path}>
                <span className="min-w-0 flex-1 truncate text-muted" title={file.path}>{file.path}</span>
                {file.binary ? <span className="shrink-0 text-muted">{file.diff?.includes("too large") ? "Too large to count" : "Binary"}</span> : <><span className="shrink-0 font-medium text-emerald-600">+{file.additions ?? 0}</span><span className="shrink-0 font-medium text-red-500">-{file.deletions ?? 0}</span></>}
              </div>
            ))}
          </div>
          {files.length > 3 ? (
            <button
              className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted outline-none transition hover:text-ink focus-visible:ring-2 focus-visible:ring-brand/30"
              type="button"
              onClick={() => setShowAllFiles((current) => !current)}
            >
              {showAllFiles ? "Show fewer files" : `Show ${files.length - 3} more files`}
              <ChevronDown aria-hidden="true" className={`transition-transform ${showAllFiles ? "rotate-180" : ""}`} size={13} />
            </button>
          ) : null}
        </div>
        {reviewing ? (
          <div className="max-h-80 space-y-3 overflow-auto border-t border-line bg-slate-50 px-3 py-3 dark:bg-[#111113]">
            {files.map((file) => <AssistantDiffPreview file={file} key={file.path} />)}
          </div>
        ) : null}
        {changes.changeError ? (
          <p className="border-t border-red-200 bg-red-50 px-3 py-2 text-[11px] leading-4 text-red-700">
            {changes.changeError}
          </p>
        ) : null}
      </section>
      <div className="px-1 text-[10px] text-muted">{timing}</div>
    </div>
  );
}

function AssistantDiffPreview({ file }) {
  return (
    <section aria-label={`Diff for ${file.path}`}>
      <h4 className="mb-1.5 truncate font-mono text-[10px] font-semibold text-ink" title={file.path}>
        {file.path}
      </h4>
      <pre className="workflow-scrollbar overflow-x-auto rounded-md border border-line bg-white py-1 font-mono text-[10px] leading-4 dark:bg-[#181818]">
        {String(file.diff || "No text preview available.").split("\n").map((line, index) => (
          <span
            className={`block min-w-max px-2 ${
              line.startsWith("+") && !line.startsWith("+++")
                ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300"
                : line.startsWith("-") && !line.startsWith("---")
                ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300"
                : "text-slate-600 dark:text-[#b9b9b9]"
            }`}
            key={`${index}-${line}`}
          >
            {line || " "}
          </span>
        ))}
      </pre>
    </section>
  );
}

export function assistantChangeLabel(files) {
  const count = files.length;
  const noun = count === 1 ? "file" : "files";
  if (count && files.every((file) => file.status === "added")) return `Created ${count} ${noun}`;
  if (count && files.every((file) => file.status === "deleted")) return `Deleted ${count} ${noun}`;
  return `Edited ${count} ${noun}`;
}

function assistantLiveChangeLabel(files) {
  const count = files.length;
  const noun = count === 1 ? "file" : "files";
  if (count && files.every((file) => file.status === "added")) return `Creating ${count} ${noun}`;
  if (count && files.every((file) => file.status === "deleted")) return `Deleting ${count} ${noun}`;
  return `Editing ${count} ${noun}`;
}

function useLiveDuration(startedAt, running) {
  const [durationMs, setDurationMs] = useState(() => (
    running ? Math.max(0, Date.now() - Number(startedAt || Date.now())) : 0
  ));

  useEffect(() => {
    if (!running) return undefined;
    const update = () => setDurationMs(Math.max(0, Date.now() - Number(startedAt || Date.now())));
    update();
    const intervalId = window.setInterval(update, 1000);
    return () => window.clearInterval(intervalId);
  }, [running, startedAt]);

  return durationMs;
}

export function formatAssistantDuration(durationMs) {
  const milliseconds = Math.max(0, Number(durationMs) || 0);
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function formatAssistantTurnTiming(completedAt, durationMs) {
  const date = new Date(completedAt);
  const completed = Number.isNaN(date.getTime())
    ? "Completed"
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const milliseconds = Math.max(0, Number(durationMs) || 0);
  const seconds = Math.round(milliseconds / 1000);
  const duration = milliseconds < 1000
    ? "<1s"
    : formatAssistantDuration(seconds * 1000);
  return `${completed} · Ran for ${duration}`;
}

export function MarkdownMessage({ compact = false, inverse = false, onOpenLink, sourcePath, value }) {
  return (
    <MarkdownContent
      compact={compact}
      inverse={inverse}
      value={value}
      onOpenRelativeLink={onOpenLink}
      sourcePath={sourcePath}
    />
  );
}

const ThoughtGroup = memo(function ThoughtGroup({ groupId, expanded, onOpenFile, onOpenLink, onToggle, sourcePath, thoughts, searchMessageId }) {
  const trace = useMemo(() => buildThoughtTrace(thoughts), [thoughts]);
  const count = trace.length;
  const match = thoughts.find(thought => String(thought.id) === String(searchMessageId));
  const matchKey = match?.trace?.id ? `trace-${match.trace.id}` : match?.id;

  if (!count) return null;

  return (
    <div className="flex justify-start" data-message-id={thoughts[0]?.groupAnchorId}>
      <div className="w-full min-w-0 text-sm text-slate-700 dark:text-[#d4d4d4]">
        <button
          className="flex w-full items-center justify-between gap-3 rounded-md px-1 py-1.5 text-left transition hover:text-ink"
          type="button"
          onClick={() => onToggle(groupId)}
        >
          <span className="min-w-0 text-xs font-semibold text-ink">
            {expanded ? "Hide thoughts" : "Show thoughts"}
            <span className="ml-1.5 font-normal text-muted">{count}</span>
          </span>
          <span className="grid h-6 w-6 place-items-center rounded-md text-muted transition">
            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </span>
        </button>
        <div
          className={`grid transition-all duration-200 ease-out ${
            expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}
        >
          <div className="overflow-hidden">
            <div className="px-1 pb-1 pt-2">
              {expanded && trace.map((entry, index) => (
                <div
                  key={entry.key}
                  data-history-anchor={`${thoughts[0]?.groupId || ""}:${entry.key}`}
                  data-thread-search-match={entry.key === matchKey ? "true" : undefined}
                  tabIndex={entry.key === matchKey ? -1 : undefined}
                  className={`relative ml-1 border-l border-line pl-5 ${
                    index === trace.length - 1 ? "pb-1" : "pb-4"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`absolute -left-[4.5px] top-1 h-2 w-2 rounded-full border border-white dark:border-[#18181a] ${
                      entry.kind === "tool" && entry.status !== "error"
                        ? "bg-emerald-500"
                        : entry.status === "error"
                        ? "bg-red-500"
                        : "bg-slate-400"
                    }`}
                  />
                  {shellTraceDetails(entry) ? (
                    <ShellCommandDisclosure entry={entry} initiallyExpanded={entry.key === matchKey} />
                  ) : editTraceDetails(entry) ? (
                    <EditDisclosure entry={entry} onOpenFile={onOpenFile} initiallyExpanded={entry.key === matchKey} />
                  ) : entry.kind === "tool" && (entry.input || entry.output) ? (
                    <ToolTraceDisclosure entry={entry} initiallyExpanded={entry.key === matchKey} />
                  ) : entry.kind === "tool" || !entry.body ? (
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-xs leading-5">
                      {entry.title ? (
                        <span className="font-semibold text-ink">{entry.title}</span>
                      ) : null}
                      {entry.detail ? (
                        <span className="min-w-0 break-words text-muted">{entry.detail}</span>
                      ) : null}
                    </div>
                  ) : null}
                  {entry.kind === "summary" && entry.body ? (
                    <div className="text-xs leading-5 text-slate-600 dark:text-[#b9b9b9]">
                      <MarkdownMessage compact sourcePath={sourcePath} onOpenLink={onOpenLink} value={entry.body} />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});

function ToolTraceDisclosure({ entry, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const detail = toolTraceDisclosureDetail(entry);
  const isSearch = entry.category === "search"
    || String(entry.title || "").trim().toLowerCase().includes("search");

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group -ml-1 flex min-h-7 w-[calc(100%+0.25rem)] items-center gap-2 rounded-md px-1 text-left text-xs outline-none transition-colors hover:bg-slate-100/70 focus-visible:ring-2 focus-visible:ring-brand/30 dark:hover:bg-[#242426]"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="shrink-0 font-semibold text-ink group-hover:text-brand">
          {entry.title || "Tool"}
        </span>
        {detail && (!isSearch || expanded) ? (
          <span className="min-w-0 flex-1 truncate text-muted" title={detail}>{detail}</span>
        ) : (
          <span className="flex-1" />
        )}
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 group-hover:text-ink ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            <div className="mt-1 overflow-hidden rounded-md border border-line bg-white dark:bg-[#181818]">
              {entry.input ? <TracePayload label="In" value={entry.input} /> : null}
              {entry.output ? <TracePayload label="Out" value={entry.output} divided={Boolean(entry.input)} /> : null}
            </div>
          </> : null}
        </div>
      </div>
    </div>
  );
}

export function toolTraceDisclosureDetail(entry) {
  const detail = String(entry?.detail || "").trim();
  if (detail) return detail;

  const title = String(entry?.title || "").trim().toLowerCase();
  if (entry?.category !== "search" && !title.includes("search")) return "";

  const input = String(entry?.input || "").trim();
  if (!input) return "";
  try {
    const parsed = JSON.parse(input);
    const query = parsed?.query
      ?? parsed?.q
      ?? parsed?.search_query?.[0]?.q
      ?? parsed?.image_query?.[0]?.q;
    return typeof query === "string" ? query.trim() : "";
  } catch {
    return input.includes("\n") ? "" : input;
  }
}

function EditDisclosure({ entry, onOpenFile, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const details = editTraceDetails(entry);
  if (!details) return null;

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group flex min-h-7 max-w-full items-center gap-1 rounded-md pr-1 text-left text-xs font-semibold text-ink outline-none transition hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/30"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="truncate">Editing files</span>
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            {details.path ? (
              <button
                aria-label={`Open ${details.path} in code editor`}
                className="mt-0.5 block w-full min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-left text-[10px] text-muted outline-none transition hover:text-brand focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-brand/30"
                style={{ direction: "rtl" }}
                title={details.path}
                type="button"
                onClick={() => onOpenFile?.(details.path)}
              >
                {details.path}
              </button>
            ) : null}
            <pre className="workflow-scrollbar mt-1 max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-2.5 py-2 font-mono text-[10px] leading-4 text-slate-600 dark:bg-[#111113] dark:text-[#b9b9b9]">
              {details.input || "Waiting for edit details..."}
            </pre>
          </> : null}
        </div>
      </div>
    </div>
  );
}

export function editTraceDetails(entry) {
  if (!entry || entry.kind !== "tool" || String(entry.title || "").trim().toLowerCase() !== "edit") {
    return null;
  }
  let path = String(entry.detail || "").trim();
  const input = String(entry.input || "").trim();
  if (!path && input) {
    try {
      const parsed = JSON.parse(input);
      path = String(parsed?.path || parsed?.file_path || "").trim();
    } catch {
      path = "";
    }
  }
  return { input, path };
}

function ShellCommandDisclosure({ entry, initiallyExpanded = false }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const details = shellTraceDetails(entry);
  if (!details) return null;

  return (
    <div className="min-w-0">
      <button
        aria-expanded={expanded}
        className="group flex min-h-7 max-w-full items-center gap-1 rounded-md pr-1 text-left text-xs font-semibold text-ink outline-none transition hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/30"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="truncate">Running {details.shell} commands</span>
        <ChevronRight
          aria-hidden="true"
          className={`shrink-0 text-muted transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
          size={14}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-150 ease-out ${
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          {expanded ? <>
            <pre className="workflow-scrollbar mt-1 max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-slate-50 px-2.5 py-2 font-mono text-[10px] leading-4 text-slate-600 dark:bg-[#111113] dark:text-[#b9b9b9]">
              {details.command || "Waiting for command..."}
            </pre>
          </> : null}
        </div>
      </div>
    </div>
  );
}


export function shellTraceDetails(entry) {
  if (!entry || entry.kind !== "tool") return null;
  const title = String(entry.title || "").trim();
  const normalizedTitle = title.toLowerCase();
  const shellTitles = new Map([
    ["bash", "bash"],
    ["sh", "sh"],
    ["zsh", "zsh"],
    ["fish", "fish"],
    ["powershell", "PowerShell"],
    ["pwsh", "PowerShell"],
    ["cmd", "Command Prompt"],
    ["command prompt", "Command Prompt"],
    ["shell", "shell"],
    ["terminal", "terminal"],
  ]);
  if (entry.category !== "shell" && !shellTitles.has(normalizedTitle)) return null;

  let command = String(entry.command || "").trim();
  if (!command && entry.input) {
    try {
      const parsed = JSON.parse(entry.input);
      command = String(parsed?.command || parsed?.cmd || parsed?.script || "").trim();
    } catch {
      command = String(entry.input).trim();
    }
  }
  return {
    command,
    shell: String(entry.shell || shellTitles.get(normalizedTitle) || "shell"),
  };
}

function TracePayload({ divided = false, label, value }) {
  return (
    <div className={`grid grid-cols-[2rem_minmax(0,1fr)] gap-2 px-2.5 py-2 ${divided ? "border-t border-line" : ""}`}>
      <span className="pt-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <pre className="workflow-scrollbar max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-4 text-slate-600 dark:text-[#b9b9b9]">
        {value}
      </pre>
    </div>
  );
}

const chatThreadsStorageKey = "gofer-flow-chat-threads";

export function bumpChatThread(threads, threadId, now = new Date().toISOString()) {
  const thread = threads.find((item) => item.id === threadId);
  return thread ? [{ ...thread, updatedAt: now }, ...threads.filter((item) => item.id !== threadId)] : threads;
}

function chatThreadMetadataKey(id) { return `gofer-flow-chat-thread-meta:${id}`; }

export function chatThreadIndex() {
  try {
    const index = JSON.parse(window.localStorage.getItem(chatThreadsStorageKey) || "[]");
    if (!Array.isArray(index)) return [];
    // One-time migration. Subsequent starts read metadata for only the first page.
    const valid = index.filter((entry) => typeof entry?.id === "string");
    if (valid.some((entry) => typeof entry.title === "string")) {
      for (const entry of valid) {
        if (typeof entry.title === "string") window.localStorage.setItem(chatThreadMetadataKey(entry.id), JSON.stringify(entry));
      }
      const migrated = valid.map(threadIndexEntry);
      migrated.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
      window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(migrated));
      return migrated;
    }
    if (valid.some(entry => entry.scopeIndexed !== true)) {
      // Already idle entries need no scope lookup until the user opens them.
      const migrated = valid.map(entry => entry.scopeIndexed ? entry
        : threadIndexEntry(threadIsArchived(entry) ? entry : loadChatThread(entry.id) || entry));
      window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(migrated));
      return migrated;
    }
    return valid;
  } catch { return []; }
}

function threadIndexEntry(thread) {
  return { id: thread.id, updatedAt: thread.updatedAt, projectRoot: thread.projectRoot || "",
    projectBranch: thread.projectBranch, archived: Boolean(thread.archived), pinned: Boolean(thread.pinned), scopeIndexed: true };
}

export function loadChatThread(id) {
  try {
    const thread = JSON.parse(window.localStorage.getItem(chatThreadMetadataKey(id)) || "null");
    return thread?.id === id && typeof thread.title === "string" ? thread : null;
  } catch { return null; }
}

function loadAllChatThreads() { return loadChatThreads(Infinity); }

export function loadChatThreads(limit = 15) {
  return chatThreadIndex().slice(0, limit).map(({ id }) => loadChatThread(id)).filter(Boolean);
}

export function persistChatThreads(threads) {
  const loadedIds = new Set(threads.map((thread) => thread.id));
  const index = new Map([
    ...threads.map(thread => [thread.id, threadIndexEntry(thread)]),
    ...chatThreadIndex().filter((entry) => !loadedIds.has(entry.id)).map((entry) => [entry.id, entry]),
  ]);
  for (const thread of threads) {
    const key = chatThreadMetadataKey(thread.id);
    const encoded = JSON.stringify(thread);
    if (window.localStorage.getItem(key) !== encoded) {
      window.localStorage.setItem(key, encoded);
      archiveThreadFromStorage(thread.id, () => conversationRepository().all(thread.id));
    }
    index.set(thread.id, threadIndexEntry(thread));
  }
  const sorted = [...index.values()].sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
  window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(sorted));
}

function deleteStoredChatThread(id) {
  const index = chatThreadIndex().filter((entry) => entry.id !== id);
  window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(index));
  window.localStorage.removeItem(chatThreadMetadataKey(id));
}

export function threadTitleFromMessage(message) {
  const words = message.trim().split(/\s+/).slice(0, 8);
  const title = words.join(" ");
  return title.length < message.trim().length ? `${title}...` : title || "New thread";
}

function formatThreadDate(value) {
  if (!value) return "No messages yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No messages yet";
  return date.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function reportArchiveError(error) {
  window.dispatchEvent?.(new CustomEvent("gofer:archive-error", { detail: error?.message || String(error) }));
}
const scheduleConversationArchive = createConversationArchiveScheduler({ reportError: reportArchiveError });
function archiveThreadFromStorage(threadId, messages, deleted = false) {
  const bridge = window.goferDesktop?.rem;
  if (!bridge?.archive) return Promise.resolve(true);
  const storage = window.localStorage;
  return scheduleConversationArchive(threadId, async () => {
    const thread = JSON.parse(storage.getItem(chatThreadMetadataKey(threadId)) || "null") || { id: threadId };
    return bridge.archive(thread, typeof messages === "function" ? await messages() : messages, deleted);
  }, { deleted });
}

async function archiveAllConversations() {
  for (const thread of chatThreadIndex()) await archiveThreadFromStorage(thread.id, () => conversationRepository().all(thread.id));
}

export function chatStorageKeyFor(threadId) {
  return `gofer-flow-chat-thread:${threadId}`;
}

function loadChatMessages(storageKey) {
  return loadConversationMessages(storageKey);
}

export function buildChatItems(messages) {
  const items = [];
  let index = 0;

  while (index < messages.length) {
    const message = messages[index];
    if (message.kind === "memory" || message.kind === "continuation-context" ||
      (message.role === "system" && String(message.body || "").startsWith("The previous process was interrupted."))) {
      index += 1;
      continue;
    }
    if (message.kind !== "thought") {
      items.push({ type: "message", message });
      index += 1;
      continue;
    }

    const groupId = message.groupId || `legacy-${message.id}`;
    const thoughts = [];
    const segmentId = `thought-group-${groupId}-${message.id}`;
    while (
      index < messages.length &&
      messages[index].kind === "thought" &&
      (messages[index].groupId || `legacy-${messages[index].id}`) === groupId
    ) {
      thoughts.push({
        ...messages[index],
        groupAnchorId: segmentId,
      });
      index += 1;
    }
    const nextMessage = messages[index];
    while (isDuplicateOutputThought(thoughts.at(-1), nextMessage)) thoughts.pop();
    if (thoughts.length) {
      items.push({
        id: segmentId,
        type: "thought-group",
        thoughts,
      });
    }
  }

  return items;
}

export function removeTrailingDuplicateOutputThought(messages, finalBody, groupId) {
  const finalMessage = { role: "assistant", kind: "final", body: finalBody };
  return messages.filter((message) => {
    if (message.groupId !== groupId || message.kind !== "thought") return true;
    if (isDuplicateOutputThought(message, finalMessage)) return false;
    const body = message.trace?.body ?? message.body;
    return message.trace?.kind === "tool" || !isProviderMetadataSummary(body);
  });
}

function isDuplicateOutputThought(thought, finalMessage) {
  if (!thought || thought.kind !== "thought" || finalMessage?.role !== "assistant") return false;
  if (finalMessage.kind === "thought" || finalMessage.kind === "memory") return false;
  if (thought.trace?.kind === "tool") return false;
  const thoughtBody = normalizeMarkdownText(thought.trace?.body ?? thought.body);
  const finalBody = normalizeMarkdownText(finalMessage.body);
  if (!thoughtBody || !finalBody) return false;
  if (thoughtBody === finalBody) return true;
  const untruncatedThought = thoughtBody.replace(/(?:\.{3}|…)$/, "").trim();
  return untruncatedThought.length >= 48 && finalBody.startsWith(untruncatedThought);
}

export function buildThoughtTrace(thoughts) {
  const entries = [];
  const traceIndexes = new Map();

  for (const thought of thoughts) {
    const hasStructuredTrace = Boolean(thought?.trace && typeof thought.trace === "object");
    const rawTrace = hasStructuredTrace
      ? thought.trace
      : { kind: "summary", title: "Thought", body: thought?.body };
    const kind = rawTrace.kind === "tool" ? "tool" : "summary";
    const traceId = rawTrace.id ? String(rawTrace.id) : "";
    const entry = {
      key: traceId ? `trace-${traceId}` : thought?.id || `trace-${entries.length}`,
      id: traceId,
      kind,
      title: kind === "summary"
        ? String(rawTrace.title || "")
        : String(rawTrace.title || "Tool"),
      detail: rawTrace.detail ? String(rawTrace.detail) : "",
      body: rawTrace.body
        ? String(rawTrace.body)
        : kind === "summary" && !hasStructuredTrace
        ? String(thought?.body || "")
        : "",
      input: rawTrace.input ? String(rawTrace.input) : "",
      output: rawTrace.output ? String(rawTrace.output) : "",
      category: rawTrace.category ? String(rawTrace.category) : "",
      shell: rawTrace.shell ? String(rawTrace.shell) : "",
      command: rawTrace.command ? String(rawTrace.command) : "",
      status: String(rawTrace.status || ""),
    };

    if (kind === "summary" && isProviderMetadataSummary(entry.body)) continue;

    if (!traceId || !traceIndexes.has(traceId)) {
      if (traceId) traceIndexes.set(traceId, entries.length);
      entries.push(entry);
      continue;
    }

    const existingIndex = traceIndexes.get(traceId);
    const existing = entries[existingIndex];
    entries[existingIndex] = {
      ...existing,
      title: kind === "summary"
        ? entry.title || existing.title
        : existing.title === "Tool result"
        ? entry.title
        : existing.title,
      detail: entry.detail || existing.detail,
      body: entry.body || existing.body,
      input: existing.input || entry.input,
      output: entry.output || existing.output,
      category: entry.category || existing.category,
      shell: entry.shell || existing.shell,
      command: entry.command || existing.command,
      status: entry.status || existing.status,
    };
  }

  return entries;
}

function isProviderMetadataSummary(value) {
  const compact = String(value ?? "").trim().replace(/\s+/g, " ");
  return /^(?:tokens? used\s*:?[\s]*)[\d,]+$/i.test(compact) ||
    /^[\d,]+\s+tokens? used$/i.test(compact);
}

export function normalizeMarkdownText(value) {
  return String(value ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/```[^\n]*\n?/g, "")
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)/gm, "")
    .replace(/\[([^\]]+)\]\([^\s)]+\)/g, "$1")
    .replace(/[*_~`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function parseChatStreamEvent(line) {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function uniqueClientId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatPreviewValue(value) {
  if (value && typeof value === "object") {
    if (typeof value.path === "string") return value.path;
    if (typeof value.name === "string") return value.name;
    return JSON.stringify(value);
  }
  return String(value);
}

function formatFanOutCount(fanOut) {
  if (!fanOut || fanOut.count == null) return "unknown";
  if (fanOut.countExact === false) {
    return `at least ${fanOut.countLowerBound ?? fanOut.count}`;
  }
  return String(fanOut.count);
}

function formatTriggerContextItems(triggerContext) {
  if (!triggerContext || typeof triggerContext !== "object") return [];
  const items = [];
  if (triggerContext.schedule) {
    const schedule = triggerContext.schedule;
    items.push(
      `Schedule: ${schedule.cron_expression ?? schedule.cron ?? "configured"} timezone=${schedule.timezone ?? "local"}`,
    );
  }
  if (triggerContext.watch) {
    const watch = triggerContext.watch;
    items.push(
      `Watch: ${watch.path ?? ""} glob=${watch.glob ?? "*"} mode=${watch.mode ?? "batch"}`,
    );
  }
  if (triggerContext.runContinuously) {
    items.push("Run continuously: enabled");
  }
  if (triggerContext.provided) {
    items.push(`Provided trigger context: ${JSON.stringify(triggerContext.provided)}`);
  }
  return items;
}

function buildRunPreviewTriggerContext(workflow) {
  const triggerContext = {};
  if (workflow.schedule) {
    triggerContext.schedule = workflow.schedule;
  }
  if (workflow.watch) {
    triggerContext.watch = workflow.watch;
  }
  if (workflow.runContinuously) {
    triggerContext.runContinuously = true;
  }
  return triggerContext;
}

function initialWorkflowParameters(workflow) {
  const values = {};
  for (const [name, spec] of Object.entries(workflow.inputs ?? workflow.parameters ?? {})) {
    if (spec.default !== undefined && spec.default !== null) {
      values[name] = spec.default;
    } else if (spec.type === "boolean") {
      values[name] = false;
    } else {
      values[name] = "";
    }
  }
  return values;
}

function validateWorkflowParameters(workflow, values) {
  const errors = {};
  for (const [name, spec] of Object.entries(workflow.inputs ?? workflow.parameters ?? {})) {
    const value = values[name];
    if (spec.required && (value === undefined || value === null || value === "")) {
      errors[name] = "Required";
      continue;
    }
    if (value === undefined || value === null || value === "") continue;
    if (spec.type === "number" && Number.isNaN(Number(value))) {
      errors[name] = "Enter a number";
    }
    if (spec.type === "enum" && Array.isArray(spec.choices) && !spec.choices.includes(value)) {
      errors[name] = "Choose a valid option";
    }
  }
  return errors;
}

export function RunPreviewDialog({
  plan,
  workflow,
  onCancel,
  onRun,
  initialParameters = {},
  executionMode = "local",
  onExecutionModeChange = () => {},
  queueState = { runners: [] },
}) {
  const parameterSchema = workflow.inputs ?? workflow.parameters ?? {};
  const [parameters, setParameters] = useState(() => ({
    ...initialWorkflowParameters(workflow),
    ...initialParameters,
  }));
  const [parameterErrors, setParameterErrors] = useState({});
  const warnings = plan?.warnings ?? [];
  const blockingDiagnostics = plan?.blockingDiagnostics ?? [];
  const destructiveActions = plan?.destructiveActions ?? [];
  const providers = plan?.providerRequirements ?? [];
  const requiredSecrets = plan?.requiredSecrets ?? [];
  const bindings = plan?.bindings ?? [];
  const triggerItems = formatTriggerContextItems(plan?.triggerContext);
  const generations = plan?.generations ?? [];
  const filesystemAccess = workflow.filesystemAccess ?? [];

  return (
    <Dialog
      description={`Review execution details for ${workflow.name}`}
      onClose={onCancel}
      panelClassName="flex max-h-[86vh] w-full max-w-[760px] flex-col rounded-lg border border-line bg-white shadow-panel"
      title={`Run preview: ${workflow.name}`}
    >
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">Run preview: {workflow.name}</h2>
            <p className="text-xs text-muted">{workflow.id}</p>
          </div>
          <button
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="Close"
            type="button"
            onClick={onCancel}
          >
            <X size={17} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-auto px-5 py-4">
          {blockingDiagnostics.length > 0 ? (
            <PreviewSection title="Cannot run" tone="danger" items={blockingDiagnostics} />
          ) : null}
          {destructiveActions.length > 0 ? (
            <PreviewSection title="Destructive actions" tone="danger" items={destructiveActions} />
          ) : null}
          {warnings.length > 0 ? (
            <PreviewSection title="Warnings" tone="warning" items={warnings} />
          ) : null}
          {requiredSecrets.length > 0 ? (
            <PreviewSection title="Required secrets" items={requiredSecrets} />
          ) : null}
          {triggerItems.length > 0 ? (
            <PreviewSection title="Trigger context" items={triggerItems} />
          ) : null}
          {filesystemAccess.length > 0 ? (
            <PreviewSection
              title="Filesystem access"
              items={normalizeWorkflowFilesystemAccess(filesystemAccess).map((entry) => {
                const permissions = [
                  entry.read ? "read" : null,
                  entry.write ? "write" : null,
                  entry.execute ? "execute" : null,
                ].filter(Boolean);
                return `${entry.path}: ${permissions.length > 0 ? permissions.join(", ") : "no access"}`;
              })}
            />
          ) : null}
          {Object.keys(parameterSchema).length > 0 ? (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
                Run inputs
              </h3>
              <div className="space-y-3 rounded-lg border border-line bg-slate-50 p-3">
                {Object.entries(parameterSchema).map(([name, spec]) => (
                  <RunParameterField
                    key={name}
                    name={name}
                    spec={spec}
                    value={parameters[name]}
                    error={parameterErrors[name]}
                    onChange={(value) =>
                      setParameters((current) => ({ ...current, [name]: value }))
                    }
                  />
                ))}
              </div>
            </section>
          ) : null}
          {providers.length > 0 ? (
            <PreviewSection
              title="Provider CLI requirements"
              items={providers.map((provider) => {
                const profile = provider.profile ? ` profile=${provider.profile}` : "";
                const model = provider.model ? ` model=${provider.model}` : "";
                const timeout =
                  provider.timeout !== undefined && provider.timeout !== null
                    ? ` timeout=${provider.timeout}s`
                    : "";
                const extraPaths = provider.extraPaths?.length
                  ? ` extra_paths=${provider.extraPaths.join(", ")}`
                  : "";
                const binary = provider.binary ?? "unknown";
                const availability = provider.available ? "available" : "missing";
                return `${provider.agentId}: ${provider.subscription} binary=${binary} (${availability}) cwd=${provider.workingDir}${profile}${model}${timeout}${extraPaths}`;
              })}
            />
          ) : null}
          {bindings.length > 0 ? <BindingPreviewSection bindings={bindings} /> : null}

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
              Execution target
            </h3>
            <div className="inline-flex rounded-lg border border-line bg-slate-50 p-1">
              <button
                className={`rounded-md px-3 py-1.5 text-sm ${
                  executionMode === "local" ? "bg-white font-semibold shadow-sm" : "text-muted"
                }`}
                type="button"
                onClick={() => onExecutionModeChange("local")}
              >
                Local
              </button>
              <button
                className={`rounded-md px-3 py-1.5 text-sm ${
                  executionMode === "remote" ? "bg-white font-semibold shadow-sm" : "text-muted"
                }`}
                type="button"
                onClick={() => onExecutionModeChange("remote")}
              >
                Remote
              </button>
            </div>
            {executionMode === "remote" ? (
              <p className="mt-2 text-xs text-muted">
                {(queueState.runners ?? []).length
                  ? `${queueState.runners.length} runner${queueState.runners.length === 1 ? "" : "s"} registered`
                  : "No runners registered yet"}
              </p>
            ) : null}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase text-muted">
              Execution order
            </h3>
            <div className="space-y-2">
              {generations.map((generation) => (
                <details
                  key={generation.index}
                  className="rounded-lg border border-line bg-slate-50 px-3 py-2"
                  open={generation.index === 0}
                >
                  <summary className="cursor-pointer text-sm font-semibold">
                    Generation {generation.index} · {(generation.nodes ?? []).length} node
                    {(generation.nodes ?? []).length === 1 ? "" : "s"}
                  </summary>
                  <div className="mt-2 space-y-2">
                    {(generation.nodes ?? []).map((node) => (
                      <div
                        key={node.id}
                        className="rounded-md border border-line bg-white px-3 py-2 text-sm"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="font-semibold">{node.id}</p>
                            <p className="break-words text-xs text-muted">{node.detail}</p>
                          </div>
                          <span className="shrink-0 rounded-md bg-slate-100 px-2 py-1 text-xs text-muted">
                            {node.type}
                          </span>
                        </div>
                        {(node.sideEffects ?? []).length > 0 ? (
                          <p className="mt-2 text-xs text-slate-600">
                            {(node.sideEffects ?? []).join("; ")}
                          </p>
                        ) : null}
                        {node.workingDir ? (
                          <p className="mt-2 break-words text-xs text-slate-600">
                            Working directory: {node.workingDir}
                          </p>
                        ) : null}
                        {node.fanOut ? (
                          <div className="mt-2 text-xs text-slate-600">
                            <p>
                              Fan-out {node.fanOut.sourceType}:{" "}
                              {formatFanOutCount(node.fanOut)} item
                              {node.fanOut.count === 1 && node.fanOut.countExact !== false
                                ? ""
                                : "s"}
                            </p>
                            {(node.fanOut.sampleItems ?? []).length > 0 ? (
                              <ul className="mt-1 space-y-0.5">
                                {(node.fanOut.sampleItems ?? []).map((sample, index) => (
                                  <li key={`${node.id}-sample-${index}`}>
                                    Sample: {formatPreviewValue(sample)}
                                  </li>
                                ))}
                              </ul>
                            ) : null}
                          </div>
                        ) : null}
                        {(node.bindings ?? []).length > 0 ? (
                          <div className="mt-2 text-xs text-slate-600">
                            <p className="font-medium text-ink">Runtime bindings</p>
                            <ul className="mt-1 space-y-1">
                              {(node.bindings ?? []).map((binding) => (
                                <li key={binding.id} className="break-words">
                                  <span className="font-medium">{binding.destinationField}</span>
                                  {" ← "}
                                  <code>{binding.expression}</code>
                                  {` · ${binding.status} · ${binding.resolutionPhase}`}
                                  {binding.readiness ? ` · secret ${binding.readiness}` : ""}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          </section>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <button className="btn-ghost" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn-primary inline-flex items-center justify-center gap-2 whitespace-nowrap"
            disabled={plan?.runnable === false}
            title={plan?.runnable === false ? "Resolve the blocking preflight errors first" : "Run workflow"}
            type="button"
            onClick={() => {
              const errors = validateWorkflowParameters(workflow, parameters);
              setParameterErrors(errors);
              if (Object.keys(errors).length === 0) {
                onRun(parameters);
              }
            }}
          >
            Run workflow
          </button>
        </div>
    </Dialog>
  );
}

function BindingPreviewSection({ bindings }) {
  const hasShellConsumer = bindings.some((binding) =>
    ["shell", "process-or-shell"].includes(binding.consumer),
  );
  return (
    <section>
      <div className="mb-2">
        <h3 className="text-xs font-semibold uppercase text-muted">Runtime bindings</h3>
        <p className="mt-1 text-xs text-slate-600">
          Deferred values resolve automatically at the phase shown below.
          {hasShellConsumer
            ? " Raticode resolves {{...}} first; the shell owns expressions such as ${FILE_NAME}."
            : ""}
        </p>
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-slate-50">
        <ul className="divide-y divide-slate-200">
          {bindings.map((binding) => {
            const isError = ["invalid", "type-incompatible"].includes(binding.status);
            return (
              <li key={binding.id} className="px-3 py-2.5 text-xs">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <p className="min-w-0 break-words text-sm font-medium text-ink">
                    {binding.destinationNode}.{binding.destinationField}
                  </p>
                  <span className={isError ? "font-semibold text-red-700" : "font-medium text-cyan-700"}>
                    {binding.status}
                  </span>
                </div>
                <p className="mt-1 break-words text-slate-600">
                  <code>{binding.expression}</code>
                  {` from ${binding.producer} · ${binding.sourceType} → ${binding.destinationType} · ${binding.resolutionPhase}`}
                  {binding.coercion === "string" ? " · string coercion" : ""}
                  {binding.readiness ? ` · secret ${binding.readiness}` : ""}
                </p>
                {binding.message ? <p className="mt-1 text-red-700">{binding.message}</p> : null}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

function RunParameterField({ error, name, onChange, spec, value }) {
  const id = `run-param-${name}`;
  const label = spec.label || name;
  const commonClass =
    "mt-1 w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-teal-500";
  const inputType =
    spec.type === "number"
      ? "number"
      : spec.type === "date"
        ? "date"
        : spec.type === "time"
          ? "time"
          : spec.type === "datetime"
            ? "datetime-local"
            : spec.type === "secret"
              ? "password"
              : "text";
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="font-medium">
        {label}
        {spec.required ? <span className="text-rose-600"> *</span> : null}
      </span>
      {spec.description ? (
        <span className="mt-0.5 block text-xs text-muted">{spec.description}</span>
      ) : null}
      {spec.type === "boolean" ? (
        <input
          id={id}
          className="mt-2 h-4 w-4 rounded border-line"
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
      ) : spec.type === "enum" ? (
        <select
          id={id}
          className={commonClass}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Select...</option>
          {(spec.choices ?? []).map((choice) => (
            <option key={String(choice)} value={choice}>
              {String(choice)}
            </option>
          ))}
        </select>
      ) : spec.type === "text" || spec.type === "multiline" ? (
        <textarea
          id={id}
          className={`${commonClass} min-h-24 resize-y`}
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          id={id}
          className={commonClass}
          type={inputType}
          value={value ?? ""}
          min={spec.min}
          max={spec.max}
          pattern={spec.pattern}
          onChange={(event) => {
            const nextValue = spec.type === "number" ? event.target.value : event.target.value;
            onChange(nextValue);
          }}
        />
      )}
      {spec.type === "file" || spec.type === "folder" ? (
        <span className="mt-1 block text-xs text-muted">
          Enter a path accessible to the runner.
        </span>
      ) : null}
      {error ? <span className="mt-1 block text-xs text-rose-700">{error}</span> : null}
    </label>
  );
}

function PreviewSection({ title, items, tone = "default" }) {
  const toneClass =
    tone === "danger"
      ? "border-red-200 bg-red-50 text-red-800"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : "border-line bg-slate-50 text-slate-700";

  return (
    <section className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <h3 className="text-xs font-semibold uppercase">{title}</h3>
      <ul className="mt-2 space-y-1 text-sm">
        {items.map((item) => (
          <li key={item}>- {item}</li>
        ))}
      </ul>
    </section>
  );
}

export function CreateWorkflowDialog({
  defaultProjectRoot = "",
  error,
  open,
  saving,
  onClose,
  onCreate,
  onImport,
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState("create");
  const [projectRoot, setProjectRoot] = useState("");
  const [projectError, setProjectError] = useState("");
  const [pickingProject, setPickingProject] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importError, setImportError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const importInputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setName("");
      setMode("create");
      setProjectRoot(defaultProjectRoot);
      setProjectError("");
      setPickingProject(false);
      setImportFile(null);
      setImportError("");
      setDragActive(false);
    }
  }, [defaultProjectRoot, open]);

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
    });
  }

  function chooseImportFile(file) {
    if (!file) return;
    if (!isRaticodeFile(file)) {
      setImportFile(null);
      setImportError("Choose a .raticode bundle.");
      return;
    }
    setImportFile(file);
    setImportError("");
  }

  async function pickProjectFolder() {
    if (!window.goferDesktop?.workspace?.selectPath) {
      setProjectError("Native folder selection is available in the desktop app.");
      return;
    }
    setPickingProject(true);
    setProjectError("");
    try {
      const selected = await window.goferDesktop.workspace.selectPath({
        currentPath: projectRoot || defaultProjectRoot,
        directoryOnly: true,
      });
      if (selected) setProjectRoot(selected);
    } catch (selectionError) {
      setProjectError(
        selectionError instanceof Error
          ? selectionError.message
          : "Unable to open the project folder picker.",
      );
    } finally {
      setPickingProject(false);
    }
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
            <p className="text-xs text-muted">Create one from scratch or import a bundle</p>
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
              onClick={() => setMode("create")}
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
              onClick={() => {
                setMode("import");
                setProjectError("");
              }}
            >
              <Upload aria-hidden="true" size={15} />
              Import
            </button>
          </div>
          {mode === "create" ? (
            <label className="block">
              <span className="text-xs font-medium text-muted">Name</span>
              <input
                autoFocus
                className="mt-1 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none transition focus:border-indigo-500"
                disabled={saving}
                placeholder="Daily Analysis"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
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
          <div>
            <span className="text-xs font-medium text-muted">Project folder</span>
            <div className="mt-1 flex gap-2">
              <input
                aria-describedby={projectError ? "project-folder-error" : "project-folder-hint"}
                className="h-10 min-w-0 flex-1 rounded-lg border border-line px-3 font-mono text-xs outline-none transition focus:border-indigo-500"
                disabled={saving || pickingProject}
                placeholder="Choose a repository or project folder"
                value={projectRoot}
                onChange={(event) => {
                  setProjectRoot(event.target.value);
                  setProjectError("");
                }}
              />
              <button
                className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={saving || pickingProject}
                type="button"
                onClick={pickProjectFolder}
              >
                {pickingProject ? <Loader2 size={15} className="animate-spin" /> : <FolderOpen size={15} />}
                Browse
              </button>
            </div>
            {projectError ? (
              <p className="mt-1 text-xs text-rose-700" id="project-folder-error" role="alert">
                {projectError}
              </p>
            ) : (
              <p className="mt-1 text-xs text-muted" id="project-folder-hint">
                {mode === "create"
                  ? "Raticode will create .raticode/<workflow-id> inside this folder."
                  : "Raticode will add the imported workflow under .raticode."}
              </p>
            )}
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
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={saving || !projectRoot.trim() || (mode === "create" ? !name.trim() : !importFile)}
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

export function ExportWorkflowDialog({
  directory,
  error,
  open,
  saving,
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
              className="mt-1 flex h-10 w-full min-w-0 items-center gap-2 rounded-lg border border-line bg-white px-3 text-left text-sm outline-none transition hover:border-slate-300 focus-visible:border-teal-500 focus-visible:ring-2 focus-visible:ring-teal-100 disabled:cursor-not-allowed disabled:opacity-60"
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
              {workflowBundleFilename(workflow)}
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
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-3 text-sm font-medium text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
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

function EmptyWorkspace({
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
    if (!isRaticodeFile(file)) {
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

function WorkflowHealthPanel({ doctorState, workflow }) {
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
    <section
      className={`border-b px-5 py-3 ${
        errorCount ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50"
      }`}
    >
      <div className="flex items-start gap-3">
        <AlertCircle
          className={`mt-0.5 shrink-0 ${errorCount ? "text-red-600" : "text-amber-700"}`}
          size={17}
        />
        <div className="min-w-0 flex-1">
          <h2 className={`text-sm font-semibold ${errorCount ? "text-red-800" : "text-amber-900"}`}>
            {errorCount ? "Environment setup needs attention" : "Environment setup warnings"}
          </h2>
          <ul className={`mt-1 space-y-1 text-sm leading-5 ${errorCount ? "text-red-700" : "text-amber-800"}`}>
            {diagnostics.slice(0, 3).map((diagnostic, index) => (
              <li key={`${diagnostic.id}-${diagnostic.subject ?? "workflow"}-${index}`}>
                {diagnostic.message}
              </li>
            ))}
          </ul>
          {diagnostics.length > 3 ? (
            <p className={`mt-1 text-xs ${errorCount ? "text-red-700" : "text-amber-800"}`}>
              {diagnostics.length - 3} more issue{diagnostics.length === 4 ? "" : "s"} shown in workflow settings.
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className={`grid h-7 w-7 shrink-0 place-items-center rounded-md transition ${
            errorCount
              ? "text-red-700 hover:bg-red-100 hover:text-red-900"
              : "text-amber-800 hover:bg-amber-100 hover:text-amber-950"
          }`}
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

function agentExternalAccessWarnings(workflow) {
  return (workflow?.resourceWarnings ?? []).filter((warning) =>
    String(warning).includes("grants provider filesystem access outside working_dir"),
  );
}

function StatusDot({ status }) {
  const normalizedStatus = status || "Ready";
  const color = {
    Ready: "bg-emerald-500",
    Success: "bg-emerald-500",
    Error: "bg-red-500",
    Stopped: "bg-amber-500",
  }[normalizedStatus] ?? "bg-emerald-500";
  const running = normalizedStatus === "Running";

  return (
    <span
      className={`flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium ${
        running
          ? "border-blue-200 bg-blue-50 text-blue-700 dark:border-sky-700/70 dark:bg-sky-950/70 dark:text-sky-200"
          : "border-line bg-white text-slate-600"
      }`}
    >
      {running ? (
        <Loader2 size={11} className="animate-spin text-blue-600 dark:text-sky-300" />
      ) : (
        <span className={`h-1.5 w-1.5 rounded-full ${color}`} />
      )}
      {normalizedStatus}
    </span>
  );
}
