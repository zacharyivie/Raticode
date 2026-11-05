import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_PLUGIN_SETTINGS,
  EDITOR_CONTENT,
  SIDEBAR_PLUGINS,
  initialMainContent,
  resolveMainContent,
  enabledPlugins,
  listedPlugins,
  movePlugin,
  normalizePluginOrder,
  normalizePluginSettings,
  pluginAvailable,
  pluginEnabled,
  resolvePluginActivity,
  stepPlugin,
  withPluginEnabled,
  withPluginPreview,
} from "./plugins.js";
import { normalizeAppSettings, updateSetting } from "./settings.js";
import { FEATURE_PREVIEWS } from "./featurePreview.js";

const ids = (plugins) => plugins.map((plugin) => plugin.id);

test("default plugins show everything except experimental ones", () => {
  const settings = normalizePluginSettings();
  assert.deepEqual(settings, { enabled: {}, locked: false, order: DEFAULT_PLUGIN_SETTINGS.order, preview: {} });
  assert.deepEqual(ids(enabledPlugins(settings)), ["rem", "workflows", "files", "search", "source-control"]);
  assert.deepEqual(ids(SIDEBAR_PLUGINS.filter((plugin) => plugin.experimental)), ["swarms", "organizations", "second-brain"]);
  for (const id of ["swarms", "organizations", "second-brain"]) assert.equal(pluginEnabled(settings, id), false);
  assert.equal(pluginEnabled(settings, "unknown"), false);
});

test("the file explorer cannot be turned off", () => {
  const settings = normalizePluginSettings({ enabled: { files: false, search: false }, preview: {} });
  assert.deepEqual(settings.enabled, { search: false });
  assert.equal(pluginEnabled(settings, "files"), true);
  assert.deepEqual(withPluginEnabled(settings, "files", false), { search: false });
  assert.equal(resolvePluginActivity(settings, "search"), "files");
  assert.equal(resolvePluginActivity(settings, "swarms"), "files");
  assert.equal(resolvePluginActivity({ enabled: {}, preview: { swarms: true } }, "swarms"), "swarms");
  assert.equal(resolvePluginActivity(settings, "rem"), "rem");
  assert.equal(resolvePluginActivity({ enabled: { rem: false } }, "rem"), "files");
});

test("only choices that differ from the default are stored", () => {
  const settings = normalizePluginSettings({});
  // Sidebar visibility defaults to shown; Feature Preview defaults to off.
  assert.deepEqual(withPluginEnabled(settings, "organizations", true), {});
  assert.deepEqual(withPluginEnabled(settings, "organizations", false), { organizations: false });
  assert.deepEqual(withPluginPreview(settings, "organizations", true), { organizations: true });
  assert.deepEqual(withPluginPreview({ preview: { organizations: true } }, "organizations", false), {});
  assert.deepEqual(withPluginPreview(settings, "search", true), {}, "only experimental plugins have a preview");
  assert.deepEqual(withPluginEnabled(settings, "search", false), { search: false });
  assert.deepEqual(withPluginEnabled({ enabled: { search: false } }, "search", true), {});
  assert.deepEqual(normalizePluginSettings({ enabled: { search: "no", stale: true }, preview: { search: true, rem: true, swarms: "yes" } }), normalizePluginSettings({ preview: {} }));
});

test("stored orders drop unknown plugins and restore missing ones near their defaults", () => {
  assert.deepEqual(
    normalizePluginOrder(["second-brain", "stale", "files", "files", "rem"]),
    ["second-brain", "files", "search", "source-control", "swarms", "organizations", "rem", "workflows"],
  );
  assert.deepEqual(normalizePluginOrder(["workflows"]), ["rem", "workflows", "files", "search", "source-control", "swarms", "organizations", "second-brain"]);
  assert.deepEqual(normalizePluginOrder("invalid"), DEFAULT_PLUGIN_SETTINGS.order);
});

test("plugins move before or after a target and step past hidden plugins", () => {
  const order = DEFAULT_PLUGIN_SETTINGS.order;
  assert.deepEqual(movePlugin(order, "second-brain", "rem"), ["second-brain", "rem", "workflows", "files", "search", "source-control", "swarms", "organizations"]);
  assert.deepEqual(movePlugin(order, "rem", "files", "after"), ["workflows", "files", "rem", "search", "source-control", "swarms", "organizations", "second-brain"]);
  assert.deepEqual(movePlugin(order, "rem", "workflows", "before"), order);
  assert.deepEqual(movePlugin(order, "rem", "rem"), order);
  assert.deepEqual(movePlugin(order, "rem", "missing"), order);
  const visible = ["rem", "files", "search"];
  assert.deepEqual(stepPlugin(order, "rem", 1, visible), ["workflows", "files", "rem", "search", "source-control", "swarms", "organizations", "second-brain"]);
  assert.deepEqual(stepPlugin(order, "rem", -1, visible), order);
  assert.deepEqual(stepPlugin(order, "search", 1, visible), order);
});

