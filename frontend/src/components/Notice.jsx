import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { noticeDuration } from "../lib/notices.js";

// One notice. With a duration it closes itself, but never while the pointer is on it or
// focus is in it. className names the look: its text and buttons use className-text and
// className-actions.
export function Notice({ className, tone = "success", icon = null, message, action = null, duration = 0, onDismiss, dismissClassName = "" }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const held = hovered || focused;
  useEffect(() => {
    if (!duration || held) return undefined;
    const timer = window.setTimeout(onDismiss, duration);
    return () => window.clearTimeout(timer);
  }, [duration, held, onDismiss]);
  return <div className={className} data-tone={tone} role={tone === "danger" ? "alert" : "status"} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
    onFocus={() => setFocused(true)} onBlur={event => { if (!event.currentTarget.contains?.(event.relatedTarget)) setFocused(false); }}>
    {icon}
    <p className={`${className}-text`}>{message}</p>
    <div className={`${className}-actions`}>{action}<button type="button" className={dismissClassName} aria-label="Dismiss notification" title="Dismiss" onClick={onDismiss}><X size={14} /></button></div>
  </div>;
}

// The boards' notices: the newest few, each { text, action: { label, run } | null, tone }.
export function useNotices(limit = 3) {
  const [notices, setNotices] = useState([]);
  const dismiss = useCallback(id => setNotices(current => current.filter(item => item.id !== id)), []);
  const notify = useCallback((text, action = null, tone = "success") => {
    const id = Math.random().toString(36).slice(2);
    setNotices(current => [...current, { id, text, action, tone }].slice(-limit));
  }, [limit]);
  return { notices, notify, dismiss };
}

export function NoticeStack({ notices, onDismiss }) {
  return <div className="oh-toasts" aria-live="polite">{notices.map(item => <StackNotice key={item.id} item={item} onDismiss={onDismiss} />)}</div>;
}

function StackNotice({ item, onDismiss }) {
  const dismiss = useCallback(() => onDismiss(item.id), [onDismiss, item.id]);
  const action = item.action ? <button type="button" className="oh-toast-action" onClick={() => { dismiss(); item.action.run(); }}>{item.action.label}</button> : null;
  return <Notice className="oh-toast" dismissClassName="oh-toast-dismiss" tone={item.tone} message={item.text} action={action} duration={noticeDuration(item)} onDismiss={dismiss} />;
}
