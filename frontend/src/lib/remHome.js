// Presentation helpers for the Rem home: one conversation plus Rem's task list.
// The backend owns task authority, scheduling and completion; these helpers only
// describe server state in plain words and shape editor drafts into requests.

export const TASK_TYPES = Object.freeze({
  research: { label: "Research", blurb: "Reads and searches the web, then saves a report." },
  maintain: { label: "Maintain", blurb: "Keeps a project working. Can edit that project." },
  develop: { label: "Develop", blurb: "Builds something new in a project." },
  operations: { label: "Errand", blurb: "Does one thing in Raticode for you." },
  reminder: { label: "Reminder", blurb: "Reminds you at the right time." },
});
// The three types offered when people create a task; Rem may add the others.
export const CREATABLE_TYPES = ["research", "maintain", "develop"];

export const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const WEEKDAY_SHORT = { monday: "Mon", tuesday: "Tue", wednesday: "Wed", thursday: "Thu", friday: "Fri", saturday: "Sat", sunday: "Sun" };
const WEEKDAY_LONG = { monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday", friday: "Friday", saturday: "Saturday", sunday: "Sunday" };
export const weekdayShort = day => WEEKDAY_SHORT[day] || day;

// Run states from the backend contract, phrased the way Rem would say them.
export const RUN_STATES = Object.freeze({
  queued: { label: "Waiting to start", tone: "neutral", active: true, spinning: false },
  executing: { label: "Working", tone: "active", active: true, spinning: true },
  verifying: { label: "Checking the work", tone: "active", active: true, spinning: true },
  repairing: { label: "Fixing what the check found", tone: "active", active: true, spinning: true },
  cancelling: { label: "Stopping", tone: "neutral", active: true, spinning: true },
  blocked: { label: "Needs you", tone: "attention", active: false, spinning: false },
  outcome_unknown: { label: "Result unclear", tone: "attention", active: false, spinning: false },
  failed: { label: "Didn't finish", tone: "danger", active: false, spinning: false },
  cancelled: { label: "Stopped", tone: "neutral", active: false, spinning: false },
  succeeded: { label: "Done", tone: "success", active: false, spinning: false },
});
export const runState = (state, needsUser) => state === "blocked" && needsUser === false
  ? { ...RUN_STATES.blocked, label: "Couldn't finish", tone: "danger" }
  : RUN_STATES[state] || { label: "Status unavailable", tone: "neutral", active: false, spinning: false };

// Queued work says why it is waiting; running work names its current phase.
const activeLabel = run => (run?.state === "queued" && run.waiting_reason) || run?.phase_label || "";

// One task card state, derived from the task and its runs. Task state and run
// state stay separate on the server; the card only needs a single summary.
export function taskActivity(task, now = new Date()) {
  const run = task?.active_run;
  if (run && runState(run.state).active) {
    const info = runState(run.state);
    return { kind: run.state === "queued" ? "queued" : "running", label: activeLabel(run) || info.label, spinning: info.spinning, tone: info.tone };
  }
  const last = task?.last_run;
  if (last && ["blocked", "outcome_unknown", "failed"].includes(last.state) && !last.acknowledged) {
    const info = runState(last.state, last.needs_user);
    return { kind: "attention", label: last.summary_short || info.label, spinning: false, tone: info.tone };
  }
  if (task?.state === "paused") return { kind: "paused", label: "Paused", spinning: false, tone: "neutral" };
  if (task?.next_occurrence_at) return { kind: "scheduled", label: `Next ${formatWhen(task.next_occurrence_at, now)}`, spinning: false, tone: "neutral" };
  if (last?.state === "succeeded") return { kind: "done", label: `Done ${formatWhen(last.finished_at, now, { past: true })}`, spinning: false, tone: "success" };
  if (last) {
    const info = runState(last.state, last.needs_user);
    return { kind: "done", label: info.label, spinning: false, tone: info.tone };
  }
  return { kind: "idle", label: "Not scheduled", spinning: false, tone: "neutral" };
}

const GROUPS = [
  { id: "now", label: "Working on", kinds: ["running", "queued"] },
  { id: "attention", label: "Needs you", kinds: ["attention"] },
  { id: "scheduled", label: "Coming up", kinds: ["scheduled", "idle"] },
  { id: "paused", label: "Paused", kinds: ["paused"] },
  { id: "done", label: "Done", kinds: ["done"] },
];
export function groupTasks(tasks = [], now = new Date()) {
  const visible = tasks.filter(task => !task.deleted_at);
  return GROUPS.map(group => {
    const items = visible
      .map(task => ({ task, activity: taskActivity(task, now) }))
      .filter(item => group.kinds.includes(item.activity.kind))
      .sort((a, b) => sortKey(a, group.id).localeCompare(sortKey(b, group.id)));
    return { ...group, label: group.id === "attention" && items.every(item => item.task.last_run?.needs_user === false) ? "Updates" : group.label, items };
  }).filter(group => group.items.length);
}
function sortKey({ task }, group) {
  if (group === "scheduled") return task.next_occurrence_at || "9999";
  if (group === "done") return invertIso(task.last_run?.finished_at);
  return invertIso(task.active_run?.started_at || task.last_run?.finished_at || task.updated_at);
}
// Newest first while comparing strings.
const invertIso = iso => (iso ? String(9e15 - Date.parse(iso)).padStart(16, "0") : "9");

// Header line: what Rem is up to, in one short phrase.
export function statusLine(tasks = [], now = new Date()) {
  const kinds = tasks.filter(task => !task.deleted_at).map(task => taskActivity(task, now).kind);
  const running = kinds.filter(kind => kind === "running" || kind === "queued").length;
  const attentionTasks = tasks.filter(task => !task.deleted_at && taskActivity(task, now).kind === "attention");
  const attention = attentionTasks.filter(task => task.last_run?.needs_user !== false).length;
  if (attention) return `${plural(attention, "thing needs", "things need")} you`;
  if (running) return `Working on ${plural(running, "thing", "things")}`;
  if (kinds.some(kind => kind === "scheduled")) return "All caught up";
  return "Here when you need me";
}
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

export function formatClock(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || ""));
  if (!match) return "";
  const hour = Number(match[1]);
  const minute = match[2];
  return `${hour % 12 || 12}${minute === "00" ? "" : `:${minute}`} ${hour < 12 ? "AM" : "PM"}`;
}

