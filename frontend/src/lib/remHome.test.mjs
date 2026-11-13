import assert from "node:assert/strict";
import { test } from "node:test";
import { changesActiveWork, checkGroups, conversationMessages, criteriaSummary, finishedRunNotifications, latestWorkMessageIds, describeSchedule, draftTitle, draftToRequest, formatClock, formatWhen, groupTasks, messageStamp, messagesByDay, newTaskDraft, plainMarkdown, statusLine, taskActivity, taskMessages, taskRecap, taskToDraft, validateDraft, workStatus, prerequisites, afterChoices, afterEditable, startsWhen, runSleep, sleepBudget, canRetryPrerequisite, selfWorkReportItems } from "./remHome.js";

const NOW = new Date(2026, 9, 7, 10, 0); // Wednesday, 7 October 2026, 10:00 local
const at = (days, hour, minute = 0) => new Date(2026, 9, 7 + days, hour, minute).toISOString();

test("schedules read as plain language", () => {
  assert.equal(describeSchedule({ kind: "once" }), "Once, right away");
  assert.equal(describeSchedule({ kind: "daily", local_time: "08:30" }), "Every day at 8:30 AM");
  assert.equal(describeSchedule({ kind: "weekly", weekdays: ["monday"], local_time: "09:00" }), "Every Monday at 9 AM");
  assert.equal(describeSchedule({ kind: "weekly", weekdays: ["friday", "monday", "tuesday", "wednesday", "thursday"], local_time: "17:15" }), "Weekdays at 5:15 PM");
  assert.equal(describeSchedule({ kind: "weekly", weekdays: ["wednesday", "monday", "friday"], local_time: "12:00" }), "Mon, Wed and Fri at 12 PM");
  assert.equal(describeSchedule({ kind: "interval", every_minutes: 120 }), "Every 2 hours");
  assert.equal(describeSchedule({ kind: "cron", cron: "0 9 * * 1" }), "Custom (0 9 * * 1)");
  assert.equal(formatClock("00:05"), "12:05 AM");
  assert.equal(formatClock("nope"), "");
});

test("relative times stay short and human", () => {
  assert.equal(formatWhen(at(0, 10, 20), NOW), "in 20 min");
  assert.equal(formatWhen(at(0, 15), NOW), "today 3 PM");
  assert.equal(formatWhen(at(1, 8, 30), NOW), "tomorrow 8:30 AM");
  assert.equal(formatWhen(at(5, 9), NOW), "Mon 9 AM");
  assert.equal(formatWhen(at(0, 9, 55), NOW, { past: true }), "5 min ago");
  assert.equal(formatWhen(at(-1, 9), NOW, { past: true }), "yesterday 9 AM");
  assert.equal(formatWhen("not a date", NOW), "");
});

test("a sleeping worker reads as sleeping with its next check, and only while parked", () => {
  const sleep = { asleep: true, waiting_for: "CI run 4294", until: at(0, 10, 20), wakes: 2, slept_seconds: 25 * 60, budget_hours: 12 };
  const task = { id: "ci", active_run: { id: "run", state: "queued", waiting_reason: "Sleeping until 10:20 UTC · waiting for CI run 4294", sleep } };
  const activity = taskActivity(task, NOW);
  assert.deepEqual([activity.kind, activity.sleeping, activity.spinning, activity.tone], ["queued", true, false, "neutral"]);
  assert.equal(activity.label, "Sleeping · waiting for CI run 4294, next check in 20 min");
  assert.equal(groupTasks([task], NOW)[0].id, "now");
  assert.equal(sleepBudget(sleep), "Woke 2 times · 25 min of 12 h wait budget used");
  assert.equal(sleepBudget({ wakes: 0, slept_seconds: 0, budget_hours: 1.5 }), "First sleep · 0 min of 1.5 h wait budget used");
  // Awake again, or from a desktop without sleeps, it reads as before.
  assert.equal(runSleep({ ...task.active_run, state: "executing" }), null);
  assert.equal(taskActivity({ active_run: { state: "queued", waiting_reason: "Waiting for a free worker" } }, NOW).sleeping, undefined);
  const card = workStatus({ task_id: "ci", run_id: "run" }, [task]);
  assert.equal(card.sleeping, true);
  assert.match(card.label, /^Sleeping · waiting for CI run 4294/);
});

