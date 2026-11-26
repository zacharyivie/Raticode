import assert from "node:assert/strict";
import test from "node:test";
import { NO_OBJECTIVES, OBJECTIVES_PAGE, applyObjectiveChange, draftChange, moreToLoad, objectiveDetails, objectivesCount, orderedObjectives, runningByObjective, sameObjectives, splitObjectives, staleCount, taskObjective } from "./remObjectives.js";

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

test("a draft is tidied, and an unchanged or empty one saves nothing", () => {
  assert.deepEqual(draftChange({ text: "  Raise   UX quality ", doneWhen: " " }), { action: "add", text: "Raise UX quality", done_when: "" });
  assert.equal(draftChange({ text: "   " }), null);
  assert.equal(draftChange({ text: items[0].text, doneWhen: "" }, items[0]), null);
  assert.deepEqual(draftChange({ text: items[0].text, doneWhen: "Every page passes the audit" }, items[0]), { action: "edit", id: "a", text: items[0].text, done_when: "Every page passes the audit" });
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
