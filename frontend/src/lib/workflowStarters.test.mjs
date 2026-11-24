import assert from "node:assert/strict";
import { test } from "node:test";
import { starterHint, starterNeeds, starterOptionLabel } from "./workflowStarters.js";

test("a starter says which provider and secrets it needs before you create it", () => {
  assert.equal(
    starterNeeds({ providers: ["claude-code"], secrets: ["ALERT_WEBHOOK_URL"] }),
    "Needs: the claude-code provider, the secret ALERT_WEBHOOK_URL when used.",
  );
  assert.equal(starterNeeds({ providers: [], secrets: [] }), "Needs: nothing beyond Raticode.");
  assert.equal(starterNeeds(null), "");
});

test("the Start from hint joins summary, needs and the test set", () => {
  assert.equal(
    starterHint({ summary: "Runs the tests.", providers: ["codex"], secrets: [], hasTests: true }),
    "Runs the tests. Needs: the codex provider. Comes with a test set.",
  );
  assert.match(starterHint(null), /^An empty workflow\./);
});

test("on Windows without Git for Windows, bash starters say they need it", () => {
  const starter = { name: "Daily report", providers: ["claude-code"], secrets: [], needsGitForWindows: true };
  assert.equal(starterOptionLabel(starter), "Daily report (Needs Git for Windows)");
  assert.equal(starterNeeds(starter), "Needs: Git for Windows, the claude-code provider.");
  assert.equal(starterOptionLabel({ ...starter, needsGitForWindows: false }), "Daily report");
  assert.equal(starterOptionLabel(null), "");
});
