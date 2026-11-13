import { useCallback, useEffect, useRef, useState } from "react";
import { request as remRequest } from "./remHomeStore.js";
import { startPolling } from "./refresh.js";

// The global Kanban board: Rem's tickets beside every organization's. The backend owns the
// tickets and the rules; this only shows them and asks for changes.

export const COLUMNS = Object.freeze([
  ["backlog", "Backlog"],
  ["todo", "To do"],
  ["in_progress", "In progress"],
  ["in_review", "In review"],
  ["blocked", "Blocked"],
  ["done", "Done"],
]);

export const PRIORITIES = Object.freeze([["critical", "Critical"], ["high", "High"], ["medium", "Medium"], ["low", "Low"]]);

const MODE_LABELS = Object.freeze({ off: "Off", waiting: "Waiting", working: "Working", paused: "Paused" });

export const modeLabel = mode => MODE_LABELS[mode] || "Waiting";

// Columns a ticket can be dragged to. Reviews and finished runs land on their own.
export function canDropOn(ticket, column) {
  // A paused ticket keeps its run: moving it to In progress resumes that run, nothing else.
  if (ticket?.paused) return column === "in_progress";
  if (!ticket || ticket.status === column || ticket.running || ticket.proposed) return false;
  if (ticket.scope === "organization") return ["backlog", "todo", "in_progress"].includes(column) && ticket.status !== "in_review";
  return ["backlog", "todo", "in_progress", "done"].includes(column);
}

export function ownerFilters(board) {
  return [["all", "Everyone"], ["rem", "Rem"], ...(board?.organizations || []).map(org => [org.organization_id, org.name])];
}

// Rem and every organization's workers, as people on the board.
export function participantOf(ticket, participants = []) {
  if (ticket.scope === "rem") return participants.find(person => person.kind === "rem") || null;
  return participants.find(person => person.organization_id === ticket.organization_id && person.employee_id === ticket.assignee_id) || null;
}

export function initials(name = "") {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return (words.length > 1 ? words[0][0] + words.at(-1)[0] : words[0].slice(0, 2)).toUpperCase();
}

