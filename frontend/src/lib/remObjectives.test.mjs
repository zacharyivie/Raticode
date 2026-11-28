import assert from "node:assert/strict";
import test from "node:test";
import { NO_OBJECTIVES, OBJECTIVES_PAGE, applyObjectiveChange, askLine, askState, projectSections, draftChange, moreToLoad, objectiveDetails, objectivesCount, orderedObjectives, runningByObjective, sameObjectives, splitObjectives, staleCount, taskObjective, withProjectPage } from "./remObjectives.js";

const items = [
  { id: "a", text: "Raise UX quality of the workflow studio", done_when: "", done: false, created_by: "user" },
  { id: "b", text: "Improve reliability of the Second Brain tools", done_when: "Nightly passes 3 nights", done: true, done_by: "rem", done_evidence: "Passed Mon to Wed.", created_by: "rem" },
  { id: "c", text: "Optimize performance of file handling", done_when: "", done: false, created_by: "rem" },
];
const view = { revision: 2, items, open: 2, done: 1 };

test("the heading counts objectives, and done ones once there are any", () => {
  assert.equal(objectivesCount(NO_OBJECTIVES), "");
  assert.equal(objectivesCount({ items: [items[0]] }), "1");
  assert.equal(objectivesCount(view), "1/3");
});

test("an unchanged poll keeps the same list object, and a missing one keeps the last", () => {
  assert.equal(sameObjectives(JSON.parse(JSON.stringify(view)), view), view);
  assert.equal(sameObjectives(undefined, view), view);
  assert.equal(sameObjectives(undefined, undefined), NO_OBJECTIVES);
  assert.notEqual(sameObjectives({ ...view, revision: 3 }, view), view);
});

test("a task shows the objective its current run serves, else its last run's", () => {
  assert.deepEqual(taskObjective({ active_run: { objective: { id: "a", text: "UX" } } }), { id: "a", text: "UX" });
  assert.deepEqual(taskObjective({ active_run: null, last_run: { objective: { id: "c", text: "Perf" } } }), { id: "c", text: "Perf" });
  assert.equal(taskObjective({ active_run: { id: "r" } }), null);
  const counts = runningByObjective([{ active_run: { objective: { id: "a" } } }, { active_run: { objective: { id: "a" } } }, { last_run: { objective: { id: "c" } } }]);
  assert.equal(counts.get("a"), 2);
  assert.equal(counts.has("c"), false, "only running tasks count");
});

test("hover text explains open-ended objectives and how a done one was met", () => {
  assert.match(objectiveDetails(items[0]), /Open-ended/);
  const done = objectiveDetails(items[1]);
  assert.match(done, /Done when: Nightly passes 3 nights/);
  assert.match(done, /Rem checked it off: Passed Mon to Wed\./);
  assert.match(done, /Written by Rem/);
});

const stamp = minutes => new Date(Date.UTC(2026, 10, 24, 12, minutes)).toISOString();

test("objectives list newest first, open above done, and stale ones last", () => {
  const mixed = [
    { id: "old-open", done: false, created_at: stamp(1) },
    { id: "stale", done: true, stale: true, created_at: stamp(9) },
    { id: "new-done", done: true, created_at: stamp(8) },
    { id: "new-open", done: false, created_at: stamp(7) },
    { id: "batch-1", done: false, created_at: stamp(5) },
    { id: "batch-2", done: false, created_at: stamp(5) },
    { id: "old-done", done: true, created_at: stamp(2) },
  ];
  assert.deepEqual(orderedObjectives(mixed).map(item => item.id), ["new-open", "batch-1", "batch-2", "old-open", "new-done", "old-done", "stale"]);
  const { current, stale } = splitObjectives(orderedObjectives(mixed));
  assert.deepEqual(stale.map(item => item.id), ["stale"]);
  assert.equal(current.length, 6);
});

