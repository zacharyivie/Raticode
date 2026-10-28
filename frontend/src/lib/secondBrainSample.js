// Seed library used by the Second Brain UI until the library service exists.
// Shapes mirror the proposed durable records: brains, documents with versions
// and passages, collections, saved searches, citations and report runs.

const day = offset => new Date(Date.now() - offset * 86400000).toISOString().slice(0, 10);
const at = (offset, time = "09:30") => `${day(offset)}T${time}:00`;

export const SAMPLE_BRAINS = [
  { id: "brain-research", name: "Raticode Research", root: "/home/doonk/Projects/second-brain", state: "available", lastOpened: at(0, "08:12"), color: "indigo",
    index: { state: "updating", progress: 86, asOf: at(0, "10:42"), documents: 412, passages: 6180, pending: 7, failed: 2, generation: 41 },
    settings: { metadata: "portable", semantic: true, embeddings: "local", allowCloud: true, formats: { markdown: true, html: true, text: true, pdf: true, ocr: false }, ocrLanguage: "eng", obsidian: true, zotero: false } },
  { id: "brain-personal", name: "Personal notes", root: "/home/doonk/Documents/Notes", state: "available", lastOpened: at(3, "21:04"), color: "sage",
    index: { state: "ready", progress: 100, asOf: at(3, "21:05"), documents: 96, passages: 840, pending: 0, failed: 0, generation: 12 },
    settings: { metadata: "app", semantic: false, embeddings: "local", allowCloud: false, formats: { markdown: true, html: true, text: true, pdf: false, ocr: false }, ocrLanguage: "eng", obsidian: true, zotero: false } },
  { id: "brain-thesis", name: "Thesis archive", root: "/run/media/doonk/ARCHIVE/thesis", state: "unavailable", lastOpened: at(41, "15:20"), color: "amber",
    index: { state: "unavailable", progress: 0, asOf: at(41, "15:22"), documents: 1288, passages: 20410, pending: 0, failed: 0, generation: 7 },
    settings: { metadata: "portable", semantic: true, embeddings: "local", allowCloud: false, formats: { markdown: true, html: true, text: true, pdf: true, ocr: true }, ocrLanguage: "eng", obsidian: false, zotero: true } },
];

const doc = (fields) => ({ brainId: "brain-research", origin: "authored", extraction: "ready", annotations: 0, cites: [], related: [], concepts: [], reading: { read: true }, ...fields });

