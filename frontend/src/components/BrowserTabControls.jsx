import { useCallback, useEffect, useRef, useState } from "react";
import { EyeOff, Plus, X } from "lucide-react";
import { engineBadge, engineLabel, webBrowserEngine } from "../lib/browserEngines.js";
import { browserGroupHasNewTab } from "../lib/browserTabs.js";

// Colors that tell the browsers apart at a glance; the badge's text and label
// name the browser, so color is never the only cue.
const BADGE_TONES = {
  raticode: "border-indigo-200 bg-indigo-50 text-indigo-800 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-200",
  gecko: "border-orange-200 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200",
  firefox: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
  chrome: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  edge: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200",
};

const FOCUS = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand";

// The small label on a web tab naming the browser it runs in.
export function BrowserEngineBadge({ engine }) {
  const value = webBrowserEngine(engine);
  return (
    <span
      aria-label={engineLabel(value)}
      className={`shrink-0 rounded border px-1 text-[9px] font-semibold leading-[13px] ${BADGE_TONES[value]}`}
      data-browser-engine={value}
      role="img"
      title={value === "gecko" ? "Raticode (gecko): Firefox's window, with its own tabs inside" : engineLabel(value)}
    >{engineBadge(value)}</span>
  );
}

// Why a browser can't open a tab right now, or "" when it can.
export function engineUnavailableReason(engine, engines = null, gecko = null) {
  if (engine === "raticode") return "";
  if (engine === "gecko") {
    if (!gecko) return "";
    if (gecko.problem) return gecko.problem;
    if (!gecko.executable) return "Install Firefox to use Raticode (gecko).";
    return "";
  }
  const status = engines?.[engine];
  if (!status) return "";
  return status.available ? "" : `${engineLabel(engine)} isn't installed. Install it to open tabs in it.`;
}

// Which browsers are installed, asked again whenever refresh is called.
export function useBrowserEngineStatus(enabled = true) {
  const [engines, setEngines] = useState(null);
  const [gecko, setGecko] = useState(null);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const refresh = useCallback(() => {
    const bridge = typeof window === "undefined" ? null : window.goferBrowser;
    if (!bridge) return;
    void bridge.library?.("engine-status")?.then((status) => { if (mounted.current) setEngines(status || null); })?.catch?.(() => {});
    void bridge.gecko?.("status")?.then((status) => { if (mounted.current) setGecko(status || null); })?.catch?.(() => {});
  }, []);
  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    const unsubscribe = window.goferBrowser?.onGecko?.((next) => { if (mounted.current) setGecko(next || null); });
    return () => unsubscribe?.();
  }, [enabled, refresh]);
  const reasonFor = useCallback((engine) => engineUnavailableReason(engine, engines, gecko), [engines, gecko]);
  return { engines, gecko, reasonFor, refresh };
}

// The + after a browser's tabs: one more tab in that browser.
export function BrowserGroupNewTab({ group, onNewTab }) {
  if (!browserGroupHasNewTab(group)) return null;
  const label = group.private ? "New private tab" : `New ${engineLabel(group.engine)} tab`;
  return (
    <button
      aria-label={label}
      className={`grid h-9 w-7 shrink-0 place-items-center border-r border-line text-muted hover:bg-white hover:text-ink ${FOCUS}`}
      data-browser-group-new-tab={group.key}
      title={label}
      type="button"
      onClick={() => onNewTab?.(group)}
    ><Plus aria-hidden="true" size={13} /></button>
  );
}

