import assert from "node:assert/strict";
import { test } from "node:test";
import { emptyThreadSelection, nextThreadSelection, presentThreadSelection, selectThreadSection, threadActionPatch, threadSelectionActions } from "./threadSelection.js";

const order = ["a", "b", "c", "d", "e"];
const ids = selection => [...selection.ids].sort();

test("click selects one thread, Ctrl toggles and Shift selects a range in display order", () => {
  let selection = nextThreadSelection(emptyThreadSelection, "b", order);
  assert.deepEqual(ids(selection), ["b"]);
  selection = nextThreadSelection(selection, "c", order, { toggle: true });
  assert.deepEqual(ids(selection), ["b", "c"]);
  selection = nextThreadSelection(selection, "c", order, { toggle: true });
  assert.deepEqual(ids(selection), ["b"]);
  selection = nextThreadSelection(selection, "d", order, { toggle: true });
  assert.deepEqual(ids(selection), ["b", "d"]);
  selection = nextThreadSelection(selection, "a", order, { range: true });
  assert.deepEqual(ids(selection), ["a", "b", "c", "d"], "The range runs from the last clicked thread");
  assert.equal(selection.anchor, "d");
  const added = nextThreadSelection({ ids: new Set(["e"]), anchor: "a" }, "b", order, { range: true, toggle: true });
  assert.deepEqual(ids(added), ["a", "b", "e"], "Ctrl+Shift adds the range to the selection");
});

test("a section header selects all its threads and a second activation clears them", () => {
  const all = selectThreadSection(emptyThreadSelection, ["b", "c"]);
  assert.deepEqual(ids(all), ["b", "c"]);
  assert.deepEqual(ids(selectThreadSection(all, ["b", "c"])), []);
  assert.deepEqual(ids(selectThreadSection({ ids: new Set(["a"]), anchor: "a" }, ["b", "c"])), ["b", "c"], "Another section's selection is replaced");
  assert.deepEqual(ids(selectThreadSection(emptyThreadSelection, [])), []);
});

test("deleted threads leave the selection", () => {
  const selection = { ids: new Set(["a", "b"]), anchor: "b" };
  assert.equal(presentThreadSelection(selection, new Set(["a", "b", "c"])), selection);
  const left = presentThreadSelection(selection, new Set(["a"]));
  assert.deepEqual([ids(left), left.anchor], [["a"], null]);
});

test("only actions that change a selected thread are offered", () => {
  const names = threads => threadSelectionActions(threads).map(item => item.action);
  assert.deepEqual(names([{ id: "a", unread: true }]), ["read", "pin", "archive", "delete"]);
  assert.deepEqual(names([{ id: "a", pinned: true }, { id: "b", archived: true, unread: true }]), ["read", "unread", "pin", "unpin", "archive", "restore", "delete"]);
  assert.deepEqual(threadSelectionActions([{ id: "a" }], { isUnread: () => true }).map(item => item.action)[0], "read",
    "A finished reply the window hasn't shown counts as unread");
  assert.deepEqual(names([]), []);
});

test("archiving marks a thread read and leaves new activity to bring it back", () => {
  const now = "2026-11-01T16:00:00.000Z";
  assert.deepEqual(threadActionPatch("archive", now), { archived: true, pinned: false, unread: false, readAt: now });
  assert.deepEqual(threadActionPatch("read", now), { unread: false, readAt: now });
  assert.deepEqual(threadActionPatch("unread", now), { unread: true });
  assert.deepEqual(threadActionPatch("restore", now), { archived: false });
  assert.equal(threadActionPatch("delete", now), null);
});
