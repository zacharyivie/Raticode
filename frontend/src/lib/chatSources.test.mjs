import test from "node:test";
import assert from "node:assert/strict";
import { collectTurnSources, sourceDomain, videoLinks, youtubeId } from "./chatSources.js";

const search = (output, id = "s") => ({ kind: "thought", trace: { kind: "tool", id, title: "WebSearch", input: '{"query":"rem"}', output } });

test("sources list cited pages first, then pages Rem read, then search results", () => {
  const thoughts = [
    search('Web search results\\n\\nLinks: [{"title":"Fast mode","url":"https://code.claude.com/docs/fast-mode"},{"title":"Effort \\"levels\\"","url":"https://example.com/effort"},{"title":"Local","url":"http://localhost:5173/x"}]'),
    { kind: "thought", trace: { kind: "tool", title: "WebFetch", input: '{"url":"https://blog.example.org/post","prompt":"summary"}' } },
    { kind: "thought", trace: { kind: "tool", title: "Bash", input: '{"url":"https://ignored.example.com"}' } },
  ];
  const sources = collectTurnSources(thoughts, "See [effort docs](https://example.com/effort).");
  assert.deepEqual(sources, [
    { url: "https://example.com/effort", title: 'Effort "levels"', kind: "cited" },
    { url: "https://blog.example.org/post", title: "", kind: "read" },
    { url: "https://code.claude.com/docs/fast-mode", title: "Fast mode", kind: "found" },
  ]);
});

test("answers without web steps have no source cards", () => {
  assert.deepEqual(collectTurnSources([], "Read https://example.com"), []);
  assert.deepEqual(collectTurnSources([{ kind: "thought", trace: { kind: "tool", title: "Read" } }], "https://example.com"), []);
});

test("search results that were cut short still yield their complete links", () => {
  const sources = collectTurnSources([search('Links: [{"title":"One","url":"https://one.example"},{"title":"Tw')], "");
  assert.deepEqual(sources.map(source => source.url), ["https://one.example/"]);
});

test("YouTube links become one video each", () => {
  assert.equal(youtubeId("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ");
  assert.equal(youtubeId("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ");
  assert.equal(youtubeId("https://www.youtube.com/channel/x"), null);
  assert.deepEqual(
    videoLinks("Watch https://www.youtube.com/watch?v=dQw4w9WgXcQ.", [{ url: "https://youtu.be/dQw4w9WgXcQ" }]),
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
  );
  assert.equal(sourceDomain("https://www.example.com/a"), "example.com");
});