// After the last group: start a tab in a browser that has none yet. It opens
// in the tab bar itself rather than as a menu over the page, because Raticode
// (gecko)'s window would have to step aside for anything drawn over it.
// inside lists browsers that already have their one tab, where a choice opens
// a tab inside that tab's own window (Raticode (gecko)).
export function BrowserFamilyChooser({ choices = [], inside = [], offerPrivate = true, reasonFor = () => "", onOpen, onChoose }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const toggleRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, [open]);
  useEffect(() => {
    if (open) rootRef.current?.querySelector("[data-browser-family]")?.focus?.();
  }, [open]);
  if (!choices.length && !offerPrivate) return null;
  function close(restoreFocus) {
    setOpen(false);
    if (restoreFocus) toggleRef.current?.focus?.();
  }
  return (
    <div
      ref={rootRef}
      className="flex h-9 max-w-full shrink-0 items-center"
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(true); return; }
        if (!open || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        const buttons = [...rootRef.current.querySelectorAll("[data-browser-family]")];
        const index = buttons.indexOf(document.activeElement);
        if (index < 0) return;
        event.preventDefault();
        buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length]?.focus();
      }}
    >
      <button
        ref={toggleRef}
        aria-expanded={open}
        aria-label={open ? "Close the browser choices" : "Open a tab in another browser"}
        className={`flex h-9 shrink-0 items-center gap-1 px-2 text-[11px] text-muted hover:bg-white hover:text-ink ${FOCUS}`}
        data-testid="browser-family-chooser"
        title={open ? "Close" : "Open a tab in another browser"}
        type="button"
        onClick={() => { if (!open) onOpen?.(); setOpen((value) => !value); }}
      >{open ? <X aria-hidden="true" size={12} /> : <><Plus aria-hidden="true" size={13} /><span>Browser</span></>}</button>
      {open ? (
        <div aria-label="Open a tab in" className="tab-strip-scrollbar flex h-9 min-w-0 items-center gap-1 overflow-x-auto pr-2" role="group">
          {choices.map((engine) => {
            const reason = reasonFor(engine);
            const action = inside.includes(engine) ? `New tab inside ${engineLabel(engine)}'s Firefox window` : `New ${engineLabel(engine)} tab`;
            return (
              <button
                key={engine}
                aria-disabled={reason ? "true" : undefined}
                aria-label={reason ? `${engineLabel(engine)}: ${reason}` : action}
                className={`flex h-7 shrink-0 items-center gap-1 rounded border border-line bg-white px-1.5 text-[11px] text-ink hover:bg-slate-50 ${reason ? "opacity-50" : ""} ${FOCUS}`}
                data-browser-family={engine}
                title={reason || action}
                type="button"
                onClick={() => { setOpen(false); onChoose?.({ engine }); }}
              >
                <BrowserEngineBadge engine={engine} />
                <span>{engineLabel(engine)}</span>
              </button>
            );
          })}
          {offerPrivate ? (
            <button
              aria-label="New private tab"
              className={`flex h-7 shrink-0 items-center gap-1 rounded border border-line bg-white px-1.5 text-[11px] text-ink hover:bg-slate-50 ${FOCUS}`}
              data-browser-family="private"
              title="New private tab, in Raticode (chromium)"
              type="button"
              onClick={() => { setOpen(false); onChoose?.({ engine: "raticode", private: true }); }}
            ><EyeOff aria-hidden="true" className="text-muted" size={11} /><span>Private</span></button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// A line under the tab bar, so it never covers a page.
export function BrowserTabNotice({ notice, onDismiss }) {
  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => onDismiss?.(), notice.tone === "error" ? 12000 : 8000);
    return () => window.clearTimeout(timer);
  }, [notice, onDismiss]);
  if (!notice) return null;
  return (
    <div
      className={`flex min-h-7 w-full items-center gap-2 border-t border-line px-3 py-1 text-[11px] ${notice.tone === "error" ? "bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-100" : "bg-white text-ink"}`}
      data-testid="browser-tab-notice"
      role={notice.tone === "error" ? "alert" : "status"}
    >
      <span className="min-w-0 flex-1">{notice.text}</span>
      <button aria-label="Dismiss" className={`grid h-5 w-5 shrink-0 place-items-center rounded text-muted hover:bg-slate-100 hover:text-ink ${FOCUS}`} type="button" onClick={() => onDismiss?.()}><X aria-hidden="true" size={11} /></button>
    </div>
  );
}
