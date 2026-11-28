import assert from "node:assert/strict";
import test from "node:test";
import { allowanceLabel, checkedLabel, costLabel, formatUsd, formatUsageAmount, formatUsageNumber, formatUsageTime, paceText, paceTime, remainingPercent, resetCountdown, signInLabel, usageCards, usageDashboardUrl, usageSignIn, usageStatus, weeklyBurndown } from "./usage.js";

test("usage retains missing values and the provider's units", () => {
  assert.equal(formatUsageNumber(null), "Not reported");
  assert.equal(formatUsageNumber(undefined), "Not reported");
  assert.equal(formatUsageNumber(NaN), "Not reported");
  assert.equal(formatUsageNumber(0), "0");
  assert.equal(allowanceLabel({ remaining: 75, unit: "percent" }), "75% remaining");
  assert.equal(allowanceLabel({ remaining: 42, unit: "requests" }), "42 requests remaining");
  assert.equal(allowanceLabel({ remaining: 0, unit: "credits" }), "0 credits remaining");
  assert.equal(formatUsageAmount(12.5, "USD"), "$12.50");
  assert.equal(allowanceLabel({ used: 12, unit: "tokens" }), "12 tokens used");
  assert.equal(allowanceLabel({ unlimited: true }), "Unlimited");
  assert.equal(allowanceLabel({}), "Not reported");
});

test("allowance meter requires a real percentage or a remaining value and limit", () => {
  assert.equal(remainingPercent({}), null);
  assert.equal(remainingPercent({ remaining: 0 }), null);
  assert.equal(remainingPercent({ remaining: null, limit: 100 }), null);
  assert.equal(remainingPercent({ remaining: 0, limit: 100 }), 0);
  assert.equal(remainingPercent({ remaining: 75, limit: 150 }), 50);
  assert.equal(remainingPercent({ remaining: 75, limit: 0 }), null);
  assert.equal(remainingPercent({ remaining_percent: 110 }), 100);
  assert.equal(remainingPercent({ remaining_percent: -5 }), 0);
});

test("usage statuses and times do not hide incomplete snapshots", () => {
  assert.equal(usageStatus("auth_required"), "Sign in required");
  assert.equal(usageStatus("stale"), "Out of date");
  assert.equal(usageStatus("error"), "Could not refresh");
  assert.equal(usageStatus("unexpected"), "Not reported");
  assert.equal(formatUsageTime(null), "Not reported");
  assert.equal(formatUsageTime("garbage"), "Not reported");
  assert.equal(formatUsageTime(1800000000), formatUsageTime("2027-01-15T08:00:00Z"));
});

test("provider links accept only secure external dashboard URLs", () => {
  assert.equal(usageDashboardUrl("https://claude.ai/settings/usage"), "https://claude.ai/settings/usage");
  for (const value of [null, "", "javascript:alert(1)", "file:///tmp/usage", "http://localhost"]) assert.equal(usageDashboardUrl(value), null);
});

test("sign-in help appears only for accounts that need it", () => {
  assert.equal(usageSignIn({ provider: "claude_code", status: "available" }), null);
  assert.equal(usageSignIn({ provider: "anthropic_api", status: "auth_required" }), null);
  assert.deepEqual(usageSignIn({ provider: "claude_code", status: "auth_required" }), { browser: true, command: "claude auth login" });
  assert.deepEqual(usageSignIn({ provider: "antigravity", status: "auth_required" }), { browser: false, command: "agy" });
  // Grok's default sign-in starts from Settings like the others (grok login opens the browser).
  assert.deepEqual(usageSignIn({ provider: "grok", status: "auth_required" }), { browser: true, command: "grok login" });
  assert.deepEqual(usageSignIn({ provider: "grok", profile: "work", status: "auth_required" }), { browser: false, command: null });
  assert.deepEqual(usageSignIn({ provider: "codex", profile: "work", status: "auth_required" }), { browser: false, command: null });
});

test("API cost estimates read as estimates and never turn unknown into $0", () => {
  assert.equal(formatUsd(null), "Unknown");
  assert.equal(formatUsd(undefined), "Unknown");
  assert.equal(formatUsd(0), "$0.00");
  assert.equal(formatUsd(0.004), "<$0.01");
  assert.equal(formatUsd(-12.5), "−$12.50");
  assert.equal(costLabel({ cost_usd: null, lower_bound: true }), "Unknown");
  assert.equal(costLabel({ cost_usd: 3.2, lower_bound: true }), "at least $3.20");
  assert.equal(costLabel({ cost_usd: 3.2, lower_bound: false }), "$3.20");
});

