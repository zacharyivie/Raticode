import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AppWindow } from "lucide-react";
import { BrowserHostContext, MOVE_NOTE, mirrorStudioStyles } from "../lib/browserHost.js";

// Must match FRAME_NAME in electron/browser-window.cjs.
const BROWSER_WINDOW_NAME = "raticode-browser-window";
const ROOT_ID = "raticode-browser-root";
const BUTTON = "h-8 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink transition hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand";

// Holds the Browser plugin and moves it between Raticode's window and a window
// of its own. The Browser's components stay mounted in Raticode's page the
// whole time (a portal into one node that changes windows), so tabs, their
// sessions, Rem's hold on them and every setting stay as they are.
export default function BrowserWindowHost({ children, onPoppedOutChange }) {
  const bridge = typeof window === "undefined" ? null : window.goferBrowser;
  const available = Boolean(bridge?.browserWindow);
  const [hostNode] = useState(() => {
    const node = document.createElement("div");
    node.className = "flex min-h-0 min-w-0 flex-1 flex-col";
    node.setAttribute("data-testid", "browser-host");
    return node;
  });
  const slotRef = useRef(null);
  const popupRef = useRef(null);
  const stopMirrorRef = useRef(null);
  const entriesRef = useRef(new Set());
  const movingRef = useRef(false);
  const [place, setPlace] = useState(() => ({ poppedOut: false, key: 0, win: typeof window === "undefined" ? undefined : window, doc: typeof document === "undefined" ? undefined : document }));
  const placeRef = useRef(place);
  placeRef.current = place;
  const [problem, setProblem] = useState("");
  const onPoppedOutChangeRef = useRef(onPoppedOutChange);
  onPoppedOutChangeRef.current = onPoppedOutChange;

  useLayoutEffect(() => {
    const slot = slotRef.current;
    if (!place.poppedOut && slot && hostNode.parentNode !== slot) slot.appendChild(hostNode);
  }, [hostNode, place.poppedOut]);

  useEffect(() => {
    onPoppedOutChangeRef.current?.(place.poppedOut);
  }, [place.poppedOut]);

  const register = useCallback((entry) => {
    entriesRef.current.add(entry);
    return () => entriesRef.current.delete(entry);
  }, []);

  // The DOM changes windows. Raticode (chromium) pages are views that follow
  // their slots there, still running; Firefox, Chrome and Edge tabs draw their
  // frames again in the new window (when place.key changes).
  const move = useCallback(async (parent, next) => {
    const entries = [...entriesRef.current];
    for (const entry of entries) {
      try { entry.detach?.(); } catch { /* A tab that is closing. */ }
    }
    parent.appendChild(hostNode);
    setPlace((current) => ({ ...next, key: current.key + 1 }));
  }, [hostNode]);

  const popOut = useCallback(async () => {
    if (placeRef.current.poppedOut) {
      void Promise.resolve(bridge?.browserWindow?.("focus")).catch(() => {});
      return;
    }
    if (movingRef.current) return;
    if (!available) {
      setProblem("The browser can only move to its own window in Raticode's desktop app.");
      return;
    }
    movingRef.current = true;
    try {
      let popup = null;
      try { popup = window.open("about:blank", BROWSER_WINDOW_NAME); } catch { popup = null; }
      let doc = null;
      try { doc = popup?.document || null; } catch { doc = null; }
      if (!doc) {
        setProblem("Raticode couldn't open a window for the browser, so it stays here.");
        return;
      }
      setProblem("");
      doc.title = "Raticode browser";
      stopMirrorRef.current?.();
      stopMirrorRef.current = mirrorStudioStyles(document, doc);
      let root = doc.getElementById(ROOT_ID);
      if (!root) {
        root = doc.createElement("div");
        root.id = ROOT_ID;
        root.setAttribute("style", "position:fixed;inset:0;display:flex;flex-direction:column;min-height:0;min-width:0");
        doc.body.appendChild(root);
      }
      popupRef.current = popup;
      await move(root, { poppedOut: true, win: popup, doc });
    } finally {
      movingRef.current = false;
    }
  }, [available, bridge, move]);

  const popIn = useCallback(async () => {
    if (movingRef.current || !placeRef.current.poppedOut) return;
    movingRef.current = true;
    try {
      if (slotRef.current) await move(slotRef.current, { poppedOut: false, win: window, doc: document });
      stopMirrorRef.current?.();
      stopMirrorRef.current = null;
      popupRef.current = null;
      await Promise.resolve(bridge?.browserWindow?.("close")).catch(() => {});
    } finally {
      movingRef.current = false;
    }
  }, [bridge, move]);

  // Closing the Browser's window brings its tabs back first.
  const popInRef = useRef(popIn);
  popInRef.current = popIn;
  useEffect(() => bridge?.onBrowserWindow?.((state) => {
    if (state?.closeRequested) void popInRef.current();
    else if (state?.open === false && placeRef.current.poppedOut && !movingRef.current) {
      // The window went without asking (it can't normally): take the DOM back anyway.
      void popInRef.current();
    }
  }), [bridge]);

  useEffect(() => () => {
    stopMirrorRef.current?.();
    if (placeRef.current.poppedOut) void Promise.resolve(window.goferBrowser?.browserWindow?.("close")).catch(() => {});
  }, []);

  const value = useMemo(() => ({
    available,
    poppedOut: place.poppedOut,
    key: place.key,
    window: place.win,
    document: place.doc,
    register,
    popOut,
    popIn,
  }), [available, place, popIn, popOut, register]);

  return (
    <BrowserHostContext.Provider value={value}>
      {problem ? (
        <p className="flex items-center gap-2 border-b border-line bg-amber-50 px-3 py-1.5 text-xs text-ink" data-testid="browser-window-problem" role="alert">
          <span className="min-w-0 flex-1">{problem}</span>
          <button className="text-xs font-semibold text-muted hover:text-ink" type="button" onClick={() => setProblem("")}>Dismiss</button>
        </p>
      ) : null}
      <div ref={slotRef} className={`${place.poppedOut ? "hidden" : "flex"} min-h-0 min-w-0 flex-1 flex-col`} data-testid="browser-slot" />
      {place.poppedOut ? (
        <div className="grid min-h-0 flex-1 place-items-center overflow-y-auto px-8 py-10" data-testid="browser-popped-out">
          <div className="w-full max-w-sm text-center">
            <AppWindow aria-hidden="true" className="mx-auto text-muted" size={28} />
            <p className="mt-3 text-sm font-semibold text-ink">The browser is in its own window</p>
            <p className="mt-1 text-xs text-muted">Move it to any screen. Closing that window brings its tabs back here.</p>
            <div className="mt-4 flex justify-center gap-2">
              <button className={BUTTON} type="button" onClick={() => void popOut()}>Show browser window</button>
              <button className={BUTTON} type="button" onClick={() => void popIn()}>Bring browser back</button>
            </div>
            <p className="mt-4 text-[11px] text-muted">{MOVE_NOTE}</p>
          </div>
        </div>
      ) : null}
      {createPortal(children, hostNode)}
    </BrowserHostContext.Provider>
  );
}
