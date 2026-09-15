// Only persisted inactive histories may be evicted. Active/running histories and
// failed writes remain available even when they exceed the inactive budget.
export function createConversationCache({ load, save, changed, maxInactive = 4, maxBytes = 8 * 1024 * 1024 }) {
  const entries = new Map();
  const sizes = new WeakMap();
  let protectedIds = new Set();
  let batchDepth = 0;
  const pending = new Set();
  function bytes(value) {
    if (typeof value === "string") return value.length * 2;
    if (!value || typeof value !== "object") return 8;
    if (!sizes.has(value)) sizes.set(value, Object.entries(value).reduce((sum, [key, child]) => sum + key.length * 2 + bytes(child), 32));
    return sizes.get(value);
  }
  function prune() {
    let count = 0;
    let retained = 0;
    for (const [id, entry] of [...entries].reverse()) {
      if (protectedIds.has(id) || entry.dirty) continue;
      count += 1;
      retained += bytes(entry.messages);
      if (count > maxInactive || retained > maxBytes) entries.delete(id);
    }
  }
  function publish() {
    prune();
    changed(Object.fromEntries([...entries].map(([id, entry]) => [id, entry.messages])));
  }
  function get(id) {
    let entry = entries.get(id);
    if (!entry) { const messages = load(id); entry = { messages, persisted: messages, dirty: false }; }
    entries.delete(id);
    entries.set(id, entry);
    return entry.messages;
  }
  function flush() {
    for (const id of pending) {
      const entry = entries.get(id);
      if (entry) {
        const messages = entry.messages;
        const revision = entry.revision;
        const known = [...new Map([...(entry.persisted || []), ...(entry.previous || [])].map(message => [message.id, message])).values()];
        const finish = saved => {
          if (saved !== false) {
            entry.persisted = entry.revision === revision ? entry.messages : messages;
            if (entry.revision === revision) { entry.dirty = false; entry.previous = []; }
          }
          return saved;
        };
        const result = save(id, messages, known, entry.persisted);
        if (result?.then) {
          entry.dirty = true;
          result.then(finish).catch(() => { entry.dirty = true; }).finally(publish);
        } else {
          entry.dirty = result === false;
          finish(result);
        }
      }
    }
    pending.clear();
    publish();
  }
  return {
    get,
    trim(id, limit) {
      const entry = entries.get(id);
      if (entry && !entry.dirty) entries.set(id, { messages: entry.messages.slice(-limit), persisted: entry.messages.slice(-limit), dirty: false });
    },
    hydrate(id, messages, { recent = false } = {}) {
      const entry = entries.get(id);
      const current = entry?.messages || [];
      const merged = new Map((recent ? current : messages).map(message => [message.id, message]));
      if (recent) for (const message of messages) if (!merged.has(message.id)) merged.set(message.id, message);
      for (const message of current) merged.set(message.id, message);
      const combined = [...merged.values()];
      if (entry) {
        entry.messages = combined;
        if (!entry.dirty) entry.persisted = combined;
      } else entries.set(id, { messages: combined, persisted: combined, dirty: false });
      publish();
    },
    activate(ids) { protectedIds = new Set(ids); publish(); },
    update(id, next) {
      const current = get(id);
      const entry = entries.get(id);
      entry.previous = [...new Map([...(entry.previous || []), ...current].map(message => [message.id, message])).values()];
      entry.messages = typeof next === "function" ? next(current) : next;
      entry.revision = (entry.revision || 0) + 1;
      entry.dirty = true;
      pending.add(id);
      if (!batchDepth) flush();
    },
    // No timer: enqueue one transaction for each complete reader chunk,
    // including when an error event interrupts processing the chunk.
    batch(callback) {
      batchDepth += 1;
      try { return callback(); } finally { if (!--batchDepth) flush(); }
    },
    remove(id) { pending.delete(id); entries.delete(id); publish(); },
  };
}
