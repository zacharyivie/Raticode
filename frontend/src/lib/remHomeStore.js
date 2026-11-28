import { useSyncExternalStore } from "react";
import { apiJson } from "./api.js";
import { uploadChatAttachments } from "./chatAttachments.js";
import { encodeScreen } from "./screenContext.js";
import { startPolling } from "./refresh.js";
import { conversationRepository } from "./conversationRepository.js";
import { mergeInServerOrder } from "./conversationCache.js";
import { mergeTaskThreadMetadata, threadIndexEntry } from "./threadActivity.js";
import { NO_OBJECTIVES, OBJECTIVES_PAGE, applyObjectiveChange, previewObjectiveChange, sameObjectives, withProjectPage } from "./remObjectives.js";
import { CONFLICT_MESSAGE, GONE_MESSAGE, NO_DECISIONS, applyDecisionChange, sameDecisions, scanSummary, settleEditLocally } from "./remDecisions.js";
import { backgroundNoticesAllowed, newRemReplies, showBackgroundNotice } from "./backgroundNotices.js";

// Shared state for the Rem sidebar and conversation. The backend control service
// (GET/POST /api/rem/...) is authoritative: the renderer submits commands and
// renders what the server reports. It never launches work or decides completion.

const NO_TICKETS = Object.freeze({});

// The ticket index from a poll, or the one already held when nothing in it changed, so
// chat rows that show pills re-render only when a ticket they name changes.
function sameTickets(next, current) {
  if (!next || !Object.keys(next).length) return NO_TICKETS;
  return JSON.stringify(next) === JSON.stringify(current) ? current : next;
}

function initialState() {
  // chatTickets and taskTickets: board tickets named in chat and on task cards, by key,
  // as the board has them now. Only these keys show as ticket pills.
  // messages: what the chat shows, oldest first: `older` (pages read with Show earlier
  // messages), then the server's newest window, then unsent messages. `earlier` counts
  // the stored messages before the first one shown; olderTickets are the pages' tickets.
  return { status: "loading", error: "", contextIssue: "", conversation: null, messages: [], older: [], earlier: 0, loadingEarlier: false, tasks: [], objectives: NO_OBJECTIVES, decisions: NO_DECISIONS, decisionsLoaded: false, chatTickets: NO_TICKETS, taskTickets: NO_TICKETS, olderTickets: NO_TICKETS, fixture: false, resetting: false, notice: "", sideNotice: "", sheet: null, composerRequest: null, messageRequest: null };
}

// Files attached in Rem's chat upload to this chat attachment thread.
const REM_ATTACHMENT_THREAD = "rem-home";
const REPLY_WAIT_MS = 10 * 60 * 1000;
const REPLY_POLL_MS = 1500;
let sendQueue = Promise.resolve();
let state = initialState();
let stopPolling = null;
let connections = 0;
let sequence = 0;
let contextFactory = null;
let stopContextPolling = null;
let configuredContext = "";
let historyGeneration = 0;
let contextPending = Promise.resolve();
const mirroredThreads = new Map();
const threadSyncPending = new Map();
const finishedThreadVersions = new Map();
const threadSyncFailures = new Map();
// Content versions of what this window last applied. The backend answers
// "unchanged" for a matching version, so a poll while nothing happens downloads,
// parses and re-renders nothing. Older backends send no version.
let versions = { conversation: "", tasks: "", decisions: "" };
// The newest messages the server last sent (GET /api/rem/conversation sends only these).
// When the window moves on while earlier pages are loaded, the messages it let go of
// join `older`, so no gap opens between the pages and the window.
let serverWindow = [];
const threadVersions = new Map();
// Task threads last seen running, so a finish between polls still counts as completion.
const runningThreads = new Set();
const clearedThreads = new Set();
let pendingClear = null;
export const remThreadWasCleared = id => clearedThreads.has(id);
const listeners = new Set();
const requestId = prefix => `${prefix}-${Date.now().toString(36)}-${(++sequence).toString(36)}`;

function emit() { for (const listener of listeners) listener(); }
function set(next) { state = typeof next === "function" ? next(state) : { ...state, ...next }; emit(); }
export function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export const getState = () => state;
export function useRemHome() { return useSyncExternalStore(subscribe, getState, getState); }

class RemServiceError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

// `known` is the version this window already has. It goes in a header, not the
// URL, so a poll's address never changes and the browser can reuse its
// cross-origin permission check instead of asking the backend again each time.
export async function request(path, { method = "GET", body, known = "" } = {}) {
  try {
    return await apiJson(`/rem${path}`, {
      method, body, headers: known ? { "X-Gofer-Known": known } : undefined,
      detailFirst: true, failure: status => `Rem request failed (${status}).`,
    });
  } catch (failure) {
    // No status means nothing answered.
    if (typeof failure?.status !== "number") throw new RemServiceError("Raticode's backend isn't reachable.", 0);
    const error = new RemServiceError(failure.message, failure.status);
    // Switch over names the files it couldn't show in full when it refuses, as triggers do.
    if (failure.unseen) error.unseen = failure.unseen;
    // Decisions name why they refused (conflict, not_found, invalid), apart from a missing route.
    if (failure.code) error.code = failure.code;
    throw error;
  }
}
// The service is absent (older backend) or unreachable; nothing can run.
const unavailable = error => error?.status === 0 || error?.status === 404 || error?.status === 501;

// Folders Rem opened or created for a request. The window lists them as its own
// projects, which renews their access and stops the backend reporting them.
let openedProjectsListener = null;
export function onRemOpenedProjects(listener) {
  openedProjectsListener = listener;
  return () => { if (openedProjectsListener === listener) openedProjectsListener = null; };
}
const trimRoot = root => (typeof root === "string" ? root.trim().replace(/(?<=.)[\\/]+$/, "") : "");
// [current] recent project roots with Rem's opened projects in front; the same array when
// every one is already listed, so a repeated report changes nothing.
export function adoptRemProjects(current = [], opened = []) {
  const listed = new Set(current.map(trimRoot));
  const missing = [...new Set((Array.isArray(opened) ? opened : []).map(project => trimRoot(project?.root)))]
    .filter(root => root.startsWith("/") || /^[A-Za-z]:[\\/]/.test(root))
    .filter(root => !listed.has(root));
  return missing.length ? [...missing, ...current] : current;
}

export function setRemHomeContext(factory) {
  stopContextPolling?.();
  stopContextPolling = null;
  contextFactory = factory;
  contextPending = Promise.resolve();
  configuredContext = "";
  if (factory) stopContextPolling = startPolling(syncContext, { interval: 60000, immediate: true, documentTarget: null });
}

