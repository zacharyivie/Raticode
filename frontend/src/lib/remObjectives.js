// Rem's self-work objectives: the goals that guide a whole self-work session, shown as a
// checklist above Rem's tasks in the side pane. The backend (rem_objectives.py) owns the
// list and its rules; these helpers only read it and shape the user's changes.
//
// The list comes newest first with open ones above done ones. When a self-work session
// ends, the objectives done by then go stale: they no longer count in the heading and sit in
// their own folded group below. The backend sends the first OBJECTIVES_PAGE of them; the
// side pane asks for more as the user scrolls to the bottom.

export const NO_OBJECTIVES = Object.freeze({ revision: 0, items: Object.freeze([]), open: 0, done: 0, stale: 0, total: 0 });
export const OBJECTIVE_TEXT_MAX = 200;
export const OBJECTIVE_CRITERIA_MAX = 500;
export const OBJECTIVES_PAGE = 30;

// Done in a self-work session that has ended.
export const isStale = item => Boolean(item?.done && item?.stale);

// A poll that sent the same list keeps the old object, so the checklist doesn't re-render.
// The same revision with more objectives loaded (the user scrolled) keeps those too.
export function sameObjectives(next, current) {
  if (!next || !Array.isArray(next.items)) return current || NO_OBJECTIVES;
  if (current && next.revision === current.revision && (current.items?.length || 0) > next.items.length) return current;
  return current && JSON.stringify(next) === JSON.stringify(current) ? current : next;
}

// The counts a view carries, worked out from its items when it has none (design fixtures).
function counts(view) {
  const items = view?.items || [];
  if (typeof view?.open === "number" && typeof view?.done === "number") {
    return { open: view.open, done: view.done, stale: view.stale || 0, total: typeof view.total === "number" ? view.total : items.length };
  }
  const stale = items.filter(isStale).length;
  const done = items.filter(item => item.done).length - stale;
  return { open: items.length - done - stale, done, stale, total: items.length };
}

// The heading's count, for this session only: "5", "2/5" once some are done, or "" when
// nothing is current. Stale objectives never count.
export function objectivesCount(view) {
  const { open, done } = counts(view);
  const total = open + done;
  if (!total) return "";
  return done ? `${done}/${total}` : String(total);
}

// How many stale objectives there are, loaded or not.
export const staleCount = view => counts(view).stale;

// Open first, then done in this session, then stale; newest first within each, and a batch
// added at one time in the order it was given. The backend sends them this way already.
export function orderedObjectives(items = []) {
  const group = item => isStale(item) ? 2 : item.done ? 1 : 0;
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => group(a.item) - group(b.item)
      || String(b.item.created_at || "").localeCompare(String(a.item.created_at || ""))
      || a.index - b.index)
    .map(entry => entry.item);
}

// The loaded objectives split into the current list and the stale group.
export function splitObjectives(items = []) {
  const current = [];
  const stale = [];
  for (const item of items) (isStale(item) ? stale : current).push(item);
  return { current, stale };
}

// Whether scrolling to the bottom should load more: current objectives not loaded yet, or
// stale ones once their group is open.
export function moreToLoad(view, staleOpen) {
  const loaded = (view?.offset || 0) + (view?.items?.length || 0);
  const { open, done, total } = counts(view);
  return loaded < open + done || (staleOpen && loaded < total);
}

// The objective a task's current (or last) self-work run serves: {id, text} or null.
export function taskObjective(task) {
  const run = task?.active_run || task?.last_run;
  const objective = run?.objective;
  return objective && objective.id && objective.text ? objective : null;
}

// How many running tasks serve each objective, by objective id.
export function runningByObjective(tasks = []) {
  const counts = new Map();
  for (const task of tasks) {
    const objective = task?.active_run?.objective;
    if (objective?.id) counts.set(objective.id, (counts.get(objective.id) || 0) + 1);
  }
  return counts;
}

// A row's details, one per line: its criteria, and for a done one how they were met. The
// row shows them when it opens and as its hover text.
export function objectiveDetails(item) {
  const lines = [];
  lines.push(item.done_when ? `Done when: ${item.done_when}` : "Open-ended: no finish line, so Rem never checks it off.");
  if (item.done) {
    const who = item.done_by === "rem" ? "Rem" : "You";
    lines.push(`${who} checked it off${item.done_evidence ? `: ${item.done_evidence}` : "."}`);
    if (isStale(item)) lines.push("Stale: done in an earlier self-work session, so it doesn't count toward this one.");
  }
  if (item.created_by === "rem") lines.push("Written by Rem.");
  return lines.join("\n");
}

// Tidy what the user typed; null when there's nothing to save.
export function draftChange(draft, item = null) {
  const text = String(draft.text || "").replace(/\s+/g, " ").trim().slice(0, OBJECTIVE_TEXT_MAX);
  const doneWhen = String(draft.doneWhen || "").replace(/\s+/g, " ").trim().slice(0, OBJECTIVE_CRITERIA_MAX);
  if (!text) return null;
  if (!item) return { action: "add", text, done_when: doneWhen };
  if (text === item.text && doneWhen === (item.done_when || "")) return null;
  return { action: "edit", id: item.id, text, done_when: doneWhen };
}

// The same change applied locally, for the design fixtures (no backend).
export function applyObjectiveChange(view, change, stamp = new Date().toISOString()) {
  let items = [...(view?.items || [])];
  const index = items.findIndex(item => item.id === change.id);
  if (change.action === "add") items.push({ id: `local-${items.length + 1}-${Date.parse(stamp) || 0}`, text: change.text, done_when: change.done_when || "", done: false, created_by: "user", created_at: stamp });
  else if (index < 0) return view;
  else if (change.action === "edit") items[index] = { ...items[index], text: change.text, done_when: change.done_when ?? items[index].done_when };
  else if (change.action === "remove") items = items.filter(item => item.id !== change.id);
  else if (change.action === "check") items[index] = { ...items[index], done: true, done_by: "user", done_at: stamp };
  else if (change.action === "uncheck") {
    const rest = { ...items[index], done: false };
    for (const key of ["done_at", "done_by", "done_evidence", "stale", "stale_at"]) delete rest[key];
    items[index] = rest;
  }
  else return view;
  items = orderedObjectives(items);
  const { open, done, stale } = counts({ items });
  return { ...view, revision: (view?.revision || 0) + 1, items, offset: 0, total: items.length, open, done, stale };
}
