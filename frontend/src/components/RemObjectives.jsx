import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, MoreHorizontal, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { askRemObjectives, changeRemObjectives, loadMoreProjectObjectives, loadMoreRemObjectives } from "../lib/remHomeStore.js";
import { ASK_REM_TITLE, OBJECTIVE_CRITERIA_MAX, OBJECTIVE_TEXT_MAX, askLine, draftChange, effortValue, moreToLoad, objectiveDetails, objectivesCount, projectSections, runningByObjective, splitObjectives, staleCount, threadsLine } from "../lib/remObjectives.js";
import { RemMenu } from "./RemHomeParts.jsx";
import { ProjectPicker } from "./RemMemoryPanel.jsx";
import SteadySpinner from "./SteadySpinner.jsx";

const OPEN_KEY = "raticode.rem.objectivesOpen";
// Each project's section remembers whether it is open, by the project's folder.
const PROJECT_OPEN_KEY = "raticode.rem.projectObjectivesOpen:";
// And whether its stale group is open, the same way.
const PROJECT_STALE_OPEN_KEY = "raticode.rem.projectStaleObjectivesOpen:";
const STALE_TITLE = "Done in earlier self-work sessions. They don't count toward this session's objectives.";

// Where a row's or form's change goes: the side pane's store by default, which shows checks
// at once; Settings > Rem sends them straight to the backend.
const ObjectiveChanges = createContext(changeRemObjectives);
// How a project's section loads its next page: the side pane's store, by project folder.
// Settings > Rem has none, so it points to the side pane instead.
const ProjectPages = createContext(loadMoreProjectObjectives);
// Rem's enabled projects, which an edit's Applies to lists after Every project.
const ObjectiveProjects = createContext([]);
// How Ask Rem starts for a project, by folder: the side pane's store by default; Settings >
// Rem sends it and reads the list again. Throws the backend's reason.
const ObjectiveAsk = createContext(askRemObjectives);

function storedOpen() {
  try { return window.localStorage?.getItem(OPEN_KEY) !== "closed"; } catch { return true; }
}

// The add and edit form. The fields keep what was typed until Save, Enter or Cancel. Editing,
// Applies to moves the objective between the global list and one of Rem's projects.
function ObjectiveForm({ item = null, project = "", onDone }) {
  const change = useContext(ObjectiveChanges);
  const projects = useContext(ObjectiveProjects);
  const [text, setText] = useState(item?.text || "");
  const [doneWhen, setDoneWhen] = useState(item?.done_when || "");
  const [appliesTo, setAppliesTo] = useState(project);
  const [busy, setBusy] = useState(false);
  const save = async event => {
    event.preventDefault();
    const draft = draftChange({ text, doneWhen, project: appliesTo }, item, project);
    if (!draft) { onDone(); return; }
    setBusy(true);
    const saved = await change(project && !item ? { ...draft, project } : draft);
    setBusy(false);
    if (saved) onDone();
  };
  return <form className="rem-objective-form" onSubmit={save} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onDone(); } }}>
    <input aria-label="Objective" placeholder="A goal for the whole session, e.g. Raise UX quality of the studio" maxLength={OBJECTIVE_TEXT_MAX} value={text} autoFocus onChange={event => setText(event.target.value)} />
    <input aria-label="Done when (optional)" placeholder="Done when… (leave empty for open-ended)" maxLength={OBJECTIVE_CRITERIA_MAX} value={doneWhen} onChange={event => setDoneWhen(event.target.value)} />
    {item && projects.length ? <ProjectPicker label="Applies to" value={appliesTo} projects={projects} disabled={busy} onChange={setAppliesTo} /> : null}
    <div className="rem-objective-form-actions">
      <button type="submit" disabled={busy || !text.trim()}>{item ? "Save" : "Add"}</button>
      <button type="button" onClick={onDone}>Cancel</button>
    </div>
  </form>;
}

// A row's text opens its details below it: the text loses its two-line clamp, and its
// finish line and, once done, how it was met show underneath. Enter and Space are handled here, with the browser's own click turned off,
// so a key press toggles it once; Escape closes it.
function ObjectiveRow({ item, running, scope = "", stale = false, expanded = false, onToggle }) {
  const changeObjectives = useContext(ObjectiveChanges);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const detailsId = `rem-objective-details-${item.id}`;
  const onKeyDown = event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onToggle(!expanded); }
    else if (event.key === "Escape" && expanded) { event.stopPropagation(); onToggle(false); }
  };
  if (editing) return <li className="rem-objective" data-editing="true"><ObjectiveForm item={item} project={scope} onDone={() => setEditing(false)} /></li>;
  if (confirming) {
    return <li className="rem-objective" data-confirming="true">
      <p>Remove <strong>{item.text}</strong>?</p>
      <div className="rem-task-confirm">
        <button type="button" data-danger="true" autoFocus onClick={() => { setConfirming(false); void changeObjectives({ action: "remove", id: item.id }); }}>Remove</button>
        <button type="button" onClick={() => setConfirming(false)}>Keep</button>
      </div>
    </li>;
  }
  const details = objectiveDetails(item);
  return <li className="rem-objective" data-done={item.done || undefined} data-stale={stale || undefined} data-expanded={expanded || undefined} title={details}>
    <input type="checkbox" checked={Boolean(item.done)} aria-label={`${item.done ? "Reopen" : "Check off"}: ${item.text}`}
      onChange={() => void changeObjectives({ action: item.done ? "uncheck" : "check", id: item.id })} />
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