test("running, verifying and repairing spin; queued and blocked do not", () => {
  const running = state => taskActivity({ active_run: { state } }, NOW);
  assert.equal(running("executing").spinning, true);
  assert.equal(running("verifying").label, "Checking the work");
  assert.equal(running("repairing").spinning, true);
  assert.equal(running("cancelling").label, "Stopping");
  assert.deepEqual([running("queued").kind, running("queued").spinning], ["queued", false]);
  assert.equal(taskActivity({ active_run: { state: "executing", phase_label: "Second pass" } }, NOW).label, "Second pass");
  const blocked = taskActivity({ last_run: { state: "blocked", summary_short: "Needs a test database" } }, NOW);
  assert.deepEqual([blocked.kind, blocked.spinning, blocked.label], ["attention", false, "Needs a test database"]);
  assert.equal(taskActivity({ last_run: { state: "blocked", acknowledged: true } }, NOW).kind, "done");
  assert.equal(taskActivity({ state: "paused", next_occurrence_at: at(1, 9) }, NOW).kind, "paused");
  assert.equal(taskActivity({ next_occurrence_at: at(1, 9) }, NOW).label, "Next tomorrow 9 AM");
  // An unknown state is reported honestly instead of animating forever.
  assert.deepEqual([taskActivity({ active_run: { state: "mystery" } }, NOW).spinning, taskActivity({ active_run: { state: "mystery" } }, NOW).label], [false, "Not scheduled"]);
});

test("tasks group by what they need, hiding deleted tasks and empty groups", () => {
  const groups = groupTasks([
    { id: "a", active_run: { state: "executing", started_at: at(0, 9) } },
    { id: "b", active_run: { state: "verifying", started_at: at(0, 9, 30) } },
    { id: "c", next_occurrence_at: at(3, 9) },
    { id: "d", next_occurrence_at: at(1, 9) },
    { id: "e", last_run: { state: "failed" } },
    { id: "f", deleted_at: at(0, 8), active_run: { state: "executing" } },
  ], NOW);
  assert.deepEqual(groups.map(group => [group.id, group.items.map(item => item.task.id)]), [["now", ["b", "a"]], ["attention", ["e"]], ["scheduled", ["d", "c"]]]);
  assert.equal(statusLine([{ last_run: { state: "failed" } }, { active_run: { state: "executing" } }], NOW), "1 thing needs you");
  assert.equal(statusLine([{ active_run: { state: "executing" } }, { active_run: { state: "queued" } }], NOW), "Working on 2 things");
  assert.equal(statusLine([{ next_occurrence_at: at(1, 9) }], NOW), "All caught up");
  assert.equal(statusLine([], NOW), "Here when you need me");
});

test("messages split by day in order", () => {
  const days = messagesByDay([{ id: 1, created_at: at(-1, 9) }, { id: 2, created_at: at(0, 8) }, { id: 3, created_at: at(0, 9) }], NOW);
  assert.deepEqual(days.map(day => [day.label, day.messages.map(message => message.id)]), [["Yesterday", [1]], ["Today", [2, 3]]]);
});

test("messages without a usable date stay with their neighbors", () => {
  const days = messagesByDay([{ id: "a" }, { id: "b", created_at: "soon" }, { id: "c", created_at: at(0, 8) }, { id: "d", created_at: null }, { id: "e", created_at: at(0, 9) }, { id: "f", created_at: at(-1, 9) }, { id: "g", created_at: at(0, 10) }], NOW);
  assert.deepEqual(days.map(day => [day.key, day.label, day.messages.map(message => message.id)]),
    [["a", "Earlier", ["a", "b"]], ["c", "Today", ["c", "d", "e"]], ["f", "Yesterday", ["f"]], ["g", "Today", ["g"]]]);
});

test("each message has a time stamp, with the day on older messages", () => {
  const today = messageStamp(at(0, 9, 5), NOW);
  assert.equal(today.label, "9:05 AM");
  assert.equal(today.iso, at(0, 9, 5));
  assert.match(today.full, /Wednesday.*October.*7.*2026, 9:05 AM/);
  assert.equal(messageStamp(at(0, 14), NOW).label, "2:00 PM");
  assert.equal(messageStamp(at(-1, 23, 59), NOW).label, "Yesterday, 11:59 PM");
  assert.match(messageStamp(at(-6, 0, 30), NOW).label, /^Oct 1, 12:30 AM$/);
  assert.match(messageStamp(new Date(2025, 11, 31, 8).toISOString(), NOW).label, /^Dec 31, 2025, 8:00 AM$/);
  for (const value of [undefined, null, "", "not a date", "2026-13-45T99:00:00Z"]) assert.equal(messageStamp(value, NOW), null, String(value));
});

