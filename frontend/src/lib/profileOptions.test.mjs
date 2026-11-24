import assert from "node:assert/strict";
import { test } from "node:test";
import { profileOptionLabel, profileSelectOptions, profileSubscription } from "./profileOptions.js";

const profiles = [
  { name: "work", subscription: "claude_code", account_id: "aaaaaaaaaaaaaaaa" },
  { name: "home", subscription: "claude_code", account_id: "bbbbbbbbbbbbbbbb", model: "opus" },
  { name: "api-key", subscription: "claude_code", model: "sonnet", env: { ANTHROPIC_API_KEY: "********" } },
  { name: "other", subscription: "codex", account_id: "cccccccccccccccc" },
];
const accountList = {
  accounts: [
    { name: "work", provider: "claude_code", label: "Claude Code · work", state: "needs_sign_in", needs_sign_in: true },
    { name: "home", provider: "claude_code", label: "Claude Code · home", state: "signed_in", needs_sign_in: false },
    { name: "other", provider: "codex", label: "Codex · other", state: "unavailable", needs_sign_in: false },
  ],
  providers: [{ id: "claude_code", label: "Claude Code" }, { id: "codex", label: "Codex" }],
};

test("account profiles read as accounts with their sign-in state; API-key profiles don't change", () => {
  assert.deepEqual(profileSelectOptions(profiles, "claude_code", accountList), [
    ["", "None"],
    ["work", "work · Claude Code account · Needs sign-in"],
    ["home", "home · Claude Code account · Signed in"],
    ["api-key", "api-key (sonnet)"],
  ]);
  assert.equal(profileOptionLabel(profiles[3], accountList), "other · Codex account · CLI not found");
});

test("before the accounts list loads, an account reads as an account without a state", () => {
  assert.equal(profileOptionLabel(profiles[0]), "work · Account");
  assert.equal(
    profileOptionLabel(profiles[0], { accounts: [{ name: "work", provider: "claude_code", label: "Claude Code · work" }] }),
    "work · Claude Code account · Not checked",
  );
});

test("a chosen profile the list doesn't have stays chosen", () => {
  const options = profileSelectOptions(profiles, "codex", { ...accountList, current: "gone" });
  assert.deepEqual(options.at(-1), ["gone", "gone · Not found"]);
  assert.equal(profileSelectOptions(profiles, "codex", { current: "other" }).length, 2);
});

test("workflow provider IDs name a profile's subscription", () => {
  assert.equal(profileSubscription("claude-code"), "claude_code");
  assert.equal(profileSubscription("anthropic-api"), "anthropic_api");
  assert.equal(profileSubscription("codex"), "codex");
});
