import { useRef, useState } from "react";
import { Building2, ChevronRight, Folder, Users } from "lucide-react";
import { initials, plural, reportingHierarchy, teamGroups } from "../lib/organizations.js";

export default function OrganizationChart({ config, onEmployee, onCollection }) {
  const [view, setView] = useState("people");
  const drag = useRef(null);
  const suppressClick = useRef(false);
  const groups = teamGroups(config);
  function startPan(event) {
    if (event.button !== 0 || event.pointerType === "touch") return;
    suppressClick.current = false;
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop, moved: false };
  }
  function pan(event) {
    const start = drag.current;
    if (!start || start.id !== event.pointerId) return;
    if (!(event.buttons & 1)) { endPan(event); return; }
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    if (!start.moved && Math.hypot(dx, dy) < 5) return;
    start.moved = true; suppressClick.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.dataset.panning = "true";
    event.currentTarget.scrollLeft = start.left - dx;
    event.currentTarget.scrollTop = start.top - dy;
    event.preventDefault();
  }
  function endPan(event) {
    drag.current = null;
    delete event.currentTarget.dataset.panning;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function collections(manager) {
    return [
      ...(view === "people" ? config.teams.filter(team => (team.manager || null) === manager).map(team => ({ ...team, kind: "teams" })) : []),
      ...config.projects.filter(project => (project.owner || null) === manager).map(project => ({ ...project, kind: "projects" })),
    ];
  }
  function links(manager) {
    const entries = collections(manager);
    return entries.length > 0 && <ul className="org-chart-links" aria-label="Teams and initiatives">{entries.map(entry => {
      const Icon = entry.kind === "teams" ? Users : Folder;
      return <li key={`${entry.kind}:${entry.id}`}><button type="button" onClick={() => onCollection(entry.kind, entry.id)} aria-label={`${entry.kind === "teams" ? "Team" : "Initiative"}: ${entry.name || entry.id}`}><Icon size={13} aria-hidden="true" /><span>{entry.name || entry.id}</span><ChevronRight size={12} aria-hidden="true" /></button></li>;
    })}</ul>;
  }
  function branch(employee) {
    return <li key={employee.id} className="org-chart-branch">
      <div className="org-chart-node">
        <button className="org-chart-person" type="button" onClick={() => onEmployee(employee.id)}>
          <span className="org-initial">{initials(employee.name)}</span><span><strong>{employee.name}</strong><small>{employee.title || employee.role || "Rem employee"}</small><small>Reports to {config.employees.find(person => person.id === employee.reportsTo)?.name || "Organization owner"}</small></span><ChevronRight size={14} aria-hidden="true" />
        </button>
        {links(employee.id)}
      </div>
      {employee.children.length > 0 && <ul aria-label={`Reports to ${employee.name}`}>{employee.children.map(branch)}</ul>}
    </li>;
  }
  return <section aria-label="Organization chart">
    <div className="org-section-heading"><div><h3>Org chart</h3><p className="org-note">Drag to explore. Select an employee, team, or initiative to see its details.</p></div><div className="org-chart-view" role="group" aria-label="Chart view">{["people", "teams"].map(option => <button key={option} type="button" aria-pressed={view === option} onClick={() => setView(option)}>{option === "people" ? "People" : "Teams"}</button>)}</div></div>
    <nav className="org-chart-navigation" aria-label="Organization directories">
      <button type="button" onClick={() => onCollection("owned-projects")}><Folder size={18} /><span>Projects<small>{plural(config.projectRoots?.length || 0, "project")}</small></span><ChevronRight size={16} /></button>
      <button type="button" onClick={() => onCollection("teams")}><Users size={18} /><span>Teams<small>{plural(config.teams.length, "team")}</small></span><ChevronRight size={16} /></button>
      <button type="button" onClick={() => onCollection("projects")}><Folder size={18} /><span>Initiatives<small>{plural(config.projects.length, "initiative")}</small></span><ChevronRight size={16} /></button>
    </nav>
    <div className="org-chart-canvas" role="region" aria-label="Reporting hierarchy" tabIndex={0}
      onPointerDown={startPan} onPointerMove={pan} onPointerUp={endPan} onPointerCancel={endPan} onLostPointerCapture={endPan}
      onDragStart={event => event.preventDefault()}
      onClickCapture={event => { if (suppressClick.current && event.detail !== 0) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; } }}>
      <div className="org-chart-root org-chart-node"><div className="org-chart-person"><Building2 size={22} /><span><strong>{config.name}</strong><small>Organization owner · You and Rem</small></span></div>{links(null)}</div>
      {view === "people" ? <ul className="org-chart-branches" aria-label="Reports to organization owner">{reportingHierarchy(config.employees).map(branch)}</ul> : <div className="org-chart-groups">{groups.map(({ team, employees }) => <section className="org-chart-team" key={team ? `team:${team.id}` : "unassigned"} aria-label={team ? `Team: ${team.name || team.id}` : "No team"}>
        <h4>{team ? <button type="button" aria-label={`Team: ${team.name || team.id}`} onClick={() => onCollection("teams", team.id)}><Users size={17} /><span>{team.name || team.id}</span><ChevronRight size={14} /></button> : "No team"}<small>{plural(employees.length, "employee")}</small></h4>
        {employees.length ? <ul className="org-chart-branches" aria-label={team ? `${team.name || team.id} reporting lines` : "Reports to organization owner"}>{reportingHierarchy(employees).map(branch)}</ul> : <p className="org-note">Choose a team manager to include their reporting chain.</p>}
      </section>)}</div>}
      {!config.employees.length && <p className="org-note">Add employees in the Employees tab, then choose who each reports to.</p>}
    </div>
  </section>;
}
