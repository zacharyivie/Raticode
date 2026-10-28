import test from "node:test";
import assert from "node:assert/strict";
import { workflowRunProblems, mergeWorkflowRunRecords, markWorkflowRunReviewed, restoreWorkflowRunRecords, workflowRunSummary, exactWorkflowRunStopPath, disconnectWorkflowRunRecords, formatRunAge, pruneUnlistedWorkflowRuns, sortWorkflowRunsNewestFirst, workflowListStatus } from "./workflowRuns.js";

const atlas = { workflowId: "digest", projectPath: "/atlas", workflowName: "Digest" };
test("run identity separates projects, workflows and concurrent runs", () => {
  let records = mergeWorkflowRunRecords([], [{ id: "one", status: "running" }, { id: "two", status: "running" }], atlas);
  records = mergeWorkflowRunRecords(records, [{ id: "one", status: "error" }], { ...atlas, projectPath: "/beacon" });
  assert.equal(records.length, 3);
  assert.equal(workflowRunSummary(records).active.length, 2);
  assert.equal(workflowRunSummary(records).unreadFailures.length, 1);
});
test("review only clears the exact result and polling does not mark it unread again", () => {
  let records = mergeWorkflowRunRecords([], [{ id: "one", status: "error" }, { id: "two", status: "success" }], atlas);
  records = markWorkflowRunReviewed(records, records.find((record) => record.runId === "one").key);
  records = mergeWorkflowRunRecords(records, [{ id: "one", status: "error" }], atlas);
  assert.equal(records.find((record) => record.runId === "one").unread, false);
  assert.equal(records.find((record) => record.runId === "two").unread, true);
  records = mergeWorkflowRunRecords(records, [{ id: "three", status: "running" }], atlas);
  assert.equal(workflowRunSummary(records).unread.length, 1);
});
test("restart makes persisted active work disconnected and reconciles without guessing success", () => {
  const before = mergeWorkflowRunRecords([], [{ id: "one", status: "running" }, { id: "two", status: "error" }], atlas);
  let records = restoreWorkflowRunRecords(JSON.stringify({ version: 1, records: before }));
  assert.equal(records.find((record) => record.runId === "one").status, "disconnected");
  assert.equal(exactWorkflowRunStopPath(records.find((record) => record.runId === "one")), null);
  assert.equal(records.find((record) => record.runId === "two").unread, true);
  records = mergeWorkflowRunRecords(records, [{ id: "one", status: "success" }], atlas);
  assert.equal(records.find((record) => record.runId === "one").status, "success");
  assert.equal(records.find((record) => record.runId === "one").unread, true);
  assert.equal(records.find((record) => record.runId === "one").stale, false);
});
test("late active response cannot revert a terminal run", () => {
  const records = mergeWorkflowRunRecords([], [{ id: "one", status: "success" }], atlas);
  assert.deepEqual(mergeWorkflowRunRecords(records, [{ id: "one", status: "running" }], atlas), records);
});
test("stop targets one encoded run and rejects incomplete or stale identities", () => {
  assert.equal(exactWorkflowRunStopPath({ workflowId: "a/b", runId: "run 1", status: "running" }), "/workflows/a%2Fb/runs/run%201/stop");
  assert.equal(exactWorkflowRunStopPath({ workflowId: "a", status: "running" }), null);
  assert.equal(exactWorkflowRunStopPath({ workflowId: "a", runId: "1", status: "success" }), null);
});
test("disconnection only changes targeted active records and malformed sessions recover safely", () => {
  const records = mergeWorkflowRunRecords([], [{ id: "one", status: "running" }], atlas);
  assert.deepEqual(disconnectWorkflowRunRecords(records, "other"), records);
  assert.equal(disconnectWorkflowRunRecords(records, "digest")[0].status, "disconnected");
  for (const value of ["broken", "null", '{"version":2,"records":[]}', '{"version":1,"records":[null,{}]}']) assert.deepEqual(restoreWorkflowRunRecords(value), []);
});