export const SAMPLE_DOCUMENTS = [
  doc({ id: "doc-event-journal", title: "Event journal design", path: "Architecture/event-journal-design.md", folder: "Architecture", type: "note", tags: ["architecture", "runtime"], author: "Zack Ivie", project: "Raticode",
    dates: { added: day(62), modified: day(4), published: "" }, version: "3a9f2c1", size: 18400, annotations: 3, reading: { read: true, starred: true, reviewed: true },
    versions: [{ hash: "3a9f2c1", at: at(4), note: "Clarified replay ordering" }, { hash: "b81d07e", at: at(19), note: "Added snapshot section" }, { hash: "50aa9c3", at: at(62), note: "Imported" }],
    cites: ["doc-fts5", "doc-rrf"], related: ["doc-index-review", "doc-run-snapshots"], concepts: ["append-only log", "replay", "durability", "activation lineage"],
    blocks: [
      { id: "ej-1", heading: "Purpose", text: "The event journal records every activation, route decision and result as an append-only sequence. Replaying the journal must reproduce the visible run state without consulting provider processes." },
      { id: "ej-2", heading: "Ordering", text: "Each event carries a monotonic sequence number assigned by the single writer. Readers never infer order from wall-clock timestamps, because two activations on different machines can share the same second." },
      { id: "ej-3", heading: "Snapshots", text: "A snapshot stores the materialized run state at a sequence number. Recovery loads the newest valid snapshot and replays only the events that follow it, which keeps cold start time bounded for long cycles." },
      { id: "ej-4", heading: "Retention", text: "Completed runs keep their journal for 30 days by default. Exported run snapshots include the event journal so a reviewer can inspect exactly which inputs produced a result." },
    ] }),
  doc({ id: "doc-fts5", title: "SQLite FTS5 ranking notes", path: "Research/Search/sqlite-fts5-ranking.md", folder: "Research/Search", type: "note", tags: ["search", "sqlite"], author: "Zack Ivie", project: "Second Brain",
    dates: { added: day(30), modified: day(9), published: "" }, version: "c40e118", size: 9200, annotations: 2, reading: { read: true, reviewed: true },
    versions: [{ hash: "c40e118", at: at(9), note: "Added snippet escaping" }, { hash: "71f3b20", at: at(30), note: "Created" }],
    source: { url: "https://sqlite.org/fts5.html", domain: "sqlite.org" }, related: ["doc-rrf", "doc-perf"], concepts: ["bm25", "full-text search", "ranking", "snippets", "lexical search"],
    blocks: [
      { id: "fts-1", heading: "BM25 weighting", text: "FTS5 exposes bm25() with per-column weights. Boosting the title column by 8 and the path column by 4 made navigational queries for remembered filenames rank above long notes that only mention the words in passing." },
      { id: "fts-2", heading: "Highlight and snippet", text: "The highlight() and snippet() auxiliary functions return marked text. Treat their output as untrusted: render match spans as escaped text and never inject the returned markup as HTML." },
      { id: "fts-3", heading: "Query construction", text: "Compile a validated query object into parameterized SQL and a bounded FTS expression. Quoted phrases must remain mandatory clauses; splitting them into OR terms loses the user's intent." },
    ] }),
  doc({ id: "doc-rrf", title: "Reciprocal rank fusion — paper notes", path: "Research/Search/reciprocal-rank-fusion.pdf", folder: "Research/Search", type: "pdf", tags: ["search", "retrieval", "paper"], author: "Cormack, Clarke and Büttcher", project: "Second Brain",
    dates: { added: day(28), modified: day(28), published: "2009-07-19" }, version: "e2b7d45", size: 412000, annotations: 4, reading: { read: true, starred: true },
    versions: [{ hash: "e2b7d45", at: at(28), note: "Imported from Zotero" }],
    source: { url: "https://dl.acm.org/doi/10.1145/1571941.1572114", domain: "dl.acm.org", doi: "10.1145/1571941.1572114" }, related: ["doc-fts5"], concepts: ["rank fusion", "hybrid search", "semantic search", "meta-search", "combining rankings"],
    blocks: [
      { id: "rrf-1", heading: "Abstract", page: 1, text: "Reciprocal rank fusion, a simple method for combining the document rankings from multiple IR systems, consistently yields better results than any individual system and better results than the standard method Condorcet fuse." },
      { id: "rrf-2", heading: "Method", page: 2, text: "RRF sorts documents by the score 1 / (k + r(d)) summed across rankings, where r(d) is the rank of document d. The constant k = 60 was fixed during a pilot investigation and not altered during subsequent validation." },
      { id: "rrf-3", heading: "Results", page: 3, text: "On the TREC collections studied, RRF outperformed Condorcet fuse and CombMNZ, and it did so without requiring score normalization across the component systems." },
    ] }),
  doc({ id: "doc-second-brain-report", title: "Second Brain workspace: research and design", path: "Reports/Raticode/second-brain-workspace-research-and-design-2026-10-05.html", folder: "Reports/Raticode", type: "report", tags: ["second brain", "product"], author: "Rem", project: "Second Brain", origin: "generated",
    dates: { added: day(0), modified: day(0), published: "" }, version: "9d1e6aa", size: 89568, reading: { read: false },
    versions: [{ hash: "9d1e6aa", at: at(0, "08:40"), note: "Generated by Rem" }],
    cites: ["doc-obsidian", "doc-zotero", "doc-fts5", "doc-rrf", "doc-w3c"], related: ["doc-perf", "doc-index-review"], concepts: ["knowledge library", "citations", "research workspace"],
    blocks: [
      { id: "sbr-1", heading: "Recommendation", text: "Build a library workspace whose central action is selecting evidence for a question, summary, or report. A user should be able to find a passage, inspect its context, add it to a research collection, and ask Rem to write from those specific sources." },
      { id: "sbr-2", heading: "Citations", text: "A citation must resolve to evidence. Give every document a stable ID and every content revision a hash, and store the quoted passage with its surrounding text and locator." },
      { id: "sbr-3", heading: "Rollout", text: "Finish search, read, select, cite and report in stage 1. Do not make OCR or semantic search a prerequisite for a usable first release." },
    ] }),
  doc({ id: "doc-perf", title: "Local indexing performance experiment", path: "Reports/Raticode/second-brain-performance-2026-09-10.html", folder: "Reports/Raticode", type: "report", tags: ["second brain", "performance"], author: "Rem", project: "Second Brain", origin: "generated",
    dates: { added: day(25), modified: day(25), published: "" }, version: "4be0f91", size: 41200, reading: { read: true },
    versions: [{ hash: "4be0f91", at: at(25), note: "Generated by Rem" }], cites: ["doc-fts5"], concepts: ["latency", "benchmark", "index speed"],
    blocks: [
      { id: "perf-1", heading: "Result", text: "Warm component searches measured a 2.06 ms median on a copied 48-note corpus. The measurement excludes model time and transport." },
      { id: "perf-2", heading: "Limits", text: "The experiment does not establish performance for a 10,000-document mixed-format library. Cold indexing and memory were not measured." },
    ] }),
  doc({ id: "doc-index-review", title: "Indexing and migration follow-up", path: "Reports/Raticode/second-brain-indexing-review-2026-09-10.md", folder: "Reports/Raticode", type: "report", tags: ["second brain", "migration"], author: "Rem", project: "Second Brain", origin: "generated",
    dates: { added: day(25), modified: day(2), published: "" }, version: "a11c3d8", size: 15800, reading: { read: true }, citationReview: true,
    versions: [{ hash: "a11c3d8", at: at(2), note: "Edited by user" }, { hash: "6c09e2f", at: at(25), note: "Generated by Rem",
      blocks: { "idx-1": "Search currently waits for complete reconciliation before returning results. Interactive browsing needs a policy for showing results from a published snapshot while extraction continues." } }],
    cites: ["doc-event-journal"], concepts: ["reconciliation", "migration", "indexing"],
    blocks: [
      { id: "idx-1", heading: "Reconciliation", text: "Search currently waits for reconciliation of outstanding work only, then returns results from the last published snapshot. Interactive browsing shows an as-of time while extraction continues." },
      { id: "idx-2", heading: "Migration", text: "When migrating the single secondBrainRoot setting, create one registry entry, preserve its enabled state and do not turn unrelated recent projects into brains." },
    ] }),
  doc({ id: "doc-obsidian", title: "Obsidian — Manage vaults", path: "Captures/help.obsidian.md/manage-vaults.html", folder: "Captures", type: "capture", tags: ["vaults", "reference"], author: "Obsidian", project: "Second Brain", origin: "imported",
    dates: { added: day(6), modified: day(6), published: "", captured: at(6, "14:02") }, version: "0f7e2b4", size: 22000, reading: { read: true },
    versions: [{ hash: "0f7e2b4", at: at(6, "14:02"), note: "Captured" }], source: { url: "https://help.obsidian.md/manage-vaults", domain: "help.obsidian.md" }, concepts: ["vault switcher", "local folders", "portability"],
    blocks: [
      { id: "obs-1", heading: "Switch vaults", text: "Open the vault switcher to see the vaults you have opened before. Select a vault to open it in a new window." },
      { id: "obs-2", heading: "Remove a vault from the list", text: "Removing a vault from the vault switcher does not delete the vault folder. The files remain on disk and can be opened again later." },
    ] }),
  doc({ id: "doc-zotero", title: "Zotero PDF reader and note editor", path: "Captures/zotero.org/pdf-reader.html", folder: "Captures", type: "capture", tags: ["annotations", "reference", "citations"], author: "Zotero", project: "Second Brain", origin: "imported",
    dates: { added: day(6), modified: day(6), published: "", captured: at(6, "14:20") }, version: "d93a510", size: 31000, annotations: 2, reading: { read: true },
    versions: [{ hash: "d93a510", at: at(6, "14:20"), note: "Captured" }], source: { url: "https://www.zotero.org/support/pdf_reader", domain: "zotero.org" }, concepts: ["annotations", "highlights", "page links", "citation workflow"],
    blocks: [
      { id: "zot-1", heading: "Annotations", text: "Highlights, notes and image annotations created in the PDF reader are stored in the Zotero database and linked to the exact page they came from." },
      { id: "zot-2", heading: "Citations from annotations", text: "Dragging an annotation into a note creates a citation that links back to the page in the PDF, so a reader can jump from the note to the original context." },
    ] }),
  doc({ id: "doc-w3c", title: "W3C Web Annotation Data Model — selectors", path: "Research/Standards/web-annotation-selectors.md", folder: "Research/Standards", type: "note", tags: ["citations", "standards"], author: "W3C", project: "Second Brain", origin: "imported",
    dates: { added: day(12), modified: day(12), published: "2017-02-23" }, version: "77ce4f0", size: 7400, reading: { read: true, reviewed: true },
    versions: [{ hash: "77ce4f0", at: at(12), note: "Imported" }], source: { url: "https://www.w3.org/TR/annotation-model/", domain: "w3.org" }, concepts: ["text quote selector", "anchoring", "position selector", "citation anchors"],
    blocks: [
      { id: "w3c-1", heading: "Text quote selector", text: "A TextQuoteSelector describes a range of text by copying it, and including some of the text immediately before (prefix) and after (suffix) it to distinguish between multiple copies of the same sequence of characters." },
      { id: "w3c-2", heading: "Text position selector", text: "A TextPositionSelector describes a range of text by recording the start and end positions in the document. Positions are brittle when the document is edited, so quotes and positions are best used together." },
    ] }),
  doc({ id: "doc-run-snapshots", title: "Run snapshot export checklist", path: "Architecture/run-snapshot-export.md", folder: "Architecture", type: "note", tags: ["architecture", "export"], author: "Zack Ivie", project: "Raticode",
    dates: { added: day(40), modified: day(14), published: "" }, version: "5d2a9b0", size: 5300, reading: { read: true },
    versions: [{ hash: "5d2a9b0", at: at(14), note: "Added manifest fields" }], cites: ["doc-event-journal"], concepts: ["export", "manifest", "portability", "zip package"],
    blocks: [
      { id: "rs-1", heading: "Manifest", text: "Every exported package starts with manifest.json naming the format version, the producing app version and the relative path of each entry. Absolute machine paths are never written." },
      { id: "rs-2", heading: "Validation", text: "Import stages extraction in a temporary directory, rejects paths outside the destination and oversized expansion, then publishes atomically." },
    ] }),
  doc({ id: "doc-citation-ux", title: "Meeting notes — citation UX review", path: "Meetings/2026-09-29-citation-ux.md", folder: "Meetings", type: "note", tags: ["citations", "design", "meeting"], author: "Zack Ivie", project: "Second Brain",
    dates: { added: day(6), modified: day(6), published: "" }, version: "8e11af2", size: 4100, reading: { read: false },
    versions: [{ hash: "8e11af2", at: at(6), note: "Created" }], related: ["doc-w3c", "doc-zotero"], concepts: ["hover preview", "citation states", "review checklist"],
    blocks: [
      { id: "cux-1", heading: "Decisions", text: "Inline citations show a hover card with the source title, a short quote and the locator. Clicking opens the reader at the passage instead of the file's first line." },
      { id: "cux-2", heading: "Open questions", text: "Should a citation whose quote moved to a different heading count as found or as needing review? We agreed ambiguous relocation always asks the user." },
      { id: "cux-3", heading: "Follow-up", text: "Prototype the claims review checklist and keep the coverage indicator clearly separate from any notion of truth." },
    ] }),
  doc({ id: "doc-whiteboard", title: "Whiteboard scan — retrieval architecture", path: "Meetings/whiteboard-retrieval-2026-08.pdf", folder: "Meetings", type: "pdf", tags: ["architecture", "retrieval"], author: "", project: "Second Brain",
    dates: { added: day(48), modified: day(48), published: "" }, version: "ff0912c", size: 2840000, extraction: "ocr", reading: { read: false },
    versions: [{ hash: "ff0912c", at: at(48), note: "Imported" }], concepts: ["retrieval", "architecture diagram"],
    blocks: [{ id: "wb-1", heading: "Page 1", page: 1, text: "This page is an image. Run OCR to make its text searchable." }] }),
  doc({ id: "doc-provider-notes", title: "Provider CLI behavior notes", path: "Research/Providers/provider-cli-notes.txt", folder: "Research/Providers", type: "text", tags: ["providers"], author: "Zack Ivie", project: "Raticode",
    dates: { added: day(80), modified: day(33), published: "" }, version: "1b34e7d", size: 11800, extraction: "failed", reading: { read: true },
    versions: [{ hash: "1b34e7d", at: at(33), note: "Edited" }], concepts: ["context retention", "provider adapters"],
    blocks: [{ id: "prov-1", heading: "Decode error", text: "The file could not be decoded as UTF-8 at byte 4,112." }] }),
  doc({ id: "doc-release", title: "Desktop release checklist", path: "Operations/release-checklist.md", folder: "Operations", type: "note", tags: ["release", "operations"], author: "Zack Ivie", project: "Raticode",
    dates: { added: day(120), modified: day(11), published: "" }, version: "2ce81f4", size: 6200, reading: { read: true, starred: true },
    versions: [{ hash: "2ce81f4", at: at(11), note: "Added notarization step" }], concepts: ["signing", "packaging", "release"],
    blocks: [
      { id: "rel-1", heading: "Before tagging", text: "Run the full test suite, ruff and mypy. Confirm CHANGELOG entries describe user-visible changes in plain language." },
      { id: "rel-2", heading: "Packaging", text: "Build AppImage, deb and rpm on Linux, NSIS on Windows and a notarized dmg on macOS. Verify each signature before publishing." },
    ] }),
  doc({ id: "doc-release-copy", title: "Desktop release checklist", path: "Archive/old/release-checklist.md", folder: "Archive/old", type: "note", tags: ["release"], author: "Zack Ivie", project: "Raticode",
    dates: { added: day(150), modified: day(150), published: "" }, version: "2ce81f4", size: 6200, reading: { read: true }, duplicateOf: "doc-release",
    versions: [{ hash: "2ce81f4", at: at(150), note: "Copied" }], concepts: ["release"],
    blocks: [{ id: "relc-1", heading: "Before tagging", text: "Run the full test suite, ruff and mypy. Confirm CHANGELOG entries describe user-visible changes in plain language." }] }),
  doc({ id: "doc-readwise", title: "Readwise Reader — Filtered views", path: "Inbox/readwise-filtered-views.html", folder: "Inbox", type: "capture", tags: [], author: "", project: "", origin: "imported", inbox: true,
    dates: { added: day(1), modified: day(1), published: "", captured: at(1, "17:48") }, version: "60c1e2a", size: 18300, reading: { read: false },
    versions: [{ hash: "60c1e2a", at: at(1, "17:48"), note: "Captured" }], source: { url: "https://docs.readwise.io/reader/docs/faqs/filtered-views", domain: "docs.readwise.io" }, concepts: ["saved views", "filters"],
    blocks: [{ id: "rw-1", heading: "Filtered views", text: "Filtered views let you save a query by tag, author or other properties and pin it to the sidebar. Full-text search and filtered views are separate features." }] }),
  doc({ id: "doc-notion", title: "Notion — Enterprise search", path: "Inbox/notion-enterprise-search.html", folder: "Inbox", type: "capture", tags: [], author: "", project: "", origin: "imported", inbox: true,
    dates: { added: day(0), modified: day(0), published: "", captured: at(0, "09:12") }, version: "d0b3317", size: 25600, reading: { read: false },
    versions: [{ hash: "d0b3317", at: at(0, "09:12"), note: "Captured" }], source: { url: "https://www.notion.com/help/enterprise-search", domain: "notion.com" }, concepts: ["permission-aware retrieval", "connected sources"],
    blocks: [{ id: "no-1", heading: "Sources", text: "Enterprise search can look across your workspace, connected apps and the web, and it cites the sources it used. You can narrow the search to specific sources." }] }),
  doc({ id: "doc-garden", title: "Spring planting plan", path: "Home/garden-2026.md", folder: "Home", type: "note", tags: ["garden"], author: "Zack Ivie", brainId: "brain-personal",
    dates: { added: day(200), modified: day(30), published: "" }, version: "aa01e3f", size: 2100, reading: { read: true },
    versions: [{ hash: "aa01e3f", at: at(30), note: "Edited" }], concepts: ["planting", "vegetables"],
    blocks: [{ id: "gar-1", heading: "Beds", text: "Tomatoes and basil go in the south bed. Rotate beans into the east bed to restore nitrogen." }] }),
  doc({ id: "doc-reading-list", title: "Reading list", path: "Reading/reading-list.md", folder: "Reading", type: "note", tags: ["books"], author: "Zack Ivie", brainId: "brain-personal",
    dates: { added: day(300), modified: day(8), published: "" }, version: "c7b221e", size: 3400, reading: { read: true, starred: true },
    versions: [{ hash: "c7b221e", at: at(8), note: "Edited" }], concepts: ["books", "knowledge management"],
    blocks: [{ id: "rl-1", heading: "Next", text: "How to Take Smart Notes; The Organized Mind; Designing Data-Intensive Applications (second read, focus on log-structured storage)." }] }),
];