async function syncContext() {
  if (!contextFactory || state.fixture || state.resetting || pendingClear) return;
  const factory = contextFactory;
  const generation = historyGeneration;
  const job = contextPending.catch(() => {}).then(async () => {
    if (factory !== contextFactory || generation !== historyGeneration || state.resetting) return;
    const context = await factory();
    if (factory !== contextFactory || generation !== historyGeneration || state.resetting) return;
    const fingerprint = JSON.stringify(context);
    if (fingerprint === configuredContext) return;
    const result = await request("/context", { method: "POST", body: context });
    if (factory !== contextFactory || generation !== historyGeneration) return;
    configuredContext = fingerprint;
    setContextIssue((result.warnings || []).join(" "));
  }).catch(error => {
    // Rem keeps working with the context it last received. The reason is shown
    // instead of blocking the conversation and every task command.
    if (factory === contextFactory && generation === historyGeneration && !unavailable(error)) setContextIssue(`Rem couldn't load your projects and settings. ${error.message}`);
  });
  contextPending = job;
  return job;
}
function setContextIssue(contextIssue) { if (state.contextIssue !== contextIssue) set({ contextIssue }); }

// `poll` marks the routine refresh: an unchanged thread then costs nothing and
// isn't announced again. Opening a thread always delivers its snapshot.
export function syncRemThread(threadId, { poll = false } = {}) {
  // Serialize reads and writes so a slow active snapshot cannot overwrite completion.
  const previous = threadSyncPending.get(threadId) || Promise.resolve();
  const result = previous.catch(() => {}).then(() => syncThreadSnapshot(threadId, poll));
  threadSyncPending.set(threadId, result);
  void result.finally(() => {
    if (threadSyncPending.get(threadId) === result) threadSyncPending.delete(threadId);
  }).catch(() => {});
  return result;
}

async function syncThreadSnapshot(threadId, poll) {
  const generation = historyGeneration;
  if (state.resetting || clearedThreads.has(threadId)) return null;
  const { version = "", ...remote } = await request(`/threads/${encodeURIComponent(threadId)}`, { known: poll ? threadVersions.get(threadId) : "" });
  if (generation !== historyGeneration || state.resetting) return null;
  if (remote.unchanged) return null;
  if (!remote.metadata) return null;
  const fingerprint = JSON.stringify(remote);
  const storage = window.localStorage;
  const metaKey = `gofer-flow-chat-thread-meta:${threadId}`;
  const saved = () => { try { return JSON.parse(storage.getItem(metaKey) || "null"); } catch { return null; } };
  if (mirroredThreads.get(threadId) === fingerprint) {
    if (version) threadVersions.set(threadId, version);
    // Everything here already has this snapshot; only a newly opened pane needs it.
    if (poll) return null;
    // A newly mounted thread pane still needs the transcript and terminal state.
    const detail = { ...remote, metadata: mergeTaskThreadMetadata(saved(), remote.metadata) };
    window.dispatchEvent(new CustomEvent("gofer:device-thread-sync", { detail }));
    return detail;
  }
  const repository = conversationRepository();
  const current = await repository.all(threadId);
  if (generation !== historyGeneration || state.resetting) return null;
  // Messages this desktop hasn't seen are new activity; a final reply among
  // them while nothing runs is a completion the user hasn't seen yet.
  const known = new Set(current.map(message => String(message.id)));
  const added = (remote.messages || []).filter(message => !known.has(String(message.id)));
  const completed = remote.running === false && (runningThreads.has(threadId)
    || added.some(message => ["final", "error", "stopped"].includes(message.kind)));
  // Reuse unchanged local copies so only new or edited messages are written.
  const local = new Map(current.map(message => [String(message.id), message]));
  const server = (remote.messages || []).map(message => {
    const saved = local.get(String(message.id));
    return saved && JSON.stringify(saved) === JSON.stringify(message) ? saved : message;
  });
  const messages = mergeInServerOrder(current, server);
  // Messages saved out of order, like an early one that arrived late, are
  // stored again in the server's order.
  const resequence = current.some((message, index) => String(messages[index].id) !== String(message.id));
  await repository.save(threadId, messages, current, current, { resequence });
  if (generation !== historyGeneration || state.resetting) return null;
  // Read the desktop's copy after the awaits so a thread opened meanwhile stays read.
  const metadata = mergeTaskThreadMetadata(saved(), remote.metadata, { activity: added.length > 0, completed });
  storage.setItem(metaKey, JSON.stringify(metadata));
  const index = JSON.parse(storage.getItem("gofer-flow-chat-threads") || "[]");
  const entry = threadIndexEntry(metadata);
  storage.setItem("gofer-flow-chat-threads", JSON.stringify([entry, ...index.filter(item => item.id !== threadId)]));
  mirroredThreads.set(threadId, fingerprint);
  if (version) threadVersions.set(threadId, version);
  else threadVersions.delete(threadId);
  if (remote.running) runningThreads.add(threadId);
  else runningThreads.delete(threadId);
  const detail = { ...remote, metadata, completed };
  window.dispatchEvent(new CustomEvent("gofer:device-thread-sync", { detail }));
  return detail;
}

