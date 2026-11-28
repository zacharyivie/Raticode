import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Building2, Check, ChevronDown, ChevronUp, ExternalLink, FolderX, Gauge, Hourglass, Monitor, PauseCircle, Pencil, Play, Plus, Search, ShieldAlert, ShieldCheck, Sparkles, Target, TriangleAlert, X } from "lucide-react";
import { COLUMNS, PRIORITIES, STATUS_LABELS, canDropOn, canEditTicket, canEditWaits, canUndoMove, columnTickets, descriptionCut, doneWhenText, draftConflictNotice, draftConflicts, filterTickets, findRequestedTicket, guardrailTag, kindChoices, kindLabel, modeLabel, moveNotice, objectiveLine, onKanbanTicket, ownerFilters, participantOf, pausedLabel, rebaseDraft, saveParticipant, scopeNote, selfWorkLine, sendTicket, takeKanbanTicket, ticketDraft, ticketEdit, usageSentence, usageShort, useKanban, violatesLine, waitChoices, waitsChange, waitsLine } from "../lib/remKanban.js";
import { openRemThread } from "../lib/remHomeStore.js";
import { GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS, activeFilterCount, liveGlobalFilters, resetFilters, useBoardFilters } from "../lib/boardFilters.js";
import KanbanPeople, { PersonAvatar } from "./KanbanPeople.jsx";
import MarkdownContent from "./MarkdownContent.jsx";
import TicketTimeline from "./TicketTimeline.jsx";
import TicketPatchReview from "./TicketPatchReview.jsx";
import { Dialog } from "./Dialog.jsx";
import { useDiscardGuard } from "./ConfirmDialog.jsx";
import { NoticeStack, useNotices } from "./Notice.jsx";
import "./OrganizationHome.css";
import "./Kanban.css";

