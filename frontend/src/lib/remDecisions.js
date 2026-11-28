// Rem's decisions: questions Rem or a worker brought the user, each with one recommended
// answer and checked evidence, and the answers in force per project and topic. The backend
// (rem_decisions.py) owns the list and its rules; these helpers only read it and shape the
// user's changes. The store (remHomeStore.js) polls GET /api/rem/decisions and posts them.

export const NO_DECISIONS = Object.freeze({ revision: 0, items: Object.freeze([]), waiting: 0, queued: 0, provisional: 0, confirmed: 0 });
export const DECISION_ANSWER_MAX = 1000;
export const OPEN_STATUSES = Object.freeze(["pending", "provisional"]);
export const CONFLICT_MESSAGE = "This changed elsewhere, so nothing was saved. The list now shows the latest; check it and try again.";
export const GONE_MESSAGE = "This decision was removed elsewhere, so nothing was saved.";

const EVIDENCE_LABELS = Object.freeze({ file: "file", quote: "quote", command: "ran", commit: "commit", doc: "doc" });

// A poll that sent the same list keeps the old object, so nothing re-renders. The poll's
// own fields (version, unchanged) aren't part of the list.
export function sameDecisions(next, current) {
  if (!next || !Array.isArray(next.items)) return current || NO_DECISIONS;
  const view = { ...next };
  delete view.version;
  delete view.unchanged;
  return current && JSON.stringify(view) === JSON.stringify(current) ? current : view;
}

export const isOpen = item => OPEN_STATUSES.includes(item?.status);

const trimRoot = path => String(path || "").replace(/(?<=.)[\\/]+$/, "");

// Whether a decision belongs with the project the pane shows. Decisions about the user
// (no project) apply everywhere; a worktree inside the project counts as the project.
export function inProject(item, root) {
  if (!root) return true;
  if (!item?.project) return true;
  const project = trimRoot(item.project);
  const here = trimRoot(root);
  return here === project || here.startsWith(`${project}/`) || here.startsWith(`${project}\\`);
}

// A design decision Raticode keeps as a Markdown file in the project's repository.
export const isRecord = item => item?.remember_as === "record";

// The side pane's parts: waiting for the user; in force, split into how Rem works here
// (facts and memories) and design decisions (records); and closed history. `decided` is
// every answer in force, both kinds together.
export function groupDecisions(view, { project = "", all = false } = {}) {
  const items = (view?.items || []).filter(item => all || inProject(item, project));
  const decided = items.filter(item => item.status === "confirmed");
  return {
    waiting: items.filter(isOpen),
    decided,
    rules: decided.filter(item => !isRecord(item)),
    records: decided.filter(isRecord),
    history: items.filter(item => item.status === "superseded" || item.status === "rejected"),
  };
}

// How many decisions need the user: waiting ones and Rem's own auto-mode answers.
export function waitingCount(view, { project = "", all = true } = {}) {
  return (view?.items || []).filter(item => isOpen(item) && (all || inProject(item, project))).length;
}

// Questions past a project's three waiting cards. They have no card yet; each moves up,
// oldest first, when one of the waiting ones is answered, turned down or forgotten.
export function queuedCount(view, { project = "", all = false } = {}) {
  return (view?.items || []).filter(item => item?.status === "queued" && (all || inProject(item, project))).length;
}

// The line under Waiting for you: "2 more queued", or "" when none are.
export const queuedLine = count => count > 0 ? `${count} more queued` : "";

// Plan with me: the plans still asking the user, each with its questions waiting now, for
// the project the pane shows. A plan with nothing waiting and nothing queued isn't listed;
// its answered questions are ordinary decisions.
export function planGroups(view, { project = "", all = false } = {}) {
  const items = (view?.items || []).filter(item => all || inProject(item, project));
  return (view?.plans || [])
    .filter(plan => plan?.id && (plan.status || "asking") === "asking" && (all || inProject(plan, project)))
    .map(plan => ({ plan, waiting: items.filter(item => item.plan === plan.id && isOpen(item)) }))
    .filter(group => group.waiting.length || group.plan.queued > 0);
}

