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

// What a move did, for the board's notice: "REM-12 cancelled." or "REM-12 moved to Backlog."
export function moveNotice(ticket, column) {
  if (column === "cancelled") return `${ticket.key} cancelled.`;
  if (column === "in_progress" && ticket.scope === "rem") return `${ticket.key} ${ticket.paused ? "resumed" : "started"}.`;
  return `${ticket.key} moved to ${STATUS_LABELS[column] || column}.`;
}

// Undo sends a ticket back with another move, so it's offered only where that move exists:
// never after starting work (In progress, which the backend also takes as "running"), and not back
// into In review or Blocked, which runs decide. A ticket of unknown scope has no Undo. The phone's
// Kanban.canUndoMove follows the same rules, and both are tested against
// mobile/android/protocol/probes/kanban-cases/undo-move.json.
const UNDO_COLUMNS = { rem: ["backlog", "todo", "done", "cancelled"], organization: ["backlog", "todo"] };
export function canUndoMove(ticket, column) {
  return column !== "in_progress" && column !== "running" && !ticket.paused && (UNDO_COLUMNS[ticket.scope] || []).includes(ticket.status);
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

// A token count as people say it: 950, 12.4k, 3.2M.
export function formatTokens(count) {
  if (count >= 1e6) return `${Number((count / 1e6).toFixed(1))}M`;
  if (count >= 1e3) return `${Number((count / 1e3).toFixed(1))}k`;
  return String(count);
}

// A ticket's model use in a few words for its card, or "" before any call. A provider that
// doesn't report its usage makes that call unknown, so a total is never shown as complete
// when it isn't. Older desktops send no usage.
export function usageShort(usage) {
  if (!usage?.calls) return "";
  const unknown = (usage.unknown_calls || 0) + (usage.partial_calls || 0);
  if (usage.total_tokens == null) return "Tokens unknown";
  return `${formatTokens(usage.total_tokens)} tokens${unknown ? ` + ${unknown} unknown` : ""}`;
}

// The same in a sentence, for the ticket's sheet.
export function usageSentence(usage) {
  if (!usage) return "";
  const calls = usage.calls || 0;
  if (!calls) return "No model calls yet.";
  const plural = n => `${n} model ${n === 1 ? "call" : "calls"}`;
  const unknown = (usage.unknown_calls || 0) + (usage.partial_calls || 0);
  if (usage.total_tokens == null) return `Unknown: the provider didn't report usage for ${calls === 1 ? "its" : "any of its"} ${plural(calls)}.`;
  const total = `${usage.total_tokens.toLocaleString("en-US")} tokens over ${plural(calls)}`;
  if (!unknown) return `${total}.`;
  return `${total}. ${unknown === calls ? "All" : unknown} of them ${unknown === 1 ? "was" : "were"} not fully reported, so the real total is higher.`;
}

// Self-work's limits in one line for Settings, such as "Stops new work past 500k tokens a
// session or at 80% of a rate limit." Older desktops send no limits.
export function selfWorkLimitsLine(settings) {
  if (!settings) return "";
  const parts = [];
  if (settings.session_token_limit) parts.push(`${formatTokens(settings.session_token_limit)} tokens a session`);
  if (settings.daily_token_limit) parts.push(`${formatTokens(settings.daily_token_limit)} tokens a day`);
  const tokens = parts.length ? `past ${parts.join(" or ")}` : "";
  const rate = settings.rate_limit_percent ? `at ${settings.rate_limit_percent}% of a rate limit` : "";
  const paced = settings.allowance_mode === "pace";
  const daily = !paced && settings.daily_allowance_percent ? `after ${settings.daily_allowance_percent}% of a weekly allowance in a day` : "";
  const pace = paced ? `while ahead of a pace line to ${settings.pace_aim_percent || 97}% by the weekly reset` : "";
  if (!tokens && !rate && !daily && !pace) return "No limits: self-work starts new work whenever threads are free.";
  const verb = pace && !tokens && !rate ? "Holds new work" : "Stops starting new work";
  return `${verb} ${[tokens, rate, daily, pace].filter(Boolean).join(" or ")}. Running work finishes normally.`;
}

// How self-work uses a weekly allowance: one choice of three (Settings > Rem > Self-work).
export const ALLOWANCE_MODES = [["off", "Off"], ["daily", "Daily cap"], ["pace", "Pace to reset"]];

/** Off, Daily cap or Pace to reset; a desktop from before the choice has only the daily cap. */
export function allowanceMode(settings) {
  if (!settings) return "off";
  if (ALLOWANCE_MODES.some(([value]) => value === settings.allowance_mode)) return settings.allowance_mode;
  return settings.daily_allowance_percent ? "daily" : "off";
}

/** Whether the desktop offers the three-way choice; older ones only take a daily percent. */
export const hasAllowanceModes = settings => Boolean(settings && "allowance_mode" in settings);

/** Pace to reset's status line, such as "Pacing work's weekly allowance: 23% used, line 24%." */
export function allowancePaceLine(paced) {
  if (!paced || typeof paced !== "object") return "";
  return typeof paced.message === "string" ? paced.message : "";
}

/** A note when the rate-limit stop would halt self-work before Pace to reset reaches its aim. */
export function paceRateWarning(settings) {
  if (allowanceMode(settings) !== "pace") return "";
  const rate = settings.rate_limit_percent || 0;
  const aim = settings.pace_aim_percent || 97;
  if (!rate || rate >= aim) return "";
  return `Rate limit use stops self-work at ${rate}%, before the pace aim of ${aim}%.`;
}

// Self-work's account per provider (Settings > Rem > Self-work). The values match a thread's
// Account menu: "" follows Rem's setting, "@default" is the provider's default sign-in,
// "auto" chooses by usage and anything else names an account.
const settingName = setting => setting === "auto" ? "Choose by usage" : setting || "Default sign-in";

/** The rows to show: one per provider with accounts, each with its select options. */
export function selfWorkAccountRows(view) {
  const rows = Array.isArray(view?.accounts) ? view.accounts : [];
  return rows.map(row => {
    const names = Array.isArray(row.accounts) ? row.accounts : [];
    const choice = typeof row.choice === "string" ? row.choice : "";
    const options = [
      ["", `Rem’s setting (${settingName(row.rem_setting)})`],
      ["@default", "Default sign-in"],
      ["auto", "Choose by usage"],
      ...names.map(name => [name, name]),
    ];
    // A named account that was removed stays selectable until another choice replaces it.
    if (!options.some(([value]) => value === choice)) options.push([choice, `${choice} (removed)`]);
    return { provider: row.provider, label: row.label || row.provider, choice, options };
  });
}

/** One provider's change, as Settings > Rem > Self-work saves it. */
export const selfWorkAccountChange = (provider, choice) => ({ provider_accounts: { [provider]: choice } });

// Today's use of the weekly allowance of the account self-work runs on and the daily cap,
// from the desktop's own sentence, which names the account. Older desktops send nothing.
export function dailyAllowanceLine(today) {
  if (!today || typeof today !== "object") return "";
  return typeof today.message === "string" ? today.message : "";
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

// When the self-work running now stops: the user's condition, its latest time and Rem's
// last check. Null from a desktop without stop conditions.
export function selfWorkStop(view) {
  const stop = view?.stop;
  return stop && typeof stop === "object" ? stop : null;
}

// "Last check 8:40 PM today (America/New_York): not met. REM-210 is still in review."
export function stopJudgmentLine(stop) {
  if (!stop?.active) return "";
  let line = "";
  if (stop.judging) line = "Checking the condition now…";
  else if (stop.judgment) line = `Last check ${stop.judgment.at_label}: ${stop.judgment.met ? "met" : "not met"}.${stop.judgment.evidence ? ` ${stop.judgment.evidence}` : ""}`;
  else if (stop.condition) line = "Not checked yet: Rem checks it after each self-work task finishes.";
  if (stop.judge_failed) line = `${line} The last check failed; Rem tries again in 10 minutes.`.trim();
  return line;
}

// How the last session's stop went, once self-work ended.
export function lastStopLine(last) {
  if (!last) return "";
  if (last.stopped_by === "done_when") return `Stopped: “${last.condition}” was met.${last.judgment?.evidence ? ` ${last.judgment.evidence}` : ""}`;
  if (last.stopped_by === "no_later_than") return `Stopped at its latest time${last.no_later_than_label ? `, ${last.no_later_than_label}` : ""}.`;
  if (last.condition) return `Self-work ended before “${last.condition}” was met.`;
  return "";
}

// What Save sends from the editor: the condition ("" clears it) and a 24-hour time the
// backend resolves in the user's zone. An empty time keeps the current latest time when
// keepTime is set, and clears it otherwise. day is "", today, tonight or tomorrow.
export function stopPayload(stop, { condition, time, day, keepTime }) {
  const payload = { condition: String(condition || "").trim().slice(0, 500) };
  if (stop && Number.isInteger(stop.revision) && stop.revision > 0) payload.expected_revision = stop.revision;
  const clock = String(time || "").trim();
  if (clock) payload.no_later_than = { time: clock, ...(["today", "tonight", "tomorrow"].includes(day) ? { day } : {}) };
  else if (!keepTime) payload.no_later_than = null;
  return payload;
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

// The Start/Stop self-work button in Rem's side pane: what it says and what a click sends. It
// reads the same view as Settings > Rem, so a change from chat, the CLI or the phone shows on
// the next poll. Start starts self-work now, as telling Rem "start self-work now" does: it
// turns self-work on and doesn't wait for quiet minutes, with the objectives as they are.
// Stop turns self-work off and ends a running window. While self-work is on but waits for
// quiet minutes, startNow is the change that starts it without waiting.
export function selfWorkToggle(view) {
  const settings = view?.settings;
  if (!settings || typeof settings.enabled !== "boolean") return null;
  const on = settings.enabled;
  let detail;
  if (on) {
    const stop = selfWorkStop(view);
    detail = [selfWorkLine(view), stop?.active && stop.label ? `${stop.label}.` : ""].filter(Boolean).join(" ");
  } else {
    const next = selfWorkWindows(view)?.upcoming?.[0];
    detail = `Rem starts on its objectives now and keeps going until you stop it.${next?.label ? ` Next window: ${next.label}.` : ""}`;
  }
  const waiting = on && view.state?.mode === "waiting" && Boolean(view.state?.starts_at) && !view.state?.started_now_at;
  return {
    on,
    mode: view.state?.mode || (on ? "waiting" : "off"),
    label: on ? "Stop self-work" : "Start self-work",
    detail,
    change: on ? { enabled: false, expected_revision: settings.revision } : { start_now: true, expected_revision: settings.revision },
    startNow: waiting ? { start_now: true, expected_revision: settings.revision } : null,
  };
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

// Whether this desktop lets self-work approve its own tickets. Older backends don't send it.
export const hasAutoApprove = view => typeof view?.settings?.auto_approve_proposals === "boolean";

// What the auto-approval setting does now, in one sentence under its switch.
export function autoApproveHint(view) {
  if (!boardOn(view)) return "Needs the Kanban board. With the board off, Rem makes no tickets.";
  if (!view?.settings?.auto_approve_proposals) return "Off: ideas from Rem's research wait in Backlog for your approval.";
  const used = view?.state?.auto_approved_session || 0;
  const pulls = view.settings.auto_pull_backlog ? "Rem ranks them and works on the best ones." : "They wait in Backlog until you move them, or turn on backlog pulling.";
  return `On: new ideas go to Backlog approved, marked "Auto-approved", until self-work ends (${used} this session). ${pulls}`;
}

// What turning auto-approval on allows, shown before the user confirms it.
export function autoApproveWarning(view) {
  return [
    "While you're away, tickets Rem's research proposes go to Backlog already approved, for as long as self-work runs, without you reading them first.",
    view?.settings?.auto_pull_backlog
      ? "Backlog pulling is on, so Rem will also work on them, inside its projects and limits."
      : "Backlog pulling is off, so they wait in Backlog until you move them or turn pulling on.",
    "Ideas already waiting for you stay yours to decide. Rem still can't mark its own tickets Done, and you can edit or cancel any ticket it approved.",
  ];
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
  globalThis.window?.dispatchEvent?.(new CustomEvent("gofer:kanban-open", { detail: { ticket_id: ticketRequest.ticket_id } }));
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
// A ticket's full patch, where it lands and whether it merges cleanly.
export const readPatch = (ticket, branch) => post("/patch", "POST", { ...ticketAddress(ticket), ...(branch ? { branch } : {}) });
// The user's Accept: lands the reviewed branch in its base. The response's result says
// whether it landed or which files conflict.
export const landTicket = (ticket, patch, note = "") => sendTicket({ ...ticketAddress(ticket), action: "land", expected_revision: ticket.revision, patch_revision: patch.revision, branch: patch.branch, ...(note.trim() ? { note: note.trim() } : {}) });
// Line comments, a note, or a rebase request go back to the ticket's worker.
export const requestChanges = (ticket, change) => sendTicket({ ...ticketAddress(ticket), action: "request_changes", expected_revision: ticket.revision, ...change });
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
export const saveSelfWorkStop = change => post("/stop", "POST", change);
export const markReportSeen = updatedAt => post("/report/seen", "POST", { updated_at: updatedAt || undefined });

// The global board's tickets, read once, for quick open.
export async function readBoardTickets() {
  const tickets = (await remRequest("/kanban"))?.board?.tickets;
  return Array.isArray(tickets) ? tickets : [];
}

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

// Tickets that wait for other tickets. The backend keeps the list, refuses loops and decides
// when a ticket is ready; this only words it and builds the change.
const OPEN = ["backlog", "todo", "in_progress", "in_review", "blocked"];

// The card's line: what the ticket still waits for, or nothing once all of it is Done.
export function waitsLine(ticket) {
  const left = (ticket?.waits_for || []).filter(item => item.status !== "done");
  if (!left.length) return "";
  const first = left[0].title || left[0].key;
  return `Waits for: ${first}${left.length > 1 ? ` and ${left.length - 1} more` : ""}`;
}

// The card's pill for a ticket that contradicts decisions in force, e.g. "Violates D-4, D-7";
// "" for any other ticket. Self-work won't start it until the ticket or a decision changes.
export function violatesLine(ticket) {
  const ids = Array.isArray(ticket?.violates_decisions) ? ticket.violates_decisions.filter(Boolean) : [];
  if (!ids.length) return "";
  return `Violates ${ids.slice(0, 3).join(", ")}${ids.length > 3 ? ` +${ids.length - 3}` : ""}`;
}

// The Guardrail tag a guardrail retro puts on the tickets it proposes, or "".
export function guardrailTag(ticket) {
  return Array.isArray(ticket?.tags) && ticket.tags.includes("Guardrail") ? "Guardrail" : "";
}

// A Rem ticket's line about the self-work objectives: the one it advances, or, while
// objectives are open, that self-work leaves it alone. Null when there is nothing to say.
export function objectiveLine(ticket) {
  if (ticket?.scope !== "rem" || ["done", "cancelled"].includes(ticket.status)) return null;
  const text = ticket.objective?.text;
  if (text) return { off: false, short: text, detail: `Advances the self-work objective “${text}”.` };
  if (ticket.objective_left) return { off: true, short: "Not on an objective: self-work leaves it", detail: ticket.objective_left };
  return null;
}

// Whether the user can change what this ticket waits for from its sheet.
export function canEditWaits(ticket) {
  if (!ticket || ticket.proposed || !OPEN.includes(ticket.status)) return false;
  // An organization changes a ticket's plan only while it isn't running.
  return ticket.scope === "rem" || (!ticket.running && !ticket.paused);
}

// Tickets this one could wait for: open or done ones with the same owner, not itself and
// not already listed. Rem's wait for Rem's; an organization's for its own.
export function waitChoices(board, ticket) {
  const listed = new Set((ticket?.waits_for || []).map(item => item.ticket_id));
  return (board?.tickets || [])
    .filter(other => other.ticket_id !== ticket.ticket_id && !listed.has(other.ticket_id)
      && other.scope === ticket.scope && (other.organization_id || "") === (ticket.organization_id || "")
      && other.status !== "cancelled" && other.key)
    .sort((a, b) => Number(a.key.split("-").at(-1)) - Number(b.key.split("-").at(-1)));
}

// The edit that replaces what a ticket waits for.
export const waitsChange = (ticket, ids) => ({ action: "edit", ticket_id: ticket.ticket_id, scope: ticket.scope, organization_id: ticket.organization_id || undefined, expected_revision: ticket.revision, waits_for: ids });

// Rem's own tickets change their plan (title, details, priority, kind, Done when and project)
// while nothing runs on them, proposals included. An organization's sheet edits its tickets.
export const canEditTicket = ticket => ticket?.scope === "rem" && !ticket.running && !ticket.paused && ["backlog", "todo", "blocked"].includes(ticket.status);

// The kinds of work a Rem ticket can be, in the phone's words. An errand keeps its kind.
const TICKET_KINDS = Object.freeze([["develop", "Build"], ["research", "Research"], ["maintain", "Maintain"]]);
const KIND_LABELS = { ...Object.fromEntries(TICKET_KINDS), errand: "Errand" };
export const kindLabel = kind => KIND_LABELS[kind] || (kind ? kind[0].toUpperCase() + kind.slice(1) : "Build");
export const kindChoices = kind => TICKET_KINDS.some(([value]) => value === kind) || !kind ? TICKET_KINDS : [...TICKET_KINDS, [kind, kindLabel(kind)]];

// Done when as the backend keeps it: one check per line, blank lines dropped.
export const doneWhenText = text => String(text || "").split("\n").map(line => line.trim()).filter(Boolean).join("\n");

// The board sends a description's first 4,000 characters; one that long may be cut, so the
// edit form leaves it as it is rather than saving the cut copy over the whole text.
const DESCRIPTION_SHOWN = 4000;
export const descriptionCut = ticket => String(ticket?.description || "").length >= DESCRIPTION_SHOWN;

// The fields the edit form starts from.
export const ticketDraft = ticket => ({ title: ticket.title || "", description: ticket.description || "", priority: ticket.priority || "medium",
  type: ticket.type || "develop", done_when: ticket.done_when || "" });

const EDIT_FIELDS = Object.freeze({ title: value => value.trim(), description: value => value.trim(), priority: value => value, type: value => value, done_when: doneWhenText });

// The edit request for what changed between `start` (ticketDraft of the ticket at `revision`)
// and `draft`, or null when nothing did. Only the changed fields are sent.
export function ticketEdit(ticket, start, draft, revision = ticket.revision) {
  const changes = Object.fromEntries(Object.entries(EDIT_FIELDS)
    .map(([field, clean]) => [field, clean(String(draft[field] ?? "")), clean(String(start[field] ?? ""))])
    .filter(([field, now, before]) => now !== before && !(field === "description" && descriptionCut(start)))
    .map(([field, now]) => [field, now]));
  if (!Object.keys(changes).length) return null;
  return { action: "edit", ticket_id: ticket.ticket_id, scope: ticket.scope, expected_revision: revision, ...changes };
}

// A draft moved onto a newer copy of its ticket: fields the user hasn't touched follow the
// ticket, and what they typed stays.
export const rebaseDraft = (start, draft, newer) => Object.fromEntries(Object.keys(EDIT_FIELDS)
  .map(field => [field, draft[field] === start[field] ? newer[field] : draft[field]]));

const EDIT_LABELS = Object.freeze({ title: "title", description: "description", priority: "priority", type: "kind", done_when: "Done when" });

// Fields the user changed that the newer copy changed too, to something else. Moving the
// form onto that copy would save over the other change without a word.
export const draftConflicts = (start, draft, newer) => Object.entries(EDIT_FIELDS)
  .filter(([field, clean]) => {
    const [before, mine, theirs] = [start, draft, newer].map(values => clean(String(values[field] ?? "")));
    return mine !== before && theirs !== before && mine !== theirs;
  })
  .map(([field]) => field);

export const draftConflictNotice = fields => `The ${fields.map(field => EDIT_LABELS[field]).join(" and ")} changed somewhere else while you were editing, so Save would undo that. Copy what you typed, then Cancel to see the new version.`;
