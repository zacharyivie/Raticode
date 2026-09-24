import test from "node:test";
import assert from "node:assert/strict";
import { copyForkAttachments, forkThreadHistory } from "./forkThread.js";

test("forks include exactly the selected prefix and independently inherit settings", () => {
  const parent = { id: "parent", title: "Debug", provider: "cursor", model: "chosen", effort: "high",
    projectRoot: "/repo", projectBranch: "feature", scopeMode: "project", archived: true, pinned: true,
    resources: { shell: false }, permissionsByProvider: { cursor: "restricted" }, sessionId: "do-not-resume" };
  const history = [{ id: "a", role: "user", body: "original", attachments: [{ id: "file" }] },
    { id: "b", role: "assistant", kind: "turn-summary", changes: { undoable: true, changing: true, files: [] } },
    { id: "c", role: "assistant", body: "future" }];
  const fork = forkThreadHistory(parent, history, "b", "child");
  assert.deepEqual(fork.messages.map(message => message.id), ["a", "b"]);
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
