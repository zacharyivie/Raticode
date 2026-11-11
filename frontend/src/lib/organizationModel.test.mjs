import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activityFeed, buildModel, describeHours, hoursStatus, claimSummary, columnOf, epicProgress, epicState, knowledgeEdges, layoutGraph, milestoneProgress,
  moveRule, needsYou, orgMilestoneProgress, removeWorker, projectProgress, reviewCaution, runSteps, searchEntities, startHint, suggestedLinks, workerStatus,
} from "./organizationModel.js";

const task = (id, status, more = {}) => ({ id, title: `Task ${id}`, status, number: Number(id.slice(1)), key: `STU-${id.slice(1)}`, revision: 1, comments: [], knowledge: [], ...more });

function organization() {
  return {
    revision: 3,
    config: {
      name: "Studio", projectRoots: ["/p"],
      employees: [{ id: "mira", name: "Mira Santos" }, { id: "sage", name: "Sage Kim", paused: true }],
      projects: [{ id: "desktop", name: "Desktop", owner: "mira", checks: [{ name: "unit", command: "npm test" }] }],
      milestones: [{ id: "M1", title: "Evidence" }, { id: "PM1", title: "Orgs 2.0", projectId: "desktop", parentId: "M1" }],
      epics: [{ id: "E1", title: "Board", milestoneId: "PM1", projectId: "desktop" }, { id: "E2", title: "Receipts", milestoneId: "PM1", projectId: "desktop" }],
    },
    runtime: {
      tasks: [
        task("t1", "done", { epicId: "E1", project: "desktop", assignee: "mira" }),
        task("t2", "in_review", { epicId: "E1", project: "desktop", assignee: "mira", knowledge: ["k1"] }),
        task("t3", "blocked", { epicId: "E2", project: "desktop", blockReason: "The last run failed" }),
        task("t4", "cancelled", { epicId: "E2", project: "desktop" }),
        task("t5", "in_progress", { epicId: "E2", project: "desktop", assignee: "sage" }),
      ],
      claims: {
        c1: { id: "c1", taskId: "t2", text: "Tests pass", at: "2026-11-05T10:00:00Z", evidence: { kind: "check", state: "ok" } },
        c2: { id: "c2", taskId: "t2", text: "Fast", at: "2026-11-05T10:01:00Z", evidence: { kind: null, state: "none" } },
        c3: { id: "c3", taskId: "t2", text: "Old", at: "2026-11-05T09:00:00Z", supersededAt: "x", evidence: { state: "bad" } },
      },
      knowledge: {
        k1: { id: "k1", kind: "decision", title: "Board columns are fixed", summary: "Backlog todo running review done columns fixed", topic: "Board", links: ["k2"] },
        k2: { id: "k2", kind: "spec", title: "Card anatomy", summary: "Cards show key title owner", topic: "Board", links: [] },
        k3: { id: "k3", kind: "note", title: "Review columns stay fixed", summary: "Review columns fixed board done", topic: "Board", links: [] },
        k4: { id: "k4", kind: "note", title: "Archived", summary: "", archived: true, links: [] },
      },
      links: {
        l1: { id: "l1", kind: "read", from: "ticket:t2", to: "kb:k2" },
        l2: { id: "l2", kind: "asserted", from: "kb:k2", to: "kb:k3", quote: "x" },
        l3: { id: "l3", kind: "asserted", from: "kb:k1", to: "kb:k3", removed: { at: "x" } },
      },
      attempts: { a1: { id: "a1", taskId: "t2", employeeId: "mira", startedAt: "2026-11-05T09:00:00Z" } },
      consentRequests: { r1: { id: "r1", status: "pending", at: new Date().toISOString(), summary: "Start" } },
    },
  };
}

test("tickets map task states to the five board columns and leave cancelled work off the board", () => {
  assert.deepEqual(["backlog", "todo", "blocked", "in_progress", "in_review", "done", "cancelled"].map(status => columnOf({ status })), ["backlog", "todo", "todo", "running", "review", "done", null]);
  const model = buildModel(organization());
  assert.deepEqual(Object.keys(model.tickets), ["t1", "t2", "t3", "t5"]);
  assert.equal(model.tickets.t3.flag, "The last run failed");
  assert.deepEqual(model.tickets.t2.used, ["k1", "k2"]);
  assert.ok(model.tickets.t2.observed.has("k2") && !model.tickets.t2.observed.has("k1"));
  assert.deepEqual(model.tickets.t2.claims.map(claim => claim.id), ["c1", "c2"]);
  assert.ok(!model.knowledge.k4);
  assert.deepEqual(Object.keys(model.orgMilestones), ["M1"]);
});

