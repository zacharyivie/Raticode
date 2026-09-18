import assert from "node:assert/strict";
import test from "node:test";
import { fetchChatTurn } from "./chatTransport.js";

test("Rem reconnects by cursor without repeating provider work or delivered events", async () => {
  globalThis.window = { goferApiBaseUrl: "http://127.0.0.1:8765" };
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return new Response(calls.length === 1
      ? '{"type":"thought","text":"working","sequence":1}\n'
      : '{"type":"thought","text":"working","sequence":1}\n{"type":"final","sequence":2}\n');
  };
  const response = await fetchChatTurn({ conversationId: "thread", turnId: "turn" }, { fetchImpl });
  const events = (await response.text()).trim().split("\n").map(JSON.parse);
  assert.deepEqual(events.map(event => event.sequence), [1, 2]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.method, "POST");
  assert.match(calls[1].url, /chat\/events\?conversationId=thread&turnId=turn&after=1/);
  assert.equal(calls[1].options.method, undefined);
});

test("Rem passes API errors through without retrying a launch", async () => {
  globalThis.window = { goferApiBaseUrl: "http://127.0.0.1:8765" };
  let calls = 0;
  const response = await fetchChatTurn({}, { fetchImpl: async () => {
    calls++; return new Response('{"error":"busy"}', { status: 503 });
  } });
  assert.equal(response.status, 503);
  assert.equal(calls, 1);
});
