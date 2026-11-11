// Web browsers that can run Raticode's web tabs. Raticode (chromium) shows
// pages in Electron views; Raticode (gecko) puts Firefox's real window in the
// tab; the others run without a window in a profile only Raticode uses and
// stream their tabs into the studio. Each tab keeps the browser it opened in,
// so one tab bar can hold tabs from all of them.
//
// The stored values ("raticode", "gecko", ...) never change: saved settings,
// open tabs and paired phones use them. Only the names shown to people do.
export const WEB_BROWSER_OPTIONS = [
  ["raticode", "Raticode (chromium)"],
  ["gecko", "Raticode (gecko)"],
  ["firefox", "Firefox"],
  ["chrome", "Chrome"],
  ["edge", "Edge"],
];

const NAMES = new Map(WEB_BROWSER_OPTIONS);
const STREAMED = new Set(["firefox", "chrome", "edge"]);
// Short names for the badge on each tab.
const BADGES = new Map([["raticode", "chromium"], ["gecko", "gecko"], ["firefox", "Firefox"], ["chrome", "Chrome"], ["edge", "Edge"]]);

export function isStreamedEngine(engine) {
  return STREAMED.has(engine);
}

// The name people see: "Raticode (chromium)", "Raticode (gecko)", "Chrome".
export function engineLabel(engine) {
  return NAMES.get(engine) || NAMES.get("raticode");
}

export function engineBadge(engine) {
  return BADGES.get(webBrowserEngine(engine));
}

// "a Chrome window", "an Edge window".
export function withArticle(name) {
  return `${/^[aeiou]/i.test(String(name)) ? "an" : "a"} ${name}`;
}

// The engine a stored value names, or Raticode (chromium) for anything else.
export function webBrowserEngine(value) {
  return value === "gecko" || STREAMED.has(value) ? value : "raticode";
}

// The engine behind tabs Raticode itself drives: Rem's tabs, previews and the
// phone's view of the computer. Gecko gives Raticode no control of its pages,
// so those stay on Raticode (chromium) when Gecko is the default browser.
export function controlledEngine(value) {
  const engine = webBrowserEngine(value);
  return engine === "gecko" ? "raticode" : engine;
}

// The browser a new tab opens in. Private tabs only exist in Raticode
// (chromium), which keeps them out of every saved profile.
export function newTabEngine(requested, defaultEngine, { privateTab = false } = {}) {
  if (privateTab) return "raticode";
  return webBrowserEngine(requested || defaultEngine);
}
