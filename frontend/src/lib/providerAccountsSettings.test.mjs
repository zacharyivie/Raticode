import assert from "node:assert/strict";
import test from "node:test";
import { followRenamedAccount, normalizeAppSettings, providerAccountsValue, updateSetting } from "./settings.js";

test("Rem's account setting is per provider and still reads the Claude-only value", () => {
  // The first version kept one claudeAccount value; it becomes Claude Code's.
  const legacy = normalizeAppSettings({ assistant: { claudeAccount: "work" } });
  assert.deepEqual(legacy.assistant.providerAccounts, { claude_code: "work" });
  assert.equal("claudeAccount" in legacy.assistant, false);
  assert.deepEqual(normalizeAppSettings().assistant.providerAccounts, {});

  const both = updateSetting(legacy, "assistant.providerAccounts", { ...legacy.assistant.providerAccounts, codex: "auto" });
  assert.deepEqual(both.assistant.providerAccounts, { claude_code: "work", codex: "auto" });
  // Bad provider IDs and non-text values are dropped; names are capped.
  assert.deepEqual(providerAccountsValue({ "Not-An-Id": "x", codex: 4, grok: "a".repeat(200) }), { grok: "a".repeat(128) });
  assert.deepEqual(providerAccountsValue(["claude_code"]), {});
});

test("Rem's account setting follows an account renamed or removed in Settings > Providers", () => {
  const settings = normalizeAppSettings({ assistant: { providerAccounts: { claude_code: "work", codex: "auto" } } });
  const renamed = followRenamedAccount(settings, { provider: "claude_code", from: "work", to: "job" });
  assert.deepEqual(renamed.assistant.providerAccounts, { claude_code: "job", codex: "auto" });
  // Removing the account Rem used goes back to the default sign-in.
  const removed = followRenamedAccount(renamed, { provider: "claude_code", from: "job", to: "" });
  assert.deepEqual(removed.assistant.providerAccounts, { claude_code: "", codex: "auto" });
  // A setting that names another account, or "auto", is left alone.
  assert.equal(followRenamedAccount(settings, { provider: "claude_code", from: "other", to: "x" }), settings);
  assert.equal(followRenamedAccount(settings, { provider: "codex", from: "work", to: "x" }), settings);
});
