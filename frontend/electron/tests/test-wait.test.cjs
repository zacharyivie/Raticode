const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromiumForTesting, waitFor } = require("./test-wait.cjs");

function playwrightCache(t, builds) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "test-wait-playwright-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const [build, folder] of Object.entries(builds)) {
    fs.mkdirSync(path.join(root, build, folder ?? "empty"), { recursive: true });
    if (folder) fs.writeFileSync(path.join(root, build, folder, "chrome"), "");
  }
  return root;
}

test("chromiumForTesting picks the newest build by number, in either folder layout", (t) => {
  const root = playwrightCache(t, { "chromium-999": "chrome-linux64", "chromium-1248": "chrome-linux", "chromium_headless_shell-2000": "chrome-linux64", "ffmpeg-3000": "chrome-linux64" });
  assert.equal(chromiumForTesting(root), path.join(root, "chromium-1248", "chrome-linux", "chrome"));
});

test("chromiumForTesting prefers chrome-linux64 and skips a build without a browser", (t) => {
  const both = playwrightCache(t, { "chromium-1300": null, "chromium-1234": "chrome-linux" });
  fs.mkdirSync(path.join(both, "chromium-1234", "chrome-linux64"));
  fs.writeFileSync(path.join(both, "chromium-1234", "chrome-linux64", "chrome"), "");
  assert.equal(chromiumForTesting(both), path.join(both, "chromium-1234", "chrome-linux64", "chrome"));
});

test("chromiumForTesting returns an empty path without Playwright downloads", (t) => {
  const root = playwrightCache(t, { "chromium-1248": null });
  assert.equal(chromiumForTesting(root), "");
  assert.equal(chromiumForTesting(path.join(root, "missing")), "");
});

test("waitFor returns the first truthy value, checking at the given interval", async () => {
  let calls = 0;
  const started = Date.now();
  const value = await waitFor(() => (++calls === 3 ? { done: true } : 0), "never", { timeout: 5000, interval: 40 });
  assert.deepEqual(value, { done: true });
  assert.equal(calls, 3);
  assert.ok(Date.now() - started >= 70, "two waits of 40 ms passed between the checks");
});

test("waitFor fails with the message when the time runs out", async () => {
  await assert.rejects(waitFor(async () => false, "nothing happened", { timeout: 60, interval: 10 }), { message: "nothing happened" });
});

test("waitFor stops at a check that throws unless told to retry", async () => {
  let calls = 0;
  const failing = () => { calls += 1; throw new Error("not ready"); };
  await assert.rejects(waitFor(failing, "unused", { timeout: 1000, interval: 10 }), { message: "not ready" });
  assert.equal(calls, 1);

  calls = 0;
  await assert.rejects(waitFor(failing, "gave up", { timeout: 60, interval: 10, retryErrors: true }), { message: "gave up (not ready)" });
  assert.ok(calls > 1, "the throwing check was tried again");
  let tries = 0;
  assert.equal(await waitFor(() => { if (++tries < 3) throw new Error("not yet"); return "ok"; }, "unused", { interval: 10, retryErrors: true }), "ok");
});
