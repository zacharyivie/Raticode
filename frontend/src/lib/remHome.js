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
  // Neither running nor finished: kept with its thread and worktree until it is resumed.
  paused: { label: "Paused", tone: "neutral", active: false, spinning: false },
  // Not run states: a task card for a task that waits for other tasks (task.dependency).
  waiting: { label: "Waiting for another task", tone: "neutral", active: false, spinning: false },
  held: { label: "On hold", tone: "attention", active: false, spinning: false },
});
export const runState = (state, needsUser, pausedBy) => state === "blocked" && needsUser === false
  ? { ...RUN_STATES.blocked, label: "Couldn't finish", tone: "danger" }
  : state === "paused" && pausedBy === "priority"
    ? { ...RUN_STATES.paused, label: PAUSED_FOR_YOU }
    : RUN_STATES[state] || { label: "Status unavailable", tone: "neutral", active: false, spinning: false };
// Self-work that made way for the user's own work; it resumes on its own afterwards.
export const PAUSED_FOR_YOU = "Paused for your work";

// Who paused a run: "user", "priority" (self-work making way for the user), or "" if it isn't.
export const pausedBy = run => run?.state !== "paused" ? "" : (run.paused_by || run.paused?.by) === "priority" ? "priority" : "user";

// What a task's controls can do with its current run: pause one that is going or waiting,
// resume one that is paused. Stopping stays separate.
export function runControls(task) {
  const active = task?.active_run;
  const last = task?.last_run;
  if (active && runState(active.state).active && active.state !== "cancelling") return { pause: active.id };
  if (last?.state === "paused") return { resume: last.id };
  return {};
}

// A worker that parked itself while it waits for something outside Raticode, such as a
// CI run (rem_home_sleep). It holds no worker and wakes in the same thread on its own.
export const runSleep = run => (run?.state === "queued" && run.sleep?.asleep ? run.sleep : null);
export function sleepLabel(sleep, now = new Date()) {
  const next = formatWhen(sleep.until, now);
  return `Sleeping · waiting for ${sleep.waiting_for || "an outside event"}${next ? `, next check ${next}` : ""}`;
}
// "Woke 2 times · 25 min of 12 h wait budget used", for the task sheet.
export function sleepBudget(sleep) {
  const span = minutes => (minutes >= 60 ? `${Math.round(minutes / 6) / 10} h` : `${Math.round(minutes)} min`);
  const wakes = Number(sleep.wakes) || 0;
  const used = (Number(sleep.slept_seconds) || 0) / 60;
  const budget = (Number(sleep.budget_hours) || 12) * 60;
  return `${wakes ? `Woke ${wakes} ${wakes === 1 ? "time" : "times"}` : "First sleep"} · ${span(used)} of ${span(budget)} wait budget used`;
}

// Queued work says why it is waiting; running work names its current phase.
const activeLabel = run => (run?.state === "queued" && run.waiting_reason) || run?.phase_label || "";

// One task card state, derived from the task and its runs. Task state and run
// state stay separate on the server; the card only needs a single summary.
export function taskActivity(task, now = new Date()) {
  const run = task?.active_run;
  if (run && runState(run.state).active) {
    const sleep = runSleep(run);
    if (sleep) return { kind: "queued", sleeping: true, label: sleepLabel(sleep, now), spinning: false, tone: "neutral" };
    const info = runState(run.state);
    return { kind: run.state === "queued" ? "queued" : "running", label: activeLabel(run) || info.label, spinning: info.spinning, tone: info.tone };
  }
  // A task that starts after other tasks: waiting, or held because one of them didn't succeed.
  const dependency = task?.dependency;
  if (dependency && task.state !== "paused") {
    if (dependency.state === "held") return { kind: "attention", label: dependency.reason || RUN_STATES.held.label, spinning: false, tone: "attention", held: true };
    return { kind: "waiting", label: dependency.state === "met" ? "Starting soon" : dependency.reason || RUN_STATES.waiting.label, spinning: false, tone: "neutral" };
  }
  const last = task?.last_run;
  if (last && ["blocked", "outcome_unknown", "failed"].includes(last.state) && !last.acknowledged) {
    const info = runState(last.state, last.needs_user);
    return { kind: "attention", label: plainMarkdown(last.summary_short) || info.label, spinning: false, tone: info.tone };
  }
  if (last?.state === "paused") {
    const by = pausedBy(last);
    return { kind: "paused", label: by === "priority" ? PAUSED_FOR_YOU : "Paused", spinning: false, tone: "neutral", pausedRun: true, pausedBy: by };
  }
  if (task?.state === "paused") return { kind: "paused", label: "Schedule paused", spinning: false, tone: "neutral" };
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
  { id: "scheduled", label: "Coming up", kinds: ["waiting", "scheduled", "idle"] },
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
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  const days = dayDiff(date, now);
  if (days === 0) return "Today";
  if (days === -1) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
}

