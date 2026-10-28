import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, SlidersHorizontal, X } from "lucide-react";
import { remResourceError, remToolSummary } from "../lib/remResources.js";
import "./RemResources.css";
const RemResources = lazy(() => import("./RemResources.jsx"));

// Composer trigger summarizing the selection, with the full menu in a popover.
export default function RemToolsMenu({ open, onOpenChange, thread, sending, value, memory, onChange }) {
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const label = thread ? "Thread tools, skills & MCP" : "New thread tools, skills & MCP";
  const issue = remResourceError(value);
  const [maxHeight, setMaxHeight] = useState(null);
  // Keep the popover inside the Rem pane: it opens upward from the composer.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const anchor = rootRef.current?.offsetParent;
      const pane = rootRef.current?.closest?.("[data-chat-pane]");
      if (!anchor?.getBoundingClientRect || !pane) return;
      setMaxHeight(Math.min(600, Math.max(180, anchor.getBoundingClientRect().top - pane.getBoundingClientRect().top - 8)));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (event.type === "keydown") {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onOpenChange(false);
        triggerRef.current?.focus();
        return;
      }
      if (!rootRef.current?.contains(event.target)) onOpenChange(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close, true);
    };
  }, [open, onOpenChange]);
  return <div ref={rootRef} className="mb-2 flex min-w-0">
    <button ref={triggerRef} aria-label={label} aria-expanded={open} aria-haspopup="dialog" className="rem-tools-trigger" type="button" onClick={() => onOpenChange(!open)}>
      <SlidersHorizontal aria-hidden="true" size={13} strokeWidth={1.75} />
      <strong>Tools</strong>
      <span className={`rem-tools-trigger__summary${issue ? " text-red-700 dark:text-red-300" : ""}`}>{issue ? "Needs attention" : remToolSummary(value)}</span>
      <ChevronDown aria-hidden="true" size={12} className={open ? "rotate-180" : ""} />
    </button>
    {open ? <div role="dialog" aria-label={label} className="rem-tools-popover" style={maxHeight ? { maxHeight } : undefined}>
      <header className="rem-tools-popover__header">
        <div>
          <h2>{thread ? "Tools for this thread" : "Tools for new threads"}</h2>
          <p role="status">{sending ? "Changes apply when you send your next instruction, which restarts the response with these settings." : thread ? "Changes apply to your next message in this thread." : "Applied when you start a thread. Each thread keeps its own selection."}</p>
        </div>
        <button aria-label="Close tools" className="rem-tools__icon-button" type="button" onClick={() => onOpenChange(false)}><X size={14} /></button>
      </header>
      <div className="rem-tools-popover__body"><Suspense fallback={<p className="rem-tools__note">Loading tools…</p>}><RemResources live value={value} memory={memory} onChange={onChange} /></Suspense></div>
    </div> : null}
  </div>;
}
