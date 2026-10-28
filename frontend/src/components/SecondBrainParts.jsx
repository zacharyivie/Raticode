import { useEffect, useRef, useState } from "react";
import { FileText, FileType2, Globe, NotebookText, ScrollText, X } from "lucide-react";
import { CITATION_STATES, typeLabel } from "../lib/secondBrain.js";
import { getState, notify } from "../lib/secondBrainStore.js";
import "./SecondBrainParts.css";

const TYPE_ICONS = { note: NotebookText, report: ScrollText, text: FileText, html: Globe, capture: Globe, pdf: FileType2 };
export function TypeIcon({ type, size = 15, ...props }) {
  const Icon = TYPE_ICONS[type] || FileText;
  return <Icon size={size} aria-hidden="true" {...props} />;
}
export function TypeBadge({ type }) { return <span className="sb-type" data-type={type}><TypeIcon type={type} size={12} />{typeLabel(type)}</span>; }

export function BrainMark({ brain, size = "md" }) {
  return <span className="sb-brain-mark" data-size={size} data-color={brain?.color || "indigo"} aria-hidden="true">{(brain?.name || "?").trim().charAt(0).toUpperCase()}</span>;
}

export function Pill({ tone = "neutral", children, title }) {
  return <span className="sb-pill" data-tone={tone} title={title}><i aria-hidden="true" />{children}</span>;
}
export function CitationState({ state }) {
  const entry = CITATION_STATES[state] || CITATION_STATES.unreviewed;
  return <Pill tone={entry.tone}>{entry.label}</Pill>;
}

export function Highlight({ segments }) {
  return <>{segments.map((segment, index) => segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}</>;
}

// Closes a popover on Escape or an outside pointer press, returning focus to the trigger.
export function useDismiss(open, onClose, refs) {
  useEffect(() => {
    if (!open) return undefined;
    const press = event => { if (!refs.some(ref => ref.current?.contains(event.target))) onClose(); };
    const key = event => { if (event.key === "Escape") { event.stopPropagation(); onClose(true); } };
    document.addEventListener("pointerdown", press, true);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("pointerdown", press, true); document.removeEventListener("keydown", key, true); };
  }, [open, onClose, refs]);
}

export function Popover({ label, trigger, triggerClassName = "", align = "start", children, className = "", open: controlledOpen, onOpenChange, disabled, title }) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = value => { onOpenChange ? onOpenChange(value) : setLocalOpen(value); };
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const refs = useRef([triggerRef, panelRef]).current;
  useDismiss(open, refocus => { setOpen(false); if (refocus) triggerRef.current?.focus(); }, refs);
  return <div className="sb-popover-anchor">
    <button ref={triggerRef} type="button" className={triggerClassName} aria-haspopup="dialog" aria-expanded={open} aria-label={label} title={title || label} disabled={disabled} onClick={() => setOpen(!open)}>{trigger}</button>
    {open ? <div ref={panelRef} role="dialog" aria-label={label} className={`sb-popover ${className}`} data-align={align}>{typeof children === "function" ? children(() => setOpen(false)) : children}</div> : null}
  </div>;
}

export function Menu({ label, trigger, triggerClassName = "", items, align = "end" }) {
  return <Popover label={label} trigger={trigger} triggerClassName={triggerClassName} align={align} className="sb-menu">
    {close => <div role="menu" aria-label={label}>{items.filter(Boolean).map(item => item.separator
      ? <hr key={item.key} />
      : <button key={item.label} role="menuitem" type="button" disabled={item.disabled} data-danger={item.danger || undefined} onClick={() => { close(); item.onSelect(); }}>{item.icon ? <item.icon size={14} aria-hidden="true" /> : null}<span>{item.label}</span>{item.hint ? <small>{item.hint}</small> : null}</button>)}</div>}
  </Popover>;
}

// Keeps the typed string while focused; commits on blur or Enter so values never snap back mid-edit.
export function DraftInput({ value, onCommit, label, className = "", ...props }) {
  const [draft, setDraft] = useState(value ?? "");
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value ?? ""); }, [value]);
  return <input aria-label={label} className={className} value={draft} {...props}
    onChange={event => setDraft(event.target.value)}
    onFocus={() => { focused.current = true; }}
    onBlur={() => { focused.current = false; if (draft !== (value ?? "")) onCommit(draft); }}
    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onCommit(draft); } props.onKeyDown?.(event); }} />;
}

