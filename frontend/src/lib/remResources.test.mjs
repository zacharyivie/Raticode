import assert from "node:assert/strict";
import test from "node:test";
import {
  brainUsable,
  DEFAULT_REM_RESOURCES,
  disabledBrainIds,
  remToolSummary,
  selectThreadBrains,
  snapshotRemResources,
} from "./remResources.js";

test("built-in defaults turn on subagents and leave the browser off", () => {
  assert.equal(DEFAULT_REM_RESOURCES.agents, true);
  assert.equal(DEFAULT_REM_RESOURCES.browser, false);
  assert.equal(remToolSummary(), "Commands · Web · Subagents");
});

test("older selections gain the new switches without losing explicit choices", () => {
  const snapshot = snapshotRemResources({ shell: false, web: false });
  assert.deepEqual(snapshot, { shell: false, web: false, agents: true, browser: false, skills: [], mcpServers: [] });
  assert.equal(remToolSummary({ shell: false, web: false, agents: false, browser: true }), "Browser");
  assert.equal(remToolSummary({ shell: false, web: false, agents: false }), "No tools");
});

const brain = (id, { allowCloud = true, state = "available", root = `/brains/${id}` } = {}) => ({ id, root, state, settings: { allowCloud } });
const school = brain("school");
const work = brain("work");
const legacy = { enabled: true, root: "/brains/legacy" };
const ids = (selection) => selection.brains.map((item) => item.id);

test("Settings > Memory turns every thread brain off", () => {
  const selection = selectThreadBrains({}, [school, work], { ...legacy, enabled: false });
  assert.deepEqual(selection, { enabled: false, brains: [], served: [] });
});

test("threads that never chose brains use every brain", () => {
  assert.deepEqual(ids(selectThreadBrains({}, [school, work], legacy)), ["school", "work"]);
});

test("brains turned off stay off while new brains start on", () => {
  const resources = { disabledSecondBrains: ["work"] };
  assert.deepEqual(ids(selectThreadBrains(resources, [school, work], legacy)), ["school"]);
  assert.deepEqual(ids(selectThreadBrains(resources, [school, work, brain("hobby")], legacy)), ["school", "hobby"]);
});

test("turning every brain off disables Second Brain for the thread", () => {
  const selection = selectThreadBrains({ disabledSecondBrains: ["school", "work"] }, [school, work], legacy);
  assert.equal(selection.enabled, false);
});

test("older empty selections keep every brain off", () => {
  // Older threads stored this list only after a choice, so empty meant all turned off.
  assert.deepEqual(disabledBrainIds({ secondBrains: [] }, [school, work]), ["school", "work"]);
  assert.equal(selectThreadBrains({ secondBrains: [] }, [school, work], legacy).enabled, false);
});

test("older brain choices keep the brains that were not chosen off", () => {
  assert.deepEqual(disabledBrainIds({ secondBrains: ["school"] }, [school, work]), ["work"]);
  assert.deepEqual(disabledBrainIds({ secondBrains: ["school"], disabledSecondBrains: [] }, [school, work]), []);
});

test("unavailable and cloud-restricted brains are skipped", () => {
  const brains = [school, brain("moved", { state: "missing" }), brain("private", { allowCloud: false })];
  assert.deepEqual(ids(selectThreadBrains({}, brains, legacy)), ["school"]);
  assert.equal(brainUsable(brains[2], legacy.root), false);
});

test("the Settings > Memory folder works by path without cloud permission", () => {
  const folder = brain("legacy", { allowCloud: false, root: legacy.root });
  assert.equal(brainUsable(folder, legacy.root), true);
  assert.deepEqual(selectThreadBrains({}, [folder], legacy), { enabled: true, brains: [], served: ["legacy"] });
  assert.equal(selectThreadBrains({ disabledSecondBrains: ["legacy"] }, [folder], legacy).enabled, false);
});

test("the Memory folder without cloud permission is not reported as served beside cloud brains", () => {
  const folder = brain("legacy", { allowCloud: false, root: legacy.root });
  const selection = selectThreadBrains({}, [folder, school], legacy);
  assert.deepEqual(ids(selection), ["school"]);
  assert.deepEqual(selection.served, ["school"]);
  assert.deepEqual(selectThreadBrains({ disabledSecondBrains: ["school"] }, [folder, school], legacy).served, ["legacy"]);
});

test("an empty library falls back to the Settings > Memory folder", () => {
  assert.deepEqual(selectThreadBrains({}, [], legacy), { enabled: true, brains: [], served: [] });
  assert.equal(selectThreadBrains({}, [], { enabled: true, root: "" }).enabled, false);
});
