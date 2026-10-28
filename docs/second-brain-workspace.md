# Second Brain workspace

The activity rail opens a persistent library of local folders. Production never loads
sample documents or simulates operations. `secondBrainSample.js` is an explicit fixture
for unit tests and the screenshot runner.

## Storage and identity

`<app-data>/second-brains/library.sqlite3` contains the opened-brain registry, documents,
normalized passages, FTS5 and embedding indexes, curated research, view preferences and
task receipts. Brains are independent of projects and organizations. Closing a brain
cancels its active tasks and removes its registry entry, without deleting its folder.

Portable metadata is `.raticode/brain.json` inside the brain. It holds stable IDs,
collections, saved searches, pinned passages, source history and report provenance.
App-only metadata supports folders where writing portable metadata is undesirable.
Indexes, downloaded models, desktop grants, provider credentials and job provider
configuration are not portable metadata. Restore keeps identities; importing a copy
remaps document, citation, collection, report and job IDs together. Imported jobs start
disabled. Cloud access and semantic indexing require explicit setup after opening a
portable brain on another installation.

Filesystem reconciliation runs every 15 seconds while the app backend is running.
In-place renames preserve document IDs. File identity or an unambiguous matching content
hash also recognizes renames. Changed sources retain normalized historical passages.
Cited missing files remain as unavailable records, with their frozen evidence intact.
An index publishes one database generation after extraction; queries keep the preceding
generation while scanning. Failed work retains a receipt with the actual error.

## Documents, search and reading

Markdown, HTML, plain text and PDFs produce normalized passages. Markdown frontmatter,
tags and optional Obsidian wiki links become searchable metadata. Original files remain
ordinary files. HTML scripts and styles are excluded from extracted reading text;
Original preserves authored styling in a sandbox that blocks scripts and remote assets.
PDF extraction preserves page locators. Scanned PDFs require OCR.

Exact retrieval uses SQLite FTS5. The UI applies the query language and visual filters
to indexed records, including reading state, dates, citations, origin and collections.
Related meaning uses FastEmbed's `BAAI/bge-small-en-v1.5` locally. Enabling it downloads
public model weights into app data; document text stays on the computer. Cloud embeddings
use an existing OpenAI-compatible API profile and require the brain's cloud permission.
The selected embedding profile and model are independent of Rem's chat model.
Old-model vectors are not mixed into current retrieval. Similar-document suggestions use
the stored passage embeddings, independently of recorded links.

Supported limits are 20,000 source documents, 32 MB per source file, 2 MB extracted text
per document, 500 PDF pages, 64 MB portable metadata and a 1 GB expanded import package.
Oversized or malformed sources appear as extraction failures rather than empty successes.
Hidden folders and symbolic links are not indexed. Backup exports preserve vault
configuration and attachments while excluding Raticode caches, `.git`, and common
credential files.

## Evidence, Rem and reports

Saved passages pin the document ID, source hash, block ID, exact quote, nearby text and
normalized snapshot. Saving invented or misplaced quote text fails validation. Selecting
across paragraphs saves separate exact passage citations. Collections preserve pinned
evidence and support independently cited comparisons, including explicit disagreements
and sources that do not address a question.

Summaries, reports, source comparisons and research jobs offer the same provider, model
and effort picker as Rem. Brain settings can inherit Rem or save a separate default.
Each action can override that default, and jobs keep their own selection when edited or
enabled. Configured API profiles remain available, including local endpoints. Grok and
Antigravity use their native CLI permissions in an isolated temporary workspace.

Saved summaries and reports run the selected provider and produce real HTML, Markdown or
PDF files. Reports record requested and reported models separately, source versions,
retrieval generation, coverage, the authored artifact hash and revision lineage.
Each provider quote must match its pinned source before publication. Large reports use
bounded intermediate summaries and validate their quotes before combining them.
Formatting differences in whitespace, quote typography and Unicode composition are
restored to the exact source substring. Changed words, numbers, ellipses and incorrect
passage IDs are not accepted. Invalid citations receive one provider correction attempt
per generation stage when the job's turn budget permits it, using the same pinned
evidence. Remaining batch and final-generation turns are reserved. Unverified citations
still prevent publication; errors identify the claim, citation and evidence ID. The final
report checks original passages, including when intermediate summaries join excerpts.

Creating a report from a brain defaults to automatic source selection. Every eligible
document is checked for topic words in its text, title, path and tags. All matching
documents are included by default, without a source-count cap. A minimum topic-match
slider filters by the percentage of distinct topic words present, from any overlap at
0% to all words at 100%. This is word overlap, not semantic similarity or AI confidence.
An optional top-matches field limits the ranked results only when explicitly supplied.
The dialog previews those sources before generation;
source selection and pinned versions are recorded in the report. An empty match asks for
a more specific brief or manual selection rather than reading the entire library.
Automatic selection can search a collection, selected documents and explicitly added
brains. Reports opened from an explicit document selection or collection retain manual
selection by default. Manual mode has a title/path filter and selection controls.

The option to include previous Second Brain outputs applies only to reports and summaries
saved by this workspace. It avoids repeatedly using earlier summaries as evidence.
Other documents, including AI-written imports, remain eligible. The origin flag records
how a document entered Second Brain; it does not infer human or AI authorship.

Reports and comparison refreshes can wait for review. A draft report requires reviewed
claims and found citations before publication. The original authored artifact remains
the generation record; review metadata and source-change warnings are stored separately.
Regeneration creates a new artifact and preserves the previous one.

