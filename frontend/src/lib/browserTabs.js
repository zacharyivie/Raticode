import { WEB_BROWSER_OPTIONS, newTabEngine, webBrowserEngine } from "./browserEngines.js";

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

// The tab bar keeps each browser's tabs together. Private tabs form their own
// group, since a new tab beside them is private too. Groups appear in the order
// their first tab was opened; tabs keep their order inside a group.
export function browserTabGroupKey(tab, defaultEngine = "raticode") {
  return tab?.private ? "private" : browserTabEngine(tab, defaultEngine);
}

export function groupBrowserTabPaths(paths = [], tabs = {}, defaultEngine = "raticode") {
  const groups = [];
  const byKey = new Map();
  for (const path of paths) {
    const tab = tabs[path];
    if (!tab) continue;
    const key = browserTabGroupKey(tab, defaultEngine);
    let group = byKey.get(key);
    if (!group) {
      group = { key, engine: key === "private" ? "raticode" : key, private: key === "private", paths: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.paths.push(path);
  }
  return groups;
}

// Paths in the order the tab bar shows them.
export function groupedBrowserPaths(paths = [], tabs = {}, defaultEngine = "raticode") {
  const grouped = groupBrowserTabPaths(paths, tabs, defaultEngine).flatMap((group) => group.paths);
  // Anything that isn't a web tab keeps its place at the end.
  return [...grouped, ...paths.filter((path) => !tabs[path])];
}

// Raticode (gecko) is Firefox's own window with its own tab strip and "+", so
// its group gets no "+" of ours: two side by side would do the same thing. Its
// new-tab action stays in "+ Browser" instead.
export function browserGroupHasNewTab(group) {
  return Boolean(group) && (group.private || group.engine !== "gecko");
}

// Browsers "+ Browser" offers: the ones with no group in the tab bar yet, and
// any whose group has no "+" of its own (Raticode (gecko), listed in inside),
// so every browser always has one way to open another tab from the tab bar.
export function browserFamilyChoices(groups = []) {
  const present = new Map(groups.map((group) => [group.key, group]));
  const inside = [];
  const choices = WEB_BROWSER_OPTIONS.map(([value]) => value).filter((value) => {
    const group = present.get(value);
    if (!group) return true;
    if (browserGroupHasNewTab(group)) return false;
    inside.push(value);
    return true;
  });
  return { choices, inside, offerPrivate: !present.has("private") };
}
