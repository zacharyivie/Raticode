// Pure decisions for the integrated browser. Electron wiring lives in
// browser-sessions.cjs; everything here is deterministic and unit tested.
const path = require("node:path");

// User tabs keep sign-ins across restarts. Rem gets its own profile so pages
// it reads cannot act through the user's accounts unless a site is shared.
// Local previews use an in-memory profile that never touches either.
const PARTITIONS = Object.freeze({
  user: "persist:raticode-browser",
  rem: "persist:raticode-rem",
  preview: "raticode-preview",
});
const BROWSER_PARTITIONS = new Set(Object.values(PARTITIONS));

// Chromium treats an input event as user activation for about five seconds.
const USER_ACTIVATION_MS = 5000;
const MAX_PAGE_TABS_PER_WINDOW = 10;
const PAGE_TAB_WINDOW_MS = 10000;

const ZOOM_LEVELS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];

// Same list Chromium uses for its "may harm your computer" warning, trimmed to
// types that run code when opened.
const DANGEROUS_EXTENSIONS = new Set([
  "action", "apk", "app", "appimage", "application", "appref-ms", "bas", "bat", "bin", "cab", "chm",
  "cmd", "com", "command", "cpl", "crx", "deb", "desktop", "dll", "dmg", "docm", "exe", "gadget",
  "hta", "inf", "ins", "isp", "jar", "jnlp", "js", "jse", "ko", "lnk", "mht", "mhtml", "mjs", "msc",
  "msi", "msp", "mst", "pif", "pkg", "pl", "plugin", "ps1", "ps1xml", "ps2", "psc1", "py", "pyc",
  "pyw", "rb", "reg", "rpm", "run", "scf", "scr", "sct", "service", "sh", "shb", "shs", "so", "sys",
  "url", "vb", "vbe", "vbs", "vsix", "workflow", "ws", "wsc", "wsf", "wsh", "xbap", "xlsm", "xpi",
]);

// Granted without a prompt, as Chromium does after a user gesture.
const AUTO_ALLOWED_PERMISSIONS = new Set(["clipboard-sanitized-write", "fullscreen", "pointerLock"]);
// Asked per site. Everything else, including device access, is denied.
const PROMPT_PERMISSIONS = new Set([
  "media", "geolocation", "notifications", "clipboard-read", "midi", "midiSysex", "idle-detection",
  "window-management", "keyboardLock", "storage-access", "top-level-storage-access", "openExternal",
]);
const PERMISSION_LABELS = {
  camera: "Use your camera",
  microphone: "Use your microphone",
  geolocation: "Know your location",
  notifications: "Show notifications",
  "clipboard-read": "See text and images copied to the clipboard",
  midi: "Use MIDI devices",
  midiSysex: "Control and reprogram MIDI devices",
  "idle-detection": "Know when you're actively using this device",
  "window-management": "Manage windows on all your displays",
  keyboardLock: "Capture keyboard shortcuts",
  "storage-access": "Use cookies from other sites",
  "top-level-storage-access": "Use cookies from other sites",
  openExternal: "Open an app on your computer",
};

// Apps that commonly receive links from web pages. Other schemes, including
// Windows handlers abused for code execution, are refused outright.
const EXTERNAL_PROTOCOLS = new Set([
  "mailto:", "tel:", "sms:", "webcal:", "zoommtg:", "zoomus:", "msteams:", "slack:", "discord:",
  "spotify:", "vscode:", "vscode-insiders:", "cursor:", "figma:", "notion:", "obsidian:", "itms-apps:",
  "maps:", "facetime:", "skype:", "whatsapp:", "tg:", "signal:", "magnet:", "steam:",
]);

function urlOf(value) {
  try { return new URL(String(value ?? "")); } catch { return null; }
}

function isHttpUrl(value) {
  const url = urlOf(value);
  return Boolean(url && (url.protocol === "http:" || url.protocol === "https:"));
}

// Site settings are kept per origin, matching Chromium's content settings.
function siteKey(value) {
  const url = urlOf(value);
  if (!url) return "";
  if (url.protocol === "http:" || url.protocol === "https:") return url.origin;
  if (url.protocol === "file:") return "file://";
  return "";
}

function hostOf(value) {
  const url = urlOf(value);
  return url && (url.protocol === "http:" || url.protocol === "https:") ? url.hostname.toLowerCase() : "";
}

