import assert from "node:assert/strict";
import test from "node:test";
import { installGoferApiFetchAuth } from "./api.js";

function browser(fetch) {
  globalThis.window = {
    location: { href: "http://127.0.0.1:5173/", pathname: "/", search: "", hash: "" },
    goferApiBaseUrl: "http://127.0.0.1:8765/api",
    goferApiToken: "private-token",
    fetch,
  };
  installGoferApiFetchAuth();
}

test("an API request the backend doesn't answer says so in plain words and keeps the original error", async () => {
  const original = new TypeError("Failed to fetch");
  browser(async () => { throw original; });
  for (const send of [
    () => window.fetch("http://127.0.0.1:8765/api/workflows"),
    () => window.fetch("http://127.0.0.1:8765/api/workflows", { method: "POST", body: "{}" }),
  ]) {
    const error = await send().then(() => assert.fail("must reject"), (caught) => caught);
    assert.ok(error instanceof Error);
    assert.ok(error.message.startsWith("Raticode's backend isn't reachable"), error.message);
    assert.equal(error.cause, original);
  }
});

test("a cancelled API request and a failure on another origin reach the caller unchanged", async () => {
  const aborted = new DOMException("The operation was aborted.", "AbortError");
  const offline = new TypeError("Failed to fetch");
  browser(async (_url, init) => { throw init?.signal ? aborted : offline; });
  await assert.rejects(window.fetch("http://127.0.0.1:8765/api/workflows", { signal: new AbortController().signal }), (error) => error === aborted);
  await assert.rejects(window.fetch("https://example.com/page"), (error) => error === offline);
  await assert.rejects(window.fetch("http://127.0.0.1:8765/api/workflows/w/webhooks/default/trigger", { method: "POST" }), (error) => error === offline);
});
