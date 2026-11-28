import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { allowanceMode, allowancePaceLine, autoApproveHint, autoApproveWarning, hasAllowanceModes, hasAutoApprove, paceRateWarning, selfWorkAccountChange, selfWorkAccountRows, selfWorkToggle } from "./remKanban.js";
import { activityLine, assignmentLine, formatTokens, selfWorkLimitsLine, dailyAllowanceLine, usageSentence, usageShort, autoPullHint, avatarColor, boardOn, cappedThreads, canDropOn, canUndoMove, moveNotice, checksLine, columnTickets, entryHeadline, entryTone, filterTickets, initials, modeLabel, ownerFilters, participantOf, remProjectChoices, savedSwitchNote, scopeNote, selfWorkLine, selfWorkWindows, selfWorkStop, stopJudgmentLine, lastStopLine, stopPayload, visibleEntries, windowDetail, withProject } from "./remKanban.js";

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
  assert.equal(selfWorkLimitsLine({ daily_allowance_percent: 11 }), "Stops starting new work after 11% of a weekly allowance in a day. Running work finishes normally.");
  assert.equal(dailyAllowanceLine({ cap_percent: 11, message: "Used today: about 3% of 11% of work's weekly allowance. Resets at midnight." }), "Used today: about 3% of 11% of work's weekly allowance. Resets at midnight.");
  assert.equal(dailyAllowanceLine(undefined), "");
});

