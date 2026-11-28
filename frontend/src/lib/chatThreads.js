// Rem's chat threads: where they are saved on this device, the archive copy, and turning
// saved messages and stream lines into what the chat pane shows.
import { apiUrl } from "./api.js";
import { createConversationArchiveScheduler } from "./conversationArchive.js";
import { conversationRepository } from "./conversationRepository.js";
import { loadConversationMessages } from "./conversationStorage.js";
import { projectNameFromPath } from "./projects.js";
import { remThreadWasCleared } from "./remHomeStore.js";
import { turnAccount } from "./threadAccounts.js";
import { ARCHIVE_GIT_LIMIT, threadIndexEntry } from "./threadActivity.js";
import { samePath } from "./workspacePaths.js";
import { dateTime } from "./timeText.js";

const chatThreadsStorageKey = "gofer-flow-chat-threads";

export function bumpChatThread(threads, threadId, now = new Date().toISOString(), patch = {}) {
  const thread = threads.find((item) => item.id === threadId);
  return thread ? [{ ...thread, ...patch, updatedAt: now }, ...threads.filter((item) => item.id !== threadId)] : threads;
}

// A finished turn is unread until the user opens its thread.
export function threadReadPatch(unread, now = new Date().toISOString()) {
  return unread ? { unread: true } : { unread: false, readAt: now };
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
      // Archived entries need no scope lookup until the user opens them.
      const migrated = valid.map(entry => entry.scopeIndexed ? entry
        : threadIndexEntry(entry.archived ? entry : loadChatThread(entry.id) || entry));
      window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(migrated));
      return migrated;
    }
    return valid;
  } catch { return []; }
}

export function loadChatThread(id) {
  try {
    const thread = JSON.parse(window.localStorage.getItem(chatThreadMetadataKey(id)) || "null");
    return thread?.id === id && typeof thread.title === "string" ? thread : null;
  } catch { return null; }
}

export function loadAllChatThreads() { return loadChatThreads(Infinity); }

export function loadChatThreads(limit = 15) {
  return chatThreadIndex().slice(0, limit).map(({ id }) => loadChatThread(id)).filter(thread => thread && thread.managedRole !== "verify");
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

export function deleteStoredChatThread(id) {
  const index = chatThreadIndex().filter((entry) => entry.id !== id);
  window.localStorage.setItem(chatThreadsStorageKey, JSON.stringify(index));
  window.localStorage.removeItem(chatThreadMetadataKey(id));
}

export function threadTitleFromMessage(message) {
  const words = message.trim().split(/\s+/).slice(0, 8);
  const title = words.join(" ");
  return title.length < message.trim().length ? `${title}...` : title || "New thread";
}

export function formatThreadDate(value) {
  if (!value) return "No messages yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No messages yet";
  return dateTime(date);
}

export function reportArchiveError(error) {
  window.dispatchEvent?.(new CustomEvent("gofer:archive-error", { detail: error?.message || String(error) }));
}
const scheduleConversationArchive = createConversationArchiveScheduler({ reportError: reportArchiveError });
export function archiveThreadFromStorage(threadId, messages, deleted = false) {
  const bridge = window.goferDesktop?.rem;
  if (!bridge?.archive) return Promise.resolve(true);
  const storage = window.localStorage;
  return scheduleConversationArchive(threadId, async () => {
    if (remThreadWasCleared(threadId)) return { skipped: true };
    const thread = JSON.parse(storage.getItem(chatThreadMetadataKey(threadId)) || "null") || { id: threadId };
    return bridge.archive(thread, typeof messages === "function" ? await messages() : messages, deleted);
  }, { deleted });
}

// Delete permanently removes the archive copy: snapshot, journal and the attachments
// only this thread used. It goes through the same per-thread queue as saves.
export function purgeThreadFromArchive(threadId) {
  const bridge = window.goferDesktop?.rem;
  if (!bridge?.archive) return Promise.resolve(true);
  if (!bridge.purge) return Promise.resolve(false);
  return scheduleConversationArchive(threadId, () => bridge.purge([threadId]), { deleted: true });
}

export function permanentDeleteMessage(title) {
  return `Delete "${title || "Untitled"}" permanently? This removes the thread from Rem and its conversation archive snapshot, journal and the attachments no other thread uses. ${ARCHIVE_GIT_LIMIT} This cannot be undone.`;
}

export async function deletePhoneThread(id, { purge = false } = {}) {
  const repository = conversationRepository();
  if (!await (purge ? purgeThreadFromArchive(id) : archiveThreadFromStorage(id, () => repository.all(id), true))) {
    throw new Error("Phone deletion is waiting for the desktop archive. Reconnect its folder or stop archiving in Settings > Memory.");
  }
  const response = await fetch(apiUrl(`/chat/threads/${encodeURIComponent(id)}`), { method: "DELETE" });
  if (!response.ok) throw new Error("Phone deletion could not stop and remove the desktop turn. It will retry.");
  await repository.remove(id);
  deleteStoredChatThread(id);
}

export async function archiveAllConversations() {
  for (const thread of chatThreadIndex()) await archiveThreadFromStorage(thread.id, () => conversationRepository().all(thread.id));
}

export function chatStorageKeyFor(threadId) {
  return `gofer-flow-chat-thread:${threadId}`;
}

export function loadChatMessages(storageKey) {
  return loadConversationMessages(storageKey);
}

export function buildChatItems(messages) {
  // Hidden context must not split adjacent thoughts in the visible conversation.
  messages = messages.filter(message => message.kind !== "memory" && message.kind !== "continuation-context" &&
    !(message.role === "system" && String(message.body || "").startsWith("The previous process was interrupted.")));
  const items = [];
  let index = 0;

  while (index < messages.length) {
    const message = messages[index];
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
      messages[index].kind === "thought"
    ) {
      thoughts.push(messages[index]);
      index += 1;
    }
    const nextMessage = messages[index];
    while (isDuplicateOutputThought(thoughts.at(-1), nextMessage)) thoughts.pop();
    if (thoughts.length) {
      items.push({
        id: segmentId,
        type: "thought-group",
        thoughts,
        lazy: thoughts[0].lazy,
      });
    }
  }

  let replyId = null;
  for (let position = items.length - 1; position >= 0; position -= 1) {
    const item = items[position];
    if (item.type === "thought-group") item.replyId = replyId;
    else if (item.message.role === "user") replyId = null;
    else if (item.message.role === "assistant" && item.message.kind !== "error" && String(item.message.body || "").trim()) replyId = item.message.id;
  }
  return items;
}