test("operational problems are updates, while essential questions need the user", () => {
  const problem = { id: "audit", last_run: { id: "run", state: "blocked", needs_user: false } };
  assert.equal(groupTasks([problem], NOW)[0].label, "Updates");
  assert.doesNotMatch(statusLine([problem], NOW), /needs you/);
  assert.equal(taskActivity(problem, NOW).label, "Couldn't finish");
  assert.equal(workStatus({ task_id: "audit", run_id: "run" }, [problem]).label, "Couldn't finish");
  assert.equal(workStatus({ state: "blocked", needs_user: false }).label, "Couldn't finish");
  const question = { ...problem, last_run: { ...problem.last_run, needs_user: true } };
  assert.equal(groupTasks([question], NOW)[0].label, "Needs you");
  assert.equal(statusLine([question], NOW), "1 thing needs you");
  assert.equal(workStatus({ task_id: "audit", run_id: "run" }, [question]).label, "Needs you");
});

test("task completion appends a recap without hiding or moving earlier messages", () => {
  const message = (id, run, body = id) => ({ id, role: "rem", body, work: { task_id: run === "c" ? "a" : run, run_id: run, thread_id: `thread-${run}` } });
  const messages = [
    { id: "user", role: "user", body: "Start a thread" },
    message("queued-a", "a"), message("queued-b", "b"),
    { id: "qa", role: "rem", body: "No other threads are running" },
    message("finished-a", "a", "Reviewed the latest commits."),
    message("rerun", "c"),
  ];
  const visible = conversationMessages(messages);
  assert.deepEqual(visible.map(m => m.id), ["user", "queued-a", "queued-b", "qa", "finished-a", "rerun"]);
  assert.equal(visible[4].work.thread_id, "thread-a");
  assert.equal(messages.length, 6);
  assert.deepEqual(taskMessages(messages, "a").map(m => [m.id, m.work.thread_id]), [
    ["queued-a", "thread-a"], ["finished-a", "thread-a"], ["rerun", "thread-c"],
  ]);
  assert.equal(visible[1].body, "queued-a");
  assert.equal(taskRecap("Reviewed the latest commits."), "Reviewed the latest commits.");
  assert.ok(taskRecap("Detailed findings ".repeat(100)).length <= 320);
});

test("work cards follow the live run and keep their thread link", () => {
  const tasks = [{ id: "t", title: "Sync research", type: "research", active_run: { id: "r2", state: "verifying", thread_id: "th2", verification_thread_id: "rv2" }, last_run: { id: "r1", state: "succeeded", thread_id: "th1" } }];
  const live = workStatus({ task_id: "t", run_id: "r2", thread_id: "stale" }, tasks);
  assert.deepEqual([live.state, live.threadId, live.verificationThreadId, live.spinning], ["verifying", "th2", "rv2", true]);
  tasks[0].last_run.phase_label = "Reading the reply";
  assert.equal(workStatus({ task_id: "t", run_id: "r1" }, tasks).label, "Done");
  const gone = workStatus({ task_id: "deleted", run_id: "r9", thread_id: "th9", title: "Old task", state: "cancelled" }, tasks);
  assert.deepEqual([gone.deleted, gone.threadId, gone.label], [true, "th9", "Stopped"]);
});

