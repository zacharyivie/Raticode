import { useEffect, useSyncExternalStore } from "react";
import { apiJson } from "./api.js";

// Raticode-wide skills and MCP servers. Agents add entries with `Raticode rem`, so
// every open tools menu refreshes on focus and while it stays open.
const EMPTY = Object.freeze({ loaded: false, error: "", skillsDir: "", skills: [], mcpServers: [] });
let state = EMPTY;
let inflight = null;
const listeners = new Set();

function publish(next) {
  state = next;
  for (const listener of listeners) listener();
}

const UNAVAILABLE = "The Raticode tool library is unavailable.";

async function request(options = {}) {
  const payload = await apiJson("/rem-resources", { ...options, failure: UNAVAILABLE });
  if (!Array.isArray(payload.skills)) throw new Error(payload.error || UNAVAILABLE);
  return payload;
}

function refreshRemLibrary() {
  inflight ??= request()
    .then((payload) => publish({ loaded: true, error: "", ...payload }))
    .catch((error) => publish({ ...state, loaded: true, error: error.message }))
    .finally(() => { inflight = null; });
  return inflight;
}

export async function editRemLibrary(body) {
  const payload = await request({ method: "POST", body });
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
