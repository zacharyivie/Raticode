import assert from "node:assert/strict";
import test from "node:test";
import { apiUrl, ensureGoferApiToken, installGoferApiFetchAuth, withGoferApiAuth } from "./api.js";

function browser(overrides = {}) {
  globalThis.window = {
    location: { href: "http://127.0.0.1:5173/", pathname: "/", search: "", hash: "" },
    goferApiBaseUrl: "http://127.0.0.1:8765/api",
    ...overrides,
  };
  return window;
}

test("API reads bootstrap through desktop IPC and never a public session endpoint", async () => {
  let ipcCalls = 0;
  const calls = [];
  browser({
    goferDesktop: { apiSession: async () => { ipcCalls += 1; return { apiToken: "private-token" }; } },
    fetch: async (url, init) => { calls.push({ url, init }); return new Response("{}", { status: 200 }); },
  });
  installGoferApiFetchAuth();
  await Promise.all([
    window.fetch("http://127.0.0.1:8765/api/workflows"),
    window.fetch("http://127.0.0.1:8765/api/doctor"),
  ]);
  assert.equal(ipcCalls, 1);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => !url.endsWith("/session")));
  assert.ok(calls.every(({ init }) => init.headers.get("Authorization") === "Bearer private-token"));
});

test("desktop backend restart renews token before an opaque-origin request", async () => {
  const tokens = [];
  browser({
    goferApiToken: "old-token",
    goferDesktop: { apiSession: async () => ({ apiToken: "new-token" }) },
    fetch: async (_url, init) => {
      const token = init.headers.get("Authorization");
      tokens.push(token);
      return new Response("{}", { status: token === "Bearer old-token" ? 401 : 200 });
    },
  });
  installGoferApiFetchAuth();
  const response = await window.fetch("http://127.0.0.1:8765/api/workflows");
  assert.equal(response.status, 200);
  assert.deepEqual(tokens, ["Bearer new-token"]);
});

test("browser launch capability is consumed locally and removed from history", async () => {
  let replaced;
  browser({
    location: { href: "http://127.0.0.1:5173/#gofer-token=launch-secret", hash: "#gofer-token=launch-secret", pathname: "/", search: "" },
    history: { state: null, replaceState: (...args) => { replaced = args; } },
  });
  assert.equal(await ensureGoferApiToken(() => assert.fail("must not fetch a public token")), "launch-secret");
  assert.deepEqual(replaced, [null, "", "/"]);
});

test("API token never goes to unrelated hosts, path prefixes or webhooks", () => {
  browser({ goferApiToken: "private-token" });
  for (const target of ["https://attacker.invalid/api/workflows", "http://127.0.0.1:8765/api-other/workflows", "http://127.0.0.1:8765/api/workflows/w/webhooks/default/trigger"]) {
    assert.equal(withGoferApiAuth(target)[1].headers, undefined);
  }
});


test("desktop IPC failure never falls back to the Vite proxy and a retry recovers", async () => {
  const calls = [];
  let ready = false;
  browser({
    goferDesktop: { apiSession: async () => {
      if (!ready) throw new Error("Backend starting");
      return { apiBaseUrl: "http://127.0.0.1:43210", apiToken: "ready-token" };
    } },
    fetch: async (url, init) => { calls.push({ url, init }); return new Response("{}"); },
  });
  installGoferApiFetchAuth();
  await assert.rejects(window.fetch(apiUrl("/workflows")), /desktop backend is unavailable/);
  assert.equal(calls.length, 0);
  ready = true;
  await window.fetch(apiUrl("/workflows"));
  assert.equal(calls[0].url, "http://127.0.0.1:43210/api/workflows");
  assert.equal(calls[0].init.headers.get("Authorization"), "Bearer ready-token");
});

test("manual desktop backends use their actual address even without a token", async () => {
  browser({ goferDesktop: { apiSession: async () => ({ apiBaseUrl: "http://127.0.0.1:43210", apiToken: "" }) } });
  await ensureGoferApiToken();
  assert.equal(apiUrl("/queue"), "http://127.0.0.1:43210/api/queue");
});
