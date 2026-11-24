import test from "node:test";
import assert from "node:assert/strict";
import {
  STRATEGY_OPTIONS, moveInOrder, orderedComputers, placementBody, placementDraft, pressureText, roomText, runningBreakdown, runningText, runningWork,
  stateText, withComputer, withShared, workText,
} from "./computerPlacement.js";

const studio = { id: "a", name: "Studio", state: "online", max_threads: 4, running_threads: 2, capacity: 1, effective_threads: 3, cpu_pressure: 35.4, memory_pressure: 0 };
const laptop = { id: "b", name: "Laptop", state: "offline", max_threads: null, running_threads: 0, capacity: 0, effective_threads: 0, cpu_pressure: null, memory_pressure: null };
const tower = { id: "c", name: "Tower", state: "online", max_threads: 8, running_threads: 0, capacity: 1, effective_threads: 8, rejoining: true };

test("the three strategies use the words Settings shows for them", () => {
  assert.deepEqual(STRATEGY_OPTIONS.map(([value]) => value), ["overflow", "balanced", "manual"]);
  assert.deepEqual(STRATEGY_OPTIONS.map(([, label]) => label), ["Fill one computer, then the next", "Spread evenly", "Only computers I pick"]);
});

test("computers follow the saved order and ones it leaves out come last", () => {
  assert.deepEqual(orderedComputers([studio, laptop, tower], ["c", "a"]).map((c) => c.id), ["c", "a", "b"]);
  assert.deepEqual(orderedComputers([studio, laptop], []).map((c) => c.id), ["a", "b"]);
});

test("move up and move down swap neighbours and stop at the ends", () => {
  assert.deepEqual(moveInOrder(["a", "b", "c"], "c", -1), ["a", "c", "b"]);
  assert.deepEqual(moveInOrder(["a", "b", "c"], "a", 1), ["b", "a", "c"]);
  assert.equal(moveInOrder(["a", "b"], "a", -1), null);
  assert.equal(moveInOrder(["a", "b"], "b", 1), null);
  assert.equal(moveInOrder(["a"], "z", 1), null);
});

test("a manual placement sends its computers and shared ones, and waits for at least one computer", () => {
  let draft = placementDraft({ strategy: "manual", computers: [], shared: [] });
  assert.equal(placementBody("rem", draft), null);
  draft = withComputer(draft, "a", true);
  draft = withComputer(draft, "b", true);
  draft = withShared(draft, "b", true);
  assert.deepEqual(placementBody("rem", draft), { consumer: "rem", strategy: "manual", computers: ["a", "b"], shared: ["b"] });
  // Dropping a computer stops sharing it; a computer that isn't picked can't be shared.
  draft = withComputer(draft, "b", false);
  assert.deepEqual(draft.shared, []);
  assert.deepEqual(withShared(draft, "z", true), draft);
  assert.deepEqual(placementBody("org:x", { strategy: "balanced", computers: ["a"], shared: [] }), { consumer: "org:x", strategy: "balanced" });
  assert.deepEqual(placementDraft(undefined), { strategy: "overflow", computers: [], shared: [] });
});

test("each computer says its state, running of limit, room and pressure", () => {
  assert.equal(stateText(studio), "Online");
  assert.equal(stateText(laptop), "Offline");
  assert.equal(stateText(tower), "Back online");
  assert.equal(runningText(studio), "2 of 4 running");
  assert.equal(runningText(laptop), "0 running, limit not reported");
  assert.equal(roomText(studio), "Room for 1, pressure lowers its limit to 3");
  assert.equal(roomText(laptop), "No room while offline");
  assert.equal(roomText(tower), "Room for 1, one at a time after coming back");
  assert.equal(roomText({ ...studio, capacity: null }), "Room unknown");
  assert.equal(roomText({ ...studio, capacity: 0, memory_hold: "0.9 GB available" }), "No room: waiting for free memory (0.9 GB available)");
  assert.equal(pressureText(studio), "Pressure: CPU 35%, memory 0%");
  assert.equal(pressureText(laptop), "");
});

test("a computer's running workers read by kind, most first, only when they add up", () => {
  const full = { ...studio, this_computer: true, running_threads: 4, running_by_kind: { rem: 1, phone: 0, organizations: 2, swarms: 1 } };
  assert.equal(runningBreakdown(full), "2 organization workers, 1 Rem task, 1 swarm turn");
  assert.equal(runningText(full), "4 of 4 running: 2 organization workers, 1 Rem task, 1 swarm turn");
  assert.equal(runningText({ ...full, max_threads: null }), "4 running, limit not reported: 2 organization workers, 1 Rem task, 1 swarm turn");
  // An older paired desktop shares only its total; counts that disagree with it aren't shown.
  assert.equal(runningText({ ...full, running_by_kind: null }), "4 of 4 running");
  assert.equal(runningText({ ...full, running_by_kind: { rem: 1 } }), "4 of 4 running");
  assert.equal(runningText({ ...full, state: "offline" }), "0 of 4 running");
  assert.equal(runningBreakdown({ ...studio, running_threads: 0, running_by_kind: { rem: 0, phone: 0, organizations: 0, swarms: 0 } }), "");
});

test("only this computer lists what its workers are", () => {
  const work = [{ kind: "rem", label: "Fix the login page" }, { kind: "organizations", label: "Acme · Ada" }, { kind: "later", label: "Unknown kind" }];
  assert.deepEqual(runningWork({ ...studio, this_computer: true, running_work: work }), [
    { kind: "Rem task", label: "Fix the login page" },
    { kind: "organization worker", label: "Acme · Ada" },
    { kind: "worker", label: "Unknown kind" },
  ]);
  assert.deepEqual(runningWork({ ...studio, this_computer: false, running_work: work }), []);
  assert.deepEqual(runningWork({ ...studio, this_computer: true, running_work: null }), []);
});

test("a paired desktop says why work can't run there, or what it shares", () => {
  assert.equal(workText({ ...studio, this_computer: true }), null);
  // Offline and an unknown limit already show in the row.
  assert.equal(workText({ ...laptop, work_reason: "Laptop is offline" }), null);
  assert.deepEqual(workText({ ...tower, work_reason: "Update Raticode on Tower so it can say which of its projects match" }), {
    tone: "warning",
    text: "Update Raticode on Tower so it can say which of its projects match",
  });
  const shared = workText({
    ...tower,
    work_reason: null,
    catalog: { state: "shared", projects: [{ id: "p", name: "Raticode", fingerprint: "a".repeat(64) }], providers: ["claude_code", "codex"] },
  });
  assert.equal(shared.tone, "muted");
  assert.equal(
    shared.text,
    "Shares 1 project with this computer: Raticode; allows Claude Code, Codex. Each start also needs the same repository open there. Work doesn't start on other computers yet.",
  );
});
