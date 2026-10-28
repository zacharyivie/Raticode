// Explicit Rem home fixtures for tests, the visual harness and development previews.
// Times are relative to load so cards read naturally whenever they are shown.

const ago = minutes => new Date(Date.now() - minutes * 60000).toISOString();
function nextWeekday(target, hour, minute) {
  const date = new Date();
  date.setHours(hour, minute, 0, 0);
  const shift = (target - date.getDay() + 7) % 7 || 7;
  date.setDate(date.getDate() + shift);
  return date.toISOString();
}
function tomorrowAt(hour, minute) {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(hour, minute, 0, 0);
  return date.toISOString();
}

const PROJECT = "/home/rem/Projects/Raticode/rembot";
const MOBILE = "/home/rem/Projects/Raticode Mobile/raticode-mobile";

function tasks() {
  return [
    {
      id: "task-sync-research", revision: 1, state: "enabled", type: "research", title: "Local-first sync engines",
      prompt: "Research local-first sync engines that could back Raticode Mobile. Compare conflict handling, offline support, licensing and maturity. Save a cited report.",
      project_dir: MOBILE, schedule: { kind: "once" },
      acceptance_criteria: [
        { id: "c-1", description: "Compares at least four engines on the same criteria", authored_by: "rem_recommended" },
        { id: "c-2", description: "Every claim links to a source", authored_by: "rem_recommended" },
        { id: "c-3", description: "Report is saved to Second Brain and opens", authored_by: "rem_recommended" },
      ],
      active_run: { id: "run-sync-1", state: "verifying", started_at: ago(14), thread_id: "thread-sync-worker", verification_thread_id: "thread-sync-review", criteria: [{ id: "c-1", result: "pass" }, { id: "c-2", result: "unknown" }, { id: "c-3", result: "pass" }] },
    },
    {
      id: "task-flaky-test", revision: 2, state: "enabled", type: "maintain", title: "Fix the flaky scheduler test",
      prompt: "test_scheduler_resumes_after_sleep fails about one run in ten. Find the cause and fix it without weakening the test.",
      project_dir: PROJECT, schedule: { kind: "once" },
      acceptance_criteria: [
        { id: "c-4", description: "The test passes 20 runs in a row", authored_by: "user" },
        { id: "c-5", description: "Ruff, mypy and the scheduler tests pass", authored_by: "project_rules" },
      ],
      active_run: { id: "run-flaky-1", state: "repairing", phase_label: "Second pass after review", started_at: ago(6), thread_id: "thread-flaky-worker", verification_thread_id: "thread-flaky-review" },
    },
    {
      id: "task-csv-export", revision: 1, state: "enabled", type: "develop", title: "CSV export for Runs",
      prompt: "Add a CSV export button to the Runs list that exports the visible rows.",
      project_dir: PROJECT, schedule: { kind: "once" },
      acceptance_criteria: [{ id: "c-6", description: "Exported rows match the filtered list", authored_by: "rem_recommended" }],
      last_run: { id: "run-csv-1", state: "blocked", summary_short: "Needs a test database", finished_at: ago(95), thread_id: "thread-csv-worker" },
    },
    {
      id: "task-weekly-health", revision: 4, state: "enabled", type: "maintain", title: "Weekly workflow health",
      prompt: "Look at last week's failed workflow runs. Fix project-local defects and explain anything you can't fix.",
      project_dir: PROJECT, schedule: { kind: "weekly", weekdays: ["monday"], local_time: "09:00" }, next_occurrence_at: nextWeekday(1, 9, 0),
      acceptance_criteria: [{ id: "c-7", description: "Each failed run is fixed or explained", authored_by: "rem_recommended" }],
      last_run: { id: "run-health-3", state: "succeeded", finished_at: ago(60 * 24 * 3) },
    },
    {
      id: "task-deps", revision: 1, state: "enabled", type: "maintain", title: "Dependency check",
      prompt: "Check for security advisories in Python and npm dependencies. Open a fix thread for anything critical.",
      project_dir: PROJECT, schedule: { kind: "daily", local_time: "08:30" }, next_occurrence_at: tomorrowAt(8, 30),
      acceptance_criteria: [{ id: "c-8", description: "Advisories are listed with severity", authored_by: "rem_recommended" }],
      last_run: { id: "run-deps-9", state: "succeeded", finished_at: ago(150) },
    },
    {
      id: "task-digest", revision: 1, state: "paused", type: "reminder", title: "Morning digest",
      prompt: "Each weekday morning, tell me what finished overnight and what's due.",
      project_dir: "", schedule: { kind: "weekly", weekdays: ["monday", "tuesday", "wednesday", "thursday", "friday"], local_time: "08:00" },
      acceptance_criteria: [],
    },
    {
      id: "task-vector-db", revision: 1, state: "enabled", type: "research", title: "Vector search for Second Brain",
      prompt: "Compare a few vector search options for Second Brain.",
      project_dir: "", schedule: { kind: "once" },
      acceptance_criteria: [{ id: "c-9", description: "Recommends one option with reasons", authored_by: "rem_recommended" }],
      last_run: { id: "run-vector-1", state: "succeeded", finished_at: ago(60 * 22), thread_id: "thread-vector-worker", criteria: [{ id: "c-9", result: "pass" }, { id: "c-x", result: "pass" }, { id: "c-y", result: "pass" }] },
    },
  ];
}

