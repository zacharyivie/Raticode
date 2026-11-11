import { useState } from "react";
import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleDot, CircleSlash } from "lucide-react";
import { openKanbanTicket } from "../lib/remKanban.js";
import { ticketStatusLabel } from "../lib/ticketMentions.js";
import "./TicketPill.css";

// Each status has its own shape, so color is never the only cue.
const STATUS_ICONS = {
  backlog: CircleDashed,
  todo: Circle,
  in_progress: CircleDot,
  in_review: CircleDot,
  blocked: CircleAlert,
  done: CircleCheck,
  cancelled: CircleSlash,
};

// A board ticket named in Rem's chat, shown like a smart link: its key, a status mark and
// its current title. Clicking opens the ticket on the global board. [ticket] is the ticket
// as the board has it now; [stored] is what a task card remembered, used when the ticket
// was deleted, so history still says which ticket it was.
export default function TicketPill({ ticketKey, ticket = null, stored = null, role = "" }) {
  const key = ticket?.key || stored?.key || ticketKey;
  if (!ticket) {
    if (!stored) return <>{key}</>;
    const gone = `${key}: ${stored.title || "ticket"}, no longer on the board`;
    return <span className="ticket-pill" data-gone="true" role="note" aria-label={gone} title={gone}>
      <CircleSlash className="ticket-pill-mark" size={11} aria-hidden="true" />
      <span className="ticket-pill-key">{key}</span>
      {stored.title ? <span className="ticket-pill-title">{stored.title}</span> : null}
    </span>;
  }
  const status = ticketStatusLabel(ticket.status);
  const Mark = STATUS_ICONS[ticket.status] || Circle;
  const where = ticket.scope === "organization" && ticket.organization_name ? `, ${ticket.organization_name}` : "";
  const label = `Open ticket ${key}: ${ticket.title}${status ? ` (${status}${ticket.proposed ? ", waiting for your approval" : ""})` : ""}${where}${role ? `. ${role}` : ""}`;
  return <button type="button" className="ticket-pill" data-status={ticket.status} aria-label={label} title={label}
    onClick={event => { event.preventDefault(); event.stopPropagation(); openKanbanTicket(ticket); }}>
    <Mark className="ticket-pill-mark" size={11} strokeWidth={2.4} aria-hidden="true" />
    <span className="ticket-pill-key">{key}</span>
    {ticket.title ? <span className="ticket-pill-title">{ticket.title}</span> : null}
  </button>;
}

const ROLE_LABELS = { created: "Created by this task", worked: "Worked on by this task", updated: "Changed by this task" };

// The tickets a task created, worked on or changed, as a row of pills. Long lists show
// the first few and a button for the rest.
export function TicketPills({ refs, index, limit = 6, label = "Tickets" }) {
  if (!refs?.length) return null;
  return <TicketPillRow refs={refs} index={index} limit={limit} label={label} />;
}


function TicketPillRow({ refs, index, limit, label }) {
  const [all, setAll] = useState(false);
  const shown = all ? refs : refs.slice(0, limit);
  return <div className="ticket-pills" role="group" aria-label={`${label}: ${refs.length}`}>
    {shown.map(ref => <TicketPill key={ref.ticket_id || ref.key} ticketKey={ref.key} ticket={index?.[ref.key]?.ticket_id && (!ref.ticket_id || index[ref.key].ticket_id === ref.ticket_id) ? index[ref.key] : null} stored={ref} role={ROLE_LABELS[ref.role] || ""} />)}
    {refs.length > shown.length ? <button type="button" className="ticket-pills-more" onClick={() => setAll(true)} aria-label={`Show ${refs.length - shown.length} more tickets`}>+{refs.length - shown.length} more</button> : null}
  </div>;
}
