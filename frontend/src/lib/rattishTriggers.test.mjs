import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalNotice,
  approvedLine,
  budgetSummary,
  changedDuringRunLine,
  dailyLimitText,
  draftFromTrigger,
  emptyTriggerDraft,
  fileChangeLabel,
  formatRunTime,
  formatRunTimeInput,
  isProblemStatus,
  modelNoticeText,
  modelNoticeTitle,
  modelNotices,
  parseDailyLimit,
  parseRunTime,
  pauseSummary,
  schedulerSummary,
  statusLabel,
  triggerBodyFromDraft,
  triggerConfirmText,
  triggerDetail,
  triggerPolicyText,
  triggerTitle,
  unsandboxedStepText,
  unseenFiles,
  unseenReasonText,
  unsandboxedSteps,
  widenedStepText,
  widenedSteps,
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
  maxRunSeconds: 6 * 3600,
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

test("a pause over all triggers wins over the scheduler line and says how resuming works", () => {
  const paused = schedulerSummary({ running: true, owner: "app", paused: true });
  assert.equal(paused.paused, true);
  assert.match(paused.text, /^All triggers are paused\./);
  assert.equal(pauseSummary({ pause: { paused: false }, triggers: 0, enabled: 0 }), "No triggers yet.");
  assert.equal(pauseSummary({ pause: { paused: false }, triggers: 1, enabled: 1 }), "1 of 1 trigger on.");
  assert.match(
    pauseSummary({ pause: { paused: true, pausedAt: null }, triggers: 4, enabled: 3 }),
    /^Paused\. Cron times are skipped, not caught up\. Resuming turns back on the 3 of 4 that were on\.$/,
  );
});

test("triggers describe what starts them and what happens on overlap", () => {
  assert.equal(triggerTitle(cron), "Schedule");
  assert.equal(triggerTitle({ ...cron, label: "Morning digest" }), "Morning digest");
  assert.equal(triggerDetail(cron), "0 9 * * 1-5 · America/New_York");
  assert.equal(triggerDetail({ ...cron, summary: "Weekdays at 9:00" }), "Weekdays at 9:00 · America/New_York");
  assert.equal(
    triggerPolicyText(cron),
    "If already running: run once after it finishes · catches up one missed run · time limit: 6 h",
  );
  const file = {
    ...cron,
    kind: "file",
    overlap: "skip",
    config: { path: "/work/inbox", glob: "*.csv", debounceSeconds: 2 },
  };
  assert.equal(triggerDetail(file), "/work/inbox · *.csv");
  assert.equal(
    triggerPolicyText({ ...file, maxRunSeconds: null }),
    "If already running: skip this run · waits 2s for quiet · no time limit",
  );
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
    runEditsWithoutApproval: false,
    maxRunSeconds: 6 * 3600,
    config: { expression: "0 9 * * 1-5", timezone: "America/New_York" },
    inputs: { topic: "news" },
  });
  const trusting = draftFromTrigger({ ...cron, runEditsWithoutApproval: true });
  assert.equal(triggerBodyFromDraft(trusting).runEditsWithoutApproval, true);
});

test("a changed or never-approved workflow says why the trigger won't run it", () => {
  assert.equal(approvalNotice({ approval: { state: "approved" } }), "");
  assert.equal(approvalNotice({ approval: { state: "changed" } }), "Workflow changed since you approved it");
  assert.equal(
    approvalNotice({ approval: { state: "unapproved" } }),
    "Waiting for you to approve this workflow version",
  );
  // With Run edits without approval on, nothing waits for approval.
  assert.equal(approvalNotice({ runEditsWithoutApproval: true, approval: { state: "changed" } }), "");
  assert.match(approvedLine({ approvedAt: "2026-11-16T09:00:00Z", approvedBy: "studio" }), /^This version was approved .+ by saving it in Studio\.$/);
  assert.equal(approvedLine({}), "");
  assert.equal(fileChangeLabel("removed"), "No longer used");
});

