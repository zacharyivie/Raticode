import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  GitCompareArrows,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";

export default function IntegratedBrowser({
  active,
  applicationKeybindings = {},
  clientId,
  dragActive = false,
  editing = false,
  focusLocationOnCreate = false,
  initialUrl = "about:blank",
  localPath = "",
  onClose,
  onCycleTab,
  onNewTab,
  onShowDiff,
  onModeChange,
  onStateChange,
  openBrowserBinding = "Mod+Alt+Slash",
  searchUrl = "https://www.google.com/search?q={query}",
  showDiffButton = false,
  showModeToggle = false,
}) {
  const addressRef = useRef(null);
  const activeRef = useRef(active);
  const focusLocationOnCreatePendingRef = useRef(focusLocationOnCreate);
  const initialUrlRef = useRef(initialUrl);
  const initialApplicationKeybindingsRef = useRef(applicationKeybindings);
  const initialOpenBrowserBindingRef = useRef(openBrowserBinding);
  const onCloseRef = useRef(onClose);
  const onCycleTabRef = useRef(onCycleTab);
  const onModeChangeRef = useRef(onModeChange);
  const onNewTabRef = useRef(onNewTab);
  const onStateChangeRef = useRef(onStateChange);
  const containerRef = useRef(null);
  const sessionIdRef = useRef("");
  const webviewRef = useRef(null);
  const [addressDraft, setAddressDraft] = useState(displayBrowserUrl(initialUrl));
  const [addressFocused, setAddressFocused] = useState(false);
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

  useEffect(() => {
    activeRef.current = active;
    onCloseRef.current = onClose;
    onCycleTabRef.current = onCycleTab;
    onModeChangeRef.current = onModeChange;
    onNewTabRef.current = onNewTab;
    onStateChangeRef.current = onStateChange;
  }, [active, onClose, onCycleTab, onModeChange, onNewTab, onStateChange]);

  useEffect(() => {
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
      if (command?.action === "focus-location") {
        addressRef.current?.focus();
        addressRef.current?.select();
      }
      if (command?.action === "close") onCloseRef.current?.();
      if (command?.action === "next-tab") onCycleTabRef.current?.(1);
      if (command?.action === "previous-tab") onCycleTabRef.current?.(-1);
      if (command?.action === "new-tab") onNewTabRef.current?.();
      if (command?.action === "edit-local-html") onModeChangeRef.current?.("edit");
    });
    bridge.create({
      applicationKeybindings: initialApplicationKeybindingsRef.current,
      clientId,
      openBrowserBinding: initialOpenBrowserBindingRef.current,
      path: localPath,
      url: localPath ? "" : initialUrlRef.current,
    })
      .then((nextState) => {
        if (disposed) {
          if (nextState?.id) void bridge.close(nextState.id).catch(() => {});
          return;
        }
        createdId = nextState?.id ?? "";
        sessionIdRef.current = createdId;
        attachBrowserWebview(bridge, createdId, containerRef.current, webviewRef, nextState?.src, (partialState) => {
          if (disposed || sessionIdRef.current !== createdId) return;
          setState((current) => ({ ...current, ...partialState }));
          onStateChangeRef.current?.(partialState);
        });
        const cleanState = { ...(nextState ?? {}) };
        delete cleanState.src;
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
  }, [bridge, clientId, localPath]);

  useEffect(() => {
    const id = sessionIdRef.current;
    if (!id || !bridge?.setPreferences) return;
    void bridge.setPreferences(id, { applicationKeybindings, openBrowserBinding }).catch(() => {});
  }, [applicationKeybindings, bridge, openBrowserBinding, state.id]);

  useEffect(() => {
    if (!active) return undefined;
    function handleBrowserChromeShortcut(event) {
      const action = browserChromeShortcutAction(event, bridge?.platform);
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      if (action === "focus-location") {
        addressRef.current?.focus();
        addressRef.current?.select();
        return;
      }
      const id = sessionIdRef.current;
      if (!id || !state.ready || !bridge?.[action]) return;
      void bridge[action](id).catch(() => {});
    }
    window.addEventListener("keydown", handleBrowserChromeShortcut, true);
    return () => window.removeEventListener("keydown", handleBrowserChromeShortcut, true);
  }, [active, bridge, state.ready]);

  useEffect(() => {
    if (!addressFocused) setAddressDraft(displayBrowserUrl(state.url));
  }, [addressFocused, state.url]);

  useEffect(() => {
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
  }, [active, bridge, state.error, state.id, state.ready]);

  useEffect(() => {
    if (webviewRef.current) webviewRef.current.style.pointerEvents = dragActive ? "none" : "";
  }, [dragActive, state.id]);

  function run(action) {
    const id = sessionIdRef.current;
    if (!id || !state.ready || !bridge?.[action]) return;
    void bridge[action](id).catch(() => {});
  }

  function navigate(event) {
    event.preventDefault();
    const id = sessionIdRef.current;
    if (!id || !state.ready || !addressDraft.trim()) return;
    addressRef.current?.blur();
    setState((current) => ({ ...current, error: "" }));
    void bridge.navigate(id, browserAddress(addressDraft, searchUrl)).catch((error) => {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Could not open that address",
      }));
    });
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white" aria-label="Integrated browser">
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line bg-slate-50 px-2">
        <BrowserButton
          disabled={!state.canGoBack}
          label="Back"
          onClick={() => run("back")}
        ><ArrowLeft size={14} /></BrowserButton>
        <BrowserButton
          disabled={!state.canGoForward}
          label="Forward"
          onClick={() => run("forward")}
        ><ArrowRight size={14} /></BrowserButton>
        <BrowserButton
          label={state.loading ? "Stop loading" : "Reload"}
          onClick={() => run(state.loading ? "stop" : "reload")}
        >{state.loading ? <X size={14} /> : <RefreshCw size={14} />}</BrowserButton>
        <form className="mx-1 flex min-w-0 flex-1" onSubmit={navigate}>
          <div className="relative min-w-0 flex-1">
            {state.loading ? (
              <span className="absolute left-2.5 top-1/2 flex -translate-y-1/2 text-muted">
                <Loader2 className="animate-spin" size={12} />
              </span>
            ) : null}
            <input
              ref={addressRef}
              aria-label="Browser address"
              className="h-7 w-full rounded-md border border-line bg-white pl-8 pr-3 text-xs text-ink outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/10"
              spellCheck={false}
              value={addressDraft}
              onBlur={() => setAddressFocused(false)}
              onChange={(event) => setAddressDraft(event.target.value)}
              onFocus={(event) => {
                setAddressFocused(true);
                event.currentTarget.select();
              }}
            />
          </div>
        </form>
        <BrowserButton
          disabled={!/^https?:/i.test(state.url ?? "")}
          label="Open in default browser"
          onClick={() => run("openExternal")}
        ><ExternalLink size={14} /></BrowserButton>
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
      <div
        ref={containerRef}
        className="relative min-h-0 flex-1 bg-white"
        style={dragActive ? { pointerEvents: "none" } : undefined}
      >
        {state.error ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-white px-8 text-center">
            <div>
              <p className="text-sm font-semibold text-ink">Could not display this page</p>
              <p className="mt-1 max-w-lg text-xs leading-5 text-muted">{state.error}</p>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function BrowserButton({ children, disabled = false, label, onClick }) {
  return (
    <button
      aria-label={label}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted transition hover:bg-slate-100 hover:text-ink disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent"
      disabled={disabled}
      title={label}
      type="button"
      onClick={onClick}
    >{children}</button>
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

export function browserChromeShortcutAction(event, platform = "") {
  if (event.defaultPrevented || event.target?.closest?.("[data-terminal-workspace], .xterm")) return null;
  if (event.repeat) return "";
  const key = String(event.key ?? "").toLowerCase();
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "d") {
    return "focus-location";
  }
  const primary = platform === "darwin" ? event.metaKey : event.ctrlKey;
  if (primary && !event.altKey && !event.shiftKey && key === "r") return "reload";
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "arrowleft") {
    return "back";
  }
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key === "arrowright") {
    return "forward";
  }
  return "";
}

function attachBrowserWebview(bridge, sessionId, container, webviewRef, src, onUpdate) {
  if (!container || typeof document.createElement !== "function") return;
  const element = document.createElement("webview");
  element.setAttribute("partition", "persist:raticode-browser");
  element.setAttribute("plugins", "true");
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
  const handleDomReady = () => {
    element.removeEventListener("dom-ready", handleDomReady);
    if (webviewRef.current !== element || !bridge.adopt) return;
    void bridge.adopt(sessionId, element.getWebContentsId())
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
