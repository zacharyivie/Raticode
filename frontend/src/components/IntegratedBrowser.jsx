import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bookmark,
  Bot,
  Code,
  Cookie,
  Download,
  EllipsisVertical,
  ExternalLink,
  FileDown,
  GitCompareArrows,
  History,
  Loader2,
  MessageSquare,
  Minus,
  Plus,
  Printer,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  Share2,
  Star,
  Undo2,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  AddressSuggestions,
  BlockedPopupsBar,
  LibraryPanel,
  PageOverlay,
  PromptBar,
  RemChip,
  SecurityChip,
  SiteInfoPopover,
} from "./BrowserPanels.jsx";
import { browserChromeShortcutAction } from "../lib/browserShortcuts.js";
import {
  addressWithCompletion,
  formatZoom,
  historyMenuEntries,
  suggestionRows,
  trimHistoryForSession,
} from "../lib/browserUi.js";

const USER_PARTITION = "persist:raticode-browser";
const HISTORY_SAVE_DELAY_MS = 800;

export default function IntegratedBrowser({
  active,
  agentLabel = "",
  agentPartition = "",
  agentSessionId = "",
  applicationKeybindings = {},
  canShareWithRem = false,
  clientId,
  discardAfterMs = 0,
  dragActive = false,
  editing = false,
  focusLocationOnCreate = false,
  initialUrl = "about:blank",
  localPath = "",
  onActivateTab,
  onAskRem,
  onClose,
  onCycleTab,
  onNewTab,
  onReopenClosedTab,
  onShareWithRem,
  onShowDiff,
  onModeChange,
  onStateChange,
  openBrowserBinding = "Mod+Alt+Slash",
  openTabs = [],
  savedHistory = null,
  searchUrl = "https://www.google.com/search?q={query}",
  showDiffButton = false,
  showModeToggle = false,
}) {
  const findInputRef = useRef(null);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findError, setFindError] = useState("");
  const addressRef = useRef(null);
  const sectionRef = useRef(null);
  const activeRef = useRef(active);
  const focusLocationOnCreatePendingRef = useRef(focusLocationOnCreate);
  const initialUrlRef = useRef(initialUrl);
  const savedHistoryRef = useRef(savedHistory);
  const initialApplicationKeybindingsRef = useRef(applicationKeybindings);
  const initialOpenBrowserBindingRef = useRef(openBrowserBinding);
  const onCloseRef = useRef(onClose);
  const onCycleTabRef = useRef(onCycleTab);
  const onNewTabRef = useRef(onNewTab);
  const onStateChangeRef = useRef(onStateChange);
  const onReopenClosedTabRef = useRef(onReopenClosedTab);
  const askRemRef = useRef(null);
  const containerRef = useRef(null);
  const sessionIdRef = useRef("");
  const webviewRef = useRef(null);
  const [typed, setTyped] = useState({ text: "", deleting: false });
  const suggestionsId = useId();
  const [addressDraft, setAddressDraft] = useState(displayBrowserUrl(initialUrl));
  const [addressFocused, setAddressFocused] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [pendingSelection, setPendingSelection] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [libraryView, setLibraryView] = useState("");
  const [siteInfo, setSiteInfo] = useState(null);
  const [historyMenu, setHistoryMenu] = useState(null);
  const [activeDownloads, setActiveDownloads] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [discarded, setDiscarded] = useState(false);
  const [state, setState] = useState({
    canGoBack: false,
    canGoForward: false,
    error: "",
    favicon: "",
    loading: true,
    ready: false,
    title: "",
    url: initialUrl,
  });
  const bridge = window.goferBrowser;
  const isPreview = Boolean(localPath) || state.profile === "preview";
  const isRemTab = Boolean(agentSessionId);

  useEffect(() => {
    activeRef.current = active;
    onCloseRef.current = onClose;
    onCycleTabRef.current = onCycleTab;
    onNewTabRef.current = onNewTab;
    onStateChangeRef.current = onStateChange;
    onReopenClosedTabRef.current = onReopenClosedTab;
  }, [active, onClose, onCycleTab, onModeChange, onNewTab, onReopenClosedTab, onStateChange]);

  useEffect(() => {
    if (discarded) return undefined;
    if (!bridge?.create) {
      setState((current) => ({
        ...current,
        error: "The integrated browser is available in the Raticode desktop app.",
        loading: false,
      }));
      return undefined;
    }
    let disposed = false;
    let createdId = "";
    const unsubscribeState = bridge.onState?.((nextState) => {
      if (nextState?.clientId !== clientId) return;
      if (disposed) return;
      if (!browserSessionEventMatches(sessionIdRef.current, nextState)) return;
      setState((current) => ({ ...current, ...nextState }));
      onStateChangeRef.current?.(nextState);
    });
    const unsubscribeCommand = bridge.onCommand?.((command) => {
      if (command?.clientId !== clientId) return;
      if (!browserSessionEventMatches(sessionIdRef.current, command)) return;
      runCommand(command);
    });
    if (agentSessionId) {
      // Raticode created this session for Rem. Closing the tab destroys the
      // view, which ends the session; a remount only attaches a new view.
      sessionIdRef.current = agentSessionId;
      attachBrowserWebview(bridge, agentSessionId, containerRef.current, webviewRef, "about:blank", agentPartition || "persist:raticode-rem", (partialState) => {
        if (disposed || sessionIdRef.current !== agentSessionId) return;
        setState((current) => ({ ...current, ...partialState }));
        onStateChangeRef.current?.(partialState);
      }, clientId, setFullscreen);
      setState((current) => ({ ...current, id: agentSessionId }));
      return () => {
        disposed = true;
        unsubscribeState?.();
        unsubscribeCommand?.();
        sessionIdRef.current = "";
        webviewRef.current?.remove?.();
        webviewRef.current = null;
      };
    }
    const restoreHistory = savedHistoryRef.current;
    bridge.create({
      applicationKeybindings: initialApplicationKeybindingsRef.current,
      clientId,
      openBrowserBinding: initialOpenBrowserBindingRef.current,
      path: localPath,
      url: localPath ? "" : initialUrlRef.current,
      ...(!localPath && restoreHistory?.entries?.length ? { history: restoreHistory } : {}),
    })
      .then((nextState) => {
        if (disposed) {
          if (nextState?.id) void bridge.close(nextState.id).catch(() => {});
          return;
        }
        createdId = nextState?.id ?? "";
        sessionIdRef.current = createdId;
        attachBrowserWebview(bridge, createdId, containerRef.current, webviewRef, nextState?.src, nextState?.partition || USER_PARTITION, (partialState) => {
          if (disposed || sessionIdRef.current !== createdId) return;
          setState((current) => ({ ...current, ...partialState }));
          onStateChangeRef.current?.(partialState);
        }, "", setFullscreen);
        const cleanState = { ...(nextState ?? {}) };
        delete cleanState.src;
        delete cleanState.partition;
        setState((current) => ({ ...current, ...cleanState }));
        onStateChangeRef.current?.(cleanState);
      })
      .catch((error) => {
        if (disposed) return;
        setState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Could not open the browser",
          loading: false,
        }));
      });
    return () => {
      disposed = true;
      unsubscribeState?.();
      unsubscribeCommand?.();
      const id = sessionIdRef.current || createdId;
      sessionIdRef.current = "";
      webviewRef.current?.remove?.();
      webviewRef.current = null;
      if (id) void bridge.close(id).catch(() => {});
    };
    // runCommand reads refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentPartition, agentSessionId, bridge, clientId, localPath, discarded]);

  function openFind() {
    setFindOpen(true);
    window.requestAnimationFrame(() => {
      findInputRef.current?.focus();
      findInputRef.current?.select();
    });
  }

  function focusLocation() {
    addressRef.current?.focus();
    addressRef.current?.select();
  }

  function runCommand(command) {
    const id = sessionIdRef.current;
    switch (command?.action) {
      case "find": openFind(); break;
      case "focus-location": focusLocation(); break;
      case "close": onCloseRef.current?.(); break;
      case "next-tab": onCycleTabRef.current?.(1); break;
      case "previous-tab": onCycleTabRef.current?.(-1); break;
      case "new-tab": onNewTabRef.current?.(); break;
      case "reopen-closed-tab": onReopenClosedTabRef.current?.(); break;
      case "history": setLibraryView("history"); break;
      case "bookmark": if (id) void bridge?.bookmark?.(id).catch(() => {}); break;
      case "ask-rem": askRemRef.current?.(command.selection || ""); break;
      default: break;
    }
  }

  // Rem's tabs report their size so hidden ones still lay out at desktop size.
  useEffect(() => {
    const container = containerRef.current;
    if (!agentSessionId || !bridge?.viewport || !container || typeof ResizeObserver !== "function") return undefined;
    let frame = 0;
    // Hidden tabs measure 0x0. Rem then lays the page out at a desktop size.
    const report = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        void bridge.viewport(agentSessionId, container.clientWidth, container.clientHeight).catch(() => {});
      });
    };
    const observer = new ResizeObserver(report);
    observer.observe(container);
    report();
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [agentSessionId, bridge, state.ready]);

  useEffect(() => {
    const id = sessionIdRef.current;
    if (!id || !bridge?.setPreferences) return;
    void bridge.setPreferences(id, { applicationKeybindings, openBrowserBinding }).catch(() => {});
  }, [applicationKeybindings, bridge, openBrowserBinding, state.id]);

  // Restored tabs keep their back/forward list and scroll positions.
  useEffect(() => {
    if (isRemTab || localPath || !state.ready || state.loading || !bridge?.historyEntries) return undefined;
    const timer = window.setTimeout(() => {
      const id = sessionIdRef.current;
      if (!id) return;
      void bridge.historyEntries(id).then((history) => {
        const trimmed = trimHistoryForSession(history);
        savedHistoryRef.current = trimmed;
        if (trimmed) onStateChangeRef.current?.({ history: trimmed });
      }).catch(() => {});
    }, HISTORY_SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [bridge, isRemTab, localPath, state.loading, state.ready, state.url]);

  // Tabs hidden for a long time give their memory back and reload on return.
  useEffect(() => {
    if (active && discarded) {
      setDiscarded(false);
      return undefined;
    }
    if (active || discarded || isRemTab || localPath || !discardAfterMs || state.audible || state.prompts?.length || !state.ready) return undefined;
    const timer = window.setTimeout(() => {
      const id = sessionIdRef.current;
      const finish = () => setDiscarded(true);
      if (!id || !bridge?.historyEntries) { finish(); return; }
      void bridge.historyEntries(id).then((history) => {
        savedHistoryRef.current = trimHistoryForSession(history) || savedHistoryRef.current;
        initialUrlRef.current = state.url;
      }).catch(() => {}).finally(finish);
    }, discardAfterMs);
    return () => window.clearTimeout(timer);
  }, [active, bridge, discardAfterMs, discarded, isRemTab, localPath, state.audible, state.prompts, state.ready, state.url]);

  useEffect(() => {
    if (!bridge?.onDownloads) return undefined;
    const count = (downloads = []) => setActiveDownloads(downloads.filter((item) => item.state === "progressing" || item.state === "quarantined").length);
    void bridge.library?.("downloads").then((result) => count(result?.downloads)).catch(() => {});
    return bridge.onDownloads((payload) => count(payload?.downloads));
  }, [bridge]);

  useEffect(() => {
    if (!active) return undefined;
    function handleBrowserChromeShortcut(event) {
      const scoped = Boolean(event.target?.closest?.("[data-integrated-browser]") && sectionRef.current?.contains(event.target));
      const action = browserChromeShortcutAction(event, bridge?.platform, { scoped });
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      const id = sessionIdRef.current;
      switch (action) {
        case "focus-location": focusLocation(); return;
        case "find": openFind(); return;
        case "history": setLibraryView("history"); return;
        case "reopen-closed-tab": onReopenClosedTabRef.current?.(); return;
        default: break;
      }
      if (!id || !state.ready) return;
      const calls = {
        back: () => bridge.back(id),
        forward: () => bridge.forward(id),
        reload: () => bridge.reload(id),
        "reload-hard": () => bridge.reloadHard?.(id),
        "toggle-devtools": () => bridge.toggleDevTools?.(id),
        print: () => bridge.print?.(id),
        bookmark: () => bridge.bookmark?.(id),
        "zoom-in": () => bridge.zoom?.(id, 1),
        "zoom-out": () => bridge.zoom?.(id, -1),
        "zoom-reset": () => bridge.zoom?.(id, 0),
      };
      void Promise.resolve(calls[action]?.()).catch(() => {});
    }
    window.addEventListener("keydown", handleBrowserChromeShortcut, true);
    return () => window.removeEventListener("keydown", handleBrowserChromeShortcut, true);
  }, [active, bridge, state.loading, state.ready]);

  useEffect(() => {
    if (!addressFocused) setAddressDraft(displayBrowserUrl(state.url));
  }, [addressFocused, state.url]);

  // Apply an inline completion's selection after React renders the value.
  useEffect(() => {
    if (!pendingSelection || !addressRef.current) return;
    if (addressRef.current.value === pendingSelection.value) {
      addressRef.current.setSelectionRange?.(pendingSelection.selectionStart, pendingSelection.selectionEnd);
    }
    setPendingSelection(null);
  }, [pendingSelection]);

  useEffect(() => {
    // Rem's tabs never take keyboard focus on their own.
    if (agentSessionId) return undefined;
    if (!active || state.error || !state.id || (!state.ready && !focusLocationOnCreatePendingRef.current)) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const id = sessionIdRef.current;
      const webview = webviewRef.current;
      if (focusLocationOnCreatePendingRef.current) {
        focusLocationOnCreatePendingRef.current = false;
        addressRef.current?.focus();
        addressRef.current?.select?.();
        return;
      }
      if (!state.ready) return;
      if (!id || !bridge?.focus) {
        webview?.focus?.();
        return;
      }
      void bridge.focus(id).catch(() => {
        if (activeRef.current && sessionIdRef.current === id) webview?.focus?.();
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, agentSessionId, bridge, state.error, state.id, state.ready]);

  useEffect(() => {
    if (webviewRef.current) webviewRef.current.style.pointerEvents = dragActive ? "none" : "";
  }, [dragActive, state.id]);

  useEffect(() => {
    const open = event => {
      if (!active || (event.detail?.clientId && event.detail.clientId !== clientId)) return;
      openFind();
    };
    window.addEventListener("gofer:find-page", open);
    return () => window.removeEventListener("gofer:find-page", open);
  }, [active, clientId]);

  useEffect(() => {
    if (!findOpen || !state.ready) return;
    const id = sessionIdRef.current;
    if (!id || !bridge?.find) return;
    setFindError("");
    void bridge.find(id, findQuery).catch(error => setFindError(error.message));
  }, [bridge, findQuery, findOpen, state.ready, state.url]);

  // Address bar suggestions from history, bookmarks and open tabs.
  useEffect(() => {
    if (!addressFocused || !bridge?.library) {
      setSuggestions([]);
      return undefined;
    }
    const text = typed.text;
    if (!text.trim()) {
      setSuggestions([]);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void bridge.library("suggest", { text }).then((result) => {
        if (cancelled) return;
        setSuggestions(suggestionRows({ text, suggestions: result?.suggestions || [], openTabs, currentPath: clientId }));
        setActiveSuggestion(-1);
        // Complete inline only while typing forward, never after a deletion.
        if (result?.inline && !typed.deleting) {
          const completed = addressWithCompletion(text, result.inline);
          if (completed.value !== text) {
            setAddressDraft((current) => (current === text ? completed.value : current));
            setPendingSelection(completed);
          }
        }
      }).catch(() => {});
    }, 80);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // Open tabs are read when results arrive; they do not restart the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addressFocused, bridge, typed]);

  function nextMatch(forward = true) {
    if (!findQuery || !sessionIdRef.current) return;
    void bridge.find(sessionIdRef.current, findQuery, { forward, newSearch: false }).catch(error => setFindError(error.message));
  }
  function closeFind() {
    setFindOpen(false);
    const id = sessionIdRef.current;
    if (id) void bridge.stopFind?.(id).catch(error => setFindError(error.message));
    webviewRef.current?.focus();
    if (id) void bridge.focus?.(id).catch(() => {});
  }

  function run(action, ...args) {
    const id = sessionIdRef.current;
    if (!id || !state.ready || !bridge?.[action]) return undefined;
    return Promise.resolve(bridge[action](id, ...args)).catch(() => {});
  }

  function go(value) {
    const id = sessionIdRef.current;
    if (!id || !state.ready || !String(value ?? "").trim()) return;
    addressRef.current?.blur();
    setSuggestions([]);
    setState((current) => ({ ...current, error: "", loadError: null }));
    void bridge.navigate(id, browserAddress(value, searchUrl), { typed: true }).catch((error) => {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Could not open that address",
      }));
    });
  }

  function navigate(event) {
    event.preventDefault();
    if (activeSuggestion >= 0 && suggestions[activeSuggestion]) {
      pickSuggestion(suggestions[activeSuggestion]);
      return;
    }
    go(addressDraft);
  }

  function pickSuggestion(row) {
    if (row.kind === "tab" && row.path && onActivateTab) {
      addressRef.current?.blur();
      setSuggestions([]);
      onActivateTab(row.path);
      return;
    }
    go(row.value);
  }

  function handleAddressChange(event) {
    const value = event.target.value;
    const inputType = event.nativeEvent?.inputType || "";
    setTyped({ text: value, deleting: inputType.startsWith("delete") });
    setAddressDraft(value);
  }

  function handleAddressKeyDown(event) {
    if (event.key === "ArrowDown" && suggestions.length) {
      event.preventDefault();
      setActiveSuggestion((index) => (index + 1) % suggestions.length);
    } else if (event.key === "ArrowUp" && suggestions.length) {
      event.preventDefault();
      setActiveSuggestion((index) => (index <= 0 ? suggestions.length - 1 : index - 1));
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (suggestions.length) {
        setSuggestions([]);
        setActiveSuggestion(-1);
      } else {
        setAddressDraft(displayBrowserUrl(state.url));
        addressRef.current?.blur();
        webviewRef.current?.focus?.();
      }
    }
  }

  async function openHistoryMenu(direction) {
    const id = sessionIdRef.current;
    if (!id || !bridge?.historyEntries) return;
    try {
      const history = await bridge.historyEntries(id);
      const entries = historyMenuEntries(history, direction);
      if (entries.length) setHistoryMenu({ direction, entries });
    } catch { /* No history to show. */ }
  }

  function longPress(direction) {
    let timer = 0;
    return {
      onContextMenu: (event) => { event.preventDefault(); void openHistoryMenu(direction); },
      onPointerDown: () => { timer = window.setTimeout(() => void openHistoryMenu(direction), 500); },
      onPointerUp: () => window.clearTimeout(timer),
      onPointerLeave: () => window.clearTimeout(timer),
    };
  }

  async function openSiteInfo() {
    const id = sessionIdRef.current;
    if (!id || !bridge?.siteInfo) return;
    try { setSiteInfo(await bridge.siteInfo(id)); } catch { /* View closed. */ }
  }

  async function askRem(selection = "") {
    const id = sessionIdRef.current;
    if (!id || !onAskRem) return;
    let context = { url: state.url, title: state.title, selection, text: "" };
    try { context = await bridge.pageContext(id, selection); } catch { /* Use what the tab knows. */ }
    onAskRem(context);
  }
  askRemRef.current = askRem;

  function respondToPrompt(prompt, response) {
    const id = sessionIdRef.current;
    if (!id || !bridge?.respondPrompt) return;
    void bridge.respondPrompt(id, prompt.requestId, response).catch(() => {});
  }

  const prompt = state.prompts?.[0] || null;
  const agent = state.agent || (agentSessionId ? { label: agentLabel || "Rem", shared: false, log: [] } : null);
  const canUseWebFeatures = state.ready && !isPreview && /^https?:/i.test(String(state.url || ""));
  const rows = useMemo(() => (addressFocused ? suggestions : []), [addressFocused, suggestions]);
  const menuItems = [
    ...(!isRemTab && onReopenClosedTab ? [{ label: "Reopen closed tab", icon: Undo2, shortcut: "Ctrl+Shift+T", run: () => onReopenClosedTab() }] : []),
    { label: "History", icon: History, run: () => setLibraryView("history") },
    { label: "Bookmarks", icon: Bookmark, run: () => setLibraryView("bookmarks") },
    { label: "Downloads", icon: Download, run: () => setLibraryView("downloads") },
    { label: "Site data and clearing", icon: Cookie, run: () => setLibraryView("data") },
    { separator: true },
    { label: "Zoom out", icon: Minus, run: () => run("zoom", -1), disabled: !state.ready },
    { label: `Reset zoom (${formatZoom(state.zoom)})`, icon: Search, run: () => run("zoom", 0), disabled: !state.ready || !state.zoom || state.zoom === 1 },
    { label: "Zoom in", icon: Plus, run: () => run("zoom", 1), disabled: !state.ready },
    { separator: true },
    { label: "Print…", icon: Printer, run: () => run("print"), disabled: !state.ready },
    { label: "Save as PDF…", icon: FileDown, run: () => run("savePdf"), disabled: !state.ready },
    { label: "Save page as…", icon: Save, run: () => run("savePage"), disabled: !state.ready },
    { label: "Hard reload", icon: RotateCw, run: () => run("reloadHard"), disabled: !state.ready },
    { label: "Developer tools", icon: Code, run: () => run("toggleDevTools"), disabled: !state.ready },
    { separator: true },
    { label: state.muted ? "Unmute tab" : "Mute tab", icon: state.muted ? Volume2 : VolumeX, run: () => run("mute", !state.muted), disabled: !state.ready },
    ...(onAskRem && !isPreview ? [{ label: "Ask Rem about this page", icon: MessageSquare, run: () => void askRem(""), disabled: !state.ready }] : []),
    ...(canShareWithRem && !isRemTab && !isPreview && !agent ? [{ label: "Share this tab with Rem…", icon: Share2, run: () => onShareWithRem?.({ id: sessionIdRef.current, url: state.url, title: state.title }), disabled: !canUseWebFeatures }] : []),
  ];

  return (
    <section
      ref={sectionRef}
      data-integrated-browser=""
      className={`flex min-h-0 flex-1 flex-col bg-white ${fullscreen ? "fixed inset-0 z-[200]" : "relative"}`}
      aria-label="Integrated browser"
    >
      <div className={`${fullscreen ? "hidden" : "flex"} h-10 shrink-0 items-center gap-1 border-b border-line bg-slate-50 px-2`}>
        <span {...longPress(-1)} className="contents">
          <BrowserButton disabled={!state.canGoBack} label="Back" onClick={() => run("back")}><ArrowLeft size={14} /></BrowserButton>
        </span>
        <span {...longPress(1)} className="contents">
          <BrowserButton disabled={!state.canGoForward} label="Forward" onClick={() => run("forward")}><ArrowRight size={14} /></BrowserButton>
        </span>
        <BrowserButton
          label={state.loading ? "Stop loading" : "Reload"}
          onClick={(event) => run(state.loading ? "stop" : event?.shiftKey ? "reloadHard" : "reload")}
        >{state.loading ? <X size={14} /> : <RefreshCw size={14} />}</BrowserButton>
        <form className="relative mx-1 flex min-w-0 flex-1" onSubmit={navigate}>
          <div className="relative min-w-0 flex-1">
            {state.loading ? (
              <span className="pointer-events-none absolute left-2.5 top-1/2 z-10 flex -translate-y-1/2 text-muted">
                <Loader2 className="animate-spin" size={12} />
              </span>
            ) : <SecurityChip security={state.security} onClick={() => void openSiteInfo()} />}
            <input
              ref={addressRef}
              aria-label="Browser address"
              aria-autocomplete="both"
              aria-controls={rows.length ? suggestionsId : undefined}
              aria-activedescendant={activeSuggestion >= 0 && rows.length ? `${suggestionsId}-${activeSuggestion}` : undefined}
              aria-expanded={rows.length > 0}
              role="combobox"
              className={`h-7 w-full rounded-md border border-line bg-white pr-16 text-xs text-ink outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/10 ${state.security?.level === "insecure" || state.security?.level === "warning" || state.security?.level === "error" ? "pl-[4.5rem] sm:pl-[5.5rem]" : "pl-8"}`}
              spellCheck={false}
              value={addressDraft}
              onBlur={() => { setAddressFocused(false); setSuggestions([]); setActiveSuggestion(-1); }}
              onChange={handleAddressChange}
              onFocus={(event) => {
                setAddressFocused(true);
                setTyped({ text: "", deleting: false });
                event.currentTarget.select();
              }}
              onKeyDown={handleAddressKeyDown}
            />
            <span className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
              {state.zoom && Math.abs(state.zoom - 1) > 0.001 ? (
                <button aria-label={`Zoom ${formatZoom(state.zoom)}. Reset zoom`} className="rounded px-1 text-[10px] font-semibold text-muted hover:bg-slate-100" title="Reset zoom" type="button" onClick={() => run("zoom", 0)}>{formatZoom(state.zoom)}</button>
              ) : null}
              {state.audible || state.muted ? (
                <button aria-label={state.muted ? "Unmute tab" : "Mute tab"} className="grid h-5 w-5 place-items-center rounded text-muted hover:bg-slate-100" title={state.muted ? "Unmute tab" : "Mute tab"} type="button" onClick={() => run("mute", !state.muted)}>
                  {state.muted ? <VolumeX size={12} /> : <Volume2 size={12} />}
                </button>
              ) : null}
              {state.profile === "user" && canUseWebFeatures ? (
                <button aria-label={state.bookmarked ? "Remove bookmark" : "Bookmark this page"} aria-pressed={Boolean(state.bookmarked)} className="grid h-5 w-5 place-items-center rounded text-muted hover:bg-slate-100" title={state.bookmarked ? "Remove bookmark" : "Bookmark this page (Ctrl+D)"} type="button" onClick={() => run("bookmark")}>
                  <Star className={state.bookmarked ? "fill-brand text-brand" : ""} size={12} />
                </button>
              ) : null}
            </span>
            <AddressSuggestions id={suggestionsId} rows={rows} activeIndex={activeSuggestion} onHover={setActiveSuggestion} onPick={pickSuggestion} />
          </div>
        </form>
        {agent ? (
          <RemChip
            agent={agent}
            signedIn={Boolean(state.remSignIn)}
            onPause={(paused) => run("pauseRem", paused)}
            onStop={() => run("stopSharing")}
            onTakeOver={() => run("takeOver")}
          />
        ) : null}
        <BrowserButton label={activeDownloads ? `Downloads (${activeDownloads} need attention or in progress)` : "Downloads"} onClick={() => setLibraryView((view) => (view === "downloads" ? "" : "downloads"))}>
          <span className="relative"><Download size={14} />{activeDownloads ? <span className="absolute -right-1.5 -top-1.5 grid h-3 min-w-3 place-items-center rounded-full bg-brand px-0.5 text-[8px] font-bold text-white">{activeDownloads}</span> : null}</span>
        </BrowserButton>
        <BrowserButton
          disabled={!state.canOpenExternal}
          label="Open in default browser"
          onClick={() => run("openExternal")}
        ><ExternalLink size={14} /></BrowserButton>
        <BrowserButton disabled={!state.ready} label="Find in Page" onClick={openFind}><Search size={14} /></BrowserButton>
        <div className="relative">
          <BrowserButton label="Browser menu" expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}><EllipsisVertical size={14} /></BrowserButton>
          {menuOpen ? <BrowserMenu items={menuItems} onClose={() => setMenuOpen(false)} /> : null}
        </div>
        {showModeToggle ? (
          <>
            {showDiffButton ? (
              <BrowserButton label="Compare HTML with HEAD" onClick={onShowDiff}>
                <GitCompareArrows size={14} />
              </BrowserButton>
            ) : null}
            <HtmlModeToggle editing={editing} onModeChange={onModeChange} />
          </>
        ) : null}
      </div>
      {historyMenu ? (
        <HistoryMenu
          entries={historyMenu.entries}
          side={historyMenu.direction < 0 ? "left-2" : "left-10"}
          onClose={() => setHistoryMenu(null)}
          onPick={(index) => { setHistoryMenu(null); void run("goToIndex", index); }}
        />
      ) : null}
      {siteInfo ? (
        <SiteInfoPopover
          info={siteInfo}
          onClose={() => setSiteInfo(null)}
          onPermission={(key, value) => void bridge.sitePermission(sessionIdRef.current, key, value).then(setSiteInfo).catch(() => {})}
          onClearData={() => void bridge.clearSiteData(sessionIdRef.current).then(setSiteInfo).catch(() => {})}
          onResetZoom={() => { void run("zoom", 0); setSiteInfo((current) => (current ? { ...current, zoom: 1 } : current)); }}
        />
      ) : null}
      {libraryView ? (
        <LibraryPanel
          bridge={bridge}
          view={libraryView}
          onClose={() => setLibraryView("")}
          onOpenUrl={(url) => { setLibraryView(""); go(url); }}
          onViewChange={setLibraryView}
        />
      ) : null}
      {!fullscreen ? <PromptBar prompt={prompt} onRespond={(response) => respondToPrompt(prompt, response)} /> : null}
      {!fullscreen ? (
        <BlockedPopupsBar
          popups={state.blockedPopups}
          onAllow={() => run("allowPopups")}
          onDismiss={() => run("dismissBlockedPopups")}
          onOpen={(index) => run("openBlockedPopup", index)}
        />
      ) : null}
      {findOpen ? <div role="search" aria-label="Find in page" className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-slate-50 px-3 py-2">
        <input ref={findInputRef} aria-label="Find text in page" className="min-w-0 rounded border border-line bg-white px-2 py-1 text-xs" value={findQuery} onChange={event => setFindQuery(event.target.value)} onKeyDown={event => {
          if (event.key === "Escape") { event.preventDefault(); closeFind(); }
          if (event.key === "Enter") { event.preventDefault(); nextMatch(!event.shiftKey); }
        }} />
        <span role="status" className="text-xs text-muted">{findError || (findQuery ? `${state.findResult?.activeMatchOrdinal || 0} of ${state.findResult?.matches || 0}` : "Enter text to find")}</span>
        <BrowserButton disabled={!state.findResult?.matches} label="Previous match" onClick={() => nextMatch(false)}><ArrowLeft size={14} /></BrowserButton>
        <BrowserButton disabled={!state.findResult?.matches} label="Next match" onClick={() => nextMatch()}><ArrowRight size={14} /></BrowserButton>
        <BrowserButton label="Close page find" onClick={closeFind}><X size={14} /></BrowserButton>
      </div> : null}
      {agent?.dialog ? (
        <p role="status" className="shrink-0 border-b border-line bg-amber-50 px-3 py-1.5 text-xs text-ink">
          <Bot aria-hidden="true" className="mr-1 inline text-amber-800" size={12} />The page is showing a {agent.dialog.type} for {agent.label || "Rem"} to answer: “{agent.dialog.message}”
        </p>
      ) : null}
      <div
        ref={containerRef}
        className="relative min-h-0 flex-1 bg-white"
        style={dragActive ? { pointerEvents: "none" } : undefined}
      >
        {fullscreen ? <p className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded bg-black/70 px-3 py-1 text-xs text-white">Press Esc to exit full screen</p> : null}
        {discarded ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-white text-xs text-muted">This tab was paused to save memory. It reloads when you return.</div>
        ) : (
          <PageOverlay
            state={state}
            onBack={() => (state.canGoBack ? run("back") : run("home"))}
            onDismissUnresponsive={() => setState((current) => ({ ...current, unresponsive: false }))}
            onProceed={() => run("allowCertificate")}
            onReload={() => run("reload")}
            onRetry={() => run("reload")}
          />
        )}
      </div>
    </section>
  );
}

