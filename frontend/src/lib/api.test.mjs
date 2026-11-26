import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";
import { apiJson, apiUrl } from "./api.js";

function reply(status, body) {
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, json: async () => JSON.parse(text) };
}

function server(status, body) {
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return reply(status, body); };
  return { calls, fetchImpl };
}

// The hooks sit inside describe so they only wrap these tests: npm test runs every unit
// file in one process, where top-level hooks would swap window around other files' tests.
describe("apiJson", () => {
  let previousWindow;
  beforeEach(() => {
    previousWindow = globalThis.window;
    globalThis.window = { location: { href: "http://127.0.0.1:5173/" }, goferApiBaseUrl: "http://127.0.0.1:8765/api" };
  });
  afterEach(() => {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  });

  test("a 2xx answer returns its body, and a plain read sends no headers so it can be shared", async () => {
    const { calls, fetchImpl } = server(200, { jobs: [1] });
    assert.deepEqual(await apiJson("/generation-jobs", { fetchImpl }), { jobs: [1] });
    assert.equal(calls[0].url, apiUrl("/generation-jobs"));
    assert.deepEqual(calls[0].init, {});
  });

  test("a body goes as JSON with its content type, next to the caller's own headers", async () => {
    const { calls, fetchImpl } = server(200, {});
    const signal = new AbortController().signal;
    await apiJson("rem/switch", { method: "POST", body: { on: true }, headers: { "X-Gofer-Known": "v3" }, signal, fetchImpl });
    assert.equal(calls[0].url, apiUrl("/rem/switch"));
    assert.deepEqual(calls[0].init, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Gofer-Known": "v3" },
      body: JSON.stringify({ on: true }),
      signal,
    });
  });

  test("a 2xx answer with an empty body returns an empty object", async () => {
    assert.deepEqual(await apiJson("/x", { fetchImpl: server(204).fetchImpl }), {});
  });

  test("a refusal throws the server's error text with the status", async () => {
    const { fetchImpl } = server(409, { error: "The post changed after you read it.", detail: "hash mismatch" });
    const error = await apiJson("/social/posts/1/approve", { method: "POST", body: {}, failure: "Posts request failed.", fetchImpl })
      .then(() => assert.fail("must reject"), caught => caught);
    assert.equal(error.message, "The post changed after you read it.");
    assert.equal(error.status, 409);
    assert.equal(error.unseen, undefined);
  });

  test("detailFirst prefers the server's detail over its error", async () => {
    const { fetchImpl } = server(400, { error: "Bad request", detail: "The swarm has no agents." });
    await assert.rejects(apiJson("/swarms", { detailFirst: true, fetchImpl }), { message: "The swarm has no agents.", status: 400 });
    await assert.rejects(apiJson("/swarms", { fetchImpl }), { message: "Bad request" });
  });

  test("an empty or non-JSON refusal throws the caller's fallback sentence", async () => {
    await assert.rejects(apiJson("/x", { failure: "Jev settings couldn't be saved.", fetchImpl: server(500).fetchImpl }), {
      message: "Jev settings couldn't be saved.", status: 500,
    });
    await assert.rejects(apiJson("/x", { failure: status => `Request failed (${status}).`, fetchImpl: server(502, "<html>Bad gateway</html>").fetchImpl }), {
      message: "Request failed (502).", status: 502,
    });
    await assert.rejects(apiJson("/x", { failure: "Could not load.", fetchImpl: server(404, { error: "" }).fetchImpl }), { message: "Could not load." });
  });

  test("a refusal keeps the files the server couldn't show and its reason code", async () => {
    const { fetchImpl } = server(409, { error: "Some changes weren't shown in full.", unseen: ["a.py", "b.py"], code: "conflict" });
    const error = await apiJson("/rattish/triggers/t/approve", { method: "POST", body: { fingerprint: "f" }, fetchImpl })
      .then(() => assert.fail("must reject"), caught => caught);
    assert.deepEqual(error.unseen, ["a.py", "b.py"]);
    assert.equal(error.code, "conflict");
    assert.equal(error.status, 409);
  });

  test("without fetchImpl it uses the global fetch, so the auth patch still applies", async () => {
    const previous = globalThis.fetch;
    const seen = [];
    globalThis.fetch = async (url) => { seen.push(url); return reply(200, { ok: 1 }); };
    try {
      assert.deepEqual(await apiJson("/computers"), { ok: 1 });
      assert.deepEqual(seen, [apiUrl("/computers")]);
    } finally {
      globalThis.fetch = previous;
    }
  });

  test("a request nothing answers rejects with fetch's own error", async () => {
    const original = new Error("Raticode's backend isn't reachable.");
    await assert.rejects(apiJson("/x", { fetchImpl: async () => { throw original; } }), error => error === original);
  });
});
