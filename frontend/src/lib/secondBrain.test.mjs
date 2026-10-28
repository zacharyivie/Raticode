import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { EMPTY_FILTERS, describeQuery, matchesFilters, parseDateBound, parseQuery, queryText, searchLibrary, wordDiff } from "./secondBrain.js";
import * as store from "./secondBrainStore.js";

const doc = (fields) => ({ id: fields.id, brainId: "b", title: "Untitled", path: `${fields.id}.md`, folder: "", type: "note", tags: [], origin: "authored", extraction: "ready", reading: { read: true }, dates: { added: "2026-01-01", modified: "2026-01-01", published: "" }, blocks: [], concepts: [], ...fields });

test("query syntax separates words, phrases, exclusions and field clauses", () => {
  const parsed = parseQuery('event "append only" -draft tag:Architecture type:note published:2025..2026-06');
  assert.deepEqual(parsed.terms, ["event"]);
  assert.deepEqual(parsed.phrases, ["append only"]);
  assert.deepEqual(parsed.exclude, ["draft"]);
  assert.deepEqual(parsed.filters, { tags: ["architecture"], types: ["note"] });
  assert.deepEqual(parsed.date, { field: "published", from: "2025-01-01", to: "2026-06-30" });
  assert.deepEqual(parsed.errors, []);
});

test("invalid clauses are reported with their text and never silently dropped", () => {
  const parsed = parseQuery('colour:red published:soon "open phrase tag:ok');
  assert.deepEqual(parsed.errors.map(error => error.text), ["colour:red", "published:soon", '"open phrase tag:ok']);
  assert.equal(parsed.date, null);
  assert.deepEqual(parsed.filters, {});
});

test("date bounds accept years, months and days and reject impossible dates", () => {
  assert.equal(parseDateBound("2026"), "2026-01-01");
  assert.equal(parseDateBound("2024-02", true), "2024-02-29");
  assert.equal(parseDateBound("2026-02-30"), null);
  assert.equal(parseDateBound("soon"), null);
  assert.equal(parseDateBound(""), "");
});

test("filter families combine with AND while values in one family use OR unless all tags are required", () => {
  const item = doc({ id: "a", type: "note", tags: ["search", "sqlite"] });
  assert.equal(matchesFilters(item, { ...EMPTY_FILTERS, types: ["note", "pdf"], tags: ["search", "missing"] }), true);
  assert.equal(matchesFilters(item, { ...EMPTY_FILTERS, types: ["pdf"], tags: ["search"] }), false);
  assert.equal(matchesFilters(item, { ...EMPTY_FILTERS, tags: ["search", "missing"], requireAllTags: true }), false);
});

test("unknown publication dates stay unknown instead of borrowing the modified date", () => {
  const undated = doc({ id: "u", dates: { added: "2026-03-01", modified: "2026-03-01", published: "" } });
  const filters = { ...EMPTY_FILTERS, dateField: "published", from: "2026" };
  assert.equal(matchesFilters(undated, filters), false);
  assert.equal(matchesFilters(undated, { ...filters, includeUnknownDates: true }), true);
  const sorted = searchLibrary([undated, doc({ id: "p", dates: { published: "2020-01-01" } })], { sort: "published" }).results.map(result => result.doc.id);
  assert.deepEqual(sorted, ["p", "u"]);
});

test("search keeps quoted phrases mandatory and labels how each document matched", () => {
  const docs = [
    doc({ id: "phrase", title: "Journal", blocks: [{ id: "1", heading: "A", text: "The event journal is append-only." }] }),
    doc({ id: "words", title: "Events", blocks: [{ id: "2", heading: "B", text: "An event appears in the journal later." }] }),
    doc({ id: "title", title: "Event journal design", blocks: [{ id: "3", heading: "C", text: "Unrelated body." }] }),
  ];
  const { results } = searchLibrary(docs, { text: '"event journal"' });
  assert.deepEqual(results.map(result => [result.doc.id, result.matchKind]).sort(), [["phrase", "phrase"], ["title", "title"]]);
  assert.deepEqual(results.find(result => result.doc.id === "phrase").passages[0].segments.filter(part => part.match).map(part => part.text), ["event journal"]);
});

test("related-meaning matches come only from concepts and documents awaiting extraction are not searched by content", () => {
  const docs = [
    doc({ id: "rrf", concepts: ["hybrid search"], blocks: [{ id: "1", heading: "A", text: "Combining rankings." }] }),
    doc({ id: "scan", extraction: "ocr", blocks: [{ id: "2", heading: "Page", text: "hybrid" }] }),
  ];
  assert.equal(searchLibrary(docs, { text: "hybrid", mode: "exact" }).results.length, 0);
  const related = searchLibrary(docs, { text: "hybrid", mode: "semantic" }).results;
  assert.deepEqual(related.map(result => [result.doc.id, result.matchKind]), [["rrf", "meaning"]]);
});