// Waiting questions that belong to none of those plans, listed on their own as before.
export function looseWaiting(waiting, groups) {
  const grouped = new Set((groups || []).map(group => group.plan.id));
  return (waiting || []).filter(item => !grouped.has(item.plan));
}

// Under a plan's title: "2 of 5 answered".
export const planLine = plan => `${plan?.answered || 0} of ${plan?.total || 0} answered`;

// Under a plan's waiting questions: "2 more come up as you answer", or "".
export const planQueuedLine = plan => plan?.queued > 0 ? `${plan.queued} more come up as you answer` : "";

// Queued questions outside those plans, for the line under Waiting for you.
export const looseQueued = (count, groups) => Math.max(0, count - (groups || []).reduce((sum, group) => sum + (group.plan.queued || 0), 0));

// The folded heading's count: "2 waiting", or "" when nothing needs the user.
export function decisionsCount(view) {
  const count = waitingCount(view);
  return count ? `${count} waiting` : "";
}

// Up to `max` pieces of evidence as {kind, label, ref, excerpt}, plus how many more there are.
export function evidenceParts(evidence = [], max = 3) {
  const list = (Array.isArray(evidence) ? evidence : []).filter(item => item && item.ref);
  const parts = list.slice(0, max).map(item => ({ kind: item.kind, label: EVIDENCE_LABELS[item.kind] || item.kind || "", ref: String(item.ref), excerpt: item.excerpt || "" }));
  return { parts, more: Math.max(0, list.length - parts.length) };
}

// The answer to show: the one in force, else Rem's recommendation.
export const decisionAnswer = item => item?.answer || item?.recommended || "";

// Where a decision applies: its project's folder name, or "About you".
export function decisionPlace(item) {
  if (!item?.project) return "About you";
  return item.project_name || trimRoot(item.project).split(/[\\/]/).pop() || item.project;
}

// A record file Raticode read in rather than wrote, until the user decides on it in Raticode:
// "Read from the repository: added in 1a2b3c4d5e6f by Sam Lee". rem_decision_records.sync sets the source.
function readFromRepository(item) {
  const source = item?.source || {};
  if (source.via !== "repository" || (item.history || []).some(entry => entry?.actor === "user")) return "";
  const by = source.author ? ` by ${source.author}` : "";
  return `Read from the repository${source.commit ? `: added in ${source.commit}${by}` : ""}`;
}

// Who decided and on what day, e.g. "You decided on 2026-11-18."; "" when nobody has yet.
export function decidedLine(item) {
  const who = item?.source?.via === "carried over" ? "Raticode carried it over from what it already did"
    : item?.decided_by === "rem" ? "Rem decided (auto mode)"
      : item?.decided_by === "user" ? readFromRepository(item) || "You decided" : "";
  if (!who) return "";
  return `${who}${item.decided_at ? ` on ${String(item.decided_at).slice(0, 10)}` : ""}.`;
}

const HISTORY_ACTORS = Object.freeze({ user: "You", rem: "Rem", raticode: "Raticode" });
// rem_decisions._history and rem_decision_records.sync write these; anything else shows as written.
const HISTORY_ACTIONS = Object.freeze({
  requested: "Asked",
  queued: "Queued",
  provisional: "Decided for now (auto mode)",
  "moved up": "Moved up to waiting",
  "conflict found": "Found a conflict",
  added: "Added",
  confirmed: "Confirmed",
  changed: "Changed the answer",
  edited: "Edited",
  rejected: "Turned down",
  retired: "Retired",
  reopened: "Asked again",
  superseded: "Replaced",
  rechecked: "Checked again",
  "carried over": "Carried over",
  "kept the approved text": "Kept the approved text",
  read: "Read from the repository",
  "edited outside": "Edited outside Raticode",
});

