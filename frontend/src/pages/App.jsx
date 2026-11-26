import { hasEditingTarget, runFocusedEdit, rememberEditingTarget } from "../lib/focusedEditing.js";
import { EMPTY_GRAPH_HISTORY, graphEditLabel, pushGraphHistoryEntry } from "../lib/graphEditHistory.js";
import { textEditorSessions, flushCodeDrafts } from "../lib/codeEditorSessions.js";
import { flushChatDrafts } from "../lib/chatDrafts.js";
import { reportThemeContext, normalizeReportThemes } from "../lib/reportThemes.js";
import { applyPhoneSettings } from "../lib/deviceMessages.js";
import { phoneLockNotice } from "../lib/phoneLocks.js";
import { controlledEngine, webBrowserEngine } from "../lib/browserEngines.js";
import { browserTabOpening, viewedBrowserMedia, withBrowserTabEngines } from "../lib/browserTabs.js";
import { externalOpenPlan } from "../lib/externalOpens.js";
import { pathKey, samePath, pathWithin, pathValue, withPathValue, replacePathPrefix, pathMatchesChange } from "../lib/workspacePaths.js";
import { loadEditorSession, saveEditorSession, loadWorkflowDraft, saveWorkflowDraft } from "../lib/editorSession.js";
import brandCompat from "../lib/brandCompat.js";
import { startPolling, shareCancellable } from "../lib/refresh.js";
import { createRecentProjectValidator, startWorkspacePolling } from "../lib/projectRefresh.js";
import { trackPointerDrag } from "../lib/paneResize.js";
import { equalJson } from "../lib/jsonValue.js";
import { menuTrigger, PopupMenu } from "../components/PopupMenu.jsx";
import { browserChromeShortcutAction } from "../lib/browserShortcuts.js";
import { snapshotRemResources } from "../lib/remResources.js";
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Download,
  FileArchive,
  FolderOpen,
  Globe2,
  History,
  Loader2,
  Moon,
  MoreVertical,
  Plus,
  PencilLine,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldAlert,
  Sun,
  Upload,
  Waypoints,
  X,
} from "lucide-react";
import { autoLayoutWorkflow } from "../lib/workflowLayout.js";
import { adoptRemProjects, connectRemHome, onRemOpenedProjects, setRemHomeContext } from "../lib/remHomeStore.js";
import { setBackgroundNotices } from "../lib/backgroundNotices.js";
const DagCanvas = lazy(() => import("../components/DagCanvas.jsx"));
const RunPreviewDialog = lazy(() => import("../components/RunPreviewDialog.jsx"));
const ExportWorkflowDialog = lazy(() => import("../components/WorkflowDialogs.jsx").then(module => ({ default: module.ExportWorkflowDialog })));
const WorkflowListItem = lazy(() => import("../components/WorkflowListItem.jsx"));
import { Dialog } from "../components/Dialog.jsx";
import { useConfirm } from "../components/ConfirmDialog.jsx";
import SavedInputsPrompt from "../components/SavedInputsPrompt.jsx";
import { ConvertTomlWorkflowDialog, LegacyTomlNotice } from "../components/LegacyTomlWorkflows.jsx";
import ProjectFolderField from "../components/ProjectFolderField.jsx";
import { convertTomlWorkflow, legacyTomlFilesFromPayload } from "../lib/legacyToml.js";
import BrowserMediaBar from "../components/BrowserMediaBar.jsx";
const KanbanWorkspace = lazy(() => import("../components/KanbanWorkspace.jsx"));
const SocialPostsWorkspace = lazy(() => import("../components/SocialPostsWorkspace.jsx"));
const WorkflowRunsView = lazy(() => import("../components/WorkflowRunsView.jsx"));
const WorkflowShortcutDialog = lazy(() => import("../components/WorkflowShortcutDialog.jsx"));
const OrganizationHome = lazy(() => import("../components/OrganizationHome.jsx"));
const SecondBrainWorkspace = lazy(() => import("../components/SecondBrainWorkspace.jsx"));
const RemHomeWorkspace = lazy(() => import("../components/RemHomeWorkspace.jsx"));
const SwarmWorkspace = lazy(() => import("../components/SwarmWorkspace.jsx"));
const CodeFileExplorer = lazy(() => import("../components/CodeFileExplorer.jsx"));
import { workflowsForFile } from "../lib/fileInputs.js";
const CodeWorkspace = lazy(() => import("../components/CodeWorkspace.jsx"));
const BrowserWindowHost = lazy(() => import("../components/BrowserWindowHost.jsx"));
import { applyCodeFilesystemChange } from "../lib/codeEditorSessions.js";
import { resolveMarkdownFileLinkTarget, markdownFileLinkTarget } from "../lib/fileLinks.js";
const SettingsPopover = lazy(() => import("../components/SettingsPopover.jsx"));
const CommandPalette = lazy(() => import("../components/CommandPalette.jsx"));
import { commandSlotSettings, defaultSettingsSnapshot, normalizeAppSettings } from "../lib/settings.js";
import { SCREEN_COMMAND_ID, screenFromCapture, setScreenContextEnabled, setScreenStatus } from "../lib/screenContext.js";
import { ANYWHERE_COMMAND_ID, VOICE_COMMAND_ID, onVoicePhase, pickRemComposer, requestAnywhereDictation, requestVoiceDictation, setAnywhereStatus, setDictationAutoSend, setDictationMode, setVoiceBinding, stopAnywhereDictation, voiceShortcutLabel } from "../lib/voiceShortcut.js";
import { BROWSER_CONTENT, EDITOR_CONTENT, WORKFLOW_RUNS_CONTENT, initialMainContent, pluginAvailable, pluginEnabled, pluginHasPane, resolveMainContent, resolvePluginActivity, withPluginPreview } from "../lib/plugins.js";
import RaticodeMark from "../components/RaticodeMark.jsx";
import { createPortal } from "react-dom";
import { createRemHost, RemSlot } from "../components/RemSlot.jsx";
import UnifiedBottomPanel from "../components/UnifiedBottomPanel.jsx";
import { listedRattishWorkflows, useWorkflowRunRegistry } from "../lib/useWorkflowRunRegistry.js";
import { initialWorkflowParameters } from "../lib/workflowRunSetup.js";
import { exactWorkflowRunStopPath, workflowListStatus, workflowRunProblems, workflowRunSummary } from "../lib/workflowRuns.js";
import { askRemAboutDiagnostic, askRemAboutRunFailure, shownRunId } from "../lib/askRemAboutFailure.js";
import { pendingRunWorktreeCount, runWorktreeDeleteNote, workflowFolderLabel } from "../lib/workflowFolder.js";
import { GIT_FOR_WINDOWS_URL, starterHint, starterOptionLabel } from "../lib/workflowStarters.js";
import { requestShortcutReview } from "../lib/workflowShortcuts.js";
import { WORKFLOW_RUNS_EVENT, openWorkflowRuns, reviewRecord } from "../lib/allWorkflowRuns.js";
import { PALETTE_COMMAND_ID, QUICK_OPEN_COMMAND_ID, actionPaletteItems, keybindingPaletteItems, loadRecentIds, paletteGivesWay, quickOpenItems, rememberRecentId } from "../lib/commandPalette.js";
import { PAUSED_TEXT, triggersApi } from "../lib/rattishTriggers.js";
import { apiUrl } from "../lib/api.js";
import { useRovingChoice } from "../lib/useRovingChoice.js";
import { ChatPane } from "../components/ChatPane.jsx";
import { archiveAllConversations, chatThreadIndex, deletePhoneThread, loadAllChatThreads, loadChatThread, reportArchiveError } from "../lib/chatThreads.js";
import { assistantMarkdownSourcePath, loadProjectLabels, mergeRecentProjects, PROJECT_LABELS_STORAGE_KEY, projectNameFromPath } from "../lib/projects.js";
import {
  DEFAULT_APP_SETTINGS,
  followRenamedAccount,
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

export function workflowBundleFilename(workflow) {
  return `${workflow?.id || "workflow"}.raticode`;
}

export function workflowBundlePath(directory, workflow) {
  const value = String(directory ?? "").trim();
  if (!value) return workflowBundleFilename(workflow);
  const separator = value.includes("\\") && !value.includes("/") ? "\\" : "/";
  const trimmedDirectory = value.replace(/[\\/]+$/, "");
  return `${trimmedDirectory || separator}${trimmedDirectory ? separator : ""}${workflowBundleFilename(workflow)}`;
}

export function workflowExportEndpoint(workflow) {
  return `/rattish/workflows/${encodeURIComponent(workflow.id)}/export`;
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
  const canonicalRoot = typeof trustedRoot === "string" && trustedRoot ? trustedRoot : normalizedRoot;
  const payload = await shareCancellable(`project-discovery:${pathKey(canonicalRoot)}`, async sharedSignal => {
    const projectGrantId = window.goferDesktop?.workspace?.pathGrantForApi?.(canonicalRoot) ?? "";
    const response = await fetch(apiUrl("/projects/open"), {
      signal: sharedSignal,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectGrantId: projectGrantId || undefined, projectRoot: canonicalRoot }),
    });
    const result = await response.json();
    sharedSignal.throwIfAborted();
    if (!response.ok) throw new Error(result.error || `Project API returned ${response.status}`);
    return result;
  }, signal);
  signal?.throwIfAborted();
  if (typeof payload.projectRoot === "string" && payload.projectRoot) onResolvedRoot?.(payload.projectRoot);
  return Array.isArray(payload.workflows) ? payload.workflows : [];
}