// Users type "github.com", "https://github.com/x" or "*.github.com".
function normalizeSitePattern(value) {
  let input = String(value ?? "").trim().toLowerCase();
  if (!input) return "";
  input = input.replace(/^\*\./, "");
  const url = urlOf(/^[a-z][a-z\d+.-]*:\/\//.test(input) ? input : `https://${input}`);
  if (!url || !url.hostname || url.username || url.password) return "";
  const host = url.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!/^[a-z\d.:-]+$/.test(host) || host.startsWith(".") || host.includes("..")) return "";
  return host;
}

function normalizeSiteList(value, limit = 200) {
  const items = Array.isArray(value) ? value : String(value ?? "").split(/[\s,]+/);
  return [...new Set(items.map(normalizeSitePattern).filter(Boolean))].slice(0, limit);
}

// A pattern covers the host itself and every subdomain.
function hostMatches(host, pattern) {
  const target = String(host ?? "").toLowerCase();
  const site = normalizeSitePattern(pattern);
  return Boolean(target && site && (target === site || target.endsWith(`.${site}`)));
}

function matchesAnySite(host, patterns = []) {
  return patterns.some((pattern) => hostMatches(host, pattern));
}

// Cookies scoped to the shared site, a subdomain, or a parent domain the site sends.
function cookieMatchesSite(cookieDomain, site) {
  const domain = String(cookieDomain ?? "").toLowerCase().replace(/^\./, "");
  const target = normalizeSitePattern(site);
  if (!domain || !target) return false;
  return domain === target || domain.endsWith(`.${target}`) || target.endsWith(`.${domain}`);
}

function isLocalhost(host) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host) || host.endsWith(".localhost");
}

function securityState({ url, certificateException = false, certificateError = null } = {}) {
  const parsed = urlOf(url);
  if (!parsed || parsed.protocol === "about:" || parsed.protocol === "data:" || parsed.protocol === "blob:") {
    return { level: "internal", label: "Raticode page" };
  }
  if (certificateError) return { level: "error", label: "Certificate error" };
  if (parsed.protocol === "https:") {
    return certificateException
      ? { level: "warning", label: "Not secure: certificate error ignored" }
      : { level: "secure", label: "Connection is secure" };
  }
  if (parsed.protocol === "http:") {
    return isLocalhost(parsed.hostname.toLowerCase())
      ? { level: "local", label: "Local development server" }
      : { level: "insecure", label: "Not secure" };
  }
  if (parsed.protocol === "file:") return { level: "local", label: "Local file" };
  return { level: "internal", label: "Raticode page" };
}

function nextZoomLevel(current = 1, direction = 1) {
  const value = Number.isFinite(current) && current > 0 ? current : 1;
  if (direction > 0) return ZOOM_LEVELS.find((level) => level > value + 0.001) ?? ZOOM_LEVELS.at(-1);
  return [...ZOOM_LEVELS].reverse().find((level) => level < value - 0.001) ?? ZOOM_LEVELS[0];
}

function clampZoom(value) {
  return Number.isFinite(value) && value > 0 ? Math.min(Math.max(value, ZOOM_LEVELS[0]), ZOOM_LEVELS.at(-1)) : 1;
}

function downloadExtension(filename) {
  const base = path.basename(String(filename ?? "")).toLowerCase();
  const index = base.lastIndexOf(".");
  return index > 0 ? base.slice(index + 1) : "";
}

function isDangerousDownload(filename) {
  return DANGEROUS_EXTENSIONS.has(downloadExtension(filename));
}

function sanitizeFilename(value, fallback = "download") {
  const cleaned = path.basename(String(value ?? ""))
    // Control and path characters are invalid or unsafe on some platform.
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/g, "_")
    .replace(/^[.\s]+|[.\s]+$/g, "")
    .slice(0, 200);
  return /^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i.test(cleaned) || !cleaned ? fallback : cleaned;
}

// Never overwrite: "report.pdf" becomes "report (1).pdf".
function uniqueDownloadPath(directory, filename, exists) {
  const safe = sanitizeFilename(filename);
  const extension = path.extname(safe);
  const stem = extension ? safe.slice(0, -extension.length) : safe;
  for (let index = 0; index < 10000; index += 1) {
    const candidate = path.join(directory, index ? `${stem} (${index})${extension}` : safe);
    if (!exists(candidate)) return candidate;
  }
  throw new Error("Could not choose a file name for the download.");
}

function parseWindowFeatures(features = "") {
  const values = {};
  for (const part of String(features).split(",")) {
    const [key, raw = ""] = part.split("=").map((item) => item.trim().toLowerCase());
    if (key) values[key] = raw;
  }
  const size = (raw, fallback, minimum, maximum) => {
    const number = Number.parseInt(raw, 10);
    return Number.isFinite(number) ? Math.min(Math.max(number, minimum), maximum) : fallback;
  };
  return {
    popup: Object.keys(values).length > 0 && values.noopener === undefined,
    width: size(values.width, 520, 240, 1600),
    height: size(values.height, 680, 200, 1200),
  };
}

