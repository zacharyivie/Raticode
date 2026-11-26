import assert from "node:assert/strict";
import test from "node:test";
import { switchedProvider } from "./providerFallback.js";

test("an account passed over at a usage limit is a skip, not a switch", () => {
  const personal = { provider: "claude-code", profile: "personal", model: "claude-sonnet-5" };
  const work = { ...personal, profile: "work-account" };
  const codex = { provider: "codex", profile: null, model: "gpt-5" };
  const skip = { from: personal, to: work, reason: "rate_limit", skipped: true, message: "" };
  assert.equal(switchedProvider([skip]), false);
  assert.equal(switchedProvider([skip, { from: personal, to: codex, reason: "rate_limit" }]), true);
  assert.equal(switchedProvider(undefined), false);
});