test("drafts validate the essentials and become minimal edits", () => {
  const draft = newTaskDraft({ type: "maintain", timezone: "America/New_York" });
  assert.deepEqual(Object.keys(validateDraft(draft)).sort(), ["project_dir", "prompt"]);
  const weekly = { ...draft, prompt: "Fix links.", project_dir: "/p", schedule: { ...draft.schedule, kind: "weekly", weekdays: [] } };
  assert.equal(validateDraft(weekly).schedule, "Pick at least one day.");
  assert.equal(validateDraft({ ...weekly, schedule: { ...weekly.schedule, kind: "cron", cron: "0 9 *" } }).schedule, "Use five cron fields, like 0 9 * * 1.");
  assert.deepEqual(validateDraft({ ...weekly, type: "research", project_dir: "", schedule: { ...weekly.schedule, kind: "once" } }), {});
  assert.deepEqual(validateDraft({ ...weekly, type: "develop", project_dir: "", schedule: { ...weekly.schedule, kind: "once" } }), {});

  const request = draftToRequest({ ...weekly, title: "", prompt: "  Keep the docs site building. \nDetails here", schedule: { ...weekly.schedule, weekdays: ["friday", "monday"] }, criteria: [{ description: " Links resolve " }, { description: " " }] });
  assert.equal(request.title, "Keep the docs site building");
  assert.deepEqual(request.schedule, { kind: "weekly", weekdays: ["monday", "friday"], local_time: "09:00", timezone: "America/New_York" });
  assert.deepEqual(request.acceptance_criteria, [{ description: "Links resolve", authored_by: "user" }]);

  const task = { id: "t", type: "maintain", title: "Docs", prompt: "Keep docs building.", project_dir: "/p", schedule: { kind: "daily", local_time: "08:00", timezone: "UTC" }, acceptance_criteria: [{ id: "c1", description: "Builds", authored_by: "rem_recommended" }] };
  assert.deepEqual(draftToRequest(taskToDraft(task), task), {});
  const retimed = taskToDraft(task);
  retimed.schedule.local_time = "09:30";
  const patch = draftToRequest(retimed, task);
  assert.deepEqual(Object.keys(patch), ["schedule"]);
  assert.equal(changesActiveWork(patch), false);
  assert.equal(changesActiveWork(draftToRequest({ ...taskToDraft(task), prompt: "Keep docs and examples building." }, task)), true);
  assert.equal(draftTitle({ title: "", prompt: "x".repeat(80) }).length, 58);
});

test("queued work explains why it is waiting", () => {
  const waiting = { id: "t", active_run: { id: "r", state: "queued", waiting_reason: "Waiting for another task in this project to finish", phase_label: "Checking the work" } };
  assert.equal(taskActivity(waiting, NOW).label, "Waiting for another task in this project to finish");
  assert.equal(workStatus({ task_id: "t", run_id: "r" }, [waiting]).label, "Waiting for another task in this project to finish");
  const running = { id: "t", active_run: { id: "r", state: "verifying", waiting_reason: "stale", phase_label: "Rechecking the work" } };
  assert.equal(taskActivity(running, NOW).label, "Rechecking the work");
  assert.equal(taskActivity({ active_run: { state: "queued" } }, NOW).label, "Waiting to start");
});

test("only the newest message for a run shows live status", () => {
  const work = run => ({ task_id: "t", run_id: run });
  const live = latestWorkMessageIds([
    { id: "queued-a", work: work("a") },
    { id: "ack-a", work: work("a") },
    { id: "plain" },
    { id: "queued-b", work: work("b") },
    { id: "finished-a", work: work("a") },
  ]);
  assert.deepEqual([...live].sort(), ["finished-a", "queued-b"]);
});

test("check counts separate failures from checks that never ran", () => {
  const criteria = results => results.map((result, index) => ({ id: String(index), result }));
  assert.equal(criteriaSummary([]), "");
  assert.equal(criteriaSummary(criteria(["unknown", "unknown", "unknown", "unknown", "unknown"])), "5 checks not run yet");
  assert.equal(criteriaSummary(criteria(["unknown"])), "Not checked yet");
  assert.equal(criteriaSummary(criteria(["pass", "pass"])), "2 of 2 checks passed");
  assert.equal(criteriaSummary(criteria(["pass", "fail", "unknown"])), "1 of 3 passed, 1 failed, 1 not checked");
  assert.equal(criteriaSummary(criteria(["fail", "fail"])), "0 of 2 passed, 2 failed");
  // A reviewed check the review couldn't confirm is unresolved, not unchecked.
  assert.equal(criteriaSummary([{ id: "a", result: "pass" }, { id: "b", result: "unknown", evidence: "Not accepted: no receipts." }]), "1 of 2 passed, 1 unresolved");
  assert.equal(criteriaSummary([null, "x"]), "");
});

