// The command palette (every command, with its key) and quick open (files,
// workflows, Rem threads and Settings sections). This module holds what can be
// worked out without the app: matching, ordering, recent items and the lists.
import { KEYBINDING_COMMANDS, formatKeybinding, settingBinding } from "./settings.js";
import { SETTINGS_SECTIONS } from "./settingsSections.js";

export const PALETTE_COMMAND_ID = "palette.commands";
export const QUICK_OPEN_COMMAND_ID = "palette.quickOpen";
export const PALETTE_RECENT_STORAGE_KEY = "raticode.palette.recent.v1";
export const RECENT_LIMIT = 30;
// The most rows the list shows at once. Typing narrows it.
export const SHOWN_LIMIT = 200;

// Places where the palette keys belong to what has focus: a terminal sends them
// to the shell and the code editor keeps its own keys.
const GIVES_WAY_SELECTOR = ".xterm, [data-terminal-workspace], .monaco-editor";

export function paletteGivesWay(target) {
  return Boolean(target?.closest?.(GIVES_WAY_SELECTOR));
}

const WORD_BREAK = /[\s/\\_.\-:>()[\]]/;

function wordStart(text, index) {
  if (index === 0) return true;
  const previous = text[index - 1];
  if (WORD_BREAK.test(previous)) return true;
  // camelCase and PascalCase humps.
  const current = text[index];
  return previous === previous.toLowerCase() && current !== current.toLowerCase();
}

// Scores one query word against the text, or null when its letters don't all
// appear in order. Runs of letters and matches at the start of words score higher.
function matchWord(word, text) {
  const lower = text.toLowerCase();
  const contiguous = lower.indexOf(word);
  if (contiguous >= 0) {
    // Prefer a copy at the start of a word over the first one.
    let at = contiguous;
    for (let index = contiguous; index >= 0; index = lower.indexOf(word, index + 1)) {
      if (wordStart(text, index)) { at = index; break; }
    }
    const indices = Array.from({ length: word.length }, (_, offset) => at + offset);
    return { score: 20 + word.length * 6 + (wordStart(text, at) ? 12 : 0) + (at === 0 ? 6 : 0), indices };
  }
  // Jumping ahead to word starts reads better ("gr" in "Graph Run"), but can skip
  // past letters a later one needs, so the plain in-order match is tried too.
  const starts = matchLetters(word, text, lower, true);
  const plain = matchLetters(word, text, lower, false);
  if (!starts) return plain;
  if (!plain) return starts;
  return starts.score >= plain.score ? starts : plain;
}

function matchLetters(word, text, lower, preferStarts) {
  const indices = [];
  let score = 0;
  let from = 0;
  for (const letter of word) {
    const plain = lower.indexOf(letter, from);
    if (plain < 0) return null;
    let found = plain;
    if (preferStarts && plain !== indices.at(-1) + 1) {
      for (let index = plain; index < lower.length; index++) {
        if (lower[index] === letter && wordStart(text, index)) { found = index; break; }
      }
    }
    const previous = indices.at(-1);
    score += 1;
    if (previous !== undefined && found === previous + 1) score += 5;
    if (wordStart(text, found)) score += 4;
    if (previous !== undefined) score -= Math.min(3, found - previous - 1) * 0.5;
    indices.push(found);
    from = found + 1;
  }
  return { score, indices };
}