test("reset countdown reads in days, hours and minutes from now", () => {
  const now = Date.parse("2026-11-17T12:00:00Z");
  assert.equal(resetCountdown("2026-11-17T14:14:00Z", now), "resets in 2h 14m");
  assert.equal(resetCountdown("2026-11-17T14:14:59Z", now), "resets in 2h 14m");
  assert.equal(resetCountdown("2026-11-17T13:00:00Z", now), "resets in 1h 0m");
  assert.equal(resetCountdown("2026-11-17T12:09:00Z", now), "resets in 9m");
  assert.equal(resetCountdown("2026-11-17T12:00:30Z", now), "resets in under a minute");
  assert.equal(resetCountdown("2026-11-20T15:30:00Z", now), "resets in 3d 3h");
  // Seconds since the epoch, as some providers send them.
  assert.equal(resetCountdown(Date.parse("2026-11-17T12:45:00Z") / 1000, now), "resets in 45m");
  // A passed reset means the reading is out of date, not that the window is empty.
  assert.equal(resetCountdown("2026-11-17T11:59:00Z", now), "reset time passed");
  assert.equal(resetCountdown(null, now), "");
  assert.equal(resetCountdown("not a date", now), "");
});

test("last-checked time is relative for a week, then a date", () => {
  const now = Date.parse("2026-11-17T12:00:00Z");
  assert.equal(checkedLabel("2026-11-17T11:59:40Z", now), "Checked just now");
  assert.equal(checkedLabel("2026-11-17T11:46:00Z", now), "Checked 14 min ago");
  assert.equal(checkedLabel("2026-11-17T09:00:00Z", now), "Checked 3 h ago");
  assert.equal(checkedLabel("2026-11-15T09:00:00Z", now), "Checked 2 days ago");
  assert.equal(checkedLabel("2026-11-01T09:00:00Z", now), "Checked Nov 1");
  assert.equal(checkedLabel(null, now), "Not checked yet");
});

test("Claude Code cards show both windows side by side, one card per account, never combined", () => {
  const cards = usageCards([
    { id: "claude_code", provider: "claude_code", profile: null, status: "available", sign_in_state: "signed_in", observed_at: "2026-11-17T11:50:00Z",
      windows: [{ id: "seven_day", label: "Weekly allowance", remaining_percent: 60, resets_at: "2026-11-20T00:00:00Z" }, { id: "five_hour", label: "Five-hour allowance", remaining_percent: 90 }, { id: "seven_day_opus", label: "Weekly Opus allowance", remaining_percent: 80 }] },
    { id: "p:work", provider: "claude_code", profile: "work", status: "available", sign_in_state: "signed_in", windows: [{ id: "five_hour", remaining_percent: 10 }],
      rem_next: { why: "Most room left: 4% of its weekly allowance used." } },
    { id: "p:home", provider: "claude_code", profile: "home", status: "auth_required", sign_in_state: "needs_sign_in", windows: [] },
    { id: "codex", provider: "codex", profile: null, status: "available", windows: [{ id: "primary", label: "5h limit", remaining_percent: 50 }] },
    { id: "cursor", provider: "cursor", status: "not_installed", windows: [] },
    { id: "openai_api", provider: "openai_api", status: "unavailable", windows: [] },
  ]);
  assert.deepEqual(cards.map(card => card.accountName), ["Default sign-in", "work", "home", "Default account"]);
  const [main, work, home, codex] = cards;
  assert.deepEqual(main.meters.map(meter => meter.label), ["5-hour", "Weekly", "Weekly Opus"]);
  assert.equal(main.meters[0].window.remaining_percent, 90);
  assert.equal(main.signIn, "signed_in");
  // A window with no reading still has its place, so the pair never collapses to "+1 more".
  assert.deepEqual(work.meters.map(meter => [meter.label, meter.window?.remaining_percent ?? null]), [["5-hour", 10], ["Weekly", null]]);
  assert.equal(home.needsSignIn, true);
  assert.equal(codex.signIn, null);
  assert.deepEqual(codex.meters.map(meter => meter.label), ["5h limit"]);
  // No card carries a figure for several accounts at once.
  assert.ok(cards.every(card => card.meters.every(meter => !meter.window || meter.window === card.account.windows.find(window => window.id === meter.window.id))));
});

test("only the account Rem picks next carries the badge and its reason", () => {
  const cards = usageCards([
    { id: "claude_code", provider: "claude_code", status: "available", windows: [] },
    { id: "p:work", provider: "claude_code", profile: "work", status: "available", sign_in_state: "signed_in", windows: [], rem_next: { why: "Most room left: 4% of its weekly allowance used." } },
    { id: "p:home", provider: "claude_code", profile: "home", status: "available", sign_in_state: "signed_in", windows: [] },
  ]);
  assert.deepEqual(cards.map(card => card.remNext), [null, { why: "Most room left: 4% of its weekly allowance used." }, null]);
  // Without "choose by usage" the desktop sends no rem_next, so no card has the badge.
  assert.ok(usageCards([{ id: "claude_code", provider: "claude_code", status: "available", windows: [] }]).every(card => card.remNext === null));
  // A Claude Code account's live sign-in check wins over an older usage reading.
  const [signedBackIn] = usageCards([{ id: "p:x", provider: "claude_code", profile: "x", status: "auth_required", sign_in_state: "signed_in", windows: [] }]);
  assert.equal(signedBackIn.needsSignIn, false);
});