test("check groups list each failed or unresolved check with its reason", () => {
  const groups = checkGroups([
    { id: "a", description: "Tests pass", result: "fail", evidence: "`test_export` failed: **3** rows missing." },
    { id: "b", description: "Docs updated", result: "unknown", evidence: "Not accepted: no project check receipts. Looked fine." },
    { id: "c", description: "Lint clean", result: "pass", evidence: "ruff passed." },
    { id: "d", description: "Not reviewed", result: "unknown" },
    { id: "e", result: "fail" },
    null,
  ]);
  const view = list => list.map(item => [item.key, item.title, item.reason]);
  assert.deepEqual(view(groups.failed), [["a", "Tests pass", "test_export failed: 3 rows missing."], ["e", "Check 5", ""]]);
  assert.deepEqual(view(groups.unresolved), [["b", "Docs updated", "Not accepted: no project check receipts. Looked fine."]]);
  assert.deepEqual(view(groups.passed), [["c", "Lint clean", "ruff passed."]]);
  assert.deepEqual(view(groups.unchecked), [["d", "Not reviewed", ""]]);
  assert.equal(groups.reviewed, true);
});

test("check groups stay quiet until a review looked at a check", () => {
  for (const criteria of [undefined, null, [], [{ id: "a", result: "unknown" }, { id: "b", result: "unknown", evidence: "  " }]]) {
    const groups = checkGroups(criteria);
    assert.equal(groups.reviewed, false);
    assert.equal(groups.failed.length + groups.unresolved.length + groups.passed.length, 0);
  }
  // An older backend sends no descriptions or IDs: checks are numbered.
  assert.deepEqual(checkGroups([{ result: "pass" }, { result: "fail", evidence: "Broke." }]).failed.map(item => [item.key, item.title]), [["check-1", "Check 2"]]);
});

test("one-line recaps stay fast on long replies full of unclosed markup", () => {
  // Unbounded patterns rescanned to the end from every "[" or "**", so these took
  // two to three seconds each and froze the task list while it rendered.
  for (const text of ["[".repeat(50_000), "**a ".repeat(25_000), `[${"x".repeat(60_000)}`, `**${"y ".repeat(40_000)}`]) {
    const started = performance.now();
    plainMarkdown(text);
    assert.ok(performance.now() - started < 400, `took ${Math.round(performance.now() - started)} ms`);
  }
  assert.equal(plainMarkdown(`**${"word ".repeat(20)}done** and [${"label ".repeat(30)}](https://example.com)`), `${"word ".repeat(20)}done and ${"label ".repeat(30)}`);
});

test("one-line recaps show Rem's link labels instead of Markdown", () => {
  const report = "/home/me/Projects/second-brain/reports/rem/Reports/Raticode/launch plan.html";
  assert.equal(plainMarkdown(`The [cited report](<${report}>) covers it.`), "The cited report covers it.");
  assert.equal(plainMarkdown("Uses **Claude Code with `opus` at high effort** and *one* setting."), "Uses Claude Code with opus at high effort and one setting.");
  assert.equal(plainMarkdown("[Cat](https://en.wikipedia.org/wiki/Cat_(disambiguation)) ![A cat](https://x/c.jpg) <https://example.com>"), "Cat A cat https://example.com");
  assert.equal(plainMarkdown("2*3*4 keeps snake_case_names and [notes](notes.md \"Notes\")"), "2*3*4 keeps snake_case_names and notes");
  // Shortening never cuts a link in half, because the markup goes first.
  const recap = taskRecap(`${"Checked the build. ".repeat(14)}[Download the APK](</home/me/Raticode Mobile/apks/app.apk>) is ready.`);
  assert.ok(recap.length <= 320);
  assert.doesNotMatch(recap, /\]\(|<\/home/);
  const blocked = { id: "t", title: "Audit", last_run: { id: "r", state: "blocked", summary_short: `Saved the [audit](<${report}>) but \`npm test\` failed.` } };
  assert.equal(taskActivity(blocked).label, "Saved the audit but npm test failed.");
  const before = { ...blocked, active_run: { id: "r", state: "executing" }, last_run: { id: "r", state: "executing" } };
  assert.equal(finishedRunNotifications([before], [blocked])[0].body, "Saved the audit but npm test failed.");
});