const capitalized = text => text ? text[0].toUpperCase() + text.slice(1) : "";

// A decision's history, newest first, as {key, date, actor, action, note}: the day, who
// (You, Rem or Raticode), what they did, and the note when it says more than who did it.
export function historyEntries(item) {
  const list = Array.isArray(item?.history) ? item.history : [];
  return list.filter(entry => entry && entry.action).map((entry, index) => {
    const raw = String(entry.action);
    let action = HISTORY_ACTIONS[raw] || capitalized(raw);
    let note = String(entry.note || "").trim();
    // A user change made without a stated reason records only where it came from.
    if (entry.actor === "user" && note === "user") note = "";
    if (raw === "superseded" && /^D-\d+$/.test(note)) { action = `Replaced by ${note}`; note = ""; }
    return {
      key: `${index}-${entry.at || ""}`,
      date: String(entry.at || "").slice(0, 10),
      actor: HISTORY_ACTORS[entry.actor] || capitalized(String(entry.actor || "")),
      action,
      note,
    };
  }).reverse();
}

export const RESTORE_NOTE = "The file differs from the approved text; Raticode puts it back when the checkout is clean";

// What a record's file note says: a Rem run's change waiting to be put back, or why the
// file isn't written yet. null when there is nothing to say.
export function recordFileNote(item) {
  if (!isRecord(item)) return null;
  if (item.record_restore) return { kind: "restore", flag: "File differs", text: RESTORE_NOTE };
  if (item.record_error) return { kind: "missing", flag: "Not in the repo yet", text: `Not in the repo yet: ${item.record_error}` };
  return null;
}

// Raticode's own last run of a decided check command, e.g. "Raticode's last run of it passed on 2026-11-21."
export function checkLine(item) {
  const check = item?.last_check;
  if (!check?.ran_at) return "";
  const state = check.passed ? "passed" : `failed${Number.isInteger(check.exit_code) ? ` (exit ${check.exit_code})` : ""}`;
  return `Raticode's last run of it ${state} on ${String(check.ran_at).slice(0, 10)}.`;
}

const SCAN_WORDS = Object.freeze({
  pending: "waiting for you",
  queued: "queued behind those waiting for you",
  provisional: "decided by Rem for now (auto mode)",
  waiting: "already waiting for you",
  answered: "already decided",
  rechecked: "already decided",
  skipped: "already decided",
});

// One sentence for what a project scan did, from its outcomes ({topic, outcome}).
export function scanSummary(outcomes = []) {
  const counted = new Map();
  for (const entry of Array.isArray(outcomes) ? outcomes : []) {
    const words = SCAN_WORDS[entry?.outcome];
    if (words) counted.set(words, (counted.get(words) || 0) + 1);
  }
  if (!counted.size) return "Nothing new in this project's files.";
  const total = [...counted.values()].reduce((sum, n) => sum + n, 0);
  const parts = [...counted].map(([words, n]) => `${n} ${words}`);
  return `Found ${total} ${total === 1 ? "answer" : "answers"}: ${parts.join(", ")}.`;
}

// What a closed decision's history row says.
export function closedLabel(item) {
  if (item.status === "superseded") return item.superseded_by ? `Replaced by ${item.superseded_by}` : "Replaced";
  if (item.status === "rejected") return item.retired_at ? "Retired" : "Turned down";
  return "";
}

// A decision record whose file is in the repository. It is retired, not forgotten: the
// backend keeps it turned down and rewrites its file with status: rejected, since deleting
// it would let the file bring it back as a new decision (rem_decisions.written).
function isWritten(item) {
  return isRecord(item) && Boolean(item?.record_path);
}

