import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";
import { deleteCollection, deleteSavedSearch, flushLibraryWrites, getState, historySize, loadHistory, passageCount, refreshLibrary, removeJob, resetSecondBrainStore, subscribe, updateBrain } from "./secondBrainStore.js";
import { noticeDuration } from "./notices.js";

// The library poll sends the revision it has and keeps its state while the answer is
// { unchanged }. Any error makes the next refresh ask for the whole library again.

const BRAIN = { id: "brain-1", name: "Notes", root: "/notes", state: "available", settings: {}, index: {} };
function library(revision, extra = {}) {
  return { brains: [BRAIN], activeBrainId: BRAIN.id, documents: [], collections: [], savedSearches: [], reports: [], citations: [], jobs: [], projects: [], topics: [], health: [], families: [], tasks: [], views: {}, revision, ...extra };
}

// Hooks stay inside this suite: the frontend tests share one process (--experimental-test-isolation=none).
describe("Second Brain library polling", () => {
  let requests;
  let answers;
  let previousWindow;
  let previousFetch;
  beforeEach(() => {
    previousWindow = globalThis.window;
    previousFetch = globalThis.fetch;
    requests = [];
    answers = [];
    globalThis.window = {};
    globalThis.fetch = async (url, init = {}) => {
      requests.push({ url, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null });
      const answer = answers.shift();
      if (answer instanceof Error) throw answer;
      return { ok: answer.status ? answer.status < 400 : true, json: async () => answer.body ?? { result: answer } };
    };
    resetSecondBrainStore();
  });
  afterEach(() => {
    resetSecondBrainStore();
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  });

  const lastUrl = () => new URL(requests.at(-1).url, "http://raticode.test");

  test("a poll at the current revision keeps the state and skips the render", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    assert.equal(lastUrl().search, "");
    const before = getState();
    let renders = 0;
    const unsubscribe = subscribe(() => renders++);
    answers.push({ unchanged: true, revision: "4.a.1" });
    await refreshLibrary();
    unsubscribe();
    assert.equal(lastUrl().searchParams.get("revision"), "4.a.1");
    assert.equal(getState(), before);
    assert.equal(renders, 0);
    assert.equal(getState().unchanged, undefined);
  });

  test("a changed library replaces the state and its revision", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    answers.push(library("5.a.1", { brains: [{ ...BRAIN, name: "Renamed" }] }));
    await refreshLibrary();
    assert.equal(getState().brains[0].name, "Renamed");
    // The next poll asks from the new revision.
    answers.push({ unchanged: true, revision: "5.a.1" });
    await refreshLibrary();
    assert.equal(lastUrl().searchParams.get("revision"), "5.a.1");
  });

  test("after a failed refresh the next one asks for the whole library", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    answers.push(new Error("Backend restarting"));
    await refreshLibrary();
    assert.equal(getState().error, "Backend restarting");
    answers.push(library("4.a.1", { brains: [{ ...BRAIN, name: "Fresh" }] }));
    await refreshLibrary();
    assert.equal(lastUrl().searchParams.get("revision"), null);
    assert.equal(getState().brains[0].name, "Fresh");
    assert.equal(getState().error, null);
  });

  test("a server error answer also forces a full refresh", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    answers.push({ status: 500, body: { error: "database is locked" } });
    await refreshLibrary();
    assert.equal(getState().error, "database is locked");
    answers.push(library("4.a.1"));
    await refreshLibrary();
    assert.equal(lastUrl().searchParams.get("revision"), null);
    assert.equal(getState().error, null);
  });

  test("a write that fails drops the optimistic edit with a full refresh", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    answers.push({ status: 400, body: { error: "Brain folder is unavailable" } });
    // The refresh after the failed write: the server still has the old name.
    answers.push(library("4.a.1"));
    updateBrain(BRAIN.id, { name: "Optimistic" });
    assert.equal(getState().brains[0].name, "Optimistic");
    await flushLibraryWrites();
    await new Promise(resolve => setTimeout(resolve, 0));
    const [write, refresh] = requests.slice(-2);
    assert.equal(write.body.action, "update_brain");
    assert.equal(new URL(refresh.url, "http://raticode.test").searchParams.get("revision"), null);
    assert.equal(getState().brains[0].name, "Notes");
    assert.equal(getState().toast.message, "Brain folder is unavailable");
  });

  // Documents come without passages; the store fetches them with "passages" by version.
  const doc = (id, version) => ({ id, brainId: BRAIN.id, title: id, version, passageCount: 1, versionCount: 0, historySize: 0 });
  const passagesOf = (...docs) => ({ documents: docs.map(([id, version]) => ({ id, brainId: BRAIN.id, version, blocks: [{ id: `${id}-1`, heading: id, text: `${id} at ${version}` }] })), missing: [] });
  const passageRequests = () => requests.filter(request => request.body?.action === "passages").map(request => request.body.params);
  const textOf = id => getState().documents.find(item => item.id === id).blocks.map(block => block.text).join(" ");
  async function loaded(...docs) {
    answers.push(library("4.a.1", { documents: docs.map(([id, version]) => doc(id, version)) }));
    answers.push(passagesOf(...docs));
    await refreshLibrary({ initial: true });
  }

  test("the first load fills the passage cache in one batch", async () => {
    answers.push(library("4.a.1", { documents: [doc("a", "v1"), doc("b", "v1")] }));
    let shown;
    const unsubscribe = subscribe(() => { shown ??= structuredClone(getState()); });
    answers.push(passagesOf(["a", "v1"], ["b", "v1"]));
    await refreshLibrary({ initial: true });
    unsubscribe();
    // The documents show at once, marked as loading until their passages arrive.
    assert.equal(shown.passagesLoading, true);
    assert.deepEqual(shown.documents.map(item => [item.blocks.length, item.passagesLoading]), [[0, true], [0, true]]);
    assert.deepEqual(passageRequests(), [{ ids: ["a", "b"] }]);
    assert.equal(getState().passagesLoading, false);
    assert.equal(textOf("b"), "b at v1");
    assert.equal(getState().documents[0].passagesLoading, undefined);
  });

  test("one changed document fetches only its own passages", async () => {
    await loaded(["a", "v1"], ["b", "v1"], ["c", "v1"]);
    answers.push(library("5.a.1", { documents: [doc("a", "v1"), doc("b", "v2"), doc("c", "v1")] }));
    answers.push(passagesOf(["b", "v2"]));
    await refreshLibrary();
    assert.deepEqual(passageRequests().at(-1), { ids: ["b"] });
    assert.equal(passageRequests().length, 2);
    assert.equal(textOf("a"), "a at v1");
    assert.equal(textOf("b"), "b at v2");
    assert.equal(getState().passagesLoading, false);
  });

  test("deleted documents leave the passage cache", async () => {
    await loaded(["a", "v1"], ["b", "v1"]);
    answers.push(library("5.a.1", { documents: [doc("a", "v1")] }));
    await refreshLibrary();
    assert.equal(passageRequests().length, 1, "a removal needs no passages");
    assert.deepEqual(getState().documents.map(item => item.id), ["a"]);
    // Brought back at the same version, b is fetched again: its old passages were dropped.
    answers.push(library("6.a.1", { documents: [doc("a", "v1"), doc("b", "v1")] }));
    answers.push(passagesOf(["b", "v1"]));
    await refreshLibrary();
    assert.deepEqual(passageRequests().at(-1), { ids: ["b"] });
  });

  test("a failed passages fetch falls back to a full refresh that carries them", async () => {
    answers.push(library("4.a.1", { documents: [doc("a", "v1")] }));
    answers.push({ status: 500, body: { error: "database is locked" } });
    const full = { ...doc("a", "v1"), blocks: [{ id: "a-1", heading: "a", text: "from the full refresh" }], versions: [{ hash: "v0", at: "2026-11-01T00:00:00Z", note: "Before", blocks: { "a-1": "older" } }] };
    answers.push(library("4.a.1", { documents: [full] }));
    await refreshLibrary({ initial: true });
    assert.equal(lastUrl().searchParams.get("passages"), "1");
    assert.equal(lastUrl().searchParams.get("revision"), null);
    assert.equal(textOf("a"), "from the full refresh");
    assert.equal(getState().passagesLoading, false);
    // The full refresh's stored versions serve the version dialogs without another request.
    const before = requests.length;
    assert.deepEqual((await loadHistory(getState().documents[0])).map(version => version.hash), ["v0"]);
    assert.equal(requests.length, before);
  });

  test("stored versions load once per document version", async () => {
    answers.push(library("4.a.1", { documents: [{ ...doc("a", "v2"), versionCount: 1, historySize: 80 }] }));
    answers.push(passagesOf(["a", "v2"]));
    await refreshLibrary({ initial: true });
    answers.push({ documents: [{ id: "a", brainId: BRAIN.id, version: "v2", blocks: [], versions: [{ hash: "v1", at: "2026-11-01T00:00:00Z", note: "Edited", blocks: { "a-1": "a at v1" } }] }], missing: [] });
    const [first, again] = [await loadHistory(getState().documents[0]), await loadHistory(getState().documents[0])];
    assert.equal(first, again);
    assert.deepEqual(passageRequests().at(-1), { ids: ["a"], history: true });
    assert.equal(passageRequests().length, 2);
    assert.equal(historySize(getState().documents[0]), 80);
    assert.equal(passageCount(getState().documents[0]), 1);
  });

  test("Undo on the delete notice puts a collection, saved search or research job back as it was", async () => {
    const collections = [
      { id: "col-1", brainId: BRAIN.id, name: "Reading list", brief: "What to read", updated: "2026-11-20T00:00:00Z", sections: [{ id: "sec-1", title: "Evidence", items: [{ id: "it-1", kind: "document", docId: "a", blockId: "", version: "v1", note: "Start here" }] }], questions: [{ id: "q-1", text: "Why?", done: false }] },
      { id: "col-2", brainId: BRAIN.id, name: "Later", brief: "", updated: "2026-11-21T00:00:00Z", sections: [], questions: [] },
    ];
    const savedSearches = [{ id: "view-1", brainId: BRAIN.id, name: "Open questions", text: "why", filters: { tags: ["todo"] }, mode: "exact", searchIn: "all", match: "all", sort: "relevance" }, { id: "view-2", brainId: BRAIN.id, name: "Second", text: "", filters: {} }];
    const jobs = [{ id: "job-0", brainId: BRAIN.id, name: "First", enabled: false, trigger: { label: "Manual" }, destination: "a/", limits: { documents: 1, turns: 1 } }, { id: "job-1", brainId: BRAIN.id, name: "Weekly digest", enabled: true, trigger: { label: "Weekly" }, destination: "digests/", limits: { documents: 20, turns: 4 }, provider: { id: "codex", model: "cli-default" } }];
    answers.push(library("4.a.1", { collections, savedSearches, jobs }));
    await refreshLibrary({ initial: true });
    const cases = [
      ["collections", "col-1", deleteCollection, "Deleted collection “Reading list”."],
      ["savedSearches", "view-1", deleteSavedSearch, "Deleted saved search “Open questions”."],
      ["jobs", "job-1", removeJob, "Deleted research job “Weekly digest”."],
    ];
    for (const [kind, id, remove, message] of cases) {
      const before = structuredClone(getState()[kind]);
      const index = before.findIndex(item => item.id === id);
      // The delete, its refresh (the library no longer has the item), the restore, its refresh.
      answers.push({ id, deleted: true }, library("5.a.1", { collections, savedSearches, jobs, [kind]: before.filter(item => item.id !== id) }), { id }, library("6.a.1", { collections, savedSearches, jobs, [kind]: before }));
      remove(id);
      assert.equal(getState()[kind].some(item => item.id === id), false);
      const { toast } = getState();
      assert.equal(toast.message, message);
      assert.equal(toast.action.label, "Undo");
      await flushLibraryWrites();
      await new Promise(resolve => setTimeout(resolve, 0));
      assert.deepEqual(requests.filter(request => request.body?.action === "mutate").at(-1).body.params, { kind, id, delete: true });
      toast.action.run();
      assert.deepEqual(getState()[kind], before, "the same object is back at its old place");
      assert.equal(getState().toast, null, "Undo closes the notice");
      await flushLibraryWrites();
      assert.deepEqual(requests.filter(request => request.body?.action === "mutate").at(-1).body.params, { kind, id, item: before[index] });
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    // A notice with a button stays long enough to use it; plain confirmations clear sooner,
    // and an error stays until it is dismissed.
    assert.ok(noticeDuration({ tone: "success", action: { label: "Undo", run() {} } }) >= 10000);
    assert.equal(noticeDuration({ tone: "success" }), 4000);
    assert.equal(noticeDuration({ tone: "danger" }), 0);
  });

  test("opening Library health asks for a recheck, kept until a refresh gets through", async () => {
    answers.push(library("4.a.1"));
    await refreshLibrary({ initial: true });
    answers.push(new Error("offline"));
    await refreshLibrary({ recheck: true });
    assert.equal(lastUrl().searchParams.get("recheck"), "1");
    answers.push(library("4.a.2"));
    await refreshLibrary();
    assert.equal(lastUrl().searchParams.get("recheck"), "1");
    answers.push({ unchanged: true, revision: "4.a.2" });
    await refreshLibrary();
    assert.equal(lastUrl().searchParams.get("recheck"), null);
    assert.equal(lastUrl().searchParams.get("revision"), "4.a.2");
  });
});