test("progress counts accepted tickets and rolls up through epics, milestones and projects", () => {
  const model = buildModel(organization());
  assert.equal(epicProgress(model, "E1"), 0.5);
  assert.equal(epicProgress(model, "E2"), 0);
  assert.equal(milestoneProgress(model, "PM1"), 0.25);
  assert.equal(orgMilestoneProgress(model, "M1"), 0.25);
  assert.equal(projectProgress(model, "desktop"), 0.25);
  assert.equal(epicState(model, "E1"), "active");
});

test("claims summarize as verified, changed, failed, pending and unproven, and risky reviews say why", () => {
  const claims = [{ evidence: { state: "ok" } }, { evidence: { state: "warn" } }, { evidence: { state: "bad" } }, { evidence: { state: "queued" } }, { evidence: { state: "unverified" } }, {}];
  assert.deepEqual(claimSummary(claims), { ok: 1, warn: 1, bad: 1, none: 2, pending: 1 });
  assert.equal(reviewCaution(claims).text, "1 failed, 1 source changed, 2 without verified evidence, 1 not checked yet");
  assert.equal(reviewCaution([{ evidence: { state: "ok" } }]).risky, false);
});

test("board moves pull work into In progress and keep Done for reviewed work", () => {
  const ticket = column => ({ column, status: { backlog: "backlog", todo: "todo", review: "in_review", done: "done", running: "in_progress" }[column] });
  assert.deepEqual(moveRule(ticket("backlog"), "todo"), { ok: true, status: "todo" });
  // In progress means "start this next": the worker runs it as soon as they're free.
  assert.deepEqual(moveRule(ticket("todo"), "running"), { ok: true, pull: true });
  assert.deepEqual(moveRule(ticket("backlog"), "running"), { ok: true, pull: true });
  assert.equal(moveRule({ column: "todo", status: "blocked" }, "running").ok, false);
  assert.equal(moveRule(ticket("review"), "running").ok, false);
  assert.equal(moveRule(ticket("done"), "running").ok, false);
  // Work in Review goes back through Request changes, which records the review.
  assert.equal(moveRule(ticket("review"), "todo").ok, false);
  assert.equal(moveRule(ticket("review"), "backlog").ok, false);
  assert.equal(moveRule(ticket("todo"), "done").ok, false);
  assert.equal(moveRule(ticket("todo"), "review").ok, false);
  assert.equal(moveRule(ticket("running"), "backlog").ok, false);
  assert.deepEqual(moveRule(ticket("review"), "done"), { ok: true, review: true });
  assert.deepEqual(moveRule(ticket("done"), "todo"), { ok: true, reopen: true, status: "todo" });
  assert.deepEqual(moveRule({ column: "todo", status: "blocked" }, "backlog"), { ok: true, reopen: true, status: "backlog" });
});

test("needs-you lists Rem's requests, reviews and flagged work; workers show what they're doing", () => {
  const model = buildModel(organization());
  assert.deepEqual(needsYou(model).map(item => item.type), ["request", "review", "flag"]);
  assert.deepEqual(needsYou(buildModel({ ...organization(), runtime: { ...organization().runtime, tasks: [...organization().runtime.tasks].reverse() } })).map(item => item.type), ["request", "review", "flag"]);
  assert.deepEqual(workerStatus(model, "mira"), ["needs", "To review"]);
  assert.deepEqual(workerStatus(model, "sage"), ["paused", "Held"]);
});

test("knowledge edges combine written and quoted links with suggestions for similar notes", () => {
  const model = buildModel(organization());
  const edges = knowledgeEdges(model);
  assert.deepEqual(edges.filter(edge => edge[2] !== "suggested").map(edge => edge.slice(0, 3)), [["k1", "k2", "written"], ["k2", "k3", "asserted"]]);
  assert.deepEqual(suggestedLinks(model.knowledge, [["k1", "k2"], ["k2", "k3"]]), [["k1", "k3", "suggested"]]);
  assert.deepEqual(knowledgeEdges(model, { suggested: false }).length, 2);
});

test("search finds tickets by key and notes by title words", () => {
  const model = buildModel(organization());
  assert.deepEqual(searchEntities(model, "STU-2").map(item => item.id), ["t2"]);
  assert.deepEqual(searchEntities(model, "anatomy card").map(item => `${item.type}:${item.id}`), ["kb:k2"]);
});