In Brain settings, an API profile whose endpoint is localhost or a loopback IP can handle
private reports and jobs. Clicking Summarize, Generate report or Build comparison
authorizes the selected provider to read that request’s evidence. This consent stays with
the task and does not enable cloud processing for background jobs or embeddings.
Background research requires cloud permission on every source brain.
The Rem chat pane requires cloud permission for attached brains. Ask Rem prepares evidence
and a draft without sending it; the evidence's brains become thread resources. Rem's
`rules`, `search`, `read_note` and `save_note` tools are scoped to those registered brains,
and recheck availability and cloud permission on every call. Disabling cloud access
cancels active remote research tasks and prevents subsequent calls.

## Import, export, Inbox and jobs

Import previews inspect inventory, paths, sizes, source metadata and duplicate hashes.
Extraction verifies every checksum in a private staging folder before publishing the new
brain. Existing destination folders and registered identities are never overwritten.
The duplicate-history option retains known snapshots while keeping independent identities
across brains. A collection package includes its referenced source versions, including
sources from other opened brains. Optional history can be omitted; versions used by
citations, reports, collections and comparisons are always retained.

Document export is a plain ZIP of readable files. Collection and complete-brain exports
include a versioned manifest. Backup verification checks the package structure and file
hashes. ZIP paths, links, special files, duplicate paths and expanded size are validated.

Inbox supports real public-page captures, excerpts, and adding local files. Public captures
reject private and loopback network addresses and validate redirects. Discarded files
move into `.raticode/trash` for recovery. Health issues are derived from current extraction,
metadata, content hashes, links and citation state. Ignoring an issue applies to that
source version. Encoding repair and local-link repair update actual extraction or files.
Library health renders the first 15 problems and adds 15 more as the next-batch control
enters the page's scroll viewport. The control also supports manual loading. Counts and
filters cover the full issue list; switching a filter or brain starts again with 15 rows.
CSL JSON, BibTeX and RIS import produce searchable reference notes; bibliography export
preserves bibliographic metadata. External attachment paths in bibliographies are not read.

Jobs persist their recipe, provider, scope, limits, destination and review policy. They
run manually, on local-time schedules, on additions or on cited-source changes. Jobs run
only while the Raticode backend is running. Scheduling uses the chosen time zone and
remembers the last scheduled day. Summarization remembers processed source versions,
respects document and provider-turn budgets, and excludes generated outputs. Comparison
refreshes produce reviewable drafts. Concurrent requests for the same job are deduplicated.

Job source rules combine a location with a topic query, time rule and optional filters.
Locations include the whole brain, Inbox, a relative folder with optional subfolders,
a collection or a saved search. Collection and saved-search references use stable IDs;
existing jobs using the older scope labels retain their behavior when loaded or edited.
Topic matching supports all words, any words, exact phrases and the existing search syntax.
Additional filters include required/excluded tags, document types, project, unread/starred
sources, previous Second Brain outputs and source versions not yet processed by that job.
All constraints apply together. Sources can be ordered oldest or newest first.

Time rules can use all dates, a configurable number of hours or days, or sources since
the last successful run. They can use the added, modified, published or captured date,
or the latest of added/modified. Dates are compared as UTC instants; unknown dates do
not qualify for a time window. On the first run, a since-success rule considers all
matching sources. Completed versions are remembered, and the checkpoint advances to
the run's start time only when all eligible sources were processed. Limits and failures
leave unfinished sources pending; sources arriving during the run qualify next time.
The preview shows eligible sources, the next batch and how many remain beyond its limits.
Previewing never generates a report. Semantic saved searches use their configured
embedding search. Creating a job leaves its trigger disabled until explicitly enabled.

## Runtime requirements

The Python package includes pypdf, FastEmbed, ONNX Runtime and bibliography parsing.
OCR needs **Tesseract and Poppler**, with `tesseract` and `pdftoppm` available on PATH,
plus trained data for the chosen language. Linux packages are commonly `tesseract-ocr`,
the language package and `poppler-utils`; macOS can use `tesseract` and `poppler` through
Homebrew. Windows needs both tools on PATH. Missing tools produce an actionable failure;
the capability snapshot reports whether they are available. OCR never replaces the PDF.
PDF report output uses Raticode's existing authenticated desktop renderer, so the desktop
app must be running. Provider work requires an installed CLI or configured API profile.
Research supports every provider in the shared provider catalog and configured API
profiles. Codex, Claude Code, Cursor, Copilot and OpenCode use restricted research
adapters. Grok and Antigravity use native CLI permissions, as they do for commit messages.

## Implementation and verification

`src/gofer/ui/brain_library.py` owns storage and task orchestration. Separate modules
handle extraction and archives, bibliography conversion, search predicates, embeddings,
privacy, public capture and report rendering. `/api/second-brains` follows the existing
UI token/origin middleware and desktop path grants. Filesystem-changing task destinations
require a grant or containment in app data. Imported metadata cannot overwrite records
belonging to another registered brain.

`frontend/src/lib/secondBrainApi.js` handles requests and grants. The shared store serializes
optimistic edits, reconciles API errors, persists per-brain views, and polls real task
receipts. It does not create fake progress or generated claims. Native export selection
uses the desktop Save dialog and grants the selected parent without writing a file.

Run the Python suite, Ruff and mypy from the repository root. In `frontend`, select
Node 22.23.4 with nvm, then run `npm test`, `npm run lint` and `npm run build`.
`npm run test:second-brain-browser` exercises real API persistence and filesystem operations
through Electron using temporary folders and a deterministic test provider. It makes no
paid provider calls. `npm run test:second-brain-visual` audits light/dark and desktop/narrow
layouts using explicit visual fixtures. On a headless Linux machine, use `xvfb-run -a`.
