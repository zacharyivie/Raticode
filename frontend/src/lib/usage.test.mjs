import assert from "node:assert/strict";
import test from "node:test";
import { allowanceLabel, formatUsageAmount, formatUsageNumber, formatUsageTime, remainingPercent, usageDashboardUrl, usageStatus } from "./usage.js";

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
