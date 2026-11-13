---
title: Second Brain
description: Search local knowledge libraries, collect evidence and create reports with checkable citations.
pageClass: docs-layout
---

# Second Brain

::: warning Experimental
Second Brain is off by default. Turn it on in **Settings > Developer > Feature Preview** to show it in the sidebar.
:::

Open **Second Brain** below Organizations in the activity rail. Each brain is an ordinary local folder, separate from projects.

- Open an existing knowledge folder or create one.
- Switch between brains with the sidebar picker.
- Removing a brain from the app keeps its files.

## Find and read evidence

Search Markdown, HTML, text and PDFs with words, phrases and filters:

```text
"exact evidence" tag:research published:2025..2026
```

You can filter by folder, collection, document type, author, tag, date, reading state and citation status. A saved search keeps its query, filters, location and matching mode.

<AppScreenshot name="second-brain" caption="An exact search across a notes folder. Results list their matching passages; the reader opens the selected one with every match highlighted." />

| Matching mode | How it works |
| --- | --- |
| Exact | Works as soon as files are indexed |
| Related meaning | Local semantic search. Turn on **Enable related-meaning search** in Brain settings; the first use downloads the model weights. |
| **Combined** | Ranks each document by both lists (reciprocal rank fusion), so one found by words and meaning moves up. Rem's search uses it when a selected brain has related meaning on. |

Cloud embeddings need a configured API profile and explicit cloud permission.

Results open at their matching passage. The reader has three views:

- **Reading:** the extracted text.
- **Original:** the file as it looks. HTML originals run in a sandbox that blocks scripts and remote assets.
- **Details:** metadata, versions and links.

Scanned PDFs need OCR: install Tesseract and Poppler with the language data you need.

## Projects, connections and the map

**Projects** groups reports, notes and archived Rem conversations by project. Raticode picks each document's project from, in order:

1. your choice
2. the Rem task or thread that saved it
3. a project named in the file: front matter `project:`, or `<meta name="project">` in HTML
4. a `Reports/<Project>/` folder

Every row says which of these it used. You can rename a project, give it other names, or merge two; the old name stays as another name, so files and folders using it still count. Documents with no project wait in **Not in a project**.

**Reports** lists reports Rem tasks saved into the brain, with the task, project folder and requested model. **Brain settings > Find report history** looks up older ones from Rem's task receipts and archived conversations. Only Raticode's records change; your files are never edited.

**Connections**, under a document's Details, shows:

- who made it
- written links, both ways
- the reports, Rem threads and organization tickets that used it
- similar documents

Evidence such as links, citations and observed reads is drawn with solid lines. Similar documents and topics are labelled **Suggested** and drawn dashed.

**Map** groups documents into topics from similar content and written links, and names each topic by its most distinctive words.

<AppScreenshot name="brain-map" caption="The Map groups a brain's notes into topics. Solid lines are links written in files; dashed ones are suggested from similar content." />

- Choose a topic to see its documents or show them on the map, or give it a name that sticks as the brain changes.
- Every map has a list view.
- Similarity uses the local embedding model when related-meaning search is on, and shared distinctive words otherwise.
- All of this runs on your computer, and nothing inferred is written into your files.

## Collect and compare

Select text to save a citation or add it to a collection. Each passage keeps its source version and exact quote; a selection across paragraphs makes one citation per paragraph.

A **collection** holds a research brief, evidence sections, open questions and sources. Build a comparison to see cited answers per source, disagreements, and questions a source doesn't address.

Raticode picks up outside edits to files while it runs. Renames keep a source's identity when it can tell. Changed or missing sources show citation warnings and keep their historical evidence.

## Work with Rem

1. Turn on **Settings > Memory > Second Brain**. Choose its folder first; the switch needs one.
2. In a thread, choose brains under **Thread tools > Second Brains**. Brains are on in a thread unless you turn them off.
3. A registered brain reaches Rem only when **Allow cloud processing** is on in its Brain settings. The **Settings > Memory** folder is already approved. Brains without permission show as unavailable in the thread menu.

Rem then gets these tools, limited to the selected brains:

| Tool | Purpose |
| --- | --- |
| `rules` | Read the selected knowledge and report preferences |
| `search` | Find documents and indexed passages |
| `read_note` | Read a document or a pinned source version |
| `save_note` | Create a note or report and register its citations |

- Turning every brain off removes Second Brain from the thread.
- Tools stay available when shell commands are off.
- Saved notes return local links and never overwrite; use a new filename for a revision.
- Stored text is reference material and doesn't override your request.
- **Ask Rem**, **Explain** and **Summarize** prepare evidence and a prompt in the chat pane without sending it. Cloud chat needs permission for every attached brain.

## Create cited reports

Create a report from search results, a collection or the Reports page. Choose sources, question, scope, output format and coverage.

- Generation uses the research **Provider and model**, and an optional **Provider profile**, chosen for the report.
- A localhost API profile can process private brains. Remote providers need cloud permission on every source brain.
- Source-only research runs with Codex, Claude Code, Cursor, Copilot, OpenCode, Grok, Antigravity, or an OpenAI or Anthropic API profile (which also takes a custom model ID). Grok and Antigravity research uses that CLI's own permissions.

Reports record source versions, generation details and checkable citations. Review claims and their citations before publishing a draft.

- Regenerating creates a new file.
- HTML and Markdown output are ordinary files; PDF needs the desktop renderer.
- Files Rem wrote also appear in Reports, with their own styling.

## Manage the library

| Area | What it does |
| --- | --- |
| **Inbox** | Captured public pages, excerpts and added local files. Review or discard; discarded files move to the brain's `.raticode/trash` folder. |
| **Library health** | Extraction problems, missing metadata, duplicates, broken links and citations needing attention |
| **Research jobs** | Summaries, digests and comparison refreshes with a visible scope, destination, provider, schedule and limits. They run while the backend runs; their output is left out of new evidence by default. |
| **Import** | Previews a brain package and verifies its files before creating a new destination. Import a copy for new identities, or restore identities when the original isn't registered. Imported jobs start off; cloud access and semantic indexing need setting up again. |
| **Export** | A plain document ZIP, a research collection, or a complete brain package. Never overwrites a destination. |

- Collection packages keep their cited sources, including evidence from other brains.
- Complete packages keep attachments and vault settings, and leave out Raticode caches and common credential files.
- Brain settings import and export bibliographies as CSL JSON, BibTeX and RIS.

## Memory settings

**Settings > Memory > Second Brain** turns Second Brain on for Rem. While it's off, no thread gets Second Brain tools and Thread tools hides the brain list.

Its folder also serves the older single-folder setup: with no registered brains, Rem searches Markdown, HTML and text there with a folder-local index. Registered brains and the per-thread list give the multi-brain behavior.

These three are separate and can be turned on independently:

- the [conversation archive](./archive.md) keeps chat history
- Second Brain stores knowledge you write
- [report themes](./themes.md) set how output looks

See also [Reports](./reports.md).