export function isThoughtGroupExpanded(item, overrides, autoHide = true) {
  const override = overrides[item.id];
  if (override && override.replyId === item.replyId) return override.expanded;
  return !item.lazy && (!autoHide || !item.replyId);
}

// While Rem writes, its answer text shows as a reply instead of a thought.
// Tool steps that follow fold it back into the thoughts above them.
export function withLiveReply(items) {
  const group = items.at(-1);
  const thought = group?.type === "thought-group" ? group.thoughts.at(-1) : null;
  const stream = String(thought?.deltaStreamId || "");
  if (!stream || thought.trace || stream.startsWith("reasoning-") || !String(thought.body || "").trim()) return items;
  const earlier = group.thoughts.slice(0, -1);
  return [...items.slice(0, -1), ...(earlier.length ? [{ ...group, thoughts: earlier }] : []),
    { type: "message", message: { ...thought, kind: "streaming" } }];
}

export function removeTrailingDuplicateOutputThought(messages, finalBody, groupId) {
  const finalMessage = { role: "assistant", kind: "final", body: finalBody };
  const streams = new Map();
  for (const message of messages) {
    if (message.groupId !== groupId || message.kind !== "thought" || !message.deltaStreamId || message.trace?.kind === "tool") continue;
    streams.set(message.deltaStreamId, (streams.get(message.deltaStreamId) || "") + message.body);
  }
  const duplicateStreams = new Set([...streams].filter(([, body]) => normalizeMarkdownText(body) === normalizeMarkdownText(finalBody)).map(([id]) => id));
  return messages.filter((message) => {
    if (message.groupId !== groupId || message.kind !== "thought") return true;
    if (message.trace?.kind !== "tool" && duplicateStreams.has(message.deltaStreamId)) return false;
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
    const traceKey = `trace-${thought?.groupId || ""}-${traceId}`;
    const entry = {
      key: traceId ? traceKey : thought?.id || `trace-${entries.length}`,
      groupId: thought?.groupId,
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

    if (!traceId || !traceIndexes.has(traceKey)) {
      if (traceId) traceIndexes.set(traceKey, entries.length);
      entries.push(entry);
      continue;
    }

    const existingIndex = traceIndexes.get(traceKey);
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

export function uniqueClientId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function chatStreamRequestBody({ effort, provider, model, messages, workflow, permissionMode, conversationId, turnId, resetSession, forkedFrom, machine, providerAccounts, claudeAccount, threadAccount }) {
  // The thread's own account (the picker's Account menu) wins; otherwise Rem's setting
  // for the provider, or "auto" to let Rem pick one per thread. claudeAccount is the
  // first, Claude-only form of Rem's setting.
  const account = turnAccount({ provider, threadAccount, providerAccounts, claudeAccount });
  return {
    // A paired desktop that runs this thread instead of this computer.
    ...(machine ? { machine } : {}),
    provider,
    model,
    // Another desktop has its own accounts, so a remote turn never names one.
    ...(account && !machine ? { providerProfile: account } : {}),
    ...(conversationId && turnId ? { conversationId, turnId } : {}),
    ...(resetSession === true ? { resetSession: true } : {}),
    // Lets the backend copy the parent's provider session instead of re-sending history.
    ...(forkedFrom ? { forkedFrom } : {}),
    ...(effort ? { effort } : {}),
    ...(permissionMode ? { permissionMode } : {}),
    messages,
    workflow,
  };
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
    scopeMode: "project",
    ...(!samePath(thread.projectRoot, root) ? { projectBranch: undefined } : {}),
    projectName: String(projectName || (root ? projectNameFromPath(root) : "No project")),
    selectedWorkflowId: null,
  };
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