export const SAMPLE_COLLECTIONS = [
  { id: "col-launch", brainId: "brain-research", name: "Second Brain launch evidence", updated: at(0, "10:05"),
    brief: "Which product and technical decisions should the first Second Brain release commit to, and what evidence supports each one?",
    sections: [
      { id: "sec-portability", title: "Portable brains", items: [
        { id: "it-1", kind: "passage", docId: "doc-obsidian", blockId: "obs-2", version: "0f7e2b4", note: "Matches our “Remove from list” behavior." },
        { id: "it-2", kind: "passage", docId: "doc-run-snapshots", blockId: "rs-1", version: "5d2a9b0", note: "" },
      ] },
      { id: "sec-search", title: "Search and ranking", items: [
        { id: "it-3", kind: "passage", docId: "doc-fts5", blockId: "fts-3", version: "c40e118", note: "Quoted phrases stay mandatory." },
        { id: "it-4", kind: "passage", docId: "doc-rrf", blockId: "rrf-2", version: "e2b7d45", note: "" },
        { id: "it-5", kind: "document", docId: "doc-perf", version: "4be0f91", note: "Historical measurement only." },
      ] },
      { id: "sec-citations", title: "Citation integrity", items: [
        { id: "it-6", kind: "passage", docId: "doc-w3c", blockId: "w3c-1", version: "77ce4f0", note: "" },
        { id: "it-7", kind: "passage", docId: "doc-zotero", blockId: "zot-2", version: "d93a510", note: "" },
        { id: "it-8", kind: "passage", docId: "doc-citation-ux", blockId: "cux-2", version: "8e11af2", note: "Decision: ambiguous relocation asks the user." },
      ] },
    ],
    questions: [
      { id: "q-1", text: "Do we need PDF page locators in the first release?", done: false },
      { id: "q-2", text: "Which metadata stays app-local for read-only folders?", done: true },
    ],
    comparison: {
      question: "How should citations stay attached to the right passage?",
      sources: ["doc-w3c", "doc-zotero", "doc-citation-ux"],
      rows: [
        { id: "cmp-1", aspect: "Anchor representation", cells: { "doc-w3c": { text: "Exact quote with prefix and suffix, plus positions.", blockId: "w3c-1" }, "doc-zotero": { text: "Annotation linked to a PDF page.", blockId: "zot-1" }, "doc-citation-ux": null } },
        { id: "cmp-2", aspect: "What happens after edits", cells: { "doc-w3c": { text: "Positions are brittle; quotes survive edits better.", blockId: "w3c-2" }, "doc-zotero": null, "doc-citation-ux": { text: "Ambiguous relocation always asks the user.", blockId: "cux-2" } } },
        { id: "cmp-3", aspect: "Navigation from a citation", cells: { "doc-w3c": null, "doc-zotero": { text: "Citation in a note jumps to the page in the PDF.", blockId: "zot-2" }, "doc-citation-ux": { text: "Click opens the reader at the passage.", blockId: "cux-1" } } },
      ],
      conflicts: [{ id: "conf-1", summary: "Zotero anchors citations to pages; the W3C model recommends text quotes. Page anchors alone cannot survive re-extraction of the same PDF.", left: { docId: "doc-zotero", blockId: "zot-1" }, right: { docId: "doc-w3c", blockId: "w3c-2" } }],
    } },
  { id: "col-indexing", brainId: "brain-research", name: "Indexing reliability", updated: at(9, "16:40"),
    brief: "What has to be true before search can return results while indexing continues?",
    sections: [{ id: "sec-idx", title: "Evidence", items: [
      { id: "it-9", kind: "passage", docId: "doc-index-review", blockId: "idx-1", version: "6c09e2f", note: "Cited version is older than the current file." },
      { id: "it-10", kind: "passage", docId: "doc-event-journal", blockId: "ej-3", version: "b81d07e", note: "" },
    ] }],
    questions: [{ id: "q-3", text: "Can query snapshots reuse the event journal's sequence numbers?", done: false }] },
];

