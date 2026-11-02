// Plugins are the views in the left activity rail. Each entry's position here
// is its default order. `required` plugins are always shown. `experimental`
// plugins exist only once turned on in Settings > Developer > Feature Preview;
// while off they appear nowhere else in the app. Showing or hiding a plugin in
// the sidebar is a separate choice. Mark a plugin experimental here.
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
  // Only choices the user made are stored. `enabled` holds sidebar
  // visibility, which defaults to shown; `preview` holds the Feature Preview
  // choices, which default to off. A plugin that graduates from experimental
  // turns on for everyone.
  enabled: {},
  locked: false,
  order: PLUGIN_IDS,
  preview: {},
});

export function sidebarPlugin(id) {
  return SIDEBAR_PLUGINS.find((plugin) => plugin.id === id) ?? null;
}

export function normalizePluginSettings(value = {}) {
  const enabled = {};
  const preview = {};
  const storedEnabled = value?.enabled && typeof value.enabled === "object" ? value.enabled : {};
  const storedPreview = value?.preview && typeof value.preview === "object" ? value.preview : null;
  for (const plugin of SIDEBAR_PLUGINS) {
    if (plugin.required) continue;
    const shown = storedEnabled[plugin.id];
    if (plugin.experimental && !storedPreview) {
      // Older settings used one switch for both choices: on meant the
      // feature was turned on and shown.
      if (shown === true) preview[plugin.id] = true;
      continue;
    }
    if (shown === false) enabled[plugin.id] = false;
    if (plugin.experimental && storedPreview[plugin.id] === true) preview[plugin.id] = true;
  }
  return {
    enabled,
    locked: value?.locked === true,
    order: normalizePluginOrder(value?.order),
    preview,
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

// Whether a plugin exists in the app at all. Experimental plugins need their
// Feature Preview switch.
export function pluginAvailable(pluginSettings, id) {
  const plugin = sidebarPlugin(id);
  if (!plugin) return false;
  return !plugin.experimental || pluginSettings?.preview?.[id] === true;
}

// Whether a plugin appears in the sidebar.
export function pluginEnabled(pluginSettings, id) {
  const plugin = sidebarPlugin(id);
  if (!pluginAvailable(pluginSettings, id)) return false;
  if (plugin.required) return true;
  return pluginSettings?.enabled?.[id] !== false;
}

export function withPluginEnabled(pluginSettings, id, enabled) {
  const plugin = sidebarPlugin(id);
  const next = { ...pluginSettings?.enabled };
  if (!plugin || plugin.required) return next;
  if (enabled) delete next[id];
  else next[id] = false;
  return next;
}

export function withPluginPreview(pluginSettings, id, enabled) {
  const next = { ...pluginSettings?.preview };
  if (!sidebarPlugin(id)?.experimental) return next;
  if (enabled) next[id] = true;
  else delete next[id];
  return next;
}

// Plugins listed in sidebar settings: everything except experimental plugins
// whose Feature Preview is off.
export function listedPlugins(pluginSettings) {
  return orderedPlugins(pluginSettings).filter((plugin) => pluginAvailable(pluginSettings, plugin.id));
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
