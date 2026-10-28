---
title: Second Brain
description: Search local knowledge libraries, collect evidence and create reports with checkable citations.
pageClass: docs-layout
---

# Second Brain

Open **Second Brain** below Organizations in the activity rail. Each brain is an ordinary local folder, independent of projects. Open an existing knowledge folder or create one, then switch between registered brains with the sidebar picker. Removing a brain from the app keeps its files.

## Find and read evidence

Search Markdown, HTML, text and PDFs with words, phrases and filters. For example:

```text
"exact evidence" tag:research published:2025..2026
```

Filter by folders, collections, document type, author, tags, dates, reading state and citation status. Saved searches preserve the query, filters, search location and matching mode. Exact search works immediately after indexing. Enable **Related meaning** in Brain settings for local semantic search; the first use downloads model weights. Cloud embeddings require a configured API profile and explicit cloud permission.

Open results at their matching passages. Reading shows extracted text, Original preserves the file's presentation, and Details shows metadata, versions and links. HTML originals use a sandbox that blocks scripts and remote assets. Scanned PDFs need OCR; install Tesseract and Poppler with the selected language data.

## Collect and compare

Select text to save a citation or add evidence to a collection. Each passage retains its source version and exact quote. Selections spanning paragraphs create separate passage citations. Collections hold a research brief, evidence sections, open questions and sources. Build a comparison to see cited answers per source, disagreements and questions a source does not address.

External file edits are reconciled while Raticode runs. Renames preserve source identities when recognizable. Changed or missing sources show citation warnings while retaining historical evidence.

## Work with Rem

Turn on **Settings > Memory > Second Brain** first, then choose brains in **Thread tools > Second Brains**. Every brain starts on, including brains you register later, so turn off the ones a thread should not use. Rem receives tools to search, read and save notes within that scope. Turning every brain off removes Second Brain from the thread. Ask Rem, Explain and Summarize prepare evidence and a prompt for the existing chat pane without sending it automatically. Cloud chat requires permission for every attached brain.

| Tool | Purpose |
| --- | --- |
| `rules` | Read the selected knowledge and report preferences |
| `search` | Find documents and indexed passages |
| `read_note` | Read a document or pinned source version |
| `save_note` | Create a note or report and register supplied citations |

Stored text is reference material and does not override your request. Tools remain available when shell execution is disabled. Saved notes return local links and refuse overwrites; use a new filename for revisions.

## Create cited reports

Create a report from search results, a collection or the Reports page. Choose sources, question, scope, output format and coverage. Generation uses the current supported Rem provider or the brain's research API profile. A localhost API profile can process private brains; remote providers require cloud permission on all source brains.

Reports record source versions, generation details and checkable citations. Review claims and citation support before publishing a draft. Regeneration creates a new artifact. HTML and Markdown output are ordinary files; PDF output requires the desktop renderer. Rem-authored files also appear in Reports, retaining their authored styling.

Source-only research supports Codex, Claude Code, Cursor, Copilot, OpenCode and API profiles. With Grok or Antigravity chat, choose a research API profile in Brain settings.

## Manage the library

**Inbox** holds captured public pages, excerpts and added local files. Review or discard items; discarded files move into the brain's `.raticode/trash` folder. **Library health** reports extraction problems, missing metadata, duplicates, broken links and citations needing attention.

**Research jobs** run summaries, digests and comparison refreshes with a visible scope, destination, provider, schedule and limits. Jobs run while the app backend is running; generated outputs are excluded from new evidence by default.

**Import** previews a brain package and verifies its files before creating a new destination. Import a copy for independent identities or restore identities when the original is not registered. Imported jobs start disabled. Cloud access and semantic indexing require explicit setup.

**Export** offers a plain document ZIP, a research collection or a complete brain package. Collection packages retain cited sources, including evidence from other brains. Complete packages preserve attachments and vault configuration while excluding Raticode caches and common credential files. Existing destinations are never overwritten. CSL JSON, BibTeX and RIS bibliography import/export are available in Brain settings.

## Existing Memory settings

**Settings > Memory > Second Brain** turns Second Brain on for Rem. While it is off, no thread receives Second Brain tools and Thread tools hides the brain checklist. Its folder also serves the legacy single-folder setup: without registered brains, Rem searches Markdown, HTML and text there using a folder-local index. The workspace's registry and the thread checklist provide the multi-brain behavior.

A conversation archive preserves chat history. Second Brain stores authored knowledge. Report themes control output design. Each can be enabled independently.

See [Conversation archive](./archive.md), [Reports](./reports.md), and [Report themes](./themes.md).