export async function refreshRemHome() {
  if (state.fixture || state.resetting || pendingClear) return;
  const generation = historyGeneration;
  try {
    // Context is sent when it changes, once a minute, and before each command,
    // not on every poll.
    if (!configuredContext) await syncContext();
    // Decisions are read on their own: an older backend without them (or a failed read)
    // leaves the chat and tasks working.
    const [conversation, tasks, decisions] = await Promise.all([
      request("/conversation", { known: versions.conversation }),
      request("/tasks", { known: versions.tasks }),
      request("/decisions", { known: versions.decisions }).catch(() => null),
    ]);
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    versions = { conversation: conversation.version || "", tasks: tasks.version || "", decisions: decisions?.version || "" };
    // A restarted backend lost the context; queued tasks wait until it's sent again.
    if (conversation.context_ready === false && configuredContext) { configuredContext = ""; void syncContext(); }
    if (conversation.opened_projects?.length) openedProjectsListener?.(conversation.opened_projects);
    const previousTasks = state.status === "ready" ? state.tasks : null;
    const previousMessages = state.status === "ready" ? state.messages : null;
    // Once read, the list stays loaded through a failed poll, so chat cards don't flicker.
    const decisionsLoaded = state.decisionsLoaded || Boolean(decisions && (decisions.unchanged || Array.isArray(decisions.items)));
    const nextDecisions = decisions && !decisions.unchanged ? sameDecisions(decisions, state.decisions) : state.decisions;
    if (nextDecisions !== state.decisions || decisionsLoaded !== state.decisionsLoaded) set({ decisions: nextDecisions, decisionsLoaded });
    if (conversation.unchanged && tasks.unchanged) {
      // Nothing re-renders unless an earlier failed poll needs clearing.
      if (state.status !== "ready" || state.error) set({ status: "ready", error: "" });
    } else {
      set(current => ({
        ...current,
        status: "ready",
        error: "",
        ...(conversation.unchanged ? {} : {
          conversation: conversation.conversation ?? current.conversation,
          ...windowedMessages(conversation, current),
          chatTickets: sameTickets(conversation.tickets, current.chatTickets),
        }),
        ...(tasks.unchanged ? {} : { tasks: withDismissals(mergeTasks(tasks.tasks ?? [], current.tasks), { settle: true }), taskTickets: sameTickets(tasks.tickets, current.taskTickets), objectives: shownObjectives(tasks.objectives) }),
      }));
      // The poll sends the first page; a changed list reloads as many as the user had loaded.
      if (!tasks.unchanged && loadedObjectives > OBJECTIVES_PAGE && state.objectives.items.length < Math.min(loadedObjectives, state.objectives.total ?? Infinity)) void loadRemObjectives(loadedObjectives);
      if (!tasks.unchanged) reloadProjectObjectives();
      if (!tasks.unchanged) notifyFinishedRuns(previousTasks, tasks.tasks ?? []);
      if (!conversation.unchanged) notifyRemReply(previousMessages, conversation.messages ?? []);
    }
    // Include finished runs so the final reply and unread state reach the
    // thread list even when a run completes between polls. Runs come newest
    // first, and a thread reused by a later run keeps that run's version, so an
    // older finished run can't mark a running thread as already synced.
    const threads = new Map();
    for (const task of state.tasks) {
      for (const run of [task.active_run, task.last_run, ...(task.recent_runs || [])].filter(Boolean)) {
        const version = run.finished_at ? JSON.stringify([run.state, run.epoch, run.finished_at]) : null;
        for (const id of [run.thread_id, run.verification_thread_id].filter(Boolean)) if (!threads.has(id)) threads.set(id, version);
      }
    }
    const outcomes = await Promise.allSettled([...threads].map(async ([id, version]) => {
      if (version && finishedThreadVersions.get(id) === version) return;
      await syncRemThread(id, { poll: true });
      if (version) finishedThreadVersions.set(id, version);
      else finishedThreadVersions.delete(id);
    }));
    // A thread copy that can't be saved (storage full, IndexedDB error) fails on every
    // poll; report each distinct problem once instead of dropping it.
    [...threads.keys()].forEach((id, index) => {
      const outcome = outcomes[index];
      if (outcome.status !== "rejected") { threadSyncFailures.delete(id); return; }
      const error = outcome.reason;
      if (error?.name === "AbortError" || unavailable(error)) return;
      const message = error?.message || String(error);
      if (threadSyncFailures.get(id) === message) return;
      threadSyncFailures.set(id, message);
      console.warn(`Rem couldn't update its copy of thread ${id}: ${message}`);
    });
  } catch (error) {
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    set(unavailable(error) ? { status: "offline", error: "" } : { status: state.status === "loading" ? "error" : state.status, error: error.message });
  }
}
// Finished work reaches the user even while Raticode is in the background
// (backgroundNotices.js). The presentation helpers load on demand so they stay
// out of the app's first chunk.
function notifyFinishedRuns(previous, tasks) {
  if (!previous || !backgroundNoticesAllowed()) return;
  void import("./remHome.js").then(({ finishedRunNotifications }) => {
    for (const notice of finishedRunNotifications(previous, tasks)) {
      showBackgroundNotice({ ...notice, open: () => {
        window.dispatchEvent(new CustomEvent("gofer:rem-show-task", { detail: { taskId: notice.taskId } }));
        openTaskSheet(notice.taskId);
      } });
    }
  }, () => {});
}
// Rem's reply in its own chat: one notice for the newest, which opens Rem's chat there.
function notifyRemReply(previous, messages) {
  const reply = newRemReplies(previous, messages).at(-1);
  if (reply) showBackgroundNotice({ title: "Rem replied", tag: `rem-reply-${reply.id}`, open: () => showRemMessage(reply.id) });
}
// Tasks the server didn't change keep their objects, so only the chat rows
// showing a changed task re-render while Rem works.
export function mergeTasks(server, current = []) {
  const previous = new Map(current.map(task => [task.id, task]));
  return server.map(task => {
    const old = previous.get(task.id);
    return old && JSON.stringify(old) === JSON.stringify(task) ? old : task;
  });
}
// The chat after a poll: earlier pages that are still next to the server's window, the
// window, then messages waiting on the server and unsent ones.
function windowedMessages(conversation, current) {
  const window = conversation.messages ?? [];
  const inWindow = new Set(window.map(message => message.id));
  // History cleared elsewhere (another window or a phone) starts a new conversation.
  const cleared = Boolean(current.conversation?.id && conversation.conversation?.id && conversation.conversation.id !== current.conversation.id);
  let older = cleared ? [] : current.older;
  let earlier = Number(conversation.earlier) || 0;
  if (older.length) {
    // Messages the window moved past stay, oldest first, after the loaded pages.
    const start = window.length ? serverWindow.findIndex(message => message.id === window[0].id) : serverWindow.length;
    if (start < 0) {
      // So much arrived at once that the window skipped past what was loaded; start over.
      older = [];
    } else {
      const kept = new Set(older.map(message => message.id));
      older = [...older, ...serverWindow.slice(0, start).filter(message => !inWindow.has(message.id) && !kept.has(message.id))]
        .filter(message => !inWindow.has(message.id));
      earlier = current.earlier;
    }
  }
  serverWindow = window;
  // Keep optimistic messages until the server reports their request. Messages sent
  // while Rem was replying, from here or a phone, wait on the server and show as waiting.
  const server = [...older, ...window, ...(conversation.waiting ?? []).map(message => ({ ...message, pending: true, waiting: true }))];
  return { older, earlier, messages: mergeMessages(server, current.messages), ...(older.length ? {} : { olderTickets: NO_TICKETS }) };
}

// Reads the page of Rem's chat before the oldest message shown (GET /api/rem/history,
// the desktop's side of the phone's rem.history). With `through`, pages back until that
// message is shown, such as a search result. Returns whether anything was added.
export async function loadEarlierRemMessages({ through = null } = {}) {
  if (state.fixture || state.resetting || state.loadingEarlier || !state.earlier) return false;
  const generation = historyGeneration;
  set({ loadingEarlier: true });
  let added = 0;
  try {
    while (state.earlier > 0 && !(through && state.messages.some(message => message.id === through))) {
      const first = state.messages.find(message => !message.pending && !message.failed);
      if (!first) break;
      const params = new URLSearchParams({ before: first.id });
      if (through) params.set("through", through);
      const page = await request(`/history?${params}`);
      if (generation !== historyGeneration || state.resetting) return false;
      const fresh = (page.messages ?? []).filter(message => !state.messages.some(shown => shown.id === message.id));
      added += fresh.length;
      set(current => ({
        ...current,
        older: [...fresh, ...current.older],
        messages: [...fresh, ...current.messages],
        earlier: Number(page.earlier) || 0,
        olderTickets: page.tickets && Object.keys(page.tickets).length ? { ...current.olderTickets, ...page.tickets } : current.olderTickets,
      }));
      // A page that adds nothing would ask for the same page again.
      if (!through || !fresh.length) break;
    }
  } catch (error) {
    if (generation === historyGeneration) notify(error.status === 404 ? "That part of Rem's chat is no longer stored." : `Couldn't load earlier messages. ${error.message}`);
  } finally {
    if (generation === historyGeneration) set({ loadingEarlier: false });
  }
  return added > 0;
}

