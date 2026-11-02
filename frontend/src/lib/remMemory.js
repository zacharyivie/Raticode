// What Rem remembers. Loaded on demand by the memory panel and reply notes,
// so it stays out of the main bundle. Each call returns { memories, mode },
// where mode is "on", "paused" or "off".
import { refreshRemHome, request } from "./remHomeStore.js";

const memoryList = payload => ({ memories: payload.memories || [], mode: payload.mode || "on" });
const memoryPath = id => `/memories/${encodeURIComponent(id)}`;

export async function loadRemMemories() { return memoryList(await request("/memories")); }
export async function addRemMemory(text) { return memoryList(await request("/memories", { method: "POST", body: { text } })); }
export async function saveRemMemory(id, text) { return memoryList(await request(memoryPath(id), { method: "PATCH", body: { text } })); }
export async function forgetRemMemory(id) { return memoryList(await request(memoryPath(id), { method: "DELETE" })); }
export async function setRemMemoryMode(mode) { return memoryList(await request("/memory-mode", { method: "PUT", body: { mode } })); }

// Reverses what one reply saved, changed or forgot, then shows the reply's note as undone.
export async function undoRemMemory(messageId) {
  const result = memoryList(await request(`/messages/${encodeURIComponent(messageId)}/undo-memory`, { method: "POST" }));
  await refreshRemHome();
  return result;
}
