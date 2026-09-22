import test from "node:test";
import assert from "node:assert/strict";
import { reconcileDesktopThread, mergeDeviceMessages } from "./deviceWorkspaceSync.js";

test("phone replay updates deltas in place, orders thoughts before the existing reply and preserves desktop edits", () => {
  const user = { id: "request", role: "user", body: "Edited on desktop" };
  const reply = { id: "reply-request", role: "assistant", body: "Answer" };
  const later = { id: "later", role: "user", body: "Next question" };
  const thought = { id: "thought", role: "assistant", kind: "thought", body: "Checking", deviceRequestId: "request", deviceSequence: 1, groupId: "group" };
  const imported = [ { ...user, body: "Original phone text", origin: "phone" }, thought,
    { ...reply, kind: "final", deviceRequestId: "request", deviceSequence: 2 } ];
  const first = mergeDeviceMessages([user, reply, later], imported);
  assert.deepEqual(first.map(m => m.id), ["request", "thought", "reply-request", "later"]);
  assert.equal(first[0].body, "Edited on desktop");
  assert.equal(first[0].origin, "phone");
  const updated = mergeDeviceMessages(first, [{ ...thought, body: "Checking the result" }]);
  assert.equal(updated[1].body, "Checking the result");
  assert.equal(updated.length, 4);
  assert.deepEqual(mergeDeviceMessages(updated, [{ ...thought, body: "Checking the result" }]), updated);
});

test("phone model selection preserves desktop thread identity, resources and history ownership", () => {
  const local = { id: "same-thread", provider: "codex", model: "a", projectRoot: "/project", resources: { skills: ["one"] } };
  const remote = { metadata: { ...local, model: "b" }, base_metadata: local, context_modified: true };
  assert.deepEqual(reconcileDesktopThread(local, remote), { ...local, model: "b" });
  assert.equal(reconcileDesktopThread({ ...local, model: "new-desktop-choice" }, remote).model, "new-desktop-choice");
  assert.deepEqual(reconcileDesktopThread(null, remote), remote.metadata);
});

test("a stale mirror cannot undo desktop configuration", () => {
  const local = { id: "same", model: "new", resources: { mcp: ["local"] } };
  assert.equal(reconcileDesktopThread(local, { metadata: { model: "old" }, context_modified: false }), local);
});

test("renderer imports model changes, phone threads and replies without duplicates", async () => {
  const { startDeviceWorkspaceSync } = await import("./deviceWorkspaceSync.js");
  const previousWindow = globalThis.window, previousFetch = globalThis.fetch;
  const base = { id: "desktop", title: "Project", provider: "codex", model: "a", projectRoot: "/project" };
  const created = { ...base, id: "phone-created", title: "From phone" };
  const data = new Map([["gofer-flow-chat-threads", JSON.stringify([{ id: "desktop" }])], ["gofer-flow-chat-thread-meta:desktop", JSON.stringify(base)]]);
  const histories = new Map([["desktop", [{ id: "old", role: "user", body: "Desktop question" }]]]);
  const repository = { all: async id => histories.get(id) || [], save: async (id, messages) => histories.set(id, messages) };
  const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  const host = new EventTarget();
  Object.assign(host, { localStorage: storage, goferDesktop: { workspace: { pathGrantForApi: () => "local-project-grant" } } });
  globalThis.window = host;
  const submitted = [];
  globalThis.fetch = async (_url, options = {}) => {
    if (!options.body) return { ok: true, json: async () => ({ workspace_peers: ["paired"] }) };
    const body = JSON.parse(options.body);
    submitted.push(body);
    if (body.action === "workspace_poll") return { ok: true, json: async () => ({ threads: [
      { thread_id: "wire", revision: 2, base_metadata: base, context_modified: true, metadata: { ...base, model: "b" }, messages: [...histories.get("desktop"), { id: "reply", role: "assistant", body: "Phone reply" }] },
      { thread_id: "wire-new", revision: 1, mobile_created: true, metadata: created, messages: [] },
    ] }) };
    return { ok: true, json: async () => ({ revision: body.revision, metadata: body.metadata, messages: body.messages }) };
  };
  let stop;
  try {
    await new Promise((resolve, reject) => {
      host.addEventListener("gofer:device-sync-status", event => event.detail ? reject(new Error(event.detail)) : resolve(), { once: true });
      stop = startDeviceWorkspaceSync({ storage, repository, interval: 60000 });
    });
    assert.equal(JSON.parse(data.get("gofer-flow-chat-thread-meta:desktop")).model, "b");
    assert.equal(JSON.parse(data.get("gofer-flow-chat-thread-meta:phone-created")).title, "From phone");
    assert.deepEqual(histories.get("desktop").map(m => m.id), ["old", "reply"]);
    assert.ok(submitted.some(body => body.action === "workspace_exchange" && body.metadata.id === "phone-created"));
    assert.ok(submitted.filter(body => body.action === "workspace_exchange").every(body => body.grantId === "local-project-grant"));
  } finally {
    stop?.(); globalThis.window = previousWindow; globalThis.fetch = previousFetch;
  }
});