function mergeMessages(server, current) {
  const known = new Set(server.map(message => message.request_id).filter(Boolean));
  // Messages the server didn't change keep their objects, so their rows stay cached.
  const previous = new Map(current.filter(message => !message.pending && !message.failed).map(message => [message.id, message]));
  const merged = server.map(message => {
    const old = previous.get(message.id);
    return old && JSON.stringify(old) === JSON.stringify(message) ? old : message;
  });
  // Unsent messages stay, so "Try again" is still there after the next poll.
  return [...merged, ...current.filter(message => (message.pending || message.failed) && !known.has(message.request_id))];
}

// Starts polling while a Rem surface is mounted. Event replay can replace this
// without changing callers.
export function connectRemHome() {
  connections += 1;
  if (!stopPolling && !state.fixture) stopPolling = startPolling(refreshRemHome, { interval: 2500, immediate: true });
  return () => {
    connections -= 1;
    if (connections > 0) return;
    stopPolling?.();
    stopPolling = null;
  };
}

export function dismissNotice() { set({ notice: "" }); }

// View state shared by the sidebar and the conversation pane.
// A task opens in the Rem sidebar; the app brings that sidebar forward when the
// request came from elsewhere, such as a task card in the chat.
export function openTaskSheet(taskId) { set({ sheet: { mode: "task", taskId } }); announceTaskSheet(); }
export function openNewTask() { set({ sheet: { mode: "new" } }); announceTaskSheet(); }
function announceTaskSheet() { globalThis.window?.dispatchEvent?.(new CustomEvent("gofer:rem-task-sheet")); }
// Scrolls Rem's chat to a message, showing the chat first when it is hidden.
export function showRemMessage(id) {
  set({ messageRequest: { id, seq: ++sequence } });
  globalThis.window?.dispatchEvent?.(new CustomEvent("gofer:rem-show-message", { detail: { id } }));
}
// Words to find in Rem's whole saved chat, newest first. The desktop searches its own
// history; no model reads the words. `before` asks for the page after an earlier one.
export async function searchRemHistory(query, { before = null, limit = 50 } = {}) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  if (before != null) params.set("before", String(before));
  return request(`/search?${params}`);
}
export function closeTaskSheet() { set({ sheet: null }); }
export function suggestMessage(text) { set({ composerRequest: { text, id: ++sequence } }); }
export function focusRemComposer() { set({ composerRequest: { id: ++sequence } }); }
function notify(message) { set({ notice: message }); }
// A refusal of something the user did in the side pane, shown there: Rem's chat, where
// `notice` shows, may not be open.
export function tellSidePane(message) { set({ sideNotice: message }); }
export function dismissSideNotice() { set({ sideNotice: "" }); }

// `conflict` explains a 409 for commands that send the revision they edited.
async function command(run, failure, conflict = "") {
  if (state.resetting) return null;
  if (state.status !== "ready" && !state.fixture) { notify("Rem's task service isn't connected, so nothing changed."); return null; }
  try {
    await syncContext();
    const result = await run();
    if (!state.fixture) await refreshRemHome();
    return result ?? true;
  } catch (error) {
    notify(error.status === 409 && conflict ? conflict : `${failure} ${error.message}`);
    return null;
  }
}

// `reference` is the preview of the message this one replies to (replyTarget in remRich.js).
// `screen` is a screenshot from Show Rem your screen (screenContext.js). It is held here
// only until Rem accepts the message, so Try again can send it again; Rem uses it for one
// reply and the message keeps only its name (screen_capture).
export async function sendRemMessage(body, attachments = [], retryId = null, reference = null, screen = null) {
  const text = body.trim();
  if (!text && !attachments.length && !screen) return false;
  const id = retryId || requestId("rem-msg");
  const link = reference?.id ? { refers_to: reference.id, referenced: reference } : {};
  const shown = screen ? { screen, screen_capture: screen.name } : {};
  const pending = { id, request_id: id, role: "user", body: text, created_at: new Date().toISOString(), pending: true, ...(attachments.length ? { attachments } : {}), ...shown, ...link };
  set(current => ({ ...current, messages: [...current.messages, pending] }));
  if (state.fixture) {
    set(current => ({
      ...current,
      conversation: { ...current.conversation, turn_state: "idle" },
      messages: [...current.messages.map(message => message.id === id ? { ...message, pending: false } : message),
        { id: requestId("rem-reply"), role: "rem", created_at: new Date().toISOString(), body: "I'm in preview mode, so I can't start anything yet." }],
    }));
    return true;
  }
  // Messages go to Rem one at a time, in the order they were written.
  const turn = sendQueue.then(() => command(async () => {
    // Uploaded references replace local files, so a retry doesn't upload them again.
    const uploaded = await uploadChatAttachments(attachments, REM_ATTACHMENT_THREAD);
    if (uploaded !== attachments) patchMessage(id, { attachments: uploaded });
    const payload = { request_id: id, body: text, ...(uploaded.length ? { attachments: uploaded } : {}), ...(link.refers_to ? { refers_to: link.refers_to } : {}), ...(screen ? { screen_image: await encodeScreen(screen) } : {}) };
    return whenRemIsFree(id, () => request("/messages", { method: "POST", body: payload }));
  }, "Rem didn't get that."));
  sendQueue = turn.catch(() => {});
  const result = await turn;
  if (!result) patchMessage(id, { pending: false, waiting: false, failed: true });
  // Rem has the picture now; this window lets go of it.
  else if (screen) patchMessage(id, { screen: null });
  return Boolean(result);
}

function patchMessage(id, patch) { set(current => ({ ...current, messages: current.messages.map(message => message.id === id ? { ...message, ...patch } : message) })); }

// Rem keeps a few messages sent while it replies and refuses more, or answers an
// older backend's refusal the same way. A refused message waits for the reply,
// then goes with the same request ID, so a retry can never deliver it twice.
async function whenRemIsFree(id, send) {
  const deadline = Date.now() + REPLY_WAIT_MS;
  for (let checkedIdle = false; ;) {
    try { return await send(); } catch (error) {
      if (error.status !== 409 || state.resetting || Date.now() > deadline) throw error;
      await refreshRemHome();
      if (state.conversation?.turn_state !== "replying") {
        // The reply may have just finished; anything else is a real conflict.
        if (checkedIdle) throw error;
        checkedIdle = true;
        continue;
      }
      checkedIdle = false;
      patchMessage(id, { waiting: true });
      await new Promise(resolve => window.setTimeout(resolve, REPLY_POLL_MS));
    }
  }
}

export function retryRemMessage(id) {
  const message = state.messages.find(item => item.id === id);
  if (!message) return;
  set(current => ({ ...current, messages: current.messages.filter(item => item.id !== id) }));
  void sendRemMessage(message.body, message.attachments || [], message.request_id || message.id, message.referenced, message.screen || null);
}

