import assert from "node:assert/strict";
import { test } from "node:test";
import { missingAppLine, starterHint, starterNeeds, starterOptionLabel, starterProviderChoice } from "./workflowStarters.js";

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

test("a starter's Provider choice waits for discovery and lists only installed apps that run model steps", () => {
  const starter = { providerApps: ["claude_code"] };
  const runnable = ["claude_code", "codex", "grok"];
  const app = (id, discoveryStatus, status = "ready", enabled = true) => ({ id, displayName: id.toUpperCase(), discoveryStatus, status, enabled });
  assert.deepEqual(
    starterProviderChoice(starter, [app("claude_code", "pending", "checking"), app("codex", "ready")], runnable, "codex"),
    { checking: true, missing: [], options: [], defaultId: "" },
  );
  const choice = starterProviderChoice(starter, [
    app("claude_code", "missing", "error", false),
    app("codex", "ready"),
    app("grok", "unauthenticated", "needs_sign_in"),
    app("cursor", "ready"),
  ], runnable, "grok");
  assert.deepEqual(choice.options.map(item => item.id), ["codex", "grok"], "Cursor isn't in providerApps here");
  assert.equal(choice.defaultId, "grok");
  assert.equal(missingAppLine(choice.missing[0]), "CLAUDE_CODE isn't installed on this computer.");
  assert.deepEqual(starterProviderChoice({ providerApps: [] }, [], runnable, "codex").options, []);
});
