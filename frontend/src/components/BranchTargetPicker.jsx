import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

const sameOption = (a, b) => Boolean(a && b) && a.name === b.name && a.type === b.type;

// A typeable branch list: recommended branches, then local, then remote-tracking branches.
// Typing filters the list; the choice is only committed on Enter, click, or leaving the
// field with an exact branch name, so a half-typed name never starts a Git preview.
export default function BranchTargetPicker({ label, value, recommended = [], local = [], remote = [], unavailableReason, disabled, onChange, onOpen }) {
  const id = useId();
  const listId = `${id}-branches`;
  const [draft, setDraft] = useState(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [navigated, setNavigated] = useState(false);
  const listRef = useRef(null);
  const justFocused = useRef(false);
  const query = (draft ?? "").trim().toLowerCase();
  const options = useMemo(() => {
    const matching = name => !query || name.toLowerCase().includes(query);
    const groups = [
      ...(query ? [] : [["Recommended", recommended]]),
      ["Local branches", local.map(name => ({ name, type: "local" }))],
      ["Remote branches", remote.map(name => ({ name, type: "remote" }))],
    ];
    return groups.flatMap(([group, items]) => items.filter(item => matching(item.name)).map(item => ({ ...item, group, reason: unavailableReason?.(item) || "" })));
  }, [query, recommended, local, remote, unavailableReason]);
  const enabled = options.map((option, index) => option.reason ? -1 : index).filter(index => index >= 0);

  useEffect(() => {
    if (open && active >= 0) listRef.current?.querySelector?.(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  function show() {
    if (disabled || open) return;
    setOpen(true);
    setActive(Math.max(0, options.findIndex(option => sameOption(option, value) && !option.reason)));
    onOpen?.();
  }
  function close() { setOpen(false); setDraft(null); setNavigated(false); setActive(-1); }
  function choose(option) {
    if (!option || option.reason) return;
    close();
    if (!sameOption(option, value)) onChange({ name: option.name, type: option.type });
  }
  // Exact names win, local before remote, so "main" never picks "domain" or "origin/main".
  function exactMatch(text) {
    const lower = text.toLowerCase();
    const usable = options.filter(option => !option.reason);
    return usable.find(option => option.name === text) || usable.find(option => option.name.toLowerCase() === lower);
  }
  function commitDraft() {
    if (draft === null) { close(); return; }
    const text = draft.trim();
    if (!text) { close(); if (value) onChange(null); return; }
    const match = exactMatch(text);
    if (match) choose(match); else close();
  }
  function move(step) {
    if (!enabled.length) return;
    const position = enabled.indexOf(active);
    const next = position < 0 ? (step > 0 ? 0 : enabled.length - 1) : (position + step + enabled.length) % enabled.length;
    setActive(enabled[next]); setNavigated(true);
  }
  function keyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) { show(); return; }
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (!open) { show(); return; }
      const typed = draft?.trim() || "";
      if (!navigated && draft !== null) {
        // Typed text: an exact name wins, an empty field clears, otherwise take the first match.
        if (!typed || exactMatch(typed)) { commitDraft(); return; }
        if (enabled.length) choose(options[enabled[0]]); else close();
        return;
      }
      if (active >= 0) choose(options[active]); else close();
    } else if (event.key === "Escape" && open) {
      event.preventDefault(); event.stopPropagation(); close();
    }
  }

  let lastGroup = "";
  const activeOption = open && active >= 0 ? options[active] : null;
  return <div className="block">
    <label htmlFor={`${id}-input`}>{label}</label>
    <div className="relative mt-1">
      <input id={`${id}-input`} role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={activeOption ? `${id}-option-${active}` : undefined}
        autoComplete="off" spellCheck={false} placeholder="Type to search branches"
        className="w-full rounded border border-line bg-canvas p-2 pr-8" disabled={disabled}
        value={draft ?? value?.name ?? ""}
        // Select the chosen name on focus so typing replaces it; the mouseup that focused the
        // field would otherwise collapse that selection to a caret.
        onFocus={event => { event.currentTarget.select?.(); justFocused.current = true; show(); }}
        onMouseUp={event => { if (justFocused.current) event.preventDefault(); justFocused.current = false; }}
        onClick={show} onBlur={() => { justFocused.current = false; commitDraft(); }} onKeyDown={keyDown}
        onChange={event => { setDraft(event.target.value); setNavigated(false); setActive(-1); if (!open) { setOpen(true); onOpen?.(); } }} />
      <button type="button" tabIndex={-1} aria-label={open ? "Hide branches" : "Show branches"} disabled={disabled}
        className="absolute inset-y-0 right-0 grid w-8 place-items-center text-muted hover:text-ink disabled:opacity-40"
        onMouseDown={event => event.preventDefault()} onClick={() => (open ? close() : show())}><ChevronDown aria-hidden="true" size={14} /></button>
    </div>
    {open ? <div ref={listRef} id={listId} role="listbox" aria-label={`${label} choices`} className="mt-1 max-h-64 overflow-y-auto rounded border border-line bg-canvas p-1">
      {options.length ? options.map((option, index) => {
        const heading = option.group !== lastGroup ? option.group : "";
        lastGroup = option.group;
        const selected = sameOption(option, value);
        return [
          heading ? <div key={`${option.group}-heading`} role="presentation" className="px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">{heading}</div> : null,
          <div key={`${option.group}:${option.type}:${option.name}`} id={`${id}-option-${index}`} data-index={index} role="option"
            aria-label={`${option.type === "remote" ? "Remote" : "Local"} branch ${option.name}`} aria-selected={selected} aria-disabled={option.reason ? true : undefined}
            title={option.reason || option.name}
            className={`flex min-h-7 cursor-default items-center gap-2 rounded px-2 py-1 ${index === active ? "bg-slate-100 text-ink" : ""} ${option.reason ? "opacity-50" : "hover:bg-slate-50"}`}
            onMouseDown={event => event.preventDefault()} onMouseEnter={() => { if (!option.reason) setActive(index); }} onClick={() => choose(option)}>
            <span className={`min-w-0 flex-1 break-all ${selected ? "font-semibold" : ""}`}>{option.name}</span>
            {option.group === "Recommended" ? <span className="shrink-0 text-[10px] text-muted">{option.type === "remote" ? "Remote" : "Local"}</span> : null}
            {option.reason ? <span className="shrink-0 text-[10px] text-muted">{option.reason}</span> : null}
          </div>,
        ];
      }) : <p className="px-2 py-1.5 text-muted">No branches match “{draft?.trim()}”.</p>}
    </div> : null}
  </div>;
}
