import { useEffect, useRef, useState } from "react";
import { coverCutouts, deviceBounds, sameBounds } from "../lib/geckoView.js";

const POSITION_CHECK_MS = 200;
const BUTTON = "h-8 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink transition hover:bg-slate-50";

// Menus and dialogs that are open and overlap the slot. They may be small
// enough to sit between the points checked for covering.
function openOverlays(rect) {
  if (typeof document.querySelectorAll !== "function") return [];
  return [...document.querySelectorAll("[role=menu], [role=dialog], [role=alertdialog], [role=listbox], [role=tooltip]")].filter((element) => {
    if (element.closest?.("[aria-hidden=true], [inert]") || window.getComputedStyle?.(element)?.visibility === "hidden") return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.right > rect.left && box.left < rect.right && box.bottom > rect.top && box.top < rect.bottom;
  });
}

function cleanError(cause) {
  return String(cause?.message || cause || "Raticode (gecko) could not start.").replace(/^Error invoking remote method '[^']+': (?:Error: )?/, "");
}

// Addresses already given to Firefox, which reopens them itself after a restart.
const HANDED_OVER_KEY = "raticode.gecko.handedOver";
const HANDED_OVER_LIMIT = 200;

function handedOver() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(HANDED_OVER_KEY) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function wasHandedOver(tabId, url) {
  return handedOver().includes(`${tabId}\n${url}`);
}

function rememberHandedOver(tabId, url) {
  try {
    window.localStorage.setItem(HANDED_OVER_KEY, JSON.stringify([...handedOver(), `${tabId}\n${url}`].slice(-HANDED_OVER_LIMIT)));
  } catch {
    // Without storage the address only opens again next time.
  }
}