export function Chip({ children, onRemove, removeLabel }) {
  return <span className="sb-chip">{children}{onRemove ? <button type="button" aria-label={removeLabel} title={removeLabel} onClick={onRemove}><X size={12} /></button> : null}</span>;
}

export function Modal({ title, description, onClose, children, footer, wide = false, labelledBy }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    // Start typing in the first field when the dialog opens with one; otherwise focus the dialog
    // itself rather than drawing a focus ring around the first radio, checkbox or button.
    const first = ref.current?.querySelector(".sb-modal-body :is(input, select, textarea, button)");
    const typing = first?.matches("textarea, select, input:not([type=checkbox]):not([type=radio])");
    (typing ? first : ref.current)?.focus();
    return () => previous?.focus?.();
  }, []);
  return <div className="sb-modal-backdrop" onPointerDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={labelledBy || "sb-modal-title"} className="sb-modal" data-wide={wide || undefined} tabIndex={-1}
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); onClose(); }
        if (event.key !== "Tab") return;
        const focusable = [...ref.current.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex='0']")];
        if (!focusable.length) return;
        if (event.shiftKey && (document.activeElement === focusable[0] || document.activeElement === ref.current)) { event.preventDefault(); focusable.at(-1).focus(); }
        else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus(); }
      }}>
      <header className="sb-modal-header"><div><h2 id={labelledBy || "sb-modal-title"}>{title}</h2>{description ? <p>{description}</p> : null}</div><button data-close type="button" className="sb-icon-button" aria-label="Close" title="Close" onClick={onClose}><X size={16} /></button></header>
      <div className="sb-modal-body">{children}</div>
      {footer ? <footer className="sb-modal-footer">{footer}</footer> : null}
    </div>
  </div>;
}

export function Segmented({ label, value, options, onChange, size }) {
  return <div className="sb-segmented" role="radiogroup" aria-label={label} data-size={size}>
    {options.map(([id, text]) => <button key={id} type="button" role="radio" aria-checked={value === id} tabIndex={value === id ? 0 : -1} onClick={() => onChange(id)}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        const index = options.findIndex(([option]) => option === value);
        const next = options[(index + (event.key === "ArrowRight" ? 1 : options.length - 1)) % options.length][0];
        onChange(next);
        event.currentTarget.parentElement.querySelectorAll("button")[options.findIndex(([option]) => option === next)]?.focus();
      }}>{text}</button>)}
  </div>;
}

export function ProgressBar({ value, label }) {
  return <div className="sb-progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>;
}

export function Empty({ icon: Icon, title, children, actions }) {
  return <div className="sb-empty">{Icon ? <Icon size={26} aria-hidden="true" /> : null}<h3>{title}</h3>{children ? <p>{children}</p> : null}{actions ? <div className="sb-empty-actions">{actions}</div> : null}</div>;
}

export function askRem({ brain, doc, passages = [], draft, title }) {
  const library = getState();
  const brains = [...new Set([brain.id, ...passages.map(item => item.doc.brainId)])].map(id => library.brains.find(item => item.id === id)).filter(Boolean);
  if (brains.some(item => item.settings.allowCloud === false)) { notify("Allow cloud processing in the source brain’s settings before attaching evidence to the Rem chat pane. Local API profiles can create reports within Second Brain.", "danger"); return; }

  const text = passages.length
    ? passages.map(({ doc: source, block }) => `[${source.title} · ${source.path}${block ? ` · ${block.heading}` : ""} · version ${source.version}]\n${block ? block.text : source.blocks.map(item => `## ${item.heading}\n${item.text}`).join("\n\n")}`).join("\n\n---\n\n")
    : doc.blocks.map(item => `## ${item.heading}\n${item.text}`).join("\n\n");
  window.dispatchEvent(new CustomEvent("gofer:rem-context", { detail: {
    mode: "ask", autoSend: false, path: doc ? `${brain.root}/${doc.path}` : brain.root,
    version: title || `Second Brain evidence from ${brain.name}`, text, draft, secondBrainIds: brains.map(item => item.id),
  } }));
}