export const SAMPLE_SAVED_SEARCHES = [
  { id: "view-recent", brainId: "*", name: "Recently added", builtIn: true, icon: "clock", text: "", filters: { dateField: "added", from: day(7) }, sort: "added" },
  { id: "view-unread", brainId: "*", name: "Unread", builtIn: true, icon: "eye", text: "", filters: { reading: ["unread"] } },
  { id: "view-reports", brainId: "*", name: "Reports", builtIn: true, icon: "report", text: "", filters: { types: ["report"] }, sort: "modified" },
  { id: "view-review", brainId: "*", name: "Needs citation review", builtIn: true, icon: "quote", text: "", filters: { citations: ["needs-review"] } },
  { id: "view-extraction", brainId: "*", name: "Extraction problems", builtIn: true, icon: "warning", text: "", filters: { extraction: ["failed", "ocr", "pending"] } },
  { id: "view-arch", brainId: "brain-research", name: "Architecture decisions 2026", text: "tag:architecture modified:2026", filters: {} },
  { id: "view-cite", brainId: "brain-research", name: "Citation research", text: "citation OR annotation OR quote", filters: { includeGenerated: false } },
];

export const SAMPLE_REPORTS = [
  { id: "rep-citations", brainId: "brain-research", title: "Citation design brief for the Second Brain launch", collectionId: "col-launch", format: "html", theme: "Evernorth-inspired teal",
    question: "Summarize how Raticode should anchor, display and review citations in the first Second Brain release.", audience: "Product and engineering", length: "Brief",
    generatedAt: at(1, "16:22"), provider: "Claude Code", model: "claude-opus-5-5", modelReported: "claude-opus-5-5", template: "Cited brief v1",
    outputHash: "sha256:5be1…c0a4", artifactPath: "Reports/Raticode/citation-design-brief.html", retrievalGeneration: 39, scope: "selected", filters: "in:\"Second Brain launch evidence\"",
    sourceVersions: [{ docId: "doc-w3c", version: "77ce4f0" }, { docId: "doc-zotero", version: "d93a510" }, { docId: "doc-citation-ux", version: "8e11af2" }, { docId: "doc-index-review", version: "6c09e2f" }, { docId: "doc-fts5", version: "c40e118" }],
    citations: [
      { id: "c1", n: 1, docId: "doc-w3c", blockId: "w3c-1", version: "77ce4f0", quote: "including some of the text immediately before (prefix) and after (suffix) it", state: "found" },
      { id: "c2", n: 2, docId: "doc-w3c", blockId: "w3c-2", version: "77ce4f0", quote: "Positions are brittle when the document is edited", state: "found" },
      { id: "c3", n: 3, docId: "doc-zotero", blockId: "zot-2", version: "d93a510", quote: "creates a citation that links back to the page in the PDF", state: "unreviewed" },
      { id: "c4", n: 4, docId: "doc-citation-ux", blockId: "cux-2", version: "8e11af2", quote: "ambiguous relocation always asks the user", state: "found" },
      { id: "c5", n: 5, docId: "doc-index-review", blockId: "idx-1", version: "6c09e2f", quote: "Search currently waits for complete reconciliation", state: "changed",
        current: "Search currently waits for reconciliation of outstanding work only, then returns results from the last published snapshot." },
      { id: "c6", n: 6, docId: "doc-fts5", blockId: "fts-2", version: "c40e118", quote: "render match spans as escaped text", state: "relocate" },
    ],
    claims: [
      { id: "cl-1", section: "Anchoring", text: "Store each citation as the exact quote with nearby prefix and suffix text, together with its position, so the passage can be found again after small edits.", citations: ["c1", "c2"], support: "paraphrased", reviewed: true },
      { id: "cl-2", section: "Anchoring", text: "Position-only anchors should not be trusted after a document changes.", citations: ["c2"], support: "quoted", reviewed: true },
      { id: "cl-3", section: "Navigation", text: "Every inline citation should open the reader at the cited passage, following the page-link behavior researchers already know from Zotero.", citations: ["c3"], support: "paraphrased", reviewed: false },
      { id: "cl-4", section: "Review", text: "When a quote can only be found in an ambiguous new location, Raticode should ask the user rather than silently re-anchor.", citations: ["c4"], support: "quoted", reviewed: true },
      { id: "cl-5", section: "Review", text: "Search results can lag behind edits because the index waits for reconciliation.", citations: ["c5"], support: "paraphrased", reviewed: false },
      { id: "cl-6", section: "Safety", text: "Matched text from the index must be rendered as escaped text.", citations: ["c6"], support: "quoted", reviewed: false },
      { id: "cl-7", section: "Safety", text: "Users will prefer hover previews to footnotes for most reading.", citations: [], support: "unsupported", reviewed: false },
    ] },
  { id: "rep-indexing", brainId: "brain-research", title: "Indexing reliability summary", collectionId: "col-indexing", format: "md", theme: "Markdown",
    question: "Summarize what must be true before search can return results while indexing continues.", audience: "Engineering", length: "Brief",
    generatedAt: at(9, "17:02"), provider: "Codex", model: "gpt-5.5", modelReported: "Not reported by provider", template: "Cited brief v1",
    outputHash: "sha256:91aa…7e10", artifactPath: "Reports/Raticode/indexing-reliability-summary.md", retrievalGeneration: 33, scope: "selected", filters: "in:\"Indexing reliability\"",
    sourceVersions: [{ docId: "doc-index-review", version: "6c09e2f" }, { docId: "doc-event-journal", version: "b81d07e" }],
    citations: [
      { id: "d1", n: 1, docId: "doc-event-journal", blockId: "ej-3", version: "b81d07e", quote: "replays only the events that follow it", state: "found" },
      { id: "d2", n: 2, docId: "doc-index-review", blockId: "idx-1", version: "6c09e2f", quote: "showing results from a published snapshot while extraction continues", state: "changed",
        current: "Search currently waits for reconciliation of outstanding work only, then returns results from the last published snapshot." },
    ],
    claims: [
      { id: "dl-1", section: "Summary", text: "Readers should query a published index generation and continue to receive results while new documents are extracted.", citations: ["d2"], support: "paraphrased", reviewed: true },
      { id: "dl-2", section: "Summary", text: "Snapshot-plus-replay keeps recovery time bounded.", citations: ["d1"], support: "paraphrased", reviewed: true },
    ] },
];

