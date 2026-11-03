import { useSyncExternalStore } from "react";
import { apiUrl } from "./api.js";
import { uploadChatAttachments } from "./chatAttachments.js";
import { startPolling } from "./refresh.js";
import { conversationRepository } from "./conversationRepository.js";
import { mergeInServerOrder } from "./conversationCache.js";
import { mergeTaskThreadMetadata, threadIndexEntry } from "./threadActivity.js";

// Shared state for the Rem sidebar and conversation. The backend control service
// (GET/POST /api/rem/...) is authoritative: the renderer submits commands and
// renders what the server reports. It never launches work or decides completion.

function initialState() {
  return { status: "loading", error: "", contextIssue: "", conversation: null, messages: [], tasks: [], fixture: false, resetting: false, notice: "", sheet: null, composerRequest: null };
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
// Content versions of what this window last applied. The backend answers
// "unchanged" for a matching version, so a poll while nothing happens downloads,
// parses and re-renders nothing. Older backends send no version.
let versions = { conversation: "", tasks: "" };
const threadVersions = new Map();
const withVersion = (path, version) => version ? `${path}?known=${encodeURIComponent(version)}` : path;
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

export async function request(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(apiUrl(`/rem${path}`), body === undefined ? { method } : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new RemServiceError("Raticode's backend isn't reachable.", 0);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new RemServiceError(payload.detail || payload.error || `Rem request failed (${response.status}).`, response.status);
  return payload;
}
// The service is absent (older backend) or unreachable; nothing can run.
const unavailable = error => error?.status === 0 || error?.status === 404 || error?.status === 501;

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
  const { version = "", ...remote } = await request(withVersion(`/threads/${encodeURIComponent(threadId)}`, poll ? threadVersions.get(threadId) : ""));
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
    const [conversation, tasks] = await Promise.all([
      request(withVersion("/conversation", versions.conversation)),
      request(withVersion("/tasks", versions.tasks)),
    ]);
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    versions = { conversation: conversation.version || "", tasks: tasks.version || "" };
    // A restarted backend lost the context; queued tasks wait until it's sent again.
    if (conversation.context_ready === false && configuredContext) { configuredContext = ""; void syncContext(); }
    const previousTasks = state.status === "ready" ? state.tasks : null;
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
          // Keep optimistic messages until the server reports their request.
          messages: mergeMessages(conversation.messages ?? [], current.messages),
        }),
        ...(tasks.unchanged ? {} : { tasks: tasks.tasks ?? [] }),
      }));
      if (!tasks.unchanged) notifyFinishedRuns(previousTasks, tasks.tasks ?? []);
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
    await Promise.allSettled([...threads].map(async ([id, version]) => {
      if (version && finishedThreadVersions.get(id) === version) return;
      await syncRemThread(id, { poll: true });
      if (version) finishedThreadVersions.set(id, version);
      else finishedThreadVersions.delete(id);
    }));
  } catch (error) {
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    set(unavailable(error) ? { status: "offline", error: "" } : { status: state.status === "loading" ? "error" : state.status, error: error.message });
  }
}
// Finished work reaches the user even while Raticode is in the background.
// Electron grants web notifications; a browser only shows them once allowed.
// The presentation helpers load on demand so they stay out of the app's first chunk.
function notifyFinishedRuns(previous, tasks) {
  const Notice = globalThis.Notification;
  if (!previous || !Notice || Notice.permission !== "granted" || globalThis.document?.hasFocus?.() !== false) return;
  void import("./remHome.js").then(({ finishedRunNotifications }) => {
    for (const notice of finishedRunNotifications(previous, tasks)) {
      try {
        const shown = new Notice(notice.title, { body: notice.body, tag: notice.tag });
        shown.onclick = () => {
          globalThis.focus?.();
          window.dispatchEvent(new CustomEvent("gofer:rem-show-task", { detail: { taskId: notice.taskId } }));
          openTaskSheet(notice.taskId);
        };
      } catch {
        // Notifications are a convenience; the result is already in Rem's chat.
      }
    }
  }, () => {});
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
export function openTaskSheet(taskId) { set({ sheet: { mode: "task", taskId } }); }
export function openNewTask() { set({ sheet: { mode: "new" } }); }
export function closeTaskSheet() { set({ sheet: null }); }
export function suggestMessage(text) { set({ composerRequest: { text, id: ++sequence } }); }
export function focusRemComposer() { set({ composerRequest: { id: ++sequence } }); }
function notify(message) { set({ notice: message }); }

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

export async function sendRemMessage(body, attachments = [], retryId = null) {
  const text = body.trim();
  if (!text && !attachments.length) return false;
  const id = retryId || requestId("rem-msg");
  const pending = { id, request_id: id, role: "user", body: text, created_at: new Date().toISOString(), pending: true, ...(attachments.length ? { attachments } : {}) };
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
    const payload = { request_id: id, body: text, ...(uploaded.length ? { attachments: uploaded } : {}) };
    return whenRemIsFree(id, () => request("/messages", { method: "POST", body: payload }));
  }, "Rem didn't get that."));
  sendQueue = turn.catch(() => {});
  const result = await turn;
  if (!result) patchMessage(id, { pending: false, waiting: false, failed: true });
  return Boolean(result);
}