// The global board shows every ticket; each one still belongs to its organization or to Rem.
export default function KanbanWorkspace() {
  const kanban = useKanban();
  const { board, selfWork, error, setError, run } = kanban;
  // Who, what and how the board shows, kept on this computer across visits and restarts.
  const live = useCallback(value => liveGlobalFilters(value, board), [board]);
  const [filters, setFilters] = useBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, live);
  const { owner, query, closed, view, person } = filters;
  const setView = next => setFilters({ view: next });
  const setPerson = next => setFilters(current => ({ person: typeof next === "function" ? next(current.person) : next }));
  const filtersOn = activeFilterCount(filters, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS);
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [over, setOver] = useState("");
  // A ticket pill in Rem's chat opened the board on one ticket.
  const [requested, setRequested] = useState(takeKanbanTicket);
  useEffect(() => onKanbanTicket(() => setRequested(takeKanbanTicket())), []);
  useEffect(() => {
    if (!requested || !board) return;
    const { ticket, missing } = findRequestedTicket(board, requested);
    if (ticket) {
      setFilters(["backlog", "todo", "in_progress", "in_review", "blocked"].includes(ticket.status) ? { view: "board" } : { view: "board", closed: true });
      setSelected(ticket.ticket_id);
    } else setError(missing);
    setRequested(null);
  }, [requested, board, setError, setFilters]);
  const participants = board?.participants || [];
  const focused = participants.find(item => item.participant_id === person) || null;
  const tickets = filterTickets(board?.tickets, { owner, query, closed, participant: focused });
  const grouped = columnTickets(tickets);
  const current = board?.tickets.find(ticket => ticket.ticket_id === selected) || null;
  const act = change => run(() => sendTicket(change));
  // An open sheet with unsaved edits asks before another ticket replaces it.
  const sheetLeave = useRef(null);
  const openTicket = id => {
    if (id !== selected && sheetLeave.current) sheetLeave.current(() => setSelected(id));
    else setSelected(id);
  };
  // Closing a sheet gives keyboard focus back to the card that opened it, or to the board's
  // first card when that ticket has left the board.
  const cards = useRef(new Map());
  const returnTo = useRef("");
  const returned = useRef(null);
  const firstCard = () => COLUMNS.map(([column]) => grouped[column][0]).find(Boolean);
  const closeSheet = useCallback(() => {
    setSelected(current => {
      returnTo.current = current;
      return "";
    });
  }, []);
  useEffect(() => {
    if (selected) {
      returned.current = null;
      return;
    }
    const first = firstCard();
    const pending = returnTo.current;
    if (pending) {
      returnTo.current = "";
      const card = cards.current.get(pending) || (first && cards.current.get(first.ticket_id));
      card?.focus();
      returned.current = card ? { id: pending, card } : null;
      return;
    }
    // A sheet action that closed it (Cancel ticket, Hand over) can take the card off the
    // board afterwards: focus moves on to the first card instead of being lost.
    const back = returned.current;
    if (!back || cards.current.get(back.id) === back.card) return;
    returned.current = null;
    const active = document.activeElement;
    if (active && active !== back.card && active !== document.body) return;
    if (first) cards.current.get(first.ticket_id)?.focus();
  });
  const { notices, notify, dismiss } = useNotices();
  // Sends one change and returns the ticket as the new board has it, or null when it failed.
  const change = async (ticket, values) => {
    let response = null;
    const done = await run(async () => (response = await sendTicket({ ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined, expected_revision: ticket.revision, ...values })));
    return done ? response?.board?.tickets.find(item => item.ticket_id === ticket.ticket_id) || null : null;
  };
  // A move says what it did. Undo moves the ticket back with its new revision; a move that
  // starts work has none.
  const move = async (ticket, column) => {
    const moved = await change(ticket, { action: "move", column });
    if (!moved) return;
    const from = STATUS_LABELS[ticket.status];
    notify(moveNotice(ticket, column), canUndoMove(ticket, column) ? { label: "Undo", run: async () => {
      if (await change(moved, { action: "move", column: ticket.status })) notify(`${ticket.key} moved back to ${from}.`);
    } } : null);
  };
  const decline = async (ticket, reason) => {
    if (await change(ticket, { action: "decline", reason })) notify(`${ticket.key} declined.`);
  };
  const drop = column => event => {
    event.preventDefault();
    setOver("");
    const ticket = board?.tickets.find(item => item.ticket_id === event.dataTransfer.getData("text/plain"));
    if (!canDropOn(ticket, column)) return;
    void move(ticket, column);
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
          <select id="kb-owner" className={owner !== GLOBAL_BOARD_FILTERS.owner ? "oh-filter-on" : ""} value={owner} onChange={event => setFilters({ owner: event.target.value })}>{ownerFilters(board).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <label className={`kb-search ${query ? "oh-filter-on" : ""}`}><Search size={13} aria-hidden="true" /><span className="oh-sr">Search tickets</span><input value={query} placeholder="Search tickets" onChange={event => setFilters({ query: event.target.value })} /></label>
          <div className="kb-stack" role="group" aria-label="Show one person's tickets">
            {(focused && !participants.slice(0, 10).includes(focused) ? [...participants.slice(0, 9), focused] : participants.slice(0, 10)).map(item => <button key={item.participant_id} type="button" aria-pressed={person === item.participant_id} title={item.organization_name ? `${item.name}, ${item.organization_name}` : item.name} onClick={() => setPerson(current => current === item.participant_id ? "" : item.participant_id)}><PersonAvatar person={item} /></button>)}
            {participants.length > 10 ? <button className="kb-more" type="button" onClick={() => setView("people")}>+{participants.length - 10}</button> : null}
          </div>
          <label className={`kb-check ${closed ? "oh-filter-on" : ""}`}><input type="checkbox" checked={closed} onChange={event => setFilters({ closed: event.target.checked })} />Closed</label>
          {filtersOn ? <button className="oh-btn small ghost kb-reset" type="button" data-testid="kanban-reset-filters" aria-label={`Clear filters, ${filtersOn} on`} onClick={() => setFilters(current => resetFilters(current, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS))}><X size={13} aria-hidden="true" />Clear filters ({filtersOn})</button> : null}
          <span className="grow" />
          <span className="oh-muted">{tickets.length} {tickets.length === 1 ? "ticket" : "tickets"}{board.earlier ? `, ${board.earlier} older not shown` : ""}</span>
        </div>
        <div className="oh-board-wrap kb-wrap"><div className="oh-board kb-board">
          {COLUMNS.map(([column, label]) => <section className="col" key={column} aria-label={label}>
            <header><span>{label}</span><span className="n">{grouped[column].length}</span></header>
            <div className={`col-body ${over === column ? "over" : ""}`} data-col={column}
              onDragOver={event => { event.preventDefault(); setOver(column); }}
              onDragLeave={() => setOver("")} onDrop={drop(column)}>
              {grouped[column].map(ticket => <Card key={ticket.ticket_id} ticket={ticket} person={participantOf(ticket, participants)} open={ticket.ticket_id === selected} cardRef={card => { if (card) cards.current.set(ticket.ticket_id, card); else cards.current.delete(ticket.ticket_id); }} onOpen={() => openTicket(ticket.ticket_id)} />)}
            </div>
          </section>)}
        </div></div>
      </div>}
    </div>
    {current ? <TicketSheet key={current.ticket_id} ticket={current} board={board} act={act} onMove={column => move(current, column)} onDecline={reason => decline(current, reason)} apply={response => run(async () => response)} onClose={closeSheet} leaveRef={sheetLeave} /> : null}
    {creating && board ? <NewTicket board={board} act={act} onClose={() => setCreating(false)} /> : null}
    <NoticeStack notices={notices} onDismiss={dismiss} />
  </div>;
}

