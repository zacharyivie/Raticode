import { useEffect, useState } from "react";
import { Brain, Check, Copy, Undo2 } from "lucide-react";
import { formatWhen } from "../lib/remHome.js";
import { addRemMemory, forgetRemMemory, loadRemMemories, saveRemMemory, setRemMemoryMode, undoRemMemory } from "../lib/remMemory.js";

const MODES = [
  { value: "on", label: "On", hint: "Rem uses what it remembers and saves new facts and preferences you state." },
  { value: "paused", label: "Paused", hint: "Rem uses what it remembers but saves nothing new. Asking it to correct or forget something still works." },
  { value: "off", label: "Off", hint: "Rem neither uses nor saves memories. Nothing here is deleted." },
];

// A memory's end date as a short day ("Dec 1", with the year when it isn't this year's).
export function endDay(until, now = new Date()) {
  const day = new Date(until);
  if (Number.isNaN(day.getTime())) return "";
  return day.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(day.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

// YYYY-MM-DD for a date input, in local time.
const inputDay = value => {
  const day = new Date(value);
  return Number.isNaN(day.getTime()) ? "" : `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
};

// The filter's choices besides a project root.
const ALL = "*";
const EVERY_PROJECT = "";

// Where a memory applies: every project, or one project picked from the open ones.
// A project that is no longer open stays listed while a memory still belongs to it.
function ProjectPicker({ label, value, projects, disabled, onChange }) {
  return <label className="rem-memory-project">
    <span>{label}</span>
    <select aria-label={label} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      <option value={EVERY_PROJECT}>Every project</option>
      {projects.map(project => <option key={project.root} value={project.root} title={project.root}>{project.name}{project.open === false ? " (not open)" : ""}</option>)}
    </select>
  </label>;
}

// What Rem remembers between conversations. Each fact can be corrected,
// moved to a project or forgotten without clearing the rest of Rem's history.
export default function RemMemoryPanel({ onClose, now = new Date() }) {
  const [memories, setMemories] = useState(null);
  const [projects, setProjects] = useState([]);
  const [filter, setFilter] = useState(ALL);
  const [draftProject, setDraftProject] = useState(EVERY_PROJECT);
  const [mode, setMode] = useState("on");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  // { id, day }: the memory whose end date is being chosen, and the day picked so far.
  const [ending, setEnding] = useState(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const show = result => { setMemories(result.memories); setMode(result.mode); setProjects(result.projects || []); };
  useEffect(() => {
    let current = true;
    loadRemMemories().then(result => { if (current) show(result); }, failure => { if (current) setError(failure.message); });
    return () => { current = false; };
  }, []);
  async function apply(action, after) {
    setBusy(true);
    setError("");
    try {
      show(await action());
      setEditing(null);
      setEnding(null);
      after?.();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  async function copyAll() {
    const text = (memories || []).map(memory => `- ${memory.text}`).join("\n");
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { setError("Couldn't copy to the clipboard."); }
  }
  const hint = MODES.find(item => item.value === mode)?.hint;
  const openProjects = projects.filter(project => project.open !== false);
  // A filter for a project that no memory or open project names any more shows everything.
  const shownFilter = filter === ALL || filter === EVERY_PROJECT || projects.some(project => project.root === filter) ? filter : ALL;
  const shown = memories?.filter(memory => shownFilter === ALL || (memory.project || EVERY_PROJECT) === shownFilter);
  const pickable = memory => memory.project && !openProjects.some(project => project.root === memory.project)
    ? [...openProjects, { root: memory.project, name: memory.project_name || memory.project, open: false }]
    : openProjects;
  const changes = (memory, edit) => ({
    ...(edit.text !== memory.text ? { text: edit.text } : {}),
    ...(edit.project !== (memory.project || EVERY_PROJECT) ? { project: edit.project || null } : {}),
  });
  return <div className="rem-clear-confirm rem-memory-panel" role="dialog" aria-labelledby="rem-memory-title">
    <h3 id="rem-memory-title">What Rem remembers</h3>
    <p>Rem saves facts and preferences you state so it doesn’t have to ask again, and you can ask it in chat to change or forget one. A memory tagged with a project only goes to work in that project. A memory with an end date stops going to Rem after that day and stays here, dimmed, until you keep or forget it. Each message sends these to the chat model’s provider, so Rem never keeps passwords, keys or tokens.</p>
    <fieldset className="rem-memory-mode" disabled={busy || memories === null}>
      <legend>Memory</legend>
      <div role="radiogroup" aria-label="Memory">
        {MODES.map(item => <label key={item.value} data-checked={mode === item.value || undefined}>
          <input type="radio" name="rem-memory-mode" value={item.value} checked={mode === item.value} onChange={() => apply(() => setRemMemoryMode(item.value))} />
          {item.label}
        </label>)}
      </div>
      {hint ? <small>{hint}</small> : null}
    </fieldset>
    {error ? <p role="alert" className="rem-memory-error">{error}</p> : null}
    {memories === null && !error ? <p role="status">Loading…</p> : null}
    {memories?.length === 0 ? <p>Rem hasn’t saved anything yet.</p> : null}
    {memories?.length && projects.length ? <label className="rem-memory-project rem-memory-filter">
      <span>Show</span>
      <select aria-label="Show" value={shownFilter} onChange={event => setFilter(event.target.value)}>
        <option value={ALL}>All memories</option>
        <option value={EVERY_PROJECT}>For every project</option>
        {projects.map(project => <option key={project.root} value={project.root} title={project.root}>{project.name}</option>)}
      </select>
    </label> : null}
    {memories?.length && shown.length === 0 ? <p>No memories here yet.</p> : null}
    {shown?.length ? <ul className="rem-memory-list">
      {shown.map(memory => <li key={memory.id} data-ended={memory.ended ? true : undefined}>
        {editing?.id === memory.id ? <>
          <textarea aria-label="Memory text" value={editing.text} maxLength={2000} rows={3} disabled={busy} onChange={event => setEditing({ ...editing, text: event.target.value })} />
          <ProjectPicker label="Applies to" value={editing.project} projects={pickable(memory)} disabled={busy} onChange={project => setEditing({ ...editing, project })} />
          <div>
            <button type="button" disabled={busy || !editing.text.trim()} onClick={() => {
              const changed = changes(memory, editing);
              return Object.keys(changed).length ? apply(() => saveRemMemory(memory.id, changed)) : setEditing(null);
            }}>Save</button>
            <button type="button" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </> : ending?.id === memory.id ? <>
          <span>{memory.text}</span>
          <label className="rem-memory-project rem-memory-until">
            <span>Rem uses this through</span>
            <input type="date" aria-label="End date" value={ending.day} min={inputDay(now)} disabled={busy} onChange={event => setEnding({ ...ending, day: event.target.value })} />
          </label>
          <div>
            <button type="button" disabled={busy || !ending.day} onClick={() => apply(() => saveRemMemory(memory.id, { until: ending.day }))}>Save</button>
            {memory.until ? <button type="button" disabled={busy} onClick={() => apply(() => saveRemMemory(memory.id, { until: null }))}>No end date</button> : null}
            <button type="button" disabled={busy} onClick={() => setEnding(null)}>Cancel</button>
          </div>
        </> : <>
          <span>{memory.text}</span>
          <small>
            {memory.project ? <span className="rem-memory-tag" title={`Rem uses this only for work in ${memory.project}`}>{memory.project_name || memory.project}</span> : null}
            {memory.until ? <span className="rem-memory-end" title={memory.ended ? "Rem no longer uses this. Keep it to use it again." : "Rem stops using this after this day."}>{memory.ended ? `Ended ${endDay(memory.until, now)}` : `Until ${endDay(memory.until, now)}`}</span> : null}
            {[formatWhen(memory.created_at, now, { past: true }), memory.added_by === "user" ? "added by you" : "", memory.edited_at ? "edited" : ""].filter(Boolean).join(" · ")}
          </small>
          <div>
            {memory.ended ? <button type="button" disabled={busy} title="Remove the end date so Rem uses this again" onClick={() => apply(() => saveRemMemory(memory.id, { until: null }))}>Keep</button> : <>
              <button type="button" disabled={busy} onClick={() => { setEnding(null); setEditing({ id: memory.id, text: memory.text, project: memory.project || EVERY_PROJECT }); }}>Edit</button>
              <button type="button" disabled={busy} onClick={() => { setEditing(null); setEnding({ id: memory.id, day: memory.until ? inputDay(memory.until) : "" }); }}>Set an end date</button>
            </>}
            <button type="button" data-danger="true" disabled={busy} onClick={() => apply(() => forgetRemMemory(memory.id))}>Forget</button>
          </div>
        </>}
      </li>)}
    </ul> : null}
    <div className="rem-memory-add">
      <textarea aria-label="Something for Rem to remember" placeholder="Add something for Rem to remember" value={draft} maxLength={2000} rows={2} disabled={busy || memories === null} onChange={event => setDraft(event.target.value)} />
      {openProjects.length ? <ProjectPicker label="For" value={openProjects.some(project => project.root === draftProject) ? draftProject : EVERY_PROJECT} projects={openProjects} disabled={busy || memories === null} onChange={setDraftProject} /> : null}
      <button type="button" disabled={busy || !draft.trim()} onClick={() => apply(() => addRemMemory(draft, openProjects.some(project => project.root === draftProject) ? draftProject : ""), () => setDraft(""))}>Add</button>
    </div>
    <div>
      <button type="button" disabled={!memories?.length} onClick={copyAll} title="Copy every memory as a list">
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}<span aria-live="polite">{copied ? "Copied" : "Copy all"}</span>
      </button>
      <button type="button" onClick={onClose}>Done</button>
    </div>
  </div>;
}

const quoted = entry => entry.removed ? "a memory you later forgot" : `“${entry.text}”`;

// Under a reply: what it saved, changed or forgot, with Undo. The phone shows the same note.
export function MemoryNote({ messageId, memory }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!memory) return null;
  const lines = [
    ...(memory.saved || []).map(entry => ({ key: `s-${entry.id}`, text: entry.until && !entry.removed ? `Saved to memory until ${endDay(entry.until)}: ${quoted(entry)}` : `Saved to memory: ${quoted(entry)}` })),
    ...(memory.updated || []).map(entry => ({ key: `u-${entry.id}`, text: `Updated memory: ${quoted(entry)}` })),
    ...(memory.forgotten || []).map(entry => ({ key: `f-${entry.id}`, text: `Forgot: ${quoted(entry)}` })),
  ];
  if (memory.refused) lines.push({ key: "refused", text: memory.refused === 1 ? "Didn’t save 1 item that looked like a password, key or token." : `Didn’t save ${memory.refused} items that looked like passwords, keys or tokens.` });
  if (!lines.length) return null;
  const undoable = (memory.saved?.length || memory.updated?.length || memory.forgotten?.length) && !memory.undone_at;
  const undo = async () => {
    setBusy(true);
    setError("");
    try { await undoRemMemory(messageId); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  return <div className="rem-memory-note" data-undone={memory.undone_at ? true : undefined}>
    <Brain size={13} aria-hidden="true" />
    <ul>{lines.map(line => <li key={line.key}>{line.text}</li>)}</ul>
    {memory.undone_at ? <small>Undone</small> : undoable ? <button type="button" disabled={busy} onClick={undo}><Undo2 size={12} aria-hidden="true" />Undo</button> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}