// Moves an unsent message back into the composer to change it before sending.
export function editRemMessage(id) {
  const message = state.messages.find(item => item.id === id);
  if (!message) return;
  set(current => ({ ...current, messages: current.messages.filter(item => item.id !== id), composerRequest: { text: message.body, attachments: message.attachments || [], replyTo: message.referenced || null, screen: message.screen || null, id: ++sequence } }));
}

export async function sendManagedRemInstruction(threadId, body) {
  await syncContext();
  const result = await request(`/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST", body: { request_id: requestId("rem-instruction"), body },
  });
  await syncRemThread(result.run.thread_id);
  return result;
}

function localTask(id, patch) { set(current => ({ ...current, tasks: current.tasks.map(task => task.id === id ? { ...task, ...patch, revision: (task.revision || 0) + 1 } : task) })); }

export function createRemTask(recipe) {
  if (state.fixture) {
    const task = { id: requestId("task"), revision: 1, state: "enabled", ...recipe, acceptance_criteria: recipe.acceptance_criteria.map(item => ({ id: requestId("criterion"), ...item })), active_run: recipe.schedule.kind === "once" ? { id: requestId("run"), state: "queued" } : null };
    set(current => ({ ...current, tasks: [task, ...current.tasks] }));
    return Promise.resolve(task);
  }
  return command(async () => (await request("/tasks", { method: "POST", body: { request_id: requestId("task-create"), ...recipe } })).task, "Couldn't save the task.");
}

// One task with its prompt, which the polled task list leaves out (GET /api/rem/tasks/{id}).
// Null when it can't be read; the sheet then offers to try again.
export async function loadRemTask(taskId) {
  if (state.fixture) return state.tasks.find(task => task.id === taskId) ?? null;
  try {
    return (await request(`/tasks/${encodeURIComponent(taskId)}`)).task ?? null;
  } catch {
    return null;
  }
}

export function updateRemTask(task, patch, { applyTo = "current_and_future" } = {}) {
  if (state.fixture) { localTask(task.id, { ...patch, ...(patch.acceptance_criteria ? { acceptance_criteria: patch.acceptance_criteria.map(item => ({ id: item.id || requestId("criterion"), ...item })) } : {}) }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}`, { method: "PATCH", body: { request_id: requestId("task-edit"), expected_revision: task.revision, apply_to: applyTo, ...patch } }), "Couldn't save your changes.", "Someone changed this first. Your edits are still here.");
}

// How many objectives the side pane has asked for: the first page, plus one more page each
// time the user scrolls to the bottom. Polls and changes then load as many again.
let loadedObjectives = OBJECTIVES_PAGE;
let loadingObjectives = null;

async function loadRemObjectives(limit) {
  if (loadingObjectives) return loadingObjectives;
  loadingObjectives = (async () => {
    try {
      const view = await request(`/objectives?limit=${limit}`);
      if (Array.isArray(view?.items)) { set({ objectives: shownObjectives(view) }); reloadProjectObjectives(); }
    } catch {
      // The next scroll or poll tries again.
    } finally {
      loadingObjectives = null;
    }
  })();
  return loadingObjectives;
}

// The user scrolled to the bottom of the objectives: load the next page.
export function loadMoreRemObjectives() {
  if (state.fixture || state.status !== "ready") return Promise.resolve();
  loadedObjectives = Math.max(loadedObjectives, state.objectives.items.length) + OBJECTIVES_PAGE;
  return loadRemObjectives(loadedObjectives);
}

// Each project's section pages on its own: how many of its objectives the user has loaded,
// by project folder, when more than the first page the poll sends.
const loadedProjectObjectives = new Map();
const loadingProjectObjectives = new Map();

function loadProjectObjectives(root, limit, offset) {
  if (loadingProjectObjectives.has(root)) return loadingProjectObjectives.get(root);
  const loading = (async () => {
    try {
      const page = await request(`/objectives?project=${encodeURIComponent(root)}&limit=${limit}&offset=${offset}`);
      const merged = withProjectPage(serverObjectives, root, page);
      if (merged) { serverObjectives = merged; showObjectives(); }
    } catch {
      // The next Show more or poll tries again.
    } finally {
      loadingProjectObjectives.delete(root);
    }
  })();
  loadingProjectObjectives.set(root, loading);
  return loading;
}

// Show more in a project's section: the next page from where its loaded list ends.
export function loadMoreProjectObjectives(root) {
  if (state.fixture || state.status !== "ready") return Promise.resolve();
  const section = serverObjectives.projects?.[root] || NO_OBJECTIVES;
  const offset = (section.offset || 0) + section.items.length;
  loadedProjectObjectives.set(root, Math.max(loadedProjectObjectives.get(root) || 0, offset) + OBJECTIVES_PAGE);
  return loadProjectObjectives(root, OBJECTIVES_PAGE, offset);
}

// A changed list comes with each project's first page: load again as many as the user had.
function reloadProjectObjectives() {
  for (const [root, limit] of loadedProjectObjectives) {
    const section = serverObjectives.projects?.[root];
    if (!section) loadedProjectObjectives.delete(root);
    else if (section.items.length < Math.min(limit, section.total ?? Infinity)) void loadProjectObjectives(root, limit, 0);
  }
}

// The objectives as the server last sent them. What the side pane shows is this with the
// user's changes still on their way laid over it (pendingObjectives).
let serverObjectives = NO_OBJECTIVES;
// Changes waiting for the backend, oldest first. Each one goes after the one before it has
// answered, with the revision that answer carried, so quick clicks never trip over each other.
const pendingObjectives = [];
let sendingObjectives = null;

// A view from the server becomes the base; one older than the base (a poll that set off
// before the last change landed) is ignored. Returns what the side pane should show.
function shownObjectives(view) {
  if (Array.isArray(view?.items) && (view.revision ?? 0) >= (serverObjectives.revision ?? 0)) serverObjectives = sameObjectives(view, serverObjectives);
  if (!pendingObjectives.length) return serverObjectives;
  const shown = pendingObjectives.reduce((current, entry) => previewObjectiveChange(current, entry.change) || current, serverObjectives);
  // Not sameObjectives: a removal shows fewer items at the same revision, which it would
  // take for a shorter page and ignore.
  return JSON.stringify(shown) === JSON.stringify(state.objectives) ? state.objectives : shown;
}
function showObjectives() {
  const objectives = shownObjectives(null);
  if (objectives !== state.objectives) set({ objectives });
}

