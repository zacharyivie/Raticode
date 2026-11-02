// Web pages Rem used in one turn, read from its search, fetch and browser steps.
// The answer's own links come first, then pages Rem read, then search results.

const SEARCH_LINK = /\{"title":"((?:[^"\\]|\\.)*)","url":"((?:[^"\\]|\\.)*)"/g;
const BARE_URL = /https?:\/\/[^\s"'<>`)\]\\]+/g;
const MARKDOWN_LINK = /\[([^\]]{1,300})\]\((https?:\/\/[^\s)]+)\)/g;
const WEB_TOOL = /search|fetch|browser_(?:navigate|open)|web/i;
const RANK = { cited: 0, read: 1, found: 2 };

export function collectTurnSources(thoughts, finalBody = "", limit = 12) {
  const found = new Map();
  const add = (url, title, kind) => {
    const clean = publicUrl(url);
    if (!clean) return;
    const key = sourceKey(clean);
    const current = found.get(key);
    if (!current) {
      found.set(key, { url: clean, title: cleanTitle(title), kind, order: found.size });
      return;
    }
    if (!current.title && title) current.title = cleanTitle(title);
    if (RANK[kind] < RANK[current.kind]) current.kind = kind;
  };
  let usedWeb = false;
  for (const thought of thoughts || []) {
    const trace = thought?.trace;
    if (trace?.kind !== "tool" || !WEB_TOOL.test(String(trace.title || ""))) continue;
    usedWeb = true;
    const input = parseJson(trace.input);
    if (typeof input?.url === "string") add(input.url, "", "read");
    const output = String(trace.output || "");
    for (const match of output.matchAll(SEARCH_LINK)) add(unescapeJson(match[2]), unescapeJson(match[1]), "found");
  }
  if (!usedWeb) return [];
  for (const match of String(finalBody || "").matchAll(MARKDOWN_LINK)) add(match[2], match[1], "cited");
  for (const match of String(finalBody || "").matchAll(BARE_URL)) add(match[0], "", "cited");
  return [...found.values()]
    .sort((a, b) => RANK[a.kind] - RANK[b.kind] || a.order - b.order)
    .slice(0, limit)
    .map(({ url, title, kind }) => ({ url, title, kind }));
}

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;

export function youtubeId(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  const host = parsed.hostname.toLowerCase().replace(/^(?:www|m)\./, "");
  let candidate = "";
  if (host === "youtu.be") candidate = parsed.pathname.split("/")[1] || "";
  else if (["youtube.com", "music.youtube.com", "youtube-nocookie.com"].includes(host)) {
    if (parsed.pathname === "/watch") candidate = parsed.searchParams.get("v") || "";
    else {
      const [kind, id] = parsed.pathname.split("/").filter(Boolean);
      if (["embed", "shorts", "live", "v"].includes(kind)) candidate = id || "";
    }
  }
  return YOUTUBE_ID.test(candidate) ? candidate : null;
}

// YouTube links in the reply and its sources, once each.
export function videoLinks(body, sources = []) {
  const ids = new Map();
  const urls = [...String(body || "").matchAll(BARE_URL)].map(match => match[0]);
  for (const url of [...urls, ...sources.map(source => source.url)]) {
    const id = youtubeId(trimUrl(url));
    if (id && !ids.has(id)) ids.set(id, `https://www.youtube.com/watch?v=${id}`);
  }
  return [...ids.values()].slice(0, 4);
}

export function sourceDomain(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

function publicUrl(value) {
  let parsed;
  try { parsed = new URL(trimUrl(String(value || ""))); } catch { return ""; }
  if (!["http:", "https:"].includes(parsed.protocol)) return "";
  const host = parsed.hostname.toLowerCase();
  if (host === "localhost" || host.endsWith(".local") || /^(?:127\.|10\.|192\.168\.|0\.|\[)/.test(host)) return "";
  return parsed.href;
}

function trimUrl(url) {
  return url.replace(/[.,;:!?'"]+$/, "");
}

function sourceKey(url) {
  const parsed = new URL(url);
  parsed.hash = "";
  return parsed.href.replace(/\/$/, "").replace(/^http:/, "https:");
}

function cleanTitle(title) {
  return String(title || "").replace(/\s+/g, " ").trim().slice(0, 200);
}

function parseJson(value) {
  try { return JSON.parse(String(value || "")); } catch { return null; }
}

function unescapeJson(value) {
  try { return JSON.parse(`"${value}"`); } catch { return value; }
}