function BrowserButton({ children, disabled = false, expanded, label, onClick }) {
  return (
    <button
      aria-expanded={expanded}
      aria-label={label}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent"
      disabled={disabled}
      title={label}
      type="button"
      onClick={onClick}
    >{children}</button>
  );
}

// Focuses the first item once on open. Parents pass inline `onClose` callbacks
// and re-render on every tab state push, so `onClose` must not be an effect
// dependency or focus would jump back to the first item mid-navigation.
// `includeParent` keeps clicks on the menu's trigger from closing and
// immediately reopening it.
function usePopupMenu(menuRef, onClose, { includeParent = false } = {}) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    menuRef.current?.querySelector("[role=menuitem]:not([disabled])")?.focus();
    const close = (event) => {
      const inside = includeParent ? menuRef.current?.parentElement : menuRef.current;
      if (!inside?.contains(event.target)) onCloseRef.current();
    };
    window.addEventListener("pointerdown", close, true);
    return () => window.removeEventListener("pointerdown", close, true);
  }, [menuRef, includeParent]);
  return (event) => {
    const enabled = [...(menuRef.current?.querySelectorAll("[role=menuitem]:not([disabled])") || [])];
    if (!enabled.length) return;
    const index = enabled.indexOf(document.activeElement);
    const next = {
      ArrowDown: (index + 1) % enabled.length,
      ArrowUp: index <= 0 ? enabled.length - 1 : index - 1,
      Home: 0,
      End: enabled.length - 1,
    }[event.key];
    if (next !== undefined) { event.preventDefault(); enabled[next].focus(); return; }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCloseRef.current({ restoreFocus: true });
    }
  };
}

