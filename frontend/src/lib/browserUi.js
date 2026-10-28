// Pure helpers for the integrated browser's chrome. Kept free of React so
// the address bar, downloads and Rem indicators are easy to test.

const MAX_SAVED_ENTRIES = 25;
const MAX_SAVED_PAGE_STATE = 16 * 1024;
const MAX_SAVED_HISTORY_CHARS = 200 * 1024;

export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

export function formatZoom(factor) {
  const value = Number(factor);
  return Number.isFinite(value) && value > 0 ? `${Math.round(value * 100)}%` : "100%";
}

export function downloadStatus(entry = {}) {
  const received = Number(entry.receivedBytes) || 0;
  const total = Number(entry.totalBytes) || 0;
  const percent = total > 0 ? Math.min(100, Math.round((received / total) * 100)) : null;
  switch (entry.state) {
    case "progressing":
      if (entry.paused) return { percent, label: `Paused · ${formatBytes(received)}${total ? ` of ${formatBytes(total)}` : ""}` };
      return { percent, label: total ? `${formatBytes(received)} of ${formatBytes(total)}` : `${formatBytes(received)} downloaded` };
    case "completed":
      return { percent: 100, label: formatBytes(total || received) || "Done" };
    case "quarantined":
      return { percent: 100, label: "This type of file can harm your computer. Keep it?" };
    case "cancelled":
      return { percent: null, label: "Cancelled" };
    case "interrupted":
      return { percent: null, label: "Failed — interrupted" };
    default:
      return { percent: null, label: "" };
  }
}

// Chrome-style inline completion: the typed prefix stays, the rest is selected.
export function addressWithCompletion(typed, completion) {
  const text = String(typed ?? "");
  const full = String(completion ?? "");
  if (!text || !full || full.length <= text.length || !full.toLowerCase().startsWith(text.toLowerCase())) {
    return { value: text, selectionStart: text.length, selectionEnd: text.length };
  }
  const value = text + full.slice(text.length);
  return { value, selectionStart: text.length, selectionEnd: value.length };
}

function looksLikeAddress(input) {
  return /^(?:file\/\/|\/|\\\\)/i.test(input)
    || /^[a-z][a-z\d+.-]*:/i.test(input)
    || /^(?:localhost|127\.0\.0\.1|\[?::1\]?)(?::\d+)?(?:[/?#]|$)/i.test(input)
    || /^[\w.-]+\.[a-z]{2,}(?::\d+)?(?:[/?#]|$)/i.test(input);
}

// The address bar menu: what Enter does first, then open tabs, then
// bookmarks and history from the desktop store.
export function suggestionRows({ text, suggestions = [], openTabs = [], currentPath = "", limit = 8 }) {
  const input = String(text ?? "").trim();
  if (!input) return [];
  const rows = [looksLikeAddress(input)
    ? { kind: "navigate", value: input, label: input }
    : { kind: "search", value: input, label: input }];
  const seen = new Set();
  const lower = input.toLowerCase();
  for (const tab of openTabs) {
    if (!tab?.url || tab.path === currentPath || !/^https?:/i.test(tab.url)) continue;
    if (!`${tab.url} ${tab.title || ""}`.toLowerCase().includes(lower)) continue;
    seen.add(tab.url);
    rows.push({ kind: "tab", value: tab.url, label: tab.title || tab.url, url: tab.url, path: tab.path });
    if (rows.length >= 3) break;
  }
  for (const item of suggestions) {
    if (!item?.url || seen.has(item.url)) continue;
    seen.add(item.url);
    rows.push({ kind: item.bookmark ? "bookmark" : "history", value: item.url, label: item.title || item.url, url: item.url });
    if (rows.length >= limit) break;
  }
  return rows;
}

// Rem's approval requests waiting in tabs, for the tab strip and banner.
export function pendingRemApprovals(states = {}) {
  return Object.entries(states).flatMap(([path, state]) => (state?.prompts || [])
    .filter((prompt) => prompt?.kind === "rem-confirm")
    .map((prompt) => ({ path, requestId: prompt.requestId, label: prompt.label, agentLabel: prompt.agentLabel || "Rem" })));
}

// Saved with the editor session so restored tabs keep back/forward and scroll.
export function trimHistoryForSession(history) {
  if (!history || !Array.isArray(history.entries) || !history.entries.length) return null;
  const index = Math.min(Math.max(Number(history.index) || 0, 0), history.entries.length - 1);
  const start = Math.max(0, Math.min(index - Math.floor(MAX_SAVED_ENTRIES / 2), history.entries.length - MAX_SAVED_ENTRIES));
  const window = history.entries.slice(start, start + MAX_SAVED_ENTRIES).filter((entry) => /^https?:/i.test(String(entry?.url ?? "")));
  if (!window.length) return null;
  let budget = MAX_SAVED_HISTORY_CHARS;
  const entries = window.map((entry) => {
    const base = { url: String(entry.url).slice(0, 8192), title: String(entry.title ?? "").slice(0, 300) };
    budget -= base.url.length + base.title.length;
    const pageState = typeof entry.pageState === "string" && entry.pageState.length <= MAX_SAVED_PAGE_STATE ? entry.pageState : "";
    if (pageState && pageState.length <= budget) {
      budget -= pageState.length;
      return { ...base, pageState };
    }
    return base;
  });
  const active = history.entries[index];
  const savedIndex = entries.findIndex((entry, position) => entry.url === active?.url && position === Math.min(index - start, entries.length - 1));
  return { entries, index: savedIndex >= 0 ? savedIndex : Math.min(Math.max(index - start, 0), entries.length - 1) };
}

// Entries for the long-press menus on Back and Forward, nearest first.
export function historyMenuEntries(history, direction) {
  if (!history || !Array.isArray(history.entries)) return [];
  const index = Number(history.index) || 0;
  const items = history.entries.map((entry, position) => ({ ...entry, index: position }));
  return direction < 0
    ? items.slice(Math.max(0, index - 15), index).reverse()
    : items.slice(index + 1, index + 16);
}

export function hostLabel(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return String(url ?? "");
  }
}

export function relativeTime(timestamp, now = Date.now()) {
  const seconds = Math.max(0, Math.round((now - Number(timestamp || 0)) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
