// Browser identity for integrated browser tabs.
//
// Electron implements navigator.userAgentData but never sends the matching
// Sec-CH-UA request headers, so sites see a browser whose network identity
// disagrees with what its scripts report. Sign-in providers treat that as an
// embedded web view and refuse to sign in. This module supplies the headers
// Chromium itself sends, with the same values the renderer reports, following
// https://wicg.github.io/ua-client-hints/ and Chromium's user-agent reduction.

const LOW_ENTROPY_HINTS = ["sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"];
// Sent only to sites that ask for them with Accept-CH.
const HIGH_ENTROPY_HINTS = new Set([
  "sec-ch-ua-arch",
  "sec-ch-ua-bitness",
  "sec-ch-ua-form-factors",
  "sec-ch-ua-full-version",
  "sec-ch-ua-full-version-list",
  "sec-ch-ua-model",
  "sec-ch-ua-platform-version",
  "sec-ch-ua-wow64",
]);
const MAX_REMEMBERED_ORIGINS = 2000;

const PLATFORM_NAMES = { darwin: "macOS", linux: "Linux", win32: "Windows" };

// Google's sign-in refuses a Chrome-shaped User-Agent header from an engine
// without Chrome's internals ("This browser or app may not be secure") but
// accepts Electron's own. Sites such as WhatsApp Web and Teams refuse
// Electron's, so only requests to these hosts carry it. Switching the tab's
// navigator.userAgent as well would make Chromium reload mid-navigation, and
// Google accepts the header alone. Sign-in hops through regional
// accounts.google.* and accounts.youtube.com to set cookies.
const EMBEDDER_IDENTITY_HOST = /^accounts\.(?:google\.[a-z]{2,3}(?:\.[a-z]{2})?|youtube\.com)$/i;

function prefersEmbedderIdentity(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && EMBEDDER_IDENTITY_HOST.test(parsed.hostname);
  } catch {
    return false;
  }
}

function majorVersion(version) {
  const major = Number.parseInt(String(version ?? ""), 10);
  return Number.isInteger(major) && major >= 0 ? major : 0;
}

// Chromium's GREASE brand (components/embedder_support/user_agent_utils.cc):
// a placeholder brand and order derived from the major version, so sites
// cannot rely on an exact brand list.
function greasedBrand(seed, fullVersion) {
  const chars = [" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_"];
  const versions = ["8", "99", "24"];
  const version = versions[seed % versions.length];
  return {
    brand: `Not${chars[seed % chars.length]}A${chars[(seed + 1) % chars.length]}Brand`,
    version: fullVersion ? `${version}.0.0.0` : version,
  };
}

function chromiumBrandList(chromeVersion, { fullVersion = false } = {}) {
  const seed = majorVersion(chromeVersion);
  const brands = [
    greasedBrand(seed, fullVersion),
    { brand: "Chromium", version: fullVersion ? String(chromeVersion) : String(seed) },
  ];
  const order = [seed % 2, (seed + 1) % 2];
  const shuffled = [];
  brands.forEach((brand, index) => { shuffled[order[index]] = brand; });
  return shuffled;
}

// Structured-header string (RFC 8941 sf-string).
function sfString(value) {
  return `"${String(value ?? "").replace(/[\\"]/g, (char) => `\\${char}`)}"`;
}

function serializeBrandList(brands) {
  return brands.map(({ brand, version }) => `${sfString(brand)};v=${sfString(version)}`).join(", ");
}

// Chrome has reported only the major version since its user-agent reduction.
function reducedUserAgent(userAgent, chromeVersion) {
  const major = majorVersion(chromeVersion);
  const value = String(userAgent ?? "");
  return major ? value.replace(/Chrome\/[\d.]+/, `Chrome/${major}.0.0.0`) : value;
}