// The words for removing a decision and the action it sends: Retire for a written record,
// Forget for the rest. Retiring commits the record's file on the landing branch, so it is
// its own action; the backend refuses "forget" for a written record.
export function removal(item) {
  return isWritten(item)
    ? { label: "Retire", action: "retire", ask: "Retire this decision?", detail: "Workers stop getting it. Its file stays in the repository: Raticode rewrites it with status: rejected and commits that on the landing branch." }
    : { label: "Forget", action: "forget", ask: "Forget this decision?", detail: "Rem may ask it again later." };
}

// Tidy what the user typed; null when there's nothing to save. A waiting request gets the
// user's own answer (change); one already decided gets its answer edited.
export function answerChange(draft, item) {
  const answer = String(draft || "").trim().slice(0, DECISION_ANSWER_MAX);
  if (!answer || !item?.id) return null;
  if (isOpen(item)) return { action: "change", id: item.id, answer };
  if (answer === (item.answer || "")) return null;
  return { action: "edit", id: item.id, answer };
}

// A decision record's written parts, in the order the detail and the form show them.
export const RECORD_SECTIONS = Object.freeze([
  Object.freeze({ key: "context", label: "Context", max: 1500 }),
  Object.freeze({ key: "options", label: "Options we didn't choose", max: 1500 }),
  Object.freeze({ key: "consequences", label: "Consequences", max: 1500 }),
  Object.freeze({ key: "check", label: "How we check it", max: 600 }),
]);
export const RECORD_LISTS = Object.freeze(["applies_to", "forbids", "signals"]);
const RECORD_LIST_MAX = 10;
const TEXT_LIMITS = Object.freeze({ answer: DECISION_ANSWER_MAX, question: 500, why: 300, note: 300 });

// The record's sections that have text, as {key, label, text}.
export function recordSections(item) {
  const record = item?.record || {};
  return RECORD_SECTIONS.map(({ key, label }) => ({ key, label, text: String(record[key] || "").trim() })).filter(part => part.text);
}

// What a path's tracked-file count says (rem_decision_records.count_note). `kind` is
// "applies" or "forbids"; an Applies to path that matches nothing and a Never touch path
// that matches files are warnings. "" when there is no count.
export function countNote(kind, count) {
  if (!Number.isInteger(count) || count < 0) return { text: "", warn: false };
  const files = count === 1 ? "1 file" : `${count} files`;
  if (kind === "forbids") return count ? { text: `matches ${files} that ${count === 1 ? "exists" : "exist"} now`, warn: true } : { text: "no files now", warn: false };
  return count ? { text: files, warn: false } : { text: "matches no files", warn: true };
}

const countOf = (counts, kind, glob) => counts?.[kind === "applies" ? "applies_to" : "forbids"]?.[glob];

// The scope chips under a record: the paths it is about, then the paths it rules out, each
// with how many tracked files it matches on the landing branch when the desktop counted.
export function scopeChips(item) {
  const record = item?.record || {};
  const counts = item?.path_counts || null;
  const list = value => (Array.isArray(value) ? value : []).map(String).filter(Boolean);
  const chip = (kind, glob, label) => {
    const note = countNote(kind, countOf(counts, kind, glob));
    return { kind, glob, label, count: note.text, warn: note.warn };
  };
  return [
    ...list(record.applies_to).map(glob => chip("applies", glob, glob)),
    ...list(record.forbids).map(glob => chip("forbids", glob, `Never: ${glob}`)),
  ];
}

// The form's warnings for typed paths, from a path_counts answer: one line per Applies to
// path that matches no files and per Never touch path that matches files. They never stop
// a save, since a path can be planned.
export function pathWarnings(counts) {
  if (!counts) return [];
  const lines = [];
  for (const [glob, count] of Object.entries(counts.applies_to || {})) {
    if (countNote("applies", count).warn) lines.push(`Applies to ${glob} matches no files on ${counts.branch || "the landing branch"}.`);
  }
  for (const [glob, count] of Object.entries(counts.forbids || {})) {
    if (countNote("forbids", count).warn) lines.push(`Never touch ${glob} ${countNote("forbids", count).text}.`);
  }
  return lines;
}