test("stale objectives don't count toward this session's done/total", () => {
  assert.equal(objectivesCount({ items: [], open: 2, done: 1, stale: 4, total: 7 }), "1/3");
  assert.equal(objectivesCount({ items: [], open: 0, done: 0, stale: 4, total: 4 }), "", "only stale ones: nothing current");
  // A view without counts (a design fixture) works them out from its items.
  assert.equal(objectivesCount({ items: [{ done: false }, { done: true }, { done: true, stale: true }] }), "1/2");
  assert.equal(staleCount({ items: [{ done: true, stale: true }, { done: false, stale: true }] }), 1, "only a done one is stale");
  assert.match(objectiveDetails({ ...items[1], stale: true }), /Stale: done in an earlier self-work session/);
});

test("scrolling loads more current ones, and stale ones only while their group is open", () => {
  const page = (loaded, counts) => ({ items: Array.from({ length: loaded }, (_, n) => ({ id: `o${n}` })), offset: 0, ...counts });
  assert.equal(OBJECTIVES_PAGE, 30);
  assert.equal(moreToLoad(page(30, { open: 20, done: 15, stale: 10, total: 45 }), false), true, "current ones still to load");
  assert.equal(moreToLoad(page(35, { open: 20, done: 15, stale: 10, total: 45 }), false), false, "only stale ones left, group folded");
  assert.equal(moreToLoad(page(35, { open: 20, done: 15, stale: 10, total: 45 }), true), true, "stale group open");
  assert.equal(moreToLoad(page(45, { open: 20, done: 15, stale: 10, total: 45 }), true), false, "everything loaded");
});

test("a poll's first page doesn't drop pages the user scrolled to in the same revision", () => {
  const loaded = { revision: 5, items: Array.from({ length: 60 }, (_, n) => ({ id: `o${n}` })), total: 60 };
  const polled = { revision: 5, items: loaded.items.slice(0, 30), total: 60 };
  assert.equal(sameObjectives(polled, loaded), loaded);
  const changed = { ...polled, revision: 6 };
  assert.equal(sameObjectives(changed, loaded), changed);
});

test("a project's Show more page joins its section, and a poll in the same revision keeps it", () => {
  const own = Array.from({ length: 35 }, (_, n) => ({ id: `p${n}` }));
  const first = { revision: 5, items: own.slice(0, 30), offset: 0, total: 35, open: 35, done: 0, stale: 0 };
  const polled = { revision: 5, items: [], offset: 0, total: 0, open: 0, done: 0, stale: 0, projects: { "/work/site": first } };
  const page = { ...first, items: own.slice(30), offset: 30, project: "/work/site" };
  const merged = withProjectPage(polled, "/work/site", page);
  assert.deepEqual(merged.projects["/work/site"].items.map(item => item.id), own.map(item => item.id));
  assert.equal(merged.projects["/work/site"].offset, 0);
  assert.equal("project" in merged.projects["/work/site"], false);
  assert.equal(moreToLoad(merged.projects["/work/site"], false), false, "everything is loaded");
  // The next poll sends the first page again; the same revision keeps what was loaded.
  assert.equal(sameObjectives(JSON.parse(JSON.stringify(polled)), merged), merged);
  assert.equal(sameObjectives({ ...polled, revision: 6 }, merged).projects["/work/site"].items.length, 30, "a changed list starts over");
  // A page from another revision, or one that doesn't start where the list ends, doesn't fit.
  assert.equal(withProjectPage(polled, "/work/site", { ...page, revision: 6 }), null);
  assert.equal(withProjectPage(polled, "/work/site", { ...page, offset: 31 }), null);
  // A page from offset 0 replaces the section.
  assert.equal(withProjectPage(polled, "/work/site", { ...first, items: own }).projects["/work/site"].items.length, 35);
});