async function sendObjectives() {
  const generation = historyGeneration;
  while (pendingObjectives.length && !state.resetting) {
    const entry = pendingObjectives[0];
    entry.sent = true;
    const limit = loadedObjectives > OBJECTIVES_PAGE ? { limit: loadedObjectives } : {};
    try {
      const view = await request("/objectives", { method: "POST", body: { ...entry.change, ...limit, expected_revision: serverObjectives.revision } });
      // A reset meanwhile emptied the queue and started over; this answer is for neither.
      if (generation !== historyGeneration) return;
      pendingObjectives.shift();
      // The answer is the list with this change in it, so it replaces the base even when a
      // poll has already shown the same revision.
      if (Array.isArray(view?.items)) serverObjectives = sameObjectives(view, serverObjectives);
      showObjectives();
      reloadProjectObjectives();
      entry.settle(view ?? true);
    } catch (error) {
      if (generation !== historyGeneration) return;
      if (error.status === 409) {
        // Someone else changed the list first (Rem, the phone, the CLI). Every change still
        // waiting was made on the list this one assumed, so none of them is sent.
        const dropped = pendingObjectives.splice(0);
        showObjectives();
        tellSidePane("The objectives changed meanwhile, so your last changes weren't saved. Check them and try again.");
        for (const item of dropped) item.settle(null);
        await loadRemObjectives(Math.max(loadedObjectives, OBJECTIVES_PAGE));
      } else {
        pendingObjectives.shift();
        showObjectives();
        tellSidePane(`Couldn't change the objectives. ${error.message}`);
        entry.settle(null);
      }
    }
  }
}

// Ask Rem in a project's section: the backend starts Rem writing objectives for it and
// answers with the list, whose ``asking`` the polls then follow. Throws the backend's reason.
export async function askRemObjectives(root) {
  if (state.fixture) return true;
  if (state.status !== "ready") throw new Error("Rem's task service isn't connected.");
  const limit = loadedObjectives > OBJECTIVES_PAGE ? { limit: loadedObjectives } : {};
  const view = await request("/objectives/ask", { method: "POST", body: { project: root, ...limit } });
  if (Array.isArray(view?.items)) set({ objectives: shownObjectives(view) });
  return true;
}

// The user's change to the self-work objectives: add, edit, remove, check, uncheck. A check,
// uncheck or remove shows at once and is sent in the background; it goes back, with the
// reason in the side pane, when the backend refuses it. A check undone before it was sent
// cancels out, so nothing goes. Every change carries the revision it was made on, so it
// never overwrites a change made meanwhile. Resolves to the new list, or null.
export function changeRemObjectives(change) {
  if (state.fixture) { set(current => ({ ...current, objectives: applyObjectiveChange(current.objectives, change) })); return Promise.resolve(true); }
  if (state.resetting) return Promise.resolve(null);
  if (state.status !== "ready") { tellSidePane("Rem's task service isn't connected, so nothing changed."); return Promise.resolve(null); }
  const opposite = { check: "uncheck", uncheck: "check" }[change.action];
  const waiting = opposite && pendingObjectives.findIndex(entry => !entry.sent && entry.change.id === change.id && entry.change.action === opposite);
  if (opposite && waiting >= 0) {
    const [undone] = pendingObjectives.splice(waiting, 1);
    undone.settle(null);
    showObjectives();
    return Promise.resolve(state.objectives);
  }
  return new Promise(resolve => {
    pendingObjectives.push({ change, sent: false, settle: resolve });
    showObjectives();
    if (!sendingObjectives) {
      const loop = sendObjectives().finally(() => { if (sendingObjectives === loop) sendingObjectives = null; });
      sendingObjectives = loop;
    }
  });
}

// The user's answer or change to Rem's decisions: use, change, edit, reject, reopen, forget,
// add. It carries the revision this window shows, so a change made meanwhile (on the phone,
// the CLI or in chat) is never overwritten: the list reloads and the user tries again.
// Resolves to {ok: true} or {ok: false, message} so the row that acted can say what happened.
export async function changeRemDecisions(change) {
  if (state.fixture) { set(current => ({ ...current, decisions: applyDecisionChange(current.decisions, change) })); return { ok: true }; }
  if (state.resetting) return { ok: false, message: "Rem is being cleared, so nothing changed." };
  if (state.status !== "ready") return { ok: false, message: "Rem's task service isn't connected, so nothing changed." };
  try {
    const view = await request("/decisions", { method: "POST", body: { ...change, expected_revision: state.decisions.revision } });
    set({ decisions: sameDecisions(view, state.decisions), decisionsLoaded: true });
    // A task that waited on this answer may move on; its card catches up now.
    void refreshRemHome();
    return { ok: true };
  } catch (error) {
    const stale = error.code === "conflict" || error.code === "not_found" || (error.status === 409 && !error.code);
    if (stale) await reloadRemDecisions();
    return { ok: false, message: stale ? (error.code === "not_found" ? GONE_MESSAGE : CONFLICT_MESSAGE) : `Couldn't save that. ${error.message}` };
  }
}

// OK or Cancel on a change the user asked Rem for in chat. No revision goes with it: the
// desktop applies the change only while the decision is as the card showed it, and says
// why when it isn't. Resolves to {ok: true} or {ok: false, message}.
export async function settleRemDecisionEdit(id, ok) {
  if (state.fixture) { set(current => settleEditLocally(current, id, ok ? "applied" : "cancelled")); return { ok: true }; }
  if (state.resetting) return { ok: false, message: "Rem is being cleared, so nothing changed." };
  if (state.status !== "ready") return { ok: false, message: "Rem's task service isn't connected, so nothing changed." };
  try {
    const view = await request("/decisions", { method: "POST", body: { action: ok ? "confirm_edit" : "cancel_edit", id } });
    set({ decisions: sameDecisions(view, state.decisions), decisionsLoaded: true });
    // The card under Rem's reply now says what happened.
    void refreshRemHome();
    return { ok: true };
  } catch (error) {
    if (error.code === "conflict" || error.code === "not_found") void reloadRemDecisions();
    return { ok: false, message: error.message || "Couldn't save that." };
  }
}

// Look for a project's check command, landing branch and decision folder in its own files.
// No model is asked and nothing runs; what is found goes through the decisions like any
// request, so a decision in force never changes. Resolves to {ok, message}.
export async function scanRemDecisions(project) {
  if (state.fixture) return { ok: true, message: scanSummary([]) };
  if (state.status !== "ready") return { ok: false, message: "Rem's task service isn't connected, so nothing was scanned." };
  try {
    const result = await request("/decisions/scan", { method: "POST", body: { project } });
    const view = { ...(result || {}) };
    const outcomes = view.outcomes || [];
    delete view.scan;
    delete view.outcomes;
    set({ decisions: sameDecisions(view, state.decisions), decisionsLoaded: true });
    return { ok: true, message: scanSummary(outcomes) };
  } catch (error) {
    return { ok: false, message: `Couldn't look through this project. ${error.message}` };
  }
}

// How many tracked files typed record paths match on the project's landing branch, before
// the user saves them. `target` is {project} for a new record or {id} for an edit or
// replacement. Resolves to the path_counts object, or null when they can't be counted.
export async function countRemDecisionPaths(target, appliesTo, forbids) {
  if (state.fixture || state.status !== "ready") return null;
  try {
    const result = await request("/decisions/paths", { method: "POST", body: { ...target, applies_to: appliesTo, forbids } });
    return result?.path_counts || null;
  } catch {
    return null;
  }
}

