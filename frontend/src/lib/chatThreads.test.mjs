import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { buildChatItems, bumpChatThread, chatStorageKeyFor, chatThreadIndex, loadChatThread, persistChatThreads, threadReadPatch, withLiveReply } from "./chatThreads.js";

function freshWindow() {
  const values = new Map();
  globalThis.window = {
    localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) },
  };
}

test("Rem's thread list and message helpers work from their own module, without App", () => {
  freshWindow();
  const threads = [
    { id: "a", title: "First", updatedAt: "2026-11-01T00:00:00.000Z" },
    { id: "b", title: "Second", updatedAt: "2026-11-02T00:00:00.000Z" },
  ];
  persistChatThreads(threads);
  assert.deepEqual(chatThreadIndex().map(entry => entry.id), ["b", "a"], "newest first");
  assert.equal(loadChatThread("b").title, "Second");
  assert.deepEqual(bumpChatThread(threads, "b", "2026-11-03T00:00:00.000Z").map(thread => thread.id), ["b", "a"]);
  assert.deepEqual(threadReadPatch(true), { unread: true });
  assert.equal(chatStorageKeyFor("a"), "gofer-flow-chat-thread:a");
  const items = buildChatItems([{ id: "q", role: "user", body: "Hi" }, { id: "r", role: "assistant", kind: "final", body: "Hello" }]);
  assert.deepEqual(items.map(item => [item.type, item.message.id]), [["message", "q"], ["message", "r"]]);
  assert.equal(withLiveReply(items), items);
});

test("App.jsx leaves Rem's chat pane and thread helpers to their own files", () => {
  const app = fs.readFileSync(new URL("../pages/App.jsx", import.meta.url), "utf8");
  for (const name of ["ChatPane", "chatThreadIndex", "persistChatThreads", "buildChatItems"]) {
    assert.doesNotMatch(app, new RegExp(`^(export )?(async )?(function|const) ${name}\\b`, "m"), `${name} is still defined in App.jsx`);
  }
  assert.ok(app.split("\n").length <= 8900, "App.jsx is over 8,900 lines");
  const pane = fs.readFileSync(new URL("../components/ChatPane.jsx", import.meta.url), "utf8");
  assert.match(pane, /^export function ChatPane\(/m);
  assert.doesNotMatch(pane, /from "\.\.\/pages\/App\.jsx"/, "ChatPane must not import App");
});
