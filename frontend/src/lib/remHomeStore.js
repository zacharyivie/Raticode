import { useSyncExternalStore } from "react";
import { apiUrl } from "./api.js";
import { startPolling } from "./refresh.js";
import { conversationRepository } from "./conversationRepository.js";
import { threadIndexEntry } from "./threadActivity.js";

// Shared state for the Rem sidebar and conversation. The backend control service
// (GET/POST /api/rem/...) is authoritative: the renderer submits commands and
// renders what the server reports. It never launches work or decides completion.

function initialState() {
  return { status: "loading", error: "", conversation: null, messages: [], tasks: [], fixture: false, resetting: false, notice: "", sheet: null, composerRequest: null };
}

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

async function request(path, { method = "GET", body } = {}) {
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
    await request("/context", { method: "POST", body: context });
    if (factory === contextFactory && generation === historyGeneration) configuredContext = fingerprint;
  });
  contextPending = job;
  return job;
}

export function syncRemThread(threadId) {
  // Serialize reads and writes so a slow active snapshot cannot overwrite completion.
  const previous = threadSyncPending.get(threadId) || Promise.resolve();
  const result = previous.catch(() => {}).then(() => syncThreadSnapshot(threadId));
  threadSyncPending.set(threadId, result);
  void result.finally(() => {
    if (threadSyncPending.get(threadId) === result) threadSyncPending.delete(threadId);
  }).catch(() => {});
  return result;
}

async function syncThreadSnapshot(threadId) {
  const generation = historyGeneration;
  if (state.resetting || clearedThreads.has(threadId)) return null;
  const remote = await request(`/threads/${encodeURIComponent(threadId)}`);
  if (generation !== historyGeneration || state.resetting) return null;
  if (!remote.metadata) return null;
  const fingerprint = JSON.stringify(remote);
  if (mirroredThreads.get(threadId) === fingerprint) {
    // A newly mounted thread pane still needs the transcript and terminal state.
    window.dispatchEvent(new CustomEvent("gofer:device-thread-sync", { detail: remote }));
    return remote;
  }
  const repository = conversationRepository();
  const current = await repository.all(threadId);
  if (generation !== historyGeneration || state.resetting) return null;
  const byId = new Map(current.map(message => [String(message.id), message]));
  for (const message of remote.messages || []) byId.set(String(message.id), message);
  const messages = [...byId.values()];
  await repository.save(threadId, messages, current, messages);
  if (generation !== historyGeneration || state.resetting) return null;
  const storage = window.localStorage;
  storage.setItem(`gofer-flow-chat-thread-meta:${threadId}`, JSON.stringify(remote.metadata));
  const index = JSON.parse(storage.getItem("gofer-flow-chat-threads") || "[]");
  const entry = threadIndexEntry(remote.metadata);
  storage.setItem("gofer-flow-chat-threads", JSON.stringify([entry, ...index.filter(item => item.id !== threadId)]));
  mirroredThreads.set(threadId, fingerprint);
  window.dispatchEvent(new CustomEvent("gofer:device-thread-sync", { detail: remote }));
  return remote;
}