// Loads the next page with ``onLoad`` when it scrolls into view, or when it's clicked.
function LoadMore({ onLoad }) {
  const ref = useRef(null);
  const load = useRef(onLoad);
  load.current = onLoad;
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver !== "function") return undefined;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) void load.current(); });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return <button ref={ref} type="button" className="rem-objectives-more" onClick={() => void load.current()}>Show more</button>;
}

// The folded group of stale objectives below the current ones, with how many there are
// (loaded or not). ``more`` goes at its end, for the next page while it's open.
function StaleFold({ count, open, onToggle, rows, more = null }) {
  return <div className="rem-objectives-stale">
    <button type="button" className="rem-objectives-stale-heading" aria-expanded={open} onClick={() => onToggle(!open)} title={STALE_TITLE}>
      {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
      <span>Stale</span><small>{count}</small>
    </button>
    {open ? <ul className="rem-objectives" aria-label="Stale objectives">{rows}</ul> : null}
    {more}
  </div>;
}

// The self-work objectives above Rem's tasks: a small checklist that folds away. Rem sets
// them when self-work starts and keeps working toward them; the user can change any of it.
// Newest first, open above done; what earlier sessions finished is stale and folds away below.
export default function RemObjectives({ objectives, tasks, projects = [], onEffort = null }) {
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
  return <ObjectiveProjects.Provider value={pickable(projects)}><section className="rem-side-group rem-side-objectives" aria-label="Objectives">
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
      {moreCurrent ? <LoadMore onLoad={loadMoreRemObjectives} /> : null}
      {stales ? <StaleFold count={stales} open={staleOpen} onToggle={setStaleOpen}
        rows={stale.map(item => <ObjectiveRow key={item.id} {...rowProps(item)} stale running={0} />)}
        more={moreStale ? <LoadMore onLoad={loadMoreRemObjectives} /> : null} /> : null}
      <ProjectObjectivesList objectives={objectives} projects={projects} running={running} onEffort={onEffort} />
    </> : null}
  </section></ObjectiveProjects.Provider>;
}

// The projects an edit can move an objective to: Rem's enabled ones, by folder.
const pickable = (projects = []) => (projects || []).filter(row => row?.root);

// A project section's folds are closed until the user opens them, by the project's folder.
function storedProjectOpen(key, root) {
  try { return window.localStorage?.getItem(key + root) === "open"; } catch { return false; }
}
function keepProjectOpen(key, root, open) {
  try { window.localStorage?.setItem(key + root, open ? "open" : "closed"); } catch { /* the choice just isn't kept */ }
}

// The level of effort: the project's share of self-work's threads, in percent. What is typed
// stays until Enter or leaving the field; an empty or unreadable entry puts the saved number
// back, and the backend rescales the other projects so they total 100.
function EffortField({ name, value, max = 100, onCommit }) {
  const [draft, setDraft] = useState(String(value));
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(String(value)); }, [value]);
  const commit = () => {
    focused.current = false;
    const next = effortValue(draft, max);
    setDraft(String(next ?? value));
    if (next !== null && next !== value) onCommit(next);
  };
  return <label className="rem-project-effort">
    <span>Level of effort</span>
    <input aria-label={`Level of effort for ${name}`} inputMode="numeric" value={draft}
      onFocus={() => { focused.current = true; }} onChange={event => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={event => {
        if (event.key === "Enter") event.currentTarget.blur();
        else if (event.key === "Escape") { event.stopPropagation(); setDraft(String(value)); focused.current = false; event.currentTarget.blur(); }
      }} />
    <span aria-hidden="true">%</span>
  </label>;
}

// Ask Rem for one project: Rem's model reads it and adds three to five broad, open-ended
// objectives. The backend reports how it goes (``ask``), so a press from the phone or the CLI
// shows here too; this window's own press shows at once and keeps a refusal until the next.
function AskRem({ section }) {
  const ask = useContext(ObjectiveAsk);
  const [local, setLocal] = useState(null);
  const line = askLine(section.ask, local);
  const press = async () => {
    setLocal({ sending: true });
    try {
      await ask(section.root);
      setLocal(null);
    } catch (failure) {
      setLocal({ error: `Couldn't ask Rem. ${failure?.message || ""}`.trim() });
    }
  };
  return <div className="rem-project-ask" data-state={line.busy ? "asking" : section.ask?.state || undefined}>
    <button type="button" className="rem-project-ask-button" disabled={line.busy} onClick={() => void press()}
      title={ASK_REM_TITLE} aria-label={`Ask Rem for ${section.name} objectives`}>
      {line.busy ? <SteadySpinner size={12} className="rem-spin" aria-hidden="true" /> : <Sparkles size={12} aria-hidden="true" />}
      <span>{line.busy ? "Asking Rem…" : "Ask Rem"}</span>
    </button>
    {line.text ? <p className="rem-project-ask-line" role={line.tone === "error" ? "alert" : "status"} data-tone={line.tone}>{line.text}</p> : null}
  </div>;
}