const AVATAR_COLORS = ["#215879", "#17767b", "#6a4bb0", "#8f5d16", "#a13c36", "#23765a", "#4d5d6a", "#7a3f8f"];
export function avatarColor(id = "") {
  if (id === "rem") return "#6a4bb0";
  let hash = 0;
  for (const letter of id) hash = (hash * 31 + letter.charCodeAt(0)) % 9973;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

const SCOPE_NOTES = Object.freeze({
  no_project: "No project, so Rem won't pick this up on its own",
  unassigned: "Rem isn't assigned to this project",
  unavailable: "This project isn't available right now",
});
// Why self-work leaves one of Rem's open tickets alone, or nothing when it can take it.
export function scopeNote(ticket) {
  if (ticket.scope !== "rem" || ticket.proposed || !["backlog", "todo"].includes(ticket.status)) return "";
  return SCOPE_NOTES[ticket.self_work_scope] || "";
}

// What a person's row says about working alone.
export function assignmentLine(person) {
  const usable = person.projects.length - person.unavailable.length;
  if (!person.projects.length) return "No projects, so no self-work";
  if (!usable) return "Assigned projects aren't available";
  return `Can work alone in ${usable} ${usable === 1 ? "project" : "projects"}`;
}

export function filterTickets(tickets = [], { owner = "all", query = "", closed = false, participant = null } = {}) {
  const needle = query.trim().toLowerCase();
  return tickets.filter(ticket => {
    if (!closed && ticket.status === "cancelled") return false;
    if (owner === "rem" && ticket.scope !== "rem") return false;
    if (owner !== "rem" && owner !== "all" && ticket.organization_id !== owner) return false;
    if (participant && !(participant.kind === "rem" ? ticket.scope === "rem" : ticket.organization_id === participant.organization_id && ticket.assignee_id === participant.employee_id)) return false;
    return !needle || `${ticket.title} ${ticket.key}`.toLowerCase().includes(needle);
  });
}

// Tickets by column. In Backlog: proposals, then Rem's ranking with left-out tickets last.
// Everything else goes by priority and then newest.
export function columnTickets(tickets = []) {
  const order = { critical: 0, urgent: 0, high: 1, medium: 2, low: 3 };
  const grouped = Object.fromEntries(COLUMNS.map(([column]) => [column, []]));
  for (const ticket of tickets) {
    if (grouped[ticket.status]) grouped[ticket.status].push(ticket);
  }
  for (const list of Object.values(grouped)) {
    list.sort((a, b) => Number(b.proposed) - Number(a.proposed)
      || Number(Boolean(a.skip_reason)) - Number(Boolean(b.skip_reason))
      || (b.backlog_score || 0) - (a.backlog_score || 0)
      || (order[a.priority] ?? 2) - (order[b.priority] ?? 2)
      || String(b.created_at).localeCompare(String(a.created_at)));
  }
  return grouped;
}

// One line per thing Rem is working on by itself, research and ranking included.
export function activityLine(item) {
  const line = item.project && !item.title.endsWith(item.project) ? `${item.title} · ${item.project}` : item.title;
  return item.paused ? `${line} · ${pausedLabel(item.paused)}` : line;
}

// A paused ticket or self-work item: paused by you, or making way for your own work.
export const pausedLabel = by => by === "priority" ? "Paused for your work" : "Paused";

// What the board header says about self-work, in one short phrase. While waiting for quiet
// minutes inside a timed window, it also says when the window ends.
export function selfWorkLine(view) {
  if (!view) return "";
  const { state, settings } = view;
  if (state.mode === "waiting" && state.starts_at) {
    const active = selfWorkWindows(view)?.active;
    return `Starts after ${settings.idle_minutes} quiet minutes${active?.end_label ? ` · ends ${active.end_label}` : ""}`;
  }
  return state.detail || "";
}

// Timed self-work: the window running now, the ones coming up and the last period that
// ended. Older desktops send no schedule, so there is nothing to show.
export function selfWorkWindows(view) {
  const schedule = view?.schedule;
  if (!schedule || typeof schedule !== "object") return null;
  return {
    active: schedule.active || null,
    upcoming: Array.isArray(schedule.upcoming) ? schedule.upcoming : [],
    last: schedule.last_period?.summary ? schedule.last_period : null,
  };
}

// "Ends 7:00 PM Thu 12 Nov (Asia/Irkutsk), then back to off": the line under a window.
export function windowDetail(window) {
  if (!window) return "";
  const end = window.end_label ? `Ends ${window.end_label}` : "";
  return [end, window.after_label].filter(Boolean).join(", ");
}

// A note when the switch shows a window's state rather than the saved one, such as
// "On for this window, then back to off." Older desktops don't send saved_enabled.
export function savedSwitchNote(view) {
  const settings = view?.settings;
  if (!settings || typeof settings.saved_enabled !== "boolean" || settings.saved_enabled === settings.enabled) return "";
  if (!settings.enabled) return "Off for now. The saved switch is on.";
  const after = selfWorkWindows(view)?.active?.after_end;
  return after === "on" ? "On for this window; stays on after it." : "On for this window; goes back to off after it.";
}

// Whether self-work uses tickets: the desktop's Kanban board is on. Older desktops don't say,
// and always used the board.
export const boardOn = view => view?.state?.board_enabled !== false;

// What turning backlog pulling on or off would do, in one sentence.
export function autoPullHint(view) {
  if (!boardOn(view)) return "Needs the Kanban board. With the board off, Rem doesn't take tickets.";
  return view?.settings?.auto_pull_backlog
    ? "After its to-dos, Rem takes the highest-impact backlog tickets and leaves out any that no longer make sense. Spare threads research new ideas for your approval."
    : "After its to-dos, Rem leaves the backlog alone and researches new ideas for your approval.";
}

// The projects Rem can be given for self-work, with the ones it has ticked. A project Rem
// has that is no longer open stays listed, so it can be taken away.
export function remProjectChoices(board) {
  const rem = board?.participants?.find(person => person.kind === "rem");
  if (!rem) return [];
  const names = new Map((board.projects || []).map(project => [project.root, project.name]));
  const roots = [...new Set([...rem.assignable, ...rem.projects])];
  return roots.map(root => ({
    root,
    name: names.get(root) || root.split(/[\\/]/).filter(Boolean).at(-1) || root,
    checked: rem.projects.includes(root),
    assignable: rem.assignable.includes(root),
    unavailable: rem.unavailable.includes(root),
  }));
}

export const withProject = (projects, root, on) => on ? [...new Set([...projects, root])] : projects.filter(item => item !== root);

// Asks the window to show Settings > Rem, where self-work is set up.
export const openSelfWorkSettings = () => window.dispatchEvent(new CustomEvent("raticode:open-rem-settings"));

// Whether the thread limit the user chose is being capped by Settings > Rem.
export function cappedThreads(view) {
  return view && view.state.thread_limit < view.settings.max_threads ? view.state.thread_limit : null;
}

// A ticket pill in Rem's chat asks the board to open one ticket. The board may not be
// mounted yet (it loads when its view opens), so the request waits here until it is.
let ticketRequest = null;
const ticketListeners = new Set();

export function openKanbanTicket(ticket) {
  if (!ticket?.ticket_id && !ticket?.key) return;
  ticketRequest = { ticket_id: ticket.ticket_id || "", key: ticket.key || "" };
  for (const listener of ticketListeners) listener();
  globalThis.dispatchEvent?.(new CustomEvent("gofer:kanban-open", { detail: { ticket_id: ticketRequest.ticket_id } }));
}

// The waiting request, once: the board that takes it opens that ticket.
export function takeKanbanTicket() {
  const request = ticketRequest;
  ticketRequest = null;
  return request;
}

export function onKanbanTicket(listener) {
  ticketListeners.add(listener);
  return () => ticketListeners.delete(listener);
}

// Where a requested ticket is on the board: the ticket, or why it can't be shown.
export function findRequestedTicket(board, request) {
  if (!board || !request) return { ticket: null, missing: "" };
  const ticket = board.tickets.find(item => item.ticket_id === request.ticket_id)
    || (request.key ? board.tickets.find(item => item.key === request.key) : null);
  if (ticket) return { ticket, missing: "" };
  const name = request.key || "That ticket";
  return { ticket: null, missing: board.earlier ? `${name} isn't on the board now. It may have been deleted, or it's older than the board shows.` : `${name} isn't on the board anymore.` };
}

let sequence = 0;
const requestId = () => `kanban-${Date.now().toString(36)}-${(++sequence).toString(36)}`;

const post = (path, method, body) => remRequest(`/kanban${path}`, { method, body });

export const sendTicket = change => post("/ticket", "POST", { request_id: requestId(), ...change });
// One ticket's timeline: its status changes and comments, oldest first.
export const readTimeline = ticket => post("/timeline", "POST", ticketAddress(ticket));
export const sendComment = (ticket, body) => sendTicket({ ...ticketAddress(ticket), action: "comment", body });
const ticketAddress = ticket => ({ ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined });

export const STATUS_LABELS = Object.freeze(Object.fromEntries([...COLUMNS, ["cancelled", "Cancelled"]]));

// How an entry looks: the column it moved to decides its color; comments have none.
export function entryTone(entry) {
  if (entry.kind !== "status") return "note";
  if (entry.to === "blocked") return "bad";
  if (entry.to === "done") return "done";
  if (entry.to === "in_review") return "review";
  if (entry.to === "in_progress") return "run";
  return "idle";
}

// "Moved to Blocked: task failed" reads as a headline; comments show their author instead.
export function entryHeadline(entry) {
  if (entry.title) return entry.title;
  if (entry.kind === "status" && entry.to) return `Moved to ${STATUS_LABELS[entry.to] || entry.to}`;
  return "";
}

// The newest entries are the ones that matter, so a long timeline keeps them and folds the rest.
export function visibleEntries(entries = [], expanded = false, keep = 8) {
  if (expanded || entries.length <= keep) return { shown: entries, hidden: 0 };
  return { shown: entries.slice(-keep), hidden: entries.length - keep };
}

// "2 of 3 checks passed", for a line above the list of checks.
export function checksLine(checks = []) {
  if (!checks.length) return "";
  const passed = checks.filter(check => check.result === "pass").length;
  return passed === checks.length ? `${passed === 1 ? "The check" : `All ${passed} checks`} passed` : `${passed} of ${checks.length} checks passed`;
}
export const saveParticipant = (person, projects) => post("/participant", "POST", { participant_id: person.participant_id, projects, expected_revision: person.revision });
export const saveSelfWork = change => post("/settings", "PUT", change);
// Ends a running self-work window now (its report is posted) or drops one that hasn't started.
export const cancelSelfWorkWindow = windowId => post("/window", "POST", { action: "cancel", window_id: windowId });
export const markReportSeen = updatedAt => post("/report/seen", "POST", { updated_at: updatedAt || undefined });

// Keeps one view of the board and self-work current while a surface is open.
export function useKanban(active = true, interval = 4000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const version = useRef("");
  const apply = useCallback(result => {
    if (!result || result.unchanged) return;
    version.current = result.version || "";
    setData(result);
    setError("");
  }, []);
  const refresh = useCallback(async () => {
    try { apply(await remRequest("/kanban", { known: version.current })); }
    catch (failure) { setError(failure.message); }
  }, [apply]);
  useEffect(() => {
    if (!active) return undefined;
    return startPolling(refresh, { interval, immediate: true, label: "the Kanban board" });
  }, [active, interval, refresh]);
  // Every change returns the new board, so the screen doesn't wait for the next poll.
  const run = useCallback(async action => {
    try { version.current = ""; apply(await action()); return true; }
    catch (failure) { setError(failure.message); return false; }
  }, [apply]);
  return { board: data?.board || null, selfWork: data?.self_work || null, error, setError, refresh, run };
}