test("the text form of visual filters parses back to the same filters", () => {
  const text = queryText("ranking", { ...EMPTY_FILTERS, types: ["pdf"], tags: ["hybrid search"], dateField: "published", from: "2009", to: "2010" });
  assert.equal(text, 'ranking type:pdf tag:"hybrid search" published:2009..2010');
  const parsed = parseQuery(text);
  assert.deepEqual(parsed.filters, { types: ["pdf"], tags: ["hybrid search"] });
  assert.deepEqual(parsed.date, { field: "published", from: "2009-01-01", to: "2010-12-31" });
});

test("query descriptions explain scope and filters in plain language", () => {
  const sentence = describeQuery({ text: "journal OR snapshot", filters: { ...EMPTY_FILTERS, tags: ["architecture"], includeGenerated: false }, brainName: "Research" });
  assert.equal(sentence, "Documents in Research where the title or content mentions journal or snapshot, and tagged architecture, and excluding generated reports and summaries.");
});

test("word diff marks removed and added words around shared text", () => {
  const parts = wordDiff("waits for complete reconciliation", "waits for outstanding reconciliation");
  assert.deepEqual(parts.filter(part => part.kind !== "same").map(part => [part.kind, part.text.trim()]), [["removed", "complete"], ["added", "outstanding"]]);
});

beforeEach(async () => { store.resetSecondBrainStore(); await store.loadSampleLibrary(); });

test("each brain restores its own query and open documents when switching", () => {
  store.updateSearch({ text: "journal" });
  store.openDocument("doc-event-journal", { preview: false });
  store.setActiveBrain("brain-personal");
  assert.equal(store.viewFor(store.getState()).search.text, "");
  store.setActiveBrain("brain-research");
  const view = store.viewFor(store.getState());
  assert.equal(view.search.text, "journal");
  assert.deepEqual(view.tabs, [{ docId: "doc-event-journal", preview: false }]);
});

test("previews replace the previous preview tab while kept tabs stay open", () => {
  store.openDocument("doc-fts5");
  store.openDocument("doc-rrf");
  assert.deepEqual(store.viewFor(store.getState()).tabs, [{ docId: "doc-rrf", preview: true }]);
  store.pinTab("doc-rrf");
  store.openDocument("doc-w3c");
  assert.deepEqual(store.viewFor(store.getState()).tabs.map(tab => [tab.docId, tab.preview]), [["doc-rrf", false], ["doc-w3c", true]]);
  store.closeTab("doc-w3c");
  assert.equal(store.viewFor(store.getState()).activeDocId, "doc-rrf");
});

test("collections pin the version they collected and ignore repeated evidence", () => {
  store.addToCollection("col-indexing", store.evidenceItem("doc-fts5", "fts-1"));
  store.addToCollection("col-indexing", store.evidenceItem("doc-fts5", "fts-1"));
  const items = store.getState().collections.find(item => item.id === "col-indexing").sections[0].items.filter(item => item.docId === "doc-fts5");
  assert.equal(items.length, 1);
  assert.equal(items[0].version, "c40e118");
});

test("opening an already registered folder reuses its brain and removing a brain keeps the others", () => {
  const before = store.getState().brains.length;
  const existing = store.createBrain({ name: "Again", root: "/home/doonk/Projects/second-brain/" });
  assert.equal(existing.id, "brain-research");
  assert.equal(store.getState().brains.length, before);
  store.removeBrain("brain-research");
  assert.equal(store.getState().brains.some(brain => brain.id === "brain-research"), false);
  assert.equal(store.getState().activeBrainId, "brain-personal");
});

test("legacy migration never overwrites a sample or unrelated registered brain", () => {
  store.adoptLegacyRoot("/data/knowledge/");
  assert.equal(store.getState().brains[0].root, "/home/doonk/Projects/second-brain");
  assert.equal(store.getState().brains.length, 3);
});

test("saved searches restore location and word matching alongside filters", () => {
  store.updateSearch({ text: "event journal", searchIn: "title", match: "phrase" });
  store.saveSearch("Titles containing a phrase");
  const saved = store.getState().savedSearches.at(-1);
  store.updateSearch({ text: "other", searchIn: "all", match: "any" });
  store.applySavedSearch(saved.id);
  const search = store.viewFor(store.getState()).search;
  assert.equal(search.text, "event journal");
  assert.equal(search.searchIn, "title");
  assert.equal(search.match, "phrase");
});

test("brain clauses resolve display names as well as stable identities", () => {
  const current = store.getState();
  const library = store.libraryFor(current);
  const document = library.documents[0];
  const brain = current.brains.find(item => item.id === document.brainId);
  assert.equal(matchesFilters(document, EMPTY_FILTERS, parseQuery(`brain:"${brain.name}"`), library), true);
  assert.equal(matchesFilters(document, EMPTY_FILTERS, parseQuery('brain:"Unknown library"'), library), false);
});