export const SAMPLE_HEALTH = [
  { id: "h-1", kind: "extraction", docId: "doc-whiteboard", detail: "Image-only PDF. Text is not searchable until OCR runs.", fix: "Run OCR" },
  { id: "h-2", kind: "extraction", docId: "doc-provider-notes", detail: "Could not decode as UTF-8 at byte 4,112.", fix: "Choose encoding" },
  { id: "h-3", kind: "duplicate", docId: "doc-release-copy", otherId: "doc-release", detail: "Identical content to Operations/release-checklist.md.", fix: "Review duplicates" },
  { id: "h-4", kind: "metadata", docId: "doc-readwise", detail: "No author or publication date.", fix: "Edit metadata" },
  { id: "h-5", kind: "metadata", docId: "doc-notion", detail: "No author or publication date.", fix: "Edit metadata" },
  { id: "h-6", kind: "link", docId: "doc-run-snapshots", detail: "Links to Architecture/package-format.md, which no longer exists.", fix: "Locate file" },
  { id: "h-7", kind: "citation", docId: "doc-fts5", detail: "A citation in “Citation design brief” matches two passages after an edit.", fix: "Review location" },
];

export const HEALTH_KINDS = [
  ["link", "Broken links"], ["metadata", "Missing metadata"], ["extraction", "Extraction problems"], ["duplicate", "Exact duplicates"], ["citation", "Ambiguous citations"],
];