// Reads the whole list again, whatever version this window holds.
async function reloadRemDecisions() {
  if (state.fixture) return;
  try {
    const view = await request("/decisions");
    versions.decisions = view.version || "";
    set({ decisions: sameDecisions(view, state.decisions), decisionsLoaded: true });
  } catch {
    // The next poll tries again.
  }
}

export function runRemTaskNow(task) {
  if (state.fixture) { localTask(task.id, { active_run: { id: requestId("run"), state: "queued" } }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}/run`, { method: "POST", body: { request_id: requestId("task-run") } }), "Couldn't start the task.");
}

// Retries an interrupted review with the saved work, like asking Rem to "retry the review".
export function retryRemReview(run) {
  if (!run?.review_retryable) return Promise.resolve(false);
  if (state.fixture) {
    set(current => ({ ...current, tasks: current.tasks.map(task => task.last_run?.id === run.id ? { ...task, active_run: { ...run, state: "verifying", review_retryable: false } } : task) }));
    return Promise.resolve(true);
  }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/retry_review`, { method: "POST", body: { request_id: requestId("run-retry-review") } }), "Couldn't retry the review.");
}

export function pauseRemTask(task) {
  if (state.fixture) { localTask(task.id, { state: "paused" }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}/pause`, { method: "POST", body: { request_id: requestId("task-pause"), cancel_current: false } }), "Couldn't pause the task.");
}

export function resumeRemTask(task) {
  if (state.fixture) { localTask(task.id, { state: "enabled" }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}`, { method: "PATCH", body: { request_id: requestId("task-resume"), expected_revision: task.revision, enabled: true } }), "Couldn't resume the task.", "This task changed just now. Try resuming it again.");
}

// Make it a workflow: a new Rem task drafts a workflow from this repeating task's runs.
export function draftRemWorkflow(task) {
  if (state.fixture) return Promise.resolve(true);
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}/workflow`, { method: "POST", body: { request_id: requestId("task-workflow"), action: "draft" } }), "Couldn't start the workflow draft.", "A draft for this task is already going.");
}

// Switch over, step one: the cron trigger it would add and the workflow's files to review.
export function previewRemWorkflowSwitch(task) {
  return request(`/tasks/${encodeURIComponent(task.id)}/workflow`, { method: "POST", body: { action: "preview" } });
}

// Switch over, step two: approves exactly the version reviewed (its fingerprint), adds the
// trigger and pauses the task's schedule. Errors reach the review, which shows them.
export async function switchRemWorkflow(task, fingerprint, { acceptUnseen = false } = {}) {
  const result = await request(`/tasks/${encodeURIComponent(task.id)}/workflow`, { method: "POST", body: { action: "switch", fingerprint, accept_unseen: acceptUnseen } });
  await refreshRemHome();
  return result;
}

export function cancelRemRun(task) {
  const run = task.active_run;
  if (!run) return Promise.resolve(false);
  // "Cancelling" until the server confirms every attempt stopped.
  if (state.fixture) { localTask(task.id, { active_run: { ...run, state: "cancelling" } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST", body: { request_id: requestId("run-cancel") } }), "Couldn't stop the run.");
}

// Pauses a run that is going: its turn stops, its thread and worktree stay, its slot frees up.
export function pauseRemRun(task) {
  const run = task.active_run;
  if (!run) return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { active_run: null, last_run: { ...run, state: "paused", paused_by: "user" } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/pause`, { method: "POST", body: { request_id: requestId("run-pause") } }), "Couldn't pause the task.");
}

// Resumes a paused run in the same thread, with what it already did.
export function resumeRemRun(task) {
  const run = task.last_run;
  if (run?.state !== "paused") return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { active_run: { ...run, state: "queued", paused_by: undefined } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/resume`, { method: "POST", body: { request_id: requestId("run-resume") } }), "Couldn't resume the task.");
}

// Wake now: a sleeping worker resumes in its own thread without waiting out its sleep.
export function wakeRemRun(task) {
  const run = task.active_run;
  if (!run?.sleep?.asleep) return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { active_run: { ...run, sleep: { ...run.sleep, asleep: false }, waiting_reason: "" } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/wake`, { method: "POST", body: { request_id: requestId("run-wake") } }), "Couldn't wake the task.");
}

// Run there: the user's pick of the paired desktop a waiting run suggests. Nothing goes
// to another computer without it; the desktop checks the suggestion is still current.
export function runRemRunThere(task) {
  const run = task.active_run;
  const suggestion = run?.suggestion;
  if (run?.state !== "queued" || !suggestion?.pick) return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { active_run: { ...run, suggestion: null, waiting_reason: `Starting on ${suggestion.computer_name}` } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/run_there`, { method: "POST", body: { request_id: requestId("run-there"), computer_id: suggestion.computer_id } }), "Couldn't send the task to that computer.");
}

// Runs the user dismissed that the task list may not show as dismissed yet: run id to
// "sending" while the request is out, then "sent" until a poll shows it dismissed.
const dismissing = new Map();

// The task list with every dismissal on its way shown as done. With `settle`, `tasks` is
// what the server just sent: a sent dismissal it now shows, or whose run is no longer a
// task's latest, has nothing left to cover.
function withDismissals(tasks, { settle = false } = {}) {
  if (!dismissing.size) return tasks;
  if (settle) {
    const latest = new Map(tasks.filter(task => task.last_run?.id).map(task => [task.last_run.id, task.last_run]));
    for (const [id, stage] of dismissing) if (stage === "sent" && (!latest.has(id) || latest.get(id).acknowledged === true)) dismissing.delete(id);
  }
  return tasks.map(task => (dismissing.has(task.last_run?.id) && task.last_run.acknowledged !== true ? { ...task, last_run: { ...task.last_run, acknowledged: true } } : task));
}

// Dismiss and Dismiss all: the updates leave the list at once and the request goes in the
// background, so the user can move on to the next one. Runs the backend didn't dismiss come
// back, with its reason in the side pane when it refused.
function dismissRuns(ids, send, failure) {
  const fresh = ids.filter(id => !dismissing.has(id));
  if (!fresh.length) return Promise.resolve(false);
  if (state.resetting) return Promise.resolve(null);
  if (state.status !== "ready") { tellSidePane("Rem's task service isn't connected, so nothing changed."); return Promise.resolve(null); }
  const previous = new Map(state.tasks.filter(task => fresh.includes(task.last_run?.id)).map(task => [task.last_run.id, task.last_run.acknowledged]));
  for (const id of fresh) dismissing.set(id, "sending");
  set(current => ({ ...current, tasks: withDismissals(current.tasks) }));
  const undo = undone => {
    if (!undone.length) return;
    for (const id of undone) dismissing.delete(id);
    set(current => ({ ...current, tasks: current.tasks.map(task => {
      const run = task.last_run;
      if (!run || !undone.includes(run.id) || !previous.has(run.id)) return task;
      const restored = { ...run };
      if (previous.get(run.id) === undefined) delete restored.acknowledged;
      else restored.acknowledged = previous.get(run.id);
      return { ...task, last_run: restored };
    }) }));
  };
  const generation = historyGeneration;
  return (async () => {
    try {
      const result = await send(fresh);
      if (generation !== historyGeneration) return null;
      const done = Array.isArray(result?.dismissed) ? new Set(result.dismissed.map(item => item.run_id)) : new Set(fresh);
      for (const id of fresh) if (done.has(id) && dismissing.has(id)) dismissing.set(id, "sent");
      undo(fresh.filter(id => !done.has(id)));
      void refreshRemHome();
      return true;
    } catch (error) {
      if (generation !== historyGeneration) return null;
      undo(fresh);
      tellSidePane(`${failure} ${error.message}`);
      return null;
    }
  })();
}

export function acknowledgeRemRun(task) {
  if (!task.last_run) return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { last_run: { ...task.last_run, acknowledged: true } }); return Promise.resolve(true); }
  return dismissRuns([task.last_run.id], ([id]) => request(`/runs/${encodeURIComponent(id)}/acknowledge`, { method: "POST", body: { request_id: requestId("run-ack") } }), "Couldn't dismiss that.");
}

// Dismiss all: one request for every task update in `tasks` (see dismissibleUpdates).
// Only the runs shown are sent, so an update that arrives meanwhile stays.
export function acknowledgeAllRemRuns(tasks) {
  const runs = tasks.map(task => task.last_run?.id).filter(Boolean);
  if (!runs.length) return Promise.resolve(false);
  if (state.fixture) {
    const ids = new Set(runs);
    set(current => ({ ...current, tasks: current.tasks.map(task => (ids.has(task.last_run?.id) ? { ...task, last_run: { ...task.last_run, acknowledged: true } } : task)) }));
    return Promise.resolve(true);
  }
  return dismissRuns(runs, ids => request("/runs/acknowledge", { method: "POST", body: { request_id: requestId("run-ack-all"), run_ids: ids } }), "Couldn't dismiss the updates.");
}

export function deleteRemTask(task) {
  if (state.fixture) { set(current => ({ ...current, tasks: current.tasks.filter(item => item.id !== task.id) })); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}?request_id=${encodeURIComponent(requestId("task-delete"))}`, { method: "DELETE" }), "Couldn't delete the task.");
}

