import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_PLUGIN_SETTINGS,
  SIDEBAR_PLUGINS,
  enabledPlugins,
  movePlugin,
  normalizePluginOrder,
  normalizePluginSettings,
  pluginEnabled,
  resolvePluginActivity,
  stepPlugin,
  withPluginEnabled,
} from "./plugins.js";
import { normalizeAppSettings, updateSetting } from "./settings.js";

const ids = (plugins) => plugins.map((plugin) => plugin.id);

test("default plugins show everything except experimental ones", () => {
  const settings = normalizePluginSettings();
  assert.deepEqual(settings, { enabled: {}, locked: false, order: DEFAULT_PLUGIN_SETTINGS.order });
  assert.deepEqual(ids(enabledPlugins(settings)), ["workflows", "files", "search", "source-control"]);
  assert.deepEqual(ids(SIDEBAR_PLUGINS.filter((plugin) => plugin.experimental)), ["rem", "swarms", "organizations", "second-brain"]);
  for (const id of ["rem", "swarms", "organizations", "second-brain"]) assert.equal(pluginEnabled(settings, id), false);
  assert.equal(pluginEnabled(settings, "unknown"), false);
});

test("the file explorer cannot be turned off", () => {
  const settings = normalizePluginSettings({ enabled: { files: false, search: false } });
  assert.deepEqual(settings.enabled, { search: false });
  assert.equal(pluginEnabled(settings, "files"), true);
  assert.deepEqual(withPluginEnabled(settings, "files", false), { search: false });
  assert.equal(resolvePluginActivity(settings, "search"), "files");
  assert.equal(resolvePluginActivity(settings, "rem"), "files");
  assert.equal(resolvePluginActivity({ enabled: { rem: true } }, "rem"), "rem");
});

test("only choices that differ from the default are stored", () => {
  const settings = normalizePluginSettings({});
  assert.deepEqual(withPluginEnabled(settings, "organizations", true), { organizations: true });
  assert.deepEqual(withPluginEnabled({ enabled: { organizations: true } }, "organizations", false), {});
  assert.deepEqual(withPluginEnabled(settings, "search", false), { search: false });
  assert.deepEqual(withPluginEnabled({ enabled: { search: false } }, "search", true), {});
  assert.deepEqual(normalizePluginSettings({ enabled: { search: "no", stale: true } }).enabled, {});
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
  settings = updateSetting(settings, "plugins.enabled", { organizations: true, files: false });
  settings = updateSetting(settings, "plugins.order", ["files", "rem"]);
  assert.equal(settings.plugins.locked, true);
  assert.deepEqual(settings.plugins.enabled, { organizations: true });
  assert.deepEqual(settings.plugins.order, ["files", "search", "source-control", "swarms", "organizations", "second-brain", "rem", "workflows"]);
  assert.deepEqual(normalizeAppSettings(JSON.parse(JSON.stringify(settings))).plugins, settings.plugins);
});