// Comma- or line-separated text as a list: trimmed, no empties or repeats, at most ten.
export function splitList(text) {
  const parts = String(text || "").split(/[\n,]+/).map(part => part.trim()).filter(Boolean);
  return [...new Set(parts)].slice(0, RECORD_LIST_MAX);
}

// The form's starting strings for a record: every field as plain text, lists comma-joined.
export function recordDraft(item = {}) {
  const record = item?.record || {};
  const draft = { question: item?.question || "", answer: decisionAnswer(item), why: item?.why || "", note: "" };
  for (const { key } of RECORD_SECTIONS) draft[key] = String(record[key] || "");
  for (const key of RECORD_LISTS) draft[key] = (Array.isArray(record[key]) ? record[key] : []).join(", ");
  return draft;
}

function tidyDraft(draft) {
  const clean = {};
  for (const [key, max] of Object.entries(TEXT_LIMITS)) clean[key] = String(draft?.[key] || "").trim().slice(0, max);
  for (const { key, max } of RECORD_SECTIONS) clean[key] = String(draft?.[key] || "").trim().slice(0, max);
  for (const key of RECORD_LISTS) clean[key] = splitList(draft?.[key]);
  return clean;
}

const recordKeys = clean => Object.fromEntries([...RECORD_SECTIONS.map(({ key }) => key), ...RECORD_LISTS].map(key => [key, clean[key]]));

// An edit of a record's wording: only the parts that changed, plus the user's note.
// null when nothing changed; {error} when the decision or question was cleared.
export function recordEditChange(draft, item) {
  if (!item?.id) return null;
  const clean = tidyDraft(draft);
  if (!clean.answer) return { error: "The decision can't be empty." };
  if (!clean.question) return { error: "The question can't be empty." };
  const current = tidyDraft(recordDraft(item));
  const change = { action: "edit", id: item.id };
  for (const key of ["answer", "question", "why", ...RECORD_SECTIONS.map(({ key: part }) => part)]) {
    if (clean[key] !== current[key]) change[key] = clean[key];
  }
  for (const key of RECORD_LISTS) if (clean[key].join("\n") !== current[key].join("\n")) change[key] = clean[key];
  if (Object.keys(change).length === 2) return null;
  if (clean.note) change.note = clean.note;
  return change;
}

// The user's new decision in place of one in force: the whole record, linked to the old one.
export function recordReplaceChange(draft, item) {
  if (!item?.id) return null;
  const clean = tidyDraft(draft);
  if (!clean.answer) return { error: "Write the new decision first." };
  const change = { action: "replace", id: item.id, answer: clean.answer, why: clean.why };
  if (clean.question) change.question = clean.question;
  if (isRecord(item)) Object.assign(change, recordKeys(clean));
  if (clean.note) change.note = clean.note;
  return change;
}

// A design decision the user adds for a project.
export function recordAddChange(draft, project) {
  if (!project) return { error: "Open a project to add a record to it." };
  const clean = tidyDraft(draft);
  if (!clean.question) return { error: "Say what the decision is about first." };
  if (!clean.answer) return { error: "Write the decision first." };
  return { action: "add", project, question: clean.question, answer: clean.answer, why: clean.why, remember_as: "record", ...recordKeys(clean) };
}

// The decisions a conflict's fix replaces, e.g. "Replaces D-3, D-5"; "" for any other item.
export function replacesLine(item) {
  const ids = Array.isArray(item?.supersedes_all) ? item.supersedes_all.filter(Boolean) : [];
  return ids.length ? `Replaces ${ids.join(", ")}` : "";
}

// The topics a project scan finds (rem_project_scan.TOPICS). Ask again on one of these
// scans the project for it, so the new card can recommend what the files say now.
export const SCAN_TOPICS = Object.freeze(["checks.fast", "branch.integration", "decisions.folder"]);

