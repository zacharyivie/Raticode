import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { beforeEach, test } from "node:test";
import { EMPTY_FILTERS, describeQuery, formatCitation, isHeadingPassage, sectionName, sectionStarts, matchesFilters, parseDateBound, parseQuery, queryText, searchLibrary, webSourceUrl, wordDiff, demoteReplaced, newerReports, checkByDue, HEALTH_KINDS, deadLinkSummary, linkReason, reportSeriesRows, reportSummary, summaryPatch, SUMMARY_MAX, folderRuleFor, withFolderRule, leftOutBy, loweredBy, folderTree, activeFilterCount } from "./secondBrain.js";
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

test("query syntax matches the shared table that Rem's Python parser also runs", () => {
  // tests/unit/test_brain_queries.py runs the same cases through parse_query.
  const table = JSON.parse(readFileSync(new URL("../../../tests/fixtures/brain-query-syntax.json", import.meta.url), "utf8"));
  assert.ok(table.cases.length >= 20);
  for (const { name, query, expected } of table.cases) assert.deepEqual(parseQuery(query), expected, name);
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

test("a note's Source link only ever points at a web address", () => {
  assert.equal(webSourceUrl("https://example.com/a?b=1"), "https://example.com/a?b=1");
  assert.equal(webSourceUrl("http://example.com"), "http://example.com/");
  for (const value of ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "", null, "not a url"]) {
    assert.equal(webSourceUrl(value), "", String(value));
  }
});

test("replaced reports rank after current ones and name the chain of newer reports", () => {
  const block = text => [{ id: "b1", heading: "", text }];
  const doc = (id, extra = {}) => ({ id, brainId: "b", title: id, path: `${id}.html`, type: "html", tags: [], extraction: "ready", dates: { modified: "2026-11-01" }, blocks: block("rollout plan evidence rollout plan"), ...extra });
  const documents = [doc("draft", { replacedBy: { docId: "final" } }), doc("final", { replacedBy: { docId: "final2" } }), doc("final2", { blocks: block("rollout plan") })];
  const { results } = searchLibrary(documents, { text: "rollout" });
  assert.deepEqual(results.map(result => result.doc.id), ["final2", "draft", "final"]);
  assert.deepEqual(demoteReplaced([{ doc: documents[0] }, { doc: documents[2] }]).map(result => result.doc.id), ["final2", "draft"]);
  assert.deepEqual(newerReports(documents[0], documents).map(item => item.id), ["final", "final2"]);
});

test("folder rules leave files out, list them after other results and show left-out files only under Left out", () => {
  const rules = { Archive: "leave-out", "Archive/Keep": "normal", Old: "list-after" };
  assert.deepEqual(folderRuleFor("Archive/2024/a.md", rules), { folder: "Archive", rule: "leave-out" });
  assert.equal(folderRuleFor("Archive/Keep/b.md", rules), null);
  assert.equal(folderRuleFor("Archived/c.md", rules), null);
  // A rule is stored only where it differs from what the folder inherits.
  assert.deepEqual(withFolderRule(rules, "Archive/Keep", "leave-out"), { Archive: "leave-out", Old: "list-after" });
  assert.deepEqual(withFolderRule({}, "Notes", "normal"), {});
  assert.deepEqual(withFolderRule({ Archive: "leave-out" }, "Archive/Keep", "normal"), { Archive: "leave-out", "Archive/Keep": "normal" });

  const block = text => [{ id: "b1", heading: "", text }];
  const documents = [
    doc({ id: "old", path: "Old/plan.md", folder: "Old", blocks: block("harbor rollout") }),
    doc({ id: "archived", path: "Archive/plan.md", folder: "Archive", blocks: block("harbor rollout"), excluded: true, excludedBy: "Archive" }),
    doc({ id: "kept", path: "Archive/Keep/plan.md", folder: "Archive/Keep", blocks: block("harbor rollout") }),
    doc({ id: "mine", path: "Notes/mine.md", folder: "Notes", blocks: block("harbor rollout"), excluded: true }),
    doc({ id: "notes", path: "Notes/plan.md", folder: "Notes", blocks: block("harbor rollout") }),
    doc({ id: "gone", path: "Archive/gone.md", folder: "Archive", blocks: [], excluded: true, missing: true, extraction: "unavailable" }),
  ];
  const library = { folderRules: { b: rules } };
  assert.deepEqual(leftOutBy(documents[1], rules), { folder: "Archive" });
  assert.deepEqual(leftOutBy(documents[3], rules), { folder: "" });
  assert.equal(leftOutBy(documents[5], rules), null);
  assert.equal(loweredBy(documents[0], rules), "Old");
  // The Old folder is listed after current matches; left-out files don't match at all.
  const { results } = searchLibrary(documents, { text: "harbor" }, library);
  assert.deepEqual(results.map(result => result.doc.id), ["kept", "notes", "old"]);
  const filters = { ...EMPTY_FILTERS, leftOut: true };
  assert.equal(activeFilterCount(filters), 1);
  assert.deepEqual(searchLibrary(documents, { text: "harbor", filters }, library).results.map(result => result.doc.id).sort(), ["archived", "mine"]);
  // A folder rule saved a moment ago applies before the library re-indexes.
  assert.deepEqual(searchLibrary(documents, { text: "harbor" }, { folderRules: { b: {} } }).results.map(result => result.doc.id).sort(), ["archived", "kept", "notes", "old"]);
  assert.deepEqual(demoteReplaced([{ doc: documents[0] }, { doc: documents[4] }], library).map(result => result.doc.id), ["notes", "old"]);
  assert.match(describeQuery({ filters }), /left out of search/);

  assert.deepEqual(folderTree(documents, { Empty: "leave-out" }).map(folder => [folder.path, folder.count, folder.depth]), [["Archive", 2, 0], ["Archive/Keep", 1, 1], ["Empty", 0, 0], ["Notes", 2, 0], ["Old", 1, 0]]);
});

test("check-by dates are due on the day and never for replaced reports", () => {
  const today = new Date(2026, 10, 13);
  assert.equal(checkByDue({ checkBy: "2026-11-13" }, today), true);
  assert.equal(checkByDue({ checkBy: "2026-11-14" }, today), false);
  assert.equal(checkByDue({ checkBy: "2026-11-01", replacedBy: { docId: "x" } }, today), false);
  assert.equal(checkByDue({}, today), false);
});

test("dead links are grouped by why they are dead, in a fixed order", () => {
  assert.deepEqual(HEALTH_KINDS[0], ["link", "Dead links"]);
  const issues = [
    { kind: "link", reason: "temporary" }, { kind: "link", reason: "absolute" }, { kind: "link" },
    { kind: "link", reason: "temporary" }, { kind: "metadata" }, { kind: "link", reason: "outside" },
  ];
  assert.deepEqual(deadLinkSummary(issues).map(item => [item.id, item.count]), [["absolute", 1], ["missing", 1], ["temporary", 2], ["outside", 1]]);
  assert.equal(linkReason({ kind: "link" })[0], "missing");
  assert.equal(linkReason({ kind: "link", reason: "unknown" })[0], "missing");
  assert.equal(linkReason({ kind: "link", reason: "absolute" })[1], "Absolute path inside the brain");
});

test("the reader names each section once and shows heading passages as headings", () => {
  const blocks = [
    { id: "a", heading: "Document", text: "Research · 14 November" },
    { id: "b", heading: "01 Short answer", text: "01 Short answer" },
    { id: "c", heading: "01 Short answer", text: "96%\n\nof passages had no heading." },
    { id: "d", heading: "Findings", text: "# Findings\nMarkdown keeps its own heading line." },
    { id: "e", heading: "Page 2", page: 2, text: "Page two text." },
    { id: "f", heading: "Page 2", page: 3, text: "Same label, next page." },
  ];
  assert.deepEqual([...sectionStarts(blocks)], ["a", "b", "d", "e", "f"]);
  assert.deepEqual(blocks.map(isHeadingPassage), [false, true, false, false, false, false]);
  assert.equal(sectionName(blocks[0]), "");
  assert.equal(sectionName(blocks[2]), "01 Short answer");
  const source = { type: "html", author: "Rem", dates: { published: "2026-11-14" } };
  assert.equal(formatCitation(source, blocks[2]), "Rem, 2026, § 01 Short answer");
  // A passage before any heading has no section to cite.
  assert.equal(formatCitation(source, blocks[0]), "Rem, 2026");
});

test("reports that are editions of one dated series show as one row, newest first", () => {
  const report = (id, path) => ({ id, brainId: "b", title: id, artifactPath: path, citations: [], claims: [] });
  const documents = [doc({ id: "d06", path: "Reports/fleet-review-2026-11-06.html" }), doc({ id: "d13", path: "Reports/fleet-review-2026-11-13.html" }), doc({ id: "d20", path: "Reports/fleet-review-2026-11-20.html" }), doc({ id: "dx", path: "Reports/other.html" })];
  const series = [{ id: "series-1", brainId: "b", stem: "fleet-review", newestId: "d20", notSeries: false, editions: [{ docId: "d20", date: "2026-11-20" }, { docId: "d13", date: "2026-11-13" }, { docId: "d06", date: "2026-11-06" }] }];
  const reports = [report("r06", "Reports/fleet-review-2026-11-06.html"), report("rx", "Reports/other.html"), report("r20", "Reports/fleet-review-2026-11-20.html"), report("r13", "Reports/fleet-review-2026-11-13.html")];
  const rows = reportSeriesRows(reports, documents, series);
  // The series sits where its first report was listed, with the newest edition on the row.
  assert.deepEqual(rows.map(row => row.report.id), ["r20", "rx"]);
  assert.equal(rows[0].date, "2026-11-20");
  assert.deepEqual(rows[0].editions.map(edition => [edition.report.id, edition.date]), [["r13", "2026-11-13"], ["r06", "2026-11-06"]]);
  assert.equal(rows[1].series, undefined);
  // A group marked "Not a series" stays as separate rows, and one report alone is a plain row.
  assert.deepEqual(reportSeriesRows(reports, documents, [{ ...series[0], notSeries: true }]).map(row => row.report.id), ["r06", "rx", "r20", "r13"]);
  const single = reportSeriesRows([reports[0], reports[1]], documents, series);
  assert.deepEqual(single.map(row => [row.report.id, row.editions.length, Boolean(row.series)]), [["r06", 0, false], ["rx", 0, false]]);
});

test("marking a series records it locally", () => {
  store.getState().series = [{ id: "series-1", brainId: "b", notSeries: false, editions: [] }];
  store.markSeries("series-1", true);
  assert.equal(store.getState().series[0].notSeries, true);
  store.markSeries("series-1", false);
  assert.equal(store.getState().series[0].notSeries, false);
});

test("Edit metadata's summary overrides the file's, and clearing it brings the file's back", () => {
  const fromFile = doc({ id: "s", summary: "File words.", summarySource: "section" });
  assert.deepEqual(summaryPatch(fromFile, "File words."), {});
  const edited = { ...fromFile, ...summaryPatch(fromFile, "My words.") };
  assert.equal(edited.summary, "My words.");
  assert.equal(edited.summarySource, "metadata");
  assert.equal(edited.fileSummary, "File words.");
  for (const back of ["", "File words."]) {
    const restored = { ...edited, ...summaryPatch(edited, back) };
    assert.equal(restored.summary, "File words.");
    assert.equal(restored.summarySource, "section");
    assert.equal(JSON.parse(JSON.stringify(restored)).fileSummary, undefined);
  }
  // Clearing a summary that came from the file leaves it: the file still has it.
  assert.deepEqual(summaryPatch(fromFile, ""), {});
  assert.equal(SUMMARY_MAX, 600);
});

test("a report's summary comes from its saved file, or a PDF's HTML source", () => {
  const report = { id: "r", brainId: "b", artifactPath: "Reports/r.pdf" };
  const docs = [doc({ id: "pdf", path: "Reports/r.pdf" }), doc({ id: "src", path: "Reports/r.pdf.html", summary: "In brief." }), doc({ id: "other", brainId: "c", path: "Reports/r.pdf", summary: "Wrong brain." })];
  assert.equal(reportSummary(report, docs), "In brief.");
  assert.equal(reportSummary({ ...report, artifactPath: "Reports/none.md" }, docs), "");
});
