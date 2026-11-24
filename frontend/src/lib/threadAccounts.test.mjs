import assert from "node:assert/strict";
import { test } from "node:test";
import { accountChangeWarning, accountMenu, accountSwitchLabel, DEFAULT_SIGN_IN, readingLabel, threadAccountChoice, turnAccount, withThreadAccount } from "./threadAccounts.js";

const now = Date.parse("2026-11-17T12:00:00Z");
const accounts = [
  { name: "work", provider: "claude_code", state: "signed_in", last_reading: { seven_day: 42.4, five_hour: null, observed_at: "2026-11-17T11:30:00Z" } },
  { name: "old", provider: "claude_code", state: "needs_sign_in", needs_sign_in: true },
  { name: "other", provider: "codex", state: "signed_in" },
];

test("each thread keeps one account choice per provider", () => {
  const thread = { accountsByProvider: withThreadAccount(undefined, "codex", "other") };
  assert.equal(threadAccountChoice(thread, "codex"), "other");
  assert.equal(threadAccountChoice(thread, "claude_code"), "");
  assert.deepEqual(withThreadAccount(thread.accountsByProvider, "codex", ""), {});
  assert.deepEqual(withThreadAccount({ codex: "other" }, "claude_code", DEFAULT_SIGN_IN), { codex: "other", claude_code: DEFAULT_SIGN_IN });
});

test("a turn sends the thread's choice, else Rem's setting", () => {
  assert.equal(turnAccount({ provider: "codex", threadAccount: "other", providerAccounts: { codex: "auto" } }), "other");
  assert.equal(turnAccount({ provider: "codex", threadAccount: "", providerAccounts: { codex: "auto" } }), "auto");
  assert.equal(turnAccount({ provider: "claude_code", claudeAccount: "work" }), "work");
  assert.equal(turnAccount({ provider: "codex" }), undefined);
});

test("the menu lists Rem's setting, the default sign-in and the provider's accounts", () => {
  assert.equal(accountMenu({ provider: "grok", accounts, now }), null);
  const menu = accountMenu({ provider: "claude_code", accounts, remSetting: "auto", choice: "", now });
  assert.deepEqual(menu.options.map(option => option.id), ["", DEFAULT_SIGN_IN, "work", "old"]);
  assert.equal(menu.label, "Rem’s setting · Choose by usage");
  assert.equal(menu.options[2].meta, "Signed in · Week 42% used, 30 min ago");
  assert.equal(menu.options[3].disabled, true);
  assert.equal(menu.options[3].needsSignIn, true);
  const removed = accountMenu({ provider: "claude_code", accounts, choice: "gone", now });
  assert.equal(removed.options.at(-1).disabled, true);
  assert.equal(accountMenu({ provider: "claude_code", accounts, choice: DEFAULT_SIGN_IN, now }).label, "Default sign-in");
});

test("labels for readings, the divider and the warning", () => {
  assert.equal(readingLabel(null), "");
  assert.equal(readingLabel({ seven_day: 5, five_hour: 80, observed_at: "bad" }, now), "Week 5% · 5h 80% used");
  assert.equal(readingLabel({ five_hour: 1, observed_at: "2026-11-14T12:00:00Z" }, now), "5h 1% used, 3 days ago");
  assert.equal(accountSwitchLabel(DEFAULT_SIGN_IN), "Default sign-in");
  assert.equal(accountSwitchLabel("", "work"), "Rem’s setting (work)");
  assert.match(accountChangeWarning("Codex", "other"), /^Your next message starts a new Codex session on other, as switching providers does\./);
});