function patchMessage(id, patch) { set(current => ({ ...current, messages: current.messages.map(message => message.id === id ? { ...message, ...patch } : message) })); }

// Rem answers one message at a time and refuses another while it replies. A
// message sent meanwhile waits for that reply, then goes with the same request
// ID, so a retry can never deliver it twice.
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
  void sendRemMessage(message.body, message.attachments || [], message.request_id || message.id);
}

// Moves an unsent message back into the composer to change it before sending.
export function editRemMessage(id) {
  const message = state.messages.find(item => item.id === id);
  if (!message) return;
  set(current => ({ ...current, messages: current.messages.filter(item => item.id !== id), composerRequest: { text: message.body, attachments: message.attachments || [], id: ++sequence } }));
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

export function updateRemTask(task, patch, { applyTo = "current_and_future" } = {}) {
  if (state.fixture) { localTask(task.id, { ...patch, ...(patch.acceptance_criteria ? { acceptance_criteria: patch.acceptance_criteria.map(item => ({ id: item.id || requestId("criterion"), ...item })) } : {}) }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}`, { method: "PATCH", body: { request_id: requestId("task-edit"), expected_revision: task.revision, apply_to: applyTo, ...patch } }), "Couldn't save your changes.", "Someone changed this first. Your edits are still here.");
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

export function cancelRemRun(task) {
  const run = task.active_run;
  if (!run) return Promise.resolve(false);
  // "Cancelling" until the server confirms every attempt stopped.
  if (state.fixture) { localTask(task.id, { active_run: { ...run, state: "cancelling" } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST", body: { request_id: requestId("run-cancel") } }), "Couldn't stop the run.");
}

export function acknowledgeRemRun(task) {
  if (!task.last_run) return Promise.resolve(false);
  if (state.fixture) { localTask(task.id, { last_run: { ...task.last_run, acknowledged: true } }); return Promise.resolve(true); }
  return command(() => request(`/runs/${encodeURIComponent(task.last_run.id)}/acknowledge`, { method: "POST", body: { request_id: requestId("run-ack") } }), "Couldn't dismiss that.");
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
    versions = { conversation: "", tasks: "" };
    contextPending = Promise.resolve();
    pendingClear = null;
    storage.removeItem("gofer-flow-rem-reset-pending");
    set({ ...initialState(), status: "ready", fixture: state.fixture, conversation: result.conversation, composerRequest: { text: "", id: ++sequence } });
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

export function resetRemHomeStore() { ++historyGeneration; stopPolling?.(); stopPolling = null; state = initialState(); configuredContext = ""; versions = { conversation: "", tasks: "" }; threadVersions.clear(); mirroredThreads.clear(); finishedThreadVersions.clear(); runningThreads.clear(); clearedThreads.clear(); pendingClear = null; emit(); }
// Explicit fixtures for tests, the visual harness and development previews.
// Production never seeds sample tasks or messages.
export async function loadRemHomeFixture(scene = "busy") {
  stopPolling?.();
  stopPolling = null;
  versions = { conversation: "", tasks: "" };
  const sample = await import("./remHomeSample.js");
  set({ ...initialState(), status: "ready", fixture: true, ...sample.remHomeScene(scene) });
}
