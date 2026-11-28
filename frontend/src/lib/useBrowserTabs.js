import { useEffect, useMemo, useRef, useState } from "react";
import { controlledEngine, webBrowserEngine } from "./browserEngines.js";
import { browserChromeShortcutAction } from "./browserShortcuts.js";
import { browserTabOpening, withBrowserTabEngines } from "./browserTabs.js";
import { externalOpenPlan } from "./externalOpens.js";
import { BROWSER_CONTENT } from "./plugins.js";
import { mergeCodeOpenPaths } from "./workspacePaths.js";

let browserTabSequence = 0;

// The Browser plugin's web tabs: opening, closing and reopening them, tabs Rem
// opens, links other apps send to Raticode, and sharing a tab with Rem.
export function useBrowserTabs({ settings, initialEditorSession, mainContent, setSidebarActivity, showMainContent, rattishEditorRef, openLinkedCodeFileRef, setTopBarNotice }) {
  const [viewedBrowserPaths, setViewedBrowserPaths] = useState([]);
  // A Rem approval waiting in a web tab while the Browser plugin isn't in front.
  const [hiddenBrowserApproval, setHiddenBrowserApproval] = useState(null);
  // The Browser plugin's tabs, apart from the File explorer's file tabs. Each
  // tab keeps the browser it runs in; tabs saved before that ran in the
  // browser chosen in Settings > Browser and keep it.
  const [initialBrowserSession] = useState(() => withBrowserTabEngines(initialEditorSession?.browserPaths || [], initialEditorSession?.browserTabs || {}, settings.browser.engine));
  const [browserTabs, setBrowserTabs] = useState(initialBrowserSession.tabs);
  const [browserOpenPaths, setBrowserOpenPaths] = useState(initialBrowserSession.paths);
  const [activeBrowserPath, setActiveBrowserPath] = useState(() => initialBrowserSession.paths.includes(initialEditorSession?.browserActivePath) ? initialEditorSession.browserActivePath : initialBrowserSession.paths.at(-1) || "");
  // The Browser plugin's tab area, and whichever tab area is in front, for
  // commands such as Find and Close tab.
  const browserWorkspaceRef = useRef(null);
  const browserContentOpenRef = useRef(false);
  // The Browser plugin can live in a window of its own; it is in view there.
  const [browserPoppedOut, setBrowserPoppedOut] = useState(false);
  const activeWorkspaceRef = useMemo(() => ({ get current() { return (browserContentOpenRef.current ? browserWorkspaceRef : rattishEditorRef).current; } }), [rattishEditorRef]);
  const openIntegratedBrowserRef = useRef(null);
  const reopenClosedBrowserTabRef = useRef(null);
  const closedBrowserTabsRef = useRef([]);
  const closeAgentBrowserTabRef = useRef(null);
  const browserContentOpen = mainContent === BROWSER_CONTENT;
  browserContentOpenRef.current = browserContentOpen;

  // Links and web pages other apps opened with Raticode: the ones that arrived
  // before this window loaded, then each new one as it comes.
  useEffect(() => {
    const bridge = window.goferBrowser;
    if (!bridge?.defaultBrowser) return undefined;
    let cancelled = false;
    const open = async (items) => {
      for (const item of Array.isArray(items) ? items : []) {
        if (cancelled) return;
        await openExternalItemRef.current?.(item);
        // Lets the tab bar update first, so a second Raticode (gecko) link joins the first one's window.
        await new Promise((resolve) => window.setTimeout(resolve, 50));
      }
    };
    const unsubscribe = bridge.onExternalOpen?.((message) => { void open(message?.items); });
    void Promise.resolve(bridge.defaultBrowser("take-opens")).then(open).catch(() => {});
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, []);

  // The Browser plugin in front. It has no side pane; Rem and threads stay on the right.
  function showBrowserContent() {
    setSidebarActivity(BROWSER_CONTENT);
    showMainContent(BROWSER_CONTENT);
  }

  function openIntegratedBrowser(initialUrl, options = {}) {
    const requestedUrl = initialUrl || settings.browser.homepage || "about:blank";
    // Background tabs join the tab bar without changing the user's view.
    if (!options.background) showBrowserContent();
    if (!options.newTab && !options.private && !options.engine && browserOpenPaths.length) {
      const targetPath = browserTabs[activeBrowserPath] ? activeBrowserPath : browserOpenPaths.at(-1);
      setActiveBrowserPath(targetPath);
      return targetPath;
    }
    const opening = browserTabOpening({ paths: browserOpenPaths, tabs: browserTabs, defaultEngine: settings.browser.engine, engine: options.engine, privateTab: options.private === true });
    if (opening.reuse) {
      // Raticode (gecko) is one Firefox window: the address opens as a Firefox tab there.
      if (/^https?:/i.test(String(initialUrl || ""))) {
        setBrowserTabs((current) => current[opening.reuse]
          ? { ...current, [opening.reuse]: { ...current[opening.reuse], pendingUrls: [...(current[opening.reuse].pendingUrls || []), initialUrl].slice(-25) } }
          : current);
      } else if (options.newTab) {
        // New tab with no address: Firefox opens an empty tab of its own.
        setBrowserTabs((current) => current[opening.reuse]
          ? { ...current, [opening.reuse]: { ...current[opening.reuse], geckoNewTabs: (current[opening.reuse].geckoNewTabs || 0) + 1 } }
          : current);
      }
      if (!options.background) setActiveBrowserPath(opening.reuse);
      return opening.reuse;
    }
    const path = createBrowserTabPath();
    setBrowserTabs((current) => ({
      ...current,
      [path]: {
        engine: opening.engine,
        error: "",
        focusLocation: !options.background && opening.engine !== "gecko" && (options.focusLocation ?? !initialUrl),
        loading: true,
        title: options.title || "",
        url: requestedUrl,
        ...(options.private ? { private: true } : {}),
        ...(options.history ? { history: options.history } : {}),
      },
    }));
    setBrowserOpenPaths((current) => mergeCodeOpenPaths(current, [path]));
    if (options.background) return path;
    setActiveBrowserPath(path);
    return path;
  }

  function clearGeckoPending(path, urls = []) {
    const sent = new Set(urls);
    setBrowserTabs((current) => {
      const tab = current[path];
      if (!tab?.pendingUrls?.length) return current;
      const pendingUrls = tab.pendingUrls.filter((url) => !sent.has(url));
      return { ...current, [path]: { ...tab, pendingUrls } };
    });
  }

  // Ctrl+Shift+T brings back the most recently closed web tab with its history.
  function reopenClosedBrowserTab() {
    const tab = closedBrowserTabsRef.current.pop();
    if (!tab) return "";
    return openIntegratedBrowser(tab.url, { newTab: true, focusLocation: false, title: tab.title, history: tab.history, ...(tab.engine ? { engine: tab.engine } : {}) });
  }
  reopenClosedBrowserTabRef.current = reopenClosedBrowserTab;

  openIntegratedBrowserRef.current = openIntegratedBrowser;

  // A link or web page another app opened with Raticode (Raticode is the
  // computer's default browser). See lib/externalOpens.js for where it goes.
  async function openExternalItem(item) {
    const engine = settings.browser.externalEngine;
    const geckoTabOpen = Boolean(browserTabOpening({ paths: browserOpenPaths, tabs: browserTabs, engine: "gecko" }).reuse);
    let geckoStatus = null;
    if (item?.kind === "url" && engine === "gecko") {
      try { geckoStatus = await window.goferBrowser?.gecko?.("status"); } catch { geckoStatus = null; }
    }
    const plan = externalOpenPlan(item, { engine, geckoStatus, geckoTabOpen });
    if (!plan) return;
    if (plan.kind === "file") {
      await openLinkedCodeFileRef.current?.(plan.path, { preview: true, external: true });
      return;
    }
    openIntegratedBrowserRef.current?.(plan.url, { newTab: true, focusLocation: false, engine: plan.engine });
    if (plan.notice) setTopBarNotice({ type: "info", message: plan.notice });
  }
  const openExternalItemRef = useRef(null);
  openExternalItemRef.current = openExternalItem;
  closeAgentBrowserTabRef.current = (id) => {
    const path = Object.keys(browserTabs).find((candidate) => browserTabs[candidate].agentSessionId === id);
    if (path) closeBrowserTabs([path]);
  };

  useEffect(() => {
    // Rem's tabs join the tab strip without being selected, so the user's
    // current view and keyboard focus stay put.
    const unsubscribeOpen = window.goferBrowser?.onAgentOpen?.((request) => {
      if (typeof request?.id !== "string" || !request.id) return;
      const path = createBrowserTabPath();
      // Rem's tab streams from Firefox, Chrome or Edge when one is the default browser.
      const engine = webBrowserEngine(request.engine);
      const tab = { ...agentBrowserTab(request), agentEngine: engine, engine };
      setBrowserTabs((current) => ({ ...current, [path]: tab }));
      setBrowserOpenPaths((current) => mergeCodeOpenPaths(current, [path]));
    });
    const unsubscribeClose = window.goferBrowser?.onAgentClose?.((request) => {
      if (request?.id) closeAgentBrowserTabRef.current?.(request.id);
    });
    return () => {
      unsubscribeOpen?.();
      unsubscribeClose?.();
    };
  }, []);

  // The main process applies Rem's site rules, sign-in sharing and downloads.
  useEffect(() => {
    void Promise.resolve(window.goferBrowser?.setPolicy?.(browserPolicyFromSettings(settings.browser))).catch(() => {});
  }, [settings.browser]);

  // On Linux, Raticode (gecko) needs Raticode to start in X11 mode. It's wanted
  // while Gecko is the default browser or a Gecko tab is open.
  const geckoWanted = webBrowserEngine(settings.browser.engine) === "gecko" || browserOpenPaths.some((path) => browserTabs[path]?.engine === "gecko");
  useEffect(() => {
    void Promise.resolve(window.goferBrowser?.gecko?.("select", { selected: geckoWanted })).catch(() => {});
  }, [geckoWanted]);

  // The browser sends the page text with the element and selection first.
  function askRemAboutPage({ url, title, text = "" }) {
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: url, title, text, draft: "",
    } }));
  }

  // Raticode (gecko) is Firefox's own window; the tab in front of it is shared.
  function shareGeckoTabWithRem() {
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: "", title: "", text: "", geckoShare: true,
    } }));
  }

  function shareBrowserTabWithRem({ id, url, title }) {
    if (!id) return;
    window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
      mode: "ask", autoSend: false, webPage: true, path: url, title, text: "", browserSessionId: id,
      draft: `I shared the browser tab “${title || url}” with you. Use it to `,
    } }));
  }

  function updateBrowserTab(path, nextState) {
    // Persist only what restores the tab; live state stays in the workspace.
    const saved = Object.fromEntries(Object.entries(nextState || {}).filter(([key]) => BROWSER_TAB_SAVED_FIELDS.has(key)));
    if (!Object.keys(saved).length) return;
    setBrowserTabs((current) => current[path]
      ? { ...current, [path]: { ...current[path], ...saved } }
      : current);
  }

  // Closing web tabs in the Browser plugin. Ctrl+Shift+T can bring back the
  // user's own tabs, in the browser they ran in.
  function closeBrowserTabs(paths) {
    const closing = new Set(paths.filter(Boolean));
    if (!closing.size) return;
    for (const path of closing) {
      const tab = browserTabs[path];
      if (tab && !tab.agentSessionId && !tab.private && /^https?:/i.test(String(tab.url || ""))) {
        closedBrowserTabsRef.current = [...closedBrowserTabsRef.current, { url: tab.url, title: tab.title || "", history: tab.history || null, engine: tab.engine || "" }].slice(-25);
      }
    }
    setBrowserOpenPaths((current) => {
      const next = current.filter((candidate) => !closing.has(candidate));
      setActiveBrowserPath((currentActive) => {
        if (!closing.has(currentActive)) return currentActive;
        const index = current.indexOf(currentActive);
        return next[Math.min(index, next.length - 1)] ?? next.at(-1) ?? "";
      });
      return next;
    });
    setBrowserTabs((current) => Object.fromEntries(
      Object.entries(current).filter(([path]) => !closing.has(path)),
    ));
  }
  return { viewedBrowserPaths, setViewedBrowserPaths, hiddenBrowserApproval, setHiddenBrowserApproval, browserTabs, browserOpenPaths, setBrowserOpenPaths, activeBrowserPath, setActiveBrowserPath, browserWorkspaceRef, browserPoppedOut, setBrowserPoppedOut, activeWorkspaceRef, openIntegratedBrowserRef, reopenClosedBrowserTabRef, browserContentOpen, showBrowserContent, openIntegratedBrowser, clearGeckoPending, askRemAboutPage, shareGeckoTabWithRem, shareBrowserTabWithRem, updateBrowserTab, closeBrowserTabs };
}

