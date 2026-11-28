/* The command palette and quick open share one dialog: a field on top, the
   matches below, and a footer that says what Enter will do. It follows the
   studio's flat surfaces and indigo selection. Keys stay in the field, so arrow
   keys move the selection while typing narrows the list. */
import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";
import { Command as CommandIcon, Search } from "lucide-react";
import { rankItems } from "../lib/commandPalette.js";
import "./CommandPalette.css";

const TITLES = {
  commands: { label: "Command palette", placeholder: "Type a command…", empty: "No commands match." },
  files: { label: "Quick open", placeholder: "Search files, workflows, Rem threads, tickets and settings…", empty: "Nothing matches." },
};

function Highlighted({ text, indices }) {
  if (!indices?.length) return text;
  const marked = new Set(indices);
  const parts = [];
  let run = "";
  let runMarked = false;
  [...text].forEach((letter, index) => {
    const isMarked = marked.has(index);
    if (run && isMarked !== runMarked) { parts.push([run, runMarked]); run = ""; }
    run += letter;
    runMarked = isMarked;
  });
  if (run) parts.push([run, runMarked]);
  return parts.map(([part, isMarked], index) => isMarked
    ? <mark key={index} className="command-palette-match">{part}</mark>
    : <Fragment key={index}>{part}</Fragment>);
}

function KeyLabel({ value }) {
  if (!value) return null;
  return (
    <span className="flex shrink-0 items-center gap-1" aria-label={`Key: ${value}`}>
      {value.split(", ").map((chord, index) => (
        <kbd key={index} className="command-palette-key">{chord}</kbd>
      ))}
    </span>
  );
}

// What Enter does for the selected row. A row that starts work names the workflow.
export function paletteFooterText(item) {
  if (!item) return "";
  if (item.disabled) return item.disabledReason || "Not available right now.";
  if (item.startsWork) return `Enter runs ${item.startsWork}`;
  return "Enter to choose · ↑↓ to move · Esc to close";
}

export default function CommandPalette({
  mode = "commands",
  items = [],
  recentIds = [],
  loading = false,
  notice = "",
  onChoose,
  onClose,
}) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [message, setMessage] = useState("");
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const listId = useId();
  const titles = TITLES[mode] ?? TITLES.commands;
  const shown = useMemo(() => rankItems(items, query, recentIds), [items, query, recentIds]);
  const active = shown[Math.min(activeIndex, shown.length - 1)];

  useEffect(() => { inputRef.current?.focus(); }, [mode]);
  useEffect(() => { setQuery(""); setActiveIndex(0); setMessage(""); }, [mode]);
  useEffect(() => {
    listRef.current?.querySelector?.('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, shown]);

  function choose(item) {
    if (!item) return;
    if (item.disabled) { setMessage(item.disabledReason || "Not available right now."); return; }
    onChoose?.(item);
  }

  function move(step) {
    if (!shown.length) return;
    setMessage("");
    setActiveIndex((current) => (Math.min(current, shown.length - 1) + step + shown.length) % shown.length);
  }

  function handleKeyDown(event) {
    if (event.key === "ArrowDown") { event.preventDefault(); move(1); return; }
    if (event.key === "ArrowUp") { event.preventDefault(); move(-1); return; }
    if (event.key === "PageDown") { event.preventDefault(); setActiveIndex((current) => Math.min(shown.length - 1, current + 8)); return; }
    if (event.key === "PageUp") { event.preventDefault(); setActiveIndex((current) => Math.max(0, current - 8)); return; }
    if (event.key === "Enter") { event.preventDefault(); choose(active); return; }
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose?.(); return; }
    // Focus stays in the field while the dialog is open.
    if (event.key === "Tab") event.preventDefault();
  }

  const footer = message || paletteFooterText(active);
  return (
    <div className="command-palette-backdrop fixed inset-0 z-[120] flex items-start justify-center px-4 pt-[12vh]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose?.(); }}>
      <div aria-label={titles.label} aria-modal="true" className="command-palette flex max-h-[min(560px,70vh)] w-full max-w-[620px] flex-col overflow-hidden rounded-lg border border-line text-ink" data-command-palette={mode} role="dialog">
        <div className="flex items-center gap-2 border-b border-line px-3">
          {mode === "commands" ? <CommandIcon aria-hidden="true" className="shrink-0 text-muted" size={15} /> : <Search aria-hidden="true" className="shrink-0 text-muted" size={15} />}
          <input
            ref={inputRef}
            aria-activedescendant={active ? `${listId}-${shown.indexOf(active)}` : undefined}
            aria-autocomplete="list"
            aria-controls={listId}
            aria-expanded="true"
            aria-label={titles.label}
            autoComplete="off"
            className="h-11 min-w-0 flex-1 bg-transparent text-[13px] text-ink outline-none placeholder:text-muted"
            placeholder={titles.placeholder}
            role="combobox"
            spellCheck={false}
            type="text"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); setMessage(""); }}
            onKeyDown={handleKeyDown}
          />
        </div>
        <ul ref={listRef} aria-label={`${titles.label} results`} className="workflow-scrollbar min-h-0 flex-1 overflow-y-auto p-1.5" id={listId} role="listbox">
          {shown.map((item, index) => {
            const selected = item === active;
            return (
              <li
                key={item.id}
                aria-disabled={item.disabled || undefined}
                aria-selected={selected}
                className={`command-palette-row flex min-h-9 cursor-pointer items-center gap-3 rounded-md px-2.5 py-1.5 ${item.disabled ? "opacity-55" : ""}`}
                data-palette-item={item.id}
                id={`${listId}-${index}`}
                role="option"
                onClick={() => choose(item)}
                onMouseMove={() => { if (index !== activeIndex) setActiveIndex(index); }}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate text-[13px]"><Highlighted indices={item.indices} text={item.label} /></span>
                    {item.description ? <span className="truncate text-[11px] text-muted">{item.description}</span> : null}
                  </span>
                  {item.detail ? <span className={`block truncate text-[11px] ${item.startsWork ? "font-medium text-brand" : "text-muted"}`}>{item.detail}</span> : null}
                </span>
                {item.recent ? <span className="shrink-0 text-[10px] text-muted">recent</span> : null}
                {mode === "files" ? <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">{item.group}</span> : null}
                {mode === "commands" ? <KeyLabel value={item.keyLabel} /> : null}
              </li>
            );
          })}
        </ul>
        {!shown.length ? <p className="px-4 py-3 text-xs text-muted" role="status">{loading ? "Loading…" : titles.empty}</p> : null}
        <div className="flex min-h-8 items-center gap-2 border-t border-line px-3 py-1.5 text-[11px]">
          <span className={`min-w-0 flex-1 truncate ${active?.startsWork && !message ? "font-semibold text-brand" : "text-muted"}`} role="status" aria-live="polite">{footer}</span>
          {notice || (loading && shown.length) ? <span className="shrink-0 text-muted">{notice || "Loading files…"}</span> : null}
        </div>
      </div>
    </div>
  );
}
