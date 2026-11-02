import assert from "node:assert/strict";
import test from "node:test";
import { bottomPanelStartState, isTerminalTabPath, TERMINAL_TAB_PATH, terminalInEditor } from "./workspaceLayout.js";
import { activeLayoutPreset, LAYOUT_PRESETS, workspacePaneOrder } from "./layoutPresets.js";
import { DEFAULT_APP_SETTINGS, normalizeAppSettings, updateSetting } from "./settings.js";

test("pane order keeps the project pane outermost on a shared edge", () => {
  assert.deepEqual(workspacePaneOrder({ projectPaneSide: "left", assistantPaneSide: "right" }), ["project", "editor", "assistant"]);
  assert.deepEqual(workspacePaneOrder({ projectPaneSide: "right", assistantPaneSide: "left" }), ["assistant", "editor", "project"]);
  assert.deepEqual(workspacePaneOrder({ projectPaneSide: "left", assistantPaneSide: "left" }), ["project", "assistant", "editor"]);
  assert.deepEqual(workspacePaneOrder({ projectPaneSide: "right", assistantPaneSide: "right" }), ["editor", "assistant", "project"]);
  assert.deepEqual(workspacePaneOrder({}), ["project", "editor", "assistant"]);
});

test("each preset is recognized from the sides it sets", () => {
  for (const preset of LAYOUT_PRESETS) assert.equal(activeLayoutPreset(preset), preset.id);
  assert.equal(activeLayoutPreset(DEFAULT_APP_SETTINGS.layout), "classic");
  assert.equal(activeLayoutPreset({ projectPaneSide: "middle" }), "");
});

test("the bottom panel opens the chosen startup view unless the terminal lives in the editor", () => {
  assert.deepEqual(bottomPanelStartState(DEFAULT_APP_SETTINGS.layout), { collapsed: true, tab: "timeline", explicit: false });
  assert.deepEqual(bottomPanelStartState({ bottomPanelAtStartup: "runs" }), { collapsed: false, tab: "runs", explicit: true });
  assert.deepEqual(bottomPanelStartState({ bottomPanelAtStartup: "terminal", terminalLocation: "panel" }), { collapsed: false, tab: "terminal", explicit: true });
  assert.deepEqual(bottomPanelStartState({ bottomPanelAtStartup: "terminal", terminalLocation: "editor" }), { collapsed: true, tab: "timeline", explicit: false });
});

test("terminal tab helpers recognize only the terminal tab", () => {
  assert.equal(isTerminalTabPath(TERMINAL_TAB_PATH), true);
  assert.equal(isTerminalTabPath("/repo/raticode-terminal:workspace"), false);
  assert.equal(terminalInEditor({ layout: { terminalLocation: "editor" } }), true);
  assert.equal(terminalInEditor(DEFAULT_APP_SETTINGS), false);
});

test("layout settings fall back to safe values", () => {
  const settings = normalizeAppSettings({ layout: {
    projectPaneSide: "top",
    assistantPaneSide: "left",
    terminalLocation: "window",
    projectPaneAtStartup: false,
    assistantPaneAtStartup: "no",
    bottomPanelAtStartup: "output",
    compactWidth: 12000,
  } });
  assert.equal(settings.layout.projectPaneSide, "left");
  assert.equal(settings.layout.assistantPaneSide, "left");
  assert.equal(settings.layout.terminalLocation, "panel");
  assert.equal(settings.layout.projectPaneAtStartup, false);
  assert.equal(settings.layout.assistantPaneAtStartup, true);
  assert.equal(settings.layout.bottomPanelAtStartup, "collapsed");
  assert.equal(settings.layout.compactWidth, 2000);
  assert.equal(normalizeAppSettings({ layout: { compactWidth: -5 } }).layout.compactWidth, 0);
  assert.equal(updateSetting(settings, "layout.terminalLocation", "editor").layout.terminalLocation, "editor");
  // Older settings without the new fields keep today's layout.
  const legacy = normalizeAppSettings({ layout: { workflowPaneWidth: 300 } });
  assert.equal(legacy.layout.projectPaneSide, "left");
  assert.equal(legacy.layout.assistantPaneSide, "right");
  assert.equal(legacy.layout.compactWidth, 1000);
});