export function describeSchedule(schedule) {
  if (!schedule || schedule.kind === "once" || schedule.kind === "now") {
    if (schedule?.at) return `Once, ${formatWhen(schedule.at)}`;
    return "Once, right away";
  }
  const at = formatClock(schedule.local_time);
  const time = at ? ` at ${at}` : "";
  if (schedule.kind === "daily") return `Every day${time}`;
  if (schedule.kind === "weekly") {
    const days = WEEKDAYS.filter(day => (schedule.weekdays || []).includes(day));
    if (!days.length) return `Weekly${time}`;
    if (days.length === 7) return `Every day${time}`;
    if (days.length === 5 && !days.includes("saturday") && !days.includes("sunday")) return `Weekdays${time}`;
    if (days.length === 2 && days.includes("saturday") && days.includes("sunday")) return `Weekends${time}`;
    if (days.length === 1) return `Every ${WEEKDAY_LONG[days[0]]}${time}`;
    return `${listJoin(days.map(day => WEEKDAY_SHORT[day]))}${time}`;
  }
  if (schedule.kind === "interval") {
    const minutes = Number(schedule.every_minutes) || 0;
    if (minutes && minutes % 1440 === 0) return minutes === 1440 ? "Every day" : `Every ${minutes / 1440} days`;
    if (minutes && minutes % 60 === 0) return minutes === 60 ? "Every hour" : `Every ${minutes / 60} hours`;
    return minutes ? `Every ${minutes} minutes` : "On an interval";
  }
  if (schedule.kind === "cron") return schedule.cron ? `Custom (${schedule.cron})` : "Custom schedule";
  return "Custom schedule";
}
const listJoin = items => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`);

function startOfDay(date) { const copy = new Date(date); copy.setHours(0, 0, 0, 0); return copy; }
const dayDiff = (date, now) => Math.round((startOfDay(date) - startOfDay(now)) / 86400000);
const timeOf = date => formatClock(`${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`);

// Short, human time for task cards: "in 20 min", "tomorrow 9 AM", "Mon 9 AM".
export function formatWhen(iso, now = new Date(), { past = false } = {}) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return "";
  const minutes = Math.round((date - now) / 60000);
  if (!past && minutes >= 0 && minutes < 60) return minutes <= 1 ? "in a minute" : `in ${minutes} min`;
  if (past && minutes <= 0 && minutes > -60) return minutes >= -1 ? "just now" : `${-minutes} min ago`;
  const days = dayDiff(date, now);
  if (days === 0) return `today ${timeOf(date)}`;
  if (days === 1) return `tomorrow ${timeOf(date)}`;
  if (days === -1) return `yesterday ${timeOf(date)}`;
  if (days > 1 && days < 7) return `${WEEKDAY_SHORT[WEEKDAYS[(date.getDay() + 6) % 7]]} ${timeOf(date)}`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

export function dayLabel(iso, now = new Date()) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const days = dayDiff(date, now);
  if (days === 0) return "Today";
  if (days === -1) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

export function messageTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : timeOf(date);
}

// Task updates are new messages; acknowledgements remain in their original place.
export function conversationMessages(messages = []) {
  return messages;
}

export function taskMessages(messages = [], taskId) {
  return messages.filter(message => message.work?.task_id === taskId);
}

export function taskRecap(text = "") {
  if (text.length <= 320) return text;
  const short = text.slice(0, 317);
  const sentence = [...short.matchAll(/[.!?](?=\s|$)/g)].at(-1)?.index;
  if (sentence > 40) return short.slice(0, sentence + 1);
  const space = short.lastIndexOf(" ");
  return `${short.slice(0, space > 0 ? space : short.length)}…`;
}

// Messages grouped under day separators, in sequence order.
export function messagesByDay(messages = [], now = new Date()) {
  const groups = [];
  for (const message of messages) {
    const label = dayLabel(message.created_at, now) || "Earlier";
    const current = groups.at(-1);
    if (current?.label === label) current.messages.push(message);
    else groups.push({ label, messages: [message] });
  }
  return groups;
}

// The live state of work a message refers to, preferring current task data
// over the snapshot stored with the message.
export function workStatus(work, tasks = []) {
  if (!work) return null;
  const task = tasks.find(item => item.id === work.task_id);
  const run = task?.active_run && task.active_run.id === work.run_id ? task.active_run
    : task?.last_run && task.last_run.id === work.run_id ? task.last_run
      : (task?.recent_runs || []).find(item => item.id === work.run_id);
  const state = run?.state || work.state;
  return {
    task,
    run,
    state,
    title: task?.title || work.title || "Task",
    type: task?.type || work.type,
    threadId: run?.thread_id || work.thread_id,
    verificationThreadId: run?.verification_thread_id || work.verification_thread_id,
    criteria: run?.criteria || work.criteria || null,
    deleted: Boolean(work.task_id) && !task && Boolean(tasks.length),
    ...runState(state, run?.needs_user ?? work.needs_user),
    label: (runState(state).active && activeLabel(run)) || runState(state, run?.needs_user ?? work.needs_user).label,
  };
}

// Several messages can refer to one run: its start, an acknowledgement and its
// result. Only the newest shows live status so the same state isn't repeated.
export function latestWorkMessageIds(messages = []) {
  const latest = new Map();
  for (const message of messages) if (message.work?.run_id) latest.set(message.work.run_id, message.id);
  return new Set(latest.values());
}

export function criteriaSummary(criteria) {
  if (!Array.isArray(criteria) || !criteria.length) return "";
  const passed = criteria.filter(item => item.result === "pass").length;
  return `${passed} of ${criteria.length} checks passed`;
}

// Editor drafts keep raw strings; they only become a request on save.
export function newTaskDraft({ type = "research", project_dir = "", timezone = localTimezone() } = {}) {
  return { title: "", type, prompt: "", project_dir, schedule: { kind: "once", local_time: "09:00", weekdays: ["monday"], cron: "", timezone }, criteria: [] };
}
export function taskToDraft(task) {
  const schedule = task.schedule || { kind: "once" };
  return {
    title: task.title || "",
    type: task.type || "research",
    prompt: task.prompt || "",
    project_dir: task.project_dir || "",
    schedule: { local_time: "09:00", weekdays: ["monday"], cron: "", timezone: localTimezone(), ...schedule },
    criteria: (task.acceptance_criteria || []).map(item => ({ id: item.id, description: item.description, authored_by: item.authored_by })),
  };
}

export function scheduleIsEditable(schedule) { return ["once", "now", "daily", "weekly", "cron"].includes(schedule?.kind || "once"); }

export function validateDraft(draft) {
  const errors = {};
  if (!draft.prompt.trim()) errors.prompt = "Tell Rem what to do.";
  if (draft.type === "maintain" && !draft.project_dir) errors.project_dir = `${TASK_TYPES[draft.type].label} tasks need a project.`;
  const schedule = draft.schedule;
  if ((schedule.kind === "daily" || schedule.kind === "weekly") && !/^([01]?\d|2[0-3]):[0-5]\d$/.test(schedule.local_time || "")) errors.schedule = "Pick a time.";
  if (schedule.kind === "weekly" && !(schedule.weekdays || []).length) errors.schedule = "Pick at least one day.";
  if (schedule.kind === "cron" && String(schedule.cron || "").trim().split(/\s+/).length !== 5) errors.schedule = "Use five cron fields, like 0 9 * * 1.";
  return errors;
}

export function draftTitle(draft) {
  const explicit = draft.title.trim();
  if (explicit) return explicit;
  const firstLine = draft.prompt.trim().split(/\n/)[0].trim().replace(/[.!?]+$/, "");
  return firstLine.length > 60 ? `${firstLine.slice(0, 57).trimEnd()}…` : firstLine;
}

function scheduleRequest(schedule) {
  const { kind, timezone } = schedule;
  if (kind === "once" || kind === "now") return schedule.at ? { kind: "once", at: schedule.at, timezone } : { kind: "once", timezone };
  if (kind === "daily") return { kind, local_time: schedule.local_time, timezone };
  if (kind === "weekly") return { kind, weekdays: WEEKDAYS.filter(day => schedule.weekdays.includes(day)), local_time: schedule.local_time, timezone };
  if (kind === "cron") return { kind, cron: schedule.cron.trim(), timezone };
  return schedule;
}

// Full recipe for POST, or only changed fields for PATCH. Criteria the person
// wrote are marked as theirs; untouched suggestions keep their origin.
export function draftToRequest(draft, task = null) {
  const request = {
    title: draftTitle(draft),
    type: draft.type,
    prompt: draft.prompt.trim(),
    project_dir: draft.project_dir || null,
    schedule: scheduleRequest(draft.schedule),
    acceptance_criteria: draft.criteria
      .filter(item => item.description.trim())
      .map(item => ({ ...(item.id ? { id: item.id } : {}), description: item.description.trim(), authored_by: item.authored_by || "user" })),
  };
  if (!task) return request;
  const before = draftToRequest(taskToDraft(task));
  return Object.fromEntries(Object.entries(request).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(before[key])));
}

// Changes that alter the work itself; Rem steers the running thread for these.
export function changesActiveWork(patch) { return ["type", "prompt", "project_dir", "acceptance_criteria"].some(key => key in patch); }

export function localTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

export function projectLabel(path) {
  if (!path) return "";
  return String(path).replace(/[/\\]+$/, "").split(/[/\\]/).at(-1) || path;
}
