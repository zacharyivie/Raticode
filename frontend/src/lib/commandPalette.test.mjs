import assert from "node:assert/strict";
import test from "node:test";

import {
  PALETTE_COMMAND_ID,
  PALETTE_RECENT_STORAGE_KEY,
  QUICK_OPEN_COMMAND_ID,
  RECENT_LIMIT,
  actionPaletteItems,
  fuzzyMatch,
  keybindingPaletteItems,
  loadRecentIds,
  paletteGivesWay,
  quickOpenItems,
  rankItems,
  rememberRecentId,
} from "./commandPalette.js";
import {
  CHORD_COMMAND_IDS,
  DEFAULT_APP_SETTINGS,
  KEYBINDING_COMMANDS,
  keybindingConflictIds,
  normalizeAppSettings,
  updateSetting,
} from "./settings.js";
import { SETTINGS_SECTIONS } from "./settingsSections.js";
import { SIDEBAR_PLUGINS, pluginAvailable } from "./plugins.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    values,
  };
}

test("Mod+Shift+P and Mod+P are rebindable global commands that take chords", () => {
  const palette = KEYBINDING_COMMANDS.find((command) => command.id === PALETTE_COMMAND_ID);
  const quickOpen = KEYBINDING_COMMANDS.find((command) => command.id === QUICK_OPEN_COMMAND_ID);
  assert.equal(palette.defaultBinding, "Mod+Shift+KeyP");
  assert.equal(quickOpen.defaultBinding, "Mod+KeyP");
  assert.equal(palette.scope, "global");
  assert.equal(quickOpen.scope, "global");
  assert.ok(CHORD_COMMAND_IDS.has(PALETTE_COMMAND_ID) && CHORD_COMMAND_IDS.has(QUICK_OPEN_COMMAND_ID));
  // The defaults clash with nothing else in Settings > Keybindings.
  assert.deepEqual(keybindingConflictIds(DEFAULT_APP_SETTINGS, PALETTE_COMMAND_ID, undefined, "Linux"), []);
  assert.deepEqual(keybindingConflictIds(DEFAULT_APP_SETTINGS, QUICK_OPEN_COMMAND_ID, undefined, "Linux"), []);
  // A rebinding, including a chord, survives a reload.
  const rebound = updateSetting(DEFAULT_APP_SETTINGS, `keybindings.${QUICK_OPEN_COMMAND_ID}`, "Mod+KeyK Mod+KeyP");
  assert.equal(normalizeAppSettings(JSON.parse(JSON.stringify(rebound))).keybindings[QUICK_OPEN_COMMAND_ID], "Mod+KeyK Mod+KeyP");
});

test("the palette lists every keybinding command with its current key", () => {
  const settings = updateSetting(updateSetting(DEFAULT_APP_SETTINGS, "keybindings.view.graph", "Alt+KeyG"), "keybindings.file.new", "");
  const items = keybindingPaletteItems(settings, "Linux");
  const listed = new Set(items.map((item) => item.commandId));
  for (const command of KEYBINDING_COMMANDS) {
    if (command.id.startsWith("palette.")) assert.ok(!listed.has(command.id), "the palette doesn't list itself");
    else assert.ok(listed.has(command.id), `missing ${command.id}`);
  }
  assert.equal(items.find((item) => item.commandId === "view.graph").keyLabel, "Alt+G");
  assert.equal(items.find((item) => item.commandId === "settings.open").keyLabel, "Ctrl+,");
  assert.equal(items.find((item) => item.commandId === "project.open").keyLabel, "Ctrl+K, Ctrl+O");
  assert.equal(items.find((item) => item.commandId === "file.new").keyLabel, "", "a cleared key shows no key");
  assert.equal(keybindingPaletteItems(DEFAULT_APP_SETTINGS, "MacIntel").find((item) => item.commandId === "settings.open").keyLabel, "Command+,");
});