const MENU_ITEM_CLASS = "browser-menu-item flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink disabled:opacity-40";

function BrowserMenu({ items, onClose }) {
  const menuRef = useRef(null);
  const handleKeyDown = usePopupMenu(menuRef, (options) => {
    if (options?.restoreFocus) menuRef.current?.parentElement?.querySelector("button[aria-expanded]")?.focus();
    onClose();
  }, { includeParent: true });
  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="Browser menu"
      className="absolute right-0 top-8 z-50 w-60 rounded-md border border-line bg-white py-1 text-xs text-ink shadow-lg"
      onKeyDown={handleKeyDown}
    >
      {items.map((item, index) => (item.separator
        ? <div key={`separator-${index}`} role="separator" className="my-1 border-t border-line" />
        : (
          <button
            key={item.label}
            className={MENU_ITEM_CLASS}
            disabled={item.disabled}
            tabIndex={-1}
            role="menuitem"
            type="button"
            onClick={() => { onClose(); item.run(); }}
          >
            {item.icon ? <item.icon aria-hidden="true" className="shrink-0 text-muted" size={12} /> : null}
            <span className="flex-1">{item.label}</span>
            {item.shortcut ? <span className="text-[10px] text-muted">{item.shortcut}</span> : null}
          </button>
        )))}
    </div>
  );
}

