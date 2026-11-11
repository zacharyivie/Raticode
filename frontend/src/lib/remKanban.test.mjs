import assert from "node:assert/strict";
import { test } from "node:test";
import { activityLine, assignmentLine, autoPullHint, avatarColor, boardOn, cappedThreads, canDropOn, checksLine, columnTickets, entryHeadline, entryTone, filterTickets, initials, modeLabel, ownerFilters, participantOf, remProjectChoices, scopeNote, selfWorkLine, visibleEntries, withProject } from "./remKanban.js";

const ticket = (fields = {}) => ({ ticket_id: "t", key: "REM-1", title: "Export", scope: "rem", organization_id: "", status: "todo", priority: "medium", proposed: false, running: false, created_at: "2026-11-01", ...fields });

test("a filter by owner keeps Rem's and an organization's tickets apart", () => {
  const tickets = [ticket({ ticket_id: "a" }), ticket({ ticket_id: "b", scope: "organization", organization_id: "eng" })];
  assert.deepEqual(filterTickets(tickets, { owner: "rem" }).map(t => t.ticket_id), ["a"]);
  assert.deepEqual(filterTickets(tickets, { owner: "eng" }).map(t => t.ticket_id), ["b"]);
  assert.deepEqual(filterTickets(tickets, { owner: "all" }).map(t => t.ticket_id), ["a", "b"]);
});

test("cancelled tickets stay hidden until closed tickets are shown", () => {
  const tickets = [ticket({ status: "cancelled" })];
  assert.equal(filterTickets(tickets).length, 0);
  assert.equal(filterTickets(tickets, { closed: true }).length, 1);
});

test("search matches the title and key", () => {
  const tickets = [ticket({ title: "Dark mode", key: "ENG-4" }), ticket({ title: "Voice", key: "REM-9" })];
  assert.equal(filterTickets(tickets, { query: "eng-4" })[0].title, "Dark mode");
  assert.equal(filterTickets(tickets, { query: "  voice " })[0].title, "Voice");
});

test("the backlog follows Rem's ranking and puts tickets it left out last", () => {
  const grouped = columnTickets([
    ticket({ ticket_id: "out", status: "backlog", backlog_score: 12, skip_reason: "Already done" }),
    ticket({ ticket_id: "mid", status: "backlog", backlog_score: 5, priority: "critical" }),
    ticket({ ticket_id: "top", status: "backlog", backlog_score: 9, priority: "low" }),
    ticket({ ticket_id: "idea", status: "backlog", proposed: true }),
  ]);
  assert.deepEqual(grouped.backlog.map(t => t.ticket_id), ["idea", "top", "mid", "out"]);
});

test("activity names the work and its project", () => {
  assert.equal(activityLine({ title: "Fix login", project: "App" }), "Fix login · App");
  assert.equal(activityLine({ title: "Research new features for App", project: "App" }), "Research new features for App");
  assert.equal(activityLine({ title: "No folder", project: "" }), "No folder");
});

test("proposals lead the backlog, then the highest priority, then the newest", () => {
  const grouped = columnTickets([
    ticket({ ticket_id: "low", status: "backlog", priority: "low" }),
    ticket({ ticket_id: "proposal", status: "backlog", proposed: true }),
    ticket({ ticket_id: "high", status: "backlog", priority: "high" }),
  ]);
  assert.deepEqual(grouped.backlog.map(t => t.ticket_id), ["proposal", "high", "low"]);
  assert.deepEqual(grouped.done, []);
});

test("cards go only where the backend accepts them", () => {
  assert.equal(canDropOn(ticket(), "backlog"), true);
  assert.equal(canDropOn(ticket(), "todo"), false);
  assert.equal(canDropOn(ticket({ proposed: true, status: "backlog" }), "todo"), false);
  assert.equal(canDropOn(ticket({ running: true }), "backlog"), false);
  const review = ticket({ scope: "organization", status: "in_review" });
  assert.equal(canDropOn(review, "backlog"), false);
  assert.equal(canDropOn(ticket({ scope: "organization" }), "done"), false);
});

test("owner filters list Rem and each organization", () => {
  assert.deepEqual(ownerFilters({ organizations: [{ organization_id: "eng", name: "Eng" }] }), [["all", "Everyone"], ["rem", "Rem"], ["eng", "Eng"]]);
  assert.deepEqual(ownerFilters(null), [["all", "Everyone"], ["rem", "Rem"]]);
});

test("the status line reads what self-work is doing", () => {
  const settings = { idle_minutes: 15, max_threads: 4 };
  assert.equal(selfWorkLine({ settings, state: { mode: "waiting", starts_at: "x", detail: "" } }), "Starts after 15 quiet minutes");
  assert.equal(selfWorkLine({ settings, state: { mode: "working", detail: "2 threads working." } }), "2 threads working.");
  assert.equal(selfWorkLine(null), "");
  assert.equal(modeLabel("working"), "Working");
  assert.equal(modeLabel("nonsense"), "Waiting");
});

test("a cap from Rem's worker limit is reported only when it applies", () => {
  assert.equal(cappedThreads({ state: { thread_limit: 2 }, settings: { max_threads: 4 } }), 2);
  assert.equal(cappedThreads({ state: { thread_limit: 4 }, settings: { max_threads: 4 } }), null);
});

const rem = { participant_id: "rem", kind: "rem", name: "Rem", projects: [], unavailable: [] };
const agent = { participant_id: "agent:eng:lead", kind: "agent", name: "Avery Lane", organization_id: "eng", employee_id: "lead", projects: ["/a", "/b"], unavailable: ["/b"] };

