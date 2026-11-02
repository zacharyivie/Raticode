import { loadConversationMessages, saveConversationMessages, removeConversationMessages } from "./conversationStorage.js";

// History pages count conversation messages; thoughts and hidden context are extra.
export const CONVERSATION_PAGE_SIZE = 15;
const SEARCH_PAGE_SIZE = 40;
const keyFor = id => `gofer-flow-chat-thread:${id}`;
// 0: thought, loaded on demand. 1: counted message. 2: hidden context.
export const laneOf = message => message.kind === "thought" ? 0 : message.kind === "memory" || message.kind === "continuation-context" ? 2 : 1;
// Index of the first row of the latest page: everything after the message
// that precedes its `limit` conversation messages.
export function pageStart(messages, limit = CONVERSATION_PAGE_SIZE) {
  let start = messages.length;
  for (let count = 0; start > 0; start -= 1) if (laneOf(messages[start - 1]) === 1 && ++count > limit) break;
  return start;
}
const normalize = text => String(text ?? "").normalize("NFKC").toLowerCase();
const grams = text => {
  const result = new Set();
  for (let i = 0; i < text.length - 2; i += 1) result.add(text.slice(i, i + 3));
  return [...result];
};
const requestValue = request => new Promise((resolve, reject) => {
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});
const committed = transaction => new Promise((resolve, reject) => {
  transaction.oncomplete = resolve;
  transaction.onabort = () => reject(transaction.error || new Error("Conversation write aborted"));
  transaction.onerror = () => {}; // onabort owns the transaction failure.
});