// The stamp under each message: the time for today's messages, the day and time for
// older ones, and the full date for hover and screen readers. Null when the date is unusable.
export function messageStamp(iso, now = new Date()) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return null;
  const time = `${date.getHours() % 12 || 12}:${String(date.getMinutes()).padStart(2, "0")} ${date.getHours() < 12 ? "AM" : "PM"}`;
  const days = dayDiff(date, now);
  const year = date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {};
  const day = days === 0 ? "" : days === -1 ? "Yesterday" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...year });
  return {
    iso: date.toISOString(),
    label: day ? `${day}, ${time}` : time,
    full: `${date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}, ${time}`,
  };
}

// Task updates are new messages; acknowledgements remain in their original place.
export function conversationMessages(messages = []) {
  return messages;
}

export function taskMessages(messages = [], taskId) {
  return messages.filter(message => message.work?.task_id === taskId);
}

// Rem writes Markdown. Places that show its text as one plain line keep link
// labels and drop the markup, so a link never shows as "[label](</long/path>)".
// Each pattern caps how far one match may reach. Unbounded, a long reply full of
// unclosed "[" or "**" made every opener rescan to the end, freezing the window
// for seconds (CWE-1333). Real labels and bold runs are far shorter than the caps.
const MARKDOWN_LINK = /(!?)\[([^\]]{0,500})\]\((?:<[^>\n]{0,2000}>|[^()\s]{0,2000}(?:\([^()\s]{0,500}\)[^()\s]{0,500})*)(?:\s+"[^"\n]{0,500}")?\)/g;
export function plainMarkdown(text = "") {
  return String(text ?? "")
    .replace(MARKDOWN_LINK, (_, image, label) => label || (image ? "" : _))
    .replace(/<((?:https?|mailto):[^>\s]+)>/gi, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/(\*\*|__)(?=\S)([^\n]{0,500}?\S)\1/g, "$2")
    .replace(/(^|[^\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])/g, "$1$2")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "");
}

export function taskRecap(text = "") {
  text = plainMarkdown(text);
  if (text.length <= 320) return text;
  const short = text.slice(0, 317);
  const sentence = [...short.matchAll(/[.!?](?=\s|$)/g)].at(-1)?.index;
  if (sentence > 40) return short.slice(0, sentence + 1);
  const space = short.lastIndexOf(" ");
  return `${short.slice(0, space > 0 ? space : short.length)}…`;
}

// Messages grouped under day separators, in sequence order. A message without a usable
// date stays with the messages around it; only leading ones get an "Earlier" group.
export function messagesByDay(messages = [], now = new Date()) {
  const groups = [];
  for (const message of messages) {
    const label = dayLabel(message.created_at, now);
    const current = groups.at(-1);
    if (current && (!label || current.label === label)) current.messages.push(message);
    else groups.push({ key: message.id, label: label || "Earlier", messages: [message] });
  }
  return groups;
}

