// Dismiss, Dismiss all and the objectives' check, uncheck and remove change the side pane at
// once and send their request in the background. A refusal puts things back and says why in
// the side pane, and quick clicks don't wait for, or trip over, the one before.
import test from "node:test";
import assert from "node:assert/strict";
import { dismissibleUpdates } from "./remHome.js";

const NOW = "2026-11-26T12:00:00.000Z";
const failedTask = (id, extra = {}) => ({
  id, title: `Task ${id}`, type: "maintain", revision: 1, state: "enabled", schedule: { kind: "daily" },
  active_run: null, last_run: { id: `${id}-run`, state: "failed", needs_user: false, finished_at: NOW, ...extra },
});
const objective = (id, created) => ({ id, text: `Objective ${id}`, done_when: "It works", done: false, created_by: "user", created_at: created });
const objectivesView = (revision, items) => {
  const done = items.filter(item => item.done).length;
  return { revision, items, offset: 0, open: items.length - done, done, stale: 0, total: items.length };
};

// A backend the test answers by hand: polls answer from `server`, and each command waits
// until the test settles it, so the test sees the side pane while the request is out.
function fakeBackend() {
  const server = { tasks: [], objectives: objectivesView(1, []), version: 0 };
  const commands = [];
  const reply = (status, payload) => new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
  const fetch = async (url, init = {}) => {
    const path = String(url).split("?")[0];
    const method = init.method || "GET";
    if (method === "GET") {
      if (path === "/api/rem/conversation") return reply(200, { conversation: { id: "rem", turn_state: "idle" }, messages: [] });
      if (path === "/api/rem/tasks") return reply(200, { tasks: structuredClone(server.tasks), objectives: structuredClone(server.objectives), version: `v${server.version}` });
      if (path === "/api/rem/objectives") return reply(200, structuredClone(server.objectives));
      return reply(200, {});
    }
    let settle;
    const answered = new Promise(resolve => { settle = resolve; });
    commands.push({ path, body: JSON.parse(init.body || "{}"), answer: (status, payload) => settle(reply(status, payload)) });
    return answered;
  };
  return { server, commands, fetch };
}

async function withStore(run) {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const host = new EventTarget();
  const data = new Map();
  host.localStorage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key) };
  globalThis.window = host;
  const backend = fakeBackend();
  globalThis.fetch = backend.fetch;
  const store = await import("./remHomeStore.js");
  store.resetRemHomeStore();
  try {
    await run(store, backend);
  } finally {
    store.resetRemHomeStore();
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
}

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const until = async (check, what) => {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (check()) return;
    await flush();
  }
  assert.fail(`Timed out waiting for ${what}`);
};
const acknowledged = store => Object.fromEntries(store.getState().tasks.map(task => [task.id, task.last_run?.acknowledged === true]));

test("Dismiss all hides the updates at once and a poll from before the request can't bring them back", async () => {
  await withStore(async (store, backend) => {
    backend.server.tasks = [failedTask("a"), failedTask("b")];
    await store.refreshRemHome();
    assert.equal(store.getState().status, "ready");
    const updates = dismissibleUpdates(store.getState().tasks, new Date(NOW));
    assert.equal(updates.length, 2);

    const done = store.acknowledgeAllRemRuns(updates);
    // Before the backend has answered, or even been asked, both updates are gone.
    assert.deepEqual(acknowledged(store), { a: true, b: true });
    assert.equal(dismissibleUpdates(store.getState().tasks, new Date(NOW)).length, 0);
    await until(() => backend.commands.length === 1, "the Dismiss all request");
    assert.equal(backend.commands[0].path, "/api/rem/runs/acknowledge");
    assert.deepEqual(backend.commands[0].body.run_ids, ["a-run", "b-run"]);

    // A poll that still has the old list (another task changed meanwhile) keeps them hidden.
    backend.server.tasks = [failedTask("a"), failedTask("b"), failedTask("c")];
    backend.server.version += 1;
    await store.refreshRemHome();
    assert.deepEqual(acknowledged(store), { a: true, b: true, c: false });

    // The backend dismisses them; the list it sends next agrees.
    backend.server.tasks = [failedTask("a", { acknowledged: true }), failedTask("b", { acknowledged: true }), failedTask("c")];
    backend.server.version += 1;
    backend.commands[0].answer(200, { dismissed: [{ run_id: "a-run" }, { run_id: "b-run" }], count: 2, needs_you: 0 });
    assert.equal(await done, true);
    await store.refreshRemHome();
    assert.deepEqual(acknowledged(store), { a: true, b: true, c: false });
    assert.equal(store.getState().sideNotice, "");
  });
});