function messages() {
  return [
    { id: "m-1", role: "user", created_at: ago(60 * 24 + 40), body: "Compare a few vector search options for Second Brain?" },
    { id: "m-2", role: "rem", created_at: ago(60 * 24 + 40), body: "On it. I'll save a cited comparison to Second Brain.", work: { task_id: "task-vector-db", run_id: "run-vector-1", thread_id: "thread-vector-worker" } },
    { id: "m-3", role: "rem", created_at: ago(60 * 22), body: "Done. Short version: SQLite full-text search plus a small vector index is plenty for now.", work: { task_id: "task-vector-db", run_id: "run-vector-1", thread_id: "thread-vector-worker" } },
    { id: "m-4", role: "rem", created_at: ago(95), body: "Morning! Dependencies look clean. The CSV export is stuck: it needs a test database. Want me to set one up?", work: { task_id: "task-csv-export", run_id: "run-csv-1", thread_id: "thread-csv-worker" } },
    { id: "m-5", role: "user", created_at: ago(15), body: "Not yet. Can you research local-first sync engines for the mobile app?" },
    { id: "m-6", role: "rem", created_at: ago(14), body: "Started a research thread. I'll check its sources before calling it done.", work: { task_id: "task-sync-research", run_id: "run-sync-1", thread_id: "thread-sync-worker" } },
    { id: "m-7", role: "user", created_at: ago(8), body: "Also the scheduler test keeps flaking, fix it?" },
    { id: "m-8", role: "rem", created_at: ago(7), body: "Yep. Done when it passes 20 runs in a row.", work: { task_id: "task-flaky-test", run_id: "run-flaky-1", thread_id: "thread-flaky-worker" } },
    { id: "m-9", role: "user", created_at: ago(1), body: "What's running?" },
    { id: "m-10", role: "rem", created_at: ago(1), body: "Two things. The sync research is being checked, and the test fix is on its second pass." },
  ];
}

export function remHomeScene(scene = "busy") {
  const conversation = { id: "rem-supervisor", provider: "claude_code", model: "claude-opus-5-5", turn_state: scene === "replying" ? "replying" : "idle" };
  if (scene === "empty") return { conversation, messages: [], tasks: [] };
  if (scene === "replying") return { conversation, messages: [...messages(), { id: "m-11", role: "user", created_at: ago(0), body: "Remind me Friday to send the release notes." }], tasks: tasks() };
  return { conversation, messages: messages(), tasks: tasks() };
}

export const SAMPLE_PROJECTS = [PROJECT, MOBILE, "/home/rem/Projects/second-brain"];