// Every word of the query must match, in any order. Returns the total score and
// the matched letters, or null.
export function fuzzyMatch(query, text) {
  const words = String(query ?? "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  const value = String(text ?? "");
  if (!words.length) return { score: 0, indices: [] };
  let score = 0;
  const indices = new Set();
  for (const word of words) {
    const match = matchWord(word, value);
    if (!match) return null;
    score += match.score;
    for (const index of match.indices) indices.add(index);
  }
  return { score, indices: [...indices].sort((a, b) => a - b) };
}

// An item matches on its label, or on its label with its group and detail.
// A label match scores higher, so "app" finds App.jsx before src/app/x.js.
export function matchItem(item, query) {
  if (!String(query ?? "").trim()) return { score: 0, indices: [] };
  const label = fuzzyMatch(query, item.label);
  const whole = fuzzyMatch(query, [item.group, item.label, item.description, ...(item.keywords || [])].filter(Boolean).join(" "));
  if (!label && !whole) return null;
  if (label && (!whole || label.score + 25 >= whole.score)) return { score: label.score + 25, indices: label.indices };
  return { score: whole.score, indices: [] };
}

// Recent items come first, most recent at the top. Without a query the rest
// keep their order; with one they follow by how well they match.
export function rankItems(items, query, recentIds = [], limit = SHOWN_LIMIT) {
  const recent = new Map(recentIds.map((id, index) => [id, index]));
  const typed = Boolean(String(query ?? "").trim());
  const matched = [];
  items.forEach((item, order) => {
    const match = matchItem(item, query);
    if (match) matched.push({ item, order, score: match.score, indices: match.indices, recent: recent.get(item.id) });
  });
  matched.sort((left, right) => {
    const leftRecent = left.recent ?? Infinity;
    const rightRecent = right.recent ?? Infinity;
    if (leftRecent !== rightRecent) return leftRecent - rightRecent;
    if (typed && right.score !== left.score) return right.score - left.score;
    return left.order - right.order;
  });
  return matched.slice(0, limit).map(({ item, indices, recent: recentIndex }) => ({ ...item, indices, recent: recentIndex !== undefined }));
}

function readRecent(storage) {
  try {
    const value = JSON.parse(storage?.getItem(PALETTE_RECENT_STORAGE_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export function loadRecentIds(mode, storage = globalThis.window?.localStorage) {
  const list = readRecent(storage)[mode];
  return Array.isArray(list) ? list.filter((id) => typeof id === "string").slice(0, RECENT_LIMIT) : [];
}

export function rememberRecentId(mode, id, storage = globalThis.window?.localStorage) {
  if (!id) return loadRecentIds(mode, storage);
  const all = readRecent(storage);
  const next = [id, ...loadRecentIds(mode, storage).filter((existing) => existing !== id)].slice(0, RECENT_LIMIT);
  try {
    storage?.setItem(PALETTE_RECENT_STORAGE_KEY, JSON.stringify({ ...all, [mode]: next }));
  } catch {
    // Recent items are a convenience; the palette works without storage.
  }
  return next;
}

// One palette row per entry in Settings > Keybindings, showing its current key.
export function keybindingPaletteItems(settings, platform) {
  return KEYBINDING_COMMANDS.filter((command) => !command.id.startsWith("palette.")).map((command) => {
    const binding = settingBinding(settings, command.id);
    return {
      id: `command:${command.id}`,
      commandId: command.id,
      group: command.group,
      label: command.label,
      keyLabel: binding ? formatKeybinding(binding, platform) : "",
      scope: command.scope,
    };
  });
}

export const PLUGIN_DESTINATIONS = Object.freeze([
  { pluginId: "browser", label: "Go to Browser tabs" },
  { pluginId: "posts", label: "Go to Posts", description: "Experimental" },
  { pluginId: "organizations", label: "Go to Organizations" },
  { pluginId: "second-brain", label: "Go to Second Brain" },
]);

// Actions that have no key of their own.
export function actionPaletteItems({ triggersPaused = false } = {}) {
  return [
    { id: "action:workflow.new", actionId: "workflow.new", group: "Workflow", label: "New workflow" },
    { id: "action:workflow.allRuns", actionId: "workflow.allRuns", group: "Workflow", label: "All workflow runs" },
    triggersPaused
      ? { id: "action:triggers.resume", actionId: "triggers.resume", group: "Workflow", label: "Resume all triggers", keywords: ["pause"] }
      : { id: "action:triggers.pause", actionId: "triggers.pause", group: "Workflow", label: "Pause all triggers" },
    ...PLUGIN_DESTINATIONS.map(({ pluginId, label, description }) => ({ id: `action:open.${pluginId}`, actionId: `open.${pluginId}`, pluginId, group: "Go to", label, ...(description ? { description } : {}) })),
    ...SETTINGS_SECTIONS.map((section) => settingsSectionItem(section)),
  ];
}

export function settingsSectionItem(section) {
  return { id: `settings:${section.id}`, settingsSection: section.id, group: "Settings", label: `Settings: ${section.label}`, keywords: ["preferences", "open settings"] };
}

function lastSegment(value) {
  const parts = String(value ?? "").split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || String(value ?? "");
}

function parentOf(relativePath) {
  const parts = String(relativePath ?? "").split(/[\\/]/).filter(Boolean);
  return parts.slice(0, -1).join("/");
}

// Quick open's rows: Settings sections, workflows, Rem threads and project files.
export function quickOpenItems({ files = [], workflows = [], threads = [], projectName = "" } = {}) {
  const items = [];
  for (const workflow of workflows) {
    if (!workflow?.id) continue;
    items.push({
      id: `workflow:${workflow.id}`,
      kind: "workflow",
      workflowId: workflow.id,
      group: "Workflow",
      label: workflow.name || workflow.id,
      description: lastSegment(workflow.projectRoot) || "",
    });
  }
  for (const thread of threads) {
    if (!thread?.id || !String(thread.title ?? "").trim()) continue;
    items.push({
      id: `thread:${thread.id}`,
      kind: "thread",
      threadId: thread.id,
      group: "Rem thread",
      label: String(thread.title).trim(),
      description: thread.scopeMode === "global" ? "Global" : lastSegment(thread.projectRoot),
    });
  }
  for (const section of SETTINGS_SECTIONS) items.push({ ...settingsSectionItem(section), kind: "settings" });
  for (const file of files) {
    if (!file?.path) continue;
    const relativePath = String(file.relativePath || file.path).replaceAll("\\", "/");
    items.push({
      id: `file:${file.path}`,
      kind: "file",
      path: file.path,
      group: "File",
      label: lastSegment(relativePath),
      description: parentOf(relativePath) || projectName,
    });
  }
  return items;
}