// The live state of work a message refers to, preferring current task data
// over the snapshot stored with the message.
// What a waiting task waits for, as the card and the task sheet list it. Each entry is a
// named task ("task", the default), a run that was going when the user asked ("run"), a
// clock time ("time"), nothing else running ("idle") or the self-work window ending
// ("self_work"). Only entries with a task can be opened, and only named tasks retried.
const CONDITION_MET = Object.freeze({ run: "Finished", time: "Time reached", idle: "Nothing running", self_work: "Ended" });
export const prerequisiteKind = item => item?.kind || "task";
export const canRetryPrerequisite = item => Boolean(item?.retryable && item.task_id) && prerequisiteKind(item) === "task";
export function prerequisites(task) {
  return (task?.dependency?.prerequisites || []).map((item, index) => {
    const kind = prerequisiteKind(item);
    const named = kind === "task";
    return {
      ...item,
      kind,
      key: `${kind}-${item.task_id || item.run_id || item.title || ""}-${index}`,
      opens: Boolean(item.task_id) && (named || kind === "run"),
      retryable: canRetryPrerequisite(item),
      label: item.state === "met" ? (named ? "Done" : CONDITION_MET[kind] || "Done")
        : item.state === "held" ? (item.run_state ? runState(item.run_state).label : item.reason || "On hold")
          : kind === "run" ? "Running"
            : !named ? "Waiting"
              : item.run_state && RUN_STATES[item.run_state]?.active ? "Running" : "Not started",
      tone: item.state === "met" ? "success" : item.state === "held" ? "attention" : "neutral",
    };
  });
}

// The tasks a new or waiting task may wait for: one-time or repeating, never itself or deleted.
export function afterChoices(tasks = [], task = null) {
  return tasks.filter(item => !item.deleted_at && item.id !== task?.id).map(item => ({ id: item.id, title: item.title }));
}

// Whether the "Start after" field can change: new one-time tasks, and waiting tasks before they start.
export function afterEditable(task, draft) {
  if ((draft?.schedule?.kind || "once") !== "once" && (draft?.schedule?.kind || "once") !== "now") return false;
  if (!task) return true;
  return !task.after_started && !task.last_run && !task.active_run;
}

export function workStatus(work, tasks = []) {
  if (!work) return null;
  const task = tasks.find(item => item.id === work.task_id);
  // A card without a run (a task on hold) follows the task: waiting, held, then its first run.
  const run = !work.run_id ? task?.active_run || task?.last_run || undefined
    : task?.active_run && task.active_run.id === work.run_id ? task.active_run
      : task?.last_run && task.last_run.id === work.run_id ? task.last_run
        : (task?.recent_runs || []).find(item => item.id === work.run_id);
  // The card's own state is the run's as the desktop last served the chat; it covers
  // a run the task list no longer has, such as one of a deleted task.
  const state = run?.state || (!work.run_id && task?.dependency ? (task.dependency.state === "held" ? "held" : "waiting") : work.state);
  return {
    task,
    run,
    state,
    step: run ? run.step : work.step,
    title: task?.title || work.title || "Task",
    type: task?.type || work.type,
    threadId: run?.thread_id || work.thread_id,
    verificationThreadId: run?.verification_thread_id || work.verification_thread_id,
    criteria: run?.criteria || work.criteria || null,
    deleted: Boolean(work.task_id) && !task && Boolean(tasks.length),
    // Paused by you, or by self-work making way for your own work.
    pausedBy: state === "paused" ? (run ? pausedBy(run) : work.paused_by === "priority" ? "priority" : "user") : "",
    ...runState(state, run?.needs_user ?? work.needs_user, run?.paused_by || run?.paused?.by || work.paused_by),
    label: (runSleep(run) && sleepLabel(runSleep(run))) || (runState(state).active && activeLabel(run)) || runState(state, run?.needs_user ?? work.needs_user, run?.paused_by || run?.paused?.by || work.paused_by).label,
    sleeping: Boolean(runSleep(run)),
  };
}

// Chat rows: cards one self-work pass started together (same self_work.batch, one after
// another) form one row, so a pass that starts several jobs is one message, not several.
// Everything else is its own row.
export function chatRows(messages = []) {
  const rows = [];
  for (const message of messages) {
    const batch = message.role === "rem" && message.kind !== "task_update" && message.work ? message.self_work?.batch || "" : "";
    const last = rows.at(-1);
    if (batch && last?.batch === batch) last.messages.push(message);
    else rows.push(batch ? { key: message.id, batch, messages: [message] } : { key: message.id, message });
  }
  return rows.map(row => (row.batch && row.messages.length === 1 ? { key: row.key, message: row.messages[0] } : row));
}