test("Codex account cards carry their own sign-in state, Rem's pick and both windows", () => {
  const cards = usageCards([
    { id: "codex", provider: "codex", profile: null, status: "available", windows: [{ id: "codex:primary", label: "codex · primary · 5-hour", remaining_percent: 70 }] },
    { id: "p:work", provider: "codex", profile: "work", status: "available", sign_in_state: "signed_in", rem_next: { why: "Most room left." },
      windows: [{ id: "codex:primary", label: "codex · primary · 5-hour", remaining_percent: 40 }, { id: "codex:secondary", label: "codex · secondary · 7-day", remaining_percent: 75 }] },
    { id: "p:home", provider: "codex", profile: "home", status: "available", sign_in_state: "needs_sign_in", windows: [] },
    { id: "p:gone", provider: "codex", profile: "gone", status: "auth_required", sign_in_state: "signed_in", windows: [] },
  ]);
  const [main, work, home, gone] = cards;
  // The default sign-in has no status check of its own.
  assert.equal(main.signIn, null);
  assert.equal(work.signIn, "signed_in");
  assert.deepEqual(work.remNext, { why: "Most room left." });
  assert.deepEqual(work.meters.map(meter => meter.label), ["codex · primary · 5-hour", "codex · secondary · 7-day"]);
  assert.equal(home.needsSignIn, true);
  // Codex's own account/read is newer than an older usage refresh that failed.
  assert.equal(gone.needsSignIn, false);
  assert.equal(signInLabel("unavailable", "Codex"), "Codex not found");
  assert.equal(signInLabel("unavailable"), "Claude Code not found");
  assert.equal(signInLabel("signed_in", "Codex"), "Signed in");
});

test("pace text names the day and hour it runs out, and says when readings are too few", () => {
  assert.equal(paceTime("2026-11-19T14:00:00Z", "UTC"), "Thu 14:00");
  assert.equal(paceText({ state: "runs_out", runs_out_at: "2026-11-19T14:00:00Z" }, "UTC"), "At this pace, runs out around Thu 14:00");
  assert.equal(paceText({ state: "on_pace" }), "On pace to last until reset");
  assert.equal(paceText({ state: "not_enough" }), "Not enough readings yet");
  assert.equal(paceText(null), "Not enough readings yet");
  assert.equal(paceText({ state: "used_up" }), "Used up until reset");
});

test("weekly burndown steps through the readings and holds the last one until it was checked", () => {
  const account = {
    observed_at: "2026-11-17T12:00:00Z",
    weekly_burndown: {
      label: "Weekly allowance", starts_at: "2026-11-14T12:00:00Z", resets_at: "2026-11-21T12:00:00Z", reading_count: 2,
      readings: [{ observed_at: "2026-11-15T12:00:00Z", used_percent: 10 }, { observed_at: "2026-11-16T12:00:00Z", used_percent: 20 }],
      pace: { state: "runs_out", runs_out_at: "2026-11-19T14:00:00Z", text: "server text" },
    },
  };
  const chart = weeklyBurndown(account, "UTC");
  assert.equal(chart.label, "Weekly");
  assert.equal(chart.count, 2);
  assert.equal(chart.left, 80);
  assert.equal(chart.state, "runs_out");
  assert.equal(chart.text, "At this pace, runs out around Thu 14:00");
  const round = points => points.map(([x, y]) => [Math.round(x * 7 * 100) / 100, y]);
  // Day 1 at 10% used, held to day 2, then 20% held to the check on day 3.
  assert.deepEqual(round(chart.points), [[1, 0.1], [2, 0.1], [2, 0.2], [3, 0.2]]);
  assert.equal(weeklyBurndown({ weekly_burndown: null }), null);
  assert.equal(weeklyBurndown({ weekly_burndown: { starts_at: "x", resets_at: "y" } }), null);
  const empty = weeklyBurndown({ weekly_burndown: { starts_at: "2026-11-14T12:00:00Z", resets_at: "2026-11-21T12:00:00Z", readings: [], pace: { state: "not_enough" } } });
  assert.deepEqual(empty.points, []);
  assert.equal(empty.left, null);
  assert.equal(empty.text, "Not enough readings yet");
});