test("actions without keys cover settings sections, workflows, triggers and plugins", () => {
  const items = actionPaletteItems();
  const labels = items.map((item) => item.label);
  for (const label of ["New workflow", "All workflow runs", "Pause all triggers", "Go to Posts", "Go to Organizations", "Go to Second Brain", "Go to Browser tabs"]) {
    assert.ok(labels.includes(label), `missing ${label}`);
  }
  for (const section of SETTINGS_SECTIONS) assert.ok(labels.includes(`Settings: ${section.label}`));
  const paused = actionPaletteItems({ triggersPaused: true }).map((item) => item.label);
  assert.ok(paused.includes("Resume all triggers"));
  assert.ok(!paused.includes("Pause all triggers"));
  assert.equal(new Set(items.map((item) => item.id)).size, items.length, "ids are unique");
  assert.equal(items.find((item) => item.label === "Go to Posts").description, "Experimental");
  assert.equal(items.find((item) => item.label === "Go to Organizations").description, undefined);
});

test("the palette can go to every plugin in the activity rail, and the obvious words find them", () => {
  const goTo = actionPaletteItems().filter((item) => item.group === "Go to");
  const byPlugin = new Map(goTo.map((item) => [item.pluginId, item]));
  for (const id of ["rem", "workflows", "files", "search", "source-control", "kanban", "swarms"]) {
    assert.equal(byPlugin.get(id)?.actionId, `open.${id}`, `missing Go to for ${id}`);
  }
  // Every rail plugin has one, named as the rail names it.
  for (const plugin of SIDEBAR_PLUGINS) {
    assert.ok(byPlugin.has(plugin.id), `missing Go to for ${plugin.id}`);
    if (plugin.id !== "browser") assert.equal(byPlugin.get(plugin.id).label, `Go to ${plugin.label}`);
  }
  assert.equal(byPlugin.get("kanban").label, "Go to Kanban");
  // App.jsx drops plugins that aren't available: Swarms waits for Feature Preview.
  const shown = (plugins) => goTo.filter((item) => pluginAvailable(plugins, item.pluginId)).map((item) => item.pluginId);
  assert.ok(!shown(DEFAULT_APP_SETTINGS.plugins).includes("swarms"));
  assert.ok(shown({ ...DEFAULT_APP_SETTINGS.plugins, preview: { swarms: true } }).includes("swarms"));
  for (const [query, label] of [["git", "Go to Source control"], ["tickets", "Go to Kanban"], ["kanban", "Go to Kanban"], ["explorer", "Go to File explorer"], ["find", "Go to Search"]]) {
    assert.equal(rankItems(goTo, query)[0]?.label, label, `"${query}" finds ${label}`);
  }
});

test("fuzzy matching finds letters in order, across words, and prefers word starts", () => {
  assert.ok(fuzzyMatch("tgl", "Toggle project pane"));
  assert.ok(fuzzyMatch("pane toggle", "Toggle project pane"), "words match in any order");
  assert.equal(fuzzyMatch("xyz", "Toggle project pane"), null);
  assert.deepEqual(fuzzyMatch("tpp", "Toggle project pane").indices, [0, 7, 15]);
  // Jumping to a later word start must not lose a match that exists.
  assert.ok(fuzzyMatch("abc", "axbyc-b"));
  assert.ok(fuzzyMatch("set dev", "Settings: Devices"));
  assert.deepEqual(fuzzyMatch("", "anything"), { score: 0, indices: [] });
  const start = fuzzyMatch("run", "Run workflow").score;
  const middle = fuzzyMatch("run", "Show all workflow runs").score;
  const scattered = fuzzyMatch("run", "Return unknown").score;
  assert.ok(start > middle && middle > scattered);
});

