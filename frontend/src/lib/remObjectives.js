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
const isStale = item => Boolean(item?.done && item?.stale);

// A poll that sent the same list keeps the old object, so the checklist doesn't re-render.
// The same revision with more objectives loaded (the user scrolled, globally or in a
// project's section) keeps those too.
export function sameObjectives(next, current) {
  if (!next || !Array.isArray(next.items)) return current || NO_OBJECTIVES;
  const kept = current && next.revision === current.revision ? withLoadedPages(next, current) : next;
  return current && JSON.stringify(kept) === JSON.stringify(current) ? current : kept;
}

// ``next`` with the longer of its and ``current``'s loaded lists, the global one and each
// project's. Only for the same revision, where a longer list is the same list loaded further.
function withLoadedPages(next, current) {
  const longer = (list, loaded) => (loaded?.items?.length || 0) > (list?.items?.length || 0) ? loaded : list;
  const global = longer(next, current) === current ? { ...next, items: current.items, offset: current.offset } : next;
  if (!next.projects) return global;
  return { ...global, projects: Object.fromEntries(Object.entries(next.projects).map(([root, list]) => [root, longer(list, current.projects?.[root])])) };
}

// A page of one project's objectives (GET /objectives?project=…) merged into that project's
// section of ``view``: a page from offset 0 replaces it, a later one adds to its end. Null
// when the page doesn't fit: another revision, or an offset other than where the loaded list
// ends. The next poll then brings a list to load from.
export function withProjectPage(view, root, page) {
  if (!root || !Array.isArray(page?.items) || page.revision !== view?.revision) return null;
  const section = view.projects?.[root] || NO_OBJECTIVES;
  const offset = page.offset || 0;
  if (offset && offset !== (section.offset || 0) + section.items.length) return null;
  const loaded = new Set(offset ? section.items.map(item => item.id) : []);
  const items = offset ? [...section.items, ...page.items.filter(item => !loaded.has(item.id))] : page.items;
  const merged = { ...page, items, offset: offset ? section.offset || 0 : 0 };
  delete merged.project;
  return { ...view, projects: { ...view.projects, [root]: merged } };
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

// Tidy what the user typed; null when there's nothing to save. For an edit, ``draft.project``
// is where it applies ("" for every project) and ``scope`` where it is now; a different one
// moves it.
export function draftChange(draft, item = null, scope = "") {
  const text = String(draft.text || "").replace(/\s+/g, " ").trim().slice(0, OBJECTIVE_TEXT_MAX);
  const doneWhen = String(draft.doneWhen || "").replace(/\s+/g, " ").trim().slice(0, OBJECTIVE_CRITERIA_MAX);
  if (!text) return null;
  if (!item) return { action: "add", text, done_when: doneWhen };
  const moves = typeof draft.project === "string" && draft.project !== scope;
  if (text === item.text && doneWhen === (item.done_when || "") && !moves) return null;
  return { action: "edit", id: item.id, text, done_when: doneWhen, ...(moves ? { project: draft.project } : {}) };
}

// What the side pane shows while a check, uncheck or remove is on its way to the backend:
// the item moves or goes at once and the counts follow, with the server's revision and
// paging kept, so the request still says which list the user changed. Null when the change
// can't be shown ahead (add, edit, or an item that isn't loaded).
export function previewObjectiveChange(view, change, stamp = new Date().toISOString()) {
  const items = view?.items || [];
  const item = items.find(entry => entry.id === change?.id);
  if (!item && change?.id) return inProject(view, change, (sub, rest) => previewObjectiveChange(sub, rest, stamp));
  if (!item || !["check", "uncheck", "remove"].includes(change.action)) return null;
  const was = isStale(item) ? "stale" : item.done ? "done" : "open";
  let next;
  let now;
  if (change.action === "remove") { next = items.filter(entry => entry !== item); now = null; }
  else if (change.action === "check" && !item.done) {
    next = orderedObjectives(items.map(entry => entry === item ? { ...item, done: true, done_by: "user", done_at: stamp } : entry));
    now = "done";
  } else if (change.action === "uncheck" && item.done) {
    const reopened = { ...item, done: false };
    for (const key of ["done_at", "done_by", "done_evidence", "stale", "stale_at"]) delete reopened[key];
    next = orderedObjectives(items.map(entry => entry === item ? reopened : entry));
    now = "open";
  } else return view;
  const tally = { ...view, items: next };
  // A view without counts (design fixtures) works them out from its items instead.
  if (typeof view.open === "number" && typeof view.done === "number") {
    tally[was] = Math.max(0, (view[was] || 0) - 1);
    if (now) tally[now] = (tally[now] || 0) + 1;
    else if (typeof view.total === "number") tally.total = Math.max(0, view.total - 1);
  }
  return tally;
}

// The same change applied locally, for the design fixtures (no backend).
export function applyObjectiveChange(view, change, stamp = new Date().toISOString()) {
  if (change.action === "edit" && typeof change.project === "string") return moveObjective(view, change);
  let items = [...(view?.items || [])];
  const index = items.findIndex(item => item.id === change.id);
  if ((change.action === "add" && change.project) || (change.action !== "add" && index < 0)) {
    const changed = inProject(view, change, (sub, rest) => applyObjectiveChange(sub, rest, stamp));
    return changed ? { ...changed, revision: (view?.revision || 0) + 1 } : view;
  }
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

// An edit that moves an objective to another list ("" for the global one), for the design
// fixtures: it keeps its id and the counts of both lists follow.
function moveObjective(view, change) {
  const lists = [["", view], ...Object.entries(view?.projects || {})];
  const [from, source] = lists.find(([, list]) => (list?.items || []).some(item => item.id === change.id)) || [];
  if (!source) return view;
  const item = source.items.find(entry => entry.id === change.id);
  const moved = { ...item, text: change.text ?? item.text, done_when: change.done_when ?? item.done_when };
  if (change.project) moved.project = change.project; else delete moved.project;
  const tally = (list, items) => { const ordered = orderedObjectives(items); return { ...list, items: ordered, offset: 0, ...counts({ items: ordered }) }; };
  const projects = { ...(view.projects || {}) };
  let top = view;
  if (from) projects[from] = tally(source, source.items.filter(entry => entry !== item));
  else top = tally(view, view.items.filter(entry => entry !== item));
  if (change.project) projects[change.project] = tally(projects[change.project] || NO_OBJECTIVES, [...(projects[change.project]?.items || []), moved]);
  else top = tally(top, [...top.items, moved]);
  return { ...top, projects, revision: (view?.revision || 0) + 1 };
}

// Each of Rem's projects can have objectives of its own, which steer only that project's
// tickets and research. The objectives view lists the global ones in items and each
// project's own under projects, by folder; the self-work view lists the enabled projects
// with their level of effort (their share of self-work's threads) and threads now/target.

// One project's own objectives, or an empty list.
const projectObjectives = (view, root) => view?.projects?.[root] || NO_OBJECTIVES;

// The child sections under Objectives: one per enabled project, in Rem's order, each with
// its objectives. A project taken off Rem's projects has none.
export function projectSections(view, projects = []) {
  return (projects || []).filter(row => row?.root).map(row => ({ ...row, objectives: projectObjectives(view, row.root), ask: askState(view, row.root) }));
}

// Ask Rem in a project's section: Rem reads the project and adds three to five broad,
// open-ended objectives, each a goal self-work can follow for a whole session.
export const ASK_REM_TITLE = "Rem reads this project's docs, code, recent commits, board and objectives, then adds 3 to 5 broad, open-ended objectives self-work can follow for a whole session.";

// A project's latest Ask Rem as the backend reports it, by folder: { state: asking, added or
// failed, message, added, at }, or null when there is none to show.
export function askState(view, root) {
  const ask = view?.asking?.[root];
  return ask && typeof ask === "object" && ["asking", "added", "failed"].includes(ask.state) ? ask : null;
}

// What a project's Ask Rem row shows: whether the button waits, and the line under it.
// ``local`` is this window's own press: "sending" until the backend answers, or an error.
export function askLine(ask, local = null) {
  if (local?.error) return { busy: false, tone: "error", text: local.error };
  if (local?.sending || ask?.state === "asking") return { busy: true, tone: "status", text: ask?.state === "asking" ? ask.message : "Asking Rem…" };
  if (ask?.state === "failed") return { busy: false, tone: "error", text: ask.message };
  if (ask?.state === "added") return { busy: false, tone: "status", text: ask.message };
  return { busy: false, tone: "", text: "" };
}

// "7 of 7 threads": the threads a project's self-work holds now against its share.
export function threadsLine(row) {
  const target = Number(row?.target_threads) || 0;
  return `${Number(row?.running_threads) || 0} of ${target} ${target === 1 ? "thread" : "threads"}`;
}

// A typed level of effort as a whole percent from 0 to 100, or null when the field holds
// nothing usable, so blurring an empty field puts the saved number back.
export function effortValue(text, max = 100) {
  const trimmed = String(text ?? "").replace(/%\s*$/, "").trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return null;
  return Math.min(max, Math.max(0, Math.round(parsed)));
}

// A change to one project's list applied with ``apply``, the global list and counts kept.
// Null when no project's loaded list has the objective.
function inProject(view, change, apply) {
  const projects = view?.projects || {};
  const root = change.project && change.action === "add"
    ? change.project
    : Object.keys(projects).find(key => (projects[key]?.items || []).some(item => item.id === change.id));
  if (!root) return null;
  const rest = { ...change };
  delete rest.project;
  const next = apply(projects[root] || NO_OBJECTIVES, rest);
  if (!next) return null;
  return { ...view, projects: { ...projects, [root]: next } };
}
