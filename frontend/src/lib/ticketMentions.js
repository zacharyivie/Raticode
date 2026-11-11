// Board ticket keys in Rem's chat (REM-5, AGE-18) show as pills with the ticket's title,
// like a smart link. The backend resolves keys against the real board and sends only the
// tickets that exist ("tickets" on the conversation and task polls); any other key-shaped
// text, such as UTF-8, stays plain. Same rule as src/gofer/ui/rem_kanban.py TICKET_KEY and
// the phone's TicketMentions.kt: a whole word, not part of a path, word or version.
export const TICKET_KEY = /(?<![A-Za-z0-9_\-/.#@])[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,6}(?![A-Za-z0-9_-]|\.[0-9])/g;

export const TICKET_STATUS_LABELS = Object.freeze({
  backlog: "Backlog",
  todo: "To do",
  in_progress: "In progress",
  in_review: "In review",
  blocked: "Blocked",
  done: "Done",
  cancelled: "Closed",
});

export function ticketStatusLabel(status) {
  return TICKET_STATUS_LABELS[status] || "";
}

// Keys written in text, first mention first, each once.
export function ticketKeys(text) {
  if (typeof text !== "string" || !text.includes("-")) return [];
  return [...new Set(text.match(TICKET_KEY) || [])];
}

// Splits text into plain strings and the keys of tickets in [index].
export function splitTicketText(text, index) {
  const parts = [];
  if (typeof text !== "string" || !index) return [text];
  let from = 0;
  for (const match of text.matchAll(TICKET_KEY)) {
    if (!index[match[0]]) continue;
    if (match.index > from) parts.push(text.slice(from, match.index));
    parts.push({ key: match[0] });
    from = match.index + match[0].length;
  }
  if (from < text.length || !parts.length) parts.push(text.slice(from));
  return parts;
}

// The tickets a task card lists: the live run's, else the ones stored with the message.
// Created first, then worked on, then changed, as the backend orders them by strength.
export function cardTickets(work, run) {
  const refs = Array.isArray(run?.tickets) && run.tickets.length ? run.tickets : work?.tickets;
  return (Array.isArray(refs) ? refs : []).filter(ref => ref && typeof ref.key === "string" && ref.key);
}

// Every ticket a task's runs touched, newest run first, one per ticket.
export function taskTickets(task) {
  const seen = new Map();
  for (const run of [task?.active_run, task?.last_run, ...(task?.recent_runs || [])]) {
    for (const ref of cardTickets(null, run)) if (!seen.has(ref.ticket_id || ref.key)) seen.set(ref.ticket_id || ref.key, ref);
  }
  return [...seen.values()];
}

// Code, links and images keep their text, so a key in a code block or a link stays as written.
const SKIPPED = new Set(["link", "linkReference", "definition", "code", "inlineCode", "html", "image", "imageReference"]);

// A remark plugin: each key of a ticket in [index] becomes a <ticket-pill> element that
// MarkdownContent renders as a TicketPill.
export function remarkTicketMentions(index) {
  return () => tree => {
    if (index && Object.keys(index).length) pillChildren(tree, index);
  };
}

function pillChildren(node, index) {
  if (!Array.isArray(node.children)) return;
  const children = [];
  for (const child of node.children) {
    if (child.type === "text") {
      for (const part of splitTicketText(child.value, index)) {
        if (typeof part === "string") { if (part) children.push({ type: "text", value: part }); }
        else children.push({ type: "ticketMention", data: { hName: "ticket-pill", hProperties: { dataTicketKey: part.key }, hChildren: [{ type: "text", value: part.key }] } });
      }
    } else {
      if (!SKIPPED.has(child.type)) pillChildren(child, index);
      children.push(child);
    }
  }
  node.children = children;
}