// One of Rem's projects under Objectives: its own objectives, which steer only its tickets
// and research, its level of effort and the threads it holds now against its share. Folded
// by default, with its stale ones folded below like the global list's; both choices are kept
// per project. A long list pages with Show more, one project at a time.
function ProjectObjectives({ section, running, onEffort }) {
  const loadPage = useContext(ProjectPages);
  const [open, setOpen] = useState(() => storedProjectOpen(PROJECT_OPEN_KEY, section.root));
  const [staleOpen, setStaleOpen] = useState(() => storedProjectOpen(PROJECT_STALE_OPEN_KEY, section.root));
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState(null);
  const { current, stale } = useMemo(() => splitObjectives(section.objectives?.items || []), [section.objectives]);
  const toggle = next => { setOpen(next); keepProjectOpen(PROJECT_OPEN_KEY, section.root, next); };
  const toggleStale = next => { setStaleOpen(next); keepProjectOpen(PROJECT_STALE_OPEN_KEY, section.root, next); };
  const rowProps = item => ({ item, expanded: openId === item.id, onToggle: next => setOpenId(next ? item.id : null) });
  const count = objectivesCount(section.objectives);
  const stales = staleCount(section.objectives);
  // As in the global list: current ones first, stale ones only while their group is open.
  const moreCurrent = moreToLoad(section.objectives, false);
  const moreStale = !moreCurrent && staleOpen && moreToLoad(section.objectives, true);
  const more = loadPage ? <LoadMore onLoad={() => loadPage(section.root)} /> : <p className="rem-objectives-empty">More in Rem&rsquo;s side pane.</p>;
  return <section className="rem-project-objectives" aria-label={`${section.name} objectives`} data-project={section.root}>
    <div className="rem-side-group-head">
      <button type="button" className="rem-project-heading" aria-expanded={open} onClick={() => toggle(!open)} title={section.root}>
        {open ? <ChevronDown size={11} aria-hidden="true" /> : <ChevronRight size={11} aria-hidden="true" />}
        <span className="rem-project-name">{section.name}</span>
        {count ? <small>{count}</small> : null}
        <small className="rem-project-share" title={`Level of effort ${section.effort_percent}%: ${threadsLine(section)} now`}>{section.effort_percent}% · {threadsLine(section)}</small>
      </button>
      <button type="button" className="rem-side-icon rem-objectives-add" aria-label={`Add objective to ${section.name}`} title={`Add an objective for ${section.name} only`}
        onClick={() => { toggle(true); setAdding(true); }}><Plus size={13} /></button>
    </div>
    {open ? <div className="rem-project-body">
      {onEffort ? <EffortField name={section.name} value={section.effort_percent} onCommit={percent => onEffort(section.root, percent)} /> : null}
      {current.length ? <ul className="rem-objectives">{current.map(item => <ObjectiveRow key={item.id} {...rowProps(item)} scope={section.root} running={running.get(item.id) || 0} />)}</ul>
        : adding ? null : <p className="rem-objectives-empty">None of its own. The global objectives above steer it; add one here for {section.name} only.</p>}
      {adding ? <ObjectiveForm project={section.root} onDone={() => setAdding(false)} /> : null}
      <AskRem section={section} />
      {moreCurrent ? more : null}
      {stales ? <StaleFold count={stales} open={staleOpen} onToggle={toggleStale}
        rows={stale.map(item => <ObjectiveRow key={item.id} {...rowProps(item)} scope={section.root} stale running={0} />)}
        more={moreStale ? more : null} /> : null}
    </div> : null}
  </section>;
}

// The child sections under Objectives, one per project Rem works in on its own. Settings > Rem
// shows the same, with its changes sent straight to the backend (``onChange``); its list is
// the self-work view's first page of each project, so longer lists page in the side pane.
export function ProjectObjectivesList({ objectives, projects = [], running = null, onEffort = null, onChange = null, onAsk = null }) {
  const sections = projectSections(objectives, projects);
  const counts = running || new Map();
  if (!sections.length) return null;
  const list = <ObjectiveProjects.Provider value={pickable(projects)}><div className="rem-project-objectives-list">
    {sections.map(section => <ProjectObjectives key={section.root} section={section} running={counts} onEffort={onEffort} />)}
  </div></ObjectiveProjects.Provider>;
  const asked = onAsk ? <ObjectiveAsk.Provider value={onAsk}>{list}</ObjectiveAsk.Provider> : list;
  return onChange ? <ObjectiveChanges.Provider value={onChange}><ProjectPages.Provider value={null}>{asked}</ProjectPages.Provider></ObjectiveChanges.Provider> : asked;
}
