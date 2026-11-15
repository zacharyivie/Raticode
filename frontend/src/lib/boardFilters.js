import { useCallback, useEffect, useRef, useState } from "react";

// Board filters, kept on this computer: one set for the global board and one per
// organization. They come back after leaving the board and after a restart.
// Nothing here is sent to the backend or to a paired phone; the phone keeps its own.
export const BOARD_FILTERS_KEY = "raticode.boardFilters.v1";
export const GLOBAL_BOARD = "global";
export const organizationBoard = id => `organization:${id}`;
// Old organizations' filters are dropped, least recently changed first; the global board's never are.
const MAX_BOARDS = 100;

export const GLOBAL_BOARD_FILTERS = Object.freeze({ owner: "all", query: "", closed: false, person: "", view: "board" });
export const ORGANIZATION_BOARD_FILTERS = Object.freeze({ board: "tickets", group: false, fProject: "all", fWorker: "all", fEpic: "all", mine: false });
// Which values narrow the tickets shown. The rest (the view, the board type and lanes)
// choose how the board looks; resetting the filters keeps them.
export const GLOBAL_FILTER_KEYS = Object.freeze(["owner", "query", "closed", "person"]);
export const ORGANIZATION_FILTER_KEYS = Object.freeze(["fProject", "fWorker", "fEpic", "mine"]);
const CHOICES = { view: ["board", "people"], board: ["tickets", "epics"] };
const MAX_TEXT = 500;

function readAll(storage) {
  try {
    const value = JSON.parse(storage?.getItem(BOARD_FILTERS_KEY) || "null");
    return value && typeof value === "object" && value.boards && typeof value.boards === "object" && !Array.isArray(value.boards) ? value : { boards: {} };
  } catch { return { boards: {} }; }
}

// Each saved value is kept only when it has its default's type and, for a fixed choice,
// is one of the choices; anything else falls back to its default on its own.
export function cleanBoardFilters(saved, defaults) {
  const value = saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
  return Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => {
    const item = value[key];
    if (typeof item !== typeof fallback) return [key, fallback];
    if (typeof item === "string" && (item.length > MAX_TEXT || (CHOICES[key] && !CHOICES[key].includes(item)))) return [key, fallback];
    return [key, item];
  }));
}

export function loadBoardFilters(board, defaults, storage = globalThis.window?.localStorage) {
  return cleanBoardFilters(readAll(storage).boards[board], defaults);
}

export function saveBoardFilters(board, filters, defaults, storage = globalThis.window?.localStorage) {
  const all = readAll(storage);
  const boards = { ...all.boards };
  const value = cleanBoardFilters(filters, defaults);
  // Only what differs from the defaults is kept, so a board at its defaults takes no space.
  const changed = Object.fromEntries(Object.entries(value).filter(([key, item]) => item !== defaults[key]));
  delete boards[board];
  const global = board !== GLOBAL_BOARD && boards[GLOBAL_BOARD] ? [[GLOBAL_BOARD, boards[GLOBAL_BOARD]]] : [];
  delete boards[GLOBAL_BOARD];
  const others = Object.entries(boards).sort(([, a], [, b]) => (Number(b?.savedAt) || 0) - (Number(a?.savedAt) || 0));
  // The board just changed always stays, even when others were saved in the same millisecond.
  const kept = [...global, ...(Object.keys(changed).length ? [[board, { ...changed, savedAt: Date.now() }], ...others] : others).slice(0, MAX_BOARDS)];
  try { storage?.setItem(BOARD_FILTERS_KEY, JSON.stringify({ version: 1, boards: Object.fromEntries(kept) })); } catch { /* The board still filters when local storage is full. */ }
}

export function activeFilterCount(filters, defaults, keys) {
  return keys.filter(key => filters[key] !== defaults[key]).length;
}

export function resetFilters(filters, defaults, keys) {
  return { ...filters, ...Object.fromEntries(keys.map(key => [key, defaults[key]])) };
}

// A saved organization or person that is no longer on the global board shows everyone
// again; the other filters stay as they were.
export function liveGlobalFilters(filters, board) {
  if (!board) return filters;
  const owners = new Set(["all", "rem", ...(board.organizations || []).map(org => org.organization_id)]);
  const people = new Set((board.participants || []).map(person => person.participant_id));
  return {
    ...filters,
    owner: owners.has(filters.owner) ? filters.owner : GLOBAL_BOARD_FILTERS.owner,
    person: !filters.person || people.has(filters.person) ? filters.person : GLOBAL_BOARD_FILTERS.person,
  };
}

// The same for one organization's board: a removed project, worker or epic falls back alone.
export function liveOrganizationFilters(filters, model) {
  if (!model) return filters;
  const has = (items, id) => Boolean(items && Object.prototype.hasOwnProperty.call(items, id));
  return {
    ...filters,
    fProject: filters.fProject === "all" || has(model.projects, filters.fProject) ? filters.fProject : "all",
    fWorker: filters.fWorker === "all" || has(model.workers, filters.fWorker) ? filters.fWorker : "all",
    fEpic: ["all", "none"].includes(filters.fEpic) || has(model.epics, filters.fEpic) ? filters.fEpic : "all",
  };
}

// Filters for one board, read once when the board opens and saved after each change.
// `live` drops saved values the board no longer has; a change starts from what is shown.
export function useBoardFilters(board, defaults, live = value => value, storage = globalThis.window?.localStorage) {
  const [saved, setSaved] = useState(() => loadBoardFilters(board, defaults, storage));
  const liveRef = useRef(live);
  liveRef.current = live;
  const changed = useRef(false);
  useEffect(() => {
    if (changed.current) saveBoardFilters(board, saved, defaults, storage);
  }, [board, saved, defaults, storage]);
  const set = useCallback(change => {
    changed.current = true;
    setSaved(current => {
      const shown = liveRef.current(current);
      return cleanBoardFilters({ ...shown, ...(typeof change === "function" ? change(shown) : change) }, defaults);
    });
  }, [defaults]);
  return [live(saved), set];
}
