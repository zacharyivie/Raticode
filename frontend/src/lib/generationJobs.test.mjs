import assert from "node:assert/strict";
import { test } from "node:test";
import { latestGenerationJob, startGenerationJob } from "./generationJobs.js";

function fixture(t, workspace, fetch) {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  globalThis.window = { goferDesktop: { workspace } };
  globalThis.fetch = fetch;
  t.after(() => { globalThis.window = previousWindow; globalThis.fetch = previousFetch; });
}

test("commit polling waits for startup registration and renews expired grants", async t => {
  let grant = "";
  let release;
  let fetches = 0;
  const workspace = {
    trustProjectRoot: async root => {
      assert.equal(root, "/project with spaces");
      await new Promise(resolve => { release = resolve; });
      grant = "registered";
    },
    pathGrantForApi: () => grant,
  };
  fixture(t, workspace, async url => {
    fetches++;
    const query = new URL(url, "http://localhost").searchParams;
    assert.equal(query.get("projectRoot"), "/project with spaces");
    assert.equal(query.get("branch"), "feature/search");
    assert.equal(query.get("grantId"), "registered");
    return { ok: true, json: async () => ({ jobs: [{ id: "draft" }] }) };
  });
  for (const cached of ["", "expired"]) {
    grant = cached;
    const before = fetches;
    const pending = latestGenerationJob("commit", "/project with spaces", "feature/search");
    assert.equal(fetches, before, "Never fetch with an unregistered or expired grant");
    release();
    assert.deepEqual(await pending, { id: "draft" });
    assert.equal(fetches, before + 1);
  }
});

test("deleted worktrees and missing ancestors skip commit polling", async t => {
  fixture(t, {}, () => assert.fail("A missing project must not request saved jobs"));
  for (const code of ["ENOENT", "ENOTDIR"]) {
    window.goferDesktop.workspace.trustProjectRoot = async () => { throw Object.assign(new Error("Missing project"), { code }); };
    assert.equal(await latestGenerationJob("commit", "/removed-worktree", "main"), null);
  }
  assert.equal(await latestGenerationJob("commit"), null);
});

test("commit polling preserves real access and backend failures", async t => {
  fixture(t, { trustProjectRoot: async () => { throw Object.assign(new Error("Access denied"), { code: "EACCES" }); } },
    () => assert.fail("Failed registration must not use a stale grant"));
  await assert.rejects(latestGenerationJob("commit", "/private", "main"), /Access denied/);
  window.goferDesktop.workspace.trustProjectRoot = async () => {};
  globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: "Job storage unavailable" }) });
  await assert.rejects(latestGenerationJob("commit", "/project", "main"), /Job storage unavailable/);
});

test("starting a commit job registers its project before submitting the request", async t => {
  let grant = "expired";
  fixture(t, {
    trustProjectRoot: async () => { grant = "renewed"; }, pathGrantForApi: () => grant,
  }, async (url, options) => {
    assert.equal(url, "/api/generation-jobs");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { kind: "commit", provider: "codex", projectRoot: "/project", branch: "main", grantId: "renewed" });
    return { ok: true, json: async () => ({ id: "started" }) };
  });
  assert.deepEqual(await startGenerationJob("commit", { projectRoot: "/project", branch: "main" }, { provider: "codex" }), { id: "started" });
});

test("theme jobs and browser-only polling work without desktop folder grants", async t => {
  let fetched = 0;
  fixture(t, undefined, async () => { fetched++; return { ok: true, json: async () => ({ jobs: [] }) }; });
  assert.equal(await latestGenerationJob("theme"), null);
  assert.equal(await latestGenerationJob("commit", "/project", "main"), null);
  await startGenerationJob("theme", { description: "Blueprint" }, { provider: "codex" });
  assert.equal(fetched, 3);
});
