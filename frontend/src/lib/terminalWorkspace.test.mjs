import assert from "node:assert/strict";
import { test } from "node:test";
import { groupTerminalTabsByProject, terminalTabGroupId } from "./terminalWorkspace.js";

test("terminal project groups merge equivalent Windows folders", () => {
  const groups = groupTerminalTabsByProject([
    { key: "a", projectPath: "C:\\Repo" },
    { key: "b", projectPath: "c:/repo/" },
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].items.length, 2);
});

test("saved terminal project IDs still match after normalization", () => {
  assert.equal(terminalTabGroupId({ groupId: "project:C:\\Repo" }), "project:c:/repo");
  const groups = groupTerminalTabsByProject([{ key: "a", projectPath: "c:/repo/" }], [{ id: "project:C:\\Repo", keepEmpty: true, name: "My terminals" }]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].name, "My terminals");
  assert.equal(groups[0].items.length, 1);
});
