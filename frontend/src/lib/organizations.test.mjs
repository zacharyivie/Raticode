import assert from "node:assert/strict";
import { test } from "node:test";
import { historyByDate, teamMembers, teamGroups, projectPeople, employeeTree, reportingHierarchy, eligibleManagers, configurationChanges, newEmployee, taskGroups, changeLabel, activityEntries, actorLabel, formatUsd, usageCost, plural, initials, relativeDay } from "./organizations.js";

test("configuration revisions group by local calendar date, retaining revision order", () => {
  const history = [
    { revision: 3, at: "2026-09-25T12:00:00Z" },
    { revision: 2, at: "2026-09-25T11:00:00Z" },
    { revision: 1, at: "2026-09-24T12:00:00Z" },
  ];
  assert.deepEqual(historyByDate(history).map(bucket => bucket.revisions.map(r => r.revision)), [[3, 2], [1]]);
});
test("employee tree follows managers without dropping orphaned or cyclic legacy records", () => {
  const tree = employeeTree([{ id: "dev", reportsTo: "lead" }, { id: "lead" }, { id: "legacy", reportsTo: "missing" }]);
  assert.deepEqual(tree.map(e => [e.id, e.depth]), [["lead", 0], ["dev", 1], ["legacy", 0]]);
});
test("restore preview describes nested changes and removed fields", () => {
  assert.deepEqual(configurationChanges({ name: "New", options: { enabled: true } }, { name: "Old" }), [
    { path: "/name", before: "New", after: "Old" }, { path: "/options", before: { enabled: true }, after: undefined },
  ]);
});
test("new employees own independent resource selections", () => {
  const defaults = { resources: { skills: [{ path: "/skills/review" }], mcpServers: [] } };
  const employee = newEmployee(defaults); employee.resources.skills[0].path = "/changed";
  assert.equal(defaults.resources.skills[0].path, "/skills/review");
});
test("task groups lead with plans awaiting approval and skip empty lifecycle states", () => {
  const groups = taskGroups([
    { id: "a", status: "todo" }, { id: "b", status: "todo", approvalRequired: true, approvedRevision: null },
    { id: "c", status: "done", approvalRequired: true, approvedRevision: null }, { id: "d", status: "in_progress" },
  ]);
  assert.deepEqual(groups.map(group => [group.id, group.tasks.map(task => task.id)]), [["approval", ["b"]], ["in_progress", ["d"]], ["todo", ["a"]], ["done", ["c"]]]);
});
test("change labels name employees and fields instead of raw identifiers", () => {
  const config = { employees: [{ id: "mira", name: "Mira Santos" }] };
  assert.equal(changeLabel("/employees/mira/heartbeatSeconds", config), "Employees › Mira Santos › Heartbeat");
  assert.equal(changeLabel("/employees/gone/name", config, { employees: [{ id: "gone", name: "Former" }] }), "Employees › Former › Name");
  assert.equal(changeLabel("/packageFiles/skills/review/SKILL.md"), "Package files › skills/review/SKILL.md");
  assert.equal(changeLabel("/"), "Whole configuration");
});
test("activity collapses provider updates per turn and names actors", () => {
  const tasks = [{ id: "t1", title: "Ship" }];
  const entries = activityEntries([
    { sequence: 1, at: "2026-09-25T10:00:00Z", actor: "system", kind: "dispatch", payload: { taskId: "t1", turnId: "u1", employeeId: "mira" } },
    { sequence: 2, at: "2026-09-25T10:00:01Z", actor: "employee:mira", kind: "turn_event", payload: { turnId: "u1", taskId: "t1", event: {} } },
    { sequence: 3, at: "2026-09-25T10:00:02Z", actor: "employee:mira", kind: "turn_event", payload: { turnId: "u1", taskId: "t1", event: {} } },
    { sequence: 4, at: "2026-09-25T10:00:03Z", actor: "employee:mira", kind: "turn_finished", payload: { turnId: "u1", taskId: "t1", status: "in_review" } },
  ], tasks);
  assert.deepEqual(entries.map(entry => entry.text), ["Finished a turn on “Ship”", "Shared 2 updates on “Ship”", "Started a turn on “Ship”"]);
  assert.equal(actorLabel(entries[2].actor, [{ id: "mira", name: "Mira" }]), "Mira");
  assert.equal(actorLabel("Rem:thread"), "Rem");
  assert.equal(actorLabel("user"), "You");
});
test("formatting helpers keep small costs visible and pluralize counts", () => {
  assert.equal(formatUsd(0), "$0.00");
  assert.equal(formatUsd(0.0042), "$0.0042");
  assert.equal(formatUsd(12.5), "$12.50");
  assert.equal(formatUsd(undefined), "Unknown");
  assert.equal(plural(1, "turn"), "1 turn");
  assert.equal(plural(3, "turn"), "3 turns");
  assert.equal(initials("Theo Brandt-Nakamura"), "TB");
  assert.equal(initials("Ada"), "AD");
  assert.equal(relativeDay("2026-09-24T12:00:00", new Date("2026-09-25T09:00:00")), "Yesterday");
});