export default function App() {
  const deviceSyncContext = useRef({});
  const [confirmAction, confirmDialog] = useConfirm();
  useEffect(() => {
    // The sync engine loads after startup; it is not needed to render the app.
    let stop = null;
    let closed = false;
    import("../lib/deviceWorkspaceSync.js").then(({ startDeviceWorkspaceSync }) => {
      if (!closed) stop = startDeviceWorkspaceSync({ getContext: () => deviceSyncContext.current, deleteThread: deletePhoneThread });
    });
    return () => { closed = true; stop?.(); };
  }, []);
  const [settings, setSettings] = useState(loadAppSettings);
  const [initialStudioSession] = useState(loadStudioSession);
  const [initialEditorSession] = useState(loadEditorSession);
  const [textZoom, setTextZoom] = useState(loadTextZoom);
  useEffect(() => {
    const bridge = window.goferDesktop?.rem;
    if (!bridge) return;
    void bridge.settings().then((memory) => {
      setSettings((current) => ({ ...current, memory: { ...current.memory, ...memory, reportThemes: normalizeReportThemes(memory.reportThemes ?? (memory.secondBrainTheme ? undefined : current.memory.reportThemes), memory.secondBrainTheme) } }));
    }).catch(reportArchiveError);
  }, []);
  useEffect(() => {
    const editError = event => setTopBarNotice({ type: "error", message: event.detail || "Unable to edit the focused field" });
    window.addEventListener("gofer:edit-error", editError);
    return () => window.removeEventListener("gofer:edit-error", editError);
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
  // The command palette or quick open, while one is showing.
  const [palette, setPalette] = useState(null);
  const [paletteFiles, setPaletteFiles] = useState({ root: "", files: [], loading: false, error: "" });
  const [paletteTriggersPaused, setPaletteTriggersPaused] = useState(false);
  const openPaletteRef = useRef(null);
  const paletteOpenRef = useRef(false);
  const runShortcutActionRef = useRef(null);
  // The editor tabs on screen; the media bar skips a tab the user is looking at.
  const [viewedCodePaths, setViewedCodePaths] = useState([]);
  const [viewedBrowserPaths, setViewedBrowserPaths] = useState([]);
  // A Rem approval waiting in a web tab while the Browser plugin isn't in front.
  const [hiddenBrowserApproval, setHiddenBrowserApproval] = useState(null);
  // Phones the suspicious-request guard locked; only the user unlocks them, in Settings.
  const [phoneLocks, setPhoneLocks] = useState([]);
  const [settingsCategory, setSettingsCategory] = useState("general");
  const [settingsBrowserSection, setSettingsBrowserSection] = useState("");
  // Selectors for one setting Settings scrolls to when it opens, such as Rem's provider.
  const [settingsReveal, setSettingsReveal] = useState(null);
  useEffect(() => {
    const openProviders = () => { setSettingsCategory("providers"); setSettingsOpen(true); };
    // Firefox and Chrome tabs open Settings > Browser for that browser's settings.
    // A tab's menu names its browser, so Settings shows that browser's section.
    const openBrowser = (event) => { setSettingsCategory("browser"); setSettingsBrowserSection(typeof event?.detail?.engine === "string" ? event.detail.engine : ""); setSettingsOpen(true); };
    // Self-work's settings are in Settings > Rem, reachable with the Kanban board off.
    const openRem = () => { setSettingsCategory("assistant"); setSettingsOpen(true); };
    window.addEventListener("raticode:open-provider-settings", openProviders);
    window.addEventListener("raticode:open-browser-settings", openBrowser);
    window.addEventListener("raticode:open-rem-settings", openRem);
    const openDevices = () => { setSettingsCategory("devices"); setSettingsOpen(true); };
    const locked = (event) => setPhoneLocks(current => {
      const next = Array.isArray(event?.detail) ? event.detail : [];
      return JSON.stringify(current) === JSON.stringify(next) ? current : next;
    });
    window.addEventListener("raticode:open-device-settings", openDevices);
    window.addEventListener("gofer:phone-locked", locked);
    return () => {
      window.removeEventListener("raticode:open-device-settings", openDevices);
      window.removeEventListener("gofer:phone-locked", locked);
      window.removeEventListener("raticode:open-provider-settings", openProviders);
      window.removeEventListener("raticode:open-browser-settings", openBrowser);
      window.removeEventListener("raticode:open-rem-settings", openRem);
    };
  }, []);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );
  const [workflows, setWorkflows] = useState([]);
  const [legacyTomlFiles, setLegacyTomlFiles] = useState([]);
  // The TOML file being converted to Rattish, and how its conversion went.
  const [tomlConversion, setTomlConversion] = useState(null);
  const [activeWorkflowId, setActiveWorkflowId] = useState(initialStudioSession.workflowId || undefined);
  const activeWorkflowIdRef = useRef(activeWorkflowId);
  activeWorkflowIdRef.current = activeWorkflowId;
  const [openingProjectRoot, setOpeningProjectRoot] = useState("");
  const [projectError, setProjectError] = useState("");
  const [activeProjectRoot, setActiveProjectRoot] = useState(initialStudioSession.projectRoot);
  const activeProjectRootRef = useRef(activeProjectRoot);
  activeProjectRootRef.current = activeProjectRoot;
  const [selectedSwarm, setSelectedSwarm] = useState(null);
  const [selectedOrganization, setSelectedOrganization] = useState(null);
  const [studioView, setStudioView] = useState(initialStudioSession.view || settings.general.defaultView);
  const [, setCodeEditorOpened] = useState(
    (initialStudioSession.view || settings.general.defaultView) === "code",
  );
  const [codeOpenPaths, setCodeOpenPaths] = useState(initialEditorSession?.paths || []);
  // Web pages another app opened with Raticode. They are kept out of what Rem
  // sees of the editor, and stay offline documents with scripts off across restarts.
  const [externalFilePaths, setExternalFilePaths] = useState(() => new Set(initialEditorSession?.externalPaths || []));
  const [activeCodePath, setActiveCodePath] = useState(initialEditorSession?.activePath || "");
  const [previewCodePath, setPreviewCodePath] = useState("");
  const [codeNavigationRequest, setCodeNavigationRequest] = useState(null);
  // The Browser plugin's tabs, apart from the File explorer's file tabs. Each
  // tab keeps the browser it runs in; tabs saved before that ran in the
  // browser chosen in Settings > Browser and keep it.
  const [initialBrowserSession] = useState(() => withBrowserTabEngines(initialEditorSession?.browserPaths || [], initialEditorSession?.browserTabs || {}, settings.browser.engine));
  const [browserTabs, setBrowserTabs] = useState(initialBrowserSession.tabs);
  const [browserOpenPaths, setBrowserOpenPaths] = useState(initialBrowserSession.paths);
  const [activeBrowserPath, setActiveBrowserPath] = useState(() => initialBrowserSession.paths.includes(initialEditorSession?.browserActivePath) ? initialEditorSession.browserActivePath : initialBrowserSession.paths.at(-1) || "");
  const [newCodeFileRequest, setNewCodeFileRequest] = useState(0);
  const [recentCodePaths, setRecentCodePaths] = useState(loadRecentCodePaths);
  const [recentProjectRoots, setRecentProjectRoots] = useState(loadRecentProjectRoots);
  deviceSyncContext.current = { settings, roots: mergeRecentProjects(recentProjectRoots, workflows.map(item => item.projectRoot).filter(Boolean)),
    updateSettings: changes => setSettings(current => normalizeAppSettings(applyPhoneSettings(current, changes))),
    // A paired phone's browser lists these web tabs and can send a page to a new background tab.
    browser: { searchUrl: settings.browser.searchUrl, engine: controlledEngine(settings.browser.engine), tabs: () => phoneBrowserTabs(browserOpenPaths, activeBrowserPath, browserTabs),
      // A page from the phone opens where the phone was told the computer's tabs run.
      open: url => openIntegratedBrowserRef.current?.(url, { newTab: true, background: true, engine: controlledEngine(settings.browser.engine) }) } };
  const [lastWorktreeByProject, setLastWorktreeByProject] = useState(loadLastWorktreeByProject);
  const [rattishSessions, setRattishSessions] = useState({});
  const documentSessionsRef = useRef(new Map());
  const documentSession = useCallback((workflowId) => {
    if (!documentSessionsRef.current.has(workflowId)) documentSessionsRef.current.set(workflowId, {
      state: { current: null }, metadataTimer: { current: null }, metadataPending: { current: false },
      analysisTimer: { current: null }, analysisRequest: { current: 0 },
      writes: { current: Promise.resolve() }, metadataSaving: { current: null },
      graphHistory: { current: EMPTY_GRAPH_HISTORY },
    });
    const session = documentSessionsRef.current.get(workflowId);
    return {
      documentWritesRef: session.writes,
      graphHistoryRef: session.graphHistory,
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
  // Undo and Redo stacks live in each workflow's document session; this redraws their buttons.
  const [, setGraphHistoryVersion] = useState(0);
  const graphHistoryChanged = useCallback(() => setGraphHistoryVersion(version => version + 1), []);
  const rattishEditorState = rattishSessions[activeWorkflowId] ?? null;
  const [workflowTabs, setWorkflowTabs] = useState(initialEditorSession?.workflowTabs || {});
  const [requestedSidebarActivity, setSidebarActivity] = useState(initialEditorSession?.activity || (initialStudioSession.view === "code" ? "files" : settings.general.initialActivity || "workflows"));
  // Turning a plugin off moves its view to files without losing the choice.
  const sidebarActivity = resolvePluginActivity(settings.plugins, requestedSidebarActivity);
  // The main content area changes only when something is opened, not when the
  // sidebar switches plugins, so Rem's conversation stays visible while the
  // sidebar shows source control or workflows. Clicking Rem or Second Brain in
  // the rail opens its home, and clicking File explorer brings back the file and
  // browser tabs; Swarms and Organizations show their workspace once one is opened.
  const [requestedMainContent, setMainContent] = useState(() => initialEditorSession?.mainContent || initialMainContent(requestedSidebarActivity));
  const mainContent = resolveMainContent(settings.plugins, requestedMainContent);
  const remHomeOpen = mainContent === "rem";
  const secondBrainOpen = mainContent === "second-brain";
  const kanbanOpen = mainContent === "kanban";
  const postsOpen = mainContent === "posts";
  useEffect(() => {
    const open = () => { setSidebarActivity("kanban"); setMainContent("kanban"); };
    window.addEventListener("gofer:kanban-open", open);
    return () => window.removeEventListener("gofer:kanban-open", open);
  }, []);
  // Every workflow's runs, opened from the workflows sidebar or Rem's away card. Each
  // open carries its own filters, such as the time the user went away.
  const workflowRunsOpen = mainContent === WORKFLOW_RUNS_CONTENT;
  const [workflowRunsRequest, setWorkflowRunsRequest] = useState(null);
  useEffect(() => {
    const open = (event) => {
      const { since, status, workflow } = event?.detail || {};
      setWorkflowRunsRequest(since || status || workflow ? { ...(since ? { since } : {}), ...(status ? { status } : {}), ...(workflow ? { workflow } : {}) } : null);
      setMainContent(WORKFLOW_RUNS_CONTENT);
    };
    window.addEventListener(WORKFLOW_RUNS_EVENT, open);
    return () => window.removeEventListener(WORKFLOW_RUNS_EVENT, open);
  }, []);
  // A workflow shortcut's notice was clicked: open that run, or, for a workflow that
  // changed since its shortcut's version was approved, its Review changes in Settings.
  const reviewWorkflowRunRef = useRef(null);
  useEffect(() => globalThis.window?.goferDesktop?.workflowShortcuts?.onEvent?.((message) => {
    if (message?.action === "review" && typeof message.shortcutId === "string" && message.shortcutId) {
      requestShortcutReview(message.shortcutId);
      setSettingsCategory("devices");
      setSettingsOpen(true);
      return;
    }
    if (message?.action !== "open-run" || !message.run?.workflowId || !message.run?.runId) return;
    const { workflowId, runId, status, projectRoot, workflowName } = message.run;
    void reviewWorkflowRunRef.current?.({ workflowId, runId, status: status || "running", projectPath: projectRoot, workflowName });
  }), []);
  // Rem's backend learns whether the board is shown: with it off, self-work uses no tickets.
  const kanbanBoardOn = pluginEnabled(settings.plugins, "kanban");
  useEffect(() => {
    setRemHomeContext(async () => {
      const workspace = window.goferDesktop?.workspace;
      const projects = [];
      for (const root of mergeRecentProjects(activeProjectRoot ? [activeProjectRoot] : [], recentProjectRoots)) {
        // One unreadable or untrusted project is left out instead of stopping Rem.
        try {
          if (workspace?.getPathInfo && !(await workspace.getPathInfo(root))?.isDirectory) continue;
          await workspace?.trustProjectRoot?.(root);
        } catch { continue; }
        projects.push({ root, name: projectNameFromPath(root), grantId: workspace?.pathGrantForApi?.(root) });
      }
      const memory = settings.memory;
      const selection = snapshotRemResources(settings.assistant.resources);
      let brain = { enabled: memory.secondBrainEnabled, root: memory.secondBrainRoot, format: memory.secondBrainFormat, grantId: workspace?.pathGrantForApi?.(memory.secondBrainRoot) };
      if (memory.secondBrainEnabled) await workspace?.trustProjectRoot?.(memory.secondBrainRoot);
      if (brain.enabled) {
        const { threadBrainConfig } = await import("../lib/secondBrainApi.js");
        brain = await threadBrainConfig(selection, brain);
      }
      delete selection.secondBrains;
      delete selection.disabledSecondBrains;
      brain.grantId = workspace?.pathGrantForApi?.(brain.root);
      const roots = new Set(projects.map(project => project.root));
      const threads = loadAllChatThreads().filter(thread => thread.scopeMode === "global" || roots.has(thread.projectRoot))
        .map(thread => ({ id: thread.id, title: thread.title, projectRoot: thread.projectRoot, updatedAt: thread.updatedAt, archived: thread.archived }));
      return { provider: settings.assistant.provider || "codex", model: settings.assistant.model || "cli-default", effort: settings.assistant.effort || "", chat_provider: settings.assistant.chatProvider || "", chat_model: settings.assistant.chatModel || "", chat_effort: settings.assistant.chatEffort || "", provider_accounts: settings.assistant.providerAccounts || {}, projects, resources: selection, brain, conversation_archive_root: memory.archiveFolder || "", report_theme: reportThemeContext(memory), workspace_snapshot: { threads }, browser_target: settings.browser.agentBrowser, swarm_access: settings.assistant.swarmAccessEnabled !== false, command_slots: commandSlotSettings(settings.assistant), kanban_board: kanbanBoardOn };
    });
    return () => setRemHomeContext(null);
  }, [settings.assistant, settings.memory, settings.browser.agentBrowser, activeProjectRoot, recentProjectRoots, kanbanBoardOn]);

  // Projects Rem opened for a request join the window's projects, without switching to them.
  useEffect(() => onRemOpenedProjects(opened => setRecentProjectRoots(current => adoptRemProjects(current, opened))), []);

  // Task supervision keeps updating threads even while another workspace is open.
  useEffect(() => connectRemHome(), []);
  useEffect(() => setBackgroundNotices(settings.general.backgroundNotices), [settings.general.backgroundNotices]);

  const [lifecyclePrompt, setLifecyclePrompt] = useState(null);
  const [workflowClosePrompt, setWorkflowClosePrompt] = useState(null);
  // The workflow whose "Add shortcut…" dialog is open.
  const [shortcutWorkflow, setShortcutWorkflow] = useState(null);
  const [activeCodeDocumentState, setActiveCodeDocumentState] = useState(null);
  const { projectPaneVisible, setProjectPaneVisible, assistantPaneVisible, setAssistantPaneVisible, closeCompactPane } = useResponsivePanes(settings.layout);
  const [assistantFocusRequest, setAssistantFocusRequest] = useState(0);
  // With Rem's chat in the side pane, asking about code or opening a thread
  // brings threads forward until the person goes back to Rem.
  const [threadsRequested, setThreadsRequested] = useState(false);
  useEffect(() => {
    const openRem = () => { setThreadsRequested(true); setAssistantPaneVisible(true); setAssistantFocusRequest(current => current + 1); };
    const showThread = () => { setThreadsRequested(true); setAssistantPaneVisible(true); };
    const showTask = () => { setThreadsRequested(false); setAssistantPaneVisible(true); };
    window.addEventListener("gofer:rem-context", openRem);
    window.addEventListener("gofer:rem-open-thread", showThread);
    window.addEventListener("gofer:rem-show-task", showTask);
    return () => { window.removeEventListener("gofer:rem-context", openRem); window.removeEventListener("gofer:rem-open-thread", showThread); window.removeEventListener("gofer:rem-show-task", showTask); };
  }, [setAssistantPaneVisible]);
  // "Ask Rem about this" attaches files to Rem's chat when the Rem plugin is on,
  // otherwise to the open thread in the Rem pane.
  const remPluginOn = pluginEnabled(settings.plugins, "rem");
  // Settings > Layout can show Rem's chat in the side pane in place of threads.
  // Rem's chat is shown in one place at a time, so while it fills the main area
  // (the Rem plugin) the side pane shows threads.
  const remPaneLayout = settings.layout.assistantPaneContent === "rem" && remPluginOn;
  const sidePaneShowsRem = remPaneLayout && !remHomeOpen && !threadsRequested;
  // Rem's chat mounts the first time it is shown and then stays mounted, moving
  // between the main area and the side pane without re-rendering its history.
  const [remHost] = useState(() => (typeof document === "undefined" ? null : createRemHost()));
  const [remMounted, setRemMounted] = useState(false);
  if ((remHomeOpen || sidePaneShowsRem) && !remMounted) setRemMounted(true);
  // An opened task shows in the Rem sidebar, so opening one from the chat brings
  // that sidebar forward. "View in conversation" there shows Rem's chat if it is
  // hidden: in the side pane with that layout, otherwise in the main area.
  const remChatRef = useRef({});
  remChatRef.current = { visible: remHomeOpen || sidePaneShowsRem, sidePane: remPaneLayout, showMain: () => showMainContent("rem") };
  useEffect(() => {
    const showTask = () => { setSidebarActivity("rem"); setProjectPaneVisible(true); };
    const showMessage = () => {
      const rem = remChatRef.current;
      if (rem.sidePane) { setThreadsRequested(false); setAssistantPaneVisible(true); return; }
      if (!rem.visible) rem.showMain();
    };
    window.addEventListener("gofer:rem-task-sheet", showTask);
    window.addEventListener("gofer:rem-show-message", showMessage);
    return () => { window.removeEventListener("gofer:rem-task-sheet", showTask); window.removeEventListener("gofer:rem-show-message", showMessage); };
  }, [setProjectPaneVisible, setAssistantPaneVisible]);
  useEffect(() => {
    const receive = event => {
      const paths = event.detail?.paths || [];
      // Files made in the app, such as selected log entries, arrive ready to attach.
      const files = event.detail?.files || [];
      const draft = event.detail?.draft || "";
      if (!paths.length && !files.length) return;
      // Asking from Settings (the Developer log) shows Rem instead of the popover.
      setSettingsOpen(false);
      if (!remPluginOn) {
        setAssistantPaneVisible(true);
        setAssistantFocusRequest(current => current + 1);
        window.dispatchEvent(new CustomEvent("gofer:rem-thread-attach-files", { detail: { paths, files, draft } }));
        return;
      }
      setSelectedSwarm(null);
      setSelectedOrganization(null);
      setSidebarActivity("rem");
      setMainContent("rem");
      setThreadsRequested(false);
      void import("../lib/remComposer.js").then(({ attachPathsToRemComposer, attachFilesToRemComposer }) => {
        if (files.length) attachFilesToRemComposer(files, draft);
        if (paths.length) void attachPathsToRemComposer(paths);
      });
    };
    window.addEventListener("gofer:rem-attach-files", receive);
    return () => window.removeEventListener("gofer:rem-attach-files", receive);
  }, [remPluginOn, setAssistantPaneVisible]);
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
  const [topBarNotice, setTopBarNotice] = useState({ type: "", message: "" });
  // Password inputs a Rattish run didn't save, asked for again before Resume or Run again.
  const [savedInputsPrompt, setSavedInputsPrompt] = useState(null);
  const [updateNotice, setUpdateNotice] = useState({ type: "", message: "" });
  const [runPreview, setRunPreview] = useState(null);
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
  const refreshRunRegistry = runRegistry.refresh;
  const runRegistryRecordsRef = useRef(runRegistry.records);
  runRegistryRecordsRef.current = runRegistry.records;
  const [pinnedRun, setPinnedRun] = useState(initialEditorSession?.pinnedRun || null);
  useEffect(() => {
    const savedBrowser = withoutAgentBrowserTabs(browserOpenPaths, activeBrowserPath, browserTabs);
    saveEditorSession({ paths: codeOpenPaths, activePath: activeCodePath, browserPaths: savedBrowser.paths, browserActivePath: savedBrowser.activePath, browserTabs: savedBrowser.browserTabs, workflowTabs, externalPaths: [...externalFilePaths], activity: sidebarActivity, mainContent, pinnedRun });
  }, [codeOpenPaths, activeCodePath, browserOpenPaths, activeBrowserPath, workflowTabs, externalFilePaths, browserTabs, sidebarActivity, mainContent, pinnedRun]);
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
    worktree: null,
  });
  const [approvalState, setApprovalState] = useState({
    approvals: [],
    error: "",
    loading: false,
  });
  const deletedWorkflowIdsRef = useRef(new Set());
  const logRequestRef = useRef(0);
  const logListRequestRef = useRef(0);
  const approvalRequestRef = useRef(0);
  const rattishEditorRef = useRef(null);
  // The Browser plugin's tab area, and whichever tab area is in front, for
  // commands such as Find and Close tab.
  const browserWorkspaceRef = useRef(null);
  const browserContentOpenRef = useRef(false);
  // The Browser plugin can live in a window of its own; it is in view there.
  const [browserPoppedOut, setBrowserPoppedOut] = useState(false);
  const activeWorkspaceRef = useMemo(() => ({ get current() { return (browserContentOpenRef.current ? browserWorkspaceRef : rattishEditorRef).current; } }), []);
  const previewCodePathRef = useRef("");
  const codeNavigationSequenceRef = useRef(0);
  const pendingProjectFileRef = useRef(null);
  const workflowLoadRequestRef = useRef(0);
  const projectOpenRequestRef = useRef(0);
  const projectOpenAbortRef = useRef(null);
  const projectOpenPendingRef = useRef(null);
  const workflowLoadAbortRef = useRef(null);
  const recentProjectValidatorRef = useRef(null);
  if (!recentProjectValidatorRef.current) recentProjectValidatorRef.current = createRecentProjectValidator();
  useEffect(() => () => {
    projectOpenAbortRef.current?.abort();
    workflowLoadAbortRef.current?.abort();
  }, []);
  const openProjectFolderRef = useRef(null);
  const openFileRef = useRef(null);
  const chordPendingRef = useRef(null);
  const openIntegratedBrowserRef = useRef(null);
  const reopenClosedBrowserTabRef = useRef(null);
  const closedBrowserTabsRef = useRef([]);
  const closeAgentBrowserTabRef = useRef(null);
  const openLinkedCodeFileRef = useRef(null);
  const terminalEditorRequestsRef = useRef(new Map());
  const changeStudioViewRef = useRef(null);
  const runWorkflowNowRef = useRef(null);
  const theme = resolvedTheme(settings, systemDark);
  const workflowPaneWidth = settings.layout.workflowPaneWidth;
  const chatPaneWidth = settings.layout.assistantPaneWidth;
  const activeWorkflow = workflows.find(item => item.id === activeWorkflowId);
  const browsingWorkspace = activeWorkspaceForProject(workflows, undefined, activeProjectRoot) ?? projectWorkspace(activeProjectRoot);
  const swarmProjectRoot = browsingWorkspace?.projectRoot || activeProjectRoot || "";
  const swarmOpen = mainContent === "swarms" && samePath(selectedSwarm?.projectRoot, swarmProjectRoot);
  const organizationOpen = mainContent === "organizations" && Boolean(selectedOrganization);
  const browserContentOpen = mainContent === BROWSER_CONTENT;
  browserContentOpenRef.current = browserContentOpen;
  const editorContentOpen = !remHomeOpen && !secondBrainOpen && !swarmOpen && !organizationOpen && !kanbanOpen && !postsOpen && !browserContentOpen && !workflowRunsOpen;
  const codeWorkspaceWorkflow = workflows.find(item => samePath(item.sourcePath, activeCodePath)) ?? activeWorkflow ?? browsingWorkspace;
  // Rem's chat looks the same in the main area and in the side pane.
  const remHomeProps = {
    onOpenLink: href => void openMarkdownFileLink(href),
    // target, when given, is { selectors } for the Settings › Rem control to show.
    onOpenSettings: (target) => { setSettingsCategory("assistant"); setSettingsReveal(Array.isArray(target?.selectors) ? target.selectors : null); setSettingsOpen(true); },
    remProvider: { provider: settings.assistant.provider, chatProvider: settings.assistant.chatProvider },
    projectOptions: mergeRecentProjects(activeProjectRoot ? [activeProjectRoot] : [], recentProjectRoots),
    avatar: { enabled: settings.assistant.avatarEnabled, animated: settings.assistant.avatarAnimated, reducedMotion: settings.appearance.reducedMotion },
  };


  const changeSetting = useCallback((path, value) => {
    setSettings((current) => updateSetting(current, path, value));
  }, []);

  // Renaming or removing an account in Settings > Providers keeps Rem's account setting valid.
  useEffect(() => {
    const follow = (event) => setSettings((current) => followRenamedAccount(current, event.detail || {}));
    window.addEventListener("raticode:provider-account-renamed", follow);
    return () => window.removeEventListener("raticode:provider-account-renamed", follow);
  }, []);

  // Organizations also has a backend switch. When the backend has it off, the
  // plugin's preview turns off so the feature appears only in Feature Preview.
  const organizationsShown = pluginAvailable(settings.plugins, "organizations");
  useEffect(() => {
    if (!organizationsShown) return undefined;
    let cancelled = false;
    fetch(apiUrl("/organizations?projectRoot=&action=availability")).then((response) => response.json()).then((payload) => {
      if (cancelled || payload?.result?.enabled !== false) return;
      setSettings((current) => updateSetting(current, "plugins.preview", withPluginPreview(current.plugins, "organizations", false)));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [organizationsShown]);
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
      // Zoom keys inside the browser chrome zoom the page instead.
      if (browserChromeOwnsShortcut(event)) return;
      if (matchesKeybinding(event, "Mod+Digit0") || matchesKeybinding(event, "Ctrl+Digit0")) {
        if (eventTargetsGraphVisualization(event)) return;
        event.preventDefault();
        setTextZoom(100);
        return;
      }
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
    const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const applyMotion = () => {
      if (document.documentElement.dataset) document.documentElement.dataset.reducedMotion = String(reducedMotionEnabled(settings, motionQuery?.matches ?? false));
    };
    applyMotion();
    motionQuery?.addEventListener?.("change", applyMotion);
    document.documentElement.classList?.toggle?.("dark", theme === "dark");
    try {
      window.localStorage?.setItem("gofer-ui-theme", theme);
    } catch {
      // The selected theme still applies for this session.
    }
    return () => motionQuery?.removeEventListener?.("change", applyMotion);
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
        samePath(projectRoot, activeProjectRoot) ? projectRoot : pathValue(lastWorktreeByProject, projectRoot) || projectRoot,
        window.goferDesktop.workspace,
        mainWorktreeRoot,
      ),
    )).then((projects) => {
      if (cancelled) return;
      const existingProjects = projects.filter((project, index) => project && recentProjectRoots.some(root => samePath(root, roots[index])));
      const activeProject = projects.find((project, index) => project && samePath(roots[index], activeProjectRoot));
      if (activeProject && existingProjects.some(project => samePath(project.mainProjectRoot, activeProject.mainProjectRoot))) {
        existingProjects.push(activeProject);
      }
      const missingRoots = roots.flatMap((root, index) => {
        const selected = samePath(root, activeProjectRoot) ? root : pathValue(lastWorktreeByProject, root) || root;
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
      const selections = {};
      for (const project of existingProjects) {
        const root = nextRoots.find(root => samePath(root, project.mainProjectRoot));
        // Old recent lists can contain several worktrees of the same repository.
        // Keep the active checkout, or the first remembered selection, when folding them.
        if (!selections[root] || samePath(project.selectedProjectRoot, activeProjectRoot)) {
          selections[root] = project.selectedProjectRoot;
        }
      }
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

  // Chat boxes show the dictation shortcut in their titles and hints.
  useEffect(() => {
    setVoiceBinding(settingBinding(settings, VOICE_COMMAND_ID));
    setDictationMode(settings.devices?.dictationMode);
    setDictationAutoSend(settings.devices?.dictationAutoSend);
  }, [settings]);

  // Dictate to Rem from any app: the desktop app registers the system-wide shortcut while
  // Settings > Devices has it on, and reports whether it could.
  const anywhereEnabled = settings.devices?.dictateAnywhere === true;
  const anywhereBinding = settingBinding(settings, ANYWHERE_COMMAND_ID);
  // Show Rem your screen: the Screen button in Rem's box, and the "Ask Rem about your screen"
  // shortcut while Dictate to Rem from any app is also on. Off by default.
  const screenEnabled = settings.devices?.screenContext === true;
  const screenBinding = settingBinding(settings, SCREEN_COMMAND_ID);
  useEffect(() => {
    const bridge = window.goferDesktop?.screenContext;
    setScreenContextEnabled(screenEnabled && Boolean(bridge?.configure));
    if (!bridge?.configure) {
      setScreenStatus(screenEnabled ? { unavailable: true } : null);
      return undefined;
    }
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled: screenEnabled, shortcut: anywhereEnabled, binding: screenBinding }))
      .then((status) => { if (!cancelled) setScreenStatus(status); }, () => { if (!cancelled) setScreenStatus({ enabled: screenEnabled, error: "Raticode couldn't set up screenshots." }); });
    return () => { cancelled = true; };
  }, [screenEnabled, anywhereEnabled, screenBinding]);
  useEffect(() => {
    const bridge = window.goferDesktop?.globalDictation;
    if (!bridge?.configure) {
      setAnywhereStatus(anywhereEnabled ? { unavailable: true } : null);
      return undefined;
    }
    let cancelled = false;
    void Promise.resolve(bridge.configure({ enabled: anywhereEnabled, binding: anywhereBinding, label: voiceShortcutLabel(anywhereBinding) }))
      .then((status) => { if (!cancelled) setAnywhereStatus(status); }, () => { if (!cancelled) setAnywhereStatus({ enabled: anywhereEnabled, error: "Raticode couldn't set up the shortcut." }); });
    return () => { cancelled = true; };
  }, [anywhereEnabled, anywhereBinding]);
  // The shortcut was pressed in another app: dictate into Rem's chat without bringing the
  // window forward. Rem's chat loads (unseen) if it hasn't been opened yet.
  const remPluginOnForVoice = pluginEnabled(settings.plugins, "rem");
  useEffect(() => {
    const bridge = window.goferDesktop?.globalDictation;
    if (!bridge?.onTrigger) return undefined;
    const report = (state) => { void Promise.resolve(bridge.report?.(state)).catch(() => {}); };
    const stopPhases = onVoicePhase(report);
    const stopTrigger = bridge.onTrigger((message) => {
      // A Wayland desktop answered later (its prompt, or a key changed in its settings).
      if (message?.action === "status") {
        if (message.status && typeof message.status === "object") setAnywhereStatus(message.status);
        return;
      }
      if (message?.action === "stop") {
        stopAnywhereDictation();
        return;
      }
      if (message?.action === "screen") {
        // "Ask Rem about your screen": the screenshot goes in Rem's box, then dictation starts
        // (or carries on). It always waits there to be checked before it is sent.
        if (!remPluginOnForVoice) {
          report({ phase: "error", global: true, message: "Turn on the Rem plugin in Settings > Plugins to show Rem your screen." });
          return;
        }
        const screen = screenFromCapture(message.capture, { fromShortcut: true });
        if (!screen) {
          report({ phase: "error", global: true, message: "The screenshot is too large to send to Rem." });
          return;
        }
        void import("../lib/remComposer.js").then(({ setRemScreen }) => {
          setRemScreen(screen);
          if (pickRemComposer()?.dictating()) return;
          return requestAnywhereDictation({ reveal: () => setRemMounted(true) }).then((result) => {
            if (!result.ok) report({ phase: "error", global: true, message: `${result.message} The screenshot is waiting in Rem's message box.` });
          });
        });
        return;
      }
      if (message?.action !== "toggle") return;
      if (!remPluginOnForVoice) {
        report({ phase: "error", global: true, message: "Turn on the Rem plugin in Settings > Plugins to dictate to Rem." });
        return;
      }
      void requestAnywhereDictation({ reveal: () => setRemMounted(true) }).then((result) => {
        if (!result.ok) report({ phase: "error", global: true, message: result.message });
      });
    });
    return () => { stopPhases(); stopTrigger?.(); };
  }, [remPluginOnForVoice]);

  useEffect(() => {
    function runShortcutAction(action) {
      if (action === PALETTE_COMMAND_ID) openPaletteRef.current?.("commands");
      if (action === QUICK_OPEN_COMMAND_ID) openPaletteRef.current?.("files");
      if (action === "settings.open") setSettingsOpen(true);
      if (action === "file.open") void openFileRef.current?.();
      if (action === "project.open") void openProjectFolderRef.current?.();
      if (action === "browser.open") openIntegratedBrowserRef.current?.(undefined, { newTab: true });
      if (action === "browser.reopenClosedTab") reopenClosedBrowserTabRef.current?.();
      if (action === "view.graph") changeStudioViewRef.current?.("graph");
      if (action === "view.code") changeStudioViewRef.current?.("code");
      if (action === "view.toggleProjectPane") setProjectPaneVisible((current) => !current);
      if (action === "view.toggleAssistantPane") setAssistantPaneVisible((current) => !current);
      // Dictate into the chat box in use, or show Rem and dictate there.
      if (action === VOICE_COMMAND_ID) void requestVoiceDictation({ reveal: () => setAssistantPaneVisible(true) });
      if (action === "terminal.new") window.dispatchEvent(new CustomEvent("gofer:new-terminal"));
      if (action === "panel.toggle") window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel"));
      if (action === "workflow.run" && activeWorkflow && !runState.running
        && (workflowTabs[activeCodePath]?.workflowId === activeWorkflow.id || activeCodePath === activeWorkflow.sourcePath)) {
        void runWorkflowNowRef.current?.(activeWorkflow);
      }
    }
    runShortcutActionRef.current = runShortcutAction;
    function clearChordPending() {
      window.clearTimeout(chordPendingRef.current?.timeoutId);
      chordPendingRef.current = null;
    }
    function handleApplicationShortcut(event) {
      if (settingsOpen) return;
      // Ctrl+L and other standard browser keys belong to a focused browser.
      if (browserChromeOwnsShortcut(event)) return;
      // The composer owns Enter, including Ctrl+Enter for background threads.
      // Run workflow defaults to the same keys on Windows and Linux.
      if (event.key === "Enter" && event.target?.tagName === "TEXTAREA"
        && event.target.closest?.("[data-chat-composer]")) {
        clearChordPending();
        return;
      }
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
        PALETTE_COMMAND_ID,
        QUICK_OPEN_COMMAND_ID,
        "settings.open",
        "file.open",
        "project.open",
        "browser.open",
        "browser.reopenClosedTab",
        "view.graph",
        "view.code",
        "view.toggleProjectPane",
        "view.toggleAssistantPane",
        "workflow.run",
        VOICE_COMMAND_ID,
      ]) {
        const paletteKey = commandId === PALETTE_COMMAND_ID || commandId === QUICK_OPEN_COMMAND_ID;
        // While the palette is open, only its own keys act. Its keys give way to a
        // focused terminal or code editor, which keep them.
        if (paletteKey ? paletteGivesWay(event.target) : paletteOpenRef.current) continue;
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
      if (action === VOICE_COMMAND_ID) {
        pressDictationShortcut(event);
        return;
      }
      runShortcutAction(action);
    }
    // In Hold to talk, dictation listens until the shortcut is let go: any of its keys
    // coming up, or the window losing focus. In Press to start and stop, letting go does nothing.
    function pressDictationShortcut(down) {
      void requestVoiceDictation({ kind: "press", reveal: () => setAssistantPaneVisible(true) });
      const keys = new Set([down.code, "Control", "Alt", "AltGraph", "Meta", "Shift"]);
      const release = () => {
        window.removeEventListener("keyup", keyUp, true);
        window.removeEventListener("blur", release);
        void requestVoiceDictation({ kind: "release" });
      };
      const keyUp = (event) => { if (keys.has(event.code) || keys.has(event.key)) release(); };
      window.addEventListener("keyup", keyUp, true);
      window.addEventListener("blur", release);
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
      if (command?.action === "editor-find") activeWorkspaceRef.current?.runCommand?.("edit.find");
      if (command?.action === "open-browser") runShortcutAction("browser.open");
      if (command?.action === "settings-open") runShortcutAction("settings.open");
      if (command?.action === "file-open") runShortcutAction("file.open");
      if (command?.action === "project-open") runShortcutAction("project.open");
      if (command?.action === "panel-toggle") runShortcutAction("panel.toggle");
      if (command?.action === "project-pane-toggle") runShortcutAction("view.toggleProjectPane");
      if (command?.action === "assistant-pane-toggle") runShortcutAction("view.toggleAssistantPane");
    });
    const unsubscribeOpenTab = window.goferBrowser?.onOpenTab?.((request) => {
      // A link from a web tab opens in a new tab of the same browser.
      if (request?.url) openIntegratedBrowserRef.current?.(request.url, { newTab: true, background: request.background === true, private: request.private === true, ...(typeof request.engine === "string" && request.engine ? { engine: request.engine } : {}) });
    });
    const unsubscribeOpenFile = window.goferBrowser?.onOpenFile?.((request) => {
      const target = request?.href ? markdownFileLinkTarget("", request.href) : request;
      // A page's link is a link: opening a folder asks first, and a file opened from a
      // file of another app keeps scripts off (see openLinkedCodeFile).
      const fromPage = request?.fromPage === true;
      if (target?.path) void openLinkedCodeFileRef.current?.(target.path, { ...target, preview: true, ...(fromPage ? { fromLink: true } : {}), ...(fromPage && request?.external === true ? { external: true } : {}) });
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
  }, [activeWorkflow, activeCodePath, activeWorkspaceRef, workflowTabs, runState.running, settings, settingsOpen, setAssistantPaneVisible, setProjectPaneVisible]);

  // Links and web pages other apps opened with Raticode: the ones that arrived
  // before this window loaded, then each new one as it comes.
  useEffect(() => {
    const bridge = window.goferBrowser;
    if (!bridge?.defaultBrowser) return undefined;
    let cancelled = false;
    const open = async (items) => {
      for (const item of Array.isArray(items) ? items : []) {
        if (cancelled) return;
        await openExternalItemRef.current?.(item);
        // Lets the tab bar update first, so a second Raticode (gecko) link joins the first one's window.
        await new Promise((resolve) => window.setTimeout(resolve, 50));
      }
    };
    const unsubscribe = bridge.onExternalOpen?.((message) => { void open(message?.items); });
    void Promise.resolve(bridge.defaultBrowser("take-opens")).then(open).catch(() => {});
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, []);

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
    // A workflow's Undo and Redo end when it closes.
    const openIds = new Set(targets.map(target => target.id));
    for (const [id, session] of documentSessionsRef.current) {
      if (openIds.has(id) || session.graphHistory.current === EMPTY_GRAPH_HISTORY) continue;
      session.graphHistory.current = EMPTY_GRAPH_HISTORY;
      graphHistoryChanged();
    }
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
  }, [activeWorkflowId, workflows, codeOpenPaths, workflowTabs, documentSession, graphHistoryChanged]);

  // Leaving a swarm or organization closes it, so its sidebar row is no longer
  // marked open.
  function showMainContent(next) {
    setMainContent(next);
    if (next === "rem") setThreadsRequested(false);
    if (next !== "swarms") setSelectedSwarm(null);
    if (next !== "organizations") setSelectedOrganization(null);
  }

  function showEditorContent() {
    showMainContent(EDITOR_CONTENT);
  }

  // Background opens prepare the graph tab without replacing an open workspace such as Rem's home.
  function openWorkflowGraph(target, { background = false } = {}) {
    if (!target?.id) return;
    if (!background) {
      setMainContent(EDITOR_CONTENT);
      setSelectedSwarm(null);
      setSelectedOrganization(null);
    }
    const path = `workflow-graph:${encodeURIComponent(target.id)}`;
    setWorkflowTabs(current => ({ ...current, [path]: { kind: "workflow", workflowId: target.id,
      name: target.name, sourcePath: target.sourcePath, projectRoot: target.projectRoot, contextLabel: projectNameFromPath(target.projectRoot) } }));
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
    if ((initialStudioSession.view || settings.general.defaultView) === "graph" && activeWorkflow) openWorkflowGraph(activeWorkflow, { background: true });
  }, [loadState.loading, activeWorkflow, initialStudioSession.view, settings.general.defaultView]);

  useEffect(() => {
    const tab = workflowTabs[activeCodePath];
    const target = tab ? workflows.find(item => item.id === tab.workflowId) : workflows.find(item => samePath(item.sourcePath, activeCodePath));
    if (target && target.id !== activeWorkflowId) setActiveWorkflowId(target.id);
    if (activeCodePath) setStudioView(tab ? "graph" : "code");
  }, [activeCodePath, activeWorkflowId, workflowTabs, workflows]);

  function activateEditorPath(path) {
    showEditorContent();
    setActiveCodePath(path);
    const target = workflowTabs[path] ? workflows.find(item => item.id === workflowTabs[path].workflowId)
      : workflows.find(item => samePath(item.sourcePath, path));
    if (target) setActiveWorkflowId(target.id);
    setStudioView(workflowTabs[path] ? "graph" : "code");
  }

  function changeStudioView(nextView) {
    if (nextView === "graph") { openWorkflowGraph(activeWorkflow); return; }
    showEditorContent();
    setStudioView("code");
    setCodeEditorOpened(true);
    if (workflowTabs[activeCodePath] && activeWorkflow?.sourcePath) openCodeFile(activeWorkflow.sourcePath);
  }

  function openCodeFile(path, options = {}) {
    if (!path) return;
    showEditorContent();
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

  // The Browser plugin in front. It has no side pane; Rem and threads stay on the right.
  function showBrowserContent() {
    setSidebarActivity(BROWSER_CONTENT);
    showMainContent(BROWSER_CONTENT);
  }

  function openIntegratedBrowser(initialUrl, options = {}) {
    const requestedUrl = initialUrl || settings.browser.homepage || "about:blank";
    // Background tabs join the tab bar without changing the user's view.
    if (!options.background) showBrowserContent();
    if (!options.newTab && !options.private && !options.engine && browserOpenPaths.length) {
      const targetPath = browserTabs[activeBrowserPath] ? activeBrowserPath : browserOpenPaths.at(-1);
      setActiveBrowserPath(targetPath);
      return targetPath;
    }
    const opening = browserTabOpening({ paths: browserOpenPaths, tabs: browserTabs, defaultEngine: settings.browser.engine, engine: options.engine, privateTab: options.private === true });
    if (opening.reuse) {
      // Raticode (gecko) is one Firefox window: the address opens as a Firefox tab there.
      if (/^https?:/i.test(String(initialUrl || ""))) {
        setBrowserTabs((current) => current[opening.reuse]
          ? { ...current, [opening.reuse]: { ...current[opening.reuse], pendingUrls: [...(current[opening.reuse].pendingUrls || []), initialUrl].slice(-25) } }
          : current);
      } else if (options.newTab) {
        // New tab with no address: Firefox opens an empty tab of its own.
        setBrowserTabs((current) => current[opening.reuse]
          ? { ...current, [opening.reuse]: { ...current[opening.reuse], geckoNewTabs: (current[opening.reuse].geckoNewTabs || 0) + 1 } }
          : current);
      }
      if (!options.background) setActiveBrowserPath(opening.reuse);
      return opening.reuse;
    }
    const path = createBrowserTabPath();
    setBrowserTabs((current) => ({
      ...current,
      [path]: {
        engine: opening.engine,
        error: "",
        focusLocation: !options.background && opening.engine !== "gecko" && (options.focusLocation ?? !initialUrl),
        loading: true,
        title: options.title || "",
        url: requestedUrl,
        ...(options.private ? { private: true } : {}),
        ...(options.history ? { history: options.history } : {}),
      },
    }));
    setBrowserOpenPaths((current) => mergeCodeOpenPaths(current, [path]));
    if (options.background) return path;
    setActiveBrowserPath(path);
    return path;
  }

  function clearGeckoPending(path, urls = []) {
    const sent = new Set(urls);
    setBrowserTabs((current) => {
      const tab = current[path];
      if (!tab?.pendingUrls?.length) return current;
      const pendingUrls = tab.pendingUrls.filter((url) => !sent.has(url));
      return { ...current, [path]: { ...tab, pendingUrls } };
    });
  }

  // Ctrl+Shift+T brings back the most recently closed web tab with its history.
  function reopenClosedBrowserTab() {
    const tab = closedBrowserTabsRef.current.pop();
    if (!tab) return "";
    return openIntegratedBrowser(tab.url, { newTab: true, focusLocation: false, title: tab.title, history: tab.history, ...(tab.engine ? { engine: tab.engine } : {}) });
  }
  reopenClosedBrowserTabRef.current = reopenClosedBrowserTab;

  openIntegratedBrowserRef.current = openIntegratedBrowser;

  // A link or web page another app opened with Raticode (Raticode is the
  // computer's default browser). See lib/externalOpens.js for where it goes.
  async function openExternalItem(item) {
    const engine = settings.browser.externalEngine;
    const geckoTabOpen = Boolean(browserTabOpening({ paths: browserOpenPaths, tabs: browserTabs, engine: "gecko" }).reuse);
    let geckoStatus = null;
    if (item?.kind === "url" && engine === "gecko") {
      try { geckoStatus = await window.goferBrowser?.gecko?.("status"); } catch { geckoStatus = null; }
    }
    const plan = externalOpenPlan(item, { engine, geckoStatus, geckoTabOpen });
    if (!plan) return;
    if (plan.kind === "file") {
      await openLinkedCodeFileRef.current?.(plan.path, { preview: true, external: true });
      return;
    }
    openIntegratedBrowserRef.current?.(plan.url, { newTab: true, focusLocation: false, engine: plan.engine });
    if (plan.notice) setTopBarNotice({ type: "info", message: plan.notice });
  }
  const openExternalItemRef = useRef(null);
  openExternalItemRef.current = openExternalItem;
  closeAgentBrowserTabRef.current = (id) => {
    const path = Object.keys(browserTabs).find((candidate) => browserTabs[candidate].agentSessionId === id);
    if (path) closeBrowserTabs([path]);
  };

  useEffect(() => {
    // Rem's tabs join the tab strip without being selected, so the user's
    // current view and keyboard focus stay put.
    const unsubscribeOpen = window.goferBrowser?.onAgentOpen?.((request) => {
      if (typeof request?.id !== "string" || !request.id) return;
      const path = createBrowserTabPath();
      // Rem's tab streams from Firefox, Chrome or Edge when one is the default browser.
      const engine = webBrowserEngine(request.engine);
      const tab = { ...agentBrowserTab(request), agentEngine: engine, engine };
      setBrowserTabs((current) => ({ ...current, [path]: tab }));
      setBrowserOpenPaths((current) => mergeCodeOpenPaths(current, [path]));
    });
    const unsubscribeClose = window.goferBrowser?.onAgentClose?.((request) => {
      if (request?.id) closeAgentBrowserTabRef.current?.(request.id);
    });
    return () => {
      unsubscribeOpen?.();
      unsubscribeClose?.();
    };
  }, []);

  // The main process applies Rem's site rules, sign-in sharing and downloads.
  useEffect(() => {
    void Promise.resolve(window.goferBrowser?.setPolicy?.(browserPolicyFromSettings(settings.browser))).catch(() => {});
  }, [settings.browser]);

  // On Linux, Raticode (gecko) needs Raticode to start in X11 mode. It's wanted
  // while Gecko is the default browser or a Gecko tab is open.
  const geckoWanted = webBrowserEngine(settings.browser.engine) === "gecko" || browserOpenPaths.some((path) => browserTabs[path]?.engine === "gecko");
  useEffect(() => {
    void Promise.resolve(window.goferBrowser?.gecko?.("select", { selected: geckoWanted })).catch(() => {});
  }, [geckoWanted]);

  // The browser sends the page text with the element and selection first.
  function askRemAboutPage({ url, title, text = "" }) {
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: url, title, text, draft: "",
    } }));
  }

  // Raticode (gecko) is Firefox's own window; the tab in front of it is shared.
  function shareGeckoTabWithRem() {
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: "", title: "", text: "", geckoShare: true,
    } }));
  }

  function shareBrowserTabWithRem({ id, url, title }) {
    if (!id) return;
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: url, title, text: "", browserSessionId: id,
      draft: `I shared the browser tab “${title || url}” with you. Use it to `,
    } }));
  }

  function updateBrowserTab(path, nextState) {
    // Persist only what restores the tab; live state stays in the workspace.
    const saved = Object.fromEntries(Object.entries(nextState || {}).filter(([key]) => BROWSER_TAB_SAVED_FIELDS.has(key)));
    if (!Object.keys(saved).length) return;
    setBrowserTabs((current) => current[path]
      ? { ...current, [path]: { ...current[path], ...saved } }
      : current);
  }

  async function openMarkdownFileLink(href, sourcePath) {
    try {
      const target = await resolveMarkdownFileLinkTarget(
        sourcePath,
        href,
      );
      if (target?.path) await openLinkedCodeFile(target.path, { ...target, preview: true, fromLink: true });
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
      if (info?.isDirectory) {
        const folder = info.path || path;
        // A link in a message or a Markdown file can point anywhere. Opening a
        // folder makes it a trusted project Rem can work in, so a folder
        // outside the open project is opened only after the user agrees, and
        // isn't added to recent projects.
        const outside = options.fromLink && !(activeProjectRootRef.current && pathWithin(folder, activeProjectRootRef.current));
        if (outside && !await confirmAction({
          message: `Open "${folder}" as a project?\n\nA link points to this folder, which is outside the open project. Opening it lets Raticode and Rem work in it.`,
          confirmLabel: "Open project",
        })) return;
        await openProjectAtPath(folder, { rememberProject: !outside });
        setStudioView("code");
        setSidebarActivity("files");
        setProjectPaneVisible(true);
        return;
      }
      if (info && !info.isFile) throw new Error(`The link does not point to a file: ${path}`);
      const filePath = info?.path || path;
      setExternalFilePaths((current) => {
        // Only an explicit open (recent files, the terminal editor) clears the flag;
        // a link never turns a file from another app back into a scripted preview.
        const clear = options.external !== true && !options.fromLink;
        if (options.external === true ? current.has(filePath) : !clear || !current.has(filePath)) return current;
        const next = new Set(current);
        if (options.external === true) next.add(filePath);
        else next.delete(filePath);
        return next;
      });
      openCodeFile(filePath, options);
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
      await openLinkedCodeFile(resolvedPath, { fromLink: true });
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

  // Closing web tabs in the Browser plugin. Ctrl+Shift+T can bring back the
  // user's own tabs, in the browser they ran in.
  function closeBrowserTabs(paths) {
    const closing = new Set(paths.filter(Boolean));
    if (!closing.size) return;
    for (const path of closing) {
      const tab = browserTabs[path];
      if (tab && !tab.agentSessionId && !tab.private && /^https?:/i.test(String(tab.url || ""))) {
        closedBrowserTabsRef.current = [...closedBrowserTabsRef.current, { url: tab.url, title: tab.title || "", history: tab.history || null, engine: tab.engine || "" }].slice(-25);
      }
    }
    setBrowserOpenPaths((current) => {
      const next = current.filter((candidate) => !closing.has(candidate));
      setActiveBrowserPath((currentActive) => {
        if (!closing.has(currentActive)) return currentActive;
        const index = current.indexOf(currentActive);
        return next[Math.min(index, next.length - 1)] ?? next.at(-1) ?? "";
      });
      return next;
    });
    setBrowserTabs((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !closing.has(path)),
    ));
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
    setWorkflowTabs(current => Object.fromEntries(Object.entries(current).filter(([path]) => !closing.has(path))));
  }

  function closeActiveCodeFile() {
    activeWorkspaceRef.current?.closeActive?.();
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

  function openProjectAtPath(projectRoot, options = {}) {
    const pending = projectOpenPendingRef.current;
    if (pending && samePath(pending.root, projectRoot)) return pending.promise;
    const request = { root: projectRoot };
    request.promise = performProjectOpen(projectRoot, options).finally(() => {
      if (projectOpenPendingRef.current === request) projectOpenPendingRef.current = null;
    });
    projectOpenPendingRef.current = request;
    return request.promise;
  }

  async function performProjectOpen(projectRoot, { rememberProject = false, focusPath = "" } = {}) {
    const requestId = projectOpenRequestRef.current + 1;
    projectOpenRequestRef.current = requestId;
    ++workflowLoadRequestRef.current;
    workflowLoadAbortRef.current?.abort();
    projectOpenAbortRef.current?.abort();
    const controller = new AbortController();
    projectOpenAbortRef.current = controller;
    setOpeningProjectRoot(projectRoot);
    setProjectError("");
    let rememberedRoot = "";
    const rememberOpenedProject = (openedRoot) => {
      if (!rememberProject || samePath(rememberedRoot, openedRoot)) return;
      rememberedRoot = openedRoot;
      void withProjectOpenTimeout(Promise.resolve().then(() => window.goferDesktop?.workspace?.gitWorktrees?.(openedRoot)), 3000)
        .catch(() => null).then(payload => {
          if (projectOpenRequestRef.current !== requestId || !samePath(rememberedRoot, openedRoot)) return;
          const mainProjectRoot = mainWorktreeRoot(payload, openedRoot);
          const aliases = [openedRoot, ...(payload?.worktrees || []).map(worktree => worktree.path)];
          recentProjectValidatorRef.current.remember(mainProjectRoot, openedRoot);
          setRecentProjectRoots(current => rememberRecentProject(
            current.filter(root => !aliases.some(alias => samePath(alias, root))), mainProjectRoot,
          ));
          setLastWorktreeByProject(current => withPathValue(
            Object.fromEntries(Object.entries(current).filter(([root]) => !aliases.some(alias => samePath(alias, root)))),
            mainProjectRoot, openedRoot,
          ));
        });
    };
    try {
      const discoveredPayloads = await withProjectOpenTimeout(discoverProjectWorkflows(projectRoot, {
        signal: controller.signal,
        onTrustedRoot: root => {
          if (projectOpenRequestRef.current !== requestId) return;
          setActiveProjectRoot(root);
          setCodeEditorOpened(true);
          rememberOpenedProject(root);
        },
        onResolvedRoot: root => { projectRoot = root; },
      }));
      if (projectOpenRequestRef.current !== requestId) return null;
      // Also handle canonical roots returned by web or older desktop bridges.
      rememberOpenedProject(projectRoot);
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
      if (projectOpenRequestRef.current !== requestId || error?.name === "AbortError") return null;
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
      setCodeOpenPaths((current) => [...new Set(current.map((path) =>
        replacePathPrefix(path, change.sourcePath, change.path, change.isDirectory),
      ))]);
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

  // Resolves to the saved document, or to { error } when the edit wasn't saved so an
  // inspector field can show why next to the text it kept.
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
          const message = "Graph changes were saved. Your newer source edits are still unsaved.";
          setTopBarNotice({ type: "error", message });
          return { error: message };
        }
        const history = session.graphHistoryRef.current;
        session.graphHistoryRef.current = {
          undo: pushGraphHistoryEntry(history.undo, { label: graphEditLabel(mutations), sourceBefore: currentDocument.source, revisionAfter: savedDocument.savedRevision }),
          redo: [],
        };
        graphHistoryChanged();
        setTopBarNotice({ type: "success", message: "Updated workflow.rattish" });
        void loadWorkflows({ silent: true });
        return savedDocument;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to edit workflow.rattish";
        setTopBarNotice({ type: "error", message });
        return { error: message };
      }
    });
  }

  // Undo ("undo") or Redo ("redo") the newest graph edit by saving the text from before
  // it. It applies only while workflow.rattish is still at the revision that edit saved,
  // so a change from the code view, Rem or another program is never overwritten.
  function stepGraphHistory(target, direction) {
    if (target?.sourceFormat !== "rattish") return Promise.resolve(null);
    return queueWorkflowDocumentWrite(target, async () => {
      const session = documentSession(target.id);
      const [from, to] = direction === "redo" ? ["redo", "undo"] : ["undo", "redo"];
      const entry = session.graphHistoryRef.current[from].at(-1);
      if (!entry) return null;
      function refuse() {
        session.graphHistoryRef.current = EMPTY_GRAPH_HISTORY;
        graphHistoryChanged();
        setTopBarNotice({ type: "error", message: `workflow.rattish changed since that edit, so it can't be ${from === "undo" ? "undone" : "redone"} here.` });
        return null;
      }
      const currentDocument = session.rattishEditorStateRef.current?.document;
      if (!currentDocument || currentDocument.dirty || currentDocument.savedRevision !== entry.revisionAfter) return refuse();
      try {
        const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(target.id)}/document/save`), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: entry.sourceBefore, expectedRevision: currentDocument.savedRevision }),
        });
        const payload = await response.json();
        if (response.status === 409) return refuse();
        if (!response.ok) throw new Error(payload.error || `Unable to ${from} ${entry.label}`);
        const history = session.graphHistoryRef.current;
        session.graphHistoryRef.current = {
          [from]: history[from].slice(0, -1),
          [to]: pushGraphHistoryEntry(history[to], { label: entry.label, sourceBefore: currentDocument.source, revisionAfter: payload.document.savedRevision }),
        };
        graphHistoryChanged();
        const savedDocument = acceptWorkflowWrite(target, currentDocument, payload.document);
        setTopBarNotice(savedDocument
          ? { type: "success", message: `${from === "undo" ? "Undid" : "Redid"} ${entry.label}` }
          : { type: "error", message: "Graph changes were saved. Your newer source edits are still unsaved." });
        void loadWorkflows({ silent: true });
        return savedDocument;
      } catch (error) {
        setTopBarNotice({ type: "error", message: error instanceof Error ? error.message : `Unable to ${from} ${entry.label}` });
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
    if (pathKey(projectRoot) !== pathKey(activeProjectRootRef.current)) return;
    if (!silent) {
      setLoadState({ loading: true, error: "" });
    }
    const requestId = ++workflowLoadRequestRef.current;
    const projectGeneration = projectOpenRequestRef.current;
    const previous = workflowLoadAbortRef.current;
    const controller = previous && !previous.signal.aborted && samePath(previous.projectRoot, projectRoot)
      ? previous : new AbortController();
    controller.projectRoot = projectRoot;
    workflowLoadAbortRef.current = controller;
    const discovery = discoverProject && projectRoot
      ? discoverProjectWorkflows(projectRoot, { signal: controller.signal }) : null;
    if (previous !== controller) previous?.abort();
    try {
      if (discoverProject && projectRoot) {
        await discovery;
      }
      controller.signal.throwIfAborted();
      const payload = await shareCancellable("workflow-list", async signal => {
        const response = await fetch(apiUrl("/workflows"), { signal });
        if (!response.ok) throw new Error(`Workflow API returned ${response.status}`);
        return response.json();
      }, controller.signal);
      if (pathKey(projectRoot) !== pathKey(activeProjectRootRef.current) || projectGeneration !== projectOpenRequestRef.current || requestId !== workflowLoadRequestRef.current) return;
      const payloadDataDir = payload.dataDir ?? "";
      setLegacyTomlFiles(legacyTomlFilesFromPayload(payload));
      const nextWorkflows = listedRattishWorkflows(payload)
        .filter((workflow) => !deletedWorkflowIdsRef.current.has(workflow.id))
        .map((workflow) => summarizeWorkflow(workflow, payloadDataDir));
      setWorkflows((current) => {
        const refreshedWorkflows = nextWorkflows.map((workflow) => {
          const localWorkflow = current.find((candidate) => candidate.id === workflow.id);
          return localWorkflow
            ? summarizeWorkflow(mergeSavedWorkflow(localWorkflow, workflow), payloadDataDir)
            : workflow;
        });
        const mergedWorkflows = refreshedWorkflows;

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
      setLoadState({ loading: false, error: (payload.errors ?? []).map(item => `${item.path}: ${item.message}`).join("\n") });
    } catch (error) {
      if (controller.signal.aborted || pathKey(projectRoot) !== pathKey(activeProjectRootRef.current) || projectGeneration !== projectOpenRequestRef.current || requestId !== workflowLoadRequestRef.current) return;
      setLoadState({
        loading: false,
        error: error instanceof Error ? error.message : "Unable to load workflows",
      });
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
  }, [loadDoctor]);

  useEffect(() => {
    const projectRoot = activeProjectRoot || activeWorkflow?.projectRoot || "";
    return startWorkspacePolling({
      refreshLive: () => Promise.allSettled([loadWorkflows({ silent: true })]),
      discover: () => loadWorkflows({ discoverProject: Boolean(projectRoot), projectRoot, silent: true }),
      doctor: () => loadDoctor({ silent: true }),
    });
  }, [activeProjectRoot, activeWorkflow?.projectRoot, loadDoctor, loadWorkflows]);

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

  useEffect(() => window.goferDesktop?.onPrepareLifecycle?.(async request => {
    flushChatDrafts();
    const dirty = [...textEditorSessions].filter(([, session]) => session.content !== session.savedContent);
    if (!dirty.length) return true;
    return new Promise(resolve => setLifecyclePrompt({ ...request, paths: dirty.map(([path]) => path), resolve }));
  }), []);

  const checkForUpdates = useCallback(async ({ silent = false } = {}) => {
    if (!window.goferUpdates?.check) return;
    if (!silent) setUpdateNotice({ type: "", message: "" });
    setUpdateState((current) => ({
      ...current,
      checking: true,
      error: silent ? current.error : "",
    }));
    try {
      const info = await window.goferUpdates.check();
      setUpdateState((current) => ({ ...current, ...info, checking: false }));
      const notice = updateCheckNotice(info);
      if (notice.type === "error") throw new Error(notice.message);
      if (!silent) setUpdateNotice(notice);
    } catch (error) {
      setUpdateState((current) => ({
        ...current,
        checking: false,
        error: error instanceof Error ? error.message : "Unable to check for updates",
      }));
      if (!silent) {
        setUpdateNotice({
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

  // The main process checks on startup and every 10 minutes while this setting is on.
  useEffect(() => {
    const enabled = Boolean(settings.general.checkForUpdates);
    if (window.goferUpdates?.setAutomatic) {
      window.goferUpdates.setAutomatic(enabled).then((nextState) => {
        if (nextState) setUpdateState((current) => ({ ...current, ...nextState }));
      }).catch(() => {});
    } else if (enabled) {
      checkForUpdates({ silent: true });
    }
  }, [checkForUpdates, settings.general.checkForUpdates]);

  async function applyUpdate(update) {
    if (!window.goferUpdates) return;
    try {
      const nextState = update.downloaded
        ? await window.goferUpdates.installDownloaded()
        : await window.goferUpdates.downloadAndInstall();
      setUpdateState((current) => ({ ...current, ...nextState }));
      if (update.supported === false) setTopBarNotice({ type: "info", message: manualUpdateNotice(update) });
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

  useEffect(() => {
    if (!updateNotice?.message) return undefined;

    const timeoutId = window.setTimeout(() => {
      setUpdateNotice({ type: "", message: "" });
    }, 3500);

    return () => window.clearTimeout(timeoutId);
  }, [updateNotice?.message]);

  const loadLatestLog = useCallback(async (workflowId, { silent = false } = {}) => {
    if (pinnedRunRef.current) return;
    const requestId = logRequestRef.current + 1;
    logRequestRef.current = requestId;
    if (!silent) {
      setLogState((current) => ({ ...current, loading: true, error: "" }));
    }
    try {
      const response = await fetch(workflowLogUrls(workflowId).latest);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (requestId !== logRequestRef.current) return;
      const revision = response.headers?.get("ETag") || "";
      const nextText = payload.log?.logText ?? "";
      const nextPath = payload.log?.logPath ?? null;
      const nextNodeOutputs = payload.log?.nodeOutputs ?? null;
      const nextUsageSummary = payload.log?.usageSummary ?? payload.log?.usage ?? null;
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
          worktree: payload.log?.worktree ?? null,
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
      // Runs started by the API, CLI or Rem reach the toolbar Stop on this faster poll.
      const knownRunIds = new Set(runRegistryRecordsRef.current
        .filter(record => record.workflowId === workflowId)
        .map(record => record.runId));
      if ((payload.runs ?? []).some(run => run.status === "running" && !knownRunIds.has(run.id))) {
        void refreshRunRegistry();
      }
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
  }, [refreshRunRegistry]);

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
      const response = await fetch(
        workflowLogUrls(workflowId, runId, { details: !silent }).selected,
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
        usageSummary: silent
          ? current.usageSummary
          : (payload.log?.usageSummary ?? payload.log?.usage ?? null),
        runEvents: payload.log?.runEvents ?? (silent ? current.runEvents : []),
        runNodes: payload.log?.runNodes ?? (silent ? current.runNodes : {}),
        selectedRunId: runId,
        workflowId,
        graphSnapshot: silent ? current.graphSnapshot : payload.log?.graphSnapshot ?? null,
        worktree: payload.log?.worktree ?? null,
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
        worktree: null,
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

  // presetParameters fills Run dialog fields, such as a file input from the file tree's
  // "Run workflow with this file".
  async function runWorkflowNow(workflow, presetParameters = null) {
    if (runStatesRef.current[workflow.id]?.running) return;
    const preset = presetParameters && typeof presetParameters === "object" && !("nativeEvent" in presetParameters) ? presetParameters : {};
    const workflowToRun = summarizeWorkflow(workflow, dataDir);
    setRunState({ running: true, workflowId: workflowToRun.id, error: "", result: null });
    if (!pinnedRun && activeWorkflowIdRef.current === workflowToRun.id) setLogState((current) => ({
      ...current,
      loading: true,
      error: "",
      selectedRunId: null,
    }));

    try {
      const session = documentSession(workflowToRun.id);
      await followSavedRattishDocument(session, async () => {
        const response = await fetch(apiUrl(`/workflows/${encodeURIComponent(workflowToRun.id)}/document`));
        const payload = await response.json();
        if (!response.ok || !payload.document) throw new Error(payload.error || "Unable to load workflow.rattish before running.");
        return payload.document;
      });
      if (session.rattishEditorStateRef.current?.document?.dirty) {
        const savedDocument = await saveWorkflowDocument(workflowToRun);
        if (!savedDocument) {
          throw new Error("Save workflow.rattish before running the workflow.");
        }
      }
      const savedWorkflow = { ...workflowToRun, expectedSourceRevision: documentSession(workflowToRun.id).rattishEditorStateRef.current?.document?.savedRevision };

      const initialParameters = initialWorkflowParameters(savedWorkflow);
      const previewRequest = workflowPlanRequest(savedWorkflow.id, {}, initialParameters);
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
        parameters: { ...initialParameters, ...preset },
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
    // A new run replaces a reviewed run snapshot so its progress is visible.
    if (pinnedRunRef.current?.workflowId === workflow.id) {
      pinnedRunRef.current = null;
      setPinnedRun(null);
    }
    setRunState({ running: true, workflowId: workflow.id, error: "", result: null });
    setLogState((current) => ({
      ...current,
      loading: true,
      error: "",
      selectedRunId: null,
    }));
    try {
      const runRequest = workflowRunRequest(workflow.id, {
        background: true,
        expectedRevision: workflow.expectedSourceRevision,
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
      if (!pinnedRunRef.current && activeWorkflowIdRef.current === workflow.id) setLogState({
        loading: false,
        error: "",
        text: payload.run?.logText ?? "",
        path: payload.run?.logPath ?? null,
        nodeOutputs: payload.run?.nodeOutputs ?? null,
        nodeOutputsTruncated: Boolean(payload.run?.nodeOutputsTruncated),
        nodeOutputsMaxBytes: payload.run?.nodeOutputsMaxBytes ?? null,
        usageSummary: payload.run?.usageSummary ?? payload.run?.usage ?? null,
        runEvents: payload.run?.runEvents ?? [],
        runNodes: payload.run?.runNodes ?? {},
        runs: logState.runs,
        selectedRunId: null,
        worktree: payload.run?.worktree ?? null,
      });
      if (!payload.run?.success) {
        setTopBarNotice({
          type: "error",
          message: workflowRunFailureMessage(payload.run),
        });
      }
      loadRunLogs(workflow.id);
      // Finished runs carry no run ID, so rescan for the record the list shows.
      void runRegistry.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to run workflow";
      setRunState({ running: false, workflowId: workflow.id, error: message, result: null });
      loadLatestLog(workflow.id, { silent: true });
      loadRunLogs(workflow.id, { silent: true });
    }
  }

  async function decideApproval(workflow, approval, decision, notes = "", by = "ui", answer = {}) {
    const choice = answer.choice ?? null;
    const reply = answer.reply ?? "";
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
          body: JSON.stringify(choice ? { by, notes, choice, reply } : { by, notes, reply }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setTopBarNotice({
        type: "success",
        message: choice
          ? `Answer “${choice}” recorded`
          : decision === "approved"
            ? "Approval recorded"
            : "Rejection recorded",
      });
      setApprovalState((current) => ({
        ...current,
        approvals: current.approvals.map((candidate) =>
          candidate.runId === approval.runId && candidate.nodeId === approval.nodeId
            ? (payload.approval ?? {
                ...candidate,
                status: "decided",
                decision: { decision, decidedBy: by, notes, choice, reply },
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
      // The approval may have been closed because its run ended.
      loadApprovals(workflow.id, { silent: true });
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

  function askForSavedInputs(action, workflowId, names) {
    if (!names?.length) return Promise.resolve({});
    const specs = workflows.find((workflow) => workflow.id === workflowId)?.inputs ?? {};
    return new Promise((resolve) => {
      setSavedInputsPrompt({
        action,
        names,
        resolve: (values) => {
          setSavedInputsPrompt(null);
          resolve(values ? savedInputValues(values, specs) : null);
        },
      });
    });
  }

  async function rerunWorkflowRunLog(workflowId, runId) {
    if (!workflowId || !runId) return;
    const rerun = logState.runs?.find((run) => run.id === runId)?.rerun;
    const inputs = await askForSavedInputs("Run again", workflowId, rerun?.needsInputs);
    if (!inputs) return;
    setRunState({ running: true, workflowId, error: "", result: null, resumingRunId: runId });
    try {
      const request = workflowRerunRequest(workflowId, runId, inputs);
      const response = await fetch(request.url, request.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      setRunState({ running: false, workflowId, error: "", result: payload.run });
      recordWorkflowRun(workflows.find((workflow) => workflow.id === workflowId) ?? { id: workflowId }, payload.run);
      void runRegistry.refresh();
      setTopBarNotice({ type: "success", message: "Started again with the same inputs" });
      void loadRunLogs(workflowId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to run the workflow again";
      setRunState({ running: false, workflowId, error: message, result: null });
      loadRunLogs(workflowId, { silent: true });
    }
  }

  // Run from here: reuse the newest fitting run's results for the unchanged steps
  // upstream of nodeId, then run nodeId and everything after it from the saved source.
  async function runWorkflowFromNode(workflow, nodeId) {
    if (!workflow?.id || !nodeId || runStatesRef.current[workflow.id]?.running) return;
    const workflowId = workflow.id;
    setRunState({ running: true, workflowId, error: "", result: null });
    try {
      const session = documentSession(workflowId);
      if (session.rattishEditorStateRef.current?.document?.dirty) {
        const savedDocument = await saveWorkflowDocument(summarizeWorkflow(workflow, dataDir));
        if (!savedDocument) throw new Error("Save workflow.rattish before running from a step.");
      }
      const expectedRevision = documentSession(workflowId).rattishEditorStateRef.current?.document?.savedRevision;
      const previewRequest = workflowRunRequest(workflowId, { dryRun: true, fromNode: nodeId, expectedRevision });
      const previewResponse = await fetch(previewRequest.url, previewRequest.options);
      const previewPayload = await previewResponse.json();
      if (!previewResponse.ok) {
        throw new Error(previewPayload.error || `Workflow API returned ${previewResponse.status}`);
      }
      const plan = previewPayload.plan ?? {};
      setRunState({ running: false, workflowId, error: "", result: null });
      const confirmation = runFromConfirmation(plan);
      if (confirmation && !await confirmAction({ message: confirmation, confirmLabel: "Run from here" })) return;
      const inputs = await askForSavedInputs("Run from here", workflowId, plan.needsInputs);
      if (!inputs) return;
      setRunState({ running: true, workflowId, error: "", result: null });
      const request = workflowRunRequest(workflowId, {
        background: true,
        expectedRevision: plan.revision || expectedRevision,
        fromNode: plan.fromNode || nodeId,
        parameters: inputs,
        reuseRunId: plan.reuseRunId,
      });
      const response = await fetch(request.url, request.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (pinnedRunRef.current?.workflowId === workflowId) {
        pinnedRunRef.current = null;
        setPinnedRun(null);
      }
      setRunState({ running: false, workflowId, error: "", result: payload.run });
      recordWorkflowRun(workflows.find((candidate) => candidate.id === workflowId) ?? { id: workflowId }, payload.run);
      void runRegistry.refresh();
      setTopBarNotice({ type: "success", message: runFromNotice(plan) });
      void loadRunLogs(workflowId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to run from this step";
      setRunState({ running: false, workflowId, error: message, result: null });
      loadRunLogs(workflowId, { silent: true });
    }
  }

  async function resumeWorkflowRunLog(workflowId, runId, options = {}) {
    if (!workflowId || !runId) return;
    // Rattish runs resume as a new background run; steps cut off mid-run start over.
    const resume = logState.runs?.find((run) => run.id === runId)?.resume;
    const rerun = resume?.rerunNodes ?? [];
    if (resume && rerun.length && !await confirmAction({
      message: `Resume this run? These steps run again: ${rerun.join(", ")}.`,
      confirmLabel: "Resume run",
    })) return;
    // Password inputs the run didn't save; its other inputs and trigger events are reused.
    const inputs = resume ? await askForSavedInputs("Resume", workflowId, resume.needsInputs) : {};
    if (!inputs) return;

    setRunState({ running: true, workflowId, error: "", result: null, resumingRunId: runId });
    setLogState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const request = workflowResumeRequest(workflowId, runId, resume ? { background: true, inputs } : options);
      const response = await fetch(request.url, request.options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }
      if (["running", "queued"].includes(payload.run?.status)) {
        setRunState({ running: false, workflowId, error: "", result: payload.run });
        recordWorkflowRun(workflows.find((workflow) => workflow.id === workflowId) ?? { id: workflowId }, payload.run);
        void runRegistry.refresh();
        setLogState((current) => ({ ...current, loading: false }));
        void loadRunLogs(workflowId);
        return;
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
        usageSummary: payload.run?.usageSummary ?? payload.run?.usage ?? null,
        runEvents: payload.run?.runEvents ?? [],
        runNodes: payload.run?.runNodes ?? {},
        runs: logState.runs,
        selectedRunId: null,
        worktree: payload.run?.worktree ?? null,
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

  async function convertLegacyTomlWorkflow(file, projectRoot) {
    setTomlConversion({ file, saving: true, error: "", result: null });
    try {
      const result = await convertTomlWorkflow({ path: file.path, projectRoot });
      const converted = summarizeWorkflow(result.workflow, dataDir);
      deletedWorkflowIdsRef.current.delete(converted.id);
      setWorkflows((current) => [
        ...current.filter((candidate) => candidate.id !== converted.id),
        converted,
      ]);
      setRecentProjectRoots((current) => rememberRecentProject(current, projectRoot));
      openWorkflowGraph(converted);
      setTomlConversion({ file, saving: false, error: "", result });
      void loadWorkflows({ silent: true });
    } catch (error) {
      setTomlConversion({
        file,
        saving: false,
        error: error instanceof Error ? error.message : "Unable to convert the workflow",
        result: null,
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
          starter: options.starter || undefined,
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
      if (workflow?.sourceFormat !== "rattish") return;
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
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Workflow validation failed",
      });
    }
  }

  async function importWorkflow(file, options = {}) {
    if (!file) return false;
    try {
      if (!isRaticodeFile(file)) {
        throw new Error(/\.toml$/i.test(file.name ?? "")
          ? "Raticode no longer runs TOML workflows. Import takes a .raticode workflow bundle."
          : "Choose a .raticode workflow bundle to import.");
      }
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
      if (!await confirmAction({
        message: `Import "${preview.workflowName}" into ${projectNameFromPath(projectRoot)}?\n\n`
          + `${preview.files.length} workflow file${preview.files.length === 1 ? "" : "s"} will be added under .raticode.`,
        confirmLabel: "Import workflow",
      })) return false;
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
    const defaultDirectory = workflow.projectRoot || dataDir;
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
      if (workflow.projectRoot) {
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
    const folder = workflowFolderLabel(workflow);
    // A workflow found outside .raticode shares its folder with project files.
    const detail = folder
      ? `\n\nThis deletes workflow.rattish and workflow.metadata.json in ${folder}. Other files in that folder stay.`
      : "";
    const pendingWorktrees = await pendingRunWorktreeCount(workflow.id, { url: apiUrl });
    const worktreeDetail = runWorktreeDeleteNote(pendingWorktrees);
    if (!await confirmAction({
      message: `Delete workflow "${workflow.name}"?${detail}${worktreeDetail}`,
      confirmLabel: "Delete workflow",
      danger: true,
    })) return;

    try {
      setCreateState({ saving: false, error: "" });
      deletedWorkflowIdsRef.current.add(workflow.id);
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
              worktree: null,
            }
          : current,
      );

      const response = await fetch(
        apiUrl(
          `/workflows/${encodeURIComponent(workflow.id)}`,
        ),
        {
          method: "DELETE",
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Workflow API returned ${response.status}`);
      }

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
        nextActiveWorkflowIdAfterDelete(workflows, currentId, workflow.id),
      );
      const removedWorktrees = Number(payload.runWorktreesRemoved) || 0;
      setTopBarNotice({
        type: "success",
        message: removedWorktrees
          ? `Deleted ${workflow.name} and ${removedWorktrees} run worktree${removedWorktrees === 1 ? "" : "s"}`
          : `Deleted ${workflow.name}`,
      });
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
      // An open editor must take the new source itself, or its next save
      // would conflict with the renamed file.
      if (documentSession(workflow.id).rattishEditorStateRef.current?.document) {
        const renamedDocument = await mutateActiveRattish(
          [{ kind: "set_field", target: "workflow", field: "name", value: nextName.trim() }],
          workflow,
        );
        if (!renamedDocument || renamedDocument.error) return;
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
      setTopBarNotice({ type: "success", message: "Data folder changed" });
      await loadWorkflows();
    } catch (error) {
      setTopBarNotice({
        type: "error",
        message: error instanceof Error ? error.message : "Unable to change the data folder",
      });
    }
  }

  const panelDocument = rattishEditorState?.document;
  const sourceDiagnostics = [
    ...(panelDocument?.diagnostics ?? []),
    ...(panelDocument?.preflight?.diagnostics ?? []),
  ];
  const panelRunResult = runState?.result?.workflowId === activeWorkflow?.id
    ? runState.result
    : null;
  const panelRunEvents = logState.runEvents?.length
    ? logState.runEvents
    : panelRunResult?.runEvents ?? [];
  const panelDiagnostics = [
    ...sourceDiagnostics,
    ...workflowRunProblems(Object.keys(logState.runNodes || {}).length ? logState.runNodes : panelRunResult?.runNodes, panelRunEvents),
  ];
  const panelWorkflowId = pinnedRun?.workflowId || (activeWorkflow?.sourceFormat === "project"
    ? ""
    : activeWorkflow?.id ?? "");
  // Each run says whether it can resume; the timeline disables Resume for one that can't.
  const panelWorkflowResumable = Boolean(panelWorkflowId);
  const panelProjectRoot = activeProjectRoot || "";
  // Ask Rem about this failure: the run's summary, or a source problem, goes in Rem's
  // message box as a file. Nothing is sent until the person sends it.
  const panelRunId = shownRunId(logState);
  const askRemAboutPanelRun = (nodeId = "") => askRemAboutRunFailure({ workflowId: panelWorkflowId, runId: panelRunId, nodeId });
  const askRemAboutPanelDiagnostic = (diagnostic) => {
    if (diagnostic.runtime) return askRemAboutPanelRun(diagnostic.nodeId || "workflow");
    askRemAboutDiagnostic(diagnostic, { workflowName: activeWorkflow?.name ?? "", workflowPath: activeWorkflow?.sourcePath ?? "", diagnostics: sourceDiagnostics });
    return undefined;
  };
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
    const projectRoot = target?.projectRoot || tab.projectRoot;
    return [path, { ...tab, name: target?.name ?? tab.name, dirty, status,
      ...(projectRoot ? { contextLabel: projectNameFromPath(projectRoot) } : {}),
      unread: summary.unread.length > 0, unreadFailure: summary.unreadFailures.length > 0,
      statusLabel: `${status}${summary.active.length > 1 ? ` · ${summary.active.length} runs` : ""}${summary.unreadFailures.length ? " · Unread failure" : ""}`,
      action, actionLabel: action === "stop" ? `Stop run ${single.runId}` : action === "review" ? "Review workflow runs" : dirty ? "Save and run" : "Run workflow" }];
  }));

  reviewWorkflowRunRef.current = reviewWorkflowRun;
  async function reviewWorkflowRun(record) {
    let target = workflows.find(item => item.id === record.workflowId);
    if (!target && record.projectPath) {
      const payload = await openProjectAtPath(record.projectPath);
      target = payload?.discovered?.find(item => item.id === record.workflowId);
    }
    if (!target) { setTopBarNotice({ type: "error", message: "This workflow is unavailable. Reopen its project to review the run." }); return; }
    openWorkflowGraph(target);
    if (record.status === "submitting") return;
    setPinnedRun(record);
    if (await loadRunLog(record.workflowId, record.runId)) runRegistry.review(record.key);
    window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", { detail: { tab: "timeline" } }));
  }

  async function stopExactWorkflowRun(workflow) {
    const active = workflowRunSummary(runRegistry.records, workflow.id).active;
    if (active.length !== 1 || !exactWorkflowRunStopPath(active[0])) { window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", { detail: { tab: "runs", open: true } })); return; }
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
      if (action === "review") window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", { detail: { tab: "runs", open: true } }));
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
    const currentSourceMatchesRun = Boolean(
      document && !document.dirty && logState.workflowId === target.id
      && logState.graphSnapshot?.sourceFingerprint === document.sourceRevision,
    );
    const graphRunState = runStatesById[target.id] || { running: false, error: "", result: null };
    return <WorkflowGraphPane
      active={active} visible={visible} workflow={target}
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
        dataDir={dataDir} settings={settings}
        workflow={snapshot ? autoLayoutWorkflow({ ...target, ...snapshot }) : rattishGraphWorkflow(target, document)} rattishDocument={snapshot ? null : document}
        active={active}
        readOnly={reviewingRun || !rattishGraphIsValid(document)}
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
        {...workflowStopControl(workflowRunSummary(runRegistry.records, target.id).active)}
        onLoadLatestLog={() => loadLatestLog(target.id)}
        onSelectRunLog={(runId) => { setPinnedRun({ workflowId: target.id, runId }); void loadRunLog(target.id, runId); }}
        onStopRunLog={(runId) => stopWorkflowRunLog(target.id, runId)}
        onResumeRunLog={(runId, options) => resumeWorkflowRunLog(target.id, runId, options)}
        onRerunRunLog={(runId) => rerunWorkflowRunLog(target.id, runId)}
        onRunFromNode={(nodeId) => runWorkflowFromNode(target, nodeId)}
        onSettingChange={changeSetting} onImportWorkflow={importWorkflow}
        onExportWorkflow={() => exportWorkflow(target)} onRunWorkflow={() => runWorkflowNow(target)}
        onValidateWorkflow={() => validateWorkflow(target)} onStopWorkflow={() => stopExactWorkflowRun(target)}
        onDecideApproval={(approval, decision, notes, by, answer) =>
          decideApproval(target, approval, decision, notes, by, answer)
        }
        onRattishMutation={(mutations) => mutateActiveRattish(mutations, target)}
        graphHistory={snapshot ? null : documentSession(target.id).graphHistoryRef.current}
        onGraphUndo={() => stepGraphHistory(target, "undo")}
        onGraphRedo={() => stepGraphHistory(target, "redo")}
        onWorkflowChange={(next) => updateRattishGraphMetadata(next, target)}
      /></Suspense>
    </>}</WorkflowGraphPane>;
  }

  function revealPanelDiagnostic(diagnostic) {
    if (diagnostic.runtime) {
      window.dispatchEvent(new CustomEvent("gofer:toggle-bottom-panel", { detail: { tab: "timeline", open: true } }));
      return;
    }
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
    // With the graph in front and no text field (the one focused before the menu opened),
    // Edit > Undo and Redo step through graph edits.
    if ((action === "edit.undo" || action === "edit.redo") && graphShownNow() && !hasEditingTarget()) {
      const direction = action === "edit.undo" ? "undo" : "redo";
      if (documentSession(activeWorkflow.id).graphHistoryRef.current[direction].length) void stepGraphHistory(activeWorkflow, direction);
      else setTopBarNotice({ type: "info", message: `Nothing to ${direction} in this graph.` });
      return;
    }
    if (["edit.undo", "edit.redo", "edit.cut", "edit.copy", "edit.paste", "selection.selectAll"].includes(action)) {
      if (runFocusedEdit(action)) return;
    }
    if (action.startsWith("edit.") || action.startsWith("selection.")) {
      const ran = activeWorkspaceRef.current?.runCommand?.(action);
      if (!ran) setTopBarNotice({ type: "info", message: "This command is unavailable for the active document." });
      return;
    }
    if (action === "file.new") {
      changeStudioView("code");
      setNewCodeFileRequest((current) => current + 1);
    }
    if (action === "file.open") void openFile();
    if (action === "file.quickOpen") openPalette("files");
    if (action === "view.commandPalette") openPalette("commands");
    if (action === "file.openFolder") void openProjectFolder();
    if (action === "file.save") void rattishEditorRef.current?.saveActive?.();
    if (action === "file.close") closeActiveCodeFile();
    if (action === "view.graph") changeStudioView("graph");
    if (action === "view.code") changeStudioView("code");
    if (action === "view.projectPane") setProjectPaneVisible((current) => !current);
    if (action === "view.assistantPane") setAssistantPaneVisible((current) => !current);
    if (action === "view.dictate") void requestVoiceDictation({ reveal: () => setAssistantPaneVisible(true) });
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

  // Mod+Shift+P lists every command and Mod+P opens quick open. Pressing the key
  // of the open one closes it; the other key switches to the other list.
  function openPalette(mode) {
    if (palette?.mode === mode) { closePalette(); return; }
    const returnFocus = palette?.returnFocus ?? document.activeElement;
    setPalette({ mode, returnFocus, recentIds: loadRecentIds(mode), threads: mode === "files" ? paletteThreads() : [] });
    if (mode === "commands") {
      void triggersApi.pauseState().then((state) => setPaletteTriggersPaused(Boolean(state?.pause?.paused))).catch(() => {});
    }
    if (mode === "files") void loadPaletteFiles(activeProjectRoot);
  }
  openPaletteRef.current = openPalette;
  paletteOpenRef.current = Boolean(palette);

  function closePalette() {
    const target = palette?.returnFocus;
    setPalette(null);
    if (target?.isConnected) target.focus?.();
  }

  async function loadPaletteFiles(root) {
    const list = window.goferDesktop?.workspace?.listProjectFiles;
    if (!root || !list) { setPaletteFiles({ root: root || "", files: [], loading: false, error: "" }); return; }
    setPaletteFiles((current) => ({ root, files: samePath(current.root, root) ? current.files : [], loading: true, error: "" }));
    try {
      const result = await list(root);
      setPaletteFiles((current) => samePath(current.root, root)
        ? { root, files: Array.isArray(result?.files) ? result.files : [], loading: false, error: "", truncated: result?.truncated === true }
        : current);
    } catch (error) {
      setPaletteFiles((current) => samePath(current.root, root) ? { root, files: [], loading: false, error: error?.message || "Unable to list project files." } : current);
    }
  }

  // Rem's threads from this computer, newest first, without verification threads.
  function paletteThreads() {
    return chatThreadIndex().filter((entry) => !entry.archived && entry.managedRole !== "verify").slice(0, 500)
      .map((entry) => loadChatThread(entry.id)).filter(Boolean);
  }

  // Run workflow needs the workflow's own tab in front, the same as its key.
  function shortcutRunnableWorkflow() {
    if (!activeWorkflow) return { reason: "Open a workflow first." };
    const front = workflowTabs[activeCodePath]?.workflowId === activeWorkflow.id || activeCodePath === activeWorkflow.sourcePath;
    if (!front) return { reason: `Show ${activeWorkflow.name || "the workflow"}'s tab first.` };
    if (runState.running) return { reason: `${activeWorkflow.name || "The workflow"} is already running.` };
    return { workflow: activeWorkflow };
  }

  function graphShownNow() {
    return mainContent === EDITOR_CONTENT && studioView === "graph" && Boolean(activeWorkflow);
  }

  function paletteCommandItems() {
    const runnable = shortcutRunnableWorkflow();
    const codeOpen = mainContent === EDITOR_CONTENT && studioView === "code" && Boolean(activeCodePath) && !workflowTabs[activeCodePath];
    const graphShown = graphShownNow();
    const graphHistory = graphShown ? documentSession(activeWorkflow.id).graphHistoryRef.current : EMPTY_GRAPH_HISTORY;
    const keyed = keybindingPaletteItems(settings).map((item) => {
      const { commandId, scope } = item;
      if (commandId === "workflow.run") {
        return runnable.workflow
          ? { ...item, startsWork: runnable.workflow.name || runnable.workflow.id, detail: `Runs ${runnable.workflow.name || runnable.workflow.id}` }
          : { ...item, disabled: true, disabledReason: runnable.reason, detail: runnable.reason };
      }
      if (scope === "system") {
        const reason = "Works from any app once it's turned on in Settings > Devices.";
        return { ...item, disabled: true, disabledReason: reason, detail: "In any app" };
      }
      if (commandId === "terminal.close") return { ...item, disabled: true, disabledReason: "Press it in a terminal.", detail: "In a terminal" };
      if (["file.save", "file.close", "editor.find"].includes(commandId) && !codeOpen) {
        return { ...item, disabled: true, disabledReason: "Open a file in the code editor first." };
      }
      if (scope === "graph" && !graphShown) return { ...item, disabled: true, disabledReason: "Show a workflow's graph first." };
      if (commandId === "graph.undo" || commandId === "graph.redo") {
        const entry = graphHistory[commandId === "graph.undo" ? "undo" : "redo"].at(-1);
        const verb = commandId === "graph.undo" ? "Undo" : "Redo";
        return entry ? { ...item, detail: `${verb} ${entry.label}` } : { ...item, disabled: true, disabledReason: `Nothing to ${verb.toLowerCase()} in this graph.` };
      }
      return item;
    });
    const actions = actionPaletteItems({ triggersPaused: paletteTriggersPaused }).filter((item) => (
      !item.pluginId || pluginAvailable(settings.plugins, item.pluginId)
    )).map((item) => (item.pluginId && !pluginEnabled(settings.plugins, item.pluginId)
      ? { ...item, disabled: true, disabledReason: "It's hidden. Show it again in Settings > Plugins." }
      : item));
    return [...keyed, ...actions];
  }

  const paletteMode = palette?.mode ?? "";
  const paletteThreadList = palette?.threads;
  const quickOpenProjectName = activeProjectRoot ? projectNameFromPath(activeProjectRoot) : "";
  const quickOpenList = useMemo(() => (paletteMode === "files"
    ? quickOpenItems({ files: paletteFiles.files, workflows, threads: paletteThreadList, projectName: quickOpenProjectName })
    : []), [paletteMode, paletteFiles.files, workflows, paletteThreadList, quickOpenProjectName]);

  function choosePaletteItem(item) {
    rememberRecentId(palette?.mode, item.id);
    closePalette();
    if (item.commandId) {
      const { commandId } = item;
      if (commandId === "workflow.run") {
        const runnable = shortcutRunnableWorkflow();
        if (runnable.workflow) void runWorkflowNowRef.current?.(runnable.workflow);
        return;
      }
      if (commandId === "file.new") { runGlobalMenuAction("file.new"); return; }
      if (commandId === "file.save") { runGlobalMenuAction("file.save"); return; }
      if (commandId === "file.close") { runGlobalMenuAction("file.close"); return; }
      if (commandId === "editor.find") { runGlobalMenuAction("edit.find"); return; }
      if (commandId === "editor.toggleWordWrap") { changeSetting("editor.wordWrap", !settings.editor.wordWrap); return; }
      if (commandId.startsWith("graph.")) { window.dispatchEvent(new CustomEvent("gofer:graph-command", { detail: { commandId } })); return; }
      runShortcutActionRef.current?.(commandId);
      return;
    }
    if (item.settingsSection) { setSettingsCategory(item.settingsSection); setSettingsOpen(true); return; }
    if (item.kind === "file") { openCodeFile(item.path); return; }
    if (item.kind === "workflow") { openWorkflowGraph(workflows.find((workflow) => workflow.id === item.workflowId)); return; }
    if (item.kind === "thread") { window.dispatchEvent(new CustomEvent("gofer:rem-open-thread", { detail: { threadId: item.threadId } })); return; }
    const action = item.actionId;
    if (action === "workflow.new") { setCreateState({ saving: false, error: "" }); setCreateDialogOpen(true); return; }
    if (action === "workflow.allRuns") { openWorkflowRuns(); return; }
    if (action === "triggers.pause" || action === "triggers.resume") {
      const paused = action === "triggers.pause";
      void triggersApi.setPaused(paused).then(() => {
        setPaletteTriggersPaused(paused);
        setTopBarNotice({ type: "info", message: paused ? PAUSED_TEXT : "Triggers are back on. The ones that were on before the pause fire again." });
      }).catch((error) => setTopBarNotice({ type: "error", message: `Unable to change triggers: ${error?.message || "unknown error"}` }));
      return;
    }
    if (!action?.startsWith("open.")) return;
    const target = action.slice("open.".length);
    if (target === "browser") { showBrowserContent(); closeCompactPane(); return; }
    if (target === "posts" || target === "second-brain") {
      setSidebarActivity(target);
      showMainContent(target);
      closeCompactPane();
      return;
    }
    // Plugins with a side pane show it, and open what their rail button opens.
    setSidebarActivity(target);
    setProjectPaneVisible(true);
    if (target === "rem" || target === "kanban") showMainContent(target);
    if (target === "files") showEditorContent();
  }

  return (
    <main
      aria-busy={runState.running || logState.loading || undefined}
      className={`flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-canvas text-ink ${theme}`}
    >
      <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {runState.running ? "" : workflowRunAnnouncement(runState.result)}
      </div>
      <div aria-atomic="true" aria-live="assertive" className="sr-only" role="alert">
        {runState.error}
      </div>
      {lifecyclePrompt ? <Dialog title="Unsaved file changes" onClose={() => {
        if (lifecyclePrompt.busy) return;
        lifecyclePrompt.resolve(false); setLifecyclePrompt(null);
      }}>
        <p>Save your files before you {lifecyclePrompt.reason}, or keep drafts to recover when the files reopen.</p>
        {lifecyclePrompt.error ? <p role="alert">{lifecyclePrompt.error}</p> : null}
        <div className="mt-4 flex gap-3">
          <button type="button" disabled={lifecyclePrompt.busy} onClick={() => { lifecyclePrompt.resolve(false); setLifecyclePrompt(null); }}>Cancel</button>
          <button type="button" disabled={lifecyclePrompt.busy} onClick={() => {
            if (!flushCodeDrafts()) { setLifecyclePrompt(current => ({ ...current, error: "Unable to preserve drafts. Save your files or cancel." })); return; }
            lifecyclePrompt.resolve(true); setLifecyclePrompt(null);
          }}>Keep drafts</button>
          <button type="button" disabled={lifecyclePrompt.busy} onClick={async () => {
            const prompt = lifecyclePrompt;
            setLifecyclePrompt(current => ({ ...current, busy: true, error: "" }));
            try {
              for (const path of prompt.paths) {
                const session = textEditorSessions.get(path);
                if (!session || session.content === session.savedContent) continue;
                const content = session.content;
                const managed = rattishEditorRef.current?.savePath?.(path);
                if (managed !== undefined) { if (!await managed) throw new Error(`Unable to save ${path}`); }
                else {
                  const disk = await window.goferDesktop.textFiles.read(path);
                  if (disk?.missing || disk.content !== session.savedContent) throw new Error(`${path} changed on disk. Open it to review the recovered draft, or choose Keep drafts.`);
                  await window.goferDesktop.textFiles.write({ targetPath: path, content });
                  textEditorSessions.set(path, { ...session, savedContent: content });
                }
              }
              if ([...textEditorSessions.values()].some(session => session.content !== session.savedContent)) throw new Error("Some files still have unsaved changes. Save them or keep drafts.");
              flushCodeDrafts(); prompt.resolve(true); setLifecyclePrompt(null);
            } catch (error) { setLifecyclePrompt(current => ({ ...current, busy: false, error: error.message })); }
          }}>Save all</button>
        </div>
      </Dialog> : null}
      {shortcutWorkflow ? <Suspense fallback={null}><WorkflowShortcutDialog workflow={shortcutWorkflow} settings={settings} onClose={() => setShortcutWorkflow(null)} onSaved={(saved) => setTopBarNotice({ type: "success", message: saved ? `Shortcut saved for ${shortcutWorkflow.name}.` : `Shortcut removed from ${shortcutWorkflow.name}.` })} /></Suspense> : null}
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
        activeCodeDocument={activeCodeDocumentState}
        activeCodePath={activeCodePath}
        assistantPaneVisible={assistantPaneVisible}
        projectPaneVisible={projectPaneVisible}
        recentProjectRoots={recentProjectRoots}
        settings={settings}
        settingsOpen={settingsOpen}
        theme={theme}
        updateState={updateState}
        updateNotice={updateNotice}
        view={studioView}
        onApplyUpdate={() => applyUpdate(updateState)}
        onCheckForUpdates={() => checkForUpdates()}
        onSelectProject={selectRecentProject}
        onToggleSettings={() => setSettingsOpen((current) => !current)}
        onToggleTheme={() => changeSetting("appearance.theme", theme === "dark" ? "light" : "dark")}
        onMenuAction={runGlobalMenuAction}
      />
      <div className="studio-panes flex h-full min-h-0 min-w-0 flex-1 overflow-hidden" data-project-side={settings.layout.projectPaneSide} data-assistant-side={settings.layout.assistantPaneSide}>
      {<WorkflowSidebar
        activity={sidebarActivity}
        // Browser has no side pane: the rail stays, and the browser fills the main area.
        paneVisible={projectPaneVisible && pluginHasPane(sidebarActivity)}
        activeWorkflow={browsingWorkspace}
        mainProjectRoot={Object.entries(lastWorktreeByProject).find(([, root]) => samePath(root, activeProjectRoot))?.[0] || activeProjectRoot}
        activeWorkflowId={activeWorkflow?.id}
        loading={loadState.loading}
        openingProjectRoot={openingProjectRoot}
        runRecords={runRegistry.records}
        runStatesById={runStatesById}
        settings={settings}
        onSettingsChange={changeSetting}
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
        legacyTomlFiles={legacyTomlFiles}
        onConvertLegacyToml={(file) => setTomlConversion({ file, saving: false, error: "", result: null })}
        onDeleteWorkflow={deleteWorkflow}
        onDuplicateWorkflow={duplicateWorkflow}
        onAddWorkflowShortcut={typeof window !== "undefined" && window.goferDesktop?.workflowShortcuts ? setShortcutWorkflow : null}
        onCodeFileOpen={(...args) => { openCodeFile(...args); closeCompactPane(); }}
        selectedSwarmId={samePath(selectedSwarm?.projectRoot, swarmProjectRoot) ? selectedSwarm.id : null}
        onOpenOrganization={(id) => { setSidebarActivity("organizations"); showMainContent("organizations"); setSelectedOrganization({ id }); closeCompactPane(); }}
        selectedOrganizationId={selectedOrganization ? selectedOrganization.id : null}
        onOpenSecondBrain={() => { setSidebarActivity("second-brain"); showMainContent("second-brain"); closeCompactPane(); }}
        onOpenKanban={() => { setSidebarActivity("kanban"); showMainContent("kanban"); closeCompactPane(); }}
        onOpenPosts={() => { setSidebarActivity("posts"); showMainContent("posts"); closeCompactPane(); }}
        kanbanOpen={kanbanOpen}
        secondBrainOpen={secondBrainOpen}
        onOpenRemHome={() => { setSidebarActivity("rem"); showMainContent("rem"); closeCompactPane(); }}
        onShowEditor={showEditorContent}
        onOpenBrowser={() => { showBrowserContent(); closeCompactPane(); }}
        remHomeOpen={remHomeOpen}
        remProjectOptions={remHomeProps.projectOptions}
        onOpenSwarm={(id, agentId) => { setSidebarActivity("swarms"); showMainContent("swarms"); setSelectedSwarm({ id, agentId, projectRoot: swarmProjectRoot }); closeCompactPane(); }}
        activeCodePath={activeCodePath}
        onCloseCodeFile={() => rattishEditorRef.current?.closeActive?.()}
        onCodeFilesystemChange={handleCodeFilesystemChange}
        onRefresh={() => { void runRegistry.refresh(); return loadWorkflows({ discoverProject: true }); }}
        onRenameWorkflow={renameWorkflow}
        onEditWorkflowFile={editWorkflowFile}
        onSelectProject={selectRecentProject}
        onRemoveRecentProject={removeRecentProject}
        onRunWorkflow={runWorkflowNow}
        onStopWorkflow={stopExactWorkflowRun}
        onResizeStart={(event) =>
          startPaneResize(event, {
            max: 420,
            min: 240,
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
            <BrowserMediaBar viewedTabs={[...viewedCodePaths, ...viewedBrowserMedia(viewedBrowserPaths, browserTabs)]} />
            {phoneLocks.length ? (
              <div role="alert" aria-label="Locked phone" className="flex shrink-0 items-center gap-2 border-b border-red-300 bg-red-50 px-3 py-1.5 text-xs text-ink dark:bg-red-950 dark:text-red-100">
                <ShieldAlert aria-hidden="true" className="shrink-0 text-red-700 dark:text-red-300" size={14} />
                <span className="min-w-0 flex-1">{phoneLockNotice(phoneLocks)}</span>
                <button className="rounded-md bg-brand px-2.5 py-1 font-semibold text-white hover:opacity-90" type="button" onClick={() => { setSettingsCategory("devices"); setSettingsOpen(true); }}>Review</button>
              </div>
            ) : null}
            {!browserContentOpen && hiddenBrowserApproval ? (
              <div role="alert" className="flex shrink-0 items-center gap-2 border-b border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-ink dark:bg-amber-950 dark:text-amber-100">
                <Bot aria-hidden="true" className="shrink-0 text-amber-800" size={14} />
                <span className="min-w-0 flex-1">{hiddenBrowserApproval.agentLabel} needs your approval to {hiddenBrowserApproval.label || "continue"} in “{hiddenBrowserApproval.title}”.</span>
                <button className="rounded-md bg-brand px-2.5 py-1 font-semibold text-white hover:opacity-90" type="button" onClick={() => { showBrowserContent(); setActiveBrowserPath(hiddenBrowserApproval.path); }}>Review</button>
              </div>
            ) : null}
            {(workflowTabs[activeCodePath] || activeCodePath === activeWorkflow?.sourcePath) && rattishEditorState?.recoveryWarning ? (
              <div role="status" className="shrink-0 border-b border-line bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:bg-amber-950 dark:text-amber-200">
                <strong>Draft recovery: </strong>{rattishEditorState.recoveryWarning}
              </div>
            ) : null}
        {organizationOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading organization...</p>}><OrganizationHome key={selectedOrganization.id} rootPath={swarmProjectRoot} projectOptions={recentProjectRoots} organizationId={selectedOrganization.id} defaults={settings.assistant} memorySettings={settings.memory} onSelect={id => setSelectedOrganization({ id })} onClose={() => setSelectedOrganization(null)} /></Suspense> : null}
        {secondBrainOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading Second Brain...</p>}><SecondBrainWorkspace assistant={settings.assistant} memorySettings={settings.memory} onClose={showEditorContent} /></Suspense> : null}
        {kanbanOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading the board...</p>}><KanbanWorkspace /></Suspense> : null}
        {postsOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading posts...</p>}><SocialPostsWorkspace /></Suspense> : null}
        {workflowRunsOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading workflow runs...</p>}><WorkflowRunsView request={workflowRunsRequest} onAskRem={run => askRemAboutRunFailure({ workflowId: run.workflowId, runId: run.id })} onOpenRun={run => void reviewWorkflowRun(reviewRecord(run))} onClose={showEditorContent} /></Suspense> : null}
        {remHomeOpen ? <RemSlot host={remHost} /> : null}
        {swarmOpen ? <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading swarm...</p>}><SwarmWorkspace projectPaths={mergeRecentProjects(activeProjectRoot ? [activeProjectRoot] : [], recentProjectRoots).map(root => pathValue(lastWorktreeByProject, root) || root)} selectedAgentId={selectedSwarm.agentId} defaults={settings.assistant} key={`${swarmProjectRoot}:${selectedSwarm.id}`} rootPath={swarmProjectRoot} swarmId={selectedSwarm.id} onSelect={(id) => setSelectedSwarm({ id, projectRoot: swarmProjectRoot })} onClose={() => setSelectedSwarm(null)} /></Suspense> : null}
        <div className={`${editorContentOpen ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
            {!workflowTabs[activeCodePath] && (activeCodePath || sidebarActivity !== "workflows") && topBarNotice?.message ? (
              <div role={topBarNotice.type === "error" ? "alert" : "status"} className="shrink-0 border-b border-line bg-surface px-3 py-2 text-xs text-ink break-words">
                {topBarNotice.message}
              </div>
            ) : null}
            <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading editor...</p>}>
            <CodeWorkspace
              active={editorContentOpen}
              activePath={activeCodePath}
              onViewedPathsChange={setViewedCodePaths}
              navigationRequest={codeNavigationRequest}
              ref={rattishEditorRef}
              openPaths={codeOpenPaths}
              externalPaths={externalFilePaths}
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
              onAskRemAboutPage={askRemAboutPage}
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
              onDropPaths={paths => {
                showEditorContent();
                setCodeOpenPaths(current => mergeCodeOpenPaths(current, paths));
                setRecentCodePaths(current => mergeCodeOpenPaths(current, paths));
                setActiveCodePath(paths.at(-1));
                previewCodePathRef.current = "";
                setPreviewCodePath("");
                setCodeEditorOpened(true);
                setStudioView("code");
              }}
              onOpenPathsChange={setCodeOpenPaths}
              onPinPath={pinCodeFile}
              onRattishContentChange={scheduleRattishAnalysis}
              onRattishDiscard={reloadActiveRattishDocument}
              onRattishSaved={refreshRattishAfterFileSave}
              onSettingChange={changeSetting}
              terminalProjectRoot={panelProjectRoot}
            />
            </Suspense>
        </div>
        {/* The Browser plugin. Its tabs stay mounted while another area is in
            front, so pages keep playing and keep their place. It can also move
            to a window of its own (BrowserWindowHost). */}
        <div className={`${browserContentOpen ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
            <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading browser...</p>}>
            <BrowserWindowHost onPoppedOutChange={setBrowserPoppedOut}>
            <CodeWorkspace
              ref={browserWorkspaceRef}
              variant="browser"
              active={browserContentOpen || browserPoppedOut}
              activePath={activeBrowserPath}
              openPaths={browserOpenPaths}
              browserTabs={browserTabs}
              defaultBrowserEngine={settings.browser.engine}
              theme={theme}
              settings={settings}
              onViewedPathsChange={setViewedBrowserPaths}
              onHiddenApprovalChange={setHiddenBrowserApproval}
              onShowApproval={(path) => { showBrowserContent(); setActiveBrowserPath(path); }}
              onActivePathChange={setActiveBrowserPath}
              onOpenPathsChange={setBrowserOpenPaths}
              onClosePath={(path) => closeBrowserTabs([path])}
              onClosePaths={closeBrowserTabs}
              onBrowserStateChange={updateBrowserTab}
              onGeckoPendingOpened={clearGeckoPending}
              onAskRemAboutPage={askRemAboutPage}
              onShareBrowserTab={shareBrowserTabWithRem}
              onShareGeckoTab={window.goferBrowser?.gecko ? shareGeckoTabWithRem : undefined}
              canShareBrowserTab={settings.browser.agentBrowser === "raticode"}
              onReopenClosedBrowserTab={() => reopenClosedBrowserTabRef.current?.()}
              onOpenBrowser={(options) => openIntegratedBrowser(undefined, options)}
              onOpenPath={(path) => void openRecentCodeFile(path)}
            />
            </BrowserWindowHost>
            </Suspense>
        </div>
        {pinnedRun ? <div className="flex items-center justify-between border-t border-line px-3 py-1 text-[11px] text-muted"><span className="truncate">Run logs · {pinnedRun.workflowName} · {pinnedRun.runId}</span><button type="button" onClick={() => setPinnedRun(null)}>Unpin logs</button></div> : null}
        <UnifiedBottomPanel
          testsProps={{
            workflowId: activeWorkflow?.sourceFormat === "rattish" ? activeWorkflow.id : "",
            workflowName: activeWorkflow?.name ?? "",
            refreshKey: rattishSessions[activeWorkflow?.id]?.document?.savedRevision ?? "",
            dirty: Boolean(rattishSessions[activeWorkflow?.id]?.document?.dirty),
            // Use this model edits the step like the graph editor, so the graph stays
            // valid and triggers ask for approval again.
            onUseModel: (mutations) => mutateActiveRattish(mutations),
          }}
          runsProps={{ records: visibleRunRecords, projectPath: activeProjectRoot, onAskRem: (record) => askRemAboutRunFailure({ workflowId: record.workflowId, runId: record.runId }), onReview: reviewWorkflowRun, onStop: runRegistry.stop, onRefresh: runRegistry.refresh, loading: runRegistry.loading, connectionError: runRegistry.error }}
          changesProps={{
            worktree: logState.worktree ?? null,
            workflowId: panelWorkflowId || "",
            onChanged: () => {
              if (!panelWorkflowId) return;
              if (logState.selectedRunId) void loadRunLog(panelWorkflowId, logState.selectedRunId);
              else void loadLatestLog(panelWorkflowId, { silent: true });
              void loadRunLogs(panelWorkflowId, { silent: true });
            },
          }}
          diagnostics={panelDiagnostics}
          onSettingChange={changeSetting}
          projectRoot={panelProjectRoot}
          settings={settings}
          theme={theme}
          onAskRemAboutDiagnostic={askRemAboutPanelDiagnostic}
          onRevealDiagnostic={revealPanelDiagnostic}
          timelineProps={{
            error: logState.error,
            onAskRem: panelWorkflowId && panelRunId ? (row) => askRemAboutPanelRun(row.node || "workflow") : undefined,
            loading: logState.loading,
            logPath: logState.path,
            onPruneRuns: panelWorkflowId
              ? (options) => pruneWorkflowRunLogs(panelWorkflowId, options)
              : undefined,
            onResumeRun: panelWorkflowResumable
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

      {palette ? (
        <Suspense fallback={null}>
          <CommandPalette
            items={palette.mode === "files" ? quickOpenList : paletteCommandItems()}
            loading={palette.mode === "files" && paletteFiles.loading}
            mode={palette.mode}
            notice={palette.mode !== "files" ? ""
              : paletteFiles.error ? `Files: ${paletteFiles.error}`
              : !activeProjectRoot ? "Open a project to search its files."
              : !window.goferDesktop?.workspace?.listProjectFiles ? "Project files are listed in the desktop app."
              : paletteFiles.truncated ? `Showing the first ${paletteFiles.files.length.toLocaleString()} files.` : ""}
            recentIds={palette.recentIds}
            onChoose={choosePaletteItem}
            onClose={closePalette}
          />
        </Suspense>
      ) : null}

      {settingsOpen ? (
        <Suspense fallback={<p role="status">Loading settings...</p>}>
        <SettingsPopover
          initialCategory={settingsCategory}
          initialBrowserSection={settingsBrowserSection}
          initialReveal={settingsReveal}
          dataDir={dataDir}
          open
          settings={settings}
          onChange={changeSetting}
          onChooseDataDirectory={chooseApplicationDataDirectory}
          onClose={() => { setSettingsOpen(false); setSettingsCategory("general"); setSettingsBrowserSection(""); setSettingsReveal(null); }}
          onResetAll={async () => {
            try { await window.goferDesktop?.rem?.configure?.("reset", true); setSettings(defaultSettingsSnapshot()); }
            catch (error) { reportArchiveError(error); }
          }}
        />
        </Suspense>
      ) : null}

      {sidePaneShowsRem ? <div className={assistantPaneVisible ? "contents" : "hidden"} aria-hidden={!assistantPaneVisible}>
        <RemSidePane
          width={chatPaneWidth}
          onResizeStart={(event) => startPaneResize(event, { max: 520, min: 300, width: chatPaneWidth, onResize: (width) => changeSetting("layout.assistantPaneWidth", width) })}
          onResizeKeyDown={(event) => handlePaneResizeKeyDown(event, { defaultValue: 380, max: 520, min: 300, onResize: (width) => changeSetting("layout.assistantPaneWidth", width), width: chatPaneWidth })}
        >
          <RemSlot host={remHost} />
        </RemSidePane>
      </div> : null}
      {/* Threads stay mounted while Rem fills the side pane, so their drafts survive. */}
      <div className={assistantPaneVisible && !sidePaneShowsRem ? "contents" : "hidden"} aria-hidden={!assistantPaneVisible || sidePaneShowsRem}>
        <ChatPane
          onShowRem={remPaneLayout && !remHomeOpen && threadsRequested ? () => setThreadsRequested(false) : undefined}
          composerFocusRequest={assistantFocusRequest}
          memorySettings={settings.memory}
          agentBrowser={settings.browser.agentBrowser}
          assistantDefaults={settings.assistant}
          onNewThreadResourcesChange={resources => changeSetting("assistant.resources", resources)}
          archiveRetention={settings.general.archivedThreadRetention}
          audioInputDeviceId={settings.devices.audioInputId}
          recentProjectRoots={recentProjectRoots}
          swarmProjectPaths={mergeRecentProjects(activeProjectRoot ? [activeProjectRoot] : [], recentProjectRoots).map(root => pathValue(lastWorktreeByProject, root) || root)}
          width={chatPaneWidth}
          activeWorkflowId={activeWorkflow?.id}
          activeProjectRoot={activeProjectRoot}
          workflow={activeWorkflow}
          workflows={workflows}
          openFiles={editorFileReferences(codeOpenPaths, workflowTabs, externalFilePaths)}
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
      {remMounted && remPluginOn && remHost ? createPortal(<Suspense fallback={<p role="status" className="p-4 text-sm text-muted">Loading Rem...</p>}><RemHomeWorkspace {...remHomeProps} /></Suspense>, remHost) : null}
      {runPreview ? (
        <Suspense fallback={null}><RunPreviewDialog
          plan={runPreview.plan}
          workflow={runPreview.workflow}
          onCancel={() => setRunPreview(null)}
          initialParameters={runPreview.parameters}
          onRun={(parameters) =>
            executeWorkflowRun(runPreview.workflow, {}, parameters)
          }
        /></Suspense>
      ) : null}
      {savedInputsPrompt ? (
        <SavedInputsPrompt
          action={savedInputsPrompt.action}
          names={savedInputsPrompt.names}
          onCancel={() => savedInputsPrompt.resolve(null)}
          onSubmit={(values) => savedInputsPrompt.resolve(values)}
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
      {tomlConversion ? (
        <ConvertTomlWorkflowDialog
          defaultProjectRoot={panelProjectRoot}
          error={tomlConversion.error}
          file={tomlConversion.file}
          result={tomlConversion.result}
          saving={tomlConversion.saving}
          onClose={() => setTomlConversion(null)}
          onConvert={convertLegacyTomlWorkflow}
        />
      ) : null}

      {exportDialog.workflow ? <Suspense fallback={null}><ExportWorkflowDialog
        directory={exportDialog.directory}
        error={exportDialog.error}
        filename={workflowBundleFilename(exportDialog.workflow)}
        open
        saving={exportDialog.saving}
        workflow={exportDialog.workflow}
        onClose={() => {
          if (!exportDialog.saving) {
            setExportDialog({ directory: "", error: "", grantId: "", saving: false, workflow: null });
          }
        }}
        onChooseFolder={chooseExportDirectory}
        onExport={confirmExportWorkflow}
      /></Suspense> : null}

      <TextZoomBar value={textZoom} />
      {confirmDialog}
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

// Rem, the CLI or another editor may change workflow.rattish on disk. Before a run, a
// clean editor follows the saved file so the run uses the revision its preview compiles.
// Unsaved edits are kept; saving them reports the conflict.
export async function followSavedRattishDocument(session, loadSavedDocument) {
  const loadedDocument = session.rattishEditorStateRef.current?.document;
  if (loadedDocument?.dirty) return loadedDocument;
  const savedDocument = await loadSavedDocument();
  const currentDocument = session.rattishEditorStateRef.current?.document;
  if (currentDocument !== loadedDocument) return currentDocument;
  if (currentDocument?.savedRevision !== savedDocument.savedRevision) {
    session.setRattishEditorState({ document: savedDocument, error: "", loading: false, saving: false });
  }
  return session.rattishEditorStateRef.current?.document;
}

// The toolbar Stop acts on exactly one run. With several, it opens the Runs panel.
export function workflowStopControl(activeRuns = []) {
  if (!activeRuns.length) return { stopDisabled: true, stopTitle: "No run in progress" };
  if (activeRuns.length > 1) {
    return { stopDisabled: false, stopTitle: `${activeRuns.length} runs in progress. Choose one to stop in the Runs panel` };
  }
  if (!exactWorkflowRunStopPath(activeRuns[0])) {
    return { stopDisabled: true, stopTitle: "This run can't be stopped from here" };
  }
  return { stopDisabled: false, stopTitle: "Stop this run" };
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

// Resize the pane element directly while dragging and save the width once on
// release. Saving on every move re-rendered the whole app each pointer event.
// Panes can dock to either edge, so the grabbed edge decides which way grows.
function startPaneResize(event, { max, min, onResize, width }) {
  event.stopPropagation();

  const handle = event.currentTarget;
  const pane = handle.parentElement;
  const startX = event.clientX;
  const bounds = pane.getBoundingClientRect();
  const side = startX < bounds.left + bounds.width / 2 ? "left" : "right";
  let nextWidth = width;

  trackPointerDrag(event, {
    cursor: "col-resize",
    onMove({ clientX }) {
      const delta = clientX - startX;
      nextWidth = clampNumber(side === "left" ? width - delta : width + delta, min, max);
      pane.style.width = `${nextWidth}px`;
      handle.setAttribute("aria-valuenow", String(nextWidth));
      handle.setAttribute("aria-valuetext", `${nextWidth} pixels wide`);
    },
    onEnd() {
      if (nextWidth !== width) onResize(nextWidth);
    },
  });
}

export function summarizeWorkflow(workflow) {
  return { ...workflow, status: workflow.invalid ? "Error" : workflow.status ?? "Ready" };
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
    return {
      id: node.id,
      label: node.label || node.id,
      type,
      operation: { type, ...(node.configuration ?? {}) },
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

// Background runs return while still running, so only finished results say "completed".
export function workflowRunAnnouncement(result) {
  if (!result) return "";
  if (result.status === "queued") return "Workflow run queued.";
  if (result.status === "running") return "Workflow run started.";
  return `Workflow run completed: ${result.status ?? (result.success ? "success" : "error")}`;
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
  { dryRun = false, triggerContext = {}, parameters = {}, background = false, expectedRevision, fromNode, reuseRunId } = {},
) {
  const body = { dryRun, triggerContext };
  if (background) body.background = true;
  if (expectedRevision) body.expectedRevision = expectedRevision;
  // Run from a node: reuse an earlier run's results for the steps upstream of it.
  if (fromNode) body.fromNode = fromNode;
  if (reuseRunId) body.reuseRunId = reuseRunId;
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

export function runFromNotice(plan = {}) {
  const reused = plan.reusedNodes ?? [];
  const steps = reused.length === 1 ? "1 step" : `${reused.length} steps`;
  return reused.length
    ? `Running from ${plan.fromNode}, reusing ${steps} from the last run`
    : `Running from ${plan.fromNode}`;
}

// Approval steps are never reused by Run from here, so the ones before the step ask
// again. Empty when there are none and the run starts without asking.
export function runFromConfirmation(plan = {}) {
  const gates = plan.askAgainNodes ?? [];
  if (!gates.length) return "";
  const which = gates.length === 1 ? "This approval step asks" : "These approval steps ask";
  return `Run from ${plan.fromNode}? ${which} again: ${gates.join(", ")}.`;
}

// Text from the password prompt, typed by each input's schema: strings stay text,
// anything else is read as JSON when it parses.
export function savedInputValues(values, specs = {}) {
  const typed = {};
  for (const [name, value] of Object.entries(values)) {
    const type = specs[name]?.type;
    if (!type || type === "string" || type === "enum") {
      typed[name] = value;
      continue;
    }
    try {
      typed[name] = JSON.parse(value);
    } catch {
      typed[name] = value;
    }
  }
  return typed;
}

export function workflowRerunRequest(workflowId, runId, inputs = {}) {
  return {
    url: apiUrl(`/rattish/runs/${encodeURIComponent(runId)}/rerun`),
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ workflowId, background: true, inputs }),
    },
  };
}

export function workflowResumeRequest(
  workflowId,
  runId,
  { background, force = false, fromNode = null, onlyNode = null, skipCache = false, triggerContext = {}, inputs = {} } = {},
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
      body: JSON.stringify(
        background
          ? { background, ...(Object.keys(inputs ?? {}).length ? { inputs } : {}) }
          : { force, fromNode, onlyNode, skipCache, triggerContext },
      ),
    },
  };
}

// A run's log comes as its tail; `details` adds node outputs and run events.
export function workflowLogUrls(workflowId, runId = null, { details = false } = {}) {
  const encodedWorkflowId = encodeURIComponent(workflowId);
  const selectedParams = new URLSearchParams({
    tailBytes: String(RUN_LOG_TAIL_BYTES),
    details: details ? "1" : "0",
  });
  return {
    latest: apiUrl(`/workflows/${encodedWorkflowId}/logs/latest`),
    selected: runId
      ? `${apiUrl(
          `/workflows/${encodedWorkflowId}/logs/${encodeURIComponent(runId)}`,
        )}?${selectedParams}`
      : null,
  };
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

// Startup visibility and the compact width come from Settings > Layout.
export function useResponsivePanes(layout = {}) {
  const [windowWidth, setWindowWidth] = useState(() => globalThis.window?.innerWidth ?? 1280);
  const compactWidth = layout.compactWidth ?? 1000;
  const compact = windowWidth < compactWidth;
  const compactRef = useRef(compact);
  compactRef.current = compact;
  const [desktopProjectVisible, setDesktopProjectVisible] = useState(layout.projectPaneAtStartup !== false);
  const [desktopAssistantVisible, setDesktopAssistantVisible] = useState(layout.assistantPaneAtStartup !== false);
  const [compactPane, setCompactPane] = useState("");
  useEffect(() => {
    const resize = () => setWindowWidth(window.innerWidth);
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

export function WorkflowGraphPane({ workflow, active, visible, onSource, onReveal, children }) {
  const [toolbarTarget, setToolbarTarget] = useState(null);
  return <section className="flex min-h-0 flex-1 flex-col overflow-hidden" aria-label={`${workflow.name} graph`} data-graph-active={active} data-graph-visible={visible}>
    <TopBar workflow={workflow} view="graph" onGraphToolbarTargetChange={setToolbarTarget} />
    <div className="flex min-w-0 shrink-0 items-center gap-3 border-b border-line px-3 py-1.5 text-xs [&>button]:shrink-0 [&>button]:whitespace-nowrap">
      <span className="min-w-0 flex-1 truncate text-muted" title={workflow.sourcePath}>{workflow.sourcePath}</span>
      <button className="rounded-md px-1.5 py-0.5 text-slate-600 transition hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500" type="button" onClick={onSource}>Source</button>
      <button className="rounded-md px-1.5 py-0.5 text-slate-600 transition hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500" type="button" onClick={onReveal}>Reveal in project</button>
    </div>
    {children(toolbarTarget)}
  </section>;
}

export function WorkflowSidebar({
  mainProjectRoot,
  openingProjectRoot = "",
  paneVisible = true,
  activity,
  onActivityChange,
  activeCodePath,
  activeWorkflow,
  activeWorkflowId,
  loading,
  runRecords = [],
  runStatesById = {},
  settings,
  onSettingsChange,
  workflows,
  view = "graph",
  newFileRequest,
  recentProjectRoots,
  onCodeFileOpen,
  onOpenSwarm,
  selectedSwarmId,
  onOpenOrganization,
  selectedOrganizationId,
  onOpenSecondBrain,
  secondBrainOpen,
  onOpenKanban,
  onOpenPosts,
  kanbanOpen,
  onOpenRemHome,
  remHomeOpen,
  remProjectOptions,
  onShowEditor,
  onOpenBrowser,
  onCloseCodeFile,
  onCodeFilesystemChange,
  onCreate,
  legacyTomlFiles = [],
  onConvertLegacyToml,
  onOpenProject,
  onAddWorkflowShortcut,
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
  onStopWorkflow,
  onSelect,
  width,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [projectMenu, setProjectMenu] = useState(null);
  const projectMenuTriggerRef = useRef(null);
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
    projectMenuTriggerRef.current = menuTrigger(event);
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
          projectRoot={mainProjectRoot ?? activeWorkflow?.projectRoot ?? ""}
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
            fileWorkflows={workflowsForFile(workflows, activeWorkflow?.projectRoot)}
            onRunWorkflowWithFile={(target, input, path) => onRunWorkflow(target, { [input]: path })}
            newFileRequest={newFileRequest}
            recentProjects={recentProjects}
            settings={settings}
            onSettingsChange={onSettingsChange}
            workflow={activeWorkflow}
            sidebarView={selectedActivity}
            onSidebarViewChange={selectActivity}
            paneVisible={paneVisible}
            hideProjectSelector
            onFilesystemChange={onCodeFilesystemChange}
            onCloseActiveFile={onCloseCodeFile}
            onOpenFile={onCodeFileOpen}
            onOpenOrganization={onOpenOrganization}
            selectedOrganizationId={selectedOrganizationId}
            onOpenSecondBrain={onOpenSecondBrain}
            onOpenKanban={onOpenKanban}
            onOpenPosts={onOpenPosts}
            kanbanOpen={kanbanOpen}
            secondBrainOpen={secondBrainOpen}
            onOpenRemHome={onOpenRemHome}
            remHomeOpen={remHomeOpen}
            remProjectOptions={remProjectOptions}
            onShowEditor={onShowEditor}
            onOpenBrowser={onOpenBrowser}
            onOpenSwarm={onOpenSwarm}
            selectedSwarmId={selectedSwarmId}
            onSelectProject={onSelectProject}
            onRemoveRecentProject={onRemoveRecentProject}
            workflowsHeader={
              <div className="flex flex-col gap-1">
                <button
                  className="flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 text-xs font-semibold text-white transition hover:bg-indigo-700"
                  title="New Workflow"
                  type="button"
                  onClick={onCreate}
                >
                  <Plus size={15} />
                  New Workflow
                </button>
                <button
                  className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-ink transition hover:bg-slate-100 dark:hover:bg-zinc-800"
                  title="Every workflow's runs, newest first"
                  type="button"
                  onClick={() => openWorkflowRuns()}
                >
                  <History aria-hidden="true" size={14} />
                  All workflow runs
                </button>
                <LegacyTomlNotice files={legacyTomlFiles} onConvert={onConvertLegacyToml} />
              </div>
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
                      projectMenuTriggerRef.current = menuTrigger(event);
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
                    {items.map((workflow) => {
                      const listStatus = workflowListStatus(workflow, runRecords, { submitting: Boolean(runStatesById[workflow.id]?.running) });
                      const { active: activeRuns } = workflowRunSummary(runRecords, workflow.id);
                      // Stop acts on exactly one run; several runs are stopped from the Runs panel.
                      const stoppable = activeRuns.length === 1 && Boolean(exactWorkflowRunStopPath(activeRuns[0]));
                      return (
                        <WorkflowListItem
                          key={workflow.id}
                          active={workflow.id === activeWorkflowId}
                          listStatus={listStatus}
                          workflow={workflow}
                          onDelete={() => onDeleteWorkflow(workflow)}
                          onDuplicate={() => onDuplicateWorkflow(workflow)}
                          onAddShortcut={onAddWorkflowShortcut ? () => onAddWorkflowShortcut(workflow) : null}
                          onEditFile={() => onEditWorkflowFile(workflow)}
                          onRename={(name) => onRenameWorkflow(workflow, name)}
                          onRun={["invalid", "submitting"].includes(listStatus.key) ? null : () => onRunWorkflow(workflow)}
                          onStop={stoppable && onStopWorkflow ? () => onStopWorkflow(workflow) : null}
                          onSelect={() => onSelect(workflow.id)}
                        />
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })
        ) : (
          <div className="rounded-[10px] border border-dashed border-line bg-slate-50 p-4 text-xs leading-5 text-muted" role={loading ? "status" : undefined}>
            {loading ? (
              <span className="flex items-center gap-2">
                <Loader2 aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" size={13} />
                Loading workflows...
              </span>
            ) : (
              <>
                <p>{activeWorkflow?.projectRoot ? "No workflows in this project yet." : "No workflows yet."}</p>
                <button className="mt-2 font-semibold text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300" type="button" onClick={onCreate}>
                  Create a workflow
                </button>
              </>
            )}
          </div>
        )}
          />
        </Suspense>
        {projectMenu ? (
          <PopupMenu
            aria-label={`${projectMenu.name} project actions`}
            className="fixed z-[80] w-52 rounded-lg border border-line bg-white p-1 shadow-panel"
            style={{ left: projectMenu.x, top: projectMenu.y }}
            onClose={(options) => {
              setProjectMenu(null);
              if (options?.restoreFocus) projectMenuTriggerRef.current?.focus?.();
            }}
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
          </PopupMenu>
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

// Browser tabs as the paired-device sync publishes them. The backend keeps only web
// addresses; previews, local files and blank tabs are dropped there.
export function phoneBrowserTabs(paths, activePath, browserTabs) {
  return paths.filter(path => browserTabs[path] && !browserTabs[path].private).map(path => {
    const tab = browserTabs[path];
    return { id: path, title: String(tab.title || ""), url: String(tab.url || ""), active: path === activePath,
      // The browser this tab runs in; phones that predate per-tab browsers ignore it.
      engine: webBrowserEngine(tab.agentSessionId ? tab.agentEngine || tab.engine : tab.engine),
      ...(tab.agentSessionId ? { owner: "rem", label: tab.agentLabel || "Rem" } : { owner: "you" }) };
  });
}

export function agentBrowserTab({ id, label, partition }) {
  return {
    agentLabel: typeof label === "string" && label.trim() ? label.trim().slice(0, 60) : "Rem",
    agentPartition: partition === "persist:raticode-rem" ? partition : "persist:raticode-rem",
    agentSessionId: id,
    error: "",
    focusLocation: false,
    loading: true,
    title: "",
    url: "about:blank",
  };
}

// Rem's tabs belong to live desktop sessions and cannot be restored later.
// Private tabs are never written down.
export function withoutAgentBrowserTabs(paths, activePath, browserTabs) {
  const agentPaths = new Set(Object.keys(browserTabs).filter((path) => browserTabs[path]?.agentSessionId || browserTabs[path]?.private));
  if (!agentPaths.size) return { paths, activePath, browserTabs };
  const kept = paths.filter((path) => !agentPaths.has(path));
  return {
    paths: kept,
    activePath: agentPaths.has(activePath) ? kept.at(-1) ?? "" : activePath,
    browserTabs: Object.fromEntries(Object.entries(browserTabs).filter(([path]) => !agentPaths.has(path))),
  };
}

const BROWSER_TAB_SAVED_FIELDS = new Set(["url", "title", "favicon", "history", "loading", "error"]);

// Browser keys the integrated browser handles while focus is inside it.
export function browserChromeOwnsShortcut(event) {
  if (!event?.target?.closest?.("[data-integrated-browser]")) return false;
  return Boolean(browserChromeShortcutAction(event, window.goferBrowser?.platform || "", { scoped: true }));
}

export function browserPolicyFromSettings(browser = {}) {
  return {
    engine: controlledEngine(browser.engine),
    searchUrl: browser.searchUrl,
    homepage: browser.homepage,
    remSignInSites: browser.remSignInSites || [],
    remAllowedSites: browser.remAllowedSites || [],
    remBlockedSites: browser.remBlockedSites || [],
    remConfirm: browser.remConfirm,
    remEvaluate: browser.remEvaluate !== false,
    remDownloads: browser.remDownloads,
    askWhereToSave: browser.askWhereToSave === true,
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
  if (!nextPath || /^(raticode-browser:|raticode-commit-diff:|raticode-terminal:)/.test(nextPath)) return current;
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

// `hidden` holds web pages another app opened with Raticode: Rem isn't told about them.
export function editorFileReferences(paths = [], workflowTabs = {}, hidden = new Set()) {
  return [...new Set(paths.map((path) => workflowTabs[path]?.sourcePath || path)
    .filter((path) => path && !hidden.has(path) && !/^(workflow-graph:|raticode-browser:|browser:|raticode-commit-diff:|raticode-terminal:)/.test(path)))];
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

export function TopBar({
  activeCodePath = "",
  workflow,
  view = "graph",
  onGraphToolbarTargetChange,
}) {
  const label = !workflow || (view === "code" && !activeCodePath)
    ? null
    : topBarLabelParts(workflow, view, activeCodePath);
  if (view === "graph" && workflow) {
    return <header className="studio-topbar min-w-0 shrink-0 border-b border-line bg-white px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink" title={label?.fullPath}>{workflow.name}</h2>
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
  const projects = mergeRecentProjects([], recentProjectRoots);
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
    {open ? <div role="menu" aria-label="Recent projects" className="absolute left-0 top-full z-[90] mt-1 max-h-[min(32rem,calc(100vh-160px))] w-full overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-panel" onKeyDown={event => { if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return; event.preventDefault(); const items = [...event.currentTarget.querySelectorAll('[role="menuitem"]')]; const index = items.indexOf(document.activeElement); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus(); }}>
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
  updateNotice,
  view = "graph",
  onApplyUpdate,
  onCheckForUpdates,
  onSelectProject,
  onToggleSettings,
  onToggleTheme,
  onMenuAction,
}) {
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
      </div>
      <div className="flex items-center gap-1">
        {hasUpdateBridge ? (
          <div className="flex min-w-0 items-center gap-2" aria-label="Application updates">
            {updateNotice?.message ? (
              <span role={updateNotice.type === "error" ? "alert" : "status"} title={updateNotice.message} className={`max-w-[min(28rem,35vw)] truncate text-xs ${updateNotice.type === "error" ? "text-red-700 dark:text-red-300" : "text-muted"}`}>
                {updateNotice.message}
              </span>
            ) : null}
            {updateState?.available ? (
              <button className="inline-flex h-7 shrink-0 items-center gap-2 rounded-md border border-indigo-200 bg-indigo-50 px-3 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70" disabled={Boolean(updateState.downloading || updateState.verifying)} title={updateButtonTitle(updateState)} type="button" onClick={onApplyUpdate}>
                {updateState.downloading || updateState.verifying ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                {updateButtonLabel(updateState)}
              </button>
            ) : null}
            <button className={`${buttonClass} shrink-0`} disabled={Boolean(updateState?.checking || updateState?.downloading || updateState?.verifying)} title={updateState?.error ? `Update check failed: ${updateState.error}` : "Check for updates"} aria-label="Check for updates" type="button" onClick={onCheckForUpdates}>
              <span className="relative">
                <RefreshCw size={16} className={updateState?.checking ? "animate-spin" : ""} />
                {updateState?.error && !updateState?.checking ? <span aria-hidden="true" className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-500" /> : null}
              </span>
            </button>
          </div>
        ) : null}
        <button aria-label="Open settings" aria-expanded={settingsOpen} className={`${buttonClass} ${settingsOpen ? "bg-slate-100 text-ink" : ""}`} title={`Settings (${formatKeybinding(settingBinding(settings, "settings.open"))})`} type="button" onClick={onToggleSettings}>
          <SettingsIcon size={16} />
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
    ["file.quickOpen", "Quick Open...", ""],
    ["file.openFolder", "Open Project...", "project.open"],
    ["file.recentProjects", "Recent Projects"],
    null,
    ["file.save", "Save", "Ctrl+S", "code-document"],
    ["file.close", "Close Editor", "Ctrl+W", "code-path"],
  ] },
  { label: "Edit", items: [
    ["edit.undo", "Undo", "Ctrl+Z"],
    ["edit.redo", "Redo", "Ctrl+Shift+Z"],
    null,
    ["edit.cut", "Cut", "Ctrl+X"],
    ["edit.copy", "Copy", "Ctrl+C"],
    ["edit.paste", "Paste", "Ctrl+V"],
    null,
    ["edit.find", "Find", "Ctrl+F", "code-path"],
    ["edit.replace", "Replace", "Ctrl+H", "code-document"],
    ["edit.toggleLineComment", "Toggle Line Comment", "Ctrl+/", "code-document"],
    ["edit.formatDocument", "Format Document", "Shift+Alt+F", "code-document"],
  ] },
  { label: "Selection", items: [
    ["selection.selectAll", "Select All", "Ctrl+A"],
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
    ["view.commandPalette", "Command Palette...", ""],
    null,
    ["view.graph", "Workflows", ""],
    ["view.code", "Files", ""],
    null,
    ["view.projectPane", "Project Files", "Ctrl+B", "project-check"],
    ["view.assistantPane", "Rem", "", "assistant-check"],
    ["view.dictate", "Dictate a Message", ""],
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
  const [focusedMenu, setFocusedMenu] = useState("File");
  const containerRef = useRef(null);
  const openerRef = useRef(null);
  const returnFocusRef = useRef(null);
  const menuFocusRef = useRef(false);
  useEffect(() => {
    if (openMenu && menuFocusRef.current) containerRef.current?.querySelector('[role="menu"] button:not(:disabled)')?.focus();
  }, [openMenu]);
  function closeMenu(restore = true) {
    setOpenMenu("");
    if (restore) openerRef.current?.focus();
  }
  function navigateMenu(event) {
    const trigger = event.target.closest?.("[data-menu-trigger]");
    const menu = event.target.closest?.('[role="menu"]');
    const triggers = [...containerRef.current.querySelectorAll("[data-menu-trigger]")];
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); return; }
    if (event.key === "Tab") { setOpenMenu(""); return; }
    if (trigger && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); menuFocusRef.current = true; openerRef.current = trigger;
      if (openMenu !== trigger.dataset.menuTrigger) setOpenMenu(trigger.dataset.menuTrigger);
      else containerRef.current.querySelector('[role="menu"] button:not(:disabled)')?.focus();
      return;
    }
    if (menu && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      const items = [...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')].filter(item => item.closest('[role="menu"]') === menu);
      const index = items.indexOf(event.target);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
      items[next]?.focus(); return;
    }
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      if (menu?.parentElement?.closest('[role="menu"]')) return;
      event.preventDefault();
      const index = triggers.findIndex(item => item.dataset.menuTrigger === (openMenu || trigger?.dataset.menuTrigger));
      const next = triggers[(index + (event.key === "ArrowLeft" ? -1 : 1) + triggers.length) % triggers.length];
      if (!next) return;
      openerRef.current = next;
      next.focus();
      if (openMenu) { menuFocusRef.current = true; setOpenMenu(next.dataset.menuTrigger); }
    }
  }
  useEffect(() => {
    if (!openMenu) return undefined;
    const dismiss = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpenMenu("");
    };
    const escape = (event) => {
      if (event.key === "Escape") closeMenu();
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
    <nav aria-label="Application menu" role="menubar" className="flex h-full items-center gap-0.5" ref={containerRef} onKeyDown={navigateMenu}>
      {APPLICATION_MENUS.map((menu) => (
        <div className="relative" key={menu.label}>
          <button
            data-menu-trigger={menu.label}
            role="menuitem"
            tabIndex={focusedMenu === menu.label ? 0 : -1}
            onFocus={() => setFocusedMenu(menu.label)}
            aria-expanded={openMenu === menu.label}
            aria-haspopup="menu"
            className={`rounded px-2 py-1 text-[13px] text-ink outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-brand ${openMenu === menu.label ? "bg-slate-100" : ""}`}
            type="button"
            onPointerDown={() => { returnFocusRef.current = document.activeElement; rememberEditingTarget(document.activeElement); menuFocusRef.current = false; }}
            onClick={event => {
              openerRef.current = event.currentTarget;
              if (event.detail === 0) menuFocusRef.current = true;
              setOpenMenu(current => current === menu.label ? "" : menu.label);
            }}
            onPointerEnter={event => {
              if (!openMenu) return;
              menuFocusRef.current = false;
              openerRef.current = event.currentTarget;
              setFocusedMenu(menu.label);
              setOpenMenu(menu.label);
            }}
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
                    returnFocusRef.current?.focus?.();
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
  "edit.find": "editor.find",
  "file.close": "file.close",
  "file.new": "file.new",
  "file.open": "file.open",
  "file.quickOpen": QUICK_OPEN_COMMAND_ID,
  "file.openFolder": "project.open",
  "file.save": "file.save",
  "help.settings": "settings.open",
  "terminal.toggle": "panel.toggle",
  "view.assistantPane": "view.toggleAssistantPane",
  "view.commandPalette": PALETTE_COMMAND_ID,
  "view.dictate": VOICE_COMMAND_ID,
  "view.code": "view.code",
  "view.graph": "view.graph",
  "view.panel": "panel.toggle",
  "view.projectPane": "view.toggleProjectPane",
};

function ApplicationMenuItem({ context, item, onSelect, settings }) {
  const [action, label, fallbackShortcut, condition] = item;
  const bindingId = APPLICATION_MENU_BINDINGS[action];
  const genericShortcuts = { "edit.undo": "Mod+KeyZ", "edit.redo": "Mod+Shift+KeyZ", "edit.cut": "Mod+KeyX", "edit.copy": "Mod+KeyC", "edit.paste": "Mod+KeyV", "selection.selectAll": "Mod+KeyA" };
  const shortcut = bindingId
    ? formatKeybinding(settingBinding(settings, bindingId))
    : (genericShortcuts[action] ? formatKeybinding(genericShortcuts[action]) : context.activeCodeDocument?.commands?.[action]?.shortcut)
      || (action.startsWith("edit.") || action.startsWith("selection.") ? "" : formatKeybinding(fallbackShortcut.replace("Ctrl", "Mod")));
  const capability = context.activeCodeDocument?.commands?.[action];
  const generalEdit = ["edit.undo", "edit.redo", "edit.cut", "edit.copy", "edit.paste", "selection.selectAll"].includes(action);
  const enabled = (generalEdit || capability?.supported !== false) && (!condition || condition.endsWith("-check")
    || (condition === "code" && context.view === "code")
    || (condition === "code-path" && context.view === "code" && Boolean(context.activeCodePath))
    || (condition === "code-document" && context.view === "code" && Boolean(context.activeCodeDocument)));
  const checked = condition === "graph-check" && context.view === "graph"
    || condition === "code-check" && context.view === "code"
    || condition === "project-check" && context.projectPaneVisible
    || condition === "assistant-check" && context.assistantPaneVisible;
  return (
    <button
      className="flex h-8 w-full items-center gap-3 rounded px-2 text-left text-[12px] hover:bg-slate-100 disabled:text-muted disabled:opacity-45 disabled:hover:bg-transparent"
      disabled={!enabled}
      title={capability?.supported === false ? "Unavailable for this document. No supporting language provider is loaded." : undefined}
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
  const parentRef = useRef(null);
  const submenuRef = useRef(null);
  const keyboardOpenRef = useRef(false);
  useEffect(() => { if (open && keyboardOpenRef.current) submenuRef.current?.querySelector("button")?.focus(); }, [open]);
  const labels = loadProjectLabels();
  const projects = mergeRecentProjects([], recentProjectRoots).map((root) => ({
    name: pathValue(labels, root)?.trim() || projectNameFromPath(root),
    root,
  }));
  return (
    <div className="relative" onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
      <button
        ref={parentRef}
        aria-expanded={open}
        aria-haspopup="menu"
        onKeyDown={event => {
          if (["ArrowRight", "Enter", " "].includes(event.key)) { event.preventDefault(); event.stopPropagation(); keyboardOpenRef.current = true; setOpen(true); }
        }}
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
        <div aria-label="Recent projects" className="absolute left-full top-[-6px] z-[101] min-w-[260px] rounded-md border border-line bg-white p-1.5 shadow-[0_10px_28px_rgba(15,23,42,0.18)]" role="menu" ref={submenuRef} onKeyDown={event => {
          if (["ArrowLeft", "Escape"].includes(event.key)) { event.preventDefault(); event.stopPropagation(); setOpen(false); parentRef.current?.focus(); }
        }}>
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

// Rem's chat in the side pane, used in place of threads when Settings > Layout
// asks for it. It docks and resizes like the threads pane.
function RemSidePane({ width, onResizeStart, onResizeKeyDown, children }) {
  return (
    <aside className="studio-rem-pane relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-l border-line" data-rem-pane="true" style={{ width }}>
      <div
        aria-label="Resize Rem pane"
        aria-orientation="vertical"
        aria-valuemax={520}
        aria-valuemin={300}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels wide`}
        className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition hover:bg-brand/40"
        role="separator"
        tabIndex={0}
        title="Resize Rem pane"
        onKeyDown={onResizeKeyDown}
        onPointerDown={onResizeStart}
      />
      {children}
    </aside>
  );
}

// A failed check must never read as "up to date": the main process returns its
// error in the state instead of rejecting.
export function updateCheckNotice(info) {
  if (!info) return { type: "error", message: "The update check returned no result. Try again." };
  if (info.error) return { type: "error", message: info.error };
  if (info.available) return { type: "success", message: `Raticode ${info.info?.version ?? "update"} is available` };
  if (info.info?.noReleases) return { type: "success", message: "No published Raticode releases yet" };
  return { type: "success", message: "Raticode is up to date" };
}

function opensMacInstaller(updateState) {
  return updateState?.platform === "darwin" && Boolean(updateState.info?.installerUrl);
}

export function manualUpdateNotice(updateState) {
  if (updateState?.managedBy) return `Update Raticode with ${updateState.managedBy}, for example yay -Syu or paru -Syu, then restart it.`;
  const steps = opensMacInstaller(updateState)
    ? "Quit Raticode, open the downloaded DMG, and drag Raticode into Applications to replace the old copy."
    : "Download and install the update from the release page.";
  return updateState?.manualReason ? `${updateState.manualReason} ${steps}` : steps;
}

function updateButtonLabel(updateState) {
  if (updateState?.managedBy) return `Update ${updateState?.info?.version ?? "available"} with ${updateState.managedBy}`;
  if (updateState?.supported === false) return opensMacInstaller(updateState) ? "Download Mac installer" : "Open update downloads";
  if (updateState?.downloaded) return "Restart to update";
  if (updateState?.verifying) return "Verifying update";
  if (updateState?.downloading) {
    const percent = Math.max(0, Math.min(100, updateState.progress?.percent ?? 0));
    return `Downloading ${Math.round(percent)}%`;
  }
  return `Update ${updateState?.info?.version ?? "available"}`;
}

function updateButtonTitle(updateState) {
  if (updateState?.managedBy) return `Your system package manager installed Raticode, so ${updateState.managedBy} updates it, for example with yay -Syu or paru -Syu. This opens the release notes.`;
  if (updateState?.supported === false) {
    const reason = updateState.manualReason ? `${updateState.manualReason} ` : "";
    return opensMacInstaller(updateState)
      ? `${reason}Download the Mac installer, quit Raticode, then open the DMG and drag Raticode into Applications. This copy requires manual installation.`
      : `${reason}Open the release page to download and install the update manually`;
  }
  if (updateState?.downloaded) return "Restart Raticode and apply the downloaded update";
  if (updateState?.verifying) return "macOS is checking the downloaded update's signature. Raticode restarts when it's done.";
  if (updateState?.downloading) return "Downloading update";
  return "Download, install, and restart Raticode";
}

const WORKFLOW_SOURCES = ["create", "import"];

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
  const [importFile, setImportFile] = useState(null);
  const [importError, setImportError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [starters, setStarters] = useState([]);
  const [startersError, setStartersError] = useState("");
  const [starterId, setStarterId] = useState("");
  const importInputRef = useRef(null);
  const nameInputRef = useRef(null);
  const starter = starters.find((item) => item.id === starterId) ?? null;
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
        if (!cancelled) setStarters(Array.isArray(payload.starters) ? payload.starters : []);
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
                  aria-describedby="workflow-starter-hint"
                  className="mt-1 h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none transition focus:border-indigo-500"
                  disabled={saving}
                  value={starterId}
                  onChange={(event) => setStarterId(event.target.value)}
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