// Ask again's hover text: what happens, and that the answer holds until the user chooses.
export function askAgainTitle(item) {
  const scans = SCAN_TOPICS.includes(item?.topic);
  return `${scans ? "Rem reads the project's files again and brings this back as a new question with what they say now." : "This comes back as a new question for you."} This answer stays in force until you choose.`;
}

// Why a waiting card is back: "You asked again" for the user's Ask again (rem_decisions
// ASKED_AGAIN), otherwise the trigger, such as a changed file.
export function reopenedLine(item) {
  if (item?.asked_again) return item.fresh ? "You asked again. Rem read the project's files again." : "You asked again";
  return item?.reopened ? `Asked again: ${item.reopened}` : "";
}

const MONTHS = Object.freeze(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]);

// "21 Nov" for an ISO time on this computer's calendar; "" when it can't be read.
export function shortDay(stamp) {
  const when = stamp ? new Date(stamp) : null;
  return when && !Number.isNaN(when.getTime()) ? `${when.getDate()} ${MONTHS[when.getMonth()]}` : "";
}

function runNames(entries) {
  const labels = [...new Set(entries.map(entry => String(entry?.label || "A task")))];
  if (labels.length === 1) return { names: labels[0], plural: false };
  if (labels.length === 2) return { names: `${labels[0]} and ${labels[1]}`, plural: true };
  return { names: `${labels[0]} and ${labels.length - 1} more`, plural: true };
}

// One line under a waiting question (rem_decisions.asked_line): who asked it, when, and
// whether the runs that asked wait for it or went on without it. "" without `asked`.
export function askedLine(item) {
  const asked = item?.asked;
  if (!asked || typeof asked !== "object") return "";
  const parts = [`Asked by ${asked.label || "Rem"}`];
  const day = shortDay(asked.at);
  if (day) parts.push(day);
  const waiting = Array.isArray(asked.waiting) ? asked.waiting : [];
  const wentOn = Array.isArray(asked.went_on) ? asked.went_on : [];
  if (waiting.length) {
    const { names, plural } = runNames(waiting);
    parts.push(`${names} ${plural ? "wait" : "waits"} for this`);
  } else if (wentOn.length) parts.push(`${runNames(wentOn).names} went on without it`);
  return parts.join(" · ");
}

// The asked line's hover text: the asking task's title when the line names its ticket.
export function askedTitle(item) {
  const asked = item?.asked || {};
  const title = asked.task_title && asked.task_title !== asked.label ? asked.task_title : "";
  return [title, asked.opens ? "Open the task" : ""].filter(Boolean).join(" · ");
}

// The answer in force while an Ask again card waits; "" for any other card.
export const currentAnswer = item => item?.asked_again ? String(item.current_answer || "") : "";

// The decision a chat card names, as the list has it now; null when the list doesn't have it.
export function liveDecision(view, id) {
  if (!id) return null;
  return (view?.items || []).find(item => item.id === id) || null;
}

// What a chat card shows. `card` is the message's snapshot; `live` the list's item or null;
// `loaded` whether the list has been read at all (an older backend never sends one).
//   {state: "fallback"} | {state: "open", item} | {state: "decided", answer, replaced}
//   | {state: "queued"} | {state: "rejected"} | {state: "withdrawn"} | {state: "gone"}
// The desktop marks the cards of a question the user forgot while it waited "withdrawn",
// since the list no longer has it to say why.
export function cardState(card, live, loaded) {
  if (!card) return { state: "gone" };
  if (card.fallback || !card.id) return { state: "fallback" };
  if (!live && card.status === "withdrawn") return { state: "withdrawn" };
  const item = live || (loaded ? null : card);
  if (!item) return { state: "gone" };
  if (isOpen(item)) return { state: "open", item: { ...card, ...item } };
  if (item.status === "queued") return { state: "queued" };
  if (item.status === "rejected") return { state: "rejected" };
  return { state: "decided", answer: decisionAnswer(item), replaced: item.status === "superseded" ? item.superseded_by || "" : "" };
}

