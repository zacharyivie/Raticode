// Rem's chat pane: the thread list, the open conversation and its composer.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, ChevronDown, FolderOpen, GitBranch, History, Info, Loader2, Paperclip, Plus, Search } from "lucide-react";
import { apiUrl } from "../lib/api.js";
import { askRemWebPageAttachment } from "../lib/browserAskRem.js";
import { CHAT_IMAGE_WARNING_MS, chatMessageForRequest, clipboardAttachmentFiles, isChatImage, largePasteFile, providerSupportsChatImages, readChatAttachments, transferContainsFiles, uploadChatAttachments } from "../lib/chatAttachments.js";
import { deleteChatDraft, getChatDraft, setChatDraft, threadDraftKey } from "../lib/chatDrafts.js";
import { notifyThreadTurn } from "../lib/backgroundNotices.js";
import { archiveThreadFromStorage, buildChatItems, bumpChatThread, chatStorageKeyFor, chatStreamRequestBody, chatThreadIndex, chatWorkflowContextForThread, deleteStoredChatThread, formatThreadDate, isThoughtGroupExpanded, loadAllChatThreads, loadChatMessages, loadChatThread, parseChatStreamEvent, permanentDeleteMessage, persistChatThreads, purgeThreadFromArchive, removeTrailingDuplicateOutputThought, scopeChatThreadToProject, threadReadPatch, threadTitleFromMessage, uniqueClientId, withLiveReply } from "../lib/chatThreads.js";
import { fetchChatTurn } from "../lib/chatTransport.js";
import { ndjsonBatches } from "../lib/ndjson.js";
import { generateConventionalCommit } from "../lib/commitMessage.js";
import { createConversationCache } from "../lib/conversationCache.js";
import { conversationRepository, pageStart } from "../lib/conversationRepository.js";
import { orderDeviceMessages } from "../lib/deviceMessages.js";
import { copyForkAttachments, forkThreadHistory, isForkableMessage } from "../lib/forkThread.js";
import { startGenerationJob } from "../lib/generationJobs.js";
import { ipcErrorMessage } from "../lib/ipcError.js";
import { assistantMarkdownSourcePath, loadProjectLabels, mergeRecentProjects, projectNameFromPath } from "../lib/projects.js";
import { providerPermissionDefault, providerPermissionOptions } from "../lib/providerPermissions.js";
import { remThreadWasCleared, sendManagedRemInstruction } from "../lib/remHomeStore.js";
import { previousRemModelSelection, remModelSelection, remModelSwitchBanner } from "../lib/remModelSelection.js";
import { DEFAULT_REM_RESOURCES, remResourceError, snapshotRemResources } from "../lib/remResources.js";
import { generateReportTheme, reportThemeContext } from "../lib/reportThemes.js";
import { DEFAULT_APP_SETTINGS } from "../lib/settings.js";
import { accountChangeWarning, accountMenu, accountSwitchLabel, DEFAULT_SIGN_IN, threadAccountChoice, turnAccount, withThreadAccount } from "../lib/threadAccounts.js";
import { archiveRetentionDays, cachedThreadScopes, expiredArchivedThreads, organizeThreads } from "../lib/threadActivity.js";
import { threadActionPatch } from "../lib/threadSelection.js";
import { signInProviderAccount, useProviderAccountList } from "../lib/useProviderAccountList.js";
import { pathValue, pathWithin, samePath, uniquePaths } from "../lib/workspacePaths.js";
import ChatComposer from "./ChatComposer.jsx";
import { ForkableMessage, ThoughtGroup } from "./ChatMessages.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import { ThreadContextSent } from "./ContextSent.jsx";
import { Dialog } from "./Dialog.jsx";
import { PopupMenu } from "./PopupMenu.jsx";
import { ProviderModelEffortFields, useProviderCapabilities } from "./ProviderModelEffortFields.jsx";
import RatFaceIcon from "./RatFaceIcon.jsx";
import { NewMessagesPill } from "./RemRichParts.jsx";
import RemToolsMenu from "./RemToolsMenu.jsx";
import ThreadHistoryMatch from "./ThreadHistoryMatch.jsx";
import ThreadSearch from "./ThreadSearch.jsx";
import { ThreadList as ThreadListView, ThreadSections as ThreadSectionsView } from "./ThreadSections.jsx";

// The newly arrived message that ended a turn run on another device or by Rem. A turn
// stopped there isn't announced; one that ended without a new message counts as done.
const isTurnEnd = message => ["final", "error", "stopped"].includes(message?.kind);
function notifyDeviceTurn(metadata, ended) {
  if (ended?.kind === "stopped") return;
  notifyThreadTurn({ threadId: metadata.id, title: metadata.title, outcome: ended?.kind === "error" ? "failed" : "done", turn: ended?.id || metadata.updatedAt });
}

