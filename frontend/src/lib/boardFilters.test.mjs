import test from "node:test";
import assert from "node:assert/strict";
import {
  BOARD_FILTERS_KEY, GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS, ORGANIZATION_BOARD_FILTERS, ORGANIZATION_FILTER_KEYS,
  activeFilterCount, cleanBoardFilters, liveGlobalFilters, liveOrganizationFilters, loadBoardFilters, organizationBoard, resetFilters, saveBoardFilters,
} from "./boardFilters.js";

function memory() { const data = new Map(); return { data, getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key) }; }

test("each board keeps its own filters across a reload", () => {
  const storage = memory();
  saveBoardFilters(GLOBAL_BOARD, { ...GLOBAL_BOARD_FILTERS, owner: "rem", view: "people" }, GLOBAL_BOARD_FILTERS, storage);
  saveBoardFilters(organizationBoard("acme"), { ...ORGANIZATION_BOARD_FILTERS, fWorker: "w1", mine: true }, ORGANIZATION_BOARD_FILTERS, storage);
  saveBoardFilters(organizationBoard("beta"), { ...ORGANIZATION_BOARD_FILTERS, fProject: "site" }, ORGANIZATION_BOARD_FILTERS, storage);
  assert.deepEqual(loadBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, storage), { ...GLOBAL_BOARD_FILTERS, owner: "rem", view: "people" });
  assert.deepEqual(loadBoardFilters(organizationBoard("acme"), ORGANIZATION_BOARD_FILTERS, storage), { ...ORGANIZATION_BOARD_FILTERS, fWorker: "w1", mine: true });
  assert.deepEqual(loadBoardFilters(organizationBoard("beta"), ORGANIZATION_BOARD_FILTERS, storage), { ...ORGANIZATION_BOARD_FILTERS, fProject: "site" });
  assert.deepEqual(loadBoardFilters(organizationBoard("new"), ORGANIZATION_BOARD_FILTERS, storage), ORGANIZATION_BOARD_FILTERS, "an organization never filtered starts with everything");
});

test("a board back at its defaults is removed from storage", () => {
  const storage = memory();
  saveBoardFilters(GLOBAL_BOARD, { ...GLOBAL_BOARD_FILTERS, closed: true }, GLOBAL_BOARD_FILTERS, storage);
  saveBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, GLOBAL_BOARD_FILTERS, storage);
  assert.deepEqual(JSON.parse(storage.getItem(BOARD_FILTERS_KEY)).boards, {});
});

test("damaged or wrongly typed saved values fall back one at a time", () => {
  const storage = memory();
  storage.setItem(BOARD_FILTERS_KEY, "{not json");
  assert.deepEqual(loadBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, storage), GLOBAL_BOARD_FILTERS);
  storage.setItem(BOARD_FILTERS_KEY, JSON.stringify({ version: 1, boards: { global: { owner: 7, query: "voice", closed: "yes", view: "chart", person: "rem" } } }));
  assert.deepEqual(loadBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, storage), { ...GLOBAL_BOARD_FILTERS, query: "voice", person: "rem" });
  assert.deepEqual(cleanBoardFilters({ board: "epics", group: 1, fEpic: "x".repeat(600) }, ORGANIZATION_BOARD_FILTERS), { ...ORGANIZATION_BOARD_FILTERS, board: "epics" });
  assert.deepEqual(cleanBoardFilters(null, ORGANIZATION_BOARD_FILTERS), ORGANIZATION_BOARD_FILTERS);
});

test("storage that refuses writes leaves the board working", () => {
  const storage = { getItem: () => null, setItem: () => { throw new Error("full"); } };
  assert.doesNotThrow(() => saveBoardFilters(GLOBAL_BOARD, { ...GLOBAL_BOARD_FILTERS, owner: "rem" }, GLOBAL_BOARD_FILTERS, storage));
});

test("only the most recently changed organizations are kept, and never at the global board's cost", () => {
  const storage = memory();
  saveBoardFilters(GLOBAL_BOARD, { ...GLOBAL_BOARD_FILTERS, owner: "rem" }, GLOBAL_BOARD_FILTERS, storage);
  const now = Date.now;
  Date.now = () => 1000; // Every save in the same millisecond.
  try {
    for (let index = 0; index < 105; index += 1) saveBoardFilters(organizationBoard(`org-${index}`), { ...ORGANIZATION_BOARD_FILTERS, mine: true }, ORGANIZATION_BOARD_FILTERS, storage);
  } finally { Date.now = now; }
  const boards = JSON.parse(storage.getItem(BOARD_FILTERS_KEY)).boards;
  assert.equal(Object.keys(boards).length, 101);
  assert.ok(boards[organizationBoard("org-104")]);
  assert.ok(!boards[organizationBoard("org-0")]);
  assert.equal(loadBoardFilters(GLOBAL_BOARD, GLOBAL_BOARD_FILTERS, storage).owner, "rem");
});

test("a removed organization or person on the global board shows everyone without touching the other filters", () => {
  const board = { organizations: [{ organization_id: "acme", name: "Acme" }], participants: [{ participant_id: "rem" }, { participant_id: "acme/w1" }] };
  const saved = { ...GLOBAL_BOARD_FILTERS, owner: "gone", person: "gone/w9", query: "voice", closed: true, view: "board" };
  assert.deepEqual(liveGlobalFilters(saved, board), { ...saved, owner: "all", person: "" });
  const kept = { ...saved, owner: "acme", person: "acme/w1" };
  assert.deepEqual(liveGlobalFilters(kept, board), kept);
  assert.equal(liveGlobalFilters({ ...saved, owner: "rem" }, board).owner, "rem");
  assert.deepEqual(liveGlobalFilters(saved, null), saved, "nothing is dropped before the board loads");
});

test("a removed project, worker or epic on an organization's board falls back on its own", () => {
  const model = { projects: { app: {} }, workers: { w1: {} }, epics: { e1: {} } };
  const saved = { ...ORGANIZATION_BOARD_FILTERS, fProject: "gone", fWorker: "w1", fEpic: "e9", mine: true, group: true };
  assert.deepEqual(liveOrganizationFilters(saved, model), { ...saved, fProject: "all", fEpic: "all" });
  assert.equal(liveOrganizationFilters({ ...saved, fEpic: "none" }, model).fEpic, "none");
  assert.equal(liveOrganizationFilters({ ...saved, fWorker: "toString" }, model).fWorker, "all", "inherited object keys are not workers");
});

test("the count of filters on and the reset leave the view alone", () => {
  const filters = { ...GLOBAL_BOARD_FILTERS, owner: "rem", query: "x", view: "people" };
  assert.equal(activeFilterCount(filters, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS), 2);
  assert.deepEqual(resetFilters(filters, GLOBAL_BOARD_FILTERS, GLOBAL_FILTER_KEYS), { ...GLOBAL_BOARD_FILTERS, view: "people" });
  const org = { ...ORGANIZATION_BOARD_FILTERS, board: "epics", group: true, mine: true };
  assert.equal(activeFilterCount(org, ORGANIZATION_BOARD_FILTERS, ORGANIZATION_FILTER_KEYS), 1);
  assert.deepEqual(resetFilters(org, ORGANIZATION_BOARD_FILTERS, ORGANIZATION_FILTER_KEYS), { ...ORGANIZATION_BOARD_FILTERS, board: "epics", group: true });
});
