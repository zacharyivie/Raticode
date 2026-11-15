import assert from "node:assert/strict";
import { test } from "node:test";
import { autoApproveHint, autoApproveWarning, hasAutoApprove } from "./remKanban.js";
import { activityLine, assignmentLine, formatTokens, selfWorkLimitsLine, usageSentence, usageShort, autoPullHint, avatarColor, boardOn, cappedThreads, canDropOn, checksLine, columnTickets, entryHeadline, entryTone, filterTickets, initials, modeLabel, ownerFilters, participantOf, remProjectChoices, savedSwitchNote, scopeNote, selfWorkLine, selfWorkWindows, visibleEntries, windowDetail, withProject } from "./remKanban.js";

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

test("a paused ticket can only go back to In progress, which resumes it", () => {
  const paused = ticket({ status: "blocked", paused: "priority" });
  assert.equal(canDropOn(paused, "in_progress"), true);
  for (const column of ["todo", "backlog", "done"]) assert.equal(canDropOn(paused, column), false);
  assert.equal(activityLine({ title: "Fix export", project: "app", paused: "priority" }), "Fix export · app · Paused for your work");
  assert.equal(activityLine({ title: "Fix export", project: "app", paused: "user" }), "Fix export · app · Paused");
  assert.equal(activityLine({ title: "Fix export", project: "app", paused: "" }), "Fix export · app");
});

test("timed self-work windows show only when the desktop sends a schedule", () => {
  const settings = { idle_minutes: 15, enabled: true };
  const active = { id: "w1", label: "Until 7:00 PM today (Asia/Irkutsk)", end_label: "7:00 PM Thu 12 Nov (Asia/Irkutsk)", after_label: "then back to off", after_end: "restore" };
  const upcoming = { id: "w2", label: "11:00 PM today to 7:00 AM Fri", end_label: "when 2 running tasks finish", after_label: "" };
  // Older desktops send no schedule and no saved switch: nothing new shows.
  assert.equal(selfWorkWindows({ settings, state: {} }), null);
  assert.equal(savedSwitchNote({ settings, state: {} }), "");
  const view = { settings: { ...settings, saved_enabled: false }, state: { mode: "waiting", starts_at: "x", detail: "" },
    schedule: { timezone: "Asia/Irkutsk", active, upcoming: [upcoming], last_period: { id: "p", ended_at: "2026-11-12T11:00:00Z", summary: "Started 3 tasks, finished 2." } } };
  assert.deepEqual(selfWorkWindows(view), { active, upcoming: [upcoming], last: view.schedule.last_period });
  assert.equal(selfWorkWindows({ schedule: { active: null, upcoming: [], last_period: { summary: "" } } }).last, null);
  assert.equal(windowDetail(active), "Ends 7:00 PM Thu 12 Nov (Asia/Irkutsk), then back to off");
  assert.equal(windowDetail(upcoming), "Ends when 2 running tasks finish");
  // Waiting for quiet minutes inside a window still says when the window ends.
  assert.equal(selfWorkLine(view), "Starts after 15 quiet minutes · ends 7:00 PM Thu 12 Nov (Asia/Irkutsk)");
  // The switch shows the window; a note says what it goes back to.
  assert.equal(savedSwitchNote(view), "On for this window; goes back to off after it.");
  assert.equal(savedSwitchNote({ ...view, schedule: { ...view.schedule, active: { ...active, after_end: "on" } } }), "On for this window; stays on after it.");
  assert.equal(savedSwitchNote({ ...view, settings: { ...settings, saved_enabled: true } }), "");
});

test("ticket usage says unknown when a provider didn't report it", () => {
  assert.equal(usageShort(undefined), "");
  assert.equal(usageShort({ calls: 0, total_tokens: null }), "");
  assert.equal(usageShort({ calls: 3, reported_calls: 3, unknown_calls: 0, total_tokens: 12_400 }), "12.4k tokens");
  assert.equal(usageShort({ calls: 3, reported_calls: 2, unknown_calls: 1, total_tokens: 950 }), "950 tokens + 1 unknown");
  assert.equal(usageShort({ calls: 2, unknown_calls: 2, total_tokens: null }), "Tokens unknown");
  assert.equal(usageSentence({ calls: 0 }), "No model calls yet.");
  assert.equal(usageSentence({ calls: 1, unknown_calls: 1, total_tokens: null }), "Unknown: the provider didn't report usage for its 1 model call.");
  assert.equal(usageSentence({ calls: 2, reported_calls: 2, total_tokens: 1500 }), "1,500 tokens over 2 model calls.");
  assert.match(usageSentence({ calls: 3, reported_calls: 2, unknown_calls: 1, total_tokens: 1500 }), /1 of them was not fully reported/);
  assert.equal(formatTokens(3_200_000), "3.2M");
  assert.equal(formatTokens(2_000), "2k");
});

