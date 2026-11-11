import { useState } from "react";
import { AlertTriangle, Plus, X } from "lucide-react";
import { assignmentLine, avatarColor, initials } from "../lib/remKanban.js";

export function PersonAvatar({ person, small = false }) {
  return <span className={`oh-av ${small ? "" : "big"}`} style={{ background: avatarColor(person.participant_id) }} aria-hidden="true">{initials(person.name)}</span>;
}

// Everyone who can hold tickets, and the projects each may work in on its own.
export default function KanbanPeople({ board, onSave, onShow }) {
  const groups = [];
  for (const person of board.participants) {
    const label = person.organization_name || "Coordinator";
    let group = groups.find(item => item.label === label);
    if (!group) groups.push(group = { label, people: [] });
    group.people.push(person);
  }
  return <div className="oh-view kb-people">
    <p className="oh-muted kb-people-lead">People work on their own only in the projects listed here. Work you ask Rem for directly isn&rsquo;t limited; an organization worker with projects stays inside them for all its work.</p>
    {groups.map(group => <section key={group.label} aria-label={group.label} className="kb-people-group">
      <h2>{group.label}</h2>
      <ul>{group.people.map(person => <PersonRow key={person.participant_id} person={person} board={board} onSave={onSave} onShow={onShow} />)}</ul>
    </section>)}
  </div>;
}

function PersonRow({ person, board, onSave, onShow }) {
  const [adding, setAdding] = useState(false);
  const name = root => board.projects.find(project => project.root === root)?.name || root;
  const left = person.assignable.filter(root => !person.projects.includes(root));
  const save = projects => { setAdding(false); onSave(person, projects); };
  return <li className="kb-person" data-participant={person.participant_id}>
    <PersonAvatar person={person} />
    <div className="kb-person-who">
      <button type="button" className="kb-person-name" onClick={() => onShow(person.participant_id)} title="Show this person's tickets">{person.name}</button>
      <span className="oh-muted">{[person.title, person.paused ? "On hold" : ""].filter(Boolean).join(" · ") || "\u00a0"}</span>
    </div>
    <div className="kb-person-projects">
      {person.projects.map(root => {
        const unavailable = person.unavailable.includes(root);
        return <span key={root} className="kb-chip" data-unavailable={unavailable || undefined} title={unavailable ? "Not available right now" : root}>
          {unavailable ? <AlertTriangle size={11} aria-hidden="true" /> : null}{name(root)}
          <button type="button" aria-label={`Remove ${name(root)} from ${person.name}`} onClick={() => save(person.projects.filter(item => item !== root))}><X size={11} /></button>
        </span>;
      })}
      {adding ? <select className="kb-input kb-add-project" autoFocus aria-label={`Add a project for ${person.name}`} defaultValue="" onBlur={() => setAdding(false)} onChange={event => { if (event.target.value) save([...person.projects, event.target.value]); }}>
        <option value="" disabled>Choose a project…</option>
        {left.map(root => <option key={root} value={root}>{name(root)}</option>)}
      </select> : left.length ? <button type="button" className="kb-chip kb-chip-add" onClick={() => setAdding(true)}><Plus size={11} aria-hidden="true" />Add project</button> : null}
    </div>
    <span className="oh-muted kb-person-line">{assignmentLine(person)}</span>
  </li>;
}
