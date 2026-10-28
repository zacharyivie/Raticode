// Plugins are the views in the left activity rail. Each entry's position here
// is its default order. `required` plugins cannot be turned off, and
// `experimental` plugins stay listed in Settings but are off until a user
// turns them on. Mark a plugin experimental by setting the flag here.
export const SIDEBAR_PLUGINS = Object.freeze([
  { id: "rem", label: "Rem", description: "Rem's home with threads, tasks, and reviews.", experimental: true },
  { id: "workflows", label: "Workflows", description: "Workflows in the current project." },
  { id: "files", label: "File explorer", description: "Browse, create, and organize project files.", required: true },
  { id: "search", label: "Search", description: "Find and replace text across the project." },
  { id: "source-control", label: "Source control", description: "Changes, branches, worktrees, and history." },
  { id: "swarms", label: "Swarms", description: "Agent teams working in the current project.", experimental: true },
  { id: "organizations", label: "Organizations", description: "Rem employees, teams, and their work queues.", experimental: true },
  { id: "second-brain", label: "Second Brain", description: "Notes, reports, and knowledge collections.", experimental: true },
].map(Object.freeze));

export const FALLBACK_PLUGIN_ID = "files";

const PLUGIN_IDS = SIDEBAR_PLUGINS.map((plugin) => plugin.id);

export const DEFAULT_PLUGIN_SETTINGS = Object.freeze({
  // Only choices the user made are stored, so a plugin that graduates from
  // experimental turns on for everyone who never turned it off.
  enabled: {},
  locked: false,
  order: PLUGIN_IDS,
});

export function sidebarPlugin(id) {
  return SIDEBAR_PLUGINS.find((plugin) => plugin.id === id) ?? null;
}

export function normalizePluginSettings(value = {}) {
  const enabled = {};
  const storedEnabled = value?.enabled && typeof value.enabled === "object" ? value.enabled : {};
  for (const plugin of SIDEBAR_PLUGINS) {
    if (!plugin.required && typeof storedEnabled[plugin.id] === "boolean") enabled[plugin.id] = storedEnabled[plugin.id];
  }
  return {
    enabled,
    locked: value?.locked === true,
    order: normalizePluginOrder(value?.order),
  };
}

// Keeps known plugins once each, in the stored order. Plugins missing from an
// older order are placed after the plugin that precedes them by default.
export function normalizePluginOrder(order) {
  const stored = Array.isArray(order) ? order : [];
  const next = [...new Set(stored.filter((id) => PLUGIN_IDS.includes(id)))];
  PLUGIN_IDS.forEach((id, index) => {
    if (next.includes(id)) return;
    const previous = PLUGIN_IDS.slice(0, index).reverse().find((candidate) => next.includes(candidate));
    next.splice(previous ? next.indexOf(previous) + 1 : 0, 0, id);
  });
  return next;
}

export function pluginEnabled(pluginSettings, id) {
  const plugin = sidebarPlugin(id);
  if (!plugin) return false;
  if (plugin.required) return true;
  const choice = pluginSettings?.enabled?.[id];
  return typeof choice === "boolean" ? choice : !plugin.experimental;
}

export function withPluginEnabled(pluginSettings, id, enabled) {
  const plugin = sidebarPlugin(id);
  const next = { ...pluginSettings?.enabled };
  if (!plugin || plugin.required) return next;
  if (enabled === !plugin.experimental) delete next[id];
  else next[id] = enabled;
  return next;
}

export function orderedPlugins(pluginSettings) {
  return normalizePluginOrder(pluginSettings?.order).map(sidebarPlugin);
}

export function enabledPlugins(pluginSettings) {
  return orderedPlugins(pluginSettings).filter((plugin) => pluginEnabled(pluginSettings, plugin.id));
}

// A turned-off plugin cannot own the sidebar, so its view falls back to files.
export function resolvePluginActivity(pluginSettings, activity, fallback = FALLBACK_PLUGIN_ID) {
  return pluginEnabled(pluginSettings, activity) ? activity : fallback;
}

// Moves `id` before or after `targetId`. Returns the original order when the
// move changes nothing so callers can skip saving.
export function movePlugin(order, id, targetId, placement = "before") {
  const current = normalizePluginOrder(order);
  if (id === targetId || !current.includes(id) || !current.includes(targetId)) return current;
  const next = current.filter((candidate) => candidate !== id);
  const targetIndex = next.indexOf(targetId);
  next.splice(placement === "after" ? targetIndex + 1 : targetIndex, 0, id);
  return next.every((candidate, index) => candidate === current[index]) ? current : next;
}

// Moves `id` one step among `visibleIds`, skipping plugins that are hidden.
export function stepPlugin(order, id, direction, visibleIds = normalizePluginOrder(order)) {
  const index = visibleIds.indexOf(id);
  const target = visibleIds[index + (direction < 0 ? -1 : 1)];
  if (index < 0 || !target) return normalizePluginOrder(order);
  return movePlugin(order, id, target, direction < 0 ? "before" : "after");
}

export const PLUGIN_DRAG_TYPE = "application/x-raticode-plugin";

export function dropPlacement(event) {
  const bounds = event.currentTarget.getBoundingClientRect();
  return event.clientY > bounds.top + bounds.height / 2 ? "after" : "before";
}
