// Loaded on demand by conversationRepository so the App chunk stays small.
const requestValue = request => new Promise((resolve, reject) => {
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

// Reads every stored row in order, thoughts included, between two sequences.
export function rows(database, id, { before = Infinity, after = 0, limit = Infinity, direction = "prev" } = {}) {
  const tx = database.transaction("messages");
  const range = IDBKeyRange.bound([id, after], [id, before], true, true);
  return new Promise((resolve, reject) => {
    const result = [];
    const request = tx.objectStore("messages").index("order").openCursor(range, direction);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor || result.length >= limit) { resolve(direction === "prev" ? result.reverse() : result); return; }
      result.push(cursor.value); cursor.continue();
    };
  });
}

export async function readOlder(database, id, before, limit) {
  const entries = await rows(database, id, { before, limit: limit + 1 });
  const hasMore = entries.length > limit;
  if (hasMore) entries.shift();
  return { messages: entries.map(row => row.message), before: entries[0]?.sequence ?? 0, hasMore };
}

export async function readAround(database, id, messageId, limit) {
  const half = Math.floor(limit / 2);
  const match = await requestValue(database.transaction("messages").objectStore("messages").get([id, messageId]));
  if (!match) throw new Error("The matching message is no longer available.");
  const previous = await rows(database, id, { before: match.sequence, limit: half + 1 });
  const hasMore = previous.length > half;
  if (hasMore) previous.shift();
  const remaining = limit - previous.length - 1;
  const following = await rows(database, id, { after: match.sequence, limit: remaining + 1, direction: "next" });
  const hasNewer = following.length > remaining;
  if (hasNewer) following.pop();
  const entries = [...previous, match, ...following];
  return { messages: entries.map(row => row.message), before: entries[0].sequence, after: entries.at(-1).sequence, hasMore, hasNewer };
}

export async function readNewer(database, id, after, limit) {
  const entries = await rows(database, id, { after, limit: limit + 1, direction: "next" });
  const hasNewer = entries.length > limit;
  if (hasNewer) entries.pop();
  return { messages: entries.map(row => row.message), after: entries.at(-1)?.sequence ?? after, hasNewer };
}

// Reads one lane of the `lane` index, newest first, returned oldest first.
// `keys` reads only thought positions without their bodies.
function scan(database, id, lane, after, before, { keys = false, limit = Infinity } = {}) {
  const index = database.transaction("messages").objectStore("messages").index("lane");
  const range = IDBKeyRange.bound([id, lane, after], [id, lane, before], true, true);
  return new Promise((resolve, reject) => {
    const result = [];
    const request = keys ? index.openKeyCursor(range, "prev") : index.openCursor(range, "prev");
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor || result.length >= limit) { resolve(result.reverse()); return; }
      result.push(keys ? { sequence: cursor.key[2], id: cursor.primaryKey[1] } : cursor.value);
      cursor.continue();
    };
  });
}

// A page holds `limit` conversation messages plus the hidden context and
// thoughts between them. Thoughts before a message are a placeholder anchored
// to it; only thoughts after the last message (an unfinished turn) load fully.
export async function readPage(database, id, before, limit) {
  const visible = await scan(database, id, 1, 0, before, { limit: limit + 1 });
  const hasMore = visible.length > limit;
  const after = hasMore ? visible.shift().sequence : 0;
  const [hidden, positions] = await Promise.all([
    scan(database, id, 2, after, before),
    scan(database, id, 0, after, before, { keys: true }),
  ]);
  const thoughts = [];
  let next = 0;
  for (const position of positions) {
    while (next < visible.length && visible[next].sequence < position.sequence) next += 1;
    const anchorId = visible[next]?.message.id ?? null;
    if (thoughts.at(-1)?.anchorId === anchorId) thoughts.at(-1).ids.push(position.id);
    else thoughts.push({ id: position.id, anchorId, ids: [position.id] });
  }
  const unfinished = thoughts.at(-1)?.anchorId === null
    ? (thoughts.pop(), await scan(database, id, 0, visible.at(-1)?.sequence ?? after, before)) : [];
  const messages = [...visible, ...hidden, ...unfinished].sort((a, b) => a.sequence - b.sequence).map(row => row.message);
  return { messages, before: hasMore ? after + 1 : 0, hasMore, thoughts };
}

export async function readMessages(database, id, ids) {
  const store = database.transaction("messages").objectStore("messages");
  const rows = await Promise.all(ids.map(messageId => requestValue(store.get([id, messageId]))));
  return rows.filter(Boolean).map(row => row.message);
}

// Visits each row that may contain `needle`, using its first trigram.
export function searchRows(database, needle, visit) {
  const store = database.transaction("messages").objectStore("messages");
  const candidates = needle.length >= 3 ? store.index("search").openCursor(IDBKeyRange.only(needle.slice(0, 3))) : store.openCursor();
  return new Promise((resolve, reject) => {
    candidates.onerror = () => reject(candidates.error);
    candidates.onsuccess = () => { const cursor = candidates.result; if (!cursor) return resolve(); visit(cursor.value); cursor.continue(); };
  });
}

// The checkpoint's messages plus everything after its boundary, including
// unsaved messages from `current`. Null when the boundary is gone.
export async function readCheckpoint(database, id, context, current) {
  const boundary = await requestValue(database.transaction("messages").objectStore("messages").get([id, context.throughId]));
  if (!boundary) return null;
  const following = (await rows(database, id, { after: boundary.sequence })).map(row => row.message);
  const merged = new Map(following.map(message => [message.id, message]));
  const currentBoundary = current.findIndex(message => message.id === context.throughId);
  const unsaved = currentBoundary >= 0 ? current.slice(currentBoundary + 1) : current;
  for (const message of unsaved) if (!merged.has(message.id)) {
    const saved = await requestValue(database.transaction("messages").objectStore("messages").get([id, message.id]));
    if (!saved || saved.sequence > boundary.sequence) merged.set(message.id, message);
  }
  return [...context.messages, ...merged.values()];
}
