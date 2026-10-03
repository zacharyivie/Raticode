import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

function BranchCheckbox({ label, checked, mixed = false, onChange, children }) {
  const input = useRef(null);
  useEffect(() => { if (input.current) input.current.indeterminate = mixed; }, [mixed]);
  return <label className="flex min-h-7 cursor-pointer items-center gap-2 rounded px-2 py-1 text-[11px] text-ink hover:bg-slate-100">
    <input ref={input} type="checkbox" aria-label={label} checked={checked} onChange={onChange} className="h-3.5 w-3.5 shrink-0 accent-brand focus-visible:outline-brand" />
    <span className="min-w-0 break-all">{children || label}</span>
  </label>;
}

export default function HistoryBranchFilter({ refs, selection, onChange }) {
  const [open, setOpen] = useState(false);
  const container = useRef(null);
  const trigger = useRef(null);
  const panelId = useId();
  const current = refs.find(ref => ref.current);
  const allIds = [...refs.map(ref => ref.id), ...(!current ? ["HEAD"] : [])];
  const selected = selection == null ? allIds : selection;
  const summary = selection == null ? "All branches and tags" : !selection.length ? "Select branches" : selection.length === 1 ? refs.find(ref => ref.id === selection[0])?.name || "Current branch" : `${selection.length} selected`;

  useEffect(() => {
    if (!open) return undefined;
    const dismiss = event => { if (!container.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function toggle(ids, checked) {
    onChange(checked ? [...new Set([...selected, ...ids])] : selected.filter(id => !ids.includes(id)));
  }

  return <div ref={container} className="relative" onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false); }} onKeyDown={event => {
    if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
  }}>
    <button ref={trigger} type="button" aria-label="History branches" aria-expanded={open} aria-controls={panelId} className="flex h-7 w-full items-center justify-between gap-2 rounded border border-line bg-white px-2 text-left text-[11px] text-ink focus-visible:outline-brand" onClick={() => setOpen(value => !value)}>
      <span className="truncate">{summary}</span><ChevronDown size={12} className="shrink-0" />
    </button>
    {open ? <div id={panelId} role="group" aria-label="Filter history branches" className="absolute left-0 right-0 top-full z-30 mt-1 rounded border border-line bg-white shadow-panel">
      <div className="flex flex-wrap gap-1 border-b border-line p-1.5">
        {[["All", null], ["Clear", []], ["Current branch", [current?.id || "HEAD"]]].map(([label, value]) => <button key={label} type="button" className="rounded px-2 py-1 text-[11px] text-ink hover:bg-slate-100 focus-visible:outline-brand" onClick={() => onChange(value)}>{label}</button>)}
      </div>
      <div className="max-h-64 overflow-y-auto p-1">
        {!current ? <BranchCheckbox label="Current branch" checked={selected.includes("HEAD")} onChange={event => toggle(["HEAD"], event.target.checked)} /> : null}
        {[["local", "Local branches"], ["remote", "Remote branches"], ["tag", "Tags"]].map(([type, label]) => {
          const group = refs.filter(ref => ref.type === type);
          if (!group.length) return null;
          const ids = group.map(ref => ref.id);
          const count = ids.filter(id => selected.includes(id)).length;
          return <div key={type} role="group" aria-label={`${label} filter options`} className="py-1">
            <BranchCheckbox label={label} checked={count === ids.length} mixed={count > 0 && count < ids.length} onChange={event => toggle(ids, event.target.checked)}><span className="font-semibold">{label}</span></BranchCheckbox>
            <div className="pl-3">{group.map(ref => <BranchCheckbox key={ref.id} label={`${type === "tag" ? "Tag" : type === "remote" ? "Remote branch" : "Local branch"} ${ref.name}`} checked={selected.includes(ref.id)} onChange={event => toggle([ref.id], event.target.checked)}>{ref.name}{ref.current ? <span className="ml-1 text-muted">current</span> : null}</BranchCheckbox>)}</div>
          </div>;
        })}
      </div>
    </div> : null}
  </div>;
}
