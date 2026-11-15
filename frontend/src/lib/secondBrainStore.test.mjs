import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";
import { flushLibraryWrites, getState, libraryRevision, refreshLibrary, resetSecondBrainStore, subscribe, updateBrain } from "./secondBrainStore.js";

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
    assert.equal(libraryRevision(), "4.a.1");
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
    assert.equal(libraryRevision(), "5.a.1");
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
