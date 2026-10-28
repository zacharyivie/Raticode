// Browsing history, bookmarks, per-site settings and the downloads list.
// Small JSON files in the app profile, written atomically and readable only
// by the current user. Each list is capped so files stay fast to load.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const LIMITS = { history: 10000, bookmarks: 5000, downloads: 200, sites: 5000 };
const DAY_MS = 24 * 60 * 60 * 1000;

function createBrowserStore(directory, { now = () => Date.now(), writeDelayMs = 400 } = {}) {
  const files = {
    history: path.join(directory, "history.json"),
    bookmarks: path.join(directory, "bookmarks.json"),
    sites: path.join(directory, "site-settings.json"),
    downloads: path.join(directory, "downloads.json"),
  };
  const data = {
    history: readJson(files.history, []),
    bookmarks: readJson(files.bookmarks, []),
    sites: readJson(files.sites, {}),
    downloads: readJson(files.downloads, []),
  };
  if (!Array.isArray(data.history)) data.history = [];
  if (!Array.isArray(data.bookmarks)) data.bookmarks = [];
  if (!data.sites || typeof data.sites !== "object" || Array.isArray(data.sites)) data.sites = {};
  if (!Array.isArray(data.downloads)) data.downloads = [];
  // Downloads cannot resume across restarts; report them as interrupted.
  data.downloads = data.downloads.map((item) => (
    item?.state === "progressing" ? { ...item, state: "interrupted", paused: false } : item
  )).filter((item) => item && typeof item.id === "string");
  const timers = {};

  function schedule(kind) {
    if (timers[kind]) return;
    timers[kind] = setTimeout(() => {
      timers[kind] = null;
      writeJson(files[kind], data[kind]);
    }, writeDelayMs);
    timers[kind].unref?.();
  }

  function flush() {
    for (const kind of Object.keys(timers)) {
      if (!timers[kind]) continue;
      clearTimeout(timers[kind]);
      timers[kind] = null;
      writeJson(files[kind], data[kind]);
    }
  }

  // History keeps one row per URL with visit counts, like Chromium's urls table.
  function recordVisit({ url, title = "", typed = false } = {}) {
    const key = historyKey(url);
    if (!key) return null;
    const time = now();
    let entry = data.history.find((item) => item.url === key);
    if (entry) {
      entry.visitCount += 1;
      entry.lastVisit = time;
      if (typed) entry.typedCount = (entry.typedCount || 0) + 1;
      if (title) entry.title = clip(title, 300);
      data.history.splice(data.history.indexOf(entry), 1);
    } else {
      entry = { url: key, title: clip(title, 300), visitCount: 1, typedCount: typed ? 1 : 0, lastVisit: time };
    }
    data.history.unshift(entry);
    if (data.history.length > LIMITS.history) data.history.length = LIMITS.history;
    schedule("history");
    return entry;
  }

  function updateTitle(url, title) {
    const key = historyKey(url);
    const entry = key && data.history.find((item) => item.url === key);
    if (!entry || !title || entry.title === title) return;
    entry.title = clip(title, 300);
    schedule("history");
  }

  function searchHistory(text = "", limit = 100) {
    const terms = searchTerms(text);
    return data.history
      .filter((item) => terms.every((term) => `${item.url} ${item.title}`.toLowerCase().includes(term)))
      .slice(0, clampLimit(limit));
  }

  function removeHistory(url) {
    const key = historyKey(url) || String(url ?? "");
    const before = data.history.length;
    data.history = data.history.filter((item) => item.url !== key);
    if (data.history.length !== before) schedule("history");
  }

  // range: "hour", "day", "week", "month" or "all".
  function clearHistory(range = "all") {
    const since = rangeStart(range, now());
    data.history = since === 0 ? [] : data.history.filter((item) => item.lastVisit < since);
    schedule("history");
  }

  // Address bar suggestions ranked by frecency, plus inline completion.
  function suggest(text = "", limit = 8) {
    const query = String(text ?? "").trim().toLowerCase();
    if (!query) return [];
    const terms = searchTerms(query);
    const time = now();
    const score = (item) => {
      const ageDays = Math.max(0, (time - (item.lastVisit || item.createdAt || 0)) / DAY_MS);
      const recency = ageDays < 4 ? 100 : ageDays < 14 ? 70 : ageDays < 31 ? 50 : ageDays < 90 ? 30 : 10;
      const visits = Math.min(item.visitCount || 1, 50);
      const typed = (item.typedCount || 0) * 20;
      const prefix = displayHost(item.url).startsWith(query) ? 1000 : 0;
      return prefix + typed + visits * recency + (item.bookmark ? 500 : 0);
    };
    const matches = new Map();
    for (const item of data.bookmarks) {
      if (terms.every((term) => `${item.url} ${item.title}`.toLowerCase().includes(term))) {
        matches.set(item.url, { url: item.url, title: item.title, bookmark: true, createdAt: item.createdAt });
      }
    }
    for (const item of data.history) {
      if (!terms.every((term) => `${item.url} ${item.title}`.toLowerCase().includes(term))) continue;
      matches.set(item.url, { ...item, bookmark: matches.has(item.url) });
    }
    return [...matches.values()]
      .map((item) => ({ ...item, score: score(item) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, clampLimit(limit, 20))
      .map(({ url, title, bookmark }) => ({ url, title: title || "", bookmark: Boolean(bookmark), kind: bookmark ? "bookmark" : "history" }));
  }

  // The host a typed prefix should complete to, e.g. "gi" -> "github.com/".
  function inlineCompletion(text = "") {
    const query = String(text ?? "").trim().toLowerCase();
    if (!query || /\s/.test(query) || query.includes("/")) return "";
    const candidates = [...data.history].sort((a, b) => ((b.typedCount || 0) * 5 + b.visitCount) - ((a.typedCount || 0) * 5 + a.visitCount));
    for (const item of candidates) {
      const host = displayHost(item.url);
      if (host.startsWith(query) && host.length > query.length) return `${host}/`;
    }
    return "";
  }

  function listBookmarks(text = "") {
    const terms = searchTerms(text);
    return data.bookmarks.filter((item) => terms.every((term) => `${item.url} ${item.title}`.toLowerCase().includes(term)));
  }

  function bookmarkFor(url) {
    const key = historyKey(url);
    return (key && data.bookmarks.find((item) => item.url === key)) || null;
  }

  function addBookmark({ url, title = "" } = {}) {
    const key = historyKey(url);
    if (!key) throw new Error("Only web pages can be bookmarked.");
    const existing = bookmarkFor(key);
    if (existing) return existing;
    const bookmark = { id: crypto.randomUUID(), url: key, title: clip(title || key, 300), createdAt: now() };
    data.bookmarks.unshift(bookmark);
    if (data.bookmarks.length > LIMITS.bookmarks) data.bookmarks.length = LIMITS.bookmarks;
    schedule("bookmarks");
    return bookmark;
  }

  function updateBookmark(id, { title, url } = {}) {
    const bookmark = data.bookmarks.find((item) => item.id === id);
    if (!bookmark) throw new Error("That bookmark no longer exists.");
    if (typeof title === "string") bookmark.title = clip(title.trim() || bookmark.url, 300);
    if (typeof url === "string") {
      const key = historyKey(url);
      if (!key) throw new Error("Enter an http:// or https:// address.");
      bookmark.url = key;
    }
    schedule("bookmarks");
    return bookmark;
  }

  function removeBookmark(id) {
    data.bookmarks = data.bookmarks.filter((item) => item.id !== id);
    schedule("bookmarks");
  }

  function site(origin) {
    return data.sites[origin] || {};
  }

  function updateSite(origin, update) {
    if (!origin) return {};
    const next = { ...site(origin), ...update };
    delete next.updatedAt;
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === undefined) delete next[key];
    }
    if (next.permissions && !Object.keys(next.permissions).length) delete next.permissions;
    if (Object.keys(next).length) data.sites[origin] = { ...next, updatedAt: now() };
    else delete data.sites[origin];
    const origins = Object.keys(data.sites);
    if (origins.length > LIMITS.sites) {
      origins.sort((a, b) => (data.sites[a].updatedAt || 0) - (data.sites[b].updatedAt || 0));
      for (const stale of origins.slice(0, origins.length - LIMITS.sites)) delete data.sites[stale];
    }
    schedule("sites");
    return data.sites[origin] || {};
  }

  function setPermission(origin, key, value) {
    const permissions = { ...(site(origin).permissions || {}) };
    if (value === "allow" || value === "block") permissions[key] = value;
    else delete permissions[key];
    return updateSite(origin, { permissions });
  }

  function listSites() {
    return Object.entries(data.sites).map(([origin, value]) => ({ origin, ...value }));
  }

  function clearSite(origin) {
    if (!data.sites[origin]) return;
    delete data.sites[origin];
    schedule("sites");
  }

  function clearSites() {
    data.sites = {};
    schedule("sites");
  }

  function listDownloads() {
    return data.downloads;
  }

  function upsertDownload(entry) {
    const index = data.downloads.findIndex((item) => item.id === entry.id);
    if (index >= 0) data.downloads[index] = { ...data.downloads[index], ...entry };
    else data.downloads.unshift(entry);
    if (data.downloads.length > LIMITS.downloads) data.downloads.length = LIMITS.downloads;
    schedule("downloads");
    return data.downloads[index >= 0 ? index : 0];
  }

  function removeDownload(id) {
    data.downloads = data.downloads.filter((item) => item.id !== id);
    schedule("downloads");
  }

  function clearDownloads() {
    data.downloads = data.downloads.filter((item) => item.state === "progressing" || item.state === "quarantined");
    schedule("downloads");
  }

  return {
    addBookmark,
    bookmarkFor,
    clearDownloads,
    clearHistory,
    clearSite,
    clearSites,
    flush,
    inlineCompletion,
    listBookmarks,
    listDownloads,
    listSites,
    recordVisit,
    removeBookmark,
    removeDownload,
    removeHistory,
    searchHistory,
    setPermission,
    site,
    suggest,
    updateBookmark,
    updateSite,
    updateTitle,
    upsertDownload,
  };
}

function historyKey(value) {
  try {
    const url = new URL(String(value ?? ""));
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    url.hash = "";
    return url.toString();
  } catch {
    return "";
  }
}

function displayHost(value) {
  try {
    return new URL(value).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
}

function searchTerms(text) {
  return String(text ?? "").toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
}

function rangeStart(range, time) {
  return { hour: time - 60 * 60 * 1000, day: time - DAY_MS, week: time - 7 * DAY_MS, month: time - 28 * DAY_MS }[range] ?? 0;
}

function clampLimit(value, maximum = 500) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(Math.max(Math.trunc(number), 1), maximum) : 100;
}

function clip(value, length) {
  return String(value ?? "").slice(0, length);
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(file, value) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const temporary = `${file}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value), { mode: 0o600 });
    fs.renameSync(temporary, file);
  } catch {
    // A full disk or locked profile must not break browsing; the next write retries.
  }
}

module.exports = { createBrowserStore, displayHost, historyKey };
