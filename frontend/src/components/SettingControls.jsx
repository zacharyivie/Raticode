import { useEffect, useRef, useState } from "react";

// Controls shared by Settings rows and the sections that load their own values.

export function SwitchControl({ checked, disabled = false, label, onChange }) {
  return (
    <button
      aria-checked={checked}
      aria-label={label}
      className="ml-auto grid h-7 w-10 place-items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:cursor-wait disabled:opacity-50"
      disabled={disabled}
      role="switch"
      type="button"
      onClick={() => onChange(!checked)}
    >
      <span
        aria-hidden="true"
        className={`relative h-[18px] w-8 rounded-full border transition-[background-color,border-color] duration-150 ${
          checked ? "border-brand bg-brand" : "border-line bg-slate-100"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-150 ease-out motion-reduce:transition-none ${
            checked ? "translate-x-3.5" : "translate-x-0"
          }`}
        />
      </span>
    </button>
  );
}

// Keeps what is typed until it is committed on blur or Enter, so clearing the field
// doesn't snap back to the saved value.
export function NumberControl({ max, min, onCommit, step = 1, suffix = "", value }) {
  const [draft, setDraft] = useState(String(value));
  const focusedRef = useRef(false);
  useEffect(() => {
    if (!focusedRef.current) setDraft(String(value));
  }, [value]);
  function commit() {
    focusedRef.current = false;
    const parsed = Number(draft);
    if (!Number.isFinite(parsed)) {
      setDraft(String(value));
      return;
    }
    const next = Math.min(max, Math.max(min, parsed));
    setDraft(String(next));
    onCommit(next);
  }
  return (
    <div className="relative">
      <input
        className="h-8 w-full rounded-md border border-line bg-white px-2 pr-10 text-right text-xs text-ink outline-none transition focus:border-brand"
        inputMode="decimal"
        value={draft}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={() => { focusedRef.current = true; }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(String(value));
            event.currentTarget.blur();
          }
          if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            const parsed = Number(draft);
            const base = Number.isFinite(parsed) ? parsed : value;
            setDraft(String(base + (event.key === "ArrowUp" ? step : -step)));
          }
        }}
      />
      {suffix ? <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted">{suffix}</span> : null}
    </div>
  );
}
