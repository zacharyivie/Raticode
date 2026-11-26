import test from "node:test";
import assert from "node:assert/strict";
import { deleteChatDraft, flushChatDrafts, getChatDraft, REM_CHAT_DRAFT_KEY, setChatDraft, threadDraftKey } from "./chatDrafts.js";

const KEY = "raticode.chatDrafts.v1";

// Each test gets its own window and storage, as a new app window would.
function freshWindow(initial = {}) {
  const values = new Map(Object.entries(initial));
  const listeners = {};
  globalThis.window = {
    localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) },
    addEventListener: (type, listener) => { (listeners[type] ??= []).push(listener); },
    close: type => { for (const listener of listeners[type] ?? []) listener(); },
  };
  return { values, saved: () => JSON.parse(values.get(KEY) ?? "null") };
}

test("saved chat drafts are checked on load and malformed entries are dropped", () => {
  freshWindow({ [KEY]: JSON.stringify([
    ["thread:a", "keep me"], ["thread:b", 42], ["thread:c", ""], [7, "bad key"], "not a pair",
    ["thread:d", "x".repeat(20001)], ["thread:e", "one", "extra"], [REM_CHAT_DRAFT_KEY, "Rem text"],
  ]) });
  assert.equal(getChatDraft(threadDraftKey("a")), "keep me");
  assert.equal(getChatDraft(REM_CHAT_DRAFT_KEY), "Rem text");
  for (const id of ["b", "c", "d", "e"]) assert.equal(getChatDraft(threadDraftKey(id)), "");
  freshWindow({ [KEY]: "{not json" });
  assert.equal(getChatDraft(REM_CHAT_DRAFT_KEY), "");
  freshWindow({ [KEY]: JSON.stringify({ drafts: [] }) });
  assert.equal(getChatDraft(REM_CHAT_DRAFT_KEY), "");
});

test("chat drafts are capped at 20,000 characters and the 50 newest, and an empty composer clears its draft", () => {
  const { saved, values } = freshWindow();
  setChatDraft(threadDraftKey(null), "y".repeat(25000));
  assert.equal(getChatDraft("new-thread").length, 20000);
  for (let index = 0; index < 55; index += 1) setChatDraft(threadDraftKey(`t${index}`), `draft ${index}`);
  assert.equal(values.has(KEY), false, "nothing is written until typing pauses or the window closes");
  window.close("pagehide");
  const entries = saved();
  assert.equal(entries.length, 50);
  assert.deepEqual(entries[0], ["thread:t5", "draft 5"]);
  assert.deepEqual(entries.at(-1), ["thread:t54", "draft 54"]);
  setChatDraft(threadDraftKey("t54"), "");
  deleteChatDraft(threadDraftKey("t53"));
  assert.equal(flushChatDrafts(), true);
  assert.deepEqual(saved().at(-1), ["thread:t52", "draft 52"]);
});

test("a typing pause saves chat drafts without a window close", async () => {
  const { saved } = freshWindow();
  setChatDraft(REM_CHAT_DRAFT_KEY, "typed");
  await new Promise(resolve => setTimeout(resolve, 650));
  assert.deepEqual(saved(), [[REM_CHAT_DRAFT_KEY, "typed"]]);
});

test("a failed save is retried instead of claiming the drafts were kept", () => {
  freshWindow();
  let full = true;
  const values = new Map();
  globalThis.window.localStorage = { getItem: () => null, setItem: (key, value) => { if (full) throw Error("quota"); values.set(key, value); }, removeItem: () => {} };
  setChatDraft(REM_CHAT_DRAFT_KEY, "keep trying");
  assert.equal(flushChatDrafts(), false);
  full = false;
  assert.equal(flushChatDrafts(), true);
  assert.deepEqual(JSON.parse(values.get(KEY)), [[REM_CHAT_DRAFT_KEY, "keep trying"]]);
});
