import brandCompat from "./brandCompat.js";
import { cloneJson } from "./jsonValue.js";
export const REPORT_THEMES = [
  {
    "id": "auto",
    "label": "System"
  },
  {
    "id": "light",
    "label": "Light"
  },
  {
    "id": "dark",
    "label": "Dark"
  },
  {
    "id": "sepia",
    "label": "Sepia"
  },
  {
    "id": "vaporwave",
    "label": "Vaporwave"
  },
  {
    "id": "steam",
    "label": "Steam"
  },
  {
    "id": "carbon",
    "label": "Carbon"
  },
  {
    "id": "botanical",
    "label": "Botanical"
  },
  {
    "id": "blueprint",
    "label": "Blueprint"
  },
  {
    "id": "arcade",
    "label": "Arcade"
  },
  {
    "id": "sakura",
    "label": "Sakura"
  },
  {
    "id": "deep-sea",
    "label": "Deep Sea"
  },
  {
    "id": "solarpunk",
    "label": "Solarpunk"
  },
  {
    "id": "noir",
    "label": "Noir"
  },
  {
    "id": "candy-lab",
    "label": "Candy Lab"
  },
  {
    "id": "cosmic",
    "label": "Cosmic"
  }
];

export const SETTINGS_STORAGE_KEY = "raticode.settings.v1";
export const RATICODE_BROWSER_HOME = "raticode://home";

export const KEYBINDING_COMMANDS = [
  { id: "settings.open", label: "Open settings", group: "Application", scope: "global", defaultBinding: "Mod+Comma" },
  { id: "file.open", label: "Open file", group: "Application", scope: "global", defaultBinding: "Mod+KeyO" },
  { id: "project.open", label: "Open project folder", group: "Application", scope: "global", defaultBinding: "Mod+KeyK Mod+KeyO" },
  { id: "browser.open", label: "Open browser", group: "Application", scope: "global", defaultBinding: "Ctrl+KeyJ" },
  { id: "view.graph", label: "Show graph editor", group: "Application", scope: "global", defaultBinding: "Mod+Digit1" },
  { id: "view.code", label: "Show code editor", group: "Application", scope: "global", defaultBinding: "Mod+Digit2" },
  { id: "view.toggleProjectPane", label: "Toggle project pane", group: "View", scope: "global", defaultBinding: "Ctrl+KeyB" },
  { id: "view.toggleAssistantPane", label: "Toggle Rem", group: "View", scope: "global", defaultBinding: "Ctrl+KeyL" },
  { id: "workflow.run", label: "Run workflow", group: "Workflow", scope: "global", defaultBinding: "Mod+Enter" },
  { id: "file.new", label: "New file", group: "Code editor", scope: "code", defaultBinding: "Mod+KeyN" },
  { id: "file.save", label: "Save active file", group: "Code editor", scope: "code", defaultBinding: "Mod+KeyS" },
  { id: "file.close", label: "Close active file", group: "Code editor", scope: "code", defaultBinding: "Mod+KeyW" },
  { id: "editor.toggleWordWrap", label: "Toggle word wrap", group: "Code editor", scope: "code", defaultBinding: "Alt+KeyZ" },
  { id: "panel.toggle", label: "Toggle bottom panel", group: "Panel", scope: "global", defaultBinding: "Mod+Backquote" },
  { id: "terminal.new", label: "New terminal", group: "Terminal", scope: "global", defaultBinding: "Ctrl+KeyT" },
  { id: "terminal.close", label: "Close terminal", group: "Terminal", scope: "terminal", defaultBinding: "Ctrl+KeyW" },
  { id: "graph.selectAll", label: "Select all nodes", group: "Graph editor", scope: "graph", defaultBinding: "Mod+KeyA" },
  { id: "graph.deleteSelection", label: "Delete selection", group: "Graph editor", scope: "graph", defaultBinding: "Delete" },
  { id: "graph.zoomIn", label: "Zoom in", group: "Graph editor", scope: "graph", defaultBinding: "Equal" },
  { id: "graph.zoomOut", label: "Zoom out", group: "Graph editor", scope: "graph", defaultBinding: "Minus" },
  { id: "graph.fit", label: "Fit workflow", group: "Graph editor", scope: "graph", defaultBinding: "KeyF" },
];

const DEFAULT_KEYBINDINGS = Object.fromEntries(
  KEYBINDING_COMMANDS.map((command) => [command.id, command.defaultBinding]),
);