// Matches Chrome's defaults: each regional language is followed by its base
// language, e.g. ["fr-FR", "en-US"] -> ["fr-FR", "fr", "en-US", "en"].
function acceptLanguageList(preferred) {
  const languages = [];
  for (const raw of Array.isArray(preferred) ? preferred : []) {
    const tag = String(raw ?? "").trim().replace(/_/g, "-").split(".")[0];
    if (!/^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$/.test(tag)) continue;
    const base = tag.split("-")[0].toLowerCase();
    const [, ...rest] = tag.split("-");
    const normalized = [base, ...rest.map((part) => (part.length === 2 ? part.toUpperCase() : part))].join("-");
    for (const value of [normalized, base]) if (!languages.includes(value)) languages.push(value);
    if (languages.length >= 12) break;
  }
  return languages.length ? languages : ["en-US", "en"];
}

function isPotentiallyTrustworthy(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return false; }
  if (parsed.protocol === "https:" || parsed.protocol === "wss:") return true;
  if (parsed.protocol !== "http:" && parsed.protocol !== "ws:") return false;
  const host = parsed.hostname.replace(/^\[|\]$/g, "");
  return host === "localhost" || host.endsWith(".localhost") || host === "::1" || /^127(\.\d{1,3}){3}$/.test(host);
}

function originOf(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin === "null" ? "" : parsed.origin;
  } catch {
    return "";
  }
}

function headerValues(headers, name) {
  const values = [];
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() !== name) continue;
    values.push(...(Array.isArray(value) ? value : [value]).map(String));
  }
  return values;
}

