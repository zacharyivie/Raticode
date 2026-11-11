import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_QUERY, SEARCH_DELAY_MS, createRemSearch, partsText, resultLabel, searchSummary } from "./remSearch.js";

// Timers that run only when the test says so.
function fakeTimers() {
  let next = 0;
  const pending = new Map();
  return {
    setTimeout(fn, delay) { const id = ++next; pending.set(id, { fn, delay }); return id; },
    clearTimeout(id) { pending.delete(id); },
    flush() { const due = [...pending.values()]; pending.clear(); for (const { fn } of due) fn(); },
    get size() { return pending.size; },
    delays() { return [...pending.values()].map(entry => entry.delay); },
  };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const result = (id, extra = {}) => ({ id, seq: 1, role: "rem", created_at: "", where: "message", snippet: [{ text: id, match: true }], ...extra });

test("typing searches once, after a pause, with the newest words", async () => {
  const timers = fakeTimers();
  const asked = [];
  const search = createRemSearch({ timers, search: async query => { asked.push(query); return { total: 1, results: [result("m1")], next: null }; } });
  for (const query of ["r", "re", "rel", "release "]) search.setQuery(query);
  assert.equal(timers.size, 1, "each key press replaces the waiting search");
  assert.deepEqual(timers.delays(), [SEARCH_DELAY_MS]);
  assert.equal(search.state.status, "loading");
  assert.deepEqual(asked, []);
  timers.flush();
  await settle();
  assert.deepEqual(asked, ["release"]);
  assert.equal(search.state.status, "ready");
  assert.equal(search.state.total, 1);
});

test("an answer to an older query never replaces a newer one", async () => {
  const timers = fakeTimers();
  const answers = {};
  const search = createRemSearch({ timers, search: query => new Promise(resolve => { answers[query] = resolve; }) });
  search.setQuery("old");
  timers.flush();
  search.setQuery("new");
  timers.flush();
  answers.new({ total: 1, results: [result("fresh")], next: null });
  await settle();
  answers.old({ total: 9, results: [result("stale")], next: null });
  await settle();
  assert.deepEqual(search.state.results.map(r => r.id), ["fresh"]);
  assert.equal(search.state.total, 1);
});

test("clearing the box stops a waiting search and shows nothing", async () => {
  const timers = fakeTimers();
  let calls = 0;
  const search = createRemSearch({ timers, search: async () => { calls += 1; return { total: 0, results: [] }; } });
  search.setQuery("abc");
  search.setQuery("   ");
  assert.equal(timers.size, 0);
  assert.equal(search.state.status, "idle");
  assert.equal(calls, 0);
  assert.equal(searchSummary(search.state), "");
});

test("a failed search can be tried again, and a failed page keeps the results shown", async () => {
  const timers = fakeTimers();
  let fail = true;
  const search = createRemSearch({ timers, search: async (query, before) => {
    if (fail) throw new Error("Raticode's backend isn't reachable.");
    return before == null ? { total: 3, results: [result("a"), result("b")], next: 5 } : { total: 3, results: [result("c")], next: null };
  } });
  search.setQuery("x");
  timers.flush();
  await settle();
  assert.equal(search.state.status, "error");
  assert.equal(search.state.error, "Raticode's backend isn't reachable.");
  fail = false;
  search.retry();
  await settle();
  assert.deepEqual(search.state.results.map(r => r.id), ["a", "b"]);
  assert.equal(searchSummary(search.state), "3 matches, showing 2");
  fail = true;
  search.more();
  await settle();
  assert.equal(search.state.status, "ready");
  assert.deepEqual(search.state.results.map(r => r.id), ["a", "b"]);
  assert.match(search.state.error, /reachable/);
  fail = false;
  search.more();
  await settle();
  assert.deepEqual(search.state.results.map(r => r.id), ["a", "b", "c"]);
  assert.equal(search.state.next, null);
  assert.equal(searchSummary(search.state), "3 matches");
});

test("queries are cut to the longest the desktop accepts", () => {
  const timers = fakeTimers();
  const search = createRemSearch({ timers, search: async () => ({ total: 0, results: [] }) });
  search.setQuery("x".repeat(500));
  assert.equal(search.state.query.length, MAX_QUERY);
});

test("disposing the search drops its pending work", async () => {
  const timers = fakeTimers();
  let resolve;
  const search = createRemSearch({ timers, search: () => new Promise(done => { resolve = done; }) });
  search.setQuery("x");
  timers.flush();
  search.dispose();
  resolve({ total: 1, results: [result("late")] });
  await settle();
  assert.equal(search.state.status, "loading");
  assert.deepEqual(search.state.results, []);
});

test("summaries, labels and snippet text", () => {
  assert.equal(searchSummary({ query: "a", status: "loading", total: 0, results: [] }), "Searching…");
  assert.equal(searchSummary({ query: "a", status: "ready", total: 0, results: [] }), "No matches");
  assert.equal(searchSummary({ query: "a", status: "ready", total: 1, results: [result("m")] }), "1 match");
  assert.deepEqual(resultLabel({ role: "user", where: "file" }), { who: "You", where: "In a file name" });
  assert.deepEqual(resultLabel({ role: "rem", kind: "task_update", where: "task" }), { who: "Task update", where: "In the task title" });
  assert.deepEqual(resultLabel({ role: "rem", where: "message" }), { who: "Rem", where: "" });
  assert.equal(partsText([{ text: "a ", match: false }, { text: "B", match: true }]), "a B");
});
