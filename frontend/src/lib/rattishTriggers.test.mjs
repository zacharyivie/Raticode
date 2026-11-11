import assert from "node:assert/strict";
import test from "node:test";

import {
  draftFromTrigger,
  emptyTriggerDraft,
  isProblemStatus,
  schedulerSummary,
  triggerBodyFromDraft,
  triggerDetail,
  triggerPolicyText,
  triggerTitle,
} from "./rattishTriggers.js";

const cron = {
  id: "trg_1",
  kind: "cron",
  label: "",
  enabled: true,
  overlap: "queue",
  missed: "run_once",
  inputs: {},
  config: { expression: "0 9 * * 1-5", timezone: "America/New_York" },
};

test("scheduler summary says plainly when nothing will fire", () => {
  assert.deepEqual(schedulerSummary({ running: false }), {
    running: false,
    text: "Not running. Nothing will fire until Raticode or its scheduler starts.",
  });
  assert.equal(schedulerSummary(undefined).running, false);
  assert.equal(schedulerSummary({ running: true, owner: "app" }).text, "Running in Raticode");
  assert.equal(
    schedulerSummary({ running: true, owner: "background" }).text,
    "Running in the background scheduler",
  );
});

test("triggers describe what starts them and what happens on overlap", () => {
  assert.equal(triggerTitle(cron), "Schedule");
  assert.equal(triggerTitle({ ...cron, label: "Morning digest" }), "Morning digest");
  assert.equal(triggerDetail(cron), "0 9 * * 1-5 · America/New_York");
  assert.equal(
    triggerPolicyText(cron),
    "If already running: run once after it finishes · catches up one missed run",
  );
  const file = {
    ...cron,
    kind: "file",
    overlap: "skip",
    config: { path: "/work/inbox", glob: "*.csv", debounceSeconds: 2 },
  };
  assert.equal(triggerDetail(file), "/work/inbox · *.csv");
  assert.equal(triggerPolicyText(file), "If already running: skip this run · waits 2s for quiet");
});

test("missed, failed, skipped and interrupted runs count as problems", () => {
  for (const status of ["failed", "missed", "skipped", "interrupted"]) {
    assert.equal(isProblemStatus(status), true, status);
  }
  for (const status of ["passed", "running", "queued", "stopped"]) {
    assert.equal(isProblemStatus(status), false, status);
  }
});

test("a trigger draft round-trips to the saved shape", () => {
  const draft = draftFromTrigger({ ...cron, label: "Digest", inputs: { topic: "news" } });
  assert.deepEqual(triggerBodyFromDraft(draft), {
    label: "Digest",
    overlap: "queue",
    missed: "run_once",
    config: { expression: "0 9 * * 1-5", timezone: "America/New_York" },
    inputs: { topic: "news" },
  });
});

test("the file form keeps typed debounce text until saving", () => {
  const draft = { ...emptyTriggerDraft("file"), path: "inbox", debounceSeconds: "" };
  assert.throws(() => triggerBodyFromDraft(draft), /Debounce must be a number/);
  assert.equal(
    triggerBodyFromDraft({ ...draft, debounceSeconds: "1.5" }).config.debounceSeconds,
    1.5,
  );
});

test("workflow inputs must be a JSON object", () => {
  const draft = emptyTriggerDraft("webhook");
  assert.throws(() => triggerBodyFromDraft({ ...draft, inputs: "{nope" }), /valid JSON/);
  assert.throws(() => triggerBodyFromDraft({ ...draft, inputs: "[1]" }), /JSON object/);
  assert.deepEqual(triggerBodyFromDraft(draft).inputs, {});
});
