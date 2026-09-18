import { pathKey } from "./workspacePaths.js";
import { matchesCommand } from "./settings.js";
export function projectFolderName(projectRoot) {
  const normalized = String(projectRoot ?? "").replaceAll("\\", "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Unregistered";
}

export function terminalDirectoryFromOsc(data) {
  const prefix = "P;Cwd=";
  if (typeof data !== "string" || !data.startsWith(prefix)) return "";
  const currentDirectory = data.slice(prefix.length);
  const containsControlCharacter = [...currentDirectory]
    .some((character) => character.charCodeAt(0) < 32);
  return currentDirectory && !containsControlCharacter ? currentDirectory : "";
}

export function terminalProjectGroupId(projectPath) {
  return `project:${pathKey(projectPath) || "Unregistered"}`;
}

export function terminalGroupName(number) {
  return `Group ${number}`;
}

function terminalGroupId(id) {
  return id?.startsWith("project:") ? terminalProjectGroupId(id.slice(8)) : id;
}

export function terminalTabGroupId(tab) {
  const projectPath = tab?.projectPath ?? tab?.cwd ?? "";
  return terminalGroupId(tab?.groupId) || terminalProjectGroupId(projectPath);
}

export function moveTerminalTabToGroup(tabs = [], key, groupId) {
  let changed = false;
  const next = (tabs ?? []).map((tab) => {
    if (tab.key !== key || terminalTabGroupId(tab) === terminalGroupId(groupId)) return tab;
    changed = true;
    return { ...tab, groupId };
  });
  return changed ? next : tabs;
}

export function terminalTabsAfterDeletingGroup(tabs = [], groupId) {
  return (tabs ?? []).filter((tab) => terminalTabGroupId(tab) !== terminalGroupId(groupId));
}

export function upsertTerminalGroupDefinition(groups = [], definition) {
  const index = groups.findIndex((group) => terminalGroupId(group.id) === terminalGroupId(definition.id));
  if (index < 0) return [...groups, definition];
  return groups.map((group) => (terminalGroupId(group.id) === terminalGroupId(definition.id) ? { ...group, ...definition } : group));
}

export function groupTerminalTabsByProject(tabs = [], definitions = []) {
  const groups = new Map();
  const definitionsById = new Map((definitions ?? []).map((group) => [terminalGroupId(group.id), group]));
  for (const definition of definitions ?? []) {
    if (!definition.keepEmpty) continue;
    groups.set(terminalGroupId(definition.id), {
      id: terminalGroupId(definition.id),
      items: [],
      name: definition.name,
      projectPath: definition.projectPath ?? "",
    });
  }
  for (const tab of tabs ?? []) {
    const projectPath = tab.projectPath ?? tab.cwd ?? "";
    const id = terminalTabGroupId(tab);
    const definition = definitionsById.get(id);
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        items: [],
        name: definition?.name || projectFolderName(projectPath),
        projectPath: definition?.projectPath ?? projectPath,
      });
    }
    groups.get(id).items.push(tab);
  }
  return [...groups.values()].sort((left, right) => (
    left.name.localeCompare(right.name) || left.projectPath.localeCompare(right.projectPath)
  ));
}

export function shouldCreateInitialTerminal(active, tabCount, alreadyCreated) {
  return active && tabCount === 0 && !alreadyCreated;
}

export function terminalClipboardShortcutAction(event) {
  if (
    event.type !== "keydown"
    || !event.ctrlKey
    || !event.shiftKey
    || event.metaKey
    || event.altKey
  ) {
    return null;
  }
  const key = event.key.toLowerCase();
  if (key === "c") return "copy";
  if (key === "v") return "paste";
  return null;
}

export async function copyTerminalSelection(
  terminal,
  clipboard = globalThis.navigator?.clipboard,
) {
  const selection = terminal.getSelection();
  if (!selection || typeof clipboard?.writeText !== "function") return false;
  await clipboard.writeText(selection);
  return true;
}

export function handleTerminalClipboardShortcut(event, terminal) {
  const action = terminalClipboardShortcutAction(event);
  if (action === "copy") {
    void copyTerminalSelection(terminal).catch(() => {});
    return false;
  }
  if (action === "paste") {
    // Returning false skips xterm's keydown handling while preserving the browser paste event.
    return false;
  }
  return null;
}

export function terminalWordEraseInput(event) {
  if (
    event.type !== "keydown"
    || !event.ctrlKey
    || event.metaKey
    || event.altKey
    || event.shiftKey
    || event.key !== "Backspace"
  ) {
    return null;
  }
  return "\x17";
}

export function isBottomPanelShortcut(event) {
  const backquoteKey = event.code === "Backquote" || event.key === "`";
  return backquoteKey && (event.ctrlKey || event.metaKey) && !event.altKey;
}

export function bottomPanelTabForShortcut(activeTab, hasExplicitPanelSelection) {
  return hasExplicitPanelSelection ? activeTab : "terminal";
}

export function createDisposableTerminalSession(bridge, options, callbacks = {}) {
  let disposed = false;
  let sessionId = "";

  const closeSilently = (id) => Promise.resolve()
    .then(() => bridge.close(id))
    .catch(() => {});

  const settled = Promise.resolve().then(() => (
    disposed ? null : bridge.create(options)
  )).then(async (session) => {
    if (!session) return null;
    if (disposed) {
      await closeSilently(session.id);
      return null;
    }
    sessionId = session.id;
    callbacks.onReady?.(session);
    return session;
  }).catch((error) => {
    if (!disposed) callbacks.onError?.(error);
    return null;
  });

  return {
    dispose() {
      disposed = true;
      const activeSessionId = sessionId;
      sessionId = "";
      if (activeSessionId) void closeSilently(activeSessionId);
    },
    settled,
  };
}

export function terminalWorkspaceShortcutAction(event, options = {}) {
  if (!options.active || options.renaming || event.repeat) return null;
  if (matchesCommand(event, options.settings, "terminal.new")) return "new";
  if (matchesCommand(event, options.settings, "terminal.close") && options.activeKey) return "close";
  return null;
}

export function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}
