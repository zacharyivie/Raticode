// Firefox Add-ons (addons.mozilla.org): search, details and downloads for
// the extensions the user installs into Raticode's Firefox. Files come only
// from Mozilla's servers and must match the hash Mozilla publishes. Firefox
// then checks Mozilla's signature itself when it installs them.
const crypto = require("node:crypto");
const fs = require("node:fs");

const API = "https://addons.mozilla.org/api/v5/addons";
const DOWNLOAD_HOSTS = new Set(["addons.mozilla.org", "addons.cdn.mozilla.net"]);
const MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 30000;
const AMO_ADDON_PAGE = /^https:\/\/addons\.mozilla\.org\/(?:[A-Za-z-]+\/)?firefox\/addon\/([^/?#]+)/;

function text(value, limit = 300) {
  if (value && typeof value === "object") value = value["en-US"] || Object.values(value)[0];
  return String(value ?? "").replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function summarize(addon) {
  return {
    guid: text(addon.guid, 200),
    slug: text(addon.slug, 200),
    name: text(addon.name, 120),
    summary: text(addon.summary, 300),
    users: Number(addon.average_daily_users) || 0,
    rating: Math.round((Number(addon.ratings?.average) || 0) * 10) / 10,
    recommended: Array.isArray(addon.promoted) ? addon.promoted.some((item) => item?.category === "recommended") : addon.promoted?.category === "recommended",
    url: typeof addon.url === "string" && addon.url.startsWith("https://addons.mozilla.org/") ? addon.url : "",
  };
}

// The extension's address on addons.mozilla.org, if this is one.
function addonSlugFromUrl(url) {
  const match = AMO_ADDON_PAGE.exec(String(url || ""));
  return match ? decodeURIComponent(match[1]).slice(0, 200) : "";
}

// Whether Firefox `version` falls within an add-on's min and max.
function versionCompatible(version, range = {}) {
  const parts = (value) => String(value || "0").split(".").map((part) => parseInt(part, 10) || 0);
  const compare = (a, b) => {
    const left = parts(a);
    const right = parts(b);
    for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
      if ((left[index] || 0) !== (right[index] || 0)) return (left[index] || 0) - (right[index] || 0);
    }
    return 0;
  };
  if (!version) return true;
  if (range.min && compare(version, range.min) < 0) return false;
  if (range.max && range.max !== "*" && compare(version.split(".")[0], String(range.max).split(".")[0]) > 0) return false;
  return true;
}

function createAddonCatalog({ fetchImpl = globalThis.fetch, language = "en-US" } = {}) {
  async function getJson(url) {
    const response = await fetchImpl(url, { headers: { Accept: "application/json" }, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    if (response.status === 404) throw new Error("Firefox Add-ons has no extension at that address.");
    if (!response.ok) throw new Error(`Firefox Add-ons answered ${response.status}.`);
    return response.json();
  }

  async function search(query) {
    const q = String(query || "").trim().slice(0, 200);
    if (!q) return [];
    const url = `${API}/search/?${new URLSearchParams({ q, app: "firefox", type: "extension", sort: "relevance", page_size: "20", lang: language })}`;
    const data = await getJson(url);
    return (Array.isArray(data.results) ? data.results : []).map(summarize).filter((item) => item.guid && item.slug);
  }

  // Full details of one extension, by its addons.mozilla.org slug or ID.
  async function details(key) {
    const id = String(key || "").trim();
    if (!id || id.length > 200 || /[/?#\\]/.test(id)) throw new Error("Choose an extension from Firefox Add-ons.");
    const addon = await getJson(`${API}/addon/${encodeURIComponent(id)}/?lang=${encodeURIComponent(language)}`);
    if (addon.type !== "extension") throw new Error("Raticode installs extensions only, not themes, dictionaries or language packs.");
    const version = addon.current_version || {};
    const file = version.file || {};
    if (!file.url || !/^sha256:[0-9a-f]{64}$/.test(String(file.hash || ""))) throw new Error("Firefox Add-ons did not offer a file to install.");
    const strings = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string").map((item) => item.slice(0, 200)) : []);
    return {
      ...summarize(addon),
      version: text(version.version, 50),
      authors: (Array.isArray(addon.authors) ? addon.authors : []).map((author) => text(author?.name, 80)).filter(Boolean).slice(0, 5),
      compatibility: { min: text(version.compatibility?.firefox?.min, 30), max: text(version.compatibility?.firefox?.max, 30) },
      permissions: [...new Set([...strings(file.permissions), ...strings(file.host_permissions)])].slice(0, 100),
      file: { url: String(file.url), hash: String(file.hash).slice(7), size: Number(file.size) || 0 },
    };
  }

  // Downloads the package to `destination`, refusing other hosts, oversize
  // files and any file whose SHA-256 differs from Mozilla's.
  async function download(file, destination) {
    const start = new URL(file.url);
    if (start.protocol !== "https:" || !DOWNLOAD_HOSTS.has(start.hostname)) throw new Error("Raticode downloads extensions only from Firefox Add-ons.");
    const response = await fetchImpl(start.toString(), { redirect: "follow", signal: AbortSignal.timeout(5 * 60 * 1000) });
    const landed = new URL(response.url || start.toString());
    if (landed.protocol !== "https:" || !DOWNLOAD_HOSTS.has(landed.hostname)) throw new Error("The download moved away from Firefox Add-ons.");
    if (!response.ok || !response.body) throw new Error(`The download failed (${response.status}).`);
    const hash = crypto.createHash("sha256");
    const output = fs.openSync(destination, "w", 0o600);
    let size = 0;
    try {
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > MAX_DOWNLOAD_BYTES) throw new Error("The extension is larger than Raticode accepts.");
        hash.update(chunk);
        fs.writeSync(output, chunk);
      }
    } finally {
      fs.closeSync(output);
    }
    if (hash.digest("hex") !== file.hash) {
      fs.rmSync(destination, { force: true });
      throw new Error("The downloaded file does not match the one Firefox Add-ons lists, so it was not installed.");
    }
    return { size };
  }

  return { details, download, search };
}

module.exports = { DOWNLOAD_HOSTS, addonSlugFromUrl, createAddonCatalog, versionCompatible };