export function ChatPane({
  onShowRem,
  swarmProjectPaths = [],
  activeWorkflowId,
  activeProjectRoot,
  assistantDefaults = {},
  archiveRetention = "never",
  memorySettings = DEFAULT_APP_SETTINGS.memory,
  agentBrowser = DEFAULT_APP_SETTINGS.browser.agentBrowser,
  audioInputDeviceId = "default",
  composerFocusRequest = 0,
  onNewThreadResourcesChange,
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
  const [confirmAction, confirmDialog] = useConfirm();
  const prospectiveProjectRoot = String(activeProjectRoot ?? workflow?.projectRoot ?? "").trim();
  const chatScrollRef = useRef(null);
  const chatPinnedToBottomRef = useRef(true);
  // Messages that arrived while you read further up. They are counted after the newest
  // conversation row you saw, so pages loaded above it never count.
  const [unseenMessages, setUnseenMessages] = useState(0);
  const seenConversationRef = useRef({ threadId: null, keys: new Set() });
  // Where a smooth jump to the newest message has reached: it passes through the middle
  // of the chat, which must not count as scrolling up to read.
  const jumpToLatestRef = useRef(null);
  const renderedConversationRef = useRef({ threadId: null, textKey: "" });
  const conversationMenuRef = useRef(null);
  const threadInfoRef = useRef(null);
  const dragDepthRef = useRef(0);
  const scopeMenuRef = useRef(null);
  const scopeTriggerRef = useRef(null);
  // Unsent text per thread, saved on this computer until it is sent or cleared (chatDrafts.js).
  // Only these two helpers call setDraft: loadThreadDraft shows a thread's saved text when it
  // opens, and changeThreadDraft saves every other change, so a switch never brings back old text.
  // shownDraftRef names the thread whose text the composer holds, even before activeThreadId moves.
  const [draft, setDraft] = useState("");
  const shownDraftRef = useRef({ key: threadDraftKey(null), text: "" });
  const loadThreadDraft = useCallback(threadId => {
    const key = threadDraftKey(threadId);
    const text = getChatDraft(key);
    shownDraftRef.current = { key, text };
    setDraft(text);
  }, []);
  // next is the new text, or a function of the thread's current text.
  const changeThreadDraft = useCallback((threadId, next) => {
    const key = threadDraftKey(threadId);
    const shown = shownDraftRef.current.key === key;
    const current = shown ? shownDraftRef.current.text : getChatDraft(key);
    const text = String((typeof next === "function" ? next(current) : next) ?? "");
    if (text === current) return;
    setChatDraft(key, text);
    if (!shown) return;
    shownDraftRef.current = { key, text };
    setDraft(text);
  }, []);
  const [attachments, setAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [attachmentWarning, setAttachmentWarning] = useState(null);
  const [contextSendThread, setContextSendThread] = useState(null);
  const [pendingRemContext, setPendingRemContext] = useState(null);
  const [contextFocusRequest, setContextFocusRequest] = useState(0);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [providerId, setProviderId] = useState(assistantDefaults.provider || "codex");
  const [permissionsByProvider, setPermissionsByProvider] = useState({});
  // The new thread's Account menu choices, one per provider, until it starts.
  const [draftAccounts, setDraftAccounts] = useState({});
  const { accounts: providerAccountList } = useProviderAccountList();
  const [accountSignInError, setAccountSignInError] = useState("");
  const [machines, setMachines] = useState([]);
  const [draftMachine, setDraftMachine] = useState("");
  useEffect(() => {
    const update = event => setMachines(Array.isArray(event.detail) ? event.detail : []);
    window.addEventListener("gofer:device-machines", update);
    return () => window.removeEventListener("gofer:device-machines", update);
  }, []);
  const [model, setModel] = useState(assistantDefaults.model || "");
  const [effort, setEffort] = useState(assistantDefaults.effort || "");
  const {
    capabilities: providers,
    error: providerDiscoveryError,
    loading: providersLoading,
    refresh: refreshProviders,
  } = useProviderCapabilities(prospectiveProjectRoot);
  const providerCapability = providers.find(item => item.id === providerId);
  const supportsImages = providerSupportsChatImages(providerId, providerCapability, model);
  useEffect(() => {
    if (!attachmentWarning) return undefined;
    const timer = window.setTimeout(() => setAttachmentWarning(null), CHAT_IMAGE_WARNING_MS);
    return () => window.clearTimeout(timer);
  }, [attachmentWarning]);
  useEffect(() => {
    if (supportsImages || !attachments.some(isChatImage)) return;
    setAttachments(current => current.filter(item => !isChatImage(item)));
    setAttachmentWarning({ text: `${providerCapability?.displayName || providerId} can't accept images with this selection.` });
  }, [supportsImages, attachments, providerId, providerCapability?.displayName]);
  const permissionOptions = providerPermissionOptions(providerId, providerCapability);
  const [threads, setThreads] = useState([]);
  const [renamingThread, setRenamingThread] = useState(null);
  const [launchedThreadId, setLaunchedThreadId] = useState(null);
  const forkInProgressRef = useRef(false);
  const [forkStatus, setForkStatus] = useState("");
  useEffect(() => {
    if (!launchedThreadId) return;
    const timer = window.setTimeout(() => setLaunchedThreadId(null), 1200);
    return () => window.clearTimeout(timer);
  }, [launchedThreadId]);
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
  useEffect(() => {
    const receive = ({ detail }) => {
      const { messages, running, activeTurn, completed } = detail;
      let { metadata } = detail;
      if (remThreadWasCleared(metadata.id)) return;
      if (!chatAbortControllersRef.current[metadata.id] && typeof running === "boolean") {
        // A turn started elsewhere, or a Rem task that finished while this window
        // wasn't watching, ends unread unless it is open here.
        if (!running && (completed || deviceRunningThreadsRef.current.has(metadata.id))) {
          metadata = { ...metadata, ...threadReadPatch(activeThreadIdRef.current !== metadata.id), archived: false };
          // Rem task threads are announced by the task's own notice.
          if (!metadata.managedRunId) notifyDeviceTurn(metadata, messages.findLast(isTurnEnd));
        }
        if (running) deviceRunningThreadsRef.current.add(metadata.id);
        else deviceRunningThreadsRef.current.delete(metadata.id);
      }
      setThreads(current => current.some(t => t.id === metadata.id)
        ? current.map(t => t.id === metadata.id ? metadata : t) : [metadata, ...current]);
      // Read on the phone: the finished-reply marker here goes too.
      if (metadata.unread === false) {
        setChatStateByThread(current => current[metadata.id]?.hasNewResponse
          ? { ...current, [metadata.id]: { ...current[metadata.id], hasNewResponse: false } } : current);
      }
      if (activeThreadIdRef.current === metadata.id) {
        setProviderId(metadata.provider || "codex");
        setModel(metadata.model || "");
      }
      if (messages.length) conversationCacheRef.current.hydrate(metadata.id, messages, { recent: true, device: true, authoritative: Boolean(metadata.managedRunId) });
      if (!chatAbortControllersRef.current[metadata.id] && typeof running === "boolean") {
        if (activeTurn && (!activeChatTurnsRef.current[metadata.id] || activeChatTurnsRef.current[metadata.id].remote)) activeChatTurnsRef.current[metadata.id] = { turnId: activeTurn, ready: true, remote: true };
        else if (activeChatTurnsRef.current[metadata.id]?.remote) delete activeChatTurnsRef.current[metadata.id];
        setChatStateByThread(current => ({ ...current, [metadata.id]: { ...current[metadata.id], deviceRunning: running, ...(metadata.managedRunId && !running ? { sending: false } : {}) } }));
      }
    };
    const removed = ({ detail: { id } }) => {
      deletedChatThreadIdsRef.current.add(id);
      deleteChatDraft(threadDraftKey(id));
      chatAbortControllersRef.current[id]?.abort();
      delete activeChatTurnsRef.current[id];
      conversationCacheRef.current.remove(id);
      setThreads(current => current.filter(t => t.id !== id));
      setChatStateByThread(current => { const next = { ...current }; delete next[id]; return next; });
      if (activeThreadIdRef.current === id) { activeThreadIdRef.current = null; setActiveThreadId(null); }
    };
    window.addEventListener("gofer:device-thread-sync", receive);
    window.addEventListener("gofer:device-thread-deleted", removed);
    return () => {
      window.removeEventListener("gofer:device-thread-sync", receive);
      window.removeEventListener("gofer:device-thread-deleted", removed);
    };
  }, []);
  const deviceRunningThreadsRef = useRef(new Set());
  const archiveClearingRef = useRef(false);
  const clearArchivedThreadsRef = useRef(null);
  const [chatStateByThread, setChatStateByThread] = useState({});
  const [chatAnnouncementByThread, setChatAnnouncementByThread] = useState({});
  const [backgroundChatAnnouncement, setBackgroundChatAnnouncement] = useState("");
  const [liveTurnByThread, setLiveTurnByThread] = useState({});
  const [expandedThoughtGroups, setExpandedThoughtGroups] = useState({});
  const [lazyThoughts, setLazyThoughts] = useState({});
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false);
  const [threadInfoOpen, setThreadInfoOpen] = useState(false);
  const [scopeMenuOpen, setScopeMenuOpen] = useState(false);
  const [resourcesOpen, setResourcesOpen] = useState(false);
  const [newThreadResources, setNewThreadResources] = useState(null);
  const homeResources = newThreadResources ?? assistantDefaults.resources ?? DEFAULT_REM_RESOURCES;
  const defaultGlobalScope = assistantDefaults.defaultScope === "global";
  const [homeProjectRoot, setHomeProjectRoot] = useState(defaultGlobalScope ? "" : prospectiveProjectRoot);
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
  const globalScope = activeThread ? activeThread.scopeMode === "global" : homeProjectRoot === "" && defaultGlobalScope;
  const scopedProjectName = (globalScope ? "Global" : "") || pathValue(projectLabels, scopedProjectRoot)?.trim()
    || activeThread?.projectName
    || (scopedProjectRoot ? projectNameFromPath(scopedProjectRoot) : "No project");
  const [scopeWorktrees, setScopeWorktrees] = useState([]);
  const [scopeAvailableRoots, setScopeAvailableRoots] = useState([]);
  const [scopeLoading, setScopeLoading] = useState(false);
  const scopeRoots = mergeRecentProjects(
    [scopedProjectRoot, prospectiveProjectRoot].filter(Boolean),
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
  const threadChatState = chatStateByThread[activeThreadId] || {};
  const chatState = { ...threadChatState, sending: Boolean(threadChatState.sending || threadChatState.deviceRunning), error: threadChatState.error || "" };
  const chatAnnouncement = activeThreadId
    ? chatAnnouncementByThread[activeThreadId] ?? ""
    : "";
  const liveTurn = activeThreadId ? liveTurnByThread[activeThreadId] : null;
  // The picker's Account menu: this thread's account of the selected provider. A thread
  // that a paired desktop runs uses that desktop's accounts, so it has no menu here.
  const accountChoice = threadAccountChoice(activeThread ?? { accountsByProvider: draftAccounts }, providerId);
  const remAccountSetting = turnAccount({ provider: providerId, providerAccounts: assistantDefaults.providerAccounts, claudeAccount: assistantDefaults.claudeAccount }) || "";
  const threadAccountMenu = (activeThread ? activeThread.machine : draftMachine) ? null
    : accountMenu({ provider: providerId, accounts: providerAccountList, remSetting: remAccountSetting, choice: accountChoice });
  const lastSentSelection = messages.findLast(message => message.role === "user")?.remModelSelection;
  const effectiveAccount = choice => choice === DEFAULT_SIGN_IN ? "" : choice || remAccountSetting;
  // Changing the account of a started thread starts a new provider session, as switching providers does.
  const accountWarning = threadAccountMenu && lastSentSelection?.provider === providerId
    && effectiveAccount(lastSentSelection.account || "") !== effectiveAccount(accountChoice)
    ? accountChangeWarning(providerCapability?.displayName || providerId, accountSwitchLabel(accountChoice, remAccountSetting)) : "";
  const visibleMessages = useMemo(() => {
    const start = historyState.threadId === activeThreadId && historyState.firstMessageId
      ? messages.findIndex(message => message.id === historyState.firstMessageId) : -1;
    const emptyThreadOpened = historyState.threadId === activeThreadId && !historyState.loading && !historyState.firstMessageId;
    return repository.durable ? messages.slice(start >= 0 ? start : emptyThreadOpened ? 0 : pageStart(messages)) : messages;
  }, [messages, historyState.threadId, historyState.firstMessageId, historyState.loading, activeThreadId, repository.durable]);
  const thoughtStubs = historyState.threadId === activeThreadId ? historyState.thoughts : undefined;
  const chatItems = useMemo(() => {
    // Earlier thoughts stay in storage until someone expands their placeholder.
    const ordered = orderDeviceMessages(visibleMessages);
    const shown = new Set(ordered.map(message => message.id));
    const stubs = new Map((thoughtStubs || []).filter(stub => !stub.ids.some(id => shown.has(id))).map(stub => [stub.anchorId, stub]));
    return buildChatItems(stubs.size ? ordered.flatMap(message => stubs.has(message.id) ? [{ id: stubs.get(message.id).id, kind: "thought", lazy: stubs.get(message.id) }, message] : message) : ordered);
  }, [visibleMessages, thoughtStubs]);
  const conversationItems = useMemo(() => {
    const items = chatState.sending ? withLiveReply(chatItems) : chatItems;
    if (!liveTurn || items.some((item) => item.message?.id === liveTurn.id)) return items;
    return [...items, { type: "message", message: liveTurn }];
  }, [chatItems, liveTurn, chatState.sending]);
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
      setHistoryState(current => ({ threadId, loading: false, hasMore: page.hasMore, before: page.before, firstMessageId: page.messages[0]?.id, error: "",
        thoughts: [...page.thoughts, ...(before === Infinity ? [] : current.thoughts || [])] }));
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

  useLayoutEffect(() => {
    if (activeSearchTarget) return;
    const seen = seenConversationRef.current;
    if (seen.threadId !== activeThreadId || chatPinnedToBottomRef.current) {
      seenConversationRef.current = { threadId: activeThreadId, keys: conversationItemKeys(conversationItems) };
      setUnseenMessages(0);
      return;
    }
    const count = unseenConversationMessages(conversationItems, seen.keys);
    if (count != null) setUnseenMessages(count);
  }, [activeThreadId, conversationItems, activeSearchTarget]);

  function markConversationSeen() {
    seenConversationRef.current = { threadId: activeThreadId, keys: conversationItemKeys(conversationItems) };
    setUnseenMessages(0);
  }

  function jumpToLatestMessages() {
    const element = chatScrollRef.current;
    chatPinnedToBottomRef.current = true;
    markConversationSeen();
    if (!element) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion || typeof element.scrollTo !== "function") {
      jumpToLatestRef.current = null;
      scrollConversationToBottom(element);
      return;
    }
    jumpToLatestRef.current = { top: element.scrollTop };
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }

  useEffect(() => {
    if (!activeThreadId) setHomeProjectRoot(defaultGlobalScope ? "" : prospectiveProjectRoot);
  }, [activeThreadId, prospectiveProjectRoot, defaultGlobalScope]);

  useEffect(() => {
    const current = providers.find((provider) => provider.id === providerId);
    const nextProvider = current;
    if (!nextProvider?.available || nextProvider.discoveryStatus !== "ready" || chatState.sending) return;
    const nextModel =
      nextProvider.models?.find((item) => item.id === model) ??
      nextProvider.models?.find((item) => item.id === nextProvider.defaultModel) ??
      nextProvider.models?.[0];
    if (!nextModel) return;
    if (!model) {
      setModel(nextModel.id);
      if (!effort && nextProvider.defaultEffortOverride) setEffort(nextModel.defaultEffort ?? "");
    }
    if (effort && !nextModel.efforts?.some((item) => item.id === effort)) {
      setEffort(nextModel.defaultEffort ?? "");
    }
  }, [effort, model, providerId, providers, chatState.sending]);

  useEffect(() => {
    if (!activeThreadId) {
      historyRequestRef.current += 1;
      historyLoadingRef.current = false;
      setHistoryState({ threadId: null, loading: false, hasMore: false, before: Infinity, error: "" });
      loadThreadDraft(activeThreadId);
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
    if (repository.durable && !chatAbortControllersRef.current[activeThreadId]) conversationCacheRef.current.trim(activeThreadId);
    conversationCacheRef.current.get(activeThreadId);
    conversationCacheRef.current.activate([activeThreadId, ...Object.keys(chatAbortControllersRef.current)]);
    loadThreadDraft(activeThreadId);
    setAttachments([]);
    setAttachmentError("");
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    setExpandedThoughtGroups({});
    setLazyThoughts({});
    setConversationMenuOpen(false);
    setScopeMenuOpen(false);
  }, [activeThreadId, loadHistoryPage, loadThreadDraft, repository.durable]);

  useEffect(() => {
    conversationCacheRef.current.activate([
      activeThreadId,
      ...Object.keys(chatStateByThread).filter((id) => (chatStateByThread[id]?.sending || chatStateByThread[id]?.deviceRunning)),
    ]);
  }, [activeThreadId, chatStateByThread]);


  function closeThreadSearch() {
    setThreadSearchOpen(false);
    threadSearchButtonRef.current?.focus();
  }

  function addAttachments(files) {
    const incoming = Array.from(files || []);
    if (!supportsImages && incoming.some(isChatImage)) {
      setAttachmentWarning({ text: `${providerCapability?.displayName || providerId} can't accept images with this selection.` });
    }
    const result = readChatAttachments(incoming.filter(file => supportsImages || !isChatImage(file)), attachments);
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

  // Loaded on mount so a paste can check for copied files synchronously.
  const fileAttachmentsRef = useRef(null);
  useEffect(() => { void import("../lib/fileAttachments.js").then(module => { fileAttachmentsRef.current = module; }); }, []);
  function attachFilePaths(paths) {
    import("../lib/fileAttachments.js").then(({ readFileAttachments }) => readFileAttachments(paths))
      .then(files => addAttachmentsRef.current(files), error => setAttachmentError(error.message || "Could not attach the file."));
  }
  const addAttachmentsRef = useRef(addAttachments);
  addAttachmentsRef.current = addAttachments;
  const attachFilePathsRef = useRef(attachFilePaths);
  attachFilePathsRef.current = attachFilePaths;
  useEffect(() => {
    const receive = event => {
      const { paths = [], files = [], draft = "" } = event.detail || {};
      if (paths.length) attachFilePathsRef.current(paths);
      if (files.length) addAttachmentsRef.current(files);
      if (files.length && draft) changeThreadDraft(activeThreadIdRef.current, current => (current.trim() ? current : draft));
    };
    window.addEventListener("gofer:rem-thread-attach-files", receive);
    return () => window.removeEventListener("gofer:rem-thread-attach-files", receive);
  }, [changeThreadDraft]);

  function handleClipboardPaste(event) {
    const files = clipboardAttachmentFiles(event.clipboardData);
    const pastedText = event.clipboardData?.getData?.("text/plain") || "";
    // Files copied in Raticode paste as attachments instead of their paths.
    const copiedPaths = files.length ? [] : fileAttachmentsRef.current?.copiedFilePaths(pastedText) || [];
    if (copiedPaths.length) {
      event.preventDefault();
      attachFilePaths(copiedPaths);
      return;
    }
    if (chatState.sending && !files.length) return;
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
  const toggleThoughtGroup = useCallback(async (id, replyId, expanded, lazy) => {
    setExpandedThoughtGroups(current => ({ ...current, [id]: { replyId, expanded: !expanded } }));
    if (!lazy) return;
    const threadId = activeThreadIdRef.current;
    const loaded = value => activeThreadIdRef.current === threadId && setLazyThoughts(current => ({ ...current, [lazy.id]: value }));
    loaded({});
    try { loaded({ thoughts: buildChatItems(await repository.thoughts(threadId, lazy.ids))[0]?.thoughts || [] }); }
    catch (error) { loaded({ error: error.message || "Thoughts could not be loaded." }); }
  }, [repository]);

  // Thread info closes on a click outside it, on Escape, and when another thread opens.
  useEffect(() => { setThreadInfoOpen(false); }, [activeThreadId]);
  useEffect(() => {
    if (!threadInfoOpen) return undefined;
    function handlePointerDown(event) {
      if (!threadInfoRef.current?.contains(event.target)) setThreadInfoOpen(false);
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") setThreadInfoOpen(false);
    }
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [threadInfoOpen]);

  useEffect(() => {
    if (!conversationMenuOpen || renamingThread) return undefined;

    function handlePointerDown(event) {
      if (conversationMenuRef.current?.contains(event.target)) return;
      setConversationMenuOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [conversationMenuOpen, renamingThread]);

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

  async function sendMessage(editedMessage = null, options = {}) {
    const sourceMessages = options.targetThread
      ? conversationCacheRef.current.get(options.targetThread.id)
      : messages;
    const editedMessageIndex = editedMessage
      ? sourceMessages.findIndex((message) => message.id === editedMessage.id && message.role === "user")
      : -1;
    const originalMessage = editedMessageIndex >= 0 ? sourceMessages[editedMessageIndex] : null;
    const text = originalMessage ? String(editedMessage.body ?? "").trim() : options.text ?? draft.trim();
    const selectedAttachments = originalMessage ? editedMessage.attachments ?? originalMessage.attachments ?? [] : options.targetThread ? [] : attachments;
    const hasMessageAttachments = Boolean(
      originalMessage?.attachments?.length || selectedAttachments.length,
    );
    const historyBlocked = activeThreadId && historyState.threadId === activeThreadId
      && (historyLoadingRef.current || historyState.error);
    if ((!text && !hasMessageAttachments) || (!options.targetThread && (chatState.sending || historyBlocked))) return;
    const managedThread = options.targetThread ?? activeThread;
    if (managedThread?.managedRunId) {
      try {
        if (hasMessageAttachments) throw new Error("Include file paths in this managed task's instruction.");
        await sendManagedRemInstruction(managedThread.id, text);
        if (!options.targetThread) changeThreadDraft(managedThread.id, current => current.trim() === text ? "" : current);
      } catch (error) {
        setChatStateByThread(current => ({ ...current, [managedThread.id]: {
          ...current[managedThread.id], error: error.message,
        } }));
      }
      return;
    }
    if (!options.targetThread && requiresPermissionChoice) return;
    const turnResources = snapshotRemResources(options.targetThread?.resources ?? activeThread?.resources ?? homeResources);
    const resourceError = remResourceError(turnResources);
    if (resourceError) { setAttachmentError(resourceError); return; }
    returnToLatestMessages();
    const clientTurnStartedAt = Date.now();
    const turnSummaryId = uniqueClientId();
    const targetThread = options.targetThread ?? activeThread ?? createThread(scopedProjectRoot, { background: options.background, global: globalScope });
    const targetThreadId = targetThread.id;
    if (options.background && !activeThread && !options.targetThread) {
      setLaunchedThreadId(targetThreadId);
      setBackgroundChatAnnouncement("Thread started in the background.");
    }
    const workflowContext = chatWorkflowContextForThread(targetThread, workflows, openFiles);
    const turnProvider = targetThread.provider || providerId;
    const turnModel = targetThread.model ?? model;
    const turnEffort = targetThread.effort ?? effort;
    const turnCapability = providers.find(item => item.id === turnProvider);
    const requestedPermission = targetThread.permissionsByProvider?.[turnProvider];
    const turnPermission = providerPermissionOptions(turnProvider, turnCapability).some(([id]) => id === requestedPermission)
      ? requestedPermission : providerPermissionDefault(turnProvider, turnCapability);
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
    let previousSelection;
    try {
      messageAttachments = await uploadChatAttachments(selectedAttachments, targetThreadId);
      if (options.targetThread || activeThread) {
        previousSelection = await previousRemModelSelection(repository, targetThreadId, sourceMessages, originalMessage?.id);
      }
    } catch (error) {
      setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
      setChatStateByThread((current) => ({
        ...current,
        [targetThreadId]: {
          sending: false,
          error: error instanceof Error ? error.message : "The message could not be prepared.",
          hasNewResponse: false,
        },
      }));
      return;
    }
    const titleSource = text || `Attached ${messageAttachments.map((item) => item.name).join(", ")}`;
    const targetThreadTitle =
      targetThread.title && (targetThread.titleCustomized || targetThread.title !== "New thread")
        ? targetThread.title
        : threadTitleFromMessage(titleSource);
    deletedChatThreadIdsRef.current.delete(targetThreadId);

    const turnThreadAccount = targetThread.machine ? "" : threadAccountChoice(targetThread, turnProvider);
    const selection = remModelSelection(turnProvider, turnModel, turnEffort, turnCapability, turnThreadAccount);
    const userMessage = {
      ...(originalMessage ?? { id: uniqueClientId(), role: "user" }),
      body: text,
      attachments: messageAttachments,
      remModelSelection: selection,
      modelSwitchBanner: remModelSwitchBanner(previousSelection, selection, turnCapability?.displayName),
    };
    const nextMessages = originalMessage
      ? [...sourceMessages.slice(0, editedMessageIndex), userMessage]
      : [...sourceMessages, userMessage];
    updateThreadMessages(targetThreadId, nextMessages);
    // Continuing an archived thread brings it back into the thread list.
    setThreads(current => bumpChatThread(current, targetThreadId, undefined, { archived: false, unread: false }));
    updateThreadTitleFromMessage(targetThreadId, titleSource);
    if (!options.targetThread && !originalMessage) {
      changeThreadDraft(activeThreadId, "");
      setAttachments([]);
    }
    if (originalMessage) setExpandedThoughtGroups({});
    if (!options.background) setBackgroundChatAnnouncement("");
    setChatAnnouncementByThread((current) => ({ ...current, [targetThreadId]: "" }));
    let contextBoundaryId = userMessage.id;
    const thoughtGroupId = uniqueClientId();
    // Web pages Rem reads become source cards under its reply.
    const sourceTools = import("../lib/chatSources.js").catch(() => null);
    function attachTurnSources(body) {
      void sourceTools.then(tools => tools && updateThreadMessages(targetThreadId, current => {
        const index = current.findLastIndex(message => message.kind === "final" && message.body === body);
        const sources = index < 0 ? [] : tools.collectTurnSources(current.filter(message => message.groupId === thoughtGroupId), body);
        return sources.length ? current.with(index, { ...current[index], sources }) : current;
      }));
    }
    let turnSummaryReceived = false;
    let resumeAvailable = false;
    // "done" or "failed" once the turn ends; a stopped turn stays "".
    let turnOutcome = "";
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
      const threadProjects = [];
      for (const root of mergeRecentProjects(targetThread.projectRoot ? [targetThread.projectRoot] : [], scopeRoots)) {
        const workspace = window.goferDesktop?.workspace;
        if (workspace?.getPathInfo && !(await workspace.getPathInfo(root))?.isDirectory) continue;
        await workspace?.trustProjectRoot?.(root);
        threadProjects.push({ root, name: pathValue(projectLabels, root)?.trim() || projectNameFromPath(root), grantId: workspace?.pathGrantForApi?.(root) });
      }
      let selectedBrainConfig = { enabled: memorySettings.secondBrainEnabled, root: memorySettings.secondBrainRoot, format: memorySettings.secondBrainFormat, grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(memorySettings.secondBrainRoot) };
      if (selectedBrainConfig.enabled) {
        const { threadBrainConfig } = await import("../lib/secondBrainApi.js");
        selectedBrainConfig = await threadBrainConfig(turnResources, selectedBrainConfig);
      }
      if (activeTurn.stopRequested) throw new DOMException("Rem stopped", "AbortError");
      const response = await fetchChatTurn(chatStreamRequestBody({
          conversationId: targetThreadId,
          resetSession: Boolean(originalMessage),
          forkedFrom: targetThread.forkedFromThreadId,
          turnId: activeTurn.turnId,
          permissionMode: turnPermission,
          machine: targetThread.machine,
          provider: turnProvider,
          model: turnModel,
          effort: turnEffort || undefined,
          providerAccounts: assistantDefaults.providerAccounts || undefined,
          threadAccount: turnThreadAccount || undefined,
          messages: requestMessages
            // Narration and tool steps stay visible but are not re-sent; final replies are.
            .filter((message) => !["turn-summary", "error", "thought"].includes(message.kind))
            .map(chatMessageForRequest),
          workflow: {
            ...workflowContext,
            remThreads: { global: targetThread.scopeMode === "global", projects: threadProjects, spawned: Boolean(targetThread.parentThreadId) },
            remSwarmAccess: {
              enabled: assistantDefaults.swarmAccessEnabled !== false,
              grantId: window.goferDesktop?.workspace?.pathGrantForApi?.(workflowContext.projectRoot),
              ...(assistantDefaults.swarmAccessEnabled !== false ? {
                workspaceGrants: Object.fromEntries(uniquePaths(swarmProjectPaths).flatMap(path => {
                  const grant = window.goferDesktop?.workspace?.pathGrantForApi?.(path);
                  return grant ? [[path, grant]] : [];
                })),
              } : {}),
            },
            remReportTheme: reportThemeContext(memorySettings),
            remSecondBrain: selectedBrainConfig,
            remResources: turnResources,
            remBrowser: { target: agentBrowser },
            remStreamText: true,
            id: `workflow-assistant:${targetThreadId}`,
            chatThreadId: targetThreadId,
            // Names this thread when its tests wait behind another thread's.
            chatThreadTitle: targetThreadTitle,
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
      const reader = response.body.getReader();

      for await (const lines of ndjsonBatches(reader)) {
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
                .map(message => ({ ...message, id: uniqueClientId(), kind: message.role === "system" ? "continuation-context" : undefined,
                  ...(message.role === "user" ? { remModelSelection: selection } : {}),
                }));
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

            if (event.type === "project-scope") {
              setThreads(current => current.map(thread => thread.id === targetThreadId
                ? scopeChatThreadToProject(thread, event.projectRoot, workflows, null, event.projectName) : thread));
              continue;
            }
            if (event.type === "new-thread") {
              remContextActionsRef.current.spawnThread(event, targetThread);
              continue;
            }
            if (event.type === "thought") {
              const deltaStreamId = typeof event.deltaStreamId === "string" ? event.deltaStreamId : "";
              const thought = deltaStreamId ? String(event.text ?? "") : String(event.text ?? "").trim();
              if (!thought) continue;
              appendAssistantMessage(thought, "thought", {
                groupId: thoughtGroupId,
                deltaStreamId: deltaStreamId || undefined,
                trace: event.trace && typeof event.trace === "object" ? event.trace : undefined,
            });
          } else if (event.type === "recovery") {
            const body = event.partial?.body ?? "";
            if (body.trim()) appendAssistantMessage(body, "final", { interrupted: true });
            if (event.partial?.changes) appendAssistantMessage("", "turn-summary", {
              id: uniqueClientId(), changes: event.partial.changes,
              completedAt: event.partial.completedAt, durationMs: event.partial.durationMs,
            });
            appendAssistantMessage(event.message || "Rem is resuming the interrupted response.", "system", { role: "system" });
            setChatAnnouncementByThread(current => ({ ...current, [targetThreadId]: event.message }));
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
              attachTurnSources(body);
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
            resumeAvailable = event.resumeAvailable === true;
            const body = event.message?.body ?? "";
            if (body.trim()) appendAssistantMessage(body, "final", { interrupted: true });
            appendTurnSummary(event);
            setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
            throw new Error(event.error || "Rem failed");
          }
        }
        });
      }

      if (!finalReceived) {
        throw new Error("Rem stream ended without a final response");
      }
      if (deletedChatThreadIdsRef.current.has(targetThreadId)) return;
      turnOutcome = "done";
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
      turnOutcome = "failed";
      setLiveTurnByThread((current) => ({ ...current, [targetThreadId]: null }));
      if (!turnSummaryReceived) {
        appendTurnSummary({
          completedAt: new Date().toISOString(),
          durationMs: Date.now() - clientTurnStartedAt,
        });
      }
      appendAssistantMessage(error instanceof Error ? error.message : "Unable to send message", "error", { role: "system", resumeAvailable });
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
        if (!deletedChatThreadIdsRef.current.has(targetThreadId)) {
          setThreads(current => bumpChatThread(current, targetThreadId, undefined,
            { ...threadReadPatch(activeThreadIdRef.current !== targetThreadId), archived: false }));
          notifyThreadTurn({ threadId: targetThreadId, title: targetThreadTitle, outcome: turnOutcome, turn: activeTurn.turnId });
        }
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
    if (activeThread?.managedRunId) {
      setSteeringBusy(current => ({ ...current, [threadId]: true }));
      try {
        if (selectedAttachments.length) throw new Error("Include file paths in this managed task's instruction.");
        await sendManagedRemInstruction(threadId, text);
        changeThreadDraft(threadId, current => current === text ? "" : current);
        setSteeringErrors(current => ({ ...current, [threadId]: "" }));
      } catch (error) {
        setSteeringErrors(current => ({ ...current, [threadId]: error.message }));
      } finally {
        setSteeringBusy(current => ({ ...current, [threadId]: false }));
      }
      return;
    }
    const resources = snapshotRemResources(activeThread?.resources ?? homeResources);
    const resourceError = remResourceError(resources);
    if (resourceError) { setSteeringErrors(current => ({ ...current, [threadId]: resourceError })); return; }
    const attachmentKey = JSON.stringify(selectedAttachments.map(item => item.id));
    const previous = steeringRequestsRef.current[threadId];
    const request = previous?.text === text && previous.turnId === turn.turnId && previous.attachmentKey === attachmentKey && JSON.stringify(previous.resources) === JSON.stringify(resources)
      ? { ...previous }
      : { conversationId: threadId, turnId: turn.turnId, requestId: uniqueClientId(), text, attachmentKey, resources };
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
      setThreads(current => bumpChatThread(current, threadId));
      changeThreadDraft(threadId, current => current === text ? "" : current);
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
  messageActionsRef.current = { editUserMessage, toggleAssistantChanges, forkThread, activeThreadId };
  const forkMessageAction = useCallback(id => messageActionsRef.current.forkThread(id), []);
  const editMessageAction = useCallback((id, body, attachments) => messageActionsRef.current.editUserMessage(id, body, attachments), []);
  const undoMessageAction = useCallback((message) => {
    const actions = messageActionsRef.current;
    return actions.toggleAssistantChanges(actions.activeThreadId, message.id, message.changes?.id, Boolean(message.changes?.undone));
  }, []);

  function editUserMessage(messageId, body, attachments) {
    if (!activeThreadId || chatState.sending) return;
    void sendMessage({ id: messageId, body, attachments });
  }

  async function forkThread(messageId) {
    if (!activeThread || forkInProgressRef.current) return;
    forkInProgressRef.current = true;
    setForkStatus("Forking thread…");
    const parent = structuredClone(activeThread);
    // Include unloaded pages and the latest in-memory updates from a running turn.
    const snapshot = structuredClone(conversationCacheRef.current.get(parent.id));
    try {
      const saved = await repository.all(parent.id);
      const history = new Map(saved.map(message => [message.id, message]));
      for (const message of snapshot) history.set(message.id, message);
      const fork = forkThreadHistory(parent, [...history.values()], messageId, uniqueClientId());
      await copyForkAttachments(parent.id, fork.thread.id, fork.messages);
      await repository.save(fork.thread.id, fork.messages);
      persistChatThreads([fork.thread]);
      setThreads(current => [fork.thread, ...current]);
      conversationCacheRef.current.hydrate(fork.thread.id, fork.messages);
      if (activeThreadIdRef.current === parent.id) {
        openThread(fork.thread.id);
        setContextFocusRequest(current => current + 1);
      }
      const selectedMessage = fork.messages.at(-1);
      if (selectedMessage.role === "user") {
        setForkStatus("Thread forked. Sending your message to Rem.");
        void sendMessage(selectedMessage, { targetThread: fork.thread });
      } else {
        setForkStatus("Thread forked. Continue from here.");
      }
    } catch (error) {
      setForkStatus(`Could not fork thread: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      forkInProgressRef.current = false;
    }
  }

  function createThread(projectRoot = scopedProjectRoot, options = {}) {
    const now = new Date().toISOString();
    const root = String(projectRoot ?? "").trim();
    const thread = scopeChatThreadToProject(
      {
        id: options.id || uniqueClientId(),
        title: "New thread",
        provider: providerId, model, effort,
        resources: snapshotRemResources(homeResources),
        permissionsByProvider: { ...permissionsByProvider, [providerId]: permissionMode },
        ...(Object.keys(draftAccounts).length ? { accountsByProvider: { ...draftAccounts } } : {}),
        ...(draftMachine && !options.parent ? { machine: draftMachine } : {}),
        createdAt: now,
        updatedAt: now,
      },
      root,
      workflows,
      activeWorkflowId,
      pathValue(projectLabels, root)?.trim() || (root ? projectNameFromPath(root) : "No project"),
    );
    if (options.global || (!root && globalScope && !options.parent)) {
      thread.scopeMode = "global";
      thread.projectName = "Global";
    }
    if (options.parent) {
      Object.assign(thread, { provider: options.parent.provider, model: options.parent.model, effort: options.parent.effort,
        resources: snapshotRemResources(options.parent.resources), permissionsByProvider: { ...options.parent.permissionsByProvider }, parentThreadId: options.parent.id });
      if (options.parent.accountsByProvider) thread.accountsByProvider = { ...options.parent.accountsByProvider };
      else delete thread.accountsByProvider;
    }
    setThreads(current => [thread, ...current.filter(item => item.id !== thread.id)]);
    if (!options.background) {
      activeThreadIdRef.current = thread.id;
      setActiveThreadId(thread.id);
      loadThreadDraft(thread.id);
    }
    setScopeMenuOpen(false);
    return thread;
  }

  // Context events and deferred sends use the current thread settings and draft.
  const remContextActionsRef = useRef(null);
  const spawnedThreadsRef = useRef(new Set());
  function spawnThread(event, parent) {
    if (spawnedThreadsRef.current.has(event.threadId) || loadChatThread(event.threadId)) return;
    spawnedThreadsRef.current.add(event.threadId);
    const thread = createThread(event.projectRoot, { id: event.threadId, background: true, parent });
    void sendMessage(null, { targetThread: thread, text: event.message });
  }
  remContextActionsRef.current = { createThread, sendMessage, spawnThread, openThread };
  // Thread links in Rem's conversation open the linked worker thread here.
  useEffect(() => {
    const receive = event => { if (event.detail?.threadId) remContextActionsRef.current.openThread(event.detail.threadId); };
    window.addEventListener("gofer:rem-open-thread", receive);
    return () => window.removeEventListener("gofer:rem-open-thread", receive);
  }, []);

  useEffect(() => {
    let active = true;
    const receive = async (event) => {
      const context = event.detail || {};
      let root = context.projectRoot;
      if (!root && context.path && !context.webPage) {
        const candidates = [...recentProjectRoots, ...workflows.map(item => item.projectRoot)].filter(item => typeof item === "string" && pathWithin(context.path, item));
        root = candidates.sort((a, b) => b.length - a.length)[0];
        if (!root) {
          try { root = (await window.goferDesktop?.workspace?.gitStatus?.(context.path.replace(/[/\\][^/\\]+$/, "")))?.root; } catch { /* Non-Git files still carry their exact path. */ }
        }
      }
      // Scope the thread to the brains it was opened from; the rest start off.
      let brains = [];
      if (context.secondBrainIds?.length) {
        const { brainRequest } = await import("../lib/secondBrainApi.js");
        brains = await brainRequest().then(library => library.brains, () => []);
      }
      if (!active) return;
      const thread = remContextActionsRef.current.createThread(root || "");
      if (context.secondBrainIds?.length) {
        thread.resources = { ...thread.resources, secondBrains: undefined, disabledSecondBrains: brains.map(brain => brain.id).filter(id => !context.secondBrainIds.includes(id)) };
        setThreads(current => current.map(item => item.id === thread.id ? thread : item));
      }
      // A shared tab needs the Browser tool, and is bound to this new thread.
      if (context.browserSessionId) {
        thread.resources = { ...thread.resources, browser: true };
        setThreads(current => current.map(item => item.id === thread.id ? thread : item));
        try { await window.goferBrowser?.shareWithRem?.(context.browserSessionId, thread.id, "Rem"); }
        catch (error) { setPendingRemContext({ threadId: thread.id, attachments: [], error: `Could not share the tab: ${error.message}`, draft: context.draft || "", send: false }); return; }
      }
      // The tab in front of Raticode (gecko)'s window, found as it's shared.
      if (context.geckoShare) {
        thread.resources = { ...thread.resources, browser: true };
        setThreads(current => current.map(item => item.id === thread.id ? thread : item));
        try {
          const sharedTab = await window.goferBrowser?.gecko?.("share-with-rem", { threadId: thread.id, label: "Rem" });
          context.path = sharedTab?.url || "";
          context.title = sharedTab?.title || "";
          context.draft = `I shared the Raticode (gecko) tab “${context.title || context.path}” with you. Use it to `;
        } catch (error) {
          setPendingRemContext({ threadId: thread.id, attachments: [], error: `Could not share the Gecko tab: ${ipcErrorMessage(error)}`, draft: "", send: false });
          return;
        }
      }
      const body = context.webPage
        ? askRemWebPageAttachment({ url: context.path, title: context.title || "", text: context.text || "", shared: Boolean(context.browserSessionId || context.geckoShare) })
        : context.mode === "conflicts"
        ? `Resolve the Git conflicts in this project and finish the active rebase or merge. This request authorizes resolving files, staging the fixes, committing them with meaningful commit messages, and continuing the operation without asking for confirmation at each step.\n\nInspect the live Git status and operation in this project before editing; the file list below is only an initial snapshot. Inspect both sides of each conflict and preserve the intended behavior. Resolve all current conflicts, check the fixes, and stage each resolved path explicitly, including resolved deletions. Preserve unrelated local changes and do not stage them.\n\nDuring a rebase, use git rebase --continue to create the resolved commit, preserving a meaningful original commit message or improving it when needed. Configure the editor noninteractively so continuation cannot hang waiting for input. After every continuation, inspect Git status and the operation again. If new conflicts appear, resolve, check, stage, and continue again. Repeat until the rebase is complete; do not stop with fixes unstaged or the rebase paused. For a merge, stage the resolutions and complete the merge with a meaningful commit message. If no merge or rebase is active, commit the conflict fixes with a meaningful message.\n\nDo not abort the operation, discard unrelated work, skip commits, or push without separate authorization. If a command fails for a reason other than another conflict, investigate and fix it when possible; otherwise report the exact blocker and remaining Git state rather than claiming completion. Verify that no unmerged paths remain and that the rebase or merge has ended before reporting success.\n\nAt the end, state whether the operation completed and give a full report as a numbered list of one-line summaries, one summary per conflict fix. Each summary must identify the file, what Rem changed, and why; distinguish repeated fixes to the same file in different replayed commits. Include verification results and any remaining blockers.\n\nConflicted files:\n${context.text}`
        : `File: ${context.path || "workflow.rattish"}\nProject: ${root || "No project"}\nLines: ${context.startLine || 1}-${context.endLine || context.startLine || 1}\n${context.version || "Editor selection"}\n\n${context.text || ""}`;
      const file = new File([body], context.webPage ? "web-page.txt" : context.mode === "conflicts" ? "merge-conflicts.txt" : "editor-selection.txt", { type: "text/plain" });
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
      request.handled = true;
      if (request.background) {
        startGenerationJob("commit", { projectRoot: request.projectRoot, branch: request.branch }, { provider: providerId, model, effort, permissionMode }).then(request.resolve, request.reject);
        return;
      }
      generateConventionalCommit({ provider: providerId, model, effort, permissionMode, diff: request.diff, projectRoot: request.projectRoot, inspectStaged: request.inspectStaged, signal: request.signal }).then(request.resolve, request.reject);
    };
    window.addEventListener("gofer:rem-commit-message", receive);
    return () => window.removeEventListener("gofer:rem-commit-message", receive);
  }, [providerId, model, effort, permissionMode]);

  useEffect(() => {
    const receive = event => {
      const request = event.detail;
      if (!request || request.signal?.aborted) return;
      request.handled = true;
      const selection = { provider: providerId, model, effort, permissionMode };
      (request.background ? startGenerationJob("theme", request, selection) : generateReportTheme(request, selection)).then(request.resolve, request.reject);
    };
    window.addEventListener("gofer:rem-report-theme", receive);
    return () => window.removeEventListener("gofer:rem-report-theme", receive);
  }, [providerId, model, effort, permissionMode]);

  // Apply context after the thread activation effect clears the previous draft.
  useEffect(() => {
    if (!pendingRemContext || activeThreadId !== pendingRemContext.threadId) return;
    setAttachments(pendingRemContext.attachments); setAttachmentError(pendingRemContext.error);
    changeThreadDraft(pendingRemContext.threadId, pendingRemContext.draft);
    setContextSendThread(pendingRemContext.send ? pendingRemContext.threadId : null);
    setContextFocusRequest(current => current + 1);
    setPendingRemContext(null);
  }, [pendingRemContext, activeThreadId, changeThreadDraft]);

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
    if (thread && !thread.projectRoot && thread.scopeMode !== "global") {
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
      setThreads(current => current.map(candidate => candidate.id === threadId ? { ...candidate, ...threadReadPatch(false) } : candidate));
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
          expanded={isThoughtGroupExpanded(item, expandedThoughtGroups, assistantDefaults.autoHideThoughts !== false && !item.thoughts.some(thought => String(thought.id) === String(matchId)))}
          replyId={item.replyId}
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
        <ForkableMessage
          onFork={forkMessageAction}
          canFork={isForkableMessage(history.find(message => message.id === item.message.id))}
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
              title: !thread.titleCustomized && thread.title === "New thread" ? threadTitleFromMessage(message) : thread.title,
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
    applyThreadsAction("archive", [threadId]);
  }

  function pinThread(threadId, pinned) {
    applyThreadsAction(pinned ? "pin" : "unpin", [threadId]);
  }

  // One change for every selected thread. Reading or archiving also clears the
  // window's own finished-reply marker, which would otherwise keep it unread.
  async function applyThreadsAction(action, ids) {
    if (!ids.length) return;
    if (action === "delete") {
      if (!await confirmAction({
        message: ids.length === 1
          ? `Delete thread "${(threads.find(item => item.id === ids[0]) || loadChatThread(ids[0]))?.title || "Untitled"}"? This cannot be undone.`
          : `Delete ${ids.length} threads? This cannot be undone.`,
        confirmLabel: ids.length === 1 ? "Delete thread" : `Delete ${ids.length} threads`,
        danger: true,
      })) return;
      for (const id of ids) if (!await removeThread(id)) break;
      return;
    }
    const patch = threadActionPatch(action);
    if (!patch) return;
    const chosen = new Set(ids);
    setThreads(current => {
      const known = new Set(current.map(item => item.id));
      const stored = ids.filter(id => !known.has(id)).map(loadChatThread).filter(Boolean);
      return [...current, ...stored].map(item => chosen.has(item.id) ? { ...item, ...patch } : item);
    });
    if (patch.unread === false) {
      setChatStateByThread(current => ids.some(id => current[id]?.hasNewResponse)
        ? Object.fromEntries(Object.entries(current).map(([id, state]) => [id, chosen.has(id) ? { ...state, hasNewResponse: false } : state]))
        : current);
    }
  }

  function renameThread(threadId, title) {
    const name = title.trim();
    if (!name) return;
    updateThreadOrganization(threadId, { title: name, titleCustomized: true });
    setRenamingThread(null);
  }

  async function deleteThread(threadId) {
    const thread = threads.find(item => item.id === threadId) || loadChatThread(threadId);
    if (!await confirmAction({
      message: `Delete thread "${thread?.title || "Untitled"}"? This cannot be undone.`,
      confirmLabel: "Delete thread",
      danger: true,
    })) return;
    await removeThread(threadId);
  }

  // Deletes the thread and its conversation archive copy, unlike Delete, which keeps it.
  async function deleteThreadPermanently(threadId) {
    const thread = threads.find(item => item.id === threadId) || loadChatThread(threadId);
    if (!await confirmAction({ message: permanentDeleteMessage(thread?.title), confirmLabel: "Delete permanently", danger: true })) return;
    await removeThread(threadId, { purge: true });
  }

  // Thread state is read through updaters so several deletions can run in sequence.
  async function removeThread(threadId, { purge = false } = {}) {
    deletedChatThreadIdsRef.current.add(threadId);
    await stopAssistant(threadId);
    const archived = purge ? await purgeThreadFromArchive(threadId) : await archiveThreadFromStorage(threadId, () => repository.all(threadId), true);
    if (!archived) {
      deletedChatThreadIdsRef.current.delete(threadId);
      setChatStateByThread((current) => ({ ...current, [threadId]: { sending: false, error: purge
        ? "The archive copy could not be removed, so the thread was kept. Reconnect the archive folder and try Delete permanently again."
        : "The archive could not be saved. Reconnect the archive folder, or stop archiving in Settings > Memory before deleting this thread." } }));
      return false;
    }
    try { await repository.remove(threadId); } catch (error) {
      deletedChatThreadIdsRef.current.delete(threadId);
      reportStorageFailure(threadId, error);
      return false;
    }
    deleteStoredChatThread(threadId);
    deleteChatDraft(threadDraftKey(threadId));
    setThreads(current => current.filter((thread) => thread.id !== threadId));
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
    if (activeThreadIdRef.current === threadId) {
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
    return true;
  }

  // Rem's task threads belong to Rem's home, which clears them with their tasks.
  async function clearArchivedThreads(entries, { confirm = true } = {}) {
    const ids = entries.filter(entry => !entry.managedRole).map(entry => entry.id);
    if (!ids.length || archiveClearingRef.current) return;
    if (confirm && !await confirmAction({
      message: `Delete ${ids.length} archived ${ids.length === 1 ? "thread" : "threads"}? This cannot be undone.`,
      confirmLabel: "Clear archive",
      danger: true,
    })) return;
    archiveClearingRef.current = true;
    try {
      for (const id of ids) if (!await removeThread(id)) break;
    } finally { archiveClearingRef.current = false; }
  }
  clearArchivedThreadsRef.current = clearArchivedThreads;

  useEffect(() => {
    if (!archiveRetentionDays(archiveRetention)) return undefined;
    const dispose = () => {
      const running = new Set([...Object.keys(chatAbortControllersRef.current), ...deviceRunningThreadsRef.current]);
      const entries = chatThreadIndex().filter(entry => entry.managedRole !== "verify");
      const { archived } = organizeThreads(entries, { running, ...cachedThreadScopes(window.goferDesktop?.workspace) });
      void clearArchivedThreadsRef.current(expiredArchivedThreads(archived, archiveRetention), { confirm: false });
    };
    const initial = window.setTimeout(dispose, 5000);
    const timer = window.setInterval(dispose, 60 * 60 * 1000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [archiveRetention]);

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
          {onShowRem ? (
            <button
              aria-label="Back to Rem"
              className="studio-icon-button -ml-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
              title="Back to Rem"
              type="button"
              onClick={onShowRem}
            >
              <RatFaceIcon aria-hidden="true" size={17} />
            </button>
          ) : null}
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
              ref={scopeTriggerRef}
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
              <PopupMenu
                aria-label="Rem project scope"
                className="absolute left-0 top-9 z-50 max-h-72 w-72 overflow-y-auto rounded-[14px] border border-line bg-white p-1.5 shadow-panel"
                focusKey={scopeLoading}
                onClose={(options) => {
                  setScopeMenuOpen(false);
                  if (options?.restoreFocus) scopeTriggerRef.current?.focus();
                }}
              >
                <p className="px-2 py-1 text-[10px] font-semibold text-muted">Projects and worktrees</p>
                {!activeThreadId && defaultGlobalScope ? <button type="button" role="menuitem" className="w-full px-2 py-1 text-left text-xs" onClick={() => { setHomeProjectRoot(""); setScopeMenuOpen(false); }}>Global</button> : null}
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
              </PopupMenu>
            ) : null}
          </div>
        </div>
        <div ref={conversationMenuRef} className="relative flex items-center gap-1">
          {activeThreadId ? <div ref={threadInfoRef} className="relative">
            <button
              aria-expanded={threadInfoOpen}
              aria-haspopup="dialog"
              aria-label="Thread info"
              className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
              title="Thread info"
              type="button"
              onClick={() => { setConversationMenuOpen(false); setScopeMenuOpen(false); setThreadInfoOpen(current => !current); }}
            >
              <Info aria-hidden="true" size={16} />
            </button>
            {threadInfoOpen ? <div role="dialog" aria-label="Thread info" className="absolute right-0 top-9 z-50 max-h-[70vh] w-80 overflow-y-auto rounded-[14px] border border-line bg-white p-3 shadow-panel">
              <p className="truncate text-xs font-semibold text-ink">{activeThread?.title || "This thread"}</p>
              <p className="mb-3 truncate text-[11px] text-muted" title={scopedProjectRoot}>Scoped to {scopedProjectName}</p>
              <ThreadContextSent threadId={activeThreadId} refreshKey={String(chatState.sending)} />
            </div> : null}
          </div> : null}
          <button
            aria-label="New thread"
            className="studio-icon-button grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-slate-100 hover:text-ink"
            title="New thread"
            type="button"
            onClick={() => createThread(defaultGlobalScope ? "" : prospectiveProjectRoot, { global: defaultGlobalScope })}
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
                launchedThreadId={launchedThreadId}
                onLaunchAnimationEnd={() => setLaunchedThreadId(null)}
                activityByThread={chatStateByThread}
                threads={threads}
                activeThreadId={activeThreadId}
                onArchive={archiveThread}
                onPin={pinThread}
                onRename={setRenamingThread}
                onDelete={deleteThread}
                onDeletePermanently={memorySettings?.archiveFolder ? deleteThreadPermanently : undefined}
                onThreadsAction={applyThreadsAction}
                onClearArchive={clearArchivedThreads}
                archiveRetention={archiveRetention}
                onOpen={openThread}
              />
            </div>
          ) : null}
        </div>
      </div>

      {forkStatus ? <p role="status" className="border-b border-line px-3.5 py-2 text-xs text-muted">{forkStatus}</p> : null}
      {renamingThread ? <ThreadRenameDialog
        thread={renamingThread}
        onClose={() => setRenamingThread(null)}
        onRename={renameThread}
      /> : null}
      {threadSearchOpen ? <ThreadSearch repository={repository} loadThreads={loadAllChatThreads} onOpen={openThread} onClose={closeThreadSearch} /> : null}

      {activeSearchTarget ? <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3.5 py-2 text-xs text-muted">
        <span className="min-w-0 truncate" role="status">Match for &quot;{activeSearchTarget.query}&quot;</span>
        <button type="button" className="shrink-0 rounded px-2 py-1 text-brand focus-visible:outline-brand" onClick={returnToLatestMessages}>Latest messages</button>
      </div> : null}

      <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={chatScrollRef}
        data-chat-scroll="true"
        style={{ overflowAnchor: "none" }}
        className="workflow-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto px-3.5 py-4"
        onScroll={(event) => {
          if (activeSearchTarget) return;
          const atBottom = conversationIsAtBottom(event.currentTarget);
          const jump = jumpToLatestRef.current;
          // On the way down a jump stays pinned; scrolling back up ends it.
          if (jump && !atBottom && event.currentTarget.scrollTop >= jump.top) jump.top = event.currentTarget.scrollTop;
          else {
            jumpToLatestRef.current = null;
            chatPinnedToBottomRef.current = atBottom;
          }
          if (atBottom && activeThread) markConversationSeen();
          if (event.currentTarget.scrollTop < 80 && historyState.threadId === activeThreadId && historyState.hasMore && !historyState.error) void loadHistoryPage(activeThreadId, historyState.before);
        }}
      >
        {!activeThread ? (
          <div className="min-h-full" data-assistant-home>
            <section aria-label="Threads">
              <ThreadSections
                launchedThreadId={launchedThreadId}
                onLaunchAnimationEnd={() => setLaunchedThreadId(null)}
                activityByThread={chatStateByThread}
                threads={threads}
                activeThreadId={activeThreadId}
                onArchive={archiveThread}
                onPin={pinThread}
                onRename={setRenamingThread}
                onDelete={deleteThread}
                onDeletePermanently={memorySettings?.archiveFolder ? deleteThreadPermanently : undefined}
                onThreadsAction={applyThreadsAction}
                onClearArchive={clearArchivedThreads}
                archiveRetention={archiveRetention}
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
                  expanded={isThoughtGroupExpanded(item, expandedThoughtGroups, assistantDefaults.autoHideThoughts !== false)}
                  replyId={item.replyId}
                  onOpenLink={openScopedMarkdownLink}
                  sourcePath={assistantMarkdownSourcePath(scopedProjectRoot)}
                  onOpenFile={openScopedFile}
                  thoughts={item.thoughts}
                  lazy={item.lazy}
                  loaded={item.lazy && lazyThoughts[item.lazy.id]}
                  groupId={item.id}
                  onToggle={toggleThoughtGroup}
                />
              ) : (
                <ForkableMessage
                  onFork={forkMessageAction}
                  key={item.message.id}
                  canEdit={!chatState.sending && item.message.id === latestUserMessageId}
                  message={item.message}
                  onEdit={editMessageAction}
                  onResume={!chatState.sending && item.message.id === visibleMessages.at(-1)?.id
                    ? () => void sendMessage(null, { text: "Continue the original task. Inspect completed work first and avoid repeating completed actions." }) : undefined}
                  supportsImages={supportsImages}
                  providerName={providerCapability?.displayName || providerId}
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
      {activeThread && !activeSearchTarget ? <NewMessagesPill
        count={unseenMessages}
        onClick={jumpToLatestMessages}
        className="absolute bottom-3 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-raised px-3 py-1.5 text-xs font-semibold text-brand shadow-md focus-visible:outline-brand"
      /> : null}
      </div>

      {/* Before the composer: index.css styles the chat's last child as the composer. */}
      {confirmDialog}
      <div className="relative shrink-0 border-t border-line p-3">
          <RemToolsMenu
            key={activeThreadId || "new-thread"}
            open={resourcesOpen}
            onOpenChange={setResourcesOpen}
            thread={Boolean(activeThread)}
            sending={chatState.sending}
            value={activeThread ? activeThread.resources ?? assistantDefaults.resources ?? DEFAULT_REM_RESOURCES : homeResources}
            memory={memorySettings}
            onChange={(resources) => {
              if (activeThread) updateThreadConfig({ resources });
              // New-thread tools are the saved Rem defaults, so they survive restarts.
              else if (onNewThreadResourcesChange) onNewThreadResourcesChange(resources);
              else setNewThreadResources(resources);
            }}
          />
          <ProviderModelEffortFields
            account={threadAccountMenu ? {
              menu: threadAccountMenu,
              warning: accountSignInError || accountWarning,
              onChange: (choice) => {
                setAccountSignInError("");
                if (activeThread) updateThreadConfig({ accountsByProvider: withThreadAccount(activeThread.accountsByProvider, providerId, choice) });
                else setDraftAccounts(current => withThreadAccount(current, providerId, choice));
              },
              onSignIn: (name) => {
                setAccountSignInError("");
                signInProviderAccount(name).catch(failure => setAccountSignInError(failure.message || "Could not start the sign-in."));
              },
            } : null}
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
            shortcutHint={!activeThreadId ? "Enter to start thread Shift+Enter for a new line Ctrl+Enter to start thread in background" : undefined}
            onSteer={steerAssistant}
            steeringPending={Boolean(steeringBusy[activeThreadId])}
            provider={providerId}
            permissionMode={permissionMode}
            permissionOptions={permissionOptions}
            onPermissionModeChange={selectPermission}
            machines={machines}
            machine={activeThread ? activeThread.machine || "" : draftMachine}
            machineLocked={Boolean(activeThread && messages.length)}
            onMachineChange={value => activeThread ? updateThreadConfig({ machine: value || undefined }) : setDraftMachine(value)}
            attachments={attachments}
            attachmentError={attachmentError}
            attachmentWarning={attachmentWarning?.text || ""}
            audioInputDeviceId={audioInputDeviceId}
            contextKey={activeThreadId ?? "new-thread"}
            draft={draft}
            focusRequest={composerFocusRequest + contextFocusRequest}
            sending={chatState.sending}
            sendDisabled={requiresPermissionChoice || (historyState.threadId === activeThreadId && Boolean(historyState.loading || historyState.error))}
            onAddAttachments={addAttachments}
            onAttachmentErrorChange={setAttachmentError}
            onAttachmentsChange={setAttachments}
            onDraftChange={value => changeThreadDraft(activeThreadId, value)}
            onSend={sendMessage}
            onStop={() => activeThreadId && stopAssistant(activeThreadId)}
          />
      </div>
    </aside>
  );
}

// The thread list lives in its own chunk; App supplies its history helpers.
export function ThreadSections(props) {
  return <ThreadSectionsView threadIndex={chatThreadIndex} loadStoredThread={loadChatThread} dateLabel={formatThreadDate} {...props} />;
}

export function ThreadList(props) {
  return <ThreadListView dateLabel={formatThreadDate} {...props} />;
}

function ThreadRenameDialog({ thread, onClose, onRename }) {
  const [name, setName] = useState(thread.title);
  return <Dialog
    title="Rename Thread"
    onClose={onClose}
    overlayClassName="fixed inset-0 z-[95] grid place-items-center ui-scrim px-4"
    panelClassName="w-full max-w-sm rounded-lg border border-line bg-white p-4 shadow-panel"
  >
    <form onSubmit={event => { event.preventDefault(); if (name.trim()) onRename(thread.id, name); }}>
      <h3 className="mb-3 text-sm font-semibold text-strong">Rename Thread</h3>
      <label className="mb-1 block text-xs text-muted" htmlFor="rem-thread-name">Thread name</label>
      <input
        id="rem-thread-name"
        aria-label="Thread name"
        data-dialog-initial-focus
        maxLength={160}
        className="h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand"
        value={name}
        onChange={event => setName(event.target.value)}
        onFocus={event => event.target.select?.()}
      />
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="h-9 rounded-lg border border-line px-3 text-sm hover:bg-slate-50" onClick={onClose}>Cancel</button>
        <button type="submit" disabled={!name.trim()} className="h-9 rounded-lg bg-brand px-3 text-sm font-medium text-white disabled:opacity-60">Save</button>
      </div>
    </form>
  </Dialog>;
}


function conversationItemKey(item) {
  return item.type === "message" ? item.message?.id : item.id;
}

function conversationItemKeys(items) {
  return new Set(items.map(conversationItemKey));
}

// How many messages, including a live turn's, follow the newest row in seen. Thought groups
// don't count. Null when none of the rows were seen, which leaves the count as it was.
export function unseenConversationMessages(items, seen) {
  const last = items.findLastIndex(item => seen.has(conversationItemKey(item)));
  if (last < 0) return null;
  return items.slice(last + 1).filter(item => item.type === "message").length;
}

function conversationIsAtBottom(element, tolerance = 4) {
  if (!element) return false;
  return element.scrollHeight - element.scrollTop - element.clientHeight <= tolerance;
}

function scrollConversationToBottom(element) {
  if (!element) return;
  element.scrollTop = element.scrollHeight;
}