// The report Rem posts when a self-work period ends: one row per thing it worked on, with
// how that went. A row opens its task like a task card does, or its thread when the task
// is gone. Messages without a report, from older desktops, have no rows.
const REPORT_OUTCOMES = Object.freeze({
  finished: ["Finished", "success"], running: ["Still running", "active"], paused: ["Paused", "neutral"],
  blocked: ["Blocked", "attention"], failed: ["Failed", "danger"], stopped: ["Stopped", "neutral"],
});
export function selfWorkReportItems(message, tasks = []) {
  if (message?.kind !== "self_work_report" || !Array.isArray(message.self_work_report?.items)) return [];
  return message.self_work_report.items.filter(item => item && typeof item === "object").map((item, index) => {
    const [outcome, tone] = REPORT_OUTCOMES[item.outcome] || [item.outcome ? String(item.outcome) : "", "neutral"];
    const known = Boolean(item.task_id) && (!tasks.length || tasks.some(task => task.id === item.task_id && !task.deleted_at));
    return {
      key: `${item.run_id || item.task_id || "item"}-${index}`,
      title: plainMarkdown(item.title) || "Task",
      outcome,
      tone,
      taskId: known ? item.task_id : "",
      threadId: known ? "" : item.thread_id || "",
    };
  });
}

const SELF_WORK_KINDS = [["ticket", "ticket", "tickets"], ["triage", "backlog ranking", "backlog rankings"], ["research", "research run", "research runs"]];

// The line above a self-work batch: "Started 3 things while you were away: 2 tickets and 1 research run."
export function selfWorkBatchLine(messages = []) {
  const counts = new Map();
  for (const message of messages) {
    const kind = message.self_work?.kind || message.work?.self_work || "ticket";
    counts.set(kind, (counts.get(kind) || 0) + 1);
  }
  const parts = SELF_WORK_KINDS.filter(([kind]) => counts.get(kind)).map(([kind, one, many]) => `${counts.get(kind)} ${counts.get(kind) === 1 ? one : many}`);
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0] || `${messages.length} tasks`;
  return `Started ${messages.length} things while you were away: ${list}.`;
}

// Several messages can refer to one run: its start, an acknowledgement and its
// result. Only the newest shows live status so the same state isn't repeated.
export function latestWorkMessageIds(messages = []) {
  const latest = new Map();
  for (const message of messages) {
    const work = message.work;
    if (work?.run_id) {
      latest.set(work.run_id, message.id);
      // A card from before the task had a run (on hold) gives way to the run's own card.
      latest.delete(`task:${work.task_id}`);
    } else if (work?.task_id) latest.set(`task:${work.task_id}`, message.id);
  }
  return new Set(latest.values());
}

// Unchecked criteria are not failures, so "0 of 5 passed" would mislead when
// the review never ran. Counts are split by outcome and empty parts omitted.
export function criteriaSummary(criteria) {
  const { failed, unresolved, passed, unchecked } = checkGroups(criteria);
  const total = failed.length + unresolved.length + passed.length + unchecked.length;
  if (!total) return "";
  if (unchecked.length === total) return total === 1 ? "Not checked yet" : `${total} checks not run yet`;
  if (passed.length === total) return `${total} of ${total} checks passed`;
  return [
    `${passed.length} of ${total} passed`,
    failed.length ? `${failed.length} failed` : "",
    unresolved.length ? `${unresolved.length} unresolved` : "",
    unchecked.length ? `${unchecked.length} not checked` : "",
  ].filter(Boolean).join(", ");
}

