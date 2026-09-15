import test from "node:test";
import assert from "node:assert/strict";
import { mergeWorkflowRunRecords, markWorkflowRunReviewed, restoreWorkflowRunRecords, workflowRunSummary, exactWorkflowRunStopPath, disconnectWorkflowRunRecords } from "./workflowRuns.js";

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