export function agentBrowserTab({ id, label, partition }) {
  return {
    agentLabel: typeof label === "string" && label.trim() ? label.trim().slice(0, 60) : "Rem",
    agentPartition: partition === "persist:raticode-rem" ? partition : "persist:raticode-rem",
    agentSessionId: id,
    error: "",
    focusLocation: false,
    loading: true,
    title: "",
    url: "about:blank",
  };
}

// Rem's tabs belong to live desktop sessions and cannot be restored later.
// Private tabs are never written down.
export function withoutAgentBrowserTabs(paths, activePath, browserTabs) {
  const agentPaths = new Set(Object.keys(browserTabs).filter((path) => browserTabs[path]?.agentSessionId || browserTabs[path]?.private));
  if (!agentPaths.size) return { paths, activePath, browserTabs };
  const kept = paths.filter((path) => !agentPaths.has(path));
  return {
    paths: kept,
    activePath: agentPaths.has(activePath) ? kept.at(-1) ?? "" : activePath,
    browserTabs: Object.fromEntries(Object.entries(browserTabs).filter(([path]) => !agentPaths.has(path))),
  };
}

const BROWSER_TAB_SAVED_FIELDS = new Set(["url", "title", "favicon", "history", "loading", "error"]);

// Browser keys the integrated browser handles while focus is inside it.
export function browserChromeOwnsShortcut(event) {
  if (!event?.target?.closest?.("[data-integrated-browser]")) return false;
  return Boolean(browserChromeShortcutAction(event, window.goferBrowser?.platform || "", { scoped: true }));
}

export function browserPolicyFromSettings(browser = {}) {
  return {
    engine: controlledEngine(browser.engine),
    searchUrl: browser.searchUrl,
    homepage: browser.homepage,
    remSignInSites: browser.remSignInSites || [],
    remAllowedSites: browser.remAllowedSites || [],
    remBlockedSites: browser.remBlockedSites || [],
    remConfirm: browser.remConfirm,
    remEvaluate: browser.remEvaluate !== false,
    remDownloads: browser.remDownloads,
    askWhereToSave: browser.askWhereToSave === true,
  };
}

function createBrowserTabPath() {
  browserTabSequence += 1;
  return `raticode-browser:${Date.now().toString(36)}:${browserTabSequence.toString(36)}`;
}