test("run steps summarize stored trace events without repeating streamed updates", () => {
  const trace = (id, title, input, extra = {}) => ({ type: "thought", trace: { id, kind: "tool", title, input, ...extra } });
  const steps = runSteps([
    trace("1", "organization_action", { action: "knowledge_read" }), trace("1", "organization_action", { action: "knowledge_read" }),
    trace("2", "Read", {}), trace("3", "Grep", {}), trace("4", "Edit", {}), trace("5", "Edit", {}),
    trace("6", "Bash", { command: "npm test -- board" }, { shell: "bash" }), trace("7", "Bash", { command: "git commit -m x" }, { shell: "bash" }),
    { type: "thought", trace: { id: "6", kind: "tool", title: "Tool result", phase: "result" } },
    trace("8", "organization_action", { action: "claim_add" }), trace("9", "organization_action", { action: "claim_add" }),
    { type: "final" },
  ]);
  assert.deepEqual(steps.map(step => step.text), ["Consulted organization knowledge", "Looked through the project (2 reads)", "Edited files 2 times", "Ran npm test -- board", "Committed changes", "Posted 2 claims", "Finished with a summary"]);
});

test("recent activity names who did what to which ticket or note", () => {
  const model = buildModel(organization());
  const feed = activityFeed(model, [
    { sequence: 1, at: "2026-11-05T10:00:00Z", actor: "user", kind: "task_create", payload: { taskId: "t1" } },
    { sequence: 2, at: "2026-11-05T10:01:00Z", actor: "employee:mira", kind: "turn_finished", payload: { taskId: "t2", turnId: "a1" } },
    { sequence: 3, at: "2026-11-05T10:02:00Z", actor: "system", kind: "check_finished", payload: { taskId: "t2", state: "bad" } },
    { sequence: 4, at: "2026-11-05T10:03:00Z", actor: "Rem:thread", kind: "knowledge_save", payload: { entryId: "k1" } },
  ]);
  assert.deepEqual(feed.map(item => [item.who, item.text, item.ref, Boolean(item.bad)]), [
    ["Rem", "saved", "kb:k1", false], ["Raticode", "check failed on", "ticket:t2", true], ["mira", "finished a run on", "ticket:t2", false], ["you", "created", "ticket:t1", false],
  ]);
});

test("graph layout keeps every node inside the canvas and remembers dragged positions", () => {
  const nodes = ["a", "b", "c", "d"].map(id => ({ id }));
  const positions = { a: { x: 10, y: 10, fixed: true } };
  layoutGraph(nodes, [{ a: "a", b: "b", k: "written" }], 600, 400, positions, node => (node.id < "c" ? "One" : "Two"));
  assert.deepEqual(positions.a, { x: 10, y: 10, fixed: true });
  for (const id of ["b", "c", "d"]) assert.ok(positions[id].x >= 40 && positions[id].x <= 560 && positions[id].y >= 30 && positions[id].y <= 340, id);
});