test("Review changes names a file that changed during a triggered run", () => {
  assert.equal(
    changedDuringRunLine({ path: "scripts/later.sh", runId: "run-1" }),
    "scripts/later.sh changed during a triggered run, so the step that uses it wasn't run.",
  );
  assert.equal(changedDuringRunLine(null), "");
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

test("daily limits stay as typed until saving, and an emptied one is removed", () => {
  const draft = { ...emptyTriggerDraft("cron"), expression: "0 9 * * *", dailyMaxCostUsd: "$2.50", dailyMaxTokens: "50,000" };
  const created = triggerBodyFromDraft(draft);
  assert.equal(created.dailyMaxCostUsd, 2.5);
  assert.equal(created.dailyMaxTokens, 50000);
  assert.equal("dailyMaxCostUsd" in triggerBodyFromDraft(emptyTriggerDraft("webhook")), false);
  assert.throws(() => triggerBodyFromDraft({ ...draft, dailyMaxCostUsd: "lots" }), /daily cost limit must be a number/);
  assert.throws(() => triggerBodyFromDraft({ ...draft, dailyMaxTokens: "1.5" }), /whole number/);

  const saved = { ...cron, dailyMaxCostUsd: 2, dailyMaxTokens: null };
  const unchanged = triggerBodyFromDraft(draftFromTrigger(saved), saved);
  assert.equal("dailyMaxCostUsd" in unchanged || "dailyMaxTokens" in unchanged, false);
  const removed = triggerBodyFromDraft({ ...draftFromTrigger(saved), dailyMaxCostUsd: "" }, saved);
  assert.equal(removed.dailyMaxCostUsd, null);
  assert.equal(parseDailyLimit("0", "cost"), null);
});

test("daily limit lines say what is used and when starts resume", () => {
  assert.equal(dailyLimitText(null), "");
  assert.equal(
    dailyLimitText({ summary: "$0.40 of $1.00 used today", reached: false, timezone: "UTC" }),
    "Daily limit: $0.40 of $1.00 used today.",
  );
  assert.equal(
    dailyLimitText({ summary: "$1.20 of $1.00 used today", reached: true, timezone: "Europe/Paris" }),
    "Daily limit: $1.20 of $1.00 used today. Triggered starts are skipped until midnight (Europe/Paris).",
  );
  assert.equal(budgetSummary({ budget: { maxCostUsd: null, maxTokens: null }, today: null }), "No daily limit for triggered runs.");
});

test("unsandboxed steps come from the payload and read as node, provider and reason", () => {
  assert.deepEqual(unsandboxedSteps(null), []);
  assert.deepEqual(unsandboxedSteps({ sandbox: {} }), []);
  const step = { node: "fix", provider: "claude-code", reason: "the step sets sandbox: off" };
  assert.deepEqual(unsandboxedSteps({ sandbox: { unsandboxed: [step] } }), [step]);
  assert.equal(unsandboxedStepText(step), "fix on claude-code: the step sets sandbox: off");
});

test("steps whose workflow widens their sandbox are listed and need confirming", () => {
  assert.deepEqual(widenedSteps(null), []);
  assert.deepEqual(widenedSteps({ sandbox: { unsandboxed: [] } }), []);
  const step = { node: "fetch", kind: "network", reason: "its shell can reach any host" };
  assert.deepEqual(widenedSteps({ sandbox: { widened: [step] } }), [step]);
  assert.equal(widenedStepText(step), "fetch: its shell can reach any host");
  assert.equal(triggerConfirmText([], []), "");
  assert.equal(
    triggerConfirmText([], [step]),
    "Widens its sandbox: 1 step can reach beyond the project, as the workflow file asks. Add the trigger anyway?",
  );
  const loose = { node: "fix", provider: "claude-code", reason: "the step sets sandbox: off" };
  assert.equal(
    triggerConfirmText([loose, loose], [step]),
    "Runs unsandboxed: 2 steps will run with nobody watching and no sandbox. Widens its sandbox: 1 step can reach beyond the project, as the workflow file asks. Add the trigger anyway?",
  );
});

test("changes not shown in full are listed with why", () => {
  assert.deepEqual(unseenFiles({ unseen: [{ path: "a.md", reason: "cut" }] }), [{ path: "a.md", reason: "cut" }]);
  assert.deepEqual(unseenFiles({}), []);
  assert.deepEqual(unseenFiles(null), []);
  assert.equal(unseenReasonText("cut"), "Its diff was cut short");
  assert.equal(unseenReasonText("unreadable"), "Binary or too large to show");
  assert.equal(unseenReasonText("other"), "Not shown");
});

test("model notices come from the payload and say whether a model is retiring or retired", () => {
  assert.deepEqual(modelNotices(null), []);
  assert.deepEqual(modelNotices({ models: {} }), []);
  const retiring = {
    node: "summarize",
    provider: "anthropic-api",
    model: "claude-sonnet-4-5",
    state: "deprecated",
    message: "Model 'claude-sonnet-4-5' is deprecated and retires on November 30, 2026.",
  };
  assert.deepEqual(modelNotices({ models: { notices: [retiring] } }), [retiring]);
  assert.equal(
    modelNoticeText(retiring),
    "summarize on anthropic-api: Model 'claude-sonnet-4-5' is deprecated and retires on November 30, 2026.",
  );
  assert.equal(modelNoticeTitle([retiring]), "Model retiring");
  assert.equal(modelNoticeTitle([retiring, { ...retiring, state: "retired" }]), "Model retired");
});

test("the Time limit reads like 2h or 1h30m, starts at 6 hours, and empty means none", () => {
  assert.equal(parseRunTime("2h"), 7200);
  assert.equal(parseRunTime("1h30m"), 5400);
  assert.equal(parseRunTime(" 45 min "), 2700);
  assert.equal(parseRunTime(""), null);
  assert.equal(parseRunTime("none"), null);
  assert.throws(() => parseRunTime("30"), /must look like 30m/);
  assert.throws(() => parseRunTime("2 weeks"), /unknown unit/);
  assert.throws(() => parseRunTime("31d"), /at most 30 days/);
  assert.equal(formatRunTime(7 * 3600 + 12 * 60), "7 h 12 min");
  assert.equal(formatRunTimeInput(5400), "1h30m");

  const fresh = emptyTriggerDraft("webhook");
  assert.equal(fresh.maxRunTime, "6h");
  assert.equal(triggerBodyFromDraft(fresh).maxRunSeconds, 6 * 3600);
  assert.equal(triggerBodyFromDraft({ ...fresh, maxRunTime: "" }).maxRunSeconds, null);
  assert.throws(() => triggerBodyFromDraft({ ...fresh, maxRunTime: "soon" }), /time limit/);

  // Editing sends it only when it changed, and never to a backend that has none.
  const saved = { ...cron, maxRunSeconds: 1800 };
  assert.equal(draftFromTrigger(saved).maxRunTime, "30m");
  assert.equal("maxRunSeconds" in triggerBodyFromDraft(draftFromTrigger(saved), saved), false);
  assert.equal(triggerBodyFromDraft({ ...draftFromTrigger(saved), maxRunTime: "2h" }, saved).maxRunSeconds, 7200);
  const older = { ...cron };
  delete older.maxRunSeconds;
  assert.equal("maxRunSeconds" in triggerBodyFromDraft(draftFromTrigger(older), older), false);
});

test("the usage limit switch is on by default and sent only when it changes", () => {
  const fresh = emptyTriggerDraft("cron");
  assert.equal(fresh.waitForUsageLimit, true);
  assert.equal("waitForUsageLimit" in triggerBodyFromDraft(fresh), false);
  assert.equal(triggerBodyFromDraft({ ...fresh, waitForUsageLimit: false }).waitForUsageLimit, false);

  const saved = { ...cron, waitForUsageLimit: true };
  assert.equal("waitForUsageLimit" in triggerBodyFromDraft(draftFromTrigger(saved), saved), false);
  const off = triggerBodyFromDraft({ ...draftFromTrigger(saved), waitForUsageLimit: false }, saved);
  assert.equal(off.waitForUsageLimit, false);
  const failing = { ...cron, waitForUsageLimit: false };
  assert.equal(draftFromTrigger(failing).waitForUsageLimit, false);
  assert.equal(triggerBodyFromDraft({ ...draftFromTrigger(failing), waitForUsageLimit: true }, failing).waitForUsageLimit, true);
  // An older backend sends no switch: it reads as on, and nothing is sent.
  assert.equal("waitForUsageLimit" in triggerBodyFromDraft(draftFromTrigger(cron), cron), false);

  assert.match(triggerPolicyText(failing), /fails at a usage limit/);
  assert.doesNotMatch(triggerPolicyText(saved), /usage limit/);
  assert.equal(statusLabel("waiting"), "Waiting for usage limit");
  assert.equal(isProblemStatus("waiting"), false);
});