test("usage never displays an initialized zero as reported cost for unknown turns", () => {
  assert.equal(usageCost({ turns: 2, unknownCostTurns: 2, costUsd: 0 }), "Unknown");
  assert.equal(usageCost({ turns: 2, unknownCostTurns: 1, costUsd: 0.03 }), "$0.03");
  assert.equal(usageCost({ turns: 1, unknownCostTurns: 0, costUsd: 0 }), "$0.00");
  assert.equal(usageCost(), "Unknown");
});


test("org chart nests reporting lines and retains each employee exactly once", () => {
  const employees = [
    { id: "dev", reportsTo: "lead" }, { id: "owner", reportsTo: null },
    { id: "lead", reportsTo: "owner" }, { id: "peer", reportsTo: "owner" },
    { id: "orphan", reportsTo: "missing" }, { id: "cycle", reportsTo: "cycle" },
  ];
  const roots = reportingHierarchy(employees);
  assert.deepEqual(roots.map(e => e.id), ["owner", "orphan", "cycle"]);
  assert.deepEqual(roots[0].children.map(e => e.id), ["lead", "peer"]);
  assert.equal(roots[0].children[0].children[0].id, "dev");
  assert.deepEqual(reportingHierarchy([]), []);
  assert.equal(employees[0].children, undefined);
});

test("reports-to choices exclude self and all descendants but allow another branch", () => {
  const employees = [
    { id: "ceo" }, { id: "lead", reportsTo: "ceo" },
    { id: "dev", reportsTo: "lead" }, { id: "intern", reportsTo: "dev" },
    { id: "peer", reportsTo: "ceo" },
  ];
  assert.deepEqual(eligibleManagers(employees, "lead").map(e => e.id), ["ceo", "peer"]);
  assert.deepEqual(eligibleManagers(employees, "ceo"), []);
  assert.equal(eligibleManagers(employees, "new-employee").length, 5);
});

test("team groups retain reporting lines under external managers and include unassigned employees", () => {
  const employees = [{ id: "dev", reportsTo: "lead" }, { id: "lead", reportsTo: "ceo" }, { id: "ceo" }, { id: "other" }];
  const teams = [{ id: "eng", manager: "lead" }, { id: "empty" }];
  const groups = teamGroups({ employees, teams });
  assert.deepEqual(groups.map(group => [group.team?.id, group.employees.map(e => e.id)]), [["eng", ["dev", "lead"]], ["empty", []], [undefined, ["ceo", "other"]]]);
  const roots = reportingHierarchy(groups[0].employees);
  assert.equal(roots[0].id, "lead");
  assert.equal(roots[0].reportsTo, "ceo");
  assert.equal(roots[0].children[0].id, "dev");
  assert.deepEqual(teamMembers(employees, { manager: "missing" }), []);
  assert.deepEqual(teamMembers([{ id: "a", reportsTo: "b" }, { id: "b", reportsTo: "a" }], { manager: "a" }).map(e => e.id), ["a", "b"]);
});

test("project people includes the owner and distinct task assignees only for this project", () => {
  const employees = [{ id: "owner" }, { id: "dev" }, { id: "other" }];
  const tasks = [{ project: "p", assignee: "owner" }, { project: "p", assignee: "dev" }, { project: "p", assignee: "dev", status: "done" }, { project: "q", assignee: "other" }, { project: "p", assignee: "removed" }];
  assert.deepEqual(projectPeople(employees, { id: "p", owner: "owner" }, tasks), [{ id: "owner", assignment: "Initiative owner, Task assignee" }, { id: "dev", assignment: "Task assignee" }]);
  assert.deepEqual(projectPeople(employees, { id: "empty" }, tasks), []);
});

test("work filters keep only actionable attention and combine owner and search", async () => {
  const { filterWork } = await import("./organizations.js");
  const tasks = [
    { id: "a", title: "Ship", description: "release", status: "todo", assignee: "mira", approvalRequired: true, approvedRevision: null },
    { id: "b", title: "Ship old", status: "done", assignee: "mira", approvalRequired: true, approvedRevision: null },
    { id: "c", title: "Test", status: "in_review", assignee: "theo" },
  ];
  assert.deepEqual(filterWork(tasks, { attention: true }).map(t => t.id), ["a", "c"]);
  assert.deepEqual(filterWork(tasks, { attention: true, query: "RELEASE", assignee: "mira" }).map(t => t.id), ["a"]);
});
test("incremental activity retains earlier entries and deduplicates retried pages", async () => {
  const { appendActivity } = await import("./organizations.js");
  const first = [{ sequence: 1 }, { sequence: 2 }];
  assert.deepEqual(appendActivity(first, [{ sequence: 2 }, { sequence: 3 }]).map(e => e.sequence), [1, 2, 3]);
  assert.equal(first.length, 2);
  const entries = activityEntries([{ sequence: 4, actor: "user", kind: "comment", payload: { taskId: "a", comment: { body: "Compact comment" } } }]);
  assert.equal(entries[0].detail, "Compact comment");
});
