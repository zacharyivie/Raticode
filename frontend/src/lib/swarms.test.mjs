import test from "node:test";
import assert from "node:assert/strict";
import { milestoneProgress, objectiveProgressChange, positiveDraft, swarmRequest } from "./swarms.js";

test("weighted progress counts accepted effort across objectives and excludes cancelled work", () => {
  const progress = milestoneProgress([
    { milestones: [{ weight: 1, status: "accepted" }, { weight: 3, status: "accepted" }] },
    { milestones: [{ weight: 1, status: "in_review" }, { weight: 50, status: "cancelled" }] },
  ]);
  assert.deepEqual(progress, { percent: 80, acceptedWeight: 4, totalWeight: 5, acceptedCount: 2, totalCount: 3 });
  assert.equal(milestoneProgress([]).percent, null);
});

test("numeric drafts retain valid fractions and reject cleared, invalid, or negative estimates at commit", () => {
  assert.equal(positiveDraft("", 3), 3);
  assert.equal(positiveDraft("  ", 3), 3);
  assert.equal(positiveDraft("8", 3), 8);
  assert.equal(positiveDraft("0.5", 3, 0.01), 0.5);
  assert.equal(positiveDraft("-2", 3), 3);
  assert.equal(positiveDraft("Infinity", 3), 3);
});

test("swarm mutations scope requests to their project and include the desktop grant", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  let captured;
  globalThis.window = { goferDesktop: { workspace: { pathGrantForApi: () => "project-grant" } } };
  globalThis.fetch = async (url, init) => { captured = { url, init }; return { ok: true, json: async () => ({ swarm: { id: "team" } }) }; };
  try {
    assert.deepEqual(await swarmRequest("/project with spaces", "/team/start", { method: "POST", task: "Build it" }), { swarm: { id: "team" } });
    assert.equal(captured.url, "/api/swarms/team/start?projectRoot=%2Fproject+with+spaces");
    assert.deepEqual(JSON.parse(captured.init.body), { task: "Build it", projectRoot: "/project with spaces", grantId: "project-grant" });
  } finally { globalThis.window = previousWindow; globalThis.fetch = previousFetch; }
});


test("progress history distinguishes objective edits from delivery resolution snapshots", () => {
  assert.equal(objectiveProgressChange({ kind: "delivery_resolved", payload: {
    before: { agentId: "builder", state: "uncertain" },
    after: { agentId: "builder", state: "dismissed" },
  } }), null);
  for (const payload of [undefined, {}, { before: {}, after: {} }, { before: [], after: null }]) {
    assert.equal(objectiveProgressChange({ kind: "objectives_updated", payload }), null);
  }
  assert.equal(objectiveProgressChange({ kind: "delivery_resolved", payload: { before: [], after: [] } }), null);
  const change = objectiveProgressChange({ kind: "objectives_updated", payload: {
    before: [], after: [{ milestones: [{ weight: 4, status: "accepted" }, { weight: 1, status: "planned" }] }],
  } });
  assert.equal(change.before.percent, null);
  assert.equal(change.before.totalWeight, 0);
  assert.equal(change.after.percent, 80);
  assert.equal(change.after.totalWeight, 5);
});
