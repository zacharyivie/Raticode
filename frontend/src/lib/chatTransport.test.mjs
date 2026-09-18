import assert from "node:assert/strict";
import test from "node:test";
import { fetchChatTurn } from "./chatTransport.js";

test("Rem recovers a lost launch response without launching the turn twice", async () => {
  globalThis.window = { goferApiBaseUrl: "http://127.0.0.1:8765" };
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) throw new TypeError("Connection closed before response headers");
    return new Response('{"type":"final","sequence":1}\n');
  };
  const response = await fetchChatTurn({ conversationId: "thread", turnId: "turn" }, { fetchImpl });
  assert.equal(JSON.parse(await response.text()).type, "final");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[1].options.method, undefined);
  assert.match(calls[1].url, /chat\/events\?conversationId=thread&turnId=turn&after=0/);
});

test("Rem does not reconnect an explicitly aborted launch", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(fetchChatTurn({ conversationId: "thread", turnId: "turn" }, {
    signal: controller.signal,
    fetchImpl: async () => {
      calls++;
      controller.abort();
      throw controller.signal.reason;
    },
  }), { name: "AbortError" });
  assert.equal(calls, 1);
});

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

test("Rem preserves a network batch of thoughts as one persistence update", async () => {
  const events = Array.from({ length: 100 }, (_, index) => ({ type: "thought", text: `Thought ${index}`, sequence: index + 1 }));
  events.push({ type: "final", sequence: 101 });
  const batch = events.map(event => JSON.stringify(event)).join("\n") + "\n";
  const response = await fetchChatTurn({ conversationId: "thread", turnId: "turn" }, {
    fetchImpl: async () => new Response(batch),
  });
  const reader = response.body.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), batch);
  assert.equal((await reader.read()).done, true);
});

test("Rem only advances its reconnect cursor after delivering a valid batch", async () => {
  const urls = [];
  const response = await fetchChatTurn({ conversationId: "thread", turnId: "turn" }, {
    fetchImpl: async url => {
      urls.push(url);
      return new Response(urls.length === 1
        ? '{"type":"thought","sequence":1}\ninvalid\n'
        : '{"type":"thought","sequence":1}\n{"type":"final","sequence":2}\n');
    },
  });
  assert.deepEqual((await response.text()).trim().split("\n").map(JSON.parse).map(event => event.sequence), [1, 2]);
  assert.match(urls[1], /after=0$/);
});