test("people match their tickets, and filtering by one person leaves only theirs", () => {
  const mine = ticket({ ticket_id: "a" });
  const theirs = ticket({ ticket_id: "b", scope: "organization", organization_id: "eng", assignee_id: "lead" });
  const other = ticket({ ticket_id: "c", scope: "organization", organization_id: "eng", assignee_id: "" });
  assert.equal(participantOf(mine, [rem, agent]), rem);
  assert.equal(participantOf(theirs, [rem, agent]), agent);
  assert.equal(participantOf(other, [rem, agent]), null);
  assert.deepEqual(filterTickets([mine, theirs, other], { participant: agent }).map(t => t.ticket_id), ["b"]);
  assert.deepEqual(filterTickets([mine, theirs, other], { participant: rem }).map(t => t.ticket_id), ["a"]);
});

test("an avatar has initials and the same color each time", () => {
  assert.equal(initials("Avery Lane"), "AL");
  assert.equal(initials("Rem"), "RE");
  assert.equal(initials("  "), "?");
  assert.equal(avatarColor("agent:eng:lead"), avatarColor("agent:eng:lead"));
});

test("a ticket says why self-work leaves it alone, only while it's waiting", () => {
  assert.match(scopeNote(ticket({ self_work_scope: "unassigned" })), /isn't assigned/);
  assert.match(scopeNote(ticket({ self_work_scope: "no_project" })), /No project/);
  assert.match(scopeNote(ticket({ self_work_scope: "unavailable" })), /available/);
  assert.equal(scopeNote(ticket({ self_work_scope: "assigned" })), "");
  assert.equal(scopeNote(ticket({ self_work_scope: "unassigned", status: "done" })), "");
  assert.equal(scopeNote(ticket({ self_work_scope: "unassigned", proposed: true })), "");
  assert.equal(scopeNote(ticket({ scope: "organization", self_work_scope: "" })), "");
});

test("a person's row counts only projects that can be used", () => {
  assert.match(assignmentLine(rem), /no self-work/);
  assert.equal(assignmentLine(agent), "Can work alone in 1 project");
  assert.match(assignmentLine({ projects: ["/b"], unavailable: ["/b"] }), /aren't available/);
});

test("timeline entries take their color from the column they moved to", () => {
  assert.equal(entryTone({ kind: "status", to: "blocked" }), "bad");
  assert.equal(entryTone({ kind: "status", to: "in_review" }), "review");
  assert.equal(entryTone({ kind: "status", to: "done" }), "done");
  assert.equal(entryTone({ kind: "status", to: "in_progress" }), "run");
  assert.equal(entryTone({ kind: "comment" }), "note");
  assert.equal(entryHeadline({ kind: "status", to: "cancelled", title: "" }), "Moved to Cancelled");
  assert.equal(entryHeadline({ kind: "status", to: "blocked", title: "Blocked: task failed" }), "Blocked: task failed");
  assert.equal(entryHeadline({ kind: "comment", title: "" }), "");
});

test("a long timeline keeps its newest entries and folds the rest", () => {
  const entries = Array.from({ length: 11 }, (_, index) => ({ id: String(index) }));
  const folded = visibleEntries(entries);
  assert.deepEqual(folded.shown.map(e => e.id), ["3", "4", "5", "6", "7", "8", "9", "10"]);
  assert.equal(folded.hidden, 3);
  assert.equal(visibleEntries(entries, true).shown.length, 11);
  assert.equal(visibleEntries(entries.slice(0, 4)).hidden, 0);
});

test("checks are summed up in one line", () => {
  assert.equal(checksLine([]), "");
  assert.equal(checksLine([{ result: "pass" }]), "The check passed");
  assert.equal(checksLine([{ result: "pass" }, { result: "pass" }]), "All 2 checks passed");
  assert.equal(checksLine([{ result: "pass" }, { result: "fail" }, { result: "unknown" }]), "1 of 3 checks passed");
});

test("self-work knows whether the board is on, and older desktops count as on", () => {
  assert.equal(boardOn({ state: { board_enabled: false } }), false);
  assert.equal(boardOn({ state: { board_enabled: true } }), true);
  assert.equal(boardOn({ state: {} }), true);
  assert.equal(boardOn(null), true);
  assert.match(autoPullHint({ state: { board_enabled: false }, settings: { auto_pull_backlog: true } }), /^Needs the Kanban board/);
  assert.match(autoPullHint({ state: {}, settings: { auto_pull_backlog: true } }), /highest-impact backlog/);
  assert.match(autoPullHint({ state: {}, settings: { auto_pull_backlog: false } }), /leaves the backlog alone/);
});

test("Rem's project choices list what it can have and keep what it has", () => {
  const board = {
    participants: [{ kind: "rem", participant_id: "rem", projects: ["/w/gone", "/w/app"], assignable: ["/w/app", "/w/site"], unavailable: ["/w/gone"], revision: 2 }],
    projects: [{ root: "/w/app", name: "app" }, { root: "/w/site", name: "site" }],
  };
  assert.deepEqual(remProjectChoices(board), [
    { root: "/w/app", name: "app", checked: true, assignable: true, unavailable: false },
    { root: "/w/site", name: "site", checked: false, assignable: true, unavailable: false },
    { root: "/w/gone", name: "gone", checked: true, assignable: false, unavailable: true },
  ]);
  assert.deepEqual(remProjectChoices({ participants: [] }), []);
  assert.deepEqual(remProjectChoices(null), []);
  assert.deepEqual(withProject(["/a"], "/b", true), ["/a", "/b"]);
  assert.deepEqual(withProject(["/a", "/b"], "/a", true), ["/a", "/b"]);
  assert.deepEqual(withProject(["/a", "/b"], "/a", false), ["/b"]);
});