test("a refused Dismiss all brings the updates back and says why in the side pane", async () => {
  await withStore(async (store, backend) => {
    backend.server.tasks = [failedTask("a"), failedTask("b")];
    await store.refreshRemHome();
    const done = store.acknowledgeAllRemRuns(dismissibleUpdates(store.getState().tasks, new Date(NOW)));
    assert.deepEqual(acknowledged(store), { a: true, b: true });
    // A single Dismiss on another update meanwhile doesn't wait for the first request.
    await until(() => backend.commands.length === 1, "the Dismiss all request");
    backend.commands[0].answer(500, { error: "The database is busy." });
    assert.equal(await done, null);
    assert.deepEqual(acknowledged(store), { a: false, b: false });
    assert.match(store.getState().sideNotice, /Couldn't dismiss the updates\. The database is busy\./);
    store.dismissSideNotice();
    assert.equal(store.getState().sideNotice, "");
  });
});

test("dismissing one update after another doesn't wait for the one before", async () => {
  await withStore(async (store, backend) => {
    backend.server.tasks = [failedTask("a"), failedTask("b")];
    await store.refreshRemHome();
    const [first, second] = store.getState().tasks;
    const one = store.acknowledgeRemRun(first);
    const two = store.acknowledgeRemRun(second);
    assert.deepEqual(acknowledged(store), { a: true, b: true });
    await until(() => backend.commands.length === 2, "both requests");
    assert.deepEqual(backend.commands.map(command => command.path), ["/api/rem/runs/a-run/acknowledge", "/api/rem/runs/b-run/acknowledge"]);
    // The second one fails; only its update comes back.
    backend.commands[1].answer(500, { error: "Run not found." });
    backend.commands[0].answer(200, { run: { id: "a-run", acknowledged: true } });
    assert.equal(await one, true);
    assert.equal(await two, null);
    assert.deepEqual(acknowledged(store), { a: true, b: false });
    assert.match(store.getState().sideNotice, /Couldn't dismiss that\. Run not found\./);
  });
});

test("checking objectives one after another shows each at once and sends them in turn with the newest revision", async () => {
  await withStore(async (store, backend) => {
    const items = [objective("o1", "2026-11-26T10:03:00Z"), objective("o2", "2026-11-26T10:02:00Z"), objective("o3", "2026-11-26T10:01:00Z")];
    backend.server.objectives = objectivesView(5, items);
    await store.refreshRemHome();
    const checks = ["o1", "o2", "o3"].map(id => store.changeRemObjectives({ action: "check", id }));
    const shown = store.getState().objectives;
    assert.deepEqual(shown.items.map(item => [item.id, item.done]), [["o1", true], ["o2", true], ["o3", true]]);
    assert.deepEqual([shown.open, shown.done, shown.total], [0, 3, 3]);

    await until(() => backend.commands.length === 1, "the first check");
    // A poll while the checks are out keeps them shown.
    backend.server.version += 1;
    await store.refreshRemHome();
    assert.equal(store.getState().objectives.done, 3);
    assert.equal(backend.commands.length, 1, "the next check waits for this answer, so it can carry the new revision");

    let revision = 5;
    const server = structuredClone(items);
    for (const [index, id] of ["o1", "o2", "o3"].entries()) {
      await until(() => backend.commands.length === index + 1, `the check of ${id}`);
      const command = backend.commands[index];
      assert.deepEqual([command.path, command.body.action, command.body.id, command.body.expected_revision], ["/api/rem/objectives", "check", id, revision]);
      server.find(item => item.id === id).done = true;
      revision += 1;
      command.answer(200, objectivesView(revision, structuredClone(server)));
      assert.ok(await checks[index]);
      assert.equal(store.getState().objectives.done, 3, "the side pane never flickers back");
    }
    assert.equal(store.getState().objectives.revision, 8);
  });
});

test("an objective checked and unchecked before the check was sent sends nothing", async () => {
  await withStore(async (store, backend) => {
    backend.server.objectives = objectivesView(2, [objective("o1", "2026-11-26T10:02:00Z"), objective("o2", "2026-11-26T10:01:00Z")]);
    await store.refreshRemHome();
    const first = store.changeRemObjectives({ action: "check", id: "o1" });
    void store.changeRemObjectives({ action: "check", id: "o2" });
    await store.changeRemObjectives({ action: "uncheck", id: "o2" });
    assert.deepEqual(store.getState().objectives.items.map(item => [item.id, item.done]), [["o2", false], ["o1", true]]);
    await until(() => backend.commands.length === 1, "the first check");
    backend.commands[0].answer(200, objectivesView(3, [objective("o2", "2026-11-26T10:01:00Z"), { ...objective("o1", "2026-11-26T10:02:00Z"), done: true }]));
    assert.ok(await first);
    await flush();
    assert.equal(backend.commands.length, 1, "o2's check and uncheck cancelled out");
  });
});

test("removing an objective takes it away at once and a 409 puts back every change made on that list", async () => {
  await withStore(async (store, backend) => {
    const items = [objective("o1", "2026-11-26T10:02:00Z"), objective("o2", "2026-11-26T10:01:00Z")];
    backend.server.objectives = objectivesView(4, items);
    await store.refreshRemHome();
    const removed = store.changeRemObjectives({ action: "remove", id: "o1" });
    const checked = store.changeRemObjectives({ action: "check", id: "o2" });
    const shown = store.getState().objectives;
    assert.deepEqual(shown.items.map(item => [item.id, item.done]), [["o2", true]]);
    assert.deepEqual([shown.open, shown.done, shown.total], [0, 1, 1]);

    await until(() => backend.commands.length === 1, "the remove");
    // Rem added one meanwhile, so the list the user changed is out of date.
    backend.server.objectives = objectivesView(5, [objective("o9", "2026-11-26T11:00:00Z"), ...items]);
    backend.commands[0].answer(409, { error: "The objectives changed meanwhile.", code: "conflict" });
    assert.equal(await removed, null);
    assert.equal(await checked, null);
    await until(() => store.getState().objectives.revision === 5, "the reloaded list");
    assert.deepEqual(store.getState().objectives.items.map(item => [item.id, item.done]), [["o9", false], ["o1", false], ["o2", false]]);
    assert.equal(backend.commands.length, 1, "the check made on the old list isn't sent");
    assert.match(store.getState().sideNotice, /objectives changed meanwhile/);
  });
});

test("a refused objective change other than a conflict undoes only that change", async () => {
  await withStore(async (store, backend) => {
    const items = [objective("o1", "2026-11-26T10:02:00Z"), objective("o2", "2026-11-26T10:01:00Z")];
    backend.server.objectives = objectivesView(4, items);
    await store.refreshRemHome();
    const first = store.changeRemObjectives({ action: "check", id: "o1" });
    const second = store.changeRemObjectives({ action: "check", id: "o2" });
    await until(() => backend.commands.length === 1, "the first check");
    backend.commands[0].answer(404, { error: "That objective is gone.", code: "not_found" });
    assert.equal(await first, null);
    await until(() => backend.commands.length === 2, "the second check");
    assert.equal(backend.commands[1].body.expected_revision, 4);
    assert.deepEqual(store.getState().objectives.items.map(item => [item.id, item.done]), [["o1", false], ["o2", true]]);
    backend.commands[1].answer(200, objectivesView(5, [items[0], { ...items[1], done: true }]));
    assert.ok(await second);
    assert.match(store.getState().sideNotice, /Couldn't change the objectives\. That objective is gone\./);
  });
});