export const DEFAULT_APP_SETTINGS = Object.freeze({
  version: 2,
  general: {
    autosave: true,
    defaultView: "graph",
    initialActivity: "workflows",
    executionMode: "local",
    checkForUpdates: true,
  },
  appearance: {
    theme: "system",
    reducedMotion: "system",
  },
  devices: {
    audioInputId: "default",
  },
  editor: {
    autosaveDelay: 1000,
    fontSize: 13,
    lineHeight: 20,
    markdownDefault: "preview",
    htmlDefault: "preview",
    minimap: true,
    tabSize: 2,
    wordWrap: false,
  },
  browser: {
    homepage: RATICODE_BROWSER_HOME,
    searchUrl: "https://www.google.com/search?q={query}",
  },
  terminal: {
    cursorBlink: true,
    fontSize: 12.5,
    lineHeight: 1.25,
    scrollback: 5000,
  },
  assistant: {
    swarmAccessEnabled: true,
    avatarEnabled: true,
    avatarAnimated: true,
    resources: { shell: true, web: false, skills: [], mcpServers: [] },
    effort: "",
    model: "",
    provider: "codex",
  },
  memory: { archiveFolder: "", secondBrainEnabled: false, secondBrainRoot: "", secondBrainFormat: "md", secondBrainTheme: "auto" },
  layout: {
    assistantPaneWidth: 380,
    bottomPanelHeight: 300,
    graphInspectorWidth: 340,
    workflowPaneWidth: 272,
  },
  keybindings: DEFAULT_KEYBINDINGS,
});

export function loadAppSettings(storage = globalThis.window?.localStorage) {
  let stored = null;
  try {
    stored = brandCompat.readBrandedStorage(storage, SETTINGS_STORAGE_KEY);
  } catch {
    return migrateLegacySettings(defaultSettingsSnapshot(), storage);
  }
  if (!stored) return migrateLegacySettings(defaultSettingsSnapshot(), storage);
  try {
    return normalizeAppSettings(JSON.parse(stored));
  } catch {
    return migrateLegacySettings(defaultSettingsSnapshot(), storage);
  }
}

export function saveAppSettings(settings, storage = globalThis.window?.localStorage) {
  const normalized = normalizeAppSettings(settings);
  try {
    storage?.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Keep the settings for this session when storage is unavailable.
  }
  return normalized;
}

export function normalizeAppSettings(value = {}) {
  const settings = mergeSettings(defaultSettingsSnapshot(), value);
  const storedVersion = Number(value?.version) || 1;
  settings.version = 2;
  settings.assistant.swarmAccessEnabled = settings.assistant.swarmAccessEnabled !== false;
  settings.assistant.avatarEnabled = settings.assistant.avatarEnabled !== false;
  settings.assistant.avatarAnimated = settings.assistant.avatarAnimated !== false;
  settings.general.autosave = settings.general.autosave !== false;
  settings.general.initialActivity = enumValue(value?.general?.initialActivity, ["workflows", "files", "search", "source-control"], value?.general?.defaultView === "code" ? "files" : "workflows");
  // Retain the old field for older installations reading these settings.
  settings.general.defaultView = settings.general.initialActivity === "workflows" ? "graph" : "code";
  settings.general.executionMode = enumValue(settings.general.executionMode, ["local", "remote"], "local");
  settings.general.checkForUpdates = settings.general.checkForUpdates !== false;
  settings.appearance.theme = enumValue(settings.appearance.theme, ["system", "light", "dark"], "system");
  settings.appearance.reducedMotion = enumValue(settings.appearance.reducedMotion, ["system", "on", "off"], "system");
  settings.devices.audioInputId = safeString(settings.devices.audioInputId, "default", 512) || "default";
  settings.editor.autosaveDelay = boundedNumber(settings.editor.autosaveDelay, 250, 10000, 1000);
  settings.editor.fontSize = boundedNumber(settings.editor.fontSize, 10, 24, 13);
  settings.editor.lineHeight = boundedNumber(settings.editor.lineHeight, 14, 40, 20);
  settings.editor.markdownDefault = enumValue(settings.editor.markdownDefault, ["preview", "edit"], "preview");
  settings.editor.htmlDefault = enumValue(settings.editor.htmlDefault, ["preview", "edit"], "preview");
  settings.editor.minimap = settings.editor.minimap !== false;
  settings.editor.tabSize = boundedNumber(settings.editor.tabSize, 1, 8, 2);
  settings.editor.wordWrap = settings.editor.wordWrap === true;
  settings.browser.homepage = safeString(
    storedVersion < 2 && settings.browser.homepage === "about:blank"
      ? RATICODE_BROWSER_HOME
      : brandCompat.canonicalHomeUrl(settings.browser.homepage),
    RATICODE_BROWSER_HOME,
    2048,
  );
  settings.browser.searchUrl = searchUrlValue(settings.browser.searchUrl);
  settings.terminal.cursorBlink = settings.terminal.cursorBlink !== false;
  settings.terminal.fontSize = boundedNumber(settings.terminal.fontSize, 8, 28, 12.5);
  settings.terminal.lineHeight = boundedNumber(settings.terminal.lineHeight, 1, 2, 1.25);
  settings.terminal.scrollback = boundedNumber(settings.terminal.scrollback, 100, 100000, 5000);
  settings.memory.secondBrainEnabled = settings.memory.secondBrainEnabled === true;
  settings.memory.secondBrainTheme = enumValue(settings.memory.secondBrainTheme, REPORT_THEMES.map((theme) => theme.id), "auto");
  settings.memory.secondBrainFormat = enumValue(settings.memory.secondBrainFormat, ["md", "html"], "md");
  settings.memory.archiveFolder = safeString(settings.memory.archiveFolder, "", 4096);
  settings.memory.secondBrainRoot = safeString(settings.memory.secondBrainRoot, "", 4096);
  settings.assistant.provider = safeString(settings.assistant.provider, "codex", 80);
  settings.assistant.model = safeString(settings.assistant.model, "", 160);
  settings.assistant.effort = safeString(settings.assistant.effort, "", 80);
  settings.layout.workflowPaneWidth = boundedNumber(settings.layout.workflowPaneWidth, 240, 420, 272);
  settings.layout.assistantPaneWidth = boundedNumber(settings.layout.assistantPaneWidth, 300, 520, 380);
  settings.layout.bottomPanelHeight = boundedNumber(settings.layout.bottomPanelHeight, 140, 480, 300);
  settings.layout.graphInspectorWidth = boundedNumber(settings.layout.graphInspectorWidth, 280, 520, 340);
  settings.keybindings = normalizeKeybindings(settings.keybindings);
  return settings;
}