test("allowance use is one choice of Off, Daily cap or Pace to reset", () => {
  // A desktop from before the choice: its daily percent decides.
  assert.equal(hasAllowanceModes({ daily_allowance_percent: 11 }), false);
  assert.equal(allowanceMode({ daily_allowance_percent: 11 }), "daily");
  assert.equal(allowanceMode({ daily_allowance_percent: 0 }), "off");
  assert.equal(hasAllowanceModes({ allowance_mode: "pace" }), true);
  assert.equal(allowanceMode({ allowance_mode: "pace", daily_allowance_percent: 0 }), "pace");
  assert.equal(
    selfWorkLimitsLine({ allowance_mode: "pace", pace_aim_percent: 95 }),
    "Holds new work while ahead of a pace line to 95% by the weekly reset. Running work finishes normally.",
  );
  assert.equal(allowancePaceLine({ message: "Pacing Codex's weekly allowance: 23% used, line 24%. Up to 1 task can start." }), "Pacing Codex's weekly allowance: 23% used, line 24%. Up to 1 task can start.");
  assert.equal(allowancePaceLine(null), "");
  // The rate-limit stop set below the aim would stop self-work first.
  assert.equal(paceRateWarning({ allowance_mode: "pace", pace_aim_percent: 97, rate_limit_percent: 80 }), "Rate limit use stops self-work at 80%, before the pace aim of 97%.");
  assert.equal(paceRateWarning({ allowance_mode: "pace", pace_aim_percent: 97, rate_limit_percent: 0 }), "");
  assert.equal(paceRateWarning({ allowance_mode: "daily", rate_limit_percent: 50 }), "");
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

test("a card says which decisions its ticket violates", async () => {
  const { violatesLine } = await import("./remKanban.js");
  assert.equal(violatesLine(ticket({ violates_decisions: ["D-4"] })), "Violates D-4");
  assert.equal(violatesLine(ticket({ violates_decisions: ["D-4", "D-7", "D-9", "D-11", "D-12"] })), "Violates D-4, D-7, D-9 +2");
  // An older backend sends no marker.
  assert.equal(violatesLine(ticket({ violates_decisions: [] })), "");
  assert.equal(violatesLine(ticket()), "");
});

test("a card shows the Guardrail tag a retro put on its ticket", async () => {
  const { guardrailTag } = await import("./remKanban.js");
  assert.equal(guardrailTag(ticket({ tags: ["Guardrail"] })), "Guardrail");
  assert.equal(guardrailTag(ticket({ tags: ["Other"] })), "");
  // An older backend sends no tags.
  assert.equal(guardrailTag(ticket()), "");
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

test("a Rem ticket says which self-work objective it advances, or that self-work leaves it", async () => {
  const { objectiveLine } = await import("./remKanban.js");
  const serving = objectiveLine(ticket({ objective: { id: "o1", text: "Improve UI responsiveness" } }));
  assert.equal(serving.off, false);
  assert.equal(serving.short, "Improve UI responsiveness");
  assert.match(serving.detail, /Advances the self-work objective “Improve UI responsiveness”/);
  const left = objectiveLine(ticket({ status: "backlog", objective: null, objective_left: "It doesn't advance an open self-work objective. Unrelated work." }));
  assert.equal(left.off, true);
  assert.equal(left.short, "Not on an objective: self-work leaves it");
  assert.match(left.detail, /Unrelated work/);
  // Nothing to say without open objectives, for finished work, or on organization tickets.
  assert.equal(objectiveLine(ticket()), null);
  assert.equal(objectiveLine(ticket({ status: "done", objective: { id: "o1", text: "x" } })), null);
  assert.equal(objectiveLine(ticket({ scope: "organization", objective_left: "x" })), null);
});

test("self-work's account rows offer Rem's setting, the default sign-in, choose by usage and each account", () => {
  assert.deepEqual(selfWorkAccountRows(null), []);
  const [row] = selfWorkAccountRows({ accounts: [{ provider: "codex", label: "Codex", choice: "gone", rem_setting: "", accounts: ["work"] }] });
  assert.equal(row.choice, "gone");
  assert.deepEqual(row.options, [["", "Rem’s setting (Default sign-in)"], ["@default", "Default sign-in"], ["auto", "Choose by usage"], ["work", "work"], ["gone", "gone (removed)"]]);
  assert.equal(selfWorkAccountRows({ accounts: [{ provider: "claude_code", rem_setting: "auto", accounts: [] }] })[0].options[0][1], "Rem’s setting (Choose by usage)");
  assert.deepEqual(selfWorkAccountChange("codex", ""), { provider_accounts: { codex: "" } });
});


test("when self-work stops: the condition, the last check and what Save sends", () => {
  // An older desktop sends no stop.
  assert.equal(selfWorkStop({ settings: {} }), null);
  const stop = {
    revision: 4, active: true, condition: "REM-205 to REM-212 are done", no_later_than: "2026-11-21T05:00:00+00:00",
    no_later_than_label: "12:00 AM tomorrow (America/New_York)", label: "Stops when …",
    judgment: { met: false, evidence: "REM-210 is still in review.", at_label: "8:40 PM today (America/New_York)" },
    judging: false, judge_failed: false,
  };
  assert.equal(selfWorkStop({ stop }), stop);
  assert.equal(stopJudgmentLine(stop), "Last check 8:40 PM today (America/New_York): not met. REM-210 is still in review.");
  assert.equal(stopJudgmentLine({ ...stop, judging: true }), "Checking the condition now…");
  assert.equal(stopJudgmentLine({ ...stop, judgment: null, judge_failed: true }), "Not checked yet: Rem checks it after each self-work task finishes. The last check failed; Rem tries again in 10 minutes.");
  assert.equal(stopJudgmentLine({ ...stop, active: false }), "");
  assert.equal(lastStopLine({ condition: "REM-3 is done", stopped_by: "done_when", judgment: { evidence: "REM-3 is done." } }), "Stopped: “REM-3 is done” was met. REM-3 is done.");
  assert.equal(lastStopLine({ condition: "", stopped_by: "no_later_than", no_later_than_label: "12:00 AM today" }), "Stopped at its latest time, 12:00 AM today.");
  assert.equal(lastStopLine({ condition: "REM-3 is done", stopped_by: "" }), "Self-work ended before “REM-3 is done” was met.");
  assert.deepEqual(stopPayload(stop, { condition: " REM-1 is done ", time: "23:30", day: "tomorrow", keepTime: true }),
    { condition: "REM-1 is done", expected_revision: 4, no_later_than: { time: "23:30", day: "tomorrow" } });
  // An empty time keeps the latest time when one is set, and clears it otherwise.
  assert.deepEqual(stopPayload(stop, { condition: "x", time: "", day: "", keepTime: true }), { condition: "x", expected_revision: 4 });
  assert.deepEqual(stopPayload({ revision: 0 }, { condition: "x", time: "", day: "", keepTime: false }), { condition: "x", no_later_than: null });
});

test("the side pane's self-work button starts self-work now and stops it, and offers Start now while it waits", () => {
  assert.equal(selfWorkToggle(null), null);
  assert.equal(selfWorkToggle({ settings: {} }), null, "an older desktop without the switch shows no button");
  const off = { settings: { enabled: false, saved_enabled: false, idle_minutes: 15, revision: 7 }, state: { mode: "off", detail: "Self-work is off." } };
  assert.deepEqual(selfWorkToggle(off), {
    on: false, mode: "off", label: "Start self-work",
    detail: "Rem starts on its objectives now and keeps going until you stop it.",
    change: { start_now: true, expected_revision: 7 },
    startNow: null,
  });
  // Start starts now, as "start self-work now" in chat does, not the switch that waits for
  // quiet minutes; it sends no window, no stop condition and no objectives.
  assert.deepEqual(Object.keys(selfWorkToggle(off).change), ["start_now", "expected_revision"]);
  const next = { ...off, schedule: { active: null, upcoming: [{ id: "w2", label: "11:00 PM today to 7:00 AM Fri" }] } };
  assert.match(selfWorkToggle(next).detail, /Next window: 11:00 PM today to 7:00 AM Fri\.$/);

  const working = { settings: { enabled: true, saved_enabled: true, idle_minutes: 15, revision: 8 }, state: { mode: "working", detail: "Working on 2 tasks." } };
  assert.deepEqual(selfWorkToggle(working), { on: true, mode: "working", label: "Stop self-work", detail: "Working on 2 tasks.", change: { enabled: false, expected_revision: 8 }, startNow: null });
  // A stop condition shows under the button; Stop still sends only the switch.
  const stopping = { ...working, stop: { active: true, label: "Stops when “REM-12 is done”", revision: 2 } };
  assert.equal(selfWorkToggle(stopping).detail, "Working on 2 tasks. Stops when “REM-12 is done”.");
  assert.deepEqual(selfWorkToggle(stopping).change, { enabled: false, expected_revision: 8 });
  // A window turns self-work on while the saved switch is off: the button shows it on.
  const windowed = { settings: { enabled: true, saved_enabled: false, idle_minutes: 15, revision: 9 }, state: { mode: "waiting", starts_at: "x", detail: "" },
    schedule: { active: { id: "w1", label: "Until 7:00 PM today", end_label: "7:00 PM today" }, upcoming: [] } };
  assert.equal(selfWorkToggle(windowed).label, "Stop self-work");
  assert.equal(selfWorkToggle(windowed).detail, "Starts after 15 quiet minutes · ends 7:00 PM today");
  // On but waiting for quiet minutes: Start now skips the wait; once asked, it's gone.
  assert.deepEqual(selfWorkToggle(windowed).startNow, { start_now: true, expected_revision: 9 });
  const asked = { ...windowed, state: { mode: "waiting", starts_at: "", started_now_at: "2026-11-24T18:00:00Z", detail: "Starts work on its next check." } };
  assert.equal(selfWorkToggle(asked).startNow, null);
  assert.equal(selfWorkToggle(asked).detail, "Starts work on its next check.");
  assert.equal(selfWorkToggle({ ...windowed, state: { mode: "paused", detail: "Waiting for Raticode to connect." } }).mode, "paused");
});

test("a move's notice names the ticket and its column", () => {
  const rem = { key: "REM-12", scope: "rem", status: "todo", paused: "" };
  assert.equal(moveNotice(rem, "cancelled"), "REM-12 cancelled.");
  assert.equal(moveNotice(rem, "backlog"), "REM-12 moved to Backlog.");
  assert.equal(moveNotice(rem, "in_progress"), "REM-12 started.");
  assert.equal(moveNotice({ ...rem, paused: "paused" }, "in_progress"), "REM-12 resumed.");
  assert.equal(moveNotice({ ...rem, key: "STU-5", scope: "organization" }, "in_progress"), "STU-5 moved to In progress.");
});

// The phone's KanbanTest.kt reads the same table, so both apps offer Undo for the same moves.
const undoCases = JSON.parse(fs.readFileSync(new URL("../../../mobile/android/protocol/probes/kanban-cases/undo-move.json", import.meta.url), "utf8")).cases;

test("Undo is offered only where a move can go back, as the phone's shared table says", () => {
  assert.ok(undoCases.length > 0);
  for (const { scope, status, paused, column, undo, why } of undoCases) {
    const ticket = { key: "REM-12", scope, status, paused };
    assert.equal(canUndoMove(ticket, column), undo, `${scope || "no scope"} ${status}${paused ? " paused" : ""} to ${column}${why ? `: ${why}` : ""}`);
  }
});

test("an edit form only moves onto a newer ticket when that doesn't save over someone else's change", async () => {
  const { draftConflicts, draftConflictNotice, rebaseDraft, ticketDraft, ticketEdit } = await import("./remKanban.js");
  const old = ticket({ revision: 3, title: "Export", description: "Old", done_when: "" });
  const start = ticketDraft(old);
  // The user retitles it; Rem changes the title and the description meanwhile.
  const draft = { ...start, title: "Mine" };
  const newer = ticketDraft(ticket({ revision: 4, title: "Theirs", description: "New", done_when: "" }));
  assert.deepEqual(draftConflicts(start, draft, newer), ["title"]);
  assert.match(draftConflictNotice(["title"]), /^The title changed somewhere else/);
  // Saving stays at the revision the user started from, which the desktop refuses.
  assert.equal(ticketEdit(old, start, draft, 3).expected_revision, 3);
  // A change to a field the user didn't touch, or to the same text, moves the form up.
  assert.deepEqual(draftConflicts(start, { ...start, priority: "high" }, newer), []);
  assert.deepEqual(draftConflicts(start, { ...start, title: "Theirs " }, newer), []);
  assert.deepEqual(rebaseDraft(start, { ...start, priority: "high" }, newer), { ...newer, priority: "high" });
});