function HistoryMenu({ entries, side, onClose, onPick }) {
  const ref = useRef(null);
  const handleKeyDown = usePopupMenu(ref, onClose);
  return (
    <div ref={ref} role="menu" aria-label="Session history" className={`absolute top-9 z-50 w-72 rounded-md border border-line bg-white py-1 text-xs shadow-lg ${side}`}
      onKeyDown={handleKeyDown}>
      {entries.map((entry) => (
        <button key={entry.index} role="menuitem" className={MENU_ITEM_CLASS} tabIndex={-1} type="button" onClick={() => onPick(entry.index)}>
          <span className="min-w-0 flex-1 truncate">{entry.title || entry.url}</span>
        </button>
      ))}
    </div>
  );
}

export function HtmlModeToggle({ editing, onModeChange }) {
  return (
    <div aria-label="HTML view mode" className="ml-1 flex items-center rounded-md border border-line bg-white p-0.5" role="group">
      <button
        aria-label="Browse HTML"
        aria-pressed={!editing}
        className={`h-6 rounded px-2 text-[10px] font-semibold transition ${
          !editing ? "bg-slate-100 text-ink" : "text-muted hover:text-ink"
        }`}
        title="Browse HTML"
        type="button"
        onClick={() => onModeChange?.("preview")}
      >Browse</button>
      <button
        aria-label="Edit HTML"
        aria-pressed={editing}
        className={`h-6 rounded px-2 text-[10px] font-semibold transition ${
          editing ? "bg-slate-100 text-ink" : "text-muted hover:text-ink"
        }`}
        title="Edit HTML"
        type="button"
        onClick={() => onModeChange?.("edit")}
      >Edit</button>
    </div>
  );
}

