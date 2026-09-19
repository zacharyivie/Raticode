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

test("overview distinguishes completion, unresolved work, idle recovery and provider errors", async () => {
  const { swarmOverview } = await import("./swarms.js");
  const agents = [{ id: "lead", name: "Lead" }];
  const run = { state: "running", objectives: [{ milestones: [{ id: "m", title: "Ship", status: "working" }] }], agentStates: { lead: { state: "idle" } } };
  assert.equal(swarmOverview(run, agents).stalled, true);
  run.agentStates.lead = { state: "retry_wait", error: "Provider rate limit", retryAt: 123 };
  assert.equal(swarmOverview(run, agents).issues[0].body, "Provider rate limit");
  assert.equal(swarmOverview(run, agents).stalled, false);
  run.state = "completed";
  assert.equal(swarmOverview(run, agents).success, false);
  run.objectives[0].milestones[0].status = "accepted";
  run.agentStates.lead = { state: "idle" };
  assert.equal(swarmOverview(run, agents).success, true);
  run.idleDiagnosis = { state: "failed", error: "Prompt error" };
  assert.equal(swarmOverview(run, agents).issues[0].body, "Prompt error");
  assert.equal(swarmOverview({ state: "completed", objectives: [] }, agents).success, false);
});

test("swarm requests include separate grants for selected agent repositories", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  let body;
  globalThis.window = { goferDesktop: { workspace: { pathGrantForApi: path => `grant:${path}` } } };
  globalThis.fetch = async (url, init) => { body = JSON.parse(init.body); return { ok: true, json: async () => ({}) }; };
  try {
    await swarmRequest("/desktop", "/team/start", { method: "POST", task: "Apps", workspacePaths: ["/mobile"] });
    assert.equal(body.projectRoot, "/desktop");
    assert.equal(body.grantId, "grant:/desktop");
    assert.deepEqual(body.workspaceGrants, { "/mobile": "grant:/mobile" });
    assert.equal(body.workspacePaths, undefined);
  } finally { globalThis.window = previousWindow; globalThis.fetch = previousFetch; }
});