// Decides what window.open, target=_blank and middle-click do.
function popupDecision({
  url,
  disposition = "default",
  features = "",
  postBody = null,
  agent = false,
  lastUserInputAt = 0,
  now = Date.now(),
  popupsAllowed = false,
} = {}) {
  const parsed = urlOf(url);
  const web = parsed && ["http:", "https:"].includes(parsed.protocol);
  const blank = String(url ?? "") === "about:blank" || String(url ?? "") === "";
  const blob = parsed?.protocol === "blob:";
  // Rem follows links in place; it never spawns windows the user did not open.
  if (agent) return web ? { kind: "same-tab" } : { kind: "deny" };
  if (!web && !blank && !blob) return { kind: "deny" };
  const gesture = now - lastUserInputAt <= USER_ACTIVATION_MS;
  if (!gesture && !popupsAllowed) return { kind: "blocked" };
  const windowFeatures = parseWindowFeatures(features);
  // Sign-in flows open a sized popup and talk back through window.opener.
  if (blank || disposition === "new-window" || windowFeatures.popup || postBody) {
    return { kind: "window", width: windowFeatures.width, height: windowFeatures.height };
  }
  if (disposition === "background-tab") return { kind: "tab", background: true };
  return { kind: "tab", background: false };
}

function permissionKeys(permission, details = {}) {
  if (permission === "media") {
    const types = Array.isArray(details.mediaTypes) ? details.mediaTypes : [details.mediaType].filter(Boolean);
    const keys = [];
    if (types.includes("video")) keys.push("camera");
    if (types.includes("audio")) keys.push("microphone");
    return keys;
  }
  return [permission];
}

function permissionLabel(key) {
  return PERMISSION_LABELS[key] || key;
}

function externalProtocolOf(details = {}) {
  const url = urlOf(details.externalURL);
  return url && EXTERNAL_PROTOCOLS.has(url.protocol) ? url.protocol : "";
}

// Returns "allow", "deny" or "prompt" for one permission request.
function permissionDecision({ permission, details = {}, stored = {}, agent = false } = {}) {
  if (AUTO_ALLOWED_PERMISSIONS.has(permission)) return agent && permission !== "clipboard-sanitized-write" ? "deny" : "allow";
  if (agent || !PROMPT_PERMISSIONS.has(permission)) return "deny";
  if (permission === "openExternal" && !externalProtocolOf(details)) return "deny";
  const keys = permission === "openExternal" ? [`openExternal:${externalProtocolOf(details)}`] : permissionKeys(permission, details);
  if (!keys.length) return "deny";
  const decisions = keys.map((key) => stored[key]);
  if (decisions.some((value) => value === "block")) return "deny";
  if (decisions.every((value) => value === "allow")) return "allow";
  return "prompt";
}

function isCertificateErrorCode(code) {
  return Number.isFinite(code) && code <= -200 && code > -300;
}

function classifyLoadError(code, description = "") {
  if (isCertificateErrorCode(code)) {
    return { kind: "certificate", title: "Your connection is not private", detail: "The site's security certificate is not trusted. Someone could be trying to read or change what you send." };
  }
  const text = String(description || "");
  if (code === -106 || /INTERNET_DISCONNECTED/.test(text)) {
    return { kind: "offline", title: "You're offline", detail: "Check your network connection, then retry." };
  }
  if (code === -105 || code === -137 || /NAME_NOT_RESOLVED|NAME_RESOLUTION_FAILED/.test(text)) {
    return { kind: "dns", title: "This site can't be found", detail: "The address may be misspelled, or the site's DNS lookup failed." };
  }
  if (code === -7 || code === -118 || /TIMED_OUT/.test(text)) {
    return { kind: "timeout", title: "This site took too long to respond", detail: "The server may be busy or unreachable." };
  }
  if (code === -102 || code === -101 || code === -100 || /CONNECTION_(REFUSED|RESET|CLOSED)/.test(text)) {
    return { kind: "refused", title: "This site can't be reached", detail: "The server refused or dropped the connection. If it is a local development server, make sure it is running." };
  }
  if (code === -20 || code === -21 || code === -27 || /BLOCKED/.test(text)) {
    return { kind: "blocked", title: "This page was blocked", detail: "Raticode or the site blocked this request." };
  }
  return { kind: "generic", title: "Could not display this page", detail: text || `Error ${code}` };
}

