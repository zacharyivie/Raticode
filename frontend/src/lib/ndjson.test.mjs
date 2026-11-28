import assert from "node:assert/strict";
import test from "node:test";
import { ndjsonBatches } from "./ndjson.js";

function chunkReader(chunks) {
  const encoder = new TextEncoder();
  const queue = chunks.map(chunk => typeof chunk === "string" ? encoder.encode(chunk) : chunk);
  return { read: async () => queue.length ? { done: false, value: queue.shift() } : { done: true, value: undefined } };
}

async function batches(chunks) {
  const result = [];
  for await (const lines of ndjsonBatches(chunkReader(chunks))) result.push(lines);
  return result;
}

test("a line split across two chunks arrives once, with the chunk that ends it", async () => {
  assert.deepEqual(await batches(['{"a":1}\n{"b":', '2}\n']), [['{"a":1}'], ['{"b":2}'], []]);
});

test("a multi-byte character split across chunks decodes whole", async () => {
  const bytes = new TextEncoder().encode('{"text":"café ☕"}\n');
  const cut = bytes.indexOf(0xe2) + 1; // inside the three bytes of ☕
  const result = await batches([bytes.slice(0, cut), bytes.slice(cut)]);
  assert.deepEqual(result, [[], ['{"text":"café ☕"}'], []]);
  assert.equal(JSON.parse(result[1][0]).text, "café ☕");
});

test("a last line with no newline is flushed when the stream ends", async () => {
  assert.deepEqual(await batches(['{"a":1}\n{"final"', ':true}']), [['{"a":1}'], [], ['{"final":true}']]);
});

test("blank and whitespace-only lines are dropped", async () => {
  assert.deepEqual(await batches(['\n{"a":1}\n\n  \n', '{"b":2}\n\n']), [['{"a":1}'], ['{"b":2}'], []]);
});

test("one batch per read keeps a chunk's lines together", async () => {
  assert.deepEqual(await batches(['{"a":1}\n{"b":2}\n{"c":3}\n']), [['{"a":1}', '{"b":2}', '{"c":3}'], []]);
});

test("a read error reaches the caller", async () => {
  const reader = { read: async () => { throw new TypeError("network lost"); } };
  await assert.rejects(async () => { for await (const _ of ndjsonBatches(reader)) void _; }, /network lost/);
});