// The line a settled chat card shows in place of its buttons.
export function cardLine(view) {
  if (view.state === "rejected") return "Turned down";
  if (view.state === "withdrawn") return "You withdrew this question";
  if (view.state === "gone") return "No longer listed";
  if (view.state === "queued") return "Queued behind the questions already waiting for you";
  return `Decided: ${view.answer}`;
}

// The message box text for answering a fallback card yourself: the question, quoted.
export function fallbackDraft(card) {
  const question = String(card?.question || "").trim();
  return question ? `${question.split("\n").map(line => `> ${line}`).join("\n")}\n\n` : "";
}

// The same change applied locally, for the design fixtures (no backend).
export function applyDecisionChange(view, change, stamp = new Date().toISOString()) {
  let items = [...(view?.items || [])];
  const index = items.findIndex(item => item.id === change.id);
  const note = action => [{ at: stamp, actor: "user", action }];
  const recordOf = (current = {}) => {
    const parts = Object.fromEntries([...RECORD_SECTIONS.map(({ key }) => key), ...RECORD_LISTS].filter(key => key in change).map(key => [key, change[key]]));
    return { ...current, ...parts };
  };
  if (change.action === "add") {
    const record = Boolean(change.project) && change.remember_as === "record";
    items.unshift({ id: `D-${items.length + 100}`, project: change.project || null, topic: change.topic || "", question: change.question || change.answer, answer: change.answer, recommended: "", why: change.why || "", evidence: [], alternatives: [], status: "confirmed", decided_by: "user", decided_at: stamp, history: note("added"),
      remember_as: record ? "record" : change.project ? "fact" : "memory", ...(record ? { record: recordOf() } : {}) });
  } else if (index < 0) return view;
  else if (change.action === "reopen") {
    // Ask again: a new card waits for the user and the decision stays in force.
    const old = items[index];
    const id = `D-${items.length + 100}`;
    const shown = items.filter(other => other.status === "pending" && (other.project ?? null) === (old.project ?? null)).length;
    const asked = { id, project: old.project ?? null, topic: old.topic || "", remember_as: old.remember_as, question: old.question, answer: "", recommended: "", why: "", evidence: [], alternatives: [], status: shown >= 3 ? "queued" : "pending", decided_by: null, decided_at: null, supersedes: old.id, reopened: "You asked again.", asked_again: true, current_answer: old.answer || "", history: note("requested"),
      ...(isRecord(old) ? { record: { ...(old.record || {}) } } : {}) };
    items = [asked, ...items.map(other => other.id === old.id ? { ...other, history: [...(other.history || []), ...note("reopened")] } : other)];
  } else if (change.action === "replace") {
    const old = items[index];
    const id = `D-${items.length + 100}`;
    const fresh = { id, project: old.project ?? null, topic: old.topic || "", remember_as: old.remember_as, question: change.question || old.question, answer: change.answer, recommended: "", why: change.why || "", evidence: [], alternatives: [], status: "confirmed", decided_by: "user", decided_at: stamp, supersedes: old.id, history: note("added"),
      ...(isRecord(old) ? { record: recordOf(old.record) } : {}) };
    items = [fresh, ...items.map(other => other.id === old.id ? { ...other, status: "superseded", superseded_by: id } : other)];
  } else {
    const item = { ...items[index], history: [...(items[index].history || []), ...note(change.action)] };
    if (change.action === "use" || change.action === "change") {
      if (change.action === "change") item.answer = change.answer;
      else item.answer = item.answer || item.recommended;
      Object.assign(item, { status: "confirmed", decided_by: "user", decided_at: stamp });
      for (const key of ["reopened", "asked_again", "current_answer", "fresh"]) delete item[key];
      const replaced = new Set([item.supersedes, ...(item.supersedes_all || [])].filter(Boolean));
      if (replaced.size) items = items.map(other => replaced.has(other.id) ? { ...other, status: "superseded", superseded_by: item.id } : other);
    } else if (change.action === "edit") {
      for (const key of ["answer", "question", "why"]) if (key in change) item[key] = change[key];
      if (isRecord(item)) item.record = recordOf(item.record);
      delete item.edited_outside;
    } else if (change.action === "reject") item.status = "rejected";
    // The backend refuses forget for a written record (Retire is its own action), so the
    // view shows no change for one rather than a deletion that won't happen.
    if (change.action === "forget" && isWritten(items[index])) return view;
    const retire = change.action === "retire";
    if (retire) {
      Object.assign(item, { status: "rejected", retired_at: stamp });
      item.history = [...(items[index].history || []), ...note("retired")];
    }
    if (change.action === "forget" && !retire) items = items.filter(other => other.id !== change.id);
    else items = items.map(other => other.id === item.id ? item : other);
  }
  const order = { pending: 0, queued: 1, provisional: 2, confirmed: 3, superseded: 4, rejected: 5 };
  items = items.map((item, position) => ({ item, position })).sort((a, b) => (order[a.item.status] ?? 9) - (order[b.item.status] ?? 9) || a.position - b.position).map(entry => entry.item);
  const count = status => items.filter(item => item.status === status).length;
  return { ...view, revision: (view?.revision || 0) + 1, items, waiting: count("pending"), queued: count("queued"), provisional: count("provisional"), confirmed: count("confirmed") };
}

