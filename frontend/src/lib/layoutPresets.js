// Layout choices offered in Settings > Layout. Kept apart from
// workspaceLayout.js so they load with Settings, not the workspace.

export const PANE_SIDE_OPTIONS = [["left", "Left"], ["right", "Right"]];

export const TERMINAL_LOCATION_OPTIONS = [["panel", "Bottom panel"], ["editor", "Editor tab"]];

export const BOTTOM_PANEL_STARTUP_OPTIONS = [
  ["collapsed", "Nothing, keep it collapsed"],
  ["problems", "Problems"],
  ["timeline", "Run Timeline"],
  ["runs", "Runs"],
  ["terminal", "Terminal"],
];

export const LAYOUT_PRESETS = [
  { id: "classic", label: "Classic", description: "Files left, Rem right", projectPaneSide: "left", assistantPaneSide: "right" },
  { id: "rem-first", label: "Rem first", description: "Rem left, files right", projectPaneSide: "right", assistantPaneSide: "left" },
  { id: "panes-left", label: "Panes left", description: "Files and Rem on the left", projectPaneSide: "left", assistantPaneSide: "left" },
  { id: "panes-right", label: "Panes right", description: "Files and Rem on the right", projectPaneSide: "right", assistantPaneSide: "right" },
];

export function activeLayoutPreset(layout = {}) {
  return LAYOUT_PRESETS.find((preset) => (
    preset.projectPaneSide === layout.projectPaneSide
    && preset.assistantPaneSide === layout.assistantPaneSide
  ))?.id ?? "";
}

// Left-to-right order of the project pane, Rem and the editor. Panes that
// share an edge keep the project pane, with its activity bar, outermost.
export function workspacePaneOrder(layout = {}) {
  const project = layout.projectPaneSide === "right" ? "right" : "left";
  const assistant = layout.assistantPaneSide === "left" ? "left" : "right";
  const left = [project === "left" && "project", assistant === "left" && "assistant"].filter(Boolean);
  const right = [assistant === "right" && "assistant", project === "right" && "project"].filter(Boolean);
  return [...left, "editor", ...right];
}
