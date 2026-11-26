// Unsent chat text, kept in localStorage so it survives a restart, an update or a reload.
// Only the text is kept: attachments, screenshots and reply-to previews stay in memory.
// Entries are [key, text] pairs, oldest first; keys are threadDraftKey(id) or REM_CHAT_DRAFT_KEY.
const CHAT_DRAFTS_KEY = "raticode.chatDrafts.v1";
export const REM_CHAT_DRAFT_KEY = "rem";
const NEW_THREAD_DRAFT_KEY = "new-thread";
const CHAT_DRAFT_MAX_CHARS = 20000;
const CHAT_DRAFT_MAX_COUNT = 50;
const CHAT_DRAFT_SAVE_DELAY_MS = 500;
const MAX_KEY_LENGTH = 512;

export const threadDraftKey = threadId => threadId ? `thread:${threadId}` : NEW_THREAD_DRAFT_KEY;

function loadChatDrafts(storage) {
  const drafts = new Map();
  try {
    const entries = JSON.parse(storage?.getItem(CHAT_DRAFTS_KEY) || "[]");
    if (!Array.isArray(entries)) return drafts;
    for (const entry of entries) {
      if (!Array.isArray(entry) || entry.length !== 2) continue;
      const [key, text] = entry;
      if (typeof key !== "string" || !key || key.length > MAX_KEY_LENGTH) continue;
      if (typeof text !== "string" || !text || text.length > CHAT_DRAFT_MAX_CHARS) continue;
      drafts.delete(key);
      drafts.set(key, text);
    }
  } catch { return new Map(); }
  return keepNewest(drafts);
}

function saveChatDrafts(drafts, storage) {
  try {
    const entries = [...drafts].slice(-CHAT_DRAFT_MAX_COUNT);
    if (!storage) return entries.length === 0;
    if (entries.length) storage.setItem(CHAT_DRAFTS_KEY, JSON.stringify(entries));
    else storage.removeItem(CHAT_DRAFTS_KEY);
    return true;
  } catch { return false; }
}

function keepNewest(drafts) {
  for (const key of drafts.keys()) {
    if (drafts.size <= CHAT_DRAFT_MAX_COUNT) break;
    drafts.delete(key);
  }
  return drafts;
}

// The drafts are read from the window's storage the first time they're needed, and
// again if that storage changes, so every write goes back to the storage it came from.
// Closing or reloading that window saves at once instead of after the typing pause.
let store = null;
let saveTimer;
const watchedWindows = new WeakSet();

function currentStore() {
  const win = globalThis.window;
  const storage = win?.localStorage ?? null;
  if (store?.storage !== storage) {
    if (store) flushChatDrafts();
    store = { storage, drafts: loadChatDrafts(storage), dirty: false };
  }
  if (typeof win?.addEventListener === "function" && !watchedWindows.has(win)) {
    watchedWindows.add(win);
    win.addEventListener("beforeunload", flushChatDrafts);
    win.addEventListener("pagehide", flushChatDrafts);
  }
  return store;
}

function scheduleSave() {
  store.dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushChatDrafts, CHAT_DRAFT_SAVE_DELAY_MS);
}

export function getChatDraft(key) {
  return currentStore().drafts.get(key) || "";
}

// An empty composer clears its draft; the newest change moves to the end, so the oldest drafts go first.
export function setChatDraft(key, text) {
  const { drafts } = currentStore();
  const value = String(text ?? "").slice(0, CHAT_DRAFT_MAX_CHARS);
  if (!value) return deleteChatDraft(key);
  if (drafts.get(key) === value) return;
  drafts.delete(key);
  drafts.set(key, value);
  keepNewest(drafts);
  scheduleSave();
}

export function deleteChatDraft(key) {
  if (currentStore().drafts.delete(key)) scheduleSave();
}

export function flushChatDrafts() {
  clearTimeout(saveTimer);
  if (!store?.dirty) return true;
  const saved = saveChatDrafts(store.drafts, store.storage);
  if (saved) store.dirty = false;
  return saved;
}