function Card({ ticket, person, open, cardRef, onOpen }) {
  const note = scopeNote(ticket);
  const waits = waitsLine(ticket);
  const objective = objectiveLine(ticket);
  const violates = violatesLine(ticket);
  const guardrail = guardrailTag(ticket);
  // The card's name is its key and title; its status lines are read out as its description,
  // so a screen reader hears what the card shows.
  const id = useId();
  const lines = { flag: ticket.status === "blocked" && ticket.block_reason, scope: note, waits: ticket.wait_notice || waits, skip: ticket.skip_reason, objective,
    proposed: ticket.proposed, auto: ticket.auto_approved, from: ticket.proposed_on, violates, guardrail, paused: ticket.paused };
  const line = name => `${id}-${name}`;
  const describedBy = Object.keys(lines).filter(name => lines[name]).map(line).join(" ");
  return <article ref={cardRef} className={`oh-card ${ticket.running ? "is-running" : ""} ${ticket.status === "blocked" && !ticket.paused ? "flagged" : ""} ${ticket.paused ? "is-paused" : ""}`} draggable={!ticket.running && !ticket.proposed}
    onDragStart={event => { event.dataTransfer.setData("text/plain", ticket.ticket_id); event.dataTransfer.effectAllowed = "move"; }}
    onClick={onOpen} onKeyDown={event => { if (event.key !== "Enter" && event.key !== " ") return; event.preventDefault(); onOpen(); }}
    tabIndex={0} role="button" aria-label={`${ticket.key} ${ticket.title}`} aria-describedby={describedBy || undefined} aria-current={open ? "true" : undefined}>
    <div className="row1"><span className="oh-key">{ticket.key}</span><span>{ticket.project || (ticket.priority === "critical" || ticket.priority === "high" ? ticket.priority : "")}</span></div>
    <div className="title">{ticket.title}</div>
    {lines.flag ? <div className="flag" id={line("flag")}><span>{ticket.block_reason}</span></div> : null}
    {note ? <div className="kb-scope" title={note} id={line("scope")}><FolderX size={12} aria-hidden="true" /><span>{note}</span></div> : null}
    {ticket.wait_notice ? <div className="kb-wait-notice" title={ticket.wait_notice} id={line("waits")} data-testid="ticket-wait-notice"><TriangleAlert size={12} aria-hidden="true" /><span>{ticket.wait_notice}</span></div>
      : waits ? <div className="kb-waits" title={ticket.waits_for.filter(item => item.status !== "done").map(item => `${item.key} ${item.title}`).join("\n")} id={line("waits")} data-testid="ticket-waits"><Hourglass size={12} aria-hidden="true" /><span>{waits}</span></div> : null}
    {ticket.skip_reason ? <div className="kb-scope" title={ticket.skip_reason} id={line("skip")}><FolderX size={12} aria-hidden="true" /><span>Rem left this out: {ticket.skip_reason}</span></div> : null}
    {objective ? <div className={`kb-objective ${objective.off ? "is-off" : ""}`} title={objective.detail} id={line("objective")} data-testid="ticket-objective"><Target size={11} aria-hidden="true" /><span>{objective.short}</span></div> : null}
    {ticket.running ? <div className="livebar" aria-hidden="true"><i /></div> : null}
    <div className="row3">
      {person ? <PersonAvatar person={person} small /> : <span className="oh-av" style={{ background: "var(--faint)" }} title="Unassigned">?</span>}
      {ticket.scope === "rem" ? <span>Rem</span> : <span><Building2 size={12} />{ticket.assignee || ticket.organization_name}</span>}
      <span className="grow" />
      {usageShort(ticket.usage) ? <span className="kb-usage" title={usageSentence(ticket.usage)} data-testid="ticket-usage"><Gauge size={11} aria-hidden="true" />{usageShort(ticket.usage)}</span> : null}
      {ticket.proposed ? <span className="kb-proposed" id={line("proposed")}><Sparkles size={11} aria-hidden="true" />Your approval</span> : null}
      {ticket.auto_approved ? <span className="kb-auto" id={line("auto")} title="Self-work approved this on its own; you haven't reviewed it"><Sparkles size={11} aria-hidden="true" />Auto-approved</span> : null}
      {ticket.proposed_on ? <span className="kb-auto" id={line("from")} title={`A self-work pass that ran on ${ticket.proposed_on} proposed this`} data-testid="ticket-proposed-on"><Monitor size={11} aria-hidden="true" />From {ticket.proposed_on}</span> : null}
      {violates ? <span className="kb-violates" id={line("violates")} title={ticket.violation_reason || "It contradicts a decision in force, so self-work won't start it."} data-testid="ticket-violates"><ShieldAlert size={11} aria-hidden="true" />{violates}</span> : null}
      {guardrail ? <span className="kb-guardrail" id={line("guardrail")} title="A guardrail retro proposed this to stop a failure that came back in two or more runs" data-testid="ticket-guardrail"><ShieldCheck size={11} aria-hidden="true" />{guardrail}</span> : null}
      {ticket.paused ? <span className="kb-paused" id={line("paused")}><PauseCircle size={11} aria-hidden="true" />{pausedLabel(ticket.paused)}</span> : null}
    </div>
  </article>;
}

