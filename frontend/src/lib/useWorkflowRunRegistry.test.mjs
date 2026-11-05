import assert from "node:assert/strict";
import test from "node:test";
import { workflowsToScan } from "./useWorkflowRunRegistry.js";

const workflows = ["idle", "active", "lost", "queued", "changed", "fresh"].map(id => ({ id }));
const latest = new Map([["idle", "a"], ["active", "b"], ["lost", "c"], ["queued", "d"], ["changed", "e2"], ["fresh", "f"]]);
const previousLatest = new Map([["idle", "a"], ["active", "b"], ["lost", "c"], ["queued", "d"], ["changed", "e1"]]);

test("a full scan reads every workflow", () => {
  assert.deepEqual(workflowsToScan(workflows, { full: true }).map(w => w.id), workflows.map(w => w.id));
});

test("between full scans only workflows that can have changed are read", () => {
  const scanned = workflowsToScan(workflows, {
    full: false,
    records: [
      { workflowId: "idle", status: "success" },
      { workflowId: "active", status: "running" },
      { workflowId: "lost", status: "disconnected" },
    ],
    queueRuns: [{ workflowId: "queued" }],
    latest,
    previousLatest,
  });
  // Idle has only finished runs and an unchanged latest run, so it waits for the next full scan.
  assert.deepEqual(scanned.map(w => w.id), ["active", "lost", "queued", "changed", "fresh"]);
});

test("without a workflow list, only active and queued work is read", () => {
  const scanned = workflowsToScan(workflows, {
    full: false,
    records: [{ workflowId: "active", status: "queued" }],
    previousLatest,
  });
  assert.deepEqual(scanned.map(w => w.id), ["active"]);
});