export async function refreshRemHome() {
  if (state.fixture || state.resetting || pendingClear) return;
  const generation = historyGeneration;
  try {
    await syncContext();
    const [conversation, tasks] = await Promise.all([request("/conversation"), request("/tasks")]);
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    set(current => ({
      ...current,
      status: "ready",
      error: "",
      conversation: conversation.conversation ?? current.conversation,
      // Keep optimistic messages until the server reports their request.
      messages: mergeMessages(conversation.messages ?? [], current.messages),
      tasks: tasks.tasks ?? [],
    }));
    // Include finished runs so the final reply and automatic archive reach the
    // thread list even when a run completes between polls.
    const threads = new Map();
    for (const task of tasks.tasks || []) {
      for (const run of [task.active_run, task.last_run, ...(task.recent_runs || [])].filter(Boolean)) {
        const version = run.finished_at ? JSON.stringify([run.state, run.epoch, run.finished_at]) : null;
        for (const id of [run.thread_id, run.verification_thread_id].filter(Boolean)) threads.set(id, version);
      }
    }
    await Promise.allSettled([...threads].map(async ([id, version]) => {
      if (version && finishedThreadVersions.get(id) === version) return;
      await syncRemThread(id);
      if (version) finishedThreadVersions.set(id, version);
      else finishedThreadVersions.delete(id);
    }));
  } catch (error) {
    if (state.fixture || state.resetting || generation !== historyGeneration) return;
    set(unavailable(error) ? { status: "offline", error: "" } : { status: state.status === "loading" ? "error" : state.status, error: error.message });
  }
}
function mergeMessages(server, current) {
  const known = new Set(server.map(message => message.request_id).filter(Boolean));
  return [...server, ...current.filter(message => message.pending && !known.has(message.request_id))];
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
function notify(message) { set({ notice: message }); }

async function command(run, failure) {
  if (state.resetting) return null;
  if (state.status !== "ready" && !state.fixture) { notify("Rem's task service isn't connected, so nothing changed."); return null; }
  try {
    await syncContext();
    const result = await run();
    if (!state.fixture) await refreshRemHome();
    return result ?? true;
  } catch (error) {
    notify(error.status === 409 ? "Someone changed this first. Your edits are still here." : `${failure} ${error.message}`);
    return null;
  }
}

export async function sendRemMessage(body, retryId = null) {
  const text = body.trim();
  if (!text) return false;
  const id = retryId || requestId("rem-msg");
  const pending = { id, request_id: id, role: "user", body: text, created_at: new Date().toISOString(), pending: true };
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
  const result = await command(() => request("/messages", { method: "POST", body: { request_id: id, body: text } }), "Rem didn't get that.");
  if (!result) set(current => ({ ...current, messages: current.messages.map(message => message.id === id ? { ...message, pending: false, failed: true } : message) }));
  return Boolean(result);
}

export function retryRemMessage(id) {
  const message = state.messages.find(item => item.id === id);
  if (!message) return;
  set(current => ({ ...current, messages: current.messages.filter(item => item.id !== id) }));
  void sendRemMessage(message.body, message.request_id || message.id);
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
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}`, { method: "PATCH", body: { request_id: requestId("task-edit"), expected_revision: task.revision, apply_to: applyTo, ...patch } }), "Couldn't save your changes.");
}

export function runRemTaskNow(task) {
  if (state.fixture) { localTask(task.id, { active_run: { id: requestId("run"), state: "queued" } }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}/run`, { method: "POST", body: { request_id: requestId("task-run") } }), "Couldn't start the task.");
}

export function pauseRemTask(task) {
  if (state.fixture) { localTask(task.id, { state: "paused" }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}/pause`, { method: "POST", body: { request_id: requestId("task-pause"), cancel_current: false } }), "Couldn't pause the task.");
}

export function resumeRemTask(task) {
  if (state.fixture) { localTask(task.id, { state: "enabled" }); return Promise.resolve(true); }
  return command(() => request(`/tasks/${encodeURIComponent(task.id)}`, { method: "PATCH", body: { request_id: requestId("task-resume"), expected_revision: task.revision, enabled: true } }), "Couldn't resume the task.");
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
      finishedThreadVersions.delete(id);
      window.dispatchEvent(new CustomEvent("gofer:device-thread-deleted", { detail: { id } }));
    }
    storage.setItem("gofer-flow-chat-threads", JSON.stringify(index.filter(entry => !removed.has(entry.id))));
    configuredContext = "";
    contextPending = Promise.resolve();
    pendingClear = null;
    storage.removeItem("gofer-flow-rem-reset-pending");
    set({ ...initialState(), status: "ready", fixture: state.fixture, conversation: result.conversation, composerRequest: { text: "", id: ++sequence } });
    await syncContext();
    return true;
  } catch (error) {
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

export function resetRemHomeStore() { ++historyGeneration; stopPolling?.(); stopPolling = null; state = initialState(); configuredContext = ""; mirroredThreads.clear(); finishedThreadVersions.clear(); clearedThreads.clear(); pendingClear = null; emit(); }
// Explicit fixtures for tests, the visual harness and development previews.
// Production never seeds sample tasks or messages.
export async function loadRemHomeFixture(scene = "busy") {
  stopPolling?.();
  stopPolling = null;
  const sample = await import("./remHomeSample.js");
  set({ ...initialState(), status: "ready", fixture: true, ...sample.remHomeScene(scene) });
}
