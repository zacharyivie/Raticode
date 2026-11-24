// What Rem remembers. Loaded on demand by the memory panel and reply notes,
// so it stays out of the main bundle. Each call returns { memories, mode, projects },
// where mode is "on", "paused" or "off" and projects lists { root, name, open? } for
// the project filter. A memory with a project root applies only to work in it.
import { refreshRemHome, request } from "./remHomeStore.js";

const memoryList = payload => ({ memories: payload.memories || [], mode: payload.mode || "on", projects: payload.projects || [] });
const memoryPath = id => `/memories/${encodeURIComponent(id)}`;

export async function loadRemMemories() { return memoryList(await request("/memories")); }
// project: a project root, or "" / null for every project.
export async function addRemMemory(text, project = "") { return memoryList(await request("/memories", { method: "POST", body: project ? { text, project } : { text } })); }
// changes: { text?, project?, until? }, where project null makes the memory apply to every
// project, until "YYYY-MM-DD" is the last day Rem uses it and until null removes the end (Keep).
export async function saveRemMemory(id, changes) { return memoryList(await request(memoryPath(id), { method: "PATCH", body: changes })); }
export async function forgetRemMemory(id) { return memoryList(await request(memoryPath(id), { method: "DELETE" })); }
export async function setRemMemoryMode(mode) { return memoryList(await request("/memory-mode", { method: "PUT", body: { mode } })); }

// Reverses what one reply saved, changed or forgot, then shows the reply's note as undone.
export async function undoRemMemory(messageId) {
  const result = memoryList(await request(`/messages/${encodeURIComponent(messageId)}/undo-memory`, { method: "POST" }));
  await refreshRemHome();
  return result;
}
