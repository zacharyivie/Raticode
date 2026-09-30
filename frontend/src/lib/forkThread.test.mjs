import test from "node:test";
import assert from "node:assert/strict";
import { copyForkAttachments, forkThreadHistory, isForkableMessage } from "./forkThread.js";

test("forks include exactly the selected prefix and independently inherit settings", () => {
  const parent = { id: "parent", title: "Debug", provider: "cursor", model: "chosen", effort: "high",
    projectRoot: "/repo", projectBranch: "feature", scopeMode: "project", archived: true, pinned: true,
    resources: { shell: false }, permissionsByProvider: { cursor: "restricted" }, sessionId: "do-not-resume" };
  const history = [{ id: "a", role: "user", body: "original", attachments: [{ id: "file" }] },
    { id: "summary", role: "assistant", kind: "turn-summary", changes: { undoable: true, changing: true, files: [] } },
    { id: "b", role: "assistant", kind: "final", body: "Answer" },
    { id: "c", role: "assistant", body: "future" }];
  const fork = forkThreadHistory(parent, history, "b", "child");
  assert.deepEqual(fork.messages.map(message => message.id), ["a", "summary", "b"]);
  assert.equal(fork.thread.provider, "cursor");
  assert.equal(fork.thread.projectBranch, "feature");
  assert.equal(fork.thread.sessionId, undefined);
  assert.equal(fork.thread.archived, undefined);
  assert.equal(fork.thread.pinned, undefined);
  assert.equal(fork.thread.forkedFromMessageId, "b");
  assert.equal(fork.messages[1].changes.undoable, false);
  fork.messages[0].body = "edited";
  fork.thread.resources.shell = true;
  assert.equal(history[0].body, "original");
  assert.equal(parent.resources.shell, false);
  assert.equal(history[1].changes.undoable, true);
  assert.throws(() => forkThreadHistory(parent, history, "missing", "child"), /no longer available/);
});

test("only user messages and final replies can be fork boundaries", () => {
  for (const message of [
    { role: "user", body: "Question" },
    { role: "user", body: "", attachments: [{ storageName: "image.png" }] },
    { role: "assistant", body: "Legacy final" },
    { role: "assistant", kind: "final", body: "Final" },
  ]) assert.equal(isForkableMessage(message), true);
  for (const message of [
    { role: "assistant", kind: "thought" },
    { role: "assistant", kind: "thought", trace: { kind: "tool" } },
    { role: "assistant", kind: "turn-summary" },
    { role: "assistant", kind: "error" },
    { role: "assistant", kind: "memory" },
    { role: "assistant", kind: "search-source" },
    { role: "system", body: "Status" },
    { role: "assistant", kind: "final", running: true },
  ]) {
    assert.equal(isForkableMessage(message), false);
    assert.throws(() => forkThreadHistory({ id: "parent" }, [{ ...message, id: "blocked" }], "blocked", "child"), /user message or a final reply/);
  }
});

test("forks copy only included attachments, deduplicate references, and surface failures", async () => {
  globalThis.window = { goferApiBaseUrl: undefined };
  const messages = Array.from({ length: 7 }, (_, index) => ({ attachments: [{ storageName: `file-${index}` }] }));
  messages.push(messages[0]);
  const requests = [];
  await copyForkAttachments("original", "fork", messages, async (_url, options) => {
    requests.push(JSON.parse(options.body)); return { ok: true };
  });
  assert.deepEqual(requests.map(request => request.attachments.length), [5, 2]);
  assert.ok(requests.every(request => request.sourceThreadId === "original" && request.threadId === "fork"));
  await assert.rejects(copyForkAttachments("original", "fork", messages, async () => ({ ok: false, json: async () => ({ error: "Attachment missing" }) })), /Attachment missing/);
  await copyForkAttachments("original", "fork", [], () => assert.fail("No attachments to copy"));
});