test("start hints say plainly when a ticket will run and what it waits for", () => {
  const base = organization();
  const withReasons = (state, reasons) => buildModel({ ...base, runtime: { ...base.runtime, state }, diagnostics: { taskReasons: reasons } });
  const ticket = (model, id) => model.tickets[id];
  let model = withReasons("running", { t5: "Running", t2: "Waiting for independent review" });
  assert.equal(startHint(model, ticket(model, "t5")).text, "Sage Kim is working on it now.");
  assert.equal(startHint(model, ticket(model, "t2")).text, "Waiting for your review.");
  assert.equal(startHint(model, ticket(model, "t1")), null);
  const todo = { ...base, runtime: { ...base.runtime, state: "running", tasks: [...base.runtime.tasks, task("t6", "todo", { assignee: "mira" }), task("t7", "backlog", { assignee: "mira" })] } };
  model = buildModel({ ...todo, diagnostics: { taskReasons: { t6: "Ready" } } });
  assert.deepEqual(startHint(model, model.tickets.t6), { tone: "run", text: "Starts next: Mira Santos picks it up within a few seconds." });
  assert.match(startHint(model, model.tickets.t7).text, /^In Backlog, so it won't run/);
  model = buildModel({ ...todo, runtime: { ...todo.runtime, state: "paused" }, diagnostics: { taskReasons: { t6: "Organization is paused" } } });
  assert.equal(startHint(model, model.tickets.t6).text, "It starts when you start the organization; Mira Santos will pick it up.");
  for (const [reason, expected] of [
    ["Assign an employee", "No worker is assigned, so it won't run. Edit the ticket to assign one."],
    ["Queued: Mira Santos is working on STU-2", "Mira Santos is working on STU-2; this one starts after."],
    ["Waiting for STU-1, which was cancelled. Remove the dependency or reopen it.", "Waiting for STU-1, which was cancelled. Remove the dependency or reopen it."],
    ["Waiting for STU-1", "Waiting for STU-1."],
    ["Next up: Mira Santos is working on STU-2", "Next up. Mira Santos is working on STU-2; this one starts right after."],
  ]) {
    model = buildModel({ ...todo, diagnostics: { taskReasons: { t6: reason } } });
    assert.equal(startHint(model, model.tickets.t6).text, expected, reason);
  }
  model = buildModel({ ...todo, diagnostics: { taskReasons: { t6: "Waiting for STU-1, which was cancelled. Remove the dependency or reopen it." } } });
  assert.equal(startHint(model, model.tickets.t6).tone, "you");
});

test("working hours read plainly, and the header says when they open or close", () => {
  assert.equal(describeHours({ mode: "always" }), "24/7");
  assert.equal(describeHours({ mode: "scheduled", timezone: "America/New_York", windows: [{ days: [0, 1, 2, 3, 4], start: "09:00", end: "17:00" }, { days: [4], start: "22:00", end: "06:00" }] }),
    "Weekdays 09:00–17:00; Fri 22:00–06:00 (overnight), America/New_York");
  const base = organization();
  const at = hours => buildModel({ ...base, diagnostics: { taskReasons: { t5: "Off hours until 2026-11-16T14:00:00+00:00" }, hours } });
  assert.equal(hoursStatus(at({ mode: "always", open: true })), null);
  const closed = at({ mode: "scheduled", timezone: "America/New_York", open: false, opensAt: "2026-11-16T14:00:00+00:00" });
  assert.deepEqual(hoursStatus(closed), { open: false, text: "Off hours until Mon, 09:00 EST" });
  const open = at({ mode: "scheduled", timezone: "UTC", open: true, closesAt: "2026-11-16T17:00:00+00:00" });
  assert.deepEqual(hoursStatus(open), { open: true, text: "Working hours until Mon, 17:00 UTC" });
  const todo = { ...base, runtime: { ...base.runtime, tasks: [...base.runtime.tasks, task("t6", "todo", { assignee: "mira" })] } };
  const model = buildModel({ ...todo, diagnostics: { taskReasons: { t6: "Off hours until 2026-11-16T14:00:00+00:00" }, hours: { mode: "scheduled", timezone: "America/New_York" } } });
  assert.deepEqual(startHint(model, model.tickets.t6), { tone: "wait", text: "Outside working hours. It starts when they resume, Mon, 09:00 EST." });
});

test("stalled check-ins wait on you with the reason", () => {
  const base = organization();
  const model = buildModel({ ...base, diagnostics: { heartbeats: { mira: { stalled: { taskId: "t3", reason: "The last run failed" } }, ghost: { stalled: { reason: "x" } } } } });
  const stalls = needsYou(model).filter(item => item.type === "heartbeat");
  assert.deepEqual(stalls, [{ type: "heartbeat", worker: "mira", stall: { taskId: "t3", reason: "The last run failed" } }]);
});

test("removing a worker leaves a configuration that still saves", () => {
  const config = {
    employees: [{ id: "lead" }, { id: "mira", reportsTo: "lead" }, { id: "sam", reportsTo: "mira" }],
    projects: [{ id: "p", owner: "mira" }], teams: [{ id: "t", manager: "mira" }],
    epics: [{ id: "E1", owner: "mira" }], goalRecords: [{ id: "g", owner: "mira" }],
    executionTargets: [{ id: "x", employees: ["mira"] }, { id: "y", employees: ["mira", "lead"] }],
  };
  const next = removeWorker(config, "mira");
  assert.deepEqual(next.employees, [{ id: "lead" }, { id: "sam", reportsTo: "lead" }]);
  assert.deepEqual(next.projects, [{ id: "p" }]);
  assert.deepEqual(next.teams, [{ id: "t" }]);
  assert.equal(next.epics[0].owner, null);
  assert.equal(next.goalRecords[0].owner, null);
  assert.deepEqual(next.executionTargets, [{ id: "y", employees: ["lead"] }]);
});
