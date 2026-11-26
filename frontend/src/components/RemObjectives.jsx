import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { changeRemObjectives, loadMoreRemObjectives } from "../lib/remHomeStore.js";
import { OBJECTIVE_CRITERIA_MAX, OBJECTIVE_TEXT_MAX, draftChange, moreToLoad, objectiveDetails, objectivesCount, runningByObjective, splitObjectives, staleCount } from "../lib/remObjectives.js";
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

// A row's text opens its details below it: the text loses its two-line clamp, and its
// finish line and, once done, how it was met show underneath. Enter and Space are handled here, with the browser's own click turned off,
// so a key press toggles it once; Escape closes it.
function ObjectiveRow({ item, running, stale = false, expanded = false, onToggle }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const detailsId = `rem-objective-details-${item.id}`;
  const onKeyDown = event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onToggle(!expanded); }
    else if (event.key === "Escape" && expanded) { event.stopPropagation(); onToggle(false); }
  };
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
  const details = objectiveDetails(item);
  return <li className="rem-objective" data-done={item.done || undefined} data-stale={stale || undefined} data-expanded={expanded || undefined} title={details}>
    <input type="checkbox" checked={Boolean(item.done)} aria-label={`${item.done ? "Reopen" : "Check off"}: ${item.text}`}
      onChange={() => void changeRemObjectives({ action: item.done ? "uncheck" : "check", id: item.id })} />
    <button type="button" className="rem-objective-text" aria-expanded={expanded} aria-controls={expanded ? detailsId : undefined}
      onClick={() => onToggle(!expanded)} onKeyDown={onKeyDown}>
      <span>{item.text}</span>
      {running ? <small>{running} {running === 1 ? "task" : "tasks"} on it</small> : null}
    </button>
    <RemMenu label={`${item.text} actions`} className="rem-objective-menu" trigger={<MoreHorizontal size={14} />} items={[
      { label: "Edit", icon: Pencil, onSelect: () => setEditing(true) },
      { label: "Remove", icon: Trash2, danger: true, onSelect: () => setConfirming(true) },
    ]} />
    {expanded ? <div className="rem-objective-details" id={detailsId}>
      {details.split("\n").map(line => <p key={line}>{line}</p>)}
    </div> : null}
  </li>;
}

// Loads the next page when it scrolls into view, or when it's clicked.
function LoadMore() {
  const ref = useRef(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver !== "function") return undefined;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) void loadMoreRemObjectives(); });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return <button ref={ref} type="button" className="rem-objectives-more" onClick={() => void loadMoreRemObjectives()}>Show more</button>;
}

// The self-work objectives above Rem's tasks: a small checklist that folds away. Rem sets
// them when self-work starts and keeps working toward them; the user can change any of it.
// Newest first, open above done; what earlier sessions finished is stale and folds away below.
export default function RemObjectives({ objectives, tasks }) {
  const [open, setOpen] = useState(storedOpen);
  const [adding, setAdding] = useState(false);
  const [staleOpen, setStaleOpen] = useState(false);
  // The one row whose details are open.
  const [openId, setOpenId] = useState(null);
  const rowProps = item => ({ item, expanded: openId === item.id, onToggle: next => setOpenId(next ? item.id : null) });
  const items = objectives?.items || [];
  const { current, stale } = useMemo(() => splitObjectives(items), [items]);
  const stales = staleCount(objectives);
  // Current ones not all loaded yet: the next page belongs above the stale group. Stale
  // ones load only while their group is open.
  const moreCurrent = moreToLoad(objectives, false);
  const moreStale = !moreCurrent && staleOpen && moreToLoad(objectives, true);
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
      {current.length ? <ul className="rem-objectives">{current.map(item => <ObjectiveRow key={item.id} {...rowProps(item)} running={running.get(item.id) || 0} />)}</ul>
        : adding ? null : <p className="rem-objectives-empty">{stales ? "None for this session yet." : "None yet. When self-work starts with none, Rem writes its own, each with a finish line."}</p>}
      {adding ? <ObjectiveForm onDone={() => setAdding(false)} /> : null}
      {moreCurrent ? <LoadMore /> : null}
      {stales ? <div className="rem-objectives-stale">
        <button type="button" className="rem-objectives-stale-heading" aria-expanded={staleOpen} onClick={() => setStaleOpen(!staleOpen)}
          title="Done in earlier self-work sessions. They don't count toward this session's objectives.">
          {staleOpen ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
          <span>Stale</span><small>{stales}</small>
        </button>
        {staleOpen ? <ul className="rem-objectives" aria-label="Stale objectives">{stale.map(item => <ObjectiveRow key={item.id} {...rowProps(item)} stale running={0} />)}</ul> : null}
        {moreStale ? <LoadMore /> : null}
      </div> : null}
    </> : null}
  </section>;
}