test("app settings keep plugin choices through updates", () => {
  let settings = normalizeAppSettings({});
  assert.deepEqual(settings.plugins, normalizePluginSettings());
  settings = updateSetting(settings, "plugins.locked", true);
  settings = updateSetting(settings, "plugins.enabled", { organizations: false, files: false, search: true });
  settings = updateSetting(settings, "plugins.preview", { organizations: true });
  settings = updateSetting(settings, "plugins.order", ["files", "rem"]);
  assert.equal(settings.plugins.locked, true);
  assert.deepEqual(settings.plugins.enabled, { organizations: false });
  assert.deepEqual(settings.plugins.preview, { organizations: true });
  assert.deepEqual(settings.plugins.order, ["files", "search", "source-control", "swarms", "organizations", "second-brain", "rem", "workflows"]);
  assert.deepEqual(normalizeAppSettings(JSON.parse(JSON.stringify(settings))).plugins, settings.plugins);
});

test("Feature Preview lists every experimental feature and hides the ones that are off", () => {
  assert.deepEqual(FEATURE_PREVIEWS.map((feature) => feature.id), ["swarms", "organizations", "second-brain", "device-network"]);
  assert.deepEqual(FEATURE_PREVIEWS.filter((feature) => feature.backend).map((feature) => feature.id), ["organizations", "device-network"]);
  const settings = normalizePluginSettings({ enabled: { swarms: false, search: false }, preview: { swarms: true } });
  // Plugins that are off in Feature Preview are not listed in sidebar settings.
  assert.deepEqual(ids(listedPlugins(settings)), ["rem", "workflows", "files", "search", "source-control", "swarms"]);
  // Hiding a plugin from the sidebar keeps its feature on and its settings row.
  assert.equal(pluginAvailable(settings, "swarms"), true);
  assert.equal(pluginEnabled(settings, "swarms"), false);
  assert.equal(pluginAvailable(settings, "organizations"), false);
});

test("older settings, which used one switch for both, become a Feature Preview choice", () => {
  const settings = normalizePluginSettings({ enabled: { swarms: true, organizations: false, search: false } });
  assert.deepEqual(settings.preview, { swarms: true });
  assert.deepEqual(settings.enabled, { search: false });
  assert.equal(pluginEnabled(settings, "swarms"), true);
  assert.equal(pluginAvailable(settings, "organizations"), false);
  // Once saved in the new shape, a hidden experimental plugin stays hidden but on.
  const saved = normalizePluginSettings({ enabled: { swarms: false }, preview: { swarms: true } });
  assert.equal(pluginAvailable(saved, "swarms"), true);
  assert.equal(pluginEnabled(saved, "swarms"), false);
});

test("Rem is a standard plugin and keeps the sidebar choice saved while it was a preview", () => {
  assert.equal(pluginAvailable(normalizePluginSettings(), "rem"), true);
  assert.deepEqual(withPluginPreview({ preview: {} }, "rem", true), {}, "Rem has no Feature Preview switch");
  // Rem turned on and shown, in the current and the older settings shape.
  for (const stored of [{ enabled: {}, preview: { rem: true } }, { enabled: { rem: true } }]) {
    const settings = normalizePluginSettings(stored);
    assert.deepEqual(settings.preview, {});
    assert.deepEqual(settings.enabled, {});
    assert.equal(pluginEnabled(settings, "rem"), true);
  }
  // Rem turned on but hidden from the sidebar stays hidden.
  const hidden = normalizePluginSettings({ enabled: { rem: false }, preview: { rem: true, swarms: true } });
  assert.deepEqual(hidden, { ...hidden, enabled: { rem: false }, preview: { swarms: true } });
  assert.equal(pluginAvailable(hidden, "rem"), true);
  assert.equal(pluginEnabled(hidden, "rem"), false);
  // Rem's preview was off: it now appears, and the other choices are kept.
  const off = normalizePluginSettings({ locked: true, order: ["files", "rem"], enabled: { search: false }, preview: { "second-brain": true } });
  assert.deepEqual(off.enabled, { search: false });
  assert.deepEqual(off.preview, { "second-brain": true });
  assert.equal(off.locked, true);
  assert.deepEqual(off.order, normalizePluginOrder(["files", "rem"]));
  assert.equal(pluginEnabled(off, "rem"), true);
});

test("the main content is chosen apart from the sidebar plugin", () => {
  const settings = normalizePluginSettings({ preview: { "second-brain": true, swarms: true } });
  assert.equal(resolveMainContent(settings, "rem"), "rem");
  assert.equal(resolveMainContent(settings, "swarms"), "swarms");
  // Plugins without their own workspace, unknown values, and plugins that are
  // off or hidden all show the editor.
  for (const content of ["source-control", "workflows", "files", "bogus", "organizations"]) assert.equal(resolveMainContent(settings, content), EDITOR_CONTENT);
  assert.equal(resolveMainContent(normalizePluginSettings({ enabled: { rem: false } }), "rem"), EDITOR_CONTENT);
  assert.equal(initialMainContent("second-brain"), "second-brain");
  assert.equal(initialMainContent("source-control"), EDITOR_CONTENT);
  assert.equal(initialMainContent("swarms"), EDITOR_CONTENT);
});