test("queue cancellation uses the queue identity, while an acknowledged stop stays stopping until terminal confirmation", () => {
  let records = mergeWorkflowRunRecords([], [{ id: "queue one", status: "claimed", queueRun: true }], atlas);
  assert.equal(records[0].status, "running");
  assert.equal(exactWorkflowRunStopPath(records[0]), "/queue/queue%20one/cancel");
  records = records.map(record => ({ ...record, status: "stopping" }));
  records = mergeWorkflowRunRecords(records, [{ id: "queue one", status: "running" }], atlas);
  assert.equal(records[0].status, "stopping");
  assert.equal(exactWorkflowRunStopPath(records[0]), null);
  records = mergeWorkflowRunRecords(records, [{ id: "queue one", status: "cancelled" }], atlas);
  assert.equal(records[0].status, "cancelled");
  assert.equal(records[0].unread, true);
});

test("submission without an acknowledged execution cannot expose Stop", () => {
  assert.equal(exactWorkflowRunStopPath({ workflowId: "digest", runId: "pending", status: "submitting" }), null);
});

test("a queue entry and its execution log contribute one run to global counts", () => {
  const records = mergeWorkflowRunRecords([], [
    { id: "queued-one", status: "success", queueRun: true, logPath: "/logs/one" },
    { id: "execution-one", status: "success", logPath: "/logs/one" },
    { id: "execution-two", status: "running", logPath: "/logs/two" },
  ], atlas);
  assert.equal(workflowRunSummary(records).unread.length, 1);
  assert.equal(workflowRunSummary(records).active.length, 1);
});


test("Problems show failed nodes with their cause and clear after successful retry", () => {
  const events = [{ nodeId: "digest", status: "error", message: "Command exited with status 1." }];
  const nodes = { digest: { status: "error", message: "DNS resolution failed for www.youtube.com", error: { code: "RATTISH_RUNTIME_COMMAND_FAILED" } } };
  const problems = workflowRunProblems(nodes, events);
  assert.equal(problems.length, 1);
  assert.equal(problems[0].nodeId, "digest");
  assert.match(problems[0].message, /DNS resolution/);
  assert.equal(problems[0].runtime, true);
  assert.deepEqual(workflowRunProblems({ digest: { status: "success" } }, events), []);
});

test("Problems show run-level failures that no failed node explains", () => {
  const runLimit = { nodeId: "workflow", status: "failed", errorCode: "RATTISH_RUN_LIMIT_EXCEEDED", message: "Workflow failed: Node 'cap' reached its max-runs limit." };
  const [problem] = workflowRunProblems({ cap: { status: "success" } }, [runLimit]);
  assert.equal(problem.nodeId, "workflow");
  assert.equal(problem.code, "RATTISH_RUN_LIMIT_EXCEEDED");
  assert.match(problem.message, /max-runs limit/);

  const nodeFailure = { nodeId: "workflow", status: "failed", errorCode: "RATTISH_RUNTIME_COMMAND_FAILED", message: "Workflow failed: exit 1" };
  const nodes = { digest: { status: "error", message: "exit 1", error: { code: "RATTISH_RUNTIME_COMMAND_FAILED" } } };
  assert.deepEqual(workflowRunProblems(nodes, [nodeFailure]).map(item => item.nodeId), ["digest"]);
});

test("the latest run is the newest start, not the first one a newest-first log listing returned", () => {
  const records = mergeWorkflowRunRecords([], [
    { id: "new", status: "success", startedAt: "2026-10-26T12:00:00Z" },
    { id: "old", status: "error", startedAt: "2026-10-25T12:00:00Z" },
  ], atlas);
  assert.equal(workflowRunSummary(records, "digest").latest.runId, "new");
  assert.deepEqual(sortWorkflowRunsNewestFirst([...records, { runId: "undated" }]).map((record) => record.runId), ["new", "old", "undated"]);
});