test("self-work limits read as one line", () => {
  assert.match(selfWorkLimitsLine({ session_token_limit: 0, daily_token_limit: 0, rate_limit_percent: 0 }), /^No limits/);
  assert.equal(
    selfWorkLimitsLine({ session_token_limit: 500_000, daily_token_limit: 2_000_000, rate_limit_percent: 80 }),
    "Stops starting new work past 500k tokens a session or 2M tokens a day or at 80% of a rate limit. Running work finishes normally.",
  );
  assert.equal(selfWorkLimitsLine({ rate_limit_percent: 90 }), "Stops starting new work at 90% of a rate limit. Running work finishes normally.");
});

test("auto-approval explains what it does and is hidden from older desktops", () => {
  assert.equal(hasAutoApprove({ settings: { enabled: true } }), false);
  assert.equal(hasAutoApprove({ settings: { auto_approve_proposals: false } }), true);
  assert.match(autoApproveHint({ state: { board_enabled: false }, settings: { auto_approve_proposals: true } }), /^Needs the Kanban board/);
  assert.match(autoApproveHint({ state: {}, settings: { auto_approve_proposals: false } }), /wait in Backlog for your approval/);
  const on = { state: { auto_approved_session: 13 }, limits: {}, settings: { auto_approve_proposals: true, auto_pull_backlog: false } };
  assert.match(autoApproveHint(on), /until self-work ends \(13 this session\)\. They wait in Backlog until you move them/);
  assert.match(autoApproveHint({ ...on, settings: { ...on.settings, auto_pull_backlog: true } }), /Rem ranks them and works on the best ones/);
  const warning = autoApproveWarning(on).join(" ");
  assert.match(warning, /for as long as self-work runs, without you reading them first/);
  // No per-session cap is promised anywhere.
  assert.doesNotMatch([autoApproveHint(on), warning].join(" "), /up to \d+|a session,|limit/);
  assert.match(warning, /Backlog pulling is off/);
  assert.match(warning, /Ideas already waiting for you stay yours to decide/);
});

test("a card says what its ticket still waits for", async () => {
  const { waitsLine } = await import("./remKanban.js");
  const waits = [
    { ticket_id: "a", key: "REM-1", title: "Polling triggers", status: "todo" },
    { ticket_id: "b", key: "REM-2", title: "Webhooks", status: "done" },
    { ticket_id: "c", key: "REM-3", title: "Editor", status: "cancelled" },
  ];
  assert.equal(waitsLine(ticket({ waits_for: waits })), "Waits for: Polling triggers and 1 more");
  assert.equal(waitsLine(ticket({ waits_for: waits.slice(0, 2) })), "Waits for: Polling triggers");
  assert.equal(waitsLine(ticket({ waits_for: [waits[1]] })), "");
  assert.equal(waitsLine(ticket()), "");
});

test("a ticket waits only for tickets with the same owner, each once", async () => {
  const { waitChoices, waitsChange, canEditWaits } = await import("./remKanban.js");
  const me = ticket({ ticket_id: "me", key: "REM-1", waits_for: [{ ticket_id: "listed" }], revision: 4 });
  const board = { tickets: [
    me,
    ticket({ ticket_id: "listed", key: "REM-2" }),
    ticket({ ticket_id: "ten", key: "REM-10" }),
    ticket({ ticket_id: "three", key: "REM-3", status: "done" }),
    ticket({ ticket_id: "gone", key: "REM-4", status: "cancelled" }),
    ticket({ ticket_id: "org", key: "ENG-1", scope: "organization", organization_id: "eng" }),
  ] };
  assert.deepEqual(waitChoices(board, me).map(t => t.ticket_id), ["three", "ten"]);
  const orgTicket = ticket({ ticket_id: "o2", key: "ENG-2", scope: "organization", organization_id: "eng" });
  assert.deepEqual(waitChoices({ tickets: [...board.tickets, orgTicket] }, orgTicket).map(t => t.ticket_id), ["org"]);
  assert.deepEqual(waitsChange(me, ["ten"]), { action: "edit", ticket_id: "me", scope: "rem", organization_id: undefined, expected_revision: 4, waits_for: ["ten"] });
  assert.equal(canEditWaits(me), true);
  assert.equal(canEditWaits(ticket({ running: true })), true);
  assert.equal(canEditWaits(ticket({ scope: "organization", running: true })), false);
  assert.equal(canEditWaits(ticket({ status: "done" })), false);
  assert.equal(canEditWaits(ticket({ proposed: true, status: "backlog" })), false);
});