test("ranking puts recent items first, then the best matches", () => {
  const items = [
    { id: "a", label: "App.jsx", description: "frontend/src/pages" },
    { id: "b", label: "app-log.cjs", description: "frontend/electron" },
    { id: "c", label: "settings.js", description: "frontend/src/lib" },
    { id: "d", label: "Map.jsx", description: "frontend/src/app" },
  ];
  assert.deepEqual(rankItems(items, "").map((item) => item.id), ["a", "b", "c", "d"]);
  assert.deepEqual(rankItems(items, "", ["c", "d"]).map((item) => item.id), ["c", "d", "a", "b"]);
  assert.equal(rankItems(items, "", ["c"])[0].recent, true);
  assert.deepEqual(rankItems(items, "app").map((item) => item.id).slice(0, 2), ["a", "b"], "label matches beat folder matches");
  assert.deepEqual(rankItems(items, "app", ["d"]).map((item) => item.id)[0], "d", "a recent match comes first");
  assert.deepEqual(rankItems(items, "zzz"), []);
  assert.ok(rankItems(items, "app")[0].indices.length, "matched letters are returned for highlighting");
  assert.equal(rankItems(items, "", [], 2).length, 2);
});

test("recent ids are kept per list, newest first, without repeats, and capped", () => {
  const storage = memoryStorage();
  rememberRecentId("files", "file:/a", storage);
  rememberRecentId("files", "file:/b", storage);
  rememberRecentId("commands", "command:view.graph", storage);
  assert.deepEqual(rememberRecentId("files", "file:/a", storage), ["file:/a", "file:/b"]);
  assert.deepEqual(loadRecentIds("commands", storage), ["command:view.graph"]);
  for (let index = 0; index < RECENT_LIMIT + 5; index++) rememberRecentId("files", `file:/${index}`, storage);
  assert.equal(loadRecentIds("files", storage).length, RECENT_LIMIT);
  assert.deepEqual(loadRecentIds("files", memoryStorage({ [PALETTE_RECENT_STORAGE_KEY]: "not json" })), []);
  assert.deepEqual(loadRecentIds("files", memoryStorage({ [PALETTE_RECENT_STORAGE_KEY]: JSON.stringify({ files: [1, "x"] }) })), ["x"]);
  const broken = { getItem: () => null, setItem: () => { throw new Error("full"); } };
  assert.deepEqual(rememberRecentId("files", "x", broken), ["x"]);
});

test("quick open lists workflows, Rem threads, settings sections and files", () => {
  const items = quickOpenItems({
    files: [{ path: "/p/src/App.jsx", relativePath: "src/App.jsx" }, { path: "/p/README.md", relativePath: "README.md" }],
    workflows: [{ id: "w1", name: "Summarize a link", projectRoot: "/p" }],
    threads: [{ id: "t1", title: "Fix the login page", projectRoot: "/p" }, { id: "t2", title: "  " }, { id: "t3", title: "Plan", scopeMode: "global" }],
    projectName: "p",
  });
  const byId = Object.fromEntries(items.map((item) => [item.id, item]));
  assert.equal(byId["workflow:w1"].label, "Summarize a link");
  assert.equal(byId["thread:t1"].label, "Fix the login page");
  assert.equal(byId["thread:t3"].description, "Global");
  assert.ok(!byId["thread:t2"], "untitled threads are left out");
  assert.equal(byId["file:/p/src/App.jsx"].label, "App.jsx");
  assert.equal(byId["file:/p/src/App.jsx"].description, "src");
  assert.equal(byId["file:/p/README.md"].description, "p");
  assert.ok(byId["settings:keybindings"]);
  assert.equal(rankItems(items, "login")[0].id, "thread:t1");
  assert.equal(rankItems(items, "summ")[0].id, "workflow:w1");
  assert.equal(rankItems(items, "keybind")[0].id, "settings:keybindings");
});

test("the palette keys give way to a focused terminal or code editor", () => {
  const inside = (selector) => ({ closest: (query) => (query.split(",").map((part) => part.trim()).includes(selector) ? {} : null) });
  assert.equal(paletteGivesWay(inside(".xterm")), true);
  assert.equal(paletteGivesWay(inside("[data-terminal-workspace]")), true);
  assert.equal(paletteGivesWay(inside(".monaco-editor")), true);
  assert.equal(paletteGivesWay(inside(".studio-sidebar")), false);
  assert.equal(paletteGivesWay(null), false);
});
