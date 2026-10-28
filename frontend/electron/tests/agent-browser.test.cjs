const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  CLOSE_IDLE_MS,
  LEGACY_CONNECTION_FILE,
  MAX_AGENT_TABS,
  THROTTLE_IDLE_MS,
  agentUrl,
  createAgentBrowserController,
  createCapabilityRegistry,
  removeLegacyAgentBrowserConnection,
  remNeedsApproval,
  startAgentBrowserService,
  uploadPaths,
} = require("../agent-browser.cjs");
const { normalizeBrowserUrl } = require("../browser-utils.cjs");

const AGENT = "agent-0123456789";

test("agent navigation accepts web pages only", () => {
  assert.equal(agentUrl("example.com", normalizeBrowserUrl), "https://example.com/");
  assert.equal(agentUrl("localhost:5173/app", normalizeBrowserUrl), "http://localhost:5173/app");
  assert.equal(agentUrl("about:blank", normalizeBrowserUrl), "about:blank");
  for (const url of ["/etc/passwd", "file:///etc/passwd", "raticode://home", "javascript:alert(1)"]) {
    assert.throws(() => agentUrl(url, normalizeBrowserUrl), /http and https|http:\/\//, url);
  }
  assert.throws(() => agentUrl(" ", normalizeBrowserUrl), /Enter a URL/);
});

test("agent navigation follows the user's site lists", () => {
  const settings = { remAllowedSites: ["example.com"], remBlockedSites: ["bank.example.com"] };
  assert.equal(agentUrl("https://docs.example.com/a", normalizeBrowserUrl, settings), "https://docs.example.com/a");
  assert.throws(() => agentUrl("https://other.test/", normalizeBrowserUrl, settings), /may only open example\.com/);
  assert.throws(() => agentUrl("https://bank.example.com/", normalizeBrowserUrl, settings), /blocked Rem from bank\.example\.com/);
});

test("approval is required for consequential actions on shared sign-ins", () => {
  const session = { agent: { shared: false } };
  const settings = { remConfirm: "signed-in", remSignInSites: ["github.com"] };
  assert.equal(remNeedsApproval(settings, "https://github.com/settings", session, true), true);
  assert.equal(remNeedsApproval(settings, "https://github.com/settings", session, false), false);
  assert.equal(remNeedsApproval(settings, "https://example.com/", session, true), false);
  assert.equal(remNeedsApproval(settings, "https://example.com/", { agent: { shared: true } }, true), true);
  assert.equal(remNeedsApproval({ ...settings, remConfirm: "always" }, "https://example.com/", session, true), true);
  assert.equal(remNeedsApproval({ ...settings, remConfirm: "never" }, "https://github.com/", { agent: { shared: true } }, true), false);
});

test("capabilities are owner-bound, expire and can be revoked", () => {
  let time = 1000;
  const registry = createCapabilityRegistry({ now: () => time });
  assert.throws(() => registry.grant({ owner: "short" }), /identity/);
  const first = registry.grant({ owner: AGENT, label: "  Rem  ", ttlMs: 5000, uploadRoots: ["/work", "relative"] });
  assert.match(first.capability, /^[0-9a-f]{64}$/);
  assert.deepEqual(registry.resolve(first.capability), { owner: AGENT, label: "Rem", uploadRoots: ["/work"], expiresAt: 6000 });
  assert.equal(registry.resolve("f".repeat(64)), null);
  assert.equal(registry.resolve("not-a-token"), null);
  assert.deepEqual([...registry.activeOwners()], [AGENT]);
  time = 6000;
  assert.equal(registry.resolve(first.capability), null);
  const second = registry.grant({ owner: AGENT });
  assert.equal(registry.revoke(second.capability), true);
  assert.equal(registry.resolve(second.capability), null);
  const third = registry.grant({ owner: AGENT });
  registry.revokeOwner(AGENT);
  assert.equal(registry.resolve(third.capability), null);
  const fourth = registry.grant({ owner: AGENT });
  registry.revokeAll();
  assert.equal(registry.resolve(fourth.capability), null);
});

test("browser service separates admin and capability routes", async () => {
  const calls = [];
  const service = await startAgentBrowserService(async (request) => {
    calls.push(request);
    if (request.action === "fail") throw new Error("Tab t1 was closed.");
    return { ok: true };
  });
  try {
    const post = (route, token, body, headers = {}) => fetch(`${service.url}${route}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, ...headers },
      body: JSON.stringify(body),
    });
    assert.match(service.adminToken, /^[0-9a-f]{64}$/);
    // Rem cannot use the admin route, and the admin token is not a capability.
    assert.equal((await post("", service.adminToken, { action: "tabs" })).status, 403);
    assert.equal((await post("/admin", "f".repeat(64), { action: "grant", owner: AGENT })).status, 403);
    const granted = await (await post("/admin", service.adminToken, { action: "grant", owner: AGENT, ttlSeconds: 60, uploadRoots: ["/project"] })).json();
    assert.match(granted.capability, /^[0-9a-f]{64}$/);
    // Identity comes from the capability, never from the request body.
    assert.equal((await post("", granted.capability, { action: "tabs", agent: "agent-intruder-0000", uploadRoots: ["/"] })).status, 200);
    assert.deepEqual(calls.at(-1), { action: "tabs", agent: AGENT, label: "Rem", uploadRoots: ["/project"] });
    assert.equal((await post("", granted.capability, { action: "tabs" }, { Origin: "https://example.com" })).status, 403);
    const failure = await post("", granted.capability, { action: "fail" });
    assert.equal(failure.status, 400);
    assert.deepEqual(await failure.json(), { error: "Tab t1 was closed." });
    assert.deepEqual(await (await post("/admin", service.adminToken, { action: "revoke", capability: granted.capability })).json(), { revoked: true });
    const revoked = await post("", granted.capability, { action: "tabs" });
    assert.equal(revoked.status, 403);
    assert.match((await revoked.json()).error, /access ended/);
  } finally {
    service.close();
  }
});

test("browser service rejects requests for another host name", async () => {
  const service = await startAgentBrowserService(async () => ({ ok: true }));
  try {
    const port = new URL(service.url).port;
    const http = require("node:http");
    const status = await new Promise((resolve, reject) => {
      const request = http.request({ host: "127.0.0.1", port, path: "/browser/admin", method: "POST",
        headers: { Host: `rebind.example:${port}`, Authorization: `Bearer ${service.adminToken}` } }, (response) => {
        response.resume();
        resolve(response.statusCode);
      });
      request.on("error", reject);
      request.end(JSON.stringify({ action: "grant", owner: AGENT }));
    });
    assert.equal(status, 403);
  } finally {
    service.close();
  }
});

test("the legacy connection file is removed", () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-browser-"));
  const legacy = path.join(dataDir, LEGACY_CONNECTION_FILE);
  fs.writeFileSync(legacy, JSON.stringify({ token: "a".repeat(64) }));
  removeLegacyAgentBrowserConnection(dataDir);
  assert.equal(fs.existsSync(legacy), false);
  removeLegacyAgentBrowserConnection(dataDir);
});

test("agents only reach their own tabs", async () => {
  const tabs = [
    { id: "mine", agent: { id: AGENT }, contents: null, agentUsedAt: 1 },
    { id: "theirs", agent: { id: "agent-other-0000" }, contents: null },
  ];
  const controller = createAgentBrowserController({
    openTab: () => { throw new Error("unused"); },
    agentTabs: (agent) => tabs.filter((tab) => tab.agent.id === agent),
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
  });
  await assert.rejects(controller.handle({ agent: "short", action: "tabs" }), /identity/);
  await assert.rejects(controller.handle({ agent: AGENT, action: "open", url: "file:///etc/passwd" }), /http and https/);
  const listed = await controller.handle({ agent: AGENT, action: "tabs" });
  assert.deepEqual(listed.tabs.map((tab) => tab.tab), ["mine"]);
  await assert.rejects(
    controller.handle({ agent: AGENT, action: "snapshot", tab: "theirs" }),
    /belongs to another task/,
  );
  // A closed view reports that the tab is gone instead of acting elsewhere.
  await assert.rejects(controller.handle({ agent: AGENT, action: "snapshot" }), /was closed/);
  await assert.rejects(
    controller.handle({ agent: "agent-nobody-0000", action: "snapshot" }),
    /browser_open first/,
  );
});

test("paused tabs refuse Rem's actions but can still close", async () => {
  const closed = [];
  const tab = { id: "paused", agent: { id: AGENT }, agentPaused: true, contents: null };
  const controller = createAgentBrowserController({
    openTab: () => { throw new Error("unused"); },
    agentTabs: () => [tab],
    closeTab: (item) => closed.push(item.id),
    normalizeUrl: normalizeBrowserUrl,
  });
  await assert.rejects(controller.handle({ agent: AGENT, action: "snapshot" }), /paused Rem/);
  assert.deepEqual(await controller.handle({ agent: AGENT, action: "close" }), { closed: "paused" });
  assert.deepEqual(closed, ["paused"]);
});

test("Rem may keep a limited number of its own tabs", async () => {
  const tabs = Array.from({ length: MAX_AGENT_TABS }, (_value, index) => ({ id: `t${index}`, agent: { id: AGENT } }));
  // Tabs the user shared do not count against the limit.
  tabs.push({ id: "shared", agent: { id: AGENT, shared: true } });
  const controller = createAgentBrowserController({
    openTab: () => { throw new Error("should not open"); },
    agentTabs: () => tabs,
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
  });
  await assert.rejects(controller.handle({ agent: AGENT, action: "open" }), new RegExp(`${MAX_AGENT_TABS} tabs open`));
});

test("idle Rem tabs are throttled, then closed, but shared tabs stay", () => {
  let time = 0;
  const throttled = [];
  const closed = [];
  const contents = (id) => ({ isDestroyed: () => false, setBackgroundThrottling: (value) => throttled.push([id, value]) });
  const tabs = [
    { id: "recent", agent: { id: AGENT }, agentUsedAt: 0, contents: contents("recent") },
    { id: "old", agent: { id: AGENT }, agentUsedAt: 0, contents: contents("old") },
    { id: "shared", agent: { id: AGENT, shared: true }, agentUsedAt: 0, contents: contents("shared") },
  ];
  const controller = createAgentBrowserController({
    openTab: () => null,
    agentTabs: () => tabs,
    allAgentTabs: () => tabs,
    closeTab: (tab) => closed.push(tab.id),
    normalizeUrl: normalizeBrowserUrl,
    now: () => time,
  });
  time = THROTTLE_IDLE_MS;
  controller.sweep();
  assert.deepEqual(throttled, [["recent", true], ["old", true], ["shared", true]]);
  controller.sweep();
  assert.equal(throttled.length, 3);
  tabs[0].agentUsedAt = CLOSE_IDLE_MS - 1;
  time = CLOSE_IDLE_MS;
  controller.sweep();
  assert.deepEqual(closed, ["old"]);
});

test("evaluate can be turned off", async () => {
  const tab = { id: "t", agent: { id: AGENT }, contents: {
    isDestroyed: () => false,
    setBackgroundThrottling: () => {},
    debugger: { isAttached: () => true, sendCommand: async () => ({}) },
  } };
  const controller = createAgentBrowserController({
    openTab: () => null,
    agentTabs: () => [tab],
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
    policy: () => ({ remEvaluate: false }),
  });
  await assert.rejects(controller.handle({ agent: AGENT, action: "evaluate", expression: "1" }), /turned off in Settings > Browser/);
});

test("declined approvals stop Rem's script", async () => {
  const commands = [];
  const tab = { id: "t", agent: { id: AGENT, shared: true }, contents: {
    isDestroyed: () => false,
    getURL: () => "https://mail.example.com/",
    setBackgroundThrottling: () => {},
    debugger: { isAttached: () => true, sendCommand: async (method) => { commands.push(method); return {}; } },
  } };
  const requests = [];
  const controller = createAgentBrowserController({
    openTab: () => null,
    agentTabs: () => [tab],
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
    policy: () => ({ remConfirm: "signed-in", remSignInSites: [] }),
    confirm: async (_session, details) => { requests.push(details); return false; },
  });
  await assert.rejects(controller.handle({ agent: AGENT, action: "evaluate", expression: "document.forms[0].submit()" }), /did not approve/);
  assert.equal(requests[0].label, "Run a script on this page");
  assert.equal(commands.includes("Runtime.evaluate"), false);
});

test("full access lets Rem act without approval prompts, even in shared tabs", async () => {
  const commands = [];
  const tab = { id: "t", agent: { id: AGENT, shared: true }, contents: {
    isDestroyed: () => false,
    getURL: () => "https://mail.example.com/",
    getTitle: () => "Mail",
    isLoading: () => false,
    setBackgroundThrottling: () => {},
    debugger: { isAttached: () => true, sendCommand: async (method) => { commands.push(method); return { result: { value: true } }; } },
  } };
  let asked = 0;
  const controller = createAgentBrowserController({
    openTab: () => null,
    agentTabs: () => [tab],
    closeTab: () => {},
    normalizeUrl: normalizeBrowserUrl,
    policy: () => ({ remConfirm: "never", remSignInSites: ["mail.example.com"] }),
    confirm: async () => { asked += 1; return false; },
  });
  assert.deepEqual(await controller.handle({ agent: AGENT, action: "evaluate", expression: "true" }), { tab: "t", result: "true" });
  assert.equal(asked, 0);
  assert.equal(commands.includes("Runtime.evaluate"), true);
});

test("uploads are limited to the task's folders", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-upload-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "agent-outside-"));
  const file = path.join(root, "report.pdf");
  fs.writeFileSync(file, "pdf");
  fs.mkdirSync(path.join(root, "folder"));
  fs.writeFileSync(path.join(outside, "secret.txt"), "secret");
  fs.symlinkSync(path.join(outside, "secret.txt"), path.join(root, "link.txt"));
  assert.deepEqual(uploadPaths([file], [root]), [fs.realpathSync(file)]);
  assert.throws(() => uploadPaths([], [root]), /Pass paths/);
  assert.throws(() => uploadPaths(["report.pdf"], [root]), /absolute/);
  assert.throws(() => uploadPaths([path.join(outside, "secret.txt")], [root]), /outside this task/);
  // Links cannot escape the project.
  assert.throws(() => uploadPaths([path.join(root, "link.txt")], [root]), /outside this task/);
  assert.throws(() => uploadPaths([path.join(root, "folder")], [root]), /not a file/);
  assert.throws(() => uploadPaths([path.join(root, "missing")], [root]), /not found/);
  assert.throws(() => uploadPaths([file], []), /no project folder/);
});

test("an agent tab that never opens is cleaned up", async () => {
  const closed = [];
  const session = { id: "pending", agent: { id: AGENT }, contents: null, closed: false };
  const controller = createAgentBrowserController({
    openTab: () => { setTimeout(() => { session.closed = true; }, 20); return session; },
    agentTabs: () => [session],
    closeTab: (item) => closed.push(item.id),
    normalizeUrl: normalizeBrowserUrl,
  });
  await assert.rejects(controller.handle({ agent: AGENT, action: "open" }), /closed before it opened/);
  assert.deepEqual(closed, ["pending"]);
});