test("a draft is tidied, and an unchanged or empty one saves nothing", () => {
  assert.deepEqual(draftChange({ text: "  Raise   UX quality ", doneWhen: " " }), { action: "add", text: "Raise UX quality", done_when: "" });
  assert.equal(draftChange({ text: "   " }), null);
  assert.equal(draftChange({ text: items[0].text, doneWhen: "" }, items[0]), null);
  assert.deepEqual(draftChange({ text: items[0].text, doneWhen: "Every page passes the audit" }, items[0]), { action: "edit", id: "a", text: items[0].text, done_when: "Every page passes the audit" });
  // Applies to: a different project moves it, the same one changes nothing.
  assert.deepEqual(draftChange({ text: items[0].text, doneWhen: "", project: "/work/site" }, items[0], ""), { action: "edit", id: "a", text: items[0].text, done_when: "", project: "/work/site" });
  assert.deepEqual(draftChange({ text: items[0].text, doneWhen: "", project: "" }, items[0], "/work/site"), { action: "edit", id: "a", text: items[0].text, done_when: "", project: "" });
  assert.equal(draftChange({ text: items[0].text, doneWhen: "", project: "/work/site" }, items[0], "/work/site"), null);
});

test("the design fixtures apply the same changes locally", () => {
  const stamp = "2026-11-17T12:00:00.000Z";
  const added = applyObjectiveChange(view, { action: "add", text: "Harden the CLI", done_when: "" }, stamp);
  assert.equal(added.items.length, 4);
  assert.equal(added.revision, 3);
  assert.equal(added.items[0].text, "Harden the CLI", "the newest goes on top");
  // There is no manual order; a move changes nothing.
  assert.equal(applyObjectiveChange(view, { action: "move", id: "c", index: 0 }), view);
  const checked = applyObjectiveChange(view, { action: "check", id: "a" }, stamp);
  assert.equal(checked.items.find(item => item.id === "a").done, true);
  assert.deepEqual(checked.items.map(item => item.id), ["c", "a", "b"], "done ones go below open ones");
  assert.equal(checked.done, 2);
  const reopened = applyObjectiveChange({ ...view, items: [items[0], { ...items[1], stale: true }, items[2]] }, { action: "uncheck", id: "b" });
  const b = reopened.items.find(item => item.id === "b");
  assert.equal(b.done, false);
  assert.equal("done_evidence" in b || "stale" in b, false, "reopened, it isn't stale any more");
  assert.deepEqual(applyObjectiveChange(view, { action: "remove", id: "a" }).items.map(item => item.id), ["c", "b"]);
  assert.equal(applyObjectiveChange(view, { action: "edit", id: "missing", text: "x" }), view);
});

test("each enabled project gets a section with its own objectives, effort and threads", async () => {
  const { effortValue, previewObjectiveChange, projectSections, threadsLine } = await import("./remObjectives.js");
  const withProjects = { ...view, projects: { "/p/trading": { revision: 2, items: [{ id: "t1", text: "Cut the strategy's drawdowns", done: false }], open: 1, done: 0 } } };
  const rows = [
    { root: "/p/raticode", name: "Raticode", effort_percent: 70, target_threads: 7, running_threads: 6 },
    { root: "/p/trading", name: "agent-trading", effort_percent: 30, target_threads: 1, running_threads: 1 },
  ];
  const sections = projectSections(withProjects, rows);
  assert.deepEqual(sections.map(section => section.name), ["Raticode", "agent-trading"]);
  assert.equal(sections[0].objectives, NO_OBJECTIVES, "a project with none of its own has an empty list");
  assert.equal(sections[1].objectives.items[0].text, "Cut the strategy's drawdowns");
  // A project taken off Rem's projects has no section, even with objectives stored.
  assert.deepEqual(projectSections(withProjects, rows.slice(0, 1)).map(section => section.root), ["/p/raticode"]);
  assert.deepEqual(projectSections(withProjects, undefined), []);
  assert.equal(threadsLine(rows[0]), "6 of 7 threads");
  assert.equal(threadsLine(rows[1]), "1 of 1 thread");
  // Typed percents: blank or junk keeps the saved number; others are whole and in range.
  assert.equal(effortValue(""), null);
  assert.equal(effortValue("  "), null);
  assert.equal(effortValue("abc"), null);
  assert.equal(effortValue("70%"), 70);
  assert.equal(effortValue("33.6"), 34);
  assert.equal(effortValue("140"), 100);
  assert.equal(effortValue("-5"), 0);
  // A check of a project's objective shows at once in that project's list only.
  const previewed = previewObjectiveChange(withProjects, { action: "check", id: "t1" }, "2026-11-26T00:00:00Z");
  assert.equal(previewed.projects["/p/trading"].items[0].done, true);
  assert.equal(previewed.items, withProjects.items);
  assert.equal(previewObjectiveChange(withProjects, { action: "check", id: "nope" }), null);
});

