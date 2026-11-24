import assert from "node:assert/strict";
import test from "node:test";
import { contextRows, contextSummary, currentRulesLine, formatChars, formatTokens, remCallRows, remCallTokensLine, standingRows } from "./contextSent.js";

const turn = {
  sent_chars: 4120,
  sent_tokens_estimate: 1030,
  parts: [
    { key: "identity", label: "Identity and task instructions", chars: 4000, tokens_estimate: 1000, sent: true, standing: true },
    { key: "brain", label: "Second Brain rules", chars: 800, tokens_estimate: 200, sent: false, standing: true },
    { key: "conversation", label: "Conversation", chars: 120, tokens_estimate: 30, sent: true, standing: false },
    { key: "theme", label: "Report theme", chars: 0, tokens_estimate: 0, sent: true, standing: true },
  ],
};

test("token estimates are marked with ~ and kept short", () => {
  assert.equal(formatTokens(0), "~0 tokens");
  assert.equal(formatTokens(1), "~1 token");
  assert.equal(formatTokens(999), "~999 tokens");
  assert.equal(formatTokens(1000), "~1k tokens");
  assert.equal(formatTokens(1250), "~1.3k tokens");
  assert.equal(formatTokens(12_400), "~12k tokens");
  assert.equal(formatTokens(2_500_000), "~2.5M tokens");
  assert.equal(formatChars(1), "1 char");
  assert.equal(formatChars(12345), "12,345 chars");
});

test("a turn lists what it sent biggest first, then the parts a resumed session kept", () => {
  const rows = contextRows(turn);
  assert.deepEqual(rows.map(row => [row.key, row.sent]), [["identity", true], ["conversation", true], ["brain", false]]);
  assert.equal(rows[0].width, 100);
  assert.equal(rows[1].width, 3);
  assert.equal(rows[2].width, 20);
  assert.equal(rows[1].standing, false);
  assert.equal(contextSummary(turn), "~1k tokens sent · instructions kept from the previous turn");
  assert.equal(contextSummary({ ...turn, parts: turn.parts.filter(part => part.sent) }), "~1k tokens sent");
  assert.deepEqual(contextRows(null), []);
});

test("a worker's background file is listed apart, never as kept instructions", () => {
  const filed = {
    sent_chars: 4000,
    sent_tokens_estimate: 1000,
    parts: [
      { key: "identity", chars: 4000, tokens_estimate: 1000, sent: true },
      { key: "background_file", label: "Delegation background file", chars: 20000, tokens_estimate: 5000, sent: false, file: true },
    ],
  };
  const rows = contextRows(filed);
  assert.deepEqual(rows.map(row => [row.key, row.sent, row.file]), [["identity", true, false], ["background_file", false, true]]);
  assert.equal(contextSummary(filed), "~1k tokens sent");
});

test("standing parts keep the backend's order and scale bars to the biggest sender", () => {
  const rows = standingRows({
    parts: [
      { key: "brain", label: "Second Brain rules", turns: 3, sent_turns: 2, average_chars: 900, average_tokens_estimate: 225, sent_chars: 1800, sent_tokens_estimate: 450 },
      { key: "identity", label: "Identity and task instructions", turns: 3, sent_turns: 2, average_chars: 100, sent_chars: 200 },
    ],
  });
  assert.deepEqual(rows.map(row => [row.key, row.width, row.sentTokens, row.averageTokens]), [["brain", 100, 450, 225], ["identity", 11, 50, 25]]);
  assert.deepEqual(standingRows(null), []);
});