// A finished ticket's work is on a branch: an organization ticket's own, or the one its
// Rem run recorded. Its sheet shows the patch while nothing is running on it.
const hasPatch = ticket => !ticket.running && !ticket.proposed && ticket.status !== "cancelled"
  && (ticket.scope === "organization" ? Boolean(ticket.branch) : Boolean(ticket.run_id) && ["done", "blocked"].includes(ticket.status));
// Accept and change requests: Rem's finished or blocked tickets, an organization's in review or done.
const canDecide = ticket => (ticket.scope === "organization" ? ["in_review", "done"] : ["done", "blocked"]).includes(ticket.status) && !ticket.paused;

const priorityLabel = priority => PRIORITIES.find(([value]) => value === priority)?.[1] || (priority ? priority[0].toUpperCase() + priority.slice(1) : "Medium");

function TicketSheet({ ticket, board, act, onMove, onDecline, apply, onClose, leaveRef }) {
  const [reviewing, setReviewing] = useState(false);
  const projects = board.participants.find(item => item.kind === "rem")?.assignable || [];
  const note = scopeNote(ticket);
  const objective = objectiveLine(ticket);
  const [reason, setReason] = useState("");
  const [destination, setDestination] = useState("");
  const [worker, setWorker] = useState("");
  const organization = board.organizations.find(org => org.organization_id === (destination || ticket.organization_id));
  const base = { ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined, expected_revision: ticket.revision };
  // The edit form: the fields it started from, at which revision, and what's typed now.
  const [edit, setEdit] = useState(null);
  const changed = Boolean(edit && ticketEdit(ticket, edit.start, edit.draft, edit.revision));
  const changedRef = useRef(changed);
  changedRef.current = changed;
  const after = useRef(null);
  const guard = useDiscardGuard({ changed: () => changedRef.current, onClose: () => after.current?.(), message: "Discard your changes to this ticket? They aren't saved." });
  // Leaving the form, closing the sheet or opening another ticket asks first while the form
  // has unsaved changes.
  const leave = action => {
    if (!changedRef.current) { action(); return; }
    after.current = action;
    void guard.requestClose();
  };
  const leaveNow = useRef(leave);
  leaveNow.current = leave;
  const editingRef = useRef(false);
  editingRef.current = Boolean(edit);
  useEffect(() => {
    if (!leaveRef) return undefined;
    leaveRef.current = action => leaveNow.current(action);
    return () => { leaveRef.current = null; };
  }, [leaveRef]);
  // A newer copy of the ticket, from a poll or another change, carries the fields the user
  // hasn't touched along, so Save doesn't undo them. When it changed a field the user
  // changed too, the form stays on its revision, so the desktop refuses the save.
  useEffect(() => {
    setEdit(current => {
      if (!current || current.busy || current.revision === ticket.revision) return current;
      const newer = ticketDraft(ticket);
      const conflicts = draftConflicts(current.start, current.draft, newer);
      if (conflicts.length) return { ...current, error: draftConflictNotice(conflicts) };
      return { ...current, start: newer, revision: ticket.revision, draft: rebaseDraft(current.start, current.draft, newer) };
    });
  }, [ticket]);
  const startEdit = () => setEdit({ start: ticketDraft(ticket), revision: ticket.revision, draft: ticketDraft(ticket), busy: false, error: "" });
  const draftChange = values => setEdit(current => current && { ...current, draft: { ...current.draft, ...values } });
  const save = async event => {
    event.preventDefault();
    if (!edit || edit.busy || !edit.draft.title.trim()) return;
    const change = ticketEdit(ticket, edit.start, edit.draft, edit.revision);
    if (!change) { setEdit(null); return; }
    setEdit(current => ({ ...current, busy: true, error: "" }));
    try {
      const response = await sendTicket(change);
      await apply(response);
      setEdit(null);
    } catch (failure) {
      // A refusal (a newer revision, or a run that started) keeps what was typed.
      setEdit(current => current && { ...current, busy: false, error: failure.message });
    }
  };
  // Escape closes the sheet, but not while a Decline reason is typed in it. In the edit form
  // it closes the form, asking first when something changed.
  const reasonRef = useRef(reason);
  reasonRef.current = reason;
  // Opening a ticket moves keyboard focus into its sheet, at its title; the edit form starts
  // at its Title field and gives focus back to Edit when it closes.
  const heading = useRef(null);
  const editButton = useRef(null);
  const titleField = useRef(null);
  const wasEditing = useRef(false);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    if (edit && !wasEditing.current) titleField.current?.focus();
    if (!edit && wasEditing.current) (editButton.current || heading.current)?.focus();
    wasEditing.current = Boolean(edit);
  }, [edit]);
  useEffect(() => {
    const close = event => {
      if (event.key !== "Escape") return;
      if (editingRef.current) leaveNow.current(() => setEdit(null));
      else if (!reasonRef.current.trim()) onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const cut = edit ? descriptionCut(edit.start) : false;
  return <aside className={`kb-sheet ${reviewing ? "is-reviewing" : ""}`} aria-label={`Ticket ${ticket.key}`}>
    <header className="kb-sheet-head"><span className="oh-key">{ticket.key}</span><span className="grow" />
      {canEditTicket(ticket) && !edit ? <button ref={editButton} className="oh-btn small" type="button" onClick={startEdit}><Pencil size={13} aria-hidden="true" />Edit</button> : null}
      <button className="oh-btn icon ghost" type="button" aria-label="Close" onClick={() => leave(onClose)}><X size={15} /></button></header>
    {edit ? <form className="oh-form kb-edit" aria-label={`Edit ${ticket.key}`} onSubmit={save}>
      <label>Title<input ref={titleField} maxLength={500} value={edit.draft.title} onChange={event => draftChange({ title: event.target.value })} /></label>
      <label>Description<textarea rows={6} readOnly={cut} value={edit.draft.description} onChange={event => draftChange({ description: event.target.value })} />
        {cut ? <small>Too long to edit here, so Save leaves it as it is.</small> : null}</label>
      <div className="kb-edit-row">
        <label>Priority<select value={edit.draft.priority} onChange={event => draftChange({ priority: event.target.value })}>{PRIORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Kind<select value={edit.draft.type} onChange={event => draftChange({ type: event.target.value })}>{kindChoices(edit.start.type).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      </div>
      <label>Done when<textarea rows={3} maxLength={1000} placeholder="One check per line" value={edit.draft.done_when} onChange={event => draftChange({ done_when: event.target.value })} />
        <small>One check per line. Its review checks each one.</small></label>
      {edit.error ? <p className="kb-edit-error" role="alert">{edit.error}</p> : null}
      <div className="kb-actions">
        <button className="oh-btn primary" type="submit" disabled={edit.busy || !changed || !edit.draft.title.trim()}>{edit.busy ? "Saving…" : "Save"}</button>
        <button className="oh-btn ghost" type="button" onClick={() => leave(() => setEdit(null))}>Cancel</button>
      </div>
    </form> : <h2 ref={heading} tabIndex={-1}>{ticket.title}</h2>}
    <p className="oh-sheet-sub"><span className={`oh-pill st-${ticket.status === "in_progress" ? "running" : ticket.status}`}>{COLUMNS.find(([key]) => key === ticket.status)?.[1] || ticket.status}</span><span>{ticket.scope === "rem" ? "Rem" : ticket.organization_name}</span>{ticket.assignee && ticket.scope !== "rem" ? <span>{ticket.assignee}</span> : null}{ticket.project ? <span>{ticket.project}</span> : null}</p>
    {!edit ? <dl className="kb-facts"><div><dt>Priority</dt><dd>{priorityLabel(ticket.priority)}</dd></div>{ticket.scope === "rem" ? <div><dt>Kind</dt><dd>{kindLabel(ticket.type)}</dd></div> : null}</dl> : null}
    {note ? <p className="kb-scope" role="status"><FolderX size={13} aria-hidden="true" /><span>{note}</span></p> : null}
    {canEditTicket(ticket) ? <label className="kb-field"><span>Project</span>
      <select className="kb-input" value={ticket.project_dir || ""} onChange={event => act({ ...base, action: "edit", project_dir: event.target.value || null })}>
        <option value="">No project</option>
        {[...new Set([...projects, ...(ticket.project_dir ? [ticket.project_dir] : [])])].map(root => <option key={root} value={root}>{projectLabel(board, root)}</option>)}
      </select></label> : null}
    {ticket.description && !edit ? <TicketDescription text={ticket.description} /> : null}
    {!edit ? <DoneWhen ticket={ticket} /> : null}
    <WaitsFor ticket={ticket} board={board} act={act} />
    {ticket.auto_approved ? <p className="kb-auto-note" role="note" data-testid="ticket-auto-approved"><Sparkles size={13} aria-hidden="true" /><span>Approved by self-work, not by you, because auto-approval is on in Settings &gt; Rem. Edit, move or cancel it like any ticket.</span></p> : null}
    {ticket.proposed_on ? <p className="kb-auto-note" role="note" data-testid="ticket-proposed-on-note"><Monitor size={13} aria-hidden="true" /><span>Proposed by a self-work pass that ran on {ticket.proposed_on}.{ticket.proposed ? " What another computer proposes always waits for your approval, even with auto-approval on." : ""}</span></p> : null}
    {ticket.proposal_reason ? <div className="kb-section"><h3>Why Rem suggests this</h3><TicketMarkdown value={ticket.proposal_reason} /></div> : null}
    {ticket.skip_reason ? <div className="kb-section"><h3>Why Rem left this out</h3><p>{ticket.skip_reason}</p></div> : null}
    {objective ? <div className="kb-section" data-testid="ticket-objective-detail"><h3>Objective</h3><p>{objective.detail}{objective.off ? " Move it to In progress to start it anyway." : ""}</p></div> : null}
    {ticket.summary ? <div className="kb-section"><h3>Result</h3><TicketMarkdown value={ticket.summary} /></div> : null}
    {violatesLine(ticket) ? <div className="kb-section kb-violates-note" role="note" data-testid="ticket-violates-detail"><h3><ShieldAlert size={13} aria-hidden="true" />Violates decisions</h3><p>{ticket.violation_reason || `It contradicts ${ticket.violates_decisions.join(", ")}.`}</p><p className="kb-hint">Self-work won't start it, and Rem can't make an exception for itself. Edit the ticket, or change the decision in Rem's Decisions list, and Rem checks it again.</p></div>
      : ticket.block_reason ? <div className="kb-section"><h3>{ticket.paused ? pausedLabel(ticket.paused) : "Blocked"}</h3><p>{ticket.block_reason}</p></div> : null}
    {ticket.usage ? <div className="kb-section" data-testid="ticket-usage-detail"><h3>Usage</h3><p>{usageSentence(ticket.usage)}</p></div> : null}
    {ticket.thread_id ? <button className="oh-btn" type="button" onClick={() => openRemThread(ticket.thread_id)}><ExternalLink size={13} />Open its thread</button> : null}
    {hasPatch(ticket) ? <div className="kb-section"><h3>Changes</h3>
      <TicketPatchReview key={ticket.ticket_id} ticket={ticket} worker={ticket.scope === "rem" ? "Rem" : ticket.assignee || "the worker"} decide={canDecide(ticket)}
        expanded={reviewing} onExpand={setReviewing} onUpdated={response => { if (response?.board) apply(response); }} />
    </div> : null}
    {ticket.proposed ? <div className="kb-actions">
      <button className="oh-btn primary" type="button" onClick={() => act({ ...base, action: "approve" })}><Check size={14} />Approve for To do</button>
      <label className="oh-sr" htmlFor="kb-decline">Reason (optional)</label>
      <input id="kb-decline" className="kb-input" placeholder="Reason (optional)" value={reason} onChange={event => setReason(event.target.value)} />
      <button className="oh-btn danger" type="button" onClick={() => { onDecline(reason.trim() || undefined); onClose(); }}>Decline</button>
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
      <button className="oh-btn primary" type="button" onClick={() => onMove("in_progress")}><Play size={14} />Resume</button>
    </div><p className="kb-hint">Resumes the same task in its thread. To end it instead, stop it from its task in Rem.</p></div> : null}
    {!ticket.proposed && !ticket.running && !ticket.paused ? <div className="kb-section kb-move"><h3>Move</h3><div className="kb-actions">
      {ticket.status === "cancelled" ? <button className="oh-btn" type="button" onClick={() => onMove("backlog")}>Restore to Backlog</button> : <>
        {canDropOn(ticket, "todo") ? <button className="oh-btn" type="button" onClick={() => onMove("todo")}>To do</button> : null}
        {canDropOn(ticket, "in_progress") ? <button className="oh-btn" type="button" onClick={() => onMove("in_progress")}>Start now</button> : null}
        {canDropOn(ticket, "backlog") ? <button className="oh-btn" type="button" onClick={() => onMove("backlog")}>Backlog</button> : null}
        {ticket.scope === "rem" && canDropOn(ticket, "done") ? <button className="oh-btn" type="button" onClick={() => onMove("done")}>Mark done</button> : null}
        {ticket.scope === "rem" && ticket.status !== "done" ? <button className="oh-btn ghost" type="button" onClick={() => { onMove("cancelled"); onClose(); }}>Cancel ticket</button> : null}
      </>}
    </div></div> : null}
    <TicketTimeline ticket={ticket} />
    {guard.confirmDialog}
  </aside>;
}

// Rem writes ticket text in Markdown. The sheet shows it formatted, with no images.
const TicketMarkdown = ({ value }) => <MarkdownContent compact images={false} className="oh-md" value={value} />;

// A description longer than about 12 lines starts folded to its first few, so Done when,
// Waits for and Move stay close. The phone folds it the same way. Lines are counted from the
// text, about 60 characters to a line at the sheet's width.
const FOLD_LINES = 12;
const LINE_CHARS = 60;
const foldsDescription = text => String(text).split("\n")
  .reduce((lines, line) => lines + Math.max(1, Math.ceil(line.length / LINE_CHARS)), 0) > FOLD_LINES;

function TicketDescription({ text }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const long = foldsDescription(text);
  const folded = long && !open;
  // The whole text stays in the page; tabbing to a link in the hidden part opens it.
  const reveal = event => {
    if (!folded || event.target === event.currentTarget) return;
    const bottom = event.currentTarget.getBoundingClientRect?.().bottom;
    if (bottom === undefined || event.target.getBoundingClientRect?.().bottom > bottom) setOpen(true);
  };
  return <div className="kb-description">
    <div id={id} className="kb-fold" data-folded={folded || undefined} onFocus={reveal}><TicketMarkdown value={text} /></div>
    {long ? <button type="button" className="kb-fold-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      {open ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}{open ? "Show less" : "Show all"}
    </button> : null}
  </div>;
}

// The ticket's finish line: each line is one check its review makes.
function DoneWhen({ ticket }) {
  const lines = (ticket.done_when || "").split("\n").map(line => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  return <div className="kb-section" data-testid="ticket-done-when">
    <h3>Done when</h3>
    <ul className="kb-done-list">{lines.map((line, index) => <li key={index}><Check size={13} aria-hidden="true" /><span>{line}</span></li>)}</ul>
  </div>;
}

// What a ticket waits for, with its notice, and the controls to change the list.
function WaitsFor({ ticket, board, act }) {
  const editable = canEditWaits(ticket);
  const listed = ticket.waits_for || [];
  const choices = editable ? waitChoices(board, ticket) : [];
  if (!listed.length && !choices.length) return null;
  const ids = listed.map(item => item.ticket_id);
  return <div className="kb-section kb-waits-section" data-testid="ticket-waits-for">
    <h3>Waits for</h3>
    {ticket.wait_notice ? <p className="kb-wait-notice" role="note"><TriangleAlert size={13} aria-hidden="true" /><span>{ticket.wait_notice}</span></p> : null}
    {listed.length ? <ul className="kb-wait-list">
      {listed.map(item => <li key={item.ticket_id} className={item.status === "done" ? "is-done" : ""}>
        {item.key ? <span className="oh-key">{item.key}</span> : null}
        <span className="kb-wait-title">{item.title}</span>
        <span className={`oh-pill st-${item.status === "in_progress" ? "running" : item.status}`}>{STATUS_LABELS[item.status] || (item.status === "removed" ? "Removed" : item.status)}</span>
        {editable ? <button className="oh-btn icon ghost small" type="button" aria-label={`Stop waiting for ${item.key || item.title}`} title="Stop waiting for this" onClick={() => act(waitsChange(ticket, ids.filter(id => id !== item.ticket_id)))}><X size={13} /></button> : null}
      </li>)}
    </ul> : <p className="kb-hint">Nothing. Choose a ticket that has to be Done first.</p>}
    {editable && choices.length ? <label className="kb-field"><span className="oh-sr">Wait for another ticket</span>
      <select className="kb-input" value="" data-testid="ticket-add-wait" onChange={event => { if (event.target.value) act(waitsChange(ticket, [...ids, event.target.value])); }}>
        <option value="">{listed.length ? "Also wait for…" : "Wait for a ticket…"}</option>
        {choices.map(other => <option key={other.ticket_id} value={other.ticket_id}>{other.key} {other.title}</option>)}
      </select></label> : null}
    {listed.length && ticket.scope === "rem" ? <p className="kb-hint">Self-work starts this only after every ticket here is Done. You can still start it yourself.</p> : null}
  </div>;
}

const projectLabel = (board, root) => board.projects.find(project => project.root === root)?.name || root;

function NewTicket({ board, act, onClose }) {
  const rem = board.participants.find(item => item.kind === "rem");
  const openProjects = rem?.assignable || [];
  // Starts on one of Rem's assigned projects, so a new ticket can be picked up on its own.
  const [start] = useState(() => ({ title: "", description: "", done_when: "", priority: "medium", status: "todo", owner: "rem", worker: "", project: rem?.projects.find(root => openProjects.includes(root)) || "" }));
  const [form, setForm] = useState(start);
  const [busy, setBusy] = useState(false);
  const change = values => setForm(current => ({ ...current, ...values }));
  const organization = form.owner === "rem" ? null : board.organizations.find(org => org.organization_id === form.owner);
  const submit = async event => {
    event.preventDefault();
    if (!form.title.trim() || busy) return;
    setBusy(true);
    const saved = await act({
      action: "create", title: form.title.trim(), description: form.description.trim(), priority: form.priority, status: form.status,
      ...(organization ? { scope: "organization", organization_id: organization.organization_id, assignee_id: form.worker || undefined }
        : { project_dir: form.project || undefined, ...(doneWhenText(form.done_when) ? { done_when: doneWhenText(form.done_when) } : {}) }),
    });
    setBusy(false);
    if (saved) onClose();
  };
  // What was typed or chosen stays until Cancel, Add ticket or a confirmed Escape.
  const changed = Object.keys(start).some(key => form[key] !== start[key]);
  const guard = useDiscardGuard({ changed, onClose, message: "Discard this ticket? What you entered isn't saved." });
  return <>
    <Dialog title="New ticket" onClose={guard.requestClose} overlayClassName="kb-overlay" overlayProps={guard.outsidePress} panelClassName="kb-dialog">
    <form className="oh-form" onSubmit={submit}>
      <h2 aria-hidden="true">New ticket</h2>
      <label>Title<input autoFocus maxLength={500} value={form.title} onChange={event => change({ title: event.target.value })} /></label>
      <label>Description<textarea value={form.description} onChange={event => change({ description: event.target.value })} /></label>
      <label>For
        <select value={form.owner} onChange={event => change({ owner: event.target.value, worker: "" })}>
          <option value="rem">Rem</option>
          {board.organizations.map(org => <option key={org.organization_id} value={org.organization_id}>{org.name}</option>)}
        </select>
      </label>
      {!organization ? <label>Done when <small>(optional)</small><textarea maxLength={1000} placeholder="One check per line" value={form.done_when} onChange={event => change({ done_when: event.target.value })} /></label> : null}
      {!organization ? <label>Project<select value={form.project} onChange={event => change({ project: event.target.value })}><option value="">No project</option>{openProjects.map(root => <option key={root} value={root}>{projectLabel(board, root)}</option>)}</select></label> : null}
      {organization ? <label>Worker<select value={form.worker} onChange={event => change({ worker: event.target.value })}><option value="">Unassigned</option>{organization.employees.map(employee => <option key={employee.employee_id} value={employee.employee_id}>{employee.name}</option>)}</select></label> : null}
      <label>Priority<select value={form.priority} onChange={event => change({ priority: event.target.value })}>{PRIORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Column<select value={form.status} onChange={event => change({ status: event.target.value })}><option value="todo">To do</option><option value="backlog">Backlog</option></select></label>
      <div className="kb-actions"><button className="oh-btn primary" type="submit" disabled={busy || !form.title.trim()}>Add ticket</button><button className="oh-btn ghost" type="button" onClick={onClose}>Cancel</button></div>
    </form>
    </Dialog>
    {guard.confirmDialog}
  </>;
}