export { browserChromeShortcutAction };

export function displayBrowserUrl(value) {
  return value === "about:blank" ? "" : String(value ?? "");
}

export function browserAddress(value, searchUrl) {
  const input = String(value ?? "").trim();
  if (!input || browserInputLooksLikeUrl(input)) return input;
  const template = String(searchUrl ?? "");
  if (!template.includes("{query}")) return input;
  return template.replace("{query}", encodeURIComponent(input));
}

function browserInputLooksLikeUrl(input) {
  return /^(?:file\/\/|\/|\\\\)/i.test(input)
    || /^[a-z][a-z\d+.-]*:/i.test(input)
    || /^(?:localhost|127\.0\.0\.1|\[?::1\]?)(?::\d+)?(?:[/?#]|$)/i.test(input)
    || /^[\w.-]+\.[a-z]{2,}(?::\d+)?(?:[/?#]|$)/i.test(input);
}

function attachBrowserWebview(bridge, sessionId, container, webviewRef, src, partition, onUpdate, clientId = "", onFullscreen = () => {}) {
  if (!container || typeof document.createElement !== "function") return;
  const element = document.createElement("webview");
  element.setAttribute("partition", partition || USER_PARTITION);
  element.setAttribute("plugins", "true");
  // The main process decides what each pop-up becomes; without this
  // attribute window.open and target=_blank would silently do nothing.
  element.setAttribute("allowpopups", "");
  if (bridge.preloadPath) element.setAttribute("preload", bridge.preloadPath);
  element.setAttribute("src", src || "about:blank");
  element.style.position = "absolute";
  element.style.inset = "0";
  element.style.width = "100%";
  element.style.height = "100%";
  const emit = (partialState) => {
    if (webviewRef.current !== element) return;
    let history = {};
    try {
      history = { canGoBack: element.canGoBack(), canGoForward: element.canGoForward() };
    } catch {
      // History methods are unavailable until the webview attaches.
    }
    onUpdate?.({ ...history, ...partialState });
  };
  element.addEventListener("did-start-loading", () => emit({ error: "", loading: true }));
  element.addEventListener("did-stop-loading", () => emit({ loading: false }));
  const isDisplayableUrl = (url) => typeof url === "string" && !url.startsWith("data:");
  element.addEventListener("did-navigate", (event) => {
    emit(isDisplayableUrl(event.url) ? { favicon: "", title: "", url: event.url } : {});
  });
  element.addEventListener("did-navigate-in-page", (event) => {
    if (event.isMainFrame && isDisplayableUrl(event.url)) emit({ url: event.url });
  });
  element.addEventListener("page-title-updated", (event) => emit({ title: event.title }));
  element.addEventListener("page-favicon-updated", (event) => {
    emit({ favicon: browserFaviconUrl(event.favicons) });
  });
  element.addEventListener("did-fail-load", (event) => {
    if (!event.isMainFrame || event.errorCode === -3) return;
    emit({ error: `${event.errorDescription}: ${event.validatedURL}`, loading: false });
  });
  element.addEventListener("enter-html-full-screen", () => onFullscreen(true));
  element.addEventListener("leave-html-full-screen", () => onFullscreen(false));
  const handleDomReady = () => {
    element.removeEventListener("dom-ready", handleDomReady);
    if (webviewRef.current !== element || !bridge.adopt) return;
    void bridge.adopt(sessionId, element.getWebContentsId(), clientId)
      .then((adoptedState) => {
        if (adoptedState && webviewRef.current === element) onUpdate?.(adoptedState);
      })
      .catch((error) => {
        if (webviewRef.current !== element) return;
        console.error("Integrated browser adopt failed:", error);
        emit({
          error: error instanceof Error ? error.message : "Could not attach the browser view.",
          loading: false,
        });
      });
  };
  element.addEventListener("dom-ready", handleDomReady);
  webviewRef.current = element;
  container.appendChild(element);
}

export function browserSessionEventMatches(sessionId, event) {
  return Boolean(sessionId && event?.id === sessionId);
}

export function browserFaviconUrl(favicons) {
  if (!Array.isArray(favicons)) return "";
  return favicons.find((value) => /^(?:https?:|file:|data:image\/)/i.test(String(value ?? ""))) || "";
}

export function isIntegratedBrowserShortcut(event) {
  if (event.repeat || !event.altKey || event.shiftKey) return false;
  if (!event.ctrlKey && !event.metaKey) return false;
  return event.code === "Slash" || event.key === "/";
}