// Chromium's own UA without Electron and app tokens, which sites such as
// WhatsApp Web and Teams reject. `keepElectron` keeps the honest Electron
// token for sites that require it (see browser-client-hints.cjs).
function userAgentWithoutEmbedderTokens(userAgent, { keepElectron = false } = {}) {
  let value = String(userAgent ?? "")
    // Electron puts the app's name and version before Chrome/.
    .replace(/(\(KHTML, like Gecko\)).*?(\sChrome\/)/, "$1$2")
    .replace(/\s(?:raticode|Raticode|gofer-flow-ui|gofer-flow|taskurotta)\/\S+/g, "");
  if (!keepElectron) value = value.replace(/\sElectron\/\S+/g, "");
  return value.replace(/\s{2,}/g, " ").trim();
}

const DEFAULT_BROWSER_POLICY = Object.freeze({
  searchUrl: "https://www.google.com/search?q={query}",
  homepage: "raticode://home",
  remSignInSites: [],
  remAllowedSites: [],
  remBlockedSites: [],
  remConfirm: "signed-in",
  remEvaluate: true,
  remDownloads: "block",
  askWhereToSave: false,
});

function sanitizeBrowserPolicy(value = {}) {
  const input = value && typeof value === "object" ? value : {};
  const searchUrl = typeof input.searchUrl === "string" && input.searchUrl.includes("{query}") && isHttpUrl(input.searchUrl.replace("{query}", "q"))
    ? input.searchUrl.slice(0, 2048)
    : DEFAULT_BROWSER_POLICY.searchUrl;
  return {
    searchUrl,
    homepage: typeof input.homepage === "string" ? input.homepage.slice(0, 2048) : DEFAULT_BROWSER_POLICY.homepage,
    remSignInSites: normalizeSiteList(input.remSignInSites),
    remAllowedSites: normalizeSiteList(input.remAllowedSites),
    remBlockedSites: normalizeSiteList(input.remBlockedSites),
    remConfirm: ["signed-in", "always", "never"].includes(input.remConfirm) ? input.remConfirm : "signed-in",
    remEvaluate: input.remEvaluate !== false,
    remDownloads: input.remDownloads === "allow" ? "allow" : "block",
    askWhereToSave: input.askWhereToSave === true,
  };
}

function remSiteAllowed(url, policy = DEFAULT_BROWSER_POLICY) {
  const parsed = urlOf(url);
  if (!parsed) return false;
  if (parsed.protocol === "about:") return true;
  if (!["http:", "https:"].includes(parsed.protocol)) return false;
  const host = parsed.hostname.toLowerCase();
  if (matchesAnySite(host, policy.remBlockedSites || [])) return false;
  const allowed = policy.remAllowedSites || [];
  return !allowed.length || matchesAnySite(host, allowed);
}

// Signed-in pages are where a prompt-injected action does real damage.
function remNeedsConfirmation({ policy = DEFAULT_BROWSER_POLICY, url, sharedByUser = false, consequential = false } = {}) {
  if (!consequential || policy.remConfirm === "never") return false;
  if (policy.remConfirm === "always") return true;
  return sharedByUser || matchesAnySite(hostOf(url), policy.remSignInSites || []);
}

// A small token bucket stops pages from flooding the studio with tabs.
function allowPageTab(session, now = Date.now()) {
  const recent = (session.pageTabTimes || []).filter((time) => now - time < PAGE_TAB_WINDOW_MS);
  if (recent.length >= MAX_PAGE_TABS_PER_WINDOW) {
    session.pageTabTimes = recent;
    return false;
  }
  recent.push(now);
  session.pageTabTimes = recent;
  return true;
}

module.exports = {
  AUTO_ALLOWED_PERMISSIONS,
  BROWSER_PARTITIONS,
  DEFAULT_BROWSER_POLICY,
  EXTERNAL_PROTOCOLS,
  PARTITIONS,
  PROMPT_PERMISSIONS,
  USER_ACTIVATION_MS,
  ZOOM_LEVELS,
  allowPageTab,
  clampZoom,
  classifyLoadError,
  cookieMatchesSite,
  externalProtocolOf,
  hostMatches,
  hostOf,
  isCertificateErrorCode,
  isDangerousDownload,
  isHttpUrl,
  matchesAnySite,
  nextZoomLevel,
  normalizeSiteList,
  normalizeSitePattern,
  parseWindowFeatures,
  permissionDecision,
  permissionKeys,
  permissionLabel,
  popupDecision,
  remNeedsConfirmation,
  remSiteAllowed,
  sanitizeBrowserPolicy,
  sanitizeFilename,
  securityState,
  siteKey,
  uniqueDownloadPath,
  userAgentWithoutEmbedderTokens,
};