export function updateSetting(settings, path, value) {
  const normalizedPath = String(path);
  const separatorIndex = normalizedPath.indexOf(".");
  const section = separatorIndex < 0 ? "" : normalizedPath.slice(0, separatorIndex);
  const key = separatorIndex < 0 ? "" : normalizedPath.slice(separatorIndex + 1);
  if (!section || !key || !(section in DEFAULT_APP_SETTINGS)) return settings;
  return normalizeAppSettings({
    ...settings,
    [section]: { ...settings[section], [key]: value },
  });
}

export function resetSetting(settings, path) {
  const normalizedPath = String(path);
  const separatorIndex = normalizedPath.indexOf(".");
  const section = separatorIndex < 0 ? "" : normalizedPath.slice(0, separatorIndex);
  const key = separatorIndex < 0 ? "" : normalizedPath.slice(separatorIndex + 1);
  return updateSetting(settings, path, DEFAULT_APP_SETTINGS[section]?.[key]);
}

export function settingBinding(settings, commandId) {
  return settings?.keybindings?.[commandId]
    ?? DEFAULT_APP_SETTINGS.keybindings[commandId]
    ?? "";
}

export function keybindingConflictIds(settings, commandId, binding = settingBinding(settings, commandId)) {
  if (!binding) return [];
  const command = KEYBINDING_COMMANDS.find((item) => item.id === commandId);
  if (!command) return [];
  return KEYBINDING_COMMANDS.filter((candidate) => (
    candidate.id !== commandId
    && settingBinding(settings, candidate.id) === binding
    && (candidate.scope === "global" || command.scope === "global" || candidate.scope === command.scope)
  )).map((candidate) => candidate.id);
}

export function matchesCommand(event, settings, commandId) {
  return matchesKeybinding(event, settingBinding(settings, commandId));
}

export function matchesKeybinding(event, binding, platform = globalThis.navigator?.platform ?? "") {
  if (!binding || event.repeat) return false;
  const parts = String(binding).split("+").filter(Boolean);
  const code = parts.at(-1);
  const modifiers = new Set(parts.slice(0, -1));
  const mac = /Mac|iPhone|iPad/i.test(platform);
  const primary = mac ? Boolean(event.metaKey) : Boolean(event.ctrlKey);
  if (modifiers.has("Mod") && !primary) return false;
  const explicitCtrl = Boolean(event.ctrlKey && !(!mac && modifiers.has("Mod")));
  const explicitMeta = Boolean(event.metaKey && !(mac && modifiers.has("Mod")));
  if (modifiers.has("Ctrl") !== explicitCtrl) return false;
  if (modifiers.has("Meta") !== explicitMeta) return false;
  if (modifiers.has("Alt") !== Boolean(event.altKey)) return false;
  const allowShiftedEqual = code === "Equal" && !modifiers.has("Shift");
  if (!allowShiftedEqual && modifiers.has("Shift") !== Boolean(event.shiftKey)) return false;
  const pressedCode = eventCode(event);
  if (code === "Delete" && pressedCode === "Backspace") return true;
  return pressedCode === code;
}