test("only runs seen working before notify when they finish", () => {
  const running = { id: "t1", title: "Digest", active_run: { id: "r1", state: "verifying" }, last_run: { id: "r1", state: "verifying" } };
  const done = { id: "t1", title: "Digest", active_run: null, last_run: { id: "r1", state: "succeeded", summary_short: "Saved today's digest." } };
  const stuck = { id: "t2", title: "Assess Rem", active_run: null, last_run: { id: "r2", state: "blocked", needs_user: false, summary_short: "The save tool was refused." } };
  const stuckBefore = { ...stuck, active_run: { id: "r2", state: "executing" }, last_run: { id: "r2", state: "executing" } };
  assert.deepEqual(finishedRunNotifications(null, [done]), []);
  assert.deepEqual(finishedRunNotifications([done], [done]), []);
  assert.deepEqual(finishedRunNotifications([running, stuckBefore], [done, stuck]), [
    { tag: "rem-run-r1", taskId: "t1", title: "Done: Digest", body: "Saved today's digest." },
    { tag: "rem-run-r2", taskId: "t2", title: "Couldn't finish: Assess Rem", body: "The save tool was refused." },
  ]);
  const cancelled = { ...done, last_run: { id: "r1", state: "cancelled" } };
  assert.deepEqual(finishedRunNotifications([running], [cancelled]), []);
  const waiting = { ...running, active_run: { id: "r1", state: "queued", waiting_reason: "Waiting for the 03:00 run before checking again" }, last_run: { id: "r1", state: "queued" } };
  assert.deepEqual(finishedRunNotifications([running], [waiting]), []);
});

test("a card whose run left the task list shows the state chat last served, not its first snapshot", () => {
  // The desktop serves each card with its run's current state and step.
  const done = workStatus({ task_id: "gone", run_id: "r", state: "succeeded", step: "report" }, []);
  assert.equal(done.state, "succeeded");
  assert.equal(done.label, "Done");
  // A listed run's own step wins over the card's.
  const tasks = [{ id: "t", active_run: { id: "r", state: "queued", step: "check", resume_review: true } }];
  assert.equal(workStatus({ task_id: "t", run_id: "r", state: "queued", step: "plan" }, tasks).step, "check");
});

test("a task waiting for others reads as coming up, and a held one needs the user", () => {
  const waiting = { id: "fix", title: "Fix", state: "enabled", schedule: { kind: "once" }, active_run: null, last_run: null,
    dependency: { state: "waiting", reason: "Waiting for “Audit” to finish", prerequisites: [{ task_id: "audit", title: "Audit", state: "waiting", run_state: "executing", retryable: false }] } };
  const activity = taskActivity(waiting, NOW);
  assert.equal(activity.kind, "waiting");
  assert.equal(activity.label, "Waiting for “Audit” to finish");
  assert.deepEqual(groupTasks([waiting], NOW).map(group => [group.id, group.items.length]), [["scheduled", 1]]);
  assert.equal(prerequisites(waiting)[0].label, "Running");

  const held = { ...waiting, dependency: { state: "held", reason: "On hold: “Audit” was blocked. Retry it, or start this task now.",
    prerequisites: [{ task_id: "audit", title: "Audit", state: "held", reason: "“Audit” was blocked", run_state: "blocked", retryable: true }] } };
  const attention = taskActivity(held, NOW);
  assert.equal(attention.kind, "attention");
  assert.equal(attention.held, true);
  assert.match(attention.label, /^On hold/);
  assert.equal(statusLine([held], NOW), "1 thing needs you");
  assert.deepEqual(groupTasks([held], NOW).map(group => group.label), ["Needs you"]);
  assert.equal(prerequisites(held)[0].label, "Needs you");
  assert.equal(prerequisites({ dependency: { prerequisites: [{ task_id: "x", title: "X", state: "held", reason: "“X” was deleted" }] } })[0].label, "“X” was deleted");
  assert.equal(prerequisites(held)[0].tone, "attention");
  // Paused waiting tasks read as paused, and a met condition is about to start.
  assert.equal(taskActivity({ ...held, state: "paused" }, NOW).kind, "paused");
  assert.equal(taskActivity({ ...waiting, dependency: { state: "met", prerequisites: [] } }, NOW).label, "Starting soon");
});

test("a held card in chat follows its task until the task runs", () => {
  const work = { task_id: "fix", run_id: "", thread_id: "", title: "Fix", type: "develop", state: "held", needs_user: true };
  const held = { id: "fix", title: "Fix", dependency: { state: "held", prerequisites: [] } };
  assert.equal(workStatus(work, [held]).label, "On hold");
  assert.equal(workStatus(work, [{ ...held, dependency: { state: "waiting", prerequisites: [] } }]).label, "Waiting for another task");
  const started = { id: "fix", title: "Fix", active_run: { id: "r1", state: "executing", thread_id: "t1" }, last_run: null };
  const status = workStatus(work, [started]);
  assert.equal(status.state, "executing");
  assert.equal(status.threadId, "t1");
});

