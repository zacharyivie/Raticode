import assert from "node:assert/strict";
import test from "node:test";
import { fallbackLines, providerLabel, switchedProvider } from "./providerFallback.js";

test("a provider label names the profile and model when they are set", () => {
  assert.equal(providerLabel({ provider: "codex", profile: null, model: "gpt-5", effort: "low" }), "codex (gpt-5)");
  assert.equal(providerLabel({ provider: "codex", profile: "work", model: "gpt-5" }), "codex profile work (gpt-5)");
  assert.equal(providerLabel(null), "");
});

test("each switch reads as one sentence with the reason in words", () => {
  const from = { provider: "claude-code", profile: null, model: "claude-sonnet-5", effort: "high" };
  const to = { provider: "codex", profile: null, model: "gpt-5", effort: "low" };
  assert.deepEqual(fallbackLines([
    { from, to, reason: "rate_limit", message: "429" },
    { from: to, to: from, reason: "not_installed", message: "" },
  ]), [
    "Switched from claude-code (claude-sonnet-5) to codex (gpt-5): rate limit or allowance used up.",
    "Switched from codex (gpt-5) to claude-code (claude-sonnet-5): CLI not installed.",
  ]);
  assert.deepEqual(fallbackLines(undefined), []);
  assert.deepEqual(fallbackLines([{ reason: "overloaded" }]), []);
});

test("an account passed over at a usage limit reads as a skip, not a switch", () => {
  const personal = { provider: "claude-code", profile: "personal", model: "claude-sonnet-5" };
  const work = { ...personal, profile: "work-account" };
  const codex = { provider: "codex", profile: null, model: "gpt-5" };
  const skip = { from: personal, to: work, reason: "rate_limit", skipped: true, message: "" };
  assert.deepEqual(fallbackLines([skip]), [
    "Skipped claude-code profile work-account (claude-sonnet-5): Raticode doesn't move work to another account of the same provider at a usage limit.",
  ]);
  assert.equal(switchedProvider([skip]), false);
  assert.equal(switchedProvider([skip, { from: personal, to: codex, reason: "rate_limit" }]), true);
  assert.equal(switchedProvider(undefined), false);
});