test("phone scope and effort changes preserve resources and respect concurrent desktop edits", () => {
  const base = { id: "thread", provider: "codex", model: "m", effort: "high", projectRoot: "/notes", projectName: "Notes", scopeMode: "project", resources: { shell: true } };
  const remote = { context_modified: true, base_metadata: base, metadata: { ...base, effort: "low", scopeMode: "global", projectRoot: "", projectName: "Global" } };
  const result = reconcileDesktopThread(base, remote);
  assert.equal(result.effort, "low");
  assert.equal(result.scopeMode, "global");
  assert.equal(result.id, base.id);
  assert.deepEqual(result.resources, base.resources);
  const edited = { ...base, projectRoot: "/code" };
  assert.deepEqual(reconcileDesktopThread(edited, remote), edited);
});

test("attachment metadata repairs an already imported phone message without undoing desktop text edits", () => {
  const local = { id: "phone-request", role: "user", body: "Edited desktop text" };
  const attachment = { id: "file-1", name: "README.md", size: 42, mime: "text/markdown" };
  const remote = { ...local, body: "Original text", origin: "phone", attachments: [attachment] };
  const merged = mergeDeviceMessages([local], [remote]);
  assert.equal(merged[0].body, local.body);
  assert.deepEqual(merged[0].attachments, [attachment]);
  assert.deepEqual(mergeDeviceMessages(merged, [remote]), merged);
});

test("phone organization and automatic names preserve concurrent desktop edits", () => {
  const base = { id: "thread", title: "New thread", pinned: false, archived: false, updatedAt: "2026-09-21T10:00:00Z", projectRoot: "/notes" };
  const remote = { base_metadata: base, metadata: { ...base, title: "Fix the mobile thread list", pinned: true, updatedAt: "2026-09-21T11:00:00Z" }, organization_modified: true, title_modified: true };
  assert.deepEqual(reconcileDesktopThread(base, remote), remote.metadata);
  const local = { ...base, title: "Named on desktop", archived: true };
  assert.deepEqual(reconcileDesktopThread(local, remote), local);
});

test("renderer only acknowledges phone deletion after local archive and deletion succeed", async () => {
  const { startDeviceWorkspaceSync } = await import("./deviceWorkspaceSync.js");
  const previousWindow = globalThis.window, previousFetch = globalThis.fetch;
  const meta = { id: "delete-me", title: "A shared thread", provider: "codex", model: "a", projectRoot: "/project" };
  const data = new Map([["gofer-flow-chat-threads", JSON.stringify([{ id: meta.id }])], [`gofer-flow-chat-thread-meta:${meta.id}`, JSON.stringify(meta)]]);
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  const repository = { all: async () => [], save: async () => {} };
  const host = new EventTarget();
  Object.assign(host, { localStorage: storage, goferDesktop: { workspace: { pathGrantForApi: () => "grant" } } });
  globalThis.window = host;
  const actions = [];
  globalThis.fetch = async (_url, options = {}) => {
    if (!options.body) return { ok: true, json: async () => ({ workspace_peers: ["paired"] }) };
    const body = JSON.parse(options.body); actions.push(body.action);
    return { ok: true, json: async () => body.action === "workspace_poll" ? { threads: [{ thread_id: "wire", revision: 2, metadata: meta, messages: [], delete_requested: true }] } : {} };
  };
  let stop;
  try {
    const failed = new Promise(resolve => host.addEventListener("gofer:device-sync-status", e => resolve(e.detail), { once: true }));
    stop = startDeviceWorkspaceSync({ storage, repository, interval: 60000, deleteThread: async () => { throw new Error("Archive unavailable"); } });
    assert.equal(await failed, "Archive unavailable");
    assert.ok(!actions.includes("workspace_remove"));
    assert.ok(data.has(`gofer-flow-chat-thread-meta:${meta.id}`));
    stop();
    const deleted = new Promise(resolve => host.addEventListener("gofer:device-thread-deleted", e => resolve(e.detail), { once: true }));
    stop = startDeviceWorkspaceSync({ storage, repository, interval: 60000, deleteThread: async id => {
      assert.equal(id, meta.id); actions.push("archive-and-delete"); data.delete(`gofer-flow-chat-thread-meta:${id}`); data.set("gofer-flow-chat-threads", "[]");
    } });
    assert.deepEqual(await deleted, { id: meta.id });
    assert.ok(actions.indexOf("archive-and-delete") < actions.indexOf("workspace_remove"));
  } finally { stop?.(); globalThis.window = previousWindow; globalThis.fetch = previousFetch; }
});