test("start after is part of new one-time tasks and only sent when it changes", () => {
  const draft = { ...newTaskDraft({ timezone: "UTC" }), prompt: "Fix it", after: ["audit"] };
  assert.deepEqual(draftToRequest(draft).after, ["audit"]);
  assert.equal(startsWhen(draft, true), "Starts by itself once every task above finishes successfully.");
  assert.equal(startsWhen({ ...draft, after: [] }, true), "Starts as soon as you save.");
  // An empty field is left out of new tasks; repeating tasks never wait.
  assert.equal("after" in draftToRequest({ ...draft, after: [] }), false);
  assert.equal("after" in draftToRequest({ ...draft, schedule: { kind: "daily", local_time: "09:00", timezone: "UTC" } }), false);

  const task = { id: "fix", title: "Fix it", type: "research", prompt: "Fix it", schedule: { kind: "once", timezone: "UTC" }, acceptance_criteria: [], revision: 2, after: ["audit"] };
  assert.deepEqual(draftToRequest(taskToDraft(task), task), {});
  assert.deepEqual(draftToRequest({ ...taskToDraft(task), after: [] }, task), { after: [] });
  // Tasks saved before this feature have no after; editing them never sends one.
  const old = { ...task, after: undefined };
  assert.deepEqual(draftToRequest({ ...taskToDraft(old), prompt: "Fix it now" }, old), { prompt: "Fix it now" });

  assert.equal(afterEditable(null, draft), true);
  assert.equal(afterEditable(task, taskToDraft(task)), true);
  assert.equal(afterEditable({ ...task, after_started: { how: "prerequisites" } }, taskToDraft(task)), false);
  assert.equal(afterEditable({ ...task, last_run: { id: "r", state: "succeeded" } }, taskToDraft(task)), false);
  assert.equal(afterEditable(null, { ...draft, schedule: { kind: "weekly" } }), false);
  assert.deepEqual(afterChoices([task, { id: "audit", title: "Audit" }, { id: "gone", title: "Gone", deleted_at: "x" }], task), [{ id: "audit", title: "Audit" }]);
});

test("a hold card stays live until a card for the task's run follows it", () => {
  const held = { id: "h", work: { task_id: "fix", run_id: "", state: "held" } };
  const other = { id: "o", work: { task_id: "audit", run_id: "r0" } };
  assert.deepEqual([...latestWorkMessageIds([held, other])].sort(), ["h", "o"]);
  const started = { id: "s", work: { task_id: "fix", run_id: "r1" } };
  assert.deepEqual([...latestWorkMessageIds([held, other, started])].sort(), ["o", "s"]);
});

test("a paused run reads as paused, by you or to make way for your work, with the right controls", async () => {
  const { runControls, pausedBy, runState, PAUSED_FOR_YOU } = await import("./remHome.js");
  const paused = { id: "r1", state: "paused", paused_by: "user", finished_at: null };
  const task = { id: "t", title: "Audit", state: "enabled", active_run: null, last_run: paused };
  const activity = taskActivity(task, NOW);
  assert.equal(activity.kind, "paused");
  assert.equal(activity.label, "Paused");
  assert.equal(groupTasks([task], NOW)[0].id, "paused");
  assert.deepEqual(runControls(task), { resume: "r1" });
  const yielded = { ...task, last_run: { ...paused, paused_by: "priority" } };
  assert.equal(taskActivity(yielded, NOW).label, PAUSED_FOR_YOU);
  assert.equal(pausedBy(yielded.last_run), "priority");
  assert.equal(runState("paused", false, "priority").label, PAUSED_FOR_YOU);
  // A run going can be paused; one stopping can't; a paused schedule says so plainly.
  assert.deepEqual(runControls({ active_run: { id: "r2", state: "executing" } }), { pause: "r2" });
  assert.deepEqual(runControls({ active_run: { id: "r2", state: "cancelling" } }), {});
  assert.equal(taskActivity({ ...task, state: "paused", last_run: null }, NOW).label, "Schedule paused");
  // A chat card follows the run, and an older card without the run uses its own field.
  const card = workStatus({ task_id: "t", run_id: "r1", state: "queued" }, [yielded]);
  assert.equal(card.state, "paused");
  assert.equal(card.label, PAUSED_FOR_YOU);
  assert.equal(card.pausedBy, "priority");
  assert.equal(workStatus({ task_id: "gone", run_id: "x", state: "paused", paused_by: "user" }, []).label, "Paused");
});

