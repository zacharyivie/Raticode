import test from "node:test";
import assert from "node:assert/strict";
import { accountAfterProviderChange, providerAccounts, showsAccountChoice, workerAccountOptions } from "./workerAccounts.js";

const accounts = [
  { provider: "codex", name: "work", account_id: "aaaaaaaaaaaaaaaa", state: "signed_in" },
  { provider: "codex", name: "spare", account_id: "bbbbbbbbbbbbbbbb", state: "needs_sign_in", needs_sign_in: true },
  { provider: "claude_code", name: "personal", account_id: "cccccccccccccccc", state: "signed_in" },
];

test("a worker's Account choice lists only its provider's accounts after the default sign-in", () => {
  assert.deepEqual(providerAccounts(accounts, "codex").map(a => a.name), ["work", "spare"]);
  const options = workerAccountOptions(accounts, "codex", null);
  assert.deepEqual(options.map(o => [o.id, o.label]), [["", "Default sign-in"], ["aaaaaaaaaaaaaaaa", "work"], ["bbbbbbbbbbbbbbbb", "spare"]]);
  assert.equal(options[1].meta, "Signed in");
  assert.match(options[2].meta, /^Needs sign-in: runs on the default sign-in/);
});

test("a saved account that was removed stays listed and says what its turns do", () => {
  const options = workerAccountOptions(accounts, "codex", "dddddddddddddddd");
  assert.deepEqual(options.at(-1), { id: "dddddddddddddddd", label: "Removed account", meta: "Runs on the default sign-in" });
});

test("switching provider clears an account that belongs to the old one", () => {
  assert.equal(accountAfterProviderChange(accounts, "claude_code", "aaaaaaaaaaaaaaaa"), null);
  assert.equal(accountAfterProviderChange(accounts, "codex", "aaaaaaaaaaaaaaaa"), "aaaaaaaaaaaaaaaa");
  assert.equal(accountAfterProviderChange(accounts, "codex", null), null);
  // Accounts that haven't loaded can't confirm it fits, so it's cleared too.
  assert.equal(accountAfterProviderChange([], "codex", "aaaaaaaaaaaaaaaa"), null);
});

test("the Account choice is hidden for a provider without accounts", () => {
  assert.equal(showsAccountChoice(accounts, "grok", null), false);
  // Unless the worker still names one, so it can be changed back.
  assert.equal(showsAccountChoice(accounts, "grok", "aaaaaaaaaaaaaaaa"), true);
  assert.equal(showsAccountChoice(accounts, "codex", null), true);
});