test("the design fixtures add a project's objective to that project's list", () => {
  const added = applyObjectiveChange({ ...view, projects: {} }, { action: "add", text: "Cut drawdowns", done_when: "", project: "/p/trading" }, "2026-11-26T00:00:00Z");
  assert.equal(added.revision, view.revision + 1);
  assert.equal(added.items.length, view.items.length, "the global list is unchanged");
  assert.deepEqual(added.projects["/p/trading"].items.map(item => item.text), ["Cut drawdowns"]);
  const checked = applyObjectiveChange(added, { action: "check", id: added.projects["/p/trading"].items[0].id });
  assert.equal(checked.projects["/p/trading"].items[0].done, true);
  assert.equal(checked.projects["/p/trading"].done, 1);
});

test("the design fixtures move an edited objective between the global list and a project", () => {
  const first = view.items[0];
  const moved = applyObjectiveChange({ ...view, projects: {} }, { action: "edit", id: first.id, text: first.text, done_when: "", project: "/p/trading" });
  assert.equal(moved.revision, view.revision + 1);
  assert.ok(!moved.items.some(item => item.id === first.id), "it leaves the global list");
  assert.deepEqual(moved.projects["/p/trading"].items.map(item => [item.id, item.project]), [[first.id, "/p/trading"]]);
  assert.equal(moved.projects["/p/trading"].total, 1);
  const back = applyObjectiveChange(moved, { action: "edit", id: first.id, text: "Reworded", project: "" });
  assert.deepEqual(back.projects["/p/trading"].items, []);
  const returned = back.items.find(item => item.id === first.id);
  assert.equal(returned.text, "Reworded");
  assert.equal("project" in returned, false);
  assert.equal(back.total, view.items.length);
});

test("each project section carries its latest Ask Rem, and the row says how it stands", () => {
  const asking = { state: "asking", message: "Rem is reading site to write objectives…", added: 0, at: "2026-11-26T12:00:00Z" };
  const listed = { revision: 3, items: [], asking: { "/work/site": asking, "/work/app": { state: "weird" } } };
  const sections = projectSections(listed, [{ root: "/work/app", name: "app" }, { root: "/work/site", name: "site" }]);
  assert.deepEqual(sections.map(s => s.ask), [null, asking], "an unknown state shows nothing");
  assert.equal(askState(NO_OBJECTIVES, "/work/site"), null);
  assert.deepEqual(askLine(null), { busy: false, tone: "", text: "" });
  assert.deepEqual(askLine(null, { sending: true }), { busy: true, tone: "status", text: "Asking Rem…" });
  assert.deepEqual(askLine(asking), { busy: true, tone: "status", text: asking.message });
  assert.deepEqual(askLine({ state: "added", message: "Added 4 objectives for site." }), { busy: false, tone: "status", text: "Added 4 objectives for site." });
  assert.deepEqual(askLine({ state: "failed", message: "Rem's model couldn't answer." }), { busy: false, tone: "error", text: "Rem's model couldn't answer." });
  // This window's refusal outranks what the backend last said.
  assert.deepEqual(askLine(asking, { error: "Couldn't ask Rem. Full." }), { busy: false, tone: "error", text: "Couldn't ask Rem. Full." });
  // A poll keeps a project's ask as it changes, though the revision doesn't.
  const later = sameObjectives({ ...listed, asking: { "/work/site": { ...asking, state: "added" } } }, listed);
  assert.equal(later.asking["/work/site"].state, "added");
});
