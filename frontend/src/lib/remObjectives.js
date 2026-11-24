// Rem's self-work objectives: the goals that guide a whole self-work session, shown as a
// checklist above Rem's tasks in the side pane. The backend (rem_objectives.py) owns the
// list and its rules; these helpers only read it and shape the user's changes.

export const NO_OBJECTIVES = Object.freeze({ revision: 0, items: Object.freeze([]), open: 0, done: 0 });
export const OBJECTIVE_TEXT_MAX = 200;
export const OBJECTIVE_CRITERIA_MAX = 500;

// A poll that sent the same list keeps the old object, so the checklist doesn't re-render.
export function sameObjectives(next, current) {
  if (!next || !Array.isArray(next.items)) return current || NO_OBJECTIVES;
  return current && JSON.stringify(next) === JSON.stringify(current) ? current : next;
}

// The heading's count: "5", "2/5" once some are done, or "" for an empty list.
export function objectivesCount(view) {
  const items = view?.items || [];
  if (!items.length) return "";
  const done = items.filter(item => item.done).length;
  return done ? `${done}/${items.length}` : String(items.length);
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

// The hover text of a row: its criteria, and for a done one how they were met.
export function objectiveDetails(item) {
  const lines = [];
  lines.push(item.done_when ? `Done when: ${item.done_when}` : "Open-ended: no finish line, so Rem never checks it off.");
  if (item.done) {
    const who = item.done_by === "rem" ? "Rem" : "You";
    lines.push(`${who} checked it off${item.done_evidence ? `: ${item.done_evidence}` : "."}`);
  }
  if (item.created_by === "rem") lines.push("Written by Rem.");
  return lines.join("\n");
}

// The move payload for shifting one objective up (-1) or down (+1), or null at an end.
export function moveChange(items, id, delta) {
  const index = items.findIndex(item => item.id === id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= items.length) return null;
  return { action: "move", id, index: target };
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
    for (const key of ["done_at", "done_by", "done_evidence"]) delete rest[key];
    items[index] = rest;
  }
  else if (change.action === "move") { const [moved] = items.splice(index, 1); items.splice(Math.min(change.index, items.length), 0, moved); }
  const done = items.filter(item => item.done).length;
  return { ...view, revision: (view?.revision || 0) + 1, items, open: items.length - done, done };
}