// A run's checks by outcome, for the task pane. The review answers pass, fail or
// unknown with its reason; an unknown check with a reason was looked at but not
// confirmed (unresolved), one without a reason hasn't been checked. A check whose
// description is missing, as from an older backend, is numbered instead.
export function checkGroups(criteria) {
  const items = (Array.isArray(criteria) ? criteria : [])
    .map((item, index) => [item, index])
    .filter(([item]) => item && typeof item === "object")
    .map(([item, index]) => {
      const reason = typeof item.evidence === "string" ? plainMarkdown(item.evidence).trim() : "";
      const title = typeof item.description === "string" ? plainMarkdown(item.description).trim() : "";
      const outcome = item.result === "pass" ? "passed" : item.result === "fail" ? "failed" : reason ? "unresolved" : "unchecked";
      return { key: item.id ? String(item.id) : `check-${index}`, title: title || `Check ${index + 1}`, reason, outcome };
    });
  const by = outcome => items.filter(item => item.outcome === outcome);
  const groups = { failed: by("failed"), unresolved: by("unresolved"), passed: by("passed"), unchecked: by("unchecked") };
  return { ...groups, reviewed: items.length > groups.unchecked.length };
}

// Desktop notifications for runs that finished since the previous poll. Only a
// run seen active before can notify, so the first load and old results stay quiet.
export function finishedRunNotifications(previous, tasks) {
  if (!Array.isArray(previous) || !Array.isArray(tasks)) return [];
  const active = new Set();
  for (const task of previous) {
    for (const run of [task.active_run, task.last_run]) if (run?.id && runState(run.state).active) active.add(run.id);
  }
  const notices = [];
  for (const task of tasks) {
    const run = task.last_run;
    if (task.deleted_at || !run?.id || !active.has(run.id) || runState(run.state).active || task.active_run?.id === run.id) continue;
    if (run.state === "cancelled") continue;
    const info = runState(run.state, run.needs_user);
    const summary = String(run.state === "succeeded" ? taskRecap(run.summary_short || "") : plainMarkdown(run.summary_short)).trim();
    notices.push({ tag: `rem-run-${run.id}`, taskId: task.id, title: `${info.label}: ${task.title}`, body: summary.length > 180 ? `${summary.slice(0, 179)}…` : summary });
  }
  return notices;
}

// Editor drafts keep raw strings; they only become a request on save.
export function newTaskDraft({ type = "research", project_dir = "", timezone = localTimezone() } = {}) {
  return { title: "", type, prompt: "", project_dir, schedule: { kind: "once", local_time: "09:00", weekdays: ["monday"], cron: "", timezone }, criteria: [], after: [] };
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
    after: [...(task.after || [])],
    // Set by Rem from the user's words; the form shows them but doesn't edit them.
    after_running: task.after_running || null,
    after_self_work: Boolean(task.after_self_work),
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
  // Only one-time tasks can wait for others; a new task leaves the field out when empty.
  const after = request.schedule.kind === "once" ? [...(draft.after || [])] : [];
  if (task || after.length) request.after = after;
  if (!task) return request;
  const before = { ...draftToRequest(taskToDraft(task)), after: [...(task.after || [])] };
  return Object.fromEntries(Object.entries(request).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(before[key])));
}

// Changes that alter the work itself; Rem steers the running thread for these.
// The draft's "When" summary: a task that waits starts once all it waits for is met, the
// tasks above succeeding and any condition Rem set from the user's words.
export function startsWhen(draft, isNew) {
  const once = draft.schedule.kind === "once" || draft.schedule.kind === "now";
  const waits = once ? [
    (draft.after || []).length ? "every task above finishes successfully" : "",
    draft.after_running === "snapshot" ? "the tasks that were running when you asked have ended" : "",
    draft.after_running === "idle" ? "nothing else is running" : "",
    draft.after_self_work ? "the self-work window ends" : "",
  ].filter(Boolean) : [];
  if (waits.length) return `Starts by itself once ${listJoin(waits)}.`;
  return isNew ? "Starts as soon as you save." : "Runs once. Run it again any time.";
}

export function changesActiveWork(patch) { return ["type", "prompt", "project_dir", "acceptance_criteria"].some(key => key in patch); }

export function localTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

export function projectLabel(path) {
  if (!path) return "";
  return String(path).replace(/[/\\]+$/, "").split(/[/\\]/).at(-1) || path;
}