export function eventToKeybinding(event) {
  const code = eventCode(event);
  if (!code || ["ControlLeft", "ControlRight", "ShiftLeft", "ShiftRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"].includes(code)) {
    return "";
  }
  const mac = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform ?? "");
  const parts = [];
  if ((mac && event.metaKey) || (!mac && event.ctrlKey)) parts.push("Mod");
  else {
    if (event.ctrlKey) parts.push("Ctrl");
    if (event.metaKey) parts.push("Meta");
  }
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey && !(code === "Equal" && event.key === "+")) parts.push("Shift");
  parts.push(code);
  return parts.join("+");
}

export function formatKeybinding(binding, platform = globalThis.navigator?.platform ?? "") {
  if (!binding) return "Unassigned";
  const mac = /Mac|iPhone|iPad/i.test(platform);
  const labels = {
    Alt: mac ? "Option" : "Alt",
    Backquote: "`",
    Backspace: "Backspace",
    Comma: ",",
    Ctrl: "Ctrl",
    Delete: "Delete",
    Enter: "Enter",
    Equal: "+",
    Meta: mac ? "Command" : "Meta",
    Minus: "-",
    Mod: mac ? "Command" : "Ctrl",
    Shift: "Shift",
    Slash: "/",
  };
  return String(binding).split(" ").filter(Boolean).map((segment) => (
    segment.split("+").map((part) => (
      labels[part] ?? part.replace(/^Key/, "").replace(/^Digit/, "")
    )).join("+")
  )).join(", ");
}

export function resolvedTheme(settings, prefersDark = false) {
  const choice = settings?.appearance?.theme ?? "system";
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice;
}

export function reducedMotionEnabled(settings, systemPreference = false) {
  const choice = settings?.appearance?.reducedMotion ?? "system";
  if (choice === "on") return true;
  if (choice === "off") return false;
  return systemPreference;
}

export function defaultSettingsSnapshot() {
  return cloneJson(DEFAULT_APP_SETTINGS);
}

function mergeSettings(base, value) {
  if (!value || typeof value !== "object") return base;
  for (const section of Object.keys(base)) {
    if (section === "version") continue;
    if (!value[section] || typeof value[section] !== "object") continue;
    base[section] = { ...base[section], ...value[section] };
  }
  return base;
}

function migrateLegacySettings(settings, storage) {
  try {
    const legacyTheme = storage?.getItem("gofer-ui-theme");
    if (legacyTheme === "dark" || legacyTheme === "light") settings.appearance.theme = legacyTheme;
  } catch {
    // Ignore unavailable legacy storage.
  }
  return settings;
}

function normalizeKeybindings(value) {
  const bindings = { ...DEFAULT_KEYBINDINGS };
  if (!value || typeof value !== "object") return bindings;
  for (const command of KEYBINDING_COMMANDS) {
    if (typeof value[command.id] === "string") bindings[command.id] = value[command.id];
  }
  return bindings;
}

function enumValue(value, values, fallback) {
  return values.includes(value) ? value : fallback;
}

function boundedNumber(value, minimum, maximum, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(maximum, Math.max(minimum, number));
}

function safeString(value, fallback, maxLength) {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : fallback;
}

function searchUrlValue(value) {
  const candidate = safeString(value, DEFAULT_APP_SETTINGS.browser.searchUrl, 2048);
  return candidate.includes("{query}") ? candidate : DEFAULT_APP_SETTINGS.browser.searchUrl;
}

function eventCode(event) {
  if (event.code) return event.code;
  const key = String(event.key ?? "");
  if (/^[a-z]$/i.test(key)) return `Key${key.toUpperCase()}`;
  if (/^\d$/.test(key)) return `Digit${key}`;
  return {
    ",": "Comma",
    "/": "Slash",
    "`": "Backquote",
    "+": "Equal",
    "=": "Equal",
    "-": "Minus",
  }[key] ?? key;
}