test("start conditions other than tasks read as plain waits that can't be opened or retried", () => {
  const task = { id: "fix", title: "Fix", state: "enabled", schedule: { kind: "once" }, active_run: null, last_run: null,
    dependency: { state: "waiting", reason: "Waiting for 1 running task to finish and until 7:00 AM Fri (Asia/Irkutsk)", prerequisites: [
      { kind: "run", task_id: "audit", run_id: "r1", title: "Audit", state: "waiting", reason: "Waiting for “Audit” (running when you asked) to finish", retryable: false },
      { kind: "time", task_id: "", title: "7:00 AM Fri (Asia/Irkutsk)", state: "waiting", reason: "Waiting until 7:00 AM Fri (Asia/Irkutsk)", retryable: false },
      { kind: "self_work", task_id: "", title: "Self-work window", state: "met", retryable: false },
      { kind: "idle", task_id: "", title: "Nothing else running", state: "waiting", retryable: true },
      { task_id: "old", title: "Old", state: "held", reason: "“Old” failed", run_state: "failed", retryable: true },
    ] } };
  const items = prerequisites(task);
  assert.deepEqual(items.map(item => [item.kind, item.label, item.opens, item.retryable]), [
    ["run", "Running", true, false],
    ["time", "Waiting", false, false],
    ["self_work", "Ended", false, false],
    ["idle", "Waiting", false, false],
    ["task", "Didn't finish", true, true],
  ]);
  assert.equal(new Set(items.map(item => item.key)).size, items.length, "rows have their own keys even without a task");
  assert.equal(taskActivity(task, NOW).label, "Waiting for 1 running task to finish and until 7:00 AM Fri (Asia/Irkutsk)");
  assert.equal(canRetryPrerequisite({ kind: "task", task_id: "", retryable: true }), false);
  assert.equal(canRetryPrerequisite({ task_id: "x", retryable: true }), true);
});

test("the When note lists every start condition Rem set", () => {
  const draft = { ...newTaskDraft({ timezone: "UTC" }), prompt: "Fix it" };
  assert.equal(startsWhen({ ...draft, after_running: "snapshot" }, false), "Starts by itself once the tasks that were running when you asked have ended.");
  assert.equal(startsWhen({ ...draft, after: ["a"], after_running: "idle", after_self_work: true }, false),
    "Starts by itself once every task above finishes successfully, nothing else is running and the self-work window ends.");
  const task = { id: "fix", title: "Fix it", type: "research", prompt: "Fix it", schedule: { kind: "once", timezone: "UTC" }, acceptance_criteria: [], revision: 2, after: [], after_running: "snapshot", after_self_work: true };
  assert.equal(taskToDraft(task).after_running, "snapshot");
  // The form never sends conditions it can't edit.
  assert.deepEqual(draftToRequest(taskToDraft(task), task), {});
});

test("a self-work report lists what each task did and opens the task, or its thread once the task is gone", () => {
  const message = { id: "self-work-period-p1", role: "rem", kind: "self_work_report", body: "Done.", self_work_report: { period_id: "p1", items: [
    { task_id: "t1", run_id: "r1", thread_id: "th1", title: "Fix **export**", outcome: "finished" },
    { task_id: "t2", run_id: "r2", thread_id: "th2", title: "Audit", outcome: "blocked" },
    { task_id: "", run_id: "", thread_id: "", title: "Rank backlog", outcome: "mystery" },
  ] } };
  const items = selfWorkReportItems(message, [{ id: "t1" }]);
  assert.deepEqual(items.map(item => [item.title, item.outcome, item.tone, item.taskId, item.threadId]), [
    ["Fix export", "Finished", "success", "t1", ""],
    ["Audit", "Blocked", "attention", "", "th2"],
    ["Rank backlog", "mystery", "neutral", "", ""],
  ]);
  assert.deepEqual(selfWorkReportItems({ ...message, kind: "task_update" }), []);
  assert.deepEqual(selfWorkReportItems({ ...message, self_work_report: undefined }), []);
});
