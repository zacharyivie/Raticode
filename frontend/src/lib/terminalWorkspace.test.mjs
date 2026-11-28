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

test("Find in terminal is Ctrl+Shift+F off a Mac and leaves the shell its Ctrl+F", async () => {
  const { DEFAULT_APP_SETTINGS, KEYBINDING_COMMANDS, matchesCommand } = await import("./settings.js");
  const { terminalWorkspaceShortcutAction } = await import("./terminalWorkspace.js");
  const command = KEYBINDING_COMMANDS.find((item) => item.id === "terminal.find");
  assert.deepEqual([command.label, command.group, command.scope], ["Find in terminal", "Terminal", "terminal"]);
  const key = (init) => ({ type: "keydown", key: "f", code: "KeyF", ctrlKey: false, shiftKey: false, metaKey: false, altKey: false, ...init });
  const options = { active: true, activeKey: "terminal-1", settings: DEFAULT_APP_SETTINGS };
  assert.equal(terminalWorkspaceShortcutAction(key({ ctrlKey: true, shiftKey: true, key: "F" }), options), "find");
  assert.equal(terminalWorkspaceShortcutAction(key({ ctrlKey: true }), options), null, "readline keeps Ctrl+F");
  assert.equal(terminalWorkspaceShortcutAction(key({ ctrlKey: true, shiftKey: true, key: "F" }), { ...options, activeKey: null }), null);
  // A Mac's default is Command+F, picked when settings.js loads on that platform.
  assert.ok(matchesCommand(key({ metaKey: true }), { keybindings: { "terminal.find": "Mod+KeyF" } }, "terminal.find", "MacIntel"));
  const rebound = { keybindings: { ...DEFAULT_APP_SETTINGS.keybindings, "terminal.find": "Alt+KeyG" } };
  assert.equal(terminalWorkspaceShortcutAction(key({ altKey: true, key: "g", code: "KeyG" }), { ...options, settings: rebound }), "find");
  assert.equal(terminalWorkspaceShortcutAction(key({ ctrlKey: true, shiftKey: true, key: "F" }), { ...options, settings: rebound }), null);
});

test("the find bar's count reads the addon's results and its highlight limit", async () => {
  const { terminalFindCountText, terminalFindInitialTerm } = await import("./terminalWorkspace.js");
  assert.equal(terminalFindCountText(null, "npm"), "");
  assert.equal(terminalFindCountText({ resultIndex: 1, resultCount: 4 }, ""), "");
  assert.equal(terminalFindCountText({ resultIndex: -1, resultCount: 0 }, "npm"), "No results");
  assert.equal(terminalFindCountText({ resultIndex: 1, resultCount: 4 }, "npm"), "2 of 4");
  assert.equal(terminalFindCountText({ resultIndex: -1, resultCount: 4 }, "npm"), "4 found");
  assert.equal(terminalFindCountText({ resultIndex: 0, resultCount: 1000 }, "a"), "1 of 1000+");
  assert.equal(terminalFindInitialTerm("error TS2304"), "error TS2304");
  assert.equal(terminalFindInitialTerm("line one\nline two"), "");
  assert.equal(terminalFindInitialTerm("   "), "");
});