function parseHintList(values) {
  return [...new Set(values.join(",").split(",").map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

function sanitizeBrands(value) {
  if (!Array.isArray(value) || !value.length || value.length > 8) return null;
  const brands = value.map((item) => ({ brand: String(item?.brand ?? "").slice(0, 64), version: String(item?.version ?? "").slice(0, 32) }));
  return brands.every((item) => item.brand && item.version) ? brands : null;
}

// The values navigator.userAgentData reports. Electron computes some of them
// differently from Chrome (platformVersion is empty on Linux), so the session
// samples the renderer's values and uses these only until then.
function defaultMetadata({ chromeVersion, platform = process.platform, arch = process.arch, platformVersion = "" }) {
  return {
    brands: chromiumBrandList(chromeVersion),
    fullVersionList: chromiumBrandList(chromeVersion, { fullVersion: true }),
    fullVersion: String(chromeVersion ?? ""),
    mobile: false,
    platform: PLATFORM_NAMES[platform] || "Unknown",
    platformVersion: String(platformVersion ?? ""),
    architecture: arch === "arm64" || arch === "arm" ? "arm" : "x86",
    bitness: arch === "arm" || arch === "ia32" ? "32" : "64",
    model: "",
    wow64: false,
    formFactors: ["Desktop"],
  };
}

function mergeMetadata(base, sampled) {
  if (!sampled || typeof sampled !== "object") return base;
  const text = (value, fallback) => (typeof value === "string" && value.length <= 64 ? value : fallback);
  return {
    ...base,
    brands: sanitizeBrands(sampled.brands) || base.brands,
    fullVersionList: sanitizeBrands(sampled.fullVersionList) || base.fullVersionList,
    fullVersion: text(sampled.uaFullVersion, base.fullVersion),
    mobile: typeof sampled.mobile === "boolean" ? sampled.mobile : base.mobile,
    platform: text(sampled.platform, base.platform) || base.platform,
    platformVersion: text(sampled.platformVersion, base.platformVersion),
    architecture: text(sampled.architecture, base.architecture),
    bitness: text(sampled.bitness, base.bitness),
    model: text(sampled.model, base.model),
    wow64: typeof sampled.wow64 === "boolean" ? sampled.wow64 : base.wow64,
    formFactors: Array.isArray(sampled.formFactors) && sampled.formFactors.length <= 8
      ? sampled.formFactors.map((item) => String(item).slice(0, 32))
      : base.formFactors,
  };
}

function hintValue(name, metadata) {
  switch (name) {
    case "sec-ch-ua": return serializeBrandList(metadata.brands);
    case "sec-ch-ua-mobile": return metadata.mobile ? "?1" : "?0";
    case "sec-ch-ua-platform": return sfString(metadata.platform);
    case "sec-ch-ua-arch": return sfString(metadata.architecture);
    case "sec-ch-ua-bitness": return sfString(metadata.bitness);
    case "sec-ch-ua-form-factors": return metadata.formFactors.map(sfString).join(", ");
    case "sec-ch-ua-full-version": return sfString(metadata.fullVersion);
    case "sec-ch-ua-full-version-list": return serializeBrandList(metadata.fullVersionList);
    case "sec-ch-ua-model": return sfString(metadata.model);
    case "sec-ch-ua-platform-version": return sfString(metadata.platformVersion);
    case "sec-ch-ua-wow64": return metadata.wow64 ? "?1" : "?0";
    default: return null;
  }
}

// Per-profile client hint state: Accept-CH opt-ins by origin, as Chromium
// keeps them, and the header decisions for each request and response.
function createClientHints(initialMetadata, { embedderUserAgent = "" } = {}) {
  let metadata = initialMetadata;
  const acceptCh = new Map();

  function remember(origin, hints) {
    acceptCh.delete(origin);
    acceptCh.set(origin, hints);
    while (acceptCh.size > MAX_REMEMBERED_ORIGINS) acceptCh.delete(acceptCh.keys().next().value);
  }

  // Low-entropy hints go to every potentially trustworthy origin. High-entropy
  // hints go only to the origin that asked for them, and only while it is the
  // top-level site; Chromium needs Permissions-Policy delegation for more.
  function requestHeaders({ url, firstPartyUrl, headers }) {
    if (!isPotentiallyTrustworthy(url)) return headers;
    const next = {};
    for (const [key, value] of Object.entries(headers || {})) {
      if (!key.toLowerCase().startsWith("sec-ch-ua")) next[key] = value;
    }
    for (const name of LOW_ENTROPY_HINTS) next[name] = hintValue(name, metadata);
    if (embedderUserAgent && prefersEmbedderIdentity(url)) {
      for (const key of Object.keys(next)) if (key.toLowerCase() === "user-agent") delete next[key];
      next["User-Agent"] = embedderUserAgent;
    }
    const origin = originOf(url);
    if (origin && origin === originOf(firstPartyUrl)) {
      for (const name of acceptCh.get(origin) || []) {
        const value = HIGH_ENTROPY_HINTS.has(name) ? hintValue(name, metadata) : null;
        if (value !== null) next[name] = value;
      }
    }
    return next;
  }

  // Records Accept-CH from top-level documents. Returns true when Critical-CH
  // names a hint this request lacked, so the caller restarts it once with the
  // hint, as Chromium does.
  function responseNeedsRestart({ url, method = "GET", resourceType, responseHeaders }) {
    if (resourceType !== "mainFrame" || !isPotentiallyTrustworthy(url)) return false;
    const origin = originOf(url);
    if (!origin) return false;
    const accept = headerValues(responseHeaders, "accept-ch");
    if (!accept.length) return false;
    const previous = acceptCh.get(origin) || new Set();
    const requested = new Set(parseHintList(accept).filter((name) => HIGH_ENTROPY_HINTS.has(name)));
    remember(origin, requested);
    if (String(method).toUpperCase() !== "GET") return false;
    const critical = parseHintList(headerValues(responseHeaders, "critical-ch"));
    return critical.some((name) => requested.has(name) && !previous.has(name));
  }

  return {
    requestHeaders,
    responseNeedsRestart,
    setMetadata(value) { metadata = value; },
    metadata: () => metadata,
    clear(origin) { if (origin) acceptCh.delete(origin); else acceptCh.clear(); },
  };
}

module.exports = {
  HIGH_ENTROPY_HINTS,
  LOW_ENTROPY_HINTS,
  acceptLanguageList,
  chromiumBrandList,
  createClientHints,
  defaultMetadata,
  isPotentiallyTrustworthy,
  mergeMetadata,
  prefersEmbedderIdentity,
  reducedUserAgent,
  serializeBrandList,
};