test("parts of Rem's own calls count calls and keep a key of their own", () => {
  const rows = standingRows({
    parts: [
      { id: "chat_reply:workspace.desktop", key: "workspace.desktop", call: "chat_reply", label: "Chat reply: Desktop thread list", turns: 4, sent_turns: 4, average_chars: 140000, sent_chars: 560000 },
      { key: "rules", label: "Standing rules", turns: 2, sent_turns: 2, average_chars: 300, sent_chars: 600 },
    ],
  });
  assert.deepEqual(rows.map(row => [row.key, row.unit]), [["chat_reply:workspace.desktop", "call"], ["rules", "turn"]]);
  const calls = remCallRows({
    rem_calls: [
      { call: "chat_reply", label: "Chat reply", calls: 4, reasked: 2, failed: 1, average_chars: 400000, average_tokens_estimate: 100000, max_chars: 420000, sent_chars: 1600000 },
      { call: "task_update", label: "Task update", calls: 2, average_tokens_estimate: 2500, max_tokens_estimate: 3000, sent_chars: 20000, sent_tokens_estimate: 5000 },
    ],
  });
  assert.deepEqual(calls.map(row => [row.key, row.calls, row.averageTokens, row.largestTokens, row.sentTokens, row.width]), [
    ["chat_reply", 4, 100000, 105000, 400000, 100],
    ["task_update", 2, 2500, 3000, 5000, 2],
  ]);
  // A kind from before re-asks were counted reads as none.
  assert.deepEqual(calls.map(row => [row.key, row.reasked, row.failed]), [["chat_reply", 2, 1], ["task_update", 0, 0]]);
  assert.deepEqual(remCallRows(null), []);
});

test("a kind of Rem's own call shows the input and cache tokens its providers reported", () => {
  const [reported, silent, partial] = remCallRows({
    rem_calls: [
      { call: "chat_reply", label: "Chat reply", calls: 2, sent_chars: 4000, reported_calls: 1, input_tokens: 1050, cache_read_tokens: 900, cache_write_tokens: 100 },
      { call: "task_update", label: "Task update", calls: 1, sent_chars: 400, reported_calls: 0, input_tokens: null, cache_read_tokens: null, cache_write_tokens: null },
      { call: "self_work", label: "Self-work", calls: 1, sent_chars: 40, reported_calls: 1, input_tokens: 12000, cache_read_tokens: null, cache_write_tokens: null },
    ],
  });
  assert.deepEqual(reported.tokens, { calls: 1, input: 1050, cacheRead: 900, cacheWrite: 100 });
  assert.equal(remCallTokensLine(reported.tokens), "Reported: 1.1k tokens input · 900 tokens read from cache · 100 tokens written to cache");
  // No call of the kind reported tokens, so the row says nothing rather than 0.
  assert.equal(silent.tokens, null);
  assert.equal(remCallTokensLine(silent.tokens), "");
  // A count no call reported is left out instead of shown as 0.
  assert.equal(remCallTokensLine(partial.tokens), "Reported: 12k tokens input");
});

test("a kind of Rem's own call adds a line for its current rules version alone", () => {
  const [changed, older] = remCallRows({
    rem_calls: [
      { call: "chat_reply", label: "Chat reply", calls: 9, reasked: 4, failed: 2, sent_chars: 4000, current_version: { version: "0123456789ab", calls: 3, re_asked: 1, failed: 0, first_seen: "2026-11-20T10:00:00+00:00" } },
      { call: "task_update", label: "Task update", calls: 1, sent_chars: 400, current_version: null },
    ],
  });
  assert.deepEqual(changed.current, { version: "0123456789ab", calls: 3, reasked: 1, failed: 0, since: "2026-11-20T10:00:00+00:00" });
  assert.match(currentRulesLine(changed.current), /^Current rules since .+: 1 re-asked · 0 failed of 3 calls$/);
  // A kind whose newest call had no fixed rules, or came before versions were kept, has no line.
  assert.equal(older.current, null);
  assert.equal(currentRulesLine(older.current), "");
  assert.equal(currentRulesLine({ version: "0123456789ab", calls: 1, reasked: 0, failed: 0, since: "" }), "Current rules: 0 re-asked · 0 failed of 1 call");
});
