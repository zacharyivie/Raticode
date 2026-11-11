import { newTabEngine, webBrowserEngine } from "./browserEngines.js";

// The Browser plugin's tabs: one tab bar for every browser. Each tab records
// the browser it runs in (`engine`). Raticode (chromium) and the streamed
// browsers (Firefox, Chrome, Edge) get one page per tab. Raticode (gecko) is
// Firefox's real window placed over the tab, with its own tabs inside, so the
// tab bar holds at most one Gecko tab.

export const GECKO_VIEWED = "gecko";

// The browser a tab runs in. Tabs saved before tabs had their own browser ran
// in the one chosen in Settings > Browser, so they keep it. Rem's tabs and
// private tabs never ran in Gecko.
export function browserTabEngine(tab, defaultEngine = "raticode") {
  if (!tab) return "raticode";
  if (tab.private) return "raticode";
  if (tab.agentSessionId) return webBrowserEngine(tab.agentEngine || tab.engine);
  return webBrowserEngine(tab.engine || defaultEngine);
}

// Gives every tab its browser and keeps one Gecko tab. Addresses from extra
// Gecko tabs are handed to the kept one, which opens them in Firefox's own tabs.
export function withBrowserTabEngines(paths = [], tabs = {}, defaultEngine = "raticode") {
  const nextTabs = {};
  let geckoPath = "";
  const keptPaths = [];
  for (const path of paths) {
    const tab = tabs[path];
    if (!tab) continue;
    const engine = browserTabEngine(tab, defaultEngine);
    if (engine === "gecko" && geckoPath) {
      const kept = nextTabs[geckoPath];
      const url = String(tab.url || "");
      if (/^https?:/i.test(url) && url !== kept.url && !(kept.pendingUrls || []).includes(url)) {
        nextTabs[geckoPath] = { ...kept, pendingUrls: [...(kept.pendingUrls || []), url].slice(-25) };
      }
      continue;
    }
    if (engine === "gecko") geckoPath = path;
    nextTabs[path] = tab.engine === engine ? tab : { ...tab, engine };
    keptPaths.push(path);
  }
  return { paths: keptPaths, tabs: nextTabs };
}

export function geckoTabPath(paths = [], tabs = {}) {
  return paths.find((path) => tabs[path]?.engine === "gecko") || "";
}

// What opening a tab does: a new tab in its browser, or, for Gecko when a
// Gecko tab is already open, that tab with the address sent to Firefox.
export function browserTabOpening({ paths = [], tabs = {}, defaultEngine = "raticode", engine = "", privateTab = false } = {}) {
  const chosen = newTabEngine(engine, defaultEngine, { privateTab });
  if (chosen === "gecko") {
    const existing = geckoTabPath(paths, tabs);
    if (existing) return { engine: chosen, reuse: existing };
  }
  return { engine: chosen, reuse: "" };
}

// The tabs on screen, for the media bar. A Gecko tab on screen shows Firefox's
// window, so its player is in view too.
export function viewedBrowserMedia(viewedPaths = [], tabs = {}) {
  const viewed = (Array.isArray(viewedPaths) ? viewedPaths : []).filter(Boolean);
  return viewed.some((path) => tabs[path]?.engine === "gecko") ? [...viewed, GECKO_VIEWED] : viewed;
}

// Paths are kept apart: web tabs in the Browser plugin, everything else in the
// File explorer's editor. Returns the two lists for a mixed list.
export function splitBrowserPaths(paths = [], tabs = {}) {
  const browser = [];
  const files = [];
  for (const path of paths) (tabs[path] ? browser : files).push(path);
  return { browser, files };
}