export const SAMPLE_JOBS = [
  { id: "job-digest", brainId: "brain-research", name: "Weekly research digest", recipe: "digest", enabled: true, scope: "Documents added in the last 7 days", trigger: { kind: "schedule", label: "Mondays at 09:00" },
    destination: "Reports/Digests", limits: { documents: 40, turns: 6 }, lastRun: { at: at(1, "09:00"), status: "done", detail: "Summarized 12 documents · 0 unsupported claims" } },
  { id: "job-new", brainId: "brain-research", name: "Summarize newly added documents", recipe: "summarize-new", enabled: false, scope: "Inbox folder", trigger: { kind: "added", label: "When a document is added" },
    destination: "Summaries", limits: { documents: 5, turns: 2 }, lastRun: null },
  { id: "job-compare", brainId: "brain-research", name: "Refresh citation comparison", recipe: "refresh-comparison", enabled: true, scope: "Collection: Second Brain launch evidence", trigger: { kind: "changed", label: "When a cited source changes" },
    destination: "Reports/Raticode", limits: { documents: 12, turns: 4 }, lastRun: { at: at(2, "11:31"), status: "attention", detail: "1 source changed — draft waiting for review" } },
];

export const RECIPES = [
  ["summarize-new", "Summarize newly added documents", "Write a short cited summary for each new document."],
  ["digest", "Prepare a research digest", "Collect recent additions into one cited digest."],
  ["refresh-comparison", "Refresh a comparison when sources change", "Draft a revised comparison for review when cited sources change."],
];
