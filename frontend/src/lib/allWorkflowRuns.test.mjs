import assert from "node:assert/strict";
import test from "node:test";
import { formatDuration, lifecycleVerb, reviewRecord, startedByLabel, usageCells, workflowRunsQuery } from "./allWorkflowRuns.js";

test("the run list query carries only the filters that are set", () => {
  assert.equal(workflowRunsQuery(), "/rattish/runs?limit=200");
  assert.equal(workflowRunsQuery({ status: "error", workflow: "nightly", since: "24h", limit: 50, offset: 50 }), "/rattish/runs?status=error&workflow=nightly&since=24h&limit=50&offset=50");
  assert.equal(workflowRunsQuery({ since: "2026-11-13T22:10:00+00:00" }), "/rattish/runs?since=2026-11-13T22%3A10%3A00%2B00%3A00&limit=200");
});

test("rows say how a run started, how long it took and what it cost", () => {
  assert.deepEqual(["manual", "trigger", "phone", "rem", undefined].map(startedByLabel), ["Manual", "Trigger", "Phone", "Rem", "Manual"]);
  assert.equal(formatDuration(4.4), "4s");
  assert.equal(formatDuration(75), "1m 15s");
  assert.equal(formatDuration(3725), "1h 2m");
  assert.equal(formatDuration(0, true), "Running");
  assert.deepEqual(usageCells(null), { tokens: "–", cost: "–" });
  assert.deepEqual(usageCells({ tokensLabel: "1,200", costLabel: "$0.30" }), { tokens: "1,200", cost: "$0.30" });
});

test("a row opens in the Studio the way the Runs panel opens a run", () => {
  assert.deepEqual(reviewRecord({ workflowId: "w", workflowName: "W", id: "r", status: "error", projectRoot: "/p", startedAt: "a", finishedAt: "b" }), {
    workflowId: "w", workflowName: "W", runId: "r", status: "error", projectPath: "/p", startedAt: "a", finishedAt: "b",
  });
});

test("the lifecycle dialog's button says what Raticode is about to do", () => {
  assert.equal(lifecycleVerb("quit Raticode"), "Quit");
  assert.deepEqual(["install the update", "restart Raticode", "restart the backend"].map(lifecycleVerb), ["Restart", "Restart", "Restart"]);
  assert.equal(lifecycleVerb("change the data directory"), "Continue");
});
