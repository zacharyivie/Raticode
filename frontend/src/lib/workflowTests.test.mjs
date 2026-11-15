import assert from "node:assert/strict";
import test from "node:test";

import {
  caseChanges,
  caseFromDraft,
  checkSummary,
  comparisonSentence,
  costLabel,
  draftFromCase,
  formatUsd,
  newCheckDraft,
  nextCaseId,
  shortRevision,
} from "./workflowTests.js";

const sample = {
  id: "cats",
  name: "Cats",
  inputs: { topic: "cats" },
  expectStatus: "failed",
  checks: [
    { type: "text", target: "outputs.summary", contains: "Cats", ignoreCase: true },
    { type: "json", target: "nodes.write.score", oneOf: [1, 2] },
    { type: "json", target: "outputs.extra", exists: false },
    { type: "command", command: "grep -q cat", exitCode: 1, timeoutSeconds: 5 },
    { type: "judge", criteria: "Mentions cats.", provider: "codex", label: "On topic" },
  ],
};

test("a case survives a round trip through the editor draft", () => {
  const draft = draftFromCase(sample);
  assert.equal(draft.inputs, JSON.stringify({ topic: "cats" }, null, 2));
  assert.deepEqual(draft.checks.map((check) => check.operator), ["contains", "oneOf", "missing", "", ""]);
  assert.equal(draft.checks[1].value, "[1,2]");
  assert.deepEqual(caseFromDraft(draft).case, sample);
});

test("editing a case keeps its trigger events", () => {
  const webhookCase = {
    id: "approved",
    inputs: {},
    triggerEvents: [{ type: "webhook", trigger_id: "test", payload: { action: "publish" } }],
    checks: [],
  };
  const draft = draftFromCase(webhookCase);
  draft.name = "Approved request";
  assert.deepEqual(caseFromDraft(draft).case, { ...webhookCase, name: "Approved request" });
});

test("editor drafts stay text until save and explain bad values", () => {
  const draft = draftFromCase(sample);
  // A half-typed JSON value is kept as typed; only saving checks it.
  draft.checks[1].value = "[1,";
  assert.equal(draft.checks[1].value, "[1,");
  assert.match(caseFromDraft(draft).error, /Check 2: the value must be JSON/);
  draft.checks[1].value = "3";
  assert.match(caseFromDraft(draft).error, /needs a JSON list/);
  draft.checks[1].value = "[3]";
  draft.inputs = "[1]";
  assert.match(caseFromDraft(draft).error, /Inputs must be a JSON object/);
  draft.inputs = "";
  assert.deepEqual(caseFromDraft(draft).case.inputs, {});
  draft.checks[3].exitCode = "300";
  assert.match(caseFromDraft(draft).error, /exit code must be a whole number/);
  draft.checks[3].exitCode = "0";
  draft.checks[4].criteria = "  ";
  assert.match(caseFromDraft(draft).error, /describe what the model should check/);
});

test("case IDs must be valid and unique", () => {
  const draft = draftFromCase({ id: "bad id" });
  assert.match(caseFromDraft(draft).error, /case ID must start/);
  draft.id = "taken";
  assert.match(caseFromDraft(draft, ["taken"]).error, /already uses the ID taken/);
  assert.equal(nextCaseId([{ id: "case-2" }, { id: "other" }]), "case-3");
  assert.equal(nextCaseId([{ id: "case-1" }]), "case-2");
});

test("new checks default to the workflow outputs and omit defaults when saved", () => {
  const draft = draftFromCase({ id: "x", checks: [] });
  draft.checks.push(newCheckDraft("text"));
  draft.checks[0].value = "hello";
  draft.checks.push(newCheckDraft("command"));
  draft.checks[1].command = "true";
  assert.deepEqual(caseFromDraft(draft).case.checks, [
    { type: "text", contains: "hello" },
    { type: "command", command: "true" },
  ]);
});

test("costs, revisions and comparisons read as short sentences", () => {
  assert.equal(formatUsd(0), "$0");
  assert.equal(formatUsd(0.004), "$0.0040");
  assert.equal(formatUsd(1.234), "$1.23");
  assert.equal(formatUsd(null), "unknown");
  assert.equal(costLabel({ modelCalls: 0, costUsd: 0, costComplete: true }), "$0");
  assert.equal(costLabel({ modelCalls: 4, costUsd: 0.04, costComplete: true }), "~$0.04");
  assert.equal(costLabel({ modelCalls: 4, costUsd: 0.02, costComplete: false }), "≥$0.02");
  assert.equal(costLabel({ modelCalls: 4, costUsd: null, costComplete: false }), "cost unknown");
  assert.equal(shortRevision("sha256:abcdef0123456789"), "abcdef012345");
  const comparison = {
    previousRevision: "sha256:0011223344556677",
    summary: { broke: 1, fixed: 2, same: 3 },
    cases: [{ id: "a", change: "broke" }, { id: "b", change: "fixed" }],
  };
  assert.equal(comparisonSentence(comparison), "Compared with revision 001122334455: 1 broke, 2 fixed.");
  assert.deepEqual(caseChanges(comparison), { a: "broke", b: "fixed" });
  assert.equal(checkSummary(sample.checks[0]), 'outputs.summary contains "Cats"');
  assert.equal(checkSummary(sample.checks[2]), "outputs.extra is missing");
  assert.equal(checkSummary(sample.checks[4]), "On topic");
});