// The Raticode (gecko) tab. Firefox's real window is placed over this slot by
// the main process, with Firefox's own tabs, address bar, settings, bookmarks
// and extensions. The Browser plugin keeps one such tab; addresses opened in
// Raticode (gecko) after it started arrive in pendingUrls and open as Firefox
// tabs, and onPendingOpened reports which ones Firefox has.
export default function GeckoBrowser({ active, tabId = "", initialUrl = "", pendingUrls = [], newTabRequests = 0, onPendingOpened, onStateChange }) {
  const bridge = window.goferBrowser;
  const slotRef = useRef(null);
  const openedRef = useRef(false);
  const onStateChangeRef = useRef(onStateChange);
  onStateChangeRef.current = onStateChange;
  const onPendingOpenedRef = useRef(onPendingOpened);
  onPendingOpenedRef.current = onPendingOpened;
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [restartNote, setRestartNote] = useState("");

  async function open(url) {
    setStarting(true);
    setError("");
    try {
      setStatus(await bridge.gecko("open", { url }));
      // Firefox has the address now and restores it itself. The tab keeps its
      // address, for switching back to another browser.
      if (url) rememberHandedOver(tabId, url);
    } catch (cause) {
      setError(cleanError(cause));
    } finally {
      setStarting(false);
    }
  }

  useEffect(() => {
    if (!bridge?.gecko) return undefined;
    let cancelled = false;
    const unsubscribe = bridge.onGecko?.((next) => { if (!cancelled) setStatus(next); });
    void bridge.gecko("status").then((next) => { if (!cancelled) setStatus(next); })
      .catch((cause) => { if (!cancelled) setError(cleanError(cause)); });
    return () => { cancelled = true; unsubscribe?.(); };
  }, [bridge]);

  // Firefox starts when the Gecko tab is first looked at, not when Raticode restores it.
  const ready = Boolean(status?.available && status?.supported);
  const [opened, setOpened] = useState(false);
  useEffect(() => {
    if (!active || !ready || openedRef.current) return;
    openedRef.current = true;
    void open(/^https?:/i.test(initialUrl) && !wasHandedOver(tabId, initialUrl) ? initialUrl : "").finally(() => setOpened(true));
    // The tab's first address; later changes happen inside Firefox.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, ready]);

  // Pages opened in Raticode (gecko) while its tab exists become Firefox tabs.
  const pendingKey = (Array.isArray(pendingUrls) ? pendingUrls : []).join("\n");
  useEffect(() => {
    if (!active || !opened || !pendingKey) return undefined;
    let cancelled = false;
    void (async () => {
      const sent = [];
      for (const url of pendingKey.split("\n")) {
        if (cancelled) break;
        if (/^https?:/i.test(url) && !wasHandedOver(tabId, url)) await open(url);
        sent.push(url);
      }
      if (!cancelled) onPendingOpenedRef.current?.(sent);
    })();
    return () => { cancelled = true; };
    // open() reads refs and the bridge only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, opened, pendingKey, tabId]);

  // The tab bar's + for Raticode (gecko): an empty tab inside Firefox. Requests
  // made before this tab was mounted (a restored count) are not replayed, and a
  // request that arrives before Firefox has started is met by starting it.
  const newTabsHandledRef = useRef(newTabRequests);
  useEffect(() => {
    if (newTabRequests <= newTabsHandledRef.current) return;
    if (!active || !ready) return;
    newTabsHandledRef.current = newTabRequests;
    if (!opened) return;
    setError("");
    void bridge.gecko("new-tab").then((next) => { if (next) setStatus(next); })
      .catch((cause) => setError(cleanError(cause)));
    // bridge is fixed for the window's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, ready, opened, newTabRequests]);

  // A click in Firefox's page never reaches Raticode's page, so menus open over
  // the tab wouldn't close. Pass it on as a click on the tab's slot.
  const pageClicksRef = useRef(null);
  useEffect(() => {
    const clicks = status?.pageClicks;
    if (!Number.isInteger(clicks)) return;
    const previous = pageClicksRef.current;
    pageClicksRef.current = clicks;
    if (previous === null || clicks <= previous || !active) return;
    const slot = slotRef.current;
    if (!slot) return;
    const init = { bubbles: true, cancelable: true, composed: true, button: 0 };
    slot.dispatchEvent(typeof PointerEvent === "function" ? new PointerEvent("pointerdown", init) : new MouseEvent("pointerdown", init));
    slot.dispatchEvent(new MouseEvent("mousedown", init));
  }, [active, status?.pageClicks]);

  useEffect(() => {
    if (status?.title) onStateChangeRef.current?.({ title: status.title });
  }, [status?.title]);

  // Keep Firefox's window on the slot, and out of the way of anything drawn over it.
  useEffect(() => {
    if (!active || !bridge?.gecko) return undefined;
    let last = null;
    const report = () => {
      const slot = slotRef.current;
      if (!slot) return;
      const rect = slot.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const shown = rect.width > 1 && rect.height > 1 && !document.hidden;
      const cutouts = shown ? coverCutouts(slot, rect, {
        elementAt: (x, y) => document.elementFromPoint(x, y),
        positionOf: (element) => window.getComputedStyle?.(element)?.position || "",
        rectOf: (element) => element.getBoundingClientRect(),
        extra: openOverlays(rect),
        ratio,
      }) : [];
      const visible = shown && cutouts !== null;
      const next = { ...deviceBounds(rect, ratio), visible, cutouts: visible ? cutouts : [] };
      if (sameBounds(last, next)) return;
      last = next;
      void bridge.gecko("place", next).catch(() => {});
    };
    report();
    const timer = window.setInterval(report, POSITION_CHECK_MS);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(report) : null;
    if (slotRef.current) observer?.observe(slotRef.current);
    window.addEventListener("resize", report);
    // Clicking Raticode's own interface takes the keyboard back from Firefox.
    // The click passed on from Firefox's page below isn't one of those.
    const takeFocusBack = (event) => {
      if (!event.isTrusted) return;
      void bridge.gecko("focus-app").catch(() => {});
    };
    window.addEventListener("pointerdown", takeFocusBack, true);
    return () => {
      window.clearInterval(timer);
      observer?.disconnect();
      window.removeEventListener("resize", report);
      window.removeEventListener("pointerdown", takeFocusBack, true);
      void bridge.gecko("place", { visible: false }).catch(() => {});
    };
  }, [active, bridge]);

  async function restartApp() {
    try {
      const result = await bridge.gecko("restart-app");
      if (result?.manual) setRestartNote("Close Raticode and start it again to finish.");
    } catch (cause) {
      setError(cleanError(cause));
    }
  }

  const running = Boolean(status?.placed);
  let message = "";
  if (!bridge?.gecko) message = "Raticode (gecko) needs Raticode's desktop app.";
  else if (error) message = error;
  else if (status?.error && !running && !starting) message = status.error;
  else if (status?.problem) message = status.problem;
  else if (status?.needsRestart) message = restartNote || "Raticode (gecko) needs Raticode to restart in X11 mode. Your tabs and projects come back afterwards.";
  else if (status && !status.executable) message = "Firefox was not found. Install Firefox to use Raticode (gecko).";
  else if (starting || (status && status.running && !running)) message = "Starting Raticode (gecko)…";
  else if (status && !running) message = "Raticode (gecko) is closed.";

  return (
    <section aria-label="Raticode (gecko) browser" data-testid="gecko-browser" className="flex min-h-0 flex-1 flex-col bg-white">
      <div ref={slotRef} data-testid="gecko-slot" className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        {message ? <p role="status" className="max-w-md text-sm text-ink">{message}</p> : null}
        {status?.needsRestart && !restartNote ? <button type="button" className={BUTTON} onClick={() => void restartApp()}>Restart Raticode</button> : null}
        {status?.supported && status?.executable && !running && !starting ? <button type="button" className={BUTTON} onClick={() => void open("")}>Open Raticode (gecko)</button> : null}
      </div>
    </section>
  );
}
