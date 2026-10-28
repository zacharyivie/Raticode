import assert from "node:assert/strict";
import { test } from "node:test";
import { changesActiveWork, conversationMessages, latestWorkMessageIds, describeSchedule, draftTitle, draftToRequest, formatClock, formatWhen, groupTasks, messagesByDay, newTaskDraft, statusLine, taskActivity, taskMessages, taskRecap, taskToDraft, validateDraft, workStatus } from "./remHome.js";

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