// A full reset removes the authoritative records and the renderer's durable
// mirrors. It never archives deleted history or leaves it available for recall.
export async function clearRemHistory() {
  if (state.resetting || !state.conversation) return false;
  const conversationId = state.conversation.id;
  ++historyGeneration;
  set({ resetting: true, notice: "" });
  try {
    const storage = window.localStorage;
    pendingClear ||= JSON.parse(storage.getItem("gofer-flow-rem-reset-pending") || "null") || { payload: { request_id: requestId("rem-reset"), conversation_id: conversationId, confirm: true } };
    storage.setItem("gofer-flow-rem-reset-pending", JSON.stringify(pendingClear));
    const result = pendingClear.result || (state.fixture ? { conversation: { id: requestId("preview"), turn_state: "idle" }, removed_thread_ids: [] }
      : await request("/reset", { method: "POST", body: pendingClear.payload }));
    pendingClear.result = result;
    storage.setItem("gofer-flow-rem-reset-pending", JSON.stringify(pendingClear));
    const index = JSON.parse(storage.getItem("gofer-flow-chat-threads") || "[]");
    const removed = new Set(result.removed_thread_ids || []);
    for (const entry of index) {
      const metadata = JSON.parse(storage.getItem(`gofer-flow-chat-thread-meta:${entry.id}`) || "null");
      if (metadata?.managedRunId) removed.add(entry.id);
    }
    const ids = [...removed];
    for (const id of ids) clearedThreads.add(id);
    // The archive queue serializes pending saves before physically forgetting.
    await window.goferDesktop?.rem?.forget?.(ids);
    const repository = conversationRepository();
    for (const id of ids) {
      await repository.remove(id);
      storage.removeItem(`gofer-flow-chat-thread-meta:${id}`);
      mirroredThreads.delete(id);
      threadVersions.delete(id);
      finishedThreadVersions.delete(id);
      runningThreads.delete(id);
      window.dispatchEvent(new CustomEvent("gofer:device-thread-deleted", { detail: { id } }));
    }
    storage.setItem("gofer-flow-chat-threads", JSON.stringify(index.filter(entry => !removed.has(entry.id))));
    configuredContext = "";
    versions = { conversation: "", tasks: "", decisions: "" };
    serverWindow = [];
    contextPending = Promise.resolve();
    pendingClear = null;
    storage.removeItem("gofer-flow-rem-reset-pending");
    set({ ...initialState(), status: "ready", fixture: state.fixture, conversation: result.conversation, composerRequest: { text: "", replyTo: null, id: ++sequence } });
    await syncContext();
    return true;
  } catch (error) {
    // Polling stays paused only while a confirmed reset still needs local
    // cleanup. If the server refused, the next attempt starts over; if it was
    // unreachable, the next attempt resends the same request.
    if (!pendingClear?.result) {
      if (error.status >= 400 && error.status < 500) window.localStorage.removeItem("gofer-flow-rem-reset-pending");
      pendingClear = null;
    }
    set({ resetting: false });
    notify(`Couldn't finish clearing Rem. ${error.message}`);
    return false;
  }
}

// Worker threads open in the Rem pane. Fixture thread IDs are not real threads.
export async function openRemThread(threadId) {
  if (!threadId) return;
  if (state.fixture) { notify("Preview threads aren't real, so there's nothing to open."); return; }
  try { await syncRemThread(threadId); }
  catch (error) { notify(`Couldn't open the thread. ${error.message}`); return; }
  window.dispatchEvent(new CustomEvent("gofer:rem-open-thread", { detail: { threadId } }));
}

export function resetRemHomeStore() { ++historyGeneration; serverObjectives = NO_OBJECTIVES; for (const entry of pendingObjectives.splice(0)) entry.settle(null); sendingObjectives = null; dismissing.clear(); stopPolling?.(); stopPolling = null; state = initialState(); configuredContext = ""; versions = { conversation: "", tasks: "", decisions: "" }; serverWindow = []; threadVersions.clear(); mirroredThreads.clear(); finishedThreadVersions.clear(); runningThreads.clear(); clearedThreads.clear(); pendingClear = null; loadedObjectives = OBJECTIVES_PAGE; loadingObjectives = null; loadedProjectObjectives.clear(); loadingProjectObjectives.clear(); emit(); }
// Explicit fixtures for tests, the visual harness and development previews.
// Production never seeds sample tasks or messages.
export async function loadRemHomeFixture(scene = "busy") {
  stopPolling?.();
  stopPolling = null;
  versions = { conversation: "", tasks: "", decisions: "" };
  serverWindow = [];
  const sample = await import("./remHomeSample.js");
  set({ ...initialState(), status: "ready", fixture: true, decisionsLoaded: true, ...sample.remHomeScene(scene) });
}