// Message bodies live in IndexedDB, independently of the renderer's loaded window
// and the provider's context checkpoint. No age or size based history pruning.
export function createConversationRepository({ indexedDB = globalThis.indexedDB, storage = globalThis.window?.localStorage, name = "gofer-flow-conversations" } = {}) {
  let database;
  let pending = Promise.resolve();
  const migrated = new Set();
  const contextKey = id => `${keyFor(id)}:context`;
  const durable = Boolean(indexedDB);
  function db() {
    if (!database) database = new Promise((resolve, reject) => {
      const request = indexedDB.open(name, 2);
      request.onupgradeneeded = ({ oldVersion }) => {
        let messages;
        if (oldVersion < 1) {
          messages = request.result.createObjectStore("messages", { keyPath: ["threadId", "id"] });
          messages.createIndex("order", ["threadId", "sequence"], { unique: true });
          messages.createIndex("search", "grams", { multiEntry: true });
          request.result.createObjectStore("threads", { keyPath: "id" });
        } else messages = request.transaction.objectStore("messages");
        messages.createIndex("lane", ["threadId", "lane", "sequence"], { unique: true });
        if (oldVersion < 1) return;
        const cursor = messages.openCursor();
        cursor.onsuccess = () => {
          const row = cursor.result;
          if (!row) return;
          row.update({ ...row.value, lane: laneOf(row.value.message) });
          row.continue();
        };
      };
      request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
      request.onerror = () => { database = null; reject(request.error); };
      request.onblocked = () => reject(new Error("Close other app windows to upgrade conversation storage."));
    });
    return database;
  }
  function enqueue(callback) {
    const result = pending.then(callback);
    pending = result.catch(() => {});
    return result;
  }
  // `resequence` saves `messages` in the given order after everything already
  // stored, for a thread whose stored order no longer matches its true order.
  async function write(id, messages, removed = [], context, { resequence = false } = {}) {
    const database = await db();
    const tx = database.transaction(["messages", "threads"], "readwrite");
    const done = committed(tx);
    try {
      const records = tx.objectStore("messages");
      const threads = tx.objectStore("threads");
      const meta = await requestValue(threads.get(id)) || { id, sequence: 0 };
      for (const messageId of removed) records.delete([id, messageId]);
      for (const message of messages) {
        const messageId = message.id;
        const previous = await requestValue(records.get([id, messageId]));
        const text = normalize(message.body);
        records.put({ threadId: id, id: messageId, sequence: (!resequence && previous?.sequence) || ++meta.sequence, lane: laneOf(message), message, text, grams: grams(text) });
      }
      if (context !== undefined) meta.context = context;
      threads.put(meta);
      await done;
    } catch (error) { try { tx.abort(); } catch { /* Already completed. */ } await done.catch(() => {}); throw error; }
  }
  async function migrate(id) {
    if (!durable || migrated.has(id)) return;
    const legacy = loadConversationMessages(keyFor(id), storage).map((message, i) => ({ ...message, id: String(message.id || `legacy-${i}`) }));
    if (legacy.length) {
      await write(id, legacy);
      // Keep the old copy until the entire import and its search index commit.
      removeConversationMessages(keyFor(id), storage);
    }
    migrated.add(id);
  }
  // Read paths load on demand to keep them out of the App chunk.
  const read = async (name, ...args) => (await import("./conversationPages.js"))[name](await db(), ...args);
  return {
    durable,
    save(id, messages, previous = [], persisted = previous, { resequence = false } = {}) {
      if (!durable) { saveConversationMessages(keyFor(id), messages, storage); return true; }
      const ids = new Set(messages.map(message => message.id));
      const removed = previous.filter(message => !ids.has(message.id)).map(message => message.id);
      const changed = resequence ? messages : messages.filter(message => !persisted.includes(message));
      return enqueue(async () => {
        await migrate(id);
        if (changed.length || removed.length) await write(id, changed, removed, undefined, { resequence });
        return true;
      });
    },
    // Returns `limit` conversation messages with their hidden context. Earlier
    // thought runs come back as `thoughts` placeholders; see `thoughts()`.
    async page(id, before = Infinity, limit = CONVERSATION_PAGE_SIZE) {
      await pending;
      if (!durable) {
        const all = loadConversationMessages(keyFor(id), storage);
        const end = Math.min(before, all.length);
        const start = pageStart(all.slice(0, end), limit);
        return { messages: all.slice(start, end), before: start, hasMore: start > 0, thoughts: [] };
      }
      await enqueue(() => migrate(id));
      return read("readPage", id, before, limit);
    },
    // Durable storage only: other pages have no thought placeholders.
    async thoughts(id, ids) {
      await pending;
      return read("readMessages", id, ids);
    },
    // Every stored row, thoughts included, for the search viewport.
    async older(id, before = Infinity, limit = SEARCH_PAGE_SIZE) {
      await pending;
      if (!durable) {
        const all = loadConversationMessages(keyFor(id), storage);
        const end = Math.min(before, all.length);
        const start = Math.max(0, end - limit);
        return { messages: all.slice(start, end), before: start, hasMore: start > 0 };
      }
      await enqueue(() => migrate(id));
      return read("readOlder", id, before, limit);
    },
    async all(id) {
      await pending;
      if (!durable) return loadConversationMessages(keyFor(id), storage);
      await enqueue(() => migrate(id));
      return (await read("rows", id)).map(row => row.message);
    },
    async around(id, messageId, limit = SEARCH_PAGE_SIZE) {
      await pending;
      await enqueue(() => migrate(id));
      const half = Math.floor(limit / 2);
      if (!durable) {
        const all = loadConversationMessages(keyFor(id), storage);
        const index = all.findIndex(message => String(message.id) === String(messageId));
        if (index < 0) throw new Error("The matching message is no longer available.");
        const start = Math.max(0, index - half);
        const end = Math.min(all.length, start + limit);
        return { messages: all.slice(start, end), before: start, after: end, hasMore: start > 0, hasNewer: end < all.length };
      }
      return read("readAround", id, messageId, limit);
    },
    async newer(id, after, limit = SEARCH_PAGE_SIZE) {
      await pending;
      await enqueue(() => migrate(id));
      if (!durable) {
        const all = loadConversationMessages(keyFor(id), storage);
        const end = Math.min(all.length, after + limit);
        return { messages: all.slice(after, end), after: end, hasNewer: end < all.length };
      }
      return read("readNewer", id, after, limit);
    },
    checkpoint(id, messages, throughId) {
      const context = { messages, throughId };
      if (!durable) { storage.setItem(contextKey(id), JSON.stringify(context)); return Promise.resolve(); }
      return enqueue(async () => { await migrate(id); await write(id, [], [], context); });
    },
    async context(id, current, reset = false) {
      await pending;
      let context;
      if (durable) {
        const database = await db();
        context = (await requestValue(database.transaction("threads").objectStore("threads").get(id)))?.context;
      } else { context = JSON.parse(storage.getItem(contextKey(id)) || "null"); }
      if (reset) {
        context = null;
        if (durable) await enqueue(() => write(id, [], [], null));
        else storage.removeItem(contextKey(id));
      }
      if (durable && context) {
        const checkpointed = await read("readCheckpoint", id, context, current);
        if (checkpointed) return checkpointed;
      }
      const all = await this.all(id);
      // Include unsaved messages after a failed write; never silently omit the user's request.
      const merged = new Map(all.map(message => [message.id, message]));
      for (const message of current) merged.set(message.id, message);
      const history = [...merged.values()];
      const boundary = context && !reset ? history.findIndex(message => message.id === context.throughId) : -1;
      return boundary >= 0 ? [...context.messages, ...history.slice(boundary + 1)] : history;
    },
    async search(threads, query, { offset = 0, limit = 30 } = {}) {
      const needle = normalize(query).trim();
      if (!needle) return { results: [], hasMore: false };
      await pending;
      for (const thread of threads) await enqueue(() => migrate(thread.id));
      const byId = new Map(threads.map(thread => [thread.id, thread]));
      const matches = new Map();
      for (const thread of threads) if (normalize(thread.title).includes(needle)) matches.set(thread.id, { thread, snippet: "Title match" });
      const add = row => {
        if (!byId.has(row.threadId) || !row.text.includes(needle) || matches.get(row.threadId)?.messageId != null) return;
        const position = row.text.indexOf(needle);
        matches.set(row.threadId, { thread: byId.get(row.threadId), messageId: row.id, snippet: row.message.body.slice(Math.max(0, position - 45), position + needle.length + 100) });
      };
      if (!durable) {
        for (const thread of threads) for (const message of loadConversationMessages(keyFor(thread.id), storage)) add({ threadId: thread.id, id: message.id, message, text: normalize(message.body) });
      } else {
        await read("searchRows", needle, add);
      }
      const ordered = [...matches.values()].sort((a, b) => String(b.thread.updatedAt).localeCompare(String(a.thread.updatedAt)));
      return { results: ordered.slice(offset, offset + limit), hasMore: ordered.length > offset + limit };
    },
    remove(id) {
      if (!durable) { removeConversationMessages(keyFor(id), storage); storage.removeItem(contextKey(id)); return Promise.resolve(); }
      return enqueue(async () => {
        const database = await db();
        const tx = database.transaction(["messages", "threads"], "readwrite");
        const done = committed(tx);
        const cursor = tx.objectStore("messages").index("order").openCursor(IDBKeyRange.bound([id, 0], [id, Infinity]));
        cursor.onsuccess = () => { if (cursor.result) { cursor.result.delete(); cursor.result.continue(); } };
        tx.objectStore("threads").delete(id);
        await done;
        removeConversationMessages(keyFor(id), storage);
      });
    },
  };
}

let repository;
export function conversationRepository() {
  // Tests and separate renderer windows each own their storage instance.
  if (!repository || repository.storage !== globalThis.window?.localStorage) repository = { storage: globalThis.window?.localStorage, value: createConversationRepository() };
  return repository.value;
}
