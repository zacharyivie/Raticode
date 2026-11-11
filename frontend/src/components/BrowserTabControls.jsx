import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, EyeOff, Plus } from "lucide-react";
import { MENU_ITEM_CLASS, usePopupMenu } from "./BrowserPanels.jsx";
import { WEB_BROWSER_OPTIONS, engineBadge, engineLabel, webBrowserEngine } from "../lib/browserEngines.js";

// Colors that tell the browsers apart at a glance; the badge's text and label
// name the browser, so color is never the only cue.
const BADGE_TONES = {
  raticode: "border-indigo-200 bg-indigo-50 text-indigo-800 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-200",
  gecko: "border-orange-200 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200",
  firefox: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
  chrome: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  edge: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200",
};

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
    if (!gecko.executable) return "Install Firefox to use Raticode (gecko)";
    return "";
  }
  const status = engines?.[engine];
  if (!status) return "";
  return status.available ? "" : `Install ${engineLabel(engine)} to open tabs in it`;
}

// New tab in the default browser, and an arrow to pick another browser or a
// private tab. The default is Settings > Browser > Default browser for new tabs.
export function NewBrowserTabButton({ defaultEngine = "raticode", onNewTab }) {
  const [open, setOpen] = useState(false);
  const defaultValue = webBrowserEngine(defaultEngine);
  return (
    <div className="relative flex shrink-0 items-center border-l border-line">
      <button
        aria-label={`New tab in ${engineLabel(defaultValue)}`}
        className="grid h-9 w-8 place-items-center text-muted hover:bg-white hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand"
        data-testid="browser-new-tab"
        title={`New tab in ${engineLabel(defaultValue)}`}
        type="button"
        onClick={() => onNewTab?.({ engine: defaultValue })}
      ><Plus size={14} /></button>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Choose a browser for a new tab"
        className="grid h-9 w-5 place-items-center text-muted hover:bg-white hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand"
        data-testid="browser-new-tab-menu"
        title="Choose a browser for a new tab"
        type="button"
        onClick={() => setOpen((value) => !value)}
      ><ChevronDown size={12} /></button>
      {open ? <NewTabMenu defaultEngine={defaultValue} onClose={() => setOpen(false)} onNewTab={onNewTab} /> : null}
    </div>
  );
}

function NewTabMenu({ defaultEngine, onClose, onNewTab }) {
  const menuRef = useRef(null);
  const [engines, setEngines] = useState(null);
  const [gecko, setGecko] = useState(null);
  const handleKeyDown = usePopupMenu(menuRef, (options) => {
    if (options?.restoreFocus) menuRef.current?.parentElement?.querySelector("button[aria-haspopup]")?.focus();
    onClose();
  }, { includeParent: true });
  useEffect(() => {
    const bridge = window.goferBrowser;
    let current = true;
    void bridge?.library?.("engine-status").then((status) => { if (current) setEngines(status || null); }).catch(() => {});
    void bridge?.gecko?.("status").then((status) => { if (current) setGecko(status || null); }).catch(() => {});
    return () => { current = false; };
  }, []);
  function choose(options) {
    onClose();
    onNewTab?.(options);
  }
  return (
    <div
      ref={menuRef}
      aria-label="New tab in"
      className="absolute right-0 top-9 z-50 w-60 rounded-md border border-line bg-white py-1 text-xs text-ink shadow-lg"
      role="menu"
      onKeyDown={handleKeyDown}
    >
      <p className="px-3 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">New tab in</p>
      {WEB_BROWSER_OPTIONS.map(([value, label]) => {
        const reason = engineUnavailableReason(value, engines, gecko);
        return (
          <button
            key={value}
            className={MENU_ITEM_CLASS}
            data-new-tab-engine={value}
            disabled={Boolean(reason)}
            role="menuitem"
            tabIndex={-1}
            title={reason || (value === "gecko" ? "Firefox's window inside Raticode. More Gecko tabs open inside it." : undefined)}
            type="button"
            onClick={() => choose({ engine: value })}
          >
            <BrowserEngineBadge engine={value} />
            <span className="flex-1">{label}</span>
            {value === defaultEngine ? <span className="flex items-center gap-0.5 text-[10px] text-muted"><Check aria-hidden="true" size={10} />Default</span> : null}
          </button>
        );
      })}
      <div className="my-1 border-t border-line" role="separator" />
      <button className={MENU_ITEM_CLASS} role="menuitem" tabIndex={-1} type="button" onClick={() => choose({ engine: "raticode", private: true })}>
        <EyeOff aria-hidden="true" className="shrink-0 text-muted" size={12} />
        <span className="flex-1">New private tab</span>
        <span className="text-[10px] text-muted">Raticode (chromium)</span>
      </button>
    </div>
  );
}