test("runs the backend no longer lists are pruned only when the listing proves they are gone", () => {
  let records = mergeWorkflowRunRecords([], [
    { id: "kept", status: "success", startedAt: "2026-10-26T12:00:00Z" },
    { id: "deleted", status: "error", startedAt: "2026-10-26T11:00:00Z" },
    { id: "older-page", status: "error", startedAt: "2026-10-01T11:00:00Z" },
    { id: "active", status: "running", startedAt: "2026-10-26T13:00:00Z" },
  ], atlas);
  records = mergeWorkflowRunRecords(records, [{ id: "other", status: "error" }], { ...atlas, workflowId: "other" });
  const listed = [{ id: "kept", startedAt: "2026-10-26T12:00:00Z" }, { id: "page-end", startedAt: "2026-10-20T12:00:00Z" }];

  const partial = pruneUnlistedWorkflowRuns(records, "digest", listed, { complete: false });
  assert.deepEqual(partial.map((record) => record.runId).sort(), ["active", "kept", "older-page", "other"]);

  const complete = pruneUnlistedWorkflowRuns(records, "digest", listed, { complete: true });
  assert.deepEqual(complete.map((record) => record.runId).sort(), ["active", "kept", "other"]);

  const disconnected = disconnectWorkflowRunRecords(complete, "digest");
  assert.deepEqual(pruneUnlistedWorkflowRuns(disconnected, "digest", [], { complete: true }).map((record) => record.runId), ["other"]);
});

test("the workflow list reports compile errors, active runs, and the newest finished run", () => {
  const workflow = { id: "digest", status: "Ready" };
  assert.deepEqual(
    [workflowListStatus(workflow).key, workflowListStatus(workflow).label, workflowListStatus(workflow).detail],
    ["ready", "Ready", "Not run yet"],
  );
  const invalid = workflowListStatus({ ...workflow, invalid: true, status: "Error", validationError: "Line 7: Unknown node type." });
  assert.deepEqual([invalid.key, invalid.tone, invalid.detail], ["invalid", "error", "Line 7: Unknown node type."]);
  assert.equal(workflowListStatus(workflow, [], { submitting: true }).label, "Starting");

  let records = mergeWorkflowRunRecords([], [{ id: "old", status: "error", startedAt: "2026-10-26T10:00:00Z", finishedAt: "2026-10-26T10:01:00Z" }], atlas);
  const failed = workflowListStatus(workflow, records);
  assert.deepEqual([failed.label, failed.tone, failed.since, failed.unread], ["Failed", "error", "2026-10-26T10:01:00Z", true]);

  records = mergeWorkflowRunRecords(records, [
    { id: "one", status: "running", startedAt: "2026-10-26T11:00:00Z" },
    { id: "two", status: "queued", startedAt: "2026-10-26T12:00:00Z" },
  ], atlas);
  const active = workflowListStatus(workflow, records);
  assert.deepEqual([active.label, active.tone, active.detail], ["Queued", "running", "2 runs in progress"]);

  // A compile error outranks run history because the workflow cannot run.
  assert.equal(workflowListStatus({ ...workflow, invalid: true }, records).key, "invalid");
  // Legacy payloads carry the newest run status before the registry scans logs.
  assert.equal(workflowListStatus({ id: "legacy", status: "Error" }).label, "Failed");
  assert.equal(workflowListStatus({ id: "legacy", status: "Scheduled" }).detail, "Scheduled");
});

test("the workflow list says a run is waiting for approval instead of running", () => {
  const workflow = { id: "digest", status: "Ready" };
  let records = mergeWorkflowRunRecords([], [{ id: "gate", status: "running", awaitingApproval: true, startedAt: "2026-10-26T11:00:00Z" }], atlas);
  const waiting = workflowListStatus(workflow, records);
  assert.deepEqual([waiting.label, waiting.tone], ["Awaiting approval", "warning"]);

  records = mergeWorkflowRunRecords(records, [{ id: "gate", status: "running", awaitingApproval: false }], atlas);
  assert.equal(workflowListStatus(workflow, records).label, "Running");
  records = mergeWorkflowRunRecords(records, [{ id: "gate", status: "success", awaitingApproval: true }], atlas);
  assert.equal(records[0].awaitingApproval, false);
});

test("run ages stay short and ignore missing timestamps", () => {
  const now = Date.parse("2026-10-26T12:00:00Z");
  assert.equal(formatRunAge("2026-10-26T11:59:40Z", now), "just now");
  assert.equal(formatRunAge("2026-10-26T11:55:00Z", now), "5m ago");
  assert.equal(formatRunAge("2026-10-26T09:00:00Z", now), "3h ago");
  assert.equal(formatRunAge("2026-10-24T12:00:00Z", now), "2d ago");
  assert.equal(formatRunAge(null, now), "");
});
