import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { changeRemObjectives } from "../lib/remHomeStore.js";
import { OBJECTIVE_CRITERIA_MAX, OBJECTIVE_TEXT_MAX, draftChange, moveChange, objectiveDetails, objectivesCount, runningByObjective } from "../lib/remObjectives.js";
import { RemMenu } from "./RemHomeParts.jsx";

const OPEN_KEY = "raticode.rem.objectivesOpen";

function storedOpen() {
  try { return window.localStorage?.getItem(OPEN_KEY) !== "closed"; } catch { return true; }
}

// The add and edit form. The fields keep what was typed until Save, Enter or Cancel.
function ObjectiveForm({ item = null, onDone }) {
  const [text, setText] = useState(item?.text || "");
  const [doneWhen, setDoneWhen] = useState(item?.done_when || "");
  const [busy, setBusy] = useState(false);
  const save = async event => {
    event.preventDefault();
    const change = draftChange({ text, doneWhen }, item);
    if (!change) { onDone(); return; }
    setBusy(true);
    const saved = await changeRemObjectives(change);
    setBusy(false);
    if (saved) onDone();
  };
  return <form className="rem-objective-form" onSubmit={save} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onDone(); } }}>
    <input aria-label="Objective" placeholder="A goal for the whole session, e.g. Raise UX quality of the studio" maxLength={OBJECTIVE_TEXT_MAX} value={text} autoFocus onChange={event => setText(event.target.value)} />
    <input aria-label="Done when (optional)" placeholder="Done when… (leave empty for open-ended)" maxLength={OBJECTIVE_CRITERIA_MAX} value={doneWhen} onChange={event => setDoneWhen(event.target.value)} />
    <div className="rem-objective-form-actions">
      <button type="submit" disabled={busy || !text.trim()}>{item ? "Save" : "Add"}</button>
      <button type="button" onClick={onDone}>Cancel</button>
    </div>
  </form>;
}

function ObjectiveRow({ item, items, running }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  if (editing) return <li className="rem-objective" data-editing="true"><ObjectiveForm item={item} onDone={() => setEditing(false)} /></li>;
  if (confirming) {
    return <li className="rem-objective" data-confirming="true">
      <p>Remove <strong>{item.text}</strong>?</p>
      <div className="rem-task-confirm">
        <button type="button" data-danger="true" autoFocus onClick={() => { setConfirming(false); void changeRemObjectives({ action: "remove", id: item.id }); }}>Remove</button>
        <button type="button" onClick={() => setConfirming(false)}>Keep</button>
      </div>
    </li>;
  }
  const up = moveChange(items, item.id, -1);
  const down = moveChange(items, item.id, 1);
  return <li className="rem-objective" data-done={item.done || undefined} title={objectiveDetails(item)}>
    <input type="checkbox" checked={Boolean(item.done)} aria-label={`${item.done ? "Reopen" : "Check off"}: ${item.text}`}
      onChange={() => void changeRemObjectives({ action: item.done ? "uncheck" : "check", id: item.id })} />
    <span className="rem-objective-text">
      <span>{item.text}</span>
      {running ? <small>{running} {running === 1 ? "task" : "tasks"} on it</small> : null}
    </span>
    <RemMenu label={`${item.text} actions`} className="rem-objective-menu" trigger={<MoreHorizontal size={14} />} items={[
      { label: "Edit", icon: Pencil, onSelect: () => setEditing(true) },
      up ? { label: "Move up", icon: ArrowUp, onSelect: () => void changeRemObjectives(up) } : null,
      down ? { label: "Move down", icon: ArrowDown, onSelect: () => void changeRemObjectives(down) } : null,
      { label: "Remove", icon: Trash2, danger: true, onSelect: () => setConfirming(true) },
    ]} />
  </li>;
}

// The self-work objectives above Rem's tasks: a small checklist that folds away. Rem sets
// them when self-work starts and keeps working toward them; the user can change any of it.
export default function RemObjectives({ objectives, tasks }) {
  const [open, setOpen] = useState(storedOpen);
  const [adding, setAdding] = useState(false);
  const items = objectives?.items || [];
  const running = useMemo(() => runningByObjective(tasks), [tasks]);
  const toggle = next => {
    setOpen(next);
    try { window.localStorage?.setItem(OPEN_KEY, next ? "open" : "closed"); } catch { /* the choice just isn't kept */ }
  };
  return <section className="rem-side-group rem-side-objectives" aria-label="Objectives">
    <div className="rem-side-group-head">
      <button type="button" className="rem-side-group-heading" aria-expanded={open} onClick={() => toggle(!open)}
        title="Goals that guide Rem's self-work for the whole session. Rem works toward them; check one off when it's done.">
        {open ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
        <span>Objectives</span><small>{objectivesCount(objectives)}</small>
      </button>
      <button type="button" className="rem-side-icon rem-objectives-add" aria-label="Add objective" title="Add objective" onClick={() => { toggle(true); setAdding(true); }}><Plus size={13} /></button>
    </div>
    {open ? <>
      {items.length ? <ul className="rem-objectives">{items.map(item => <ObjectiveRow key={item.id} item={item} items={items} running={running.get(item.id) || 0} />)}</ul>
        : adding ? null : <p className="rem-objectives-empty">None yet. When self-work starts with none, Rem writes its own.</p>}
      {adding ? <ObjectiveForm onDone={() => setAdding(false)} /> : null}
    </> : null}
  </section>;
}
