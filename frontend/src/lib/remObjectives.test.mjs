import assert from "node:assert/strict";
import test from "node:test";
import { NO_OBJECTIVES, applyObjectiveChange, draftChange, moveChange, objectiveDetails, objectivesCount, runningByObjective, sameObjectives, taskObjective } from "./remObjectives.js";

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

test("moving stops at the ends of the list", () => {
  assert.equal(moveChange(items, "a", -1), null);
  assert.deepEqual(moveChange(items, "a", 1), { action: "move", id: "a", index: 1 });
  assert.equal(moveChange(items, "c", 1), null);
  assert.equal(moveChange(items, "missing", 1), null);
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
  const moved = applyObjectiveChange(view, { action: "move", id: "c", index: 0 });
  assert.deepEqual(moved.items.map(item => item.id), ["c", "a", "b"]);
  const checked = applyObjectiveChange(view, { action: "check", id: "a" }, stamp);
  assert.equal(checked.items[0].done, true);
  assert.equal(checked.done, 2);
  const reopened = applyObjectiveChange(view, { action: "uncheck", id: "b" });
  assert.equal(reopened.items[1].done, false);
  assert.equal("done_evidence" in reopened.items[1], false);
  assert.deepEqual(applyObjectiveChange(view, { action: "remove", id: "a" }).items.map(item => item.id), ["b", "c"]);
  assert.equal(applyObjectiveChange(view, { action: "edit", id: "missing", text: "x" }), view);
});
