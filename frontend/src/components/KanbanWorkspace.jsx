import { useEffect, useState } from "react";
import { Building2, Check, ExternalLink, FolderX, PauseCircle, Play, Plus, Search, Sparkles, X } from "lucide-react";
import { COLUMNS, PRIORITIES, canDropOn, columnTickets, filterTickets, findRequestedTicket, modeLabel, onKanbanTicket, ownerFilters, participantOf, pausedLabel, saveParticipant, scopeNote, selfWorkLine, sendTicket, takeKanbanTicket, useKanban } from "../lib/remKanban.js";
import { openRemThread } from "../lib/remHomeStore.js";
import KanbanPeople, { PersonAvatar } from "./KanbanPeople.jsx";
import TicketTimeline from "./TicketTimeline.jsx";
import "./OrganizationHome.css";
import "./Kanban.css";

// The global board shows every ticket; each one still belongs to its organization or to Rem.
export default function KanbanWorkspace() {
  const kanban = useKanban();
  const { board, selfWork, error, setError, run } = kanban;
  const [owner, setOwner] = useState("all");
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState(false);
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [over, setOver] = useState("");
  const [view, setView] = useState("board");
  const [person, setPerson] = useState("");
  // A ticket pill in Rem's chat opened the board on one ticket.
  const [requested, setRequested] = useState(takeKanbanTicket);
  useEffect(() => onKanbanTicket(() => setRequested(takeKanbanTicket())), []);
  useEffect(() => {
    if (!requested || !board) return;
    const { ticket, missing } = findRequestedTicket(board, requested);
    if (ticket) {
      setView("board");
      if (!["backlog", "todo", "in_progress", "in_review", "blocked"].includes(ticket.status)) setClosed(true);
      setSelected(ticket.ticket_id);
    } else setError(missing);
    setRequested(null);
  }, [requested, board, setError]);
  const participants = board?.participants || [];
  const focused = participants.find(item => item.participant_id === person) || null;
  const tickets = filterTickets(board?.tickets, { owner, query, closed, participant: focused });
  const grouped = columnTickets(tickets);
  const current = board?.tickets.find(ticket => ticket.ticket_id === selected) || null;
  const act = change => run(() => sendTicket(change));
  const drop = column => event => {
    event.preventDefault();
    setOver("");
    const ticket = board?.tickets.find(item => item.ticket_id === event.dataTransfer.getData("text/plain"));
    if (!canDropOn(ticket, column)) return;
    act({ action: "move", ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined, expected_revision: ticket.revision, column });
  };
  return <div className="oh kb" data-testid="kanban-board">
    <div className="oh-main">
      <header className="oh-top">
        <h1>Kanban</h1>
        {selfWork ? <span className={`oh-pill ${selfWork.state.mode === "working" ? "st-working" : selfWork.state.mode === "off" ? "st-idle" : "st-paused"}`}><i className={`oh-dot ${selfWork.state.mode === "working" ? "pulse" : ""}`} />Rem: {modeLabel(selfWork.state.mode)}</span> : null}
        {selfWork ? <span className="oh-muted kb-line">{selfWorkLine(selfWork)}</span> : null}
        <span className="grow" />
        <div className="oh-seg" role="group" aria-label="Kanban view">
          <button type="button" aria-pressed={view === "board"} onClick={() => setView("board")}>Board</button>
          <button type="button" aria-pressed={view === "people"} onClick={() => setView("people")}>People</button>
        </div>
        <button className="oh-btn primary" type="button" onClick={() => setCreating(true)}><Plus size={14} />New ticket</button>
      </header>
      {error ? <p role="alert" className="kb-error"><span>{error}</span><button type="button" className="oh-btn small ghost" onClick={() => setError("")}>Dismiss</button></p> : null}
      {board && view === "people" ? <KanbanPeople board={board} onSave={(item, projects) => run(() => saveParticipant(item, projects))} onShow={id => { setPerson(id); setView("board"); }} /> : null}
      {!board ? <p className="oh-loading" role="status">Loading the board…</p> : view === "people" ? null : <div className="oh-view kb-view">
        <div className="oh-toolbar">
          <label className="oh-sr" htmlFor="kb-owner">Show tickets from</label>
          <select id="kb-owner" value={owner} onChange={event => setOwner(event.target.value)}>{ownerFilters(board).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <label className="kb-search"><Search size={13} aria-hidden="true" /><span className="oh-sr">Search tickets</span><input value={query} placeholder="Search tickets" onChange={event => setQuery(event.target.value)} /></label>
          <div className="kb-stack" role="group" aria-label="Show one person's tickets">
            {participants.slice(0, 10).map(item => <button key={item.participant_id} type="button" aria-pressed={person === item.participant_id} title={item.organization_name ? `${item.name}, ${item.organization_name}` : item.name} onClick={() => setPerson(current => current === item.participant_id ? "" : item.participant_id)}><PersonAvatar person={item} /></button>)}
            {participants.length > 10 ? <button className="kb-more" type="button" onClick={() => setView("people")}>+{participants.length - 10}</button> : null}
          </div>
          <label className="kb-check"><input type="checkbox" checked={closed} onChange={event => setClosed(event.target.checked)} />Closed</label>
          <span className="grow" />
          <span className="oh-muted">{tickets.length} {tickets.length === 1 ? "ticket" : "tickets"}{board.earlier ? `, ${board.earlier} older not shown` : ""}</span>
        </div>
        <div className="oh-board-wrap kb-wrap"><div className="oh-board kb-board">
          {COLUMNS.map(([column, label]) => <section className="col" key={column} aria-label={label}>
            <header><span>{label}</span><span className="n">{grouped[column].length}</span></header>
            <div className={`col-body ${over === column ? "over" : ""}`} data-col={column}
              onDragOver={event => { event.preventDefault(); setOver(column); }}
              onDragLeave={() => setOver("")} onDrop={drop(column)}>
              {grouped[column].map(ticket => <Card key={ticket.ticket_id} ticket={ticket} person={participantOf(ticket, participants)} onOpen={() => setSelected(ticket.ticket_id)} />)}
            </div>
          </section>)}
        </div></div>
      </div>}
    </div>
    {current ? <TicketSheet ticket={current} board={board} act={act} onClose={() => setSelected("")} /> : null}
    {creating && board ? <NewTicket board={board} act={act} onClose={() => setCreating(false)} /> : null}
  </div>;
}

function Card({ ticket, person, onOpen }) {
  const note = scopeNote(ticket);
  return <article className={`oh-card ${ticket.running ? "is-running" : ""} ${ticket.status === "blocked" && !ticket.paused ? "flagged" : ""} ${ticket.paused ? "is-paused" : ""}`} draggable={!ticket.running && !ticket.proposed}
    onDragStart={event => { event.dataTransfer.setData("text/plain", ticket.ticket_id); event.dataTransfer.effectAllowed = "move"; }}
    onClick={onOpen} onKeyDown={event => { if (event.key === "Enter") onOpen(); }} tabIndex={0} role="button" aria-label={`${ticket.key} ${ticket.title}`}>
    <div className="row1"><span className="oh-key">{ticket.key}</span><span>{ticket.project || (ticket.priority === "critical" || ticket.priority === "high" ? ticket.priority : "")}</span></div>
    <div className="title">{ticket.title}</div>
    {ticket.status === "blocked" && ticket.block_reason ? <div className="flag"><span>{ticket.block_reason}</span></div> : null}
    {note ? <div className="kb-scope" title={note}><FolderX size={12} aria-hidden="true" /><span>{note}</span></div> : null}
    {ticket.skip_reason ? <div className="kb-scope" title={ticket.skip_reason}><FolderX size={12} aria-hidden="true" /><span>Rem left this out: {ticket.skip_reason}</span></div> : null}
    {ticket.running ? <div className="livebar" aria-hidden="true"><i /></div> : null}
    <div className="row3">
      {person ? <PersonAvatar person={person} small /> : <span className="oh-av" style={{ background: "var(--faint)" }} title="Unassigned">?</span>}
      {ticket.scope === "rem" ? <span>Rem</span> : <span><Building2 size={12} />{ticket.assignee || ticket.organization_name}</span>}
      <span className="grow" />
      {ticket.proposed ? <span className="kb-proposed"><Sparkles size={11} />Your approval</span> : null}
      {ticket.paused ? <span className="kb-paused"><PauseCircle size={11} aria-hidden="true" />{pausedLabel(ticket.paused)}</span> : null}
    </div>
  </article>;
}

function TicketSheet({ ticket, board, act, onClose }) {
  const projects = board.participants.find(item => item.kind === "rem")?.assignable || [];
  const note = scopeNote(ticket);
  const [reason, setReason] = useState("");
  const [destination, setDestination] = useState("");
  const [worker, setWorker] = useState("");
  const organization = board.organizations.find(org => org.organization_id === (destination || ticket.organization_id));
  const base = { ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined, expected_revision: ticket.revision };
  useEffect(() => {
    const close = event => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return <aside className="kb-sheet" aria-label={`Ticket ${ticket.key}`}>
    <header className="kb-sheet-head"><span className="oh-key">{ticket.key}</span><span className="grow" /><button className="oh-btn icon ghost" type="button" aria-label="Close" onClick={onClose}><X size={15} /></button></header>
    <h2>{ticket.title}</h2>
    <p className="oh-sheet-sub"><span className={`oh-pill st-${ticket.status === "in_progress" ? "running" : ticket.status}`}>{COLUMNS.find(([key]) => key === ticket.status)?.[1] || ticket.status}</span><span>{ticket.scope === "rem" ? "Rem" : ticket.organization_name}</span>{ticket.assignee && ticket.scope !== "rem" ? <span>{ticket.assignee}</span> : null}{ticket.project ? <span>{ticket.project}</span> : null}</p>
    {note ? <p className="kb-scope" role="status"><FolderX size={13} aria-hidden="true" /><span>{note}</span></p> : null}
    {ticket.scope === "rem" && !ticket.running && !ticket.paused && ["backlog", "todo", "blocked"].includes(ticket.status) ? <label className="kb-field"><span>Project</span>
      <select className="kb-input" value={ticket.project_dir || ""} onChange={event => act({ ...base, action: "edit", project_dir: event.target.value || null })}>
        <option value="">No project</option>
        {[...new Set([...projects, ...(ticket.project_dir ? [ticket.project_dir] : [])])].map(root => <option key={root} value={root}>{projectLabel(board, root)}</option>)}
      </select></label> : null}
    {ticket.description ? <p className="kb-description">{ticket.description}</p> : null}
    {ticket.proposal_reason ? <div className="kb-section"><h3>Why Rem suggests this</h3><p>{ticket.proposal_reason}</p></div> : null}
    {ticket.skip_reason ? <div className="kb-section"><h3>Why Rem left this out</h3><p>{ticket.skip_reason}</p></div> : null}
    {ticket.summary ? <div className="kb-section"><h3>Result</h3><p>{ticket.summary}</p></div> : null}
    {ticket.block_reason ? <div className="kb-section"><h3>{ticket.paused ? pausedLabel(ticket.paused) : "Blocked"}</h3><p>{ticket.block_reason}</p></div> : null}
    {ticket.thread_id ? <button className="oh-btn" type="button" onClick={() => openRemThread(ticket.thread_id)}><ExternalLink size={13} />Open its thread</button> : null}
    {ticket.proposed ? <div className="kb-actions">
      <button className="oh-btn primary" type="button" onClick={() => act({ ...base, action: "approve" })}><Check size={14} />Approve for To do</button>
      <label className="oh-sr" htmlFor="kb-decline">Reason (optional)</label>
      <input id="kb-decline" className="kb-input" placeholder="Reason (optional)" value={reason} onChange={event => setReason(event.target.value)} />
      <button className="oh-btn danger" type="button" onClick={() => { act({ ...base, action: "decline", reason: reason.trim() || undefined }); onClose(); }}>Decline</button>
    </div> : null}
    {ticket.scope === "rem" && !ticket.proposed && !ticket.running && !ticket.paused && ["backlog", "todo", "blocked"].includes(ticket.status) && board.organizations.length ? <div className="kb-section">
      <h3>Hand to an organization</h3>
      <div className="kb-actions">
        <label className="oh-sr" htmlFor="kb-org">Organization</label>
        <select id="kb-org" className="kb-input" value={destination} onChange={event => { setDestination(event.target.value); setWorker(""); }}><option value="">Choose…</option>{board.organizations.map(org => <option key={org.organization_id} value={org.organization_id}>{org.name}</option>)}</select>
        {organization && destination ? <><label className="oh-sr" htmlFor="kb-worker">Worker</label><select id="kb-worker" className="kb-input" value={worker} onChange={event => setWorker(event.target.value)}><option value="">Unassigned</option>{organization.employees.map(employee => <option key={employee.employee_id} value={employee.employee_id}>{employee.name}</option>)}</select></> : null}
        <button className="oh-btn" type="button" disabled={!destination} onClick={() => { act({ ...base, action: "assign", to_organization_id: destination, assignee_id: worker }); onClose(); }}>Hand over</button>
      </div>
    </div> : null}
    {ticket.paused ? <div className="kb-section kb-move"><h3>Paused task</h3><div className="kb-actions">
      <button className="oh-btn primary" type="button" onClick={() => act({ ...base, action: "move", column: "in_progress" })}><Play size={14} />Resume</button>
    </div><p className="kb-hint">Resumes the same task in its thread. To end it instead, stop it from its task in Rem.</p></div> : null}
    {!ticket.proposed && !ticket.running && !ticket.paused ? <div className="kb-section kb-move"><h3>Move</h3><div className="kb-actions">
      {ticket.status === "cancelled" ? <button className="oh-btn" type="button" onClick={() => act({ ...base, action: "move", column: "backlog" })}>Restore to Backlog</button> : <>
        {canDropOn(ticket, "todo") ? <button className="oh-btn" type="button" onClick={() => act({ ...base, action: "move", column: "todo" })}>To do</button> : null}
        {canDropOn(ticket, "in_progress") ? <button className="oh-btn" type="button" onClick={() => act({ ...base, action: "move", column: "in_progress" })}>Start now</button> : null}
        {canDropOn(ticket, "backlog") ? <button className="oh-btn" type="button" onClick={() => act({ ...base, action: "move", column: "backlog" })}>Backlog</button> : null}
        {ticket.scope === "rem" && canDropOn(ticket, "done") ? <button className="oh-btn" type="button" onClick={() => act({ ...base, action: "move", column: "done" })}>Mark done</button> : null}
        {ticket.scope === "rem" && ticket.status !== "done" ? <button className="oh-btn ghost" type="button" onClick={() => { act({ ...base, action: "move", column: "cancelled" }); onClose(); }}>Cancel ticket</button> : null}
      </>}
    </div></div> : null}
    <TicketTimeline ticket={ticket} />
  </aside>;
}

const projectLabel = (board, root) => board.projects.find(project => project.root === root)?.name || root;

function NewTicket({ board, act, onClose }) {
  const rem = board.participants.find(item => item.kind === "rem");
  const openProjects = rem?.assignable || [];
  // Starts on one of Rem's assigned projects, so a new ticket can be picked up on its own.
  const [form, setForm] = useState({ title: "", description: "", priority: "medium", status: "todo", owner: "rem", worker: "", project: rem?.projects.find(root => openProjects.includes(root)) || "" });
  const [busy, setBusy] = useState(false);
  const change = values => setForm(current => ({ ...current, ...values }));
  const organization = form.owner === "rem" ? null : board.organizations.find(org => org.organization_id === form.owner);
  const submit = async event => {
    event.preventDefault();
    if (!form.title.trim() || busy) return;
    setBusy(true);
    const saved = await act({
      action: "create", title: form.title.trim(), description: form.description.trim(), priority: form.priority, status: form.status,
      ...(organization ? { scope: "organization", organization_id: organization.organization_id, assignee_id: form.worker || undefined } : { project_dir: form.project || undefined }),
    });
    setBusy(false);
    if (saved) onClose();
  };
  return <div className="kb-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="kb-dialog oh-form" onSubmit={submit} role="dialog" aria-modal="true" aria-label="New ticket">
      <h2>New ticket</h2>
      <label>Title<input autoFocus maxLength={500} value={form.title} onChange={event => change({ title: event.target.value })} /></label>
      <label>Description<textarea value={form.description} onChange={event => change({ description: event.target.value })} /></label>
      <label>For
        <select value={form.owner} onChange={event => change({ owner: event.target.value, worker: "" })}>
          <option value="rem">Rem</option>
          {board.organizations.map(org => <option key={org.organization_id} value={org.organization_id}>{org.name}</option>)}
        </select>
      </label>
      {!organization ? <label>Project<select value={form.project} onChange={event => change({ project: event.target.value })}><option value="">No project</option>{openProjects.map(root => <option key={root} value={root}>{projectLabel(board, root)}</option>)}</select></label> : null}
      {organization ? <label>Worker<select value={form.worker} onChange={event => change({ worker: event.target.value })}><option value="">Unassigned</option>{organization.employees.map(employee => <option key={employee.employee_id} value={employee.employee_id}>{employee.name}</option>)}</select></label> : null}
      <label>Priority<select value={form.priority} onChange={event => change({ priority: event.target.value })}>{PRIORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Column<select value={form.status} onChange={event => change({ status: event.target.value })}><option value="todo">To do</option><option value="backlog">Backlog</option></select></label>
      <div className="kb-actions"><button className="oh-btn primary" type="submit" disabled={busy || !form.title.trim()}>Add ticket</button><button className="oh-btn ghost" type="button" onClick={onClose}>Cancel</button></div>
    </form>
  </div>;
}