// Changes the user asked Rem for in chat (rem_decisions.propose_edit). Rem never applies
// one: the card under its reply, and the Decisions list while it waits, show each part's
// before and after with OK and Cancel. The desktop rewrites the card once it is settled.
const EDIT_EMPTY = "(empty)";

// The card's heading: "Change D-3?" or "Replace D-3?".
export const editTitle = card => `${card?.action === "replace" ? "Replace" : "Change"} ${card?.decision || "this decision"}?`;

// Each part that changes, as {field, label, before, after}, with empty sides marked.
export function editChanges(card) {
  return (Array.isArray(card?.changes) ? card.changes : [])
    .filter(change => change && change.field)
    .map(change => ({ field: change.field, label: change.label || change.field, before: change.before || EDIT_EMPTY, after: change.after || EDIT_EMPTY }));
}

// Whether OK and Cancel show, and what a settled card says instead.
export function editState(card) {
  const status = card?.status;
  if (status === "waiting") return { waiting: true, line: "" };
  if (status === "applied") {
    const line = card.action === "replace" && card.result ? `Applied: ${card.result} replaces ${card.decision}` : `Applied to ${card.decision}`;
    return { waiting: false, line };
  }
  if (status === "cancelled") return { waiting: false, line: "Cancelled. Nothing changed." };
  if (status === "replaced") return { waiting: false, line: card.replaced_by ? `Replaced by a newer change (${card.replaced_by})` : "Replaced by a newer change" };
  return { waiting: false, line: "No longer listed" };
}

// The waiting changes for the pane's project, or every project's.
export function waitingEdits(view, { project = "", all = false } = {}) {
  return (view?.edits || []).filter(edit => edit?.status === "waiting" && (all || inProject(edit, project)));
}

// Fixture mode: a card pressed in the chat settles there and leaves the waiting list.
export function settleEditLocally(state, id, status) {
  const settle = card => card?.id === id ? { ...card, status } : card;
  return {
    ...state,
    messages: (state.messages || []).map(message => Array.isArray(message.decision_edits) ? { ...message, decision_edits: message.decision_edits.map(settle) } : message),
    decisions: { ...state.decisions, edits: (state.decisions?.edits || []).filter(edit => edit.id !== id) },
  };
}
