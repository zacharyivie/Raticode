import { useEffect, useSyncExternalStore } from "react";
import { apiUrl } from "./api.js";

// Raticode-wide skills and MCP servers. Agents add entries with `gof rem`, so
// every open tools menu refreshes on focus and while it stays open.
const EMPTY = Object.freeze({ loaded: false, error: "", skillsDir: "", skills: [], mcpServers: [] });
let state = EMPTY;
let inflight = null;
const listeners = new Set();

function publish(next) {
  state = next;
  for (const listener of listeners) listener();
}

async function request(init) {
  const response = await fetch(apiUrl("/rem-resources"), init);
  let payload = null;
  try { payload = await response.json(); } catch { /* handled below */ }
  if (!response.ok || !payload || !Array.isArray(payload.skills)) {
    throw new Error(payload?.error || "The Raticode tool library is unavailable.");
  }
  return payload;
}

export function refreshRemLibrary() {
  inflight ??= request()
    .then((payload) => publish({ loaded: true, error: "", ...payload }))
    .catch((error) => publish({ ...state, loaded: true, error: error.message }))
    .finally(() => { inflight = null; });
  return inflight;
}

export async function editRemLibrary(body) {
  const payload = await request({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  publish({ loaded: true, error: "", ...payload });
  return payload;
}

export function useRemLibrary({ live = false } = {}) {
  const snapshot = useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, () => state, () => state);
  useEffect(() => {
    void refreshRemLibrary();
    if (!live) return undefined;
    const refresh = () => { if (document.visibilityState !== "hidden") void refreshRemLibrary(); };
    window.addEventListener("focus", refresh);
    const timer = window.setInterval(refresh, 5000);
    return () => { window.removeEventListener("focus", refresh); window.clearInterval(timer); };
  }, [live]);
  return snapshot;
}

export function skillKey(path = "") {
  return String(path).trim().replace(/[\\/]SKILL\.md$/i, "").replace(/[\\/]+$/, "");
}
