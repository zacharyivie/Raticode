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
- On macOS and Linux, opening a brain makes its folder private to your account (permissions `700`), like Raticode's data folder. A `.git` folder stored outside the brain gets the same rule. Files inside keep their permissions; your home folder, and folders above it, are never changed. Brains opened before this rule existed become private the next time Raticode starts.

## Find and read evidence

Search Markdown, HTML, text and PDFs with words, phrases and filters:

```text
"exact evidence" tag:research published:2025..2026
```

You can filter by folder, collection, document type, author, tag, date, reading state and citation status. A saved search keeps its query, filters, location and matching mode.

The search box reads this syntax. Rem's `search` tool and `Raticode brain search` read the same syntax.

| Write | Finds |
| --- | --- |
| `event log` | Documents with these words. The Library needs every word unless you add `OR` or choose **Any word**. Rem and the CLI rank documents that have any of them, by their [three best passages](#work-with-rem). |
| `"append only"` | Documents containing the exact phrase |
| `-draft`, `-"rough notes"` | Leaves out documents containing the word or phrase |
| `type:` | `note`, `report`, `text`, `html`, `pdf` or `capture` |
| `tag:`, `author:`, `domain:`, `project:` | A tag, author, source website or project. Repeat a field to allow several values. |
| `folder:Projects/notes` | A folder and its subfolders |
| `in:"Reading list"` | Documents in a collection |
| `state:` | `unread`, `starred`, `reviewed`, `highlighted` or `annotated` |
| `origin:` | `authored`, `imported`, `agent` or `generated` |
| `brain:` | A brain's ID or name, when several are selected |
| `modified:2026-11..2026-11-14` | A date range on `modified`, `added`, `published`, `captured` or `generated`. A year or month covers all of it, and either end can be left open: `published:2025..` |

Field names ignore case, and so do values. Quote a value with spaces (`tag:"hybrid search"`) and any text containing a colon (`"https://example.com"`), because `word:` starts a field. The Library marks a clause it can't use and leaves it out of the search. Rem and the CLI return an error naming the problem instead: an unknown field, a minus sign before a field, an impossible date or an unclosed quote. An unknown field's error lists the valid ones.

<AppScreenshot name="second-brain" caption="An exact search across a notes folder. Results list their matching passages; the reader opens the selected one with every match highlighted." />

| Matching mode | How it works |
| --- | --- |
| Exact | Works as soon as files are indexed |
| Related meaning | Local semantic search. Turn on **Enable related-meaning search** in Brain settings; the first use downloads the model weights from Hugging Face ([Privacy](../privacy.md)). |
| **Combined** | Ranks each document by both lists (reciprocal rank fusion), so one found by words and meaning moves up. Rem's search uses it when a selected brain has related meaning on. |

Cloud embeddings need a configured API profile and explicit cloud permission.

Rem's `search` and `Raticode brain search` lift recent documents a little. New in 0.6.0. The first 30 documents in match order are ranked again, each scoring 1/(60 + its match rank) + 0.7/(60 + its recency rank). The recency rank orders those 30 by the day each document appeared, which is the earlier of its added and modified dates, newest first. Match scores and dates are never compared directly, so a newer document only passes older ones that matched about as well, and of two equal matches the newer one comes first. Documents from the same day share a recency rank, so a folder copied in at once keeps its match order, and a document with no date ranks as the oldest. A search that names a date (`2026-11-13`, `Nov 13`, `2025`) or uses a date filter such as `modified:` skips the lift and keeps the plain match order. **Combined** search fuses the lifted word order with the related-meaning order. The Library's search box doesn't lift recent documents.

Results open at their matching passage. The reader has three views:

- **Reading:** the extracted text, split into passages under their section headings. In Markdown a `#` line starts a section; in HTML an `h1` to `h4` does. Very short HTML fragments, such as a lone number or status word, join the passage after them in the same section.
- **Original:** the file as it looks. HTML originals run in a sandbox that blocks scripts and remote assets.
- **Details:** metadata, versions and links.

Every document has an origin: **User-authored**, **Imported**, **Saved by an agent** or **Generated**. Web captures, imported citations and files in a [conversation archive's](./archive.md) `attachments/` folder are **Imported**: text that came from outside. Search results show the origin of anything not user-authored, and **More filters > Origin** (or `origin:imported`) narrows to one. Attachments indexed by an earlier version are relabelled when Raticode next refreshes the brain.

Scanned PDFs need OCR: install Tesseract and Poppler with the language data you need.

Each PDF's text is read in a separate process that gets 60 seconds and, on Linux and macOS, about 1 GB of memory. New in 0.6.0. The 60 seconds start once the reader has started, so a slow app start doesn't count against the file. A PDF that runs out of time or memory, or crashes the reader, doesn't hold up the rest of the brain. It's marked as failed with "This PDF took too long to read" and listed under extraction problems in **Library health**. Raticode doesn't read it again until the file changes; **Extract again** tries it once more. If the reader itself doesn't start within 3 minutes, that refresh leaves its PDFs unread and the next refresh tries them again.

## Projects, connections and the map

**Projects** groups reports, notes and archived Rem conversations by project. Raticode picks each document's project from, in order:

1. your choice
2. the Rem task or thread that saved it
3. a project named in the file: front matter `project:`, or `<meta name="project">` in HTML
4. a `Reports/<Project>/` folder

Every row says which of these it used. You can rename a project, give it other names, or merge two; the old name stays as another name, so files and folders using it still count. Documents with no project wait in **Not in a project**.

**Reports** lists reports Rem tasks saved into the brain, with the task, project folder and requested model. A research task's report keeps the title, question and citations Rem gave when saving it, so they show in **Reports**, in the cited documents' **Connections** and in **Library health**. Citations only count from brains the task may read. A report saved in a project folder has no citations to record, and Rem's reply says so. **Brain settings > Find report history** looks up older ones from Rem's task receipts and archived conversations. Only Raticode's records change; your files are never edited.

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

Deleting a collection, a saved search or a research job can be undone from the notice that follows, such as "Deleted collection “Reading list”." **Undo** puts it back as it was, in the same place in its list. The notice stays for at least 10 seconds, and doesn't close while the pointer is on it or it has focus. Undo after deleting a collection doesn't reopen it. Documents work differently: deleting one asks first and moves its file to the brain's `.raticode/trash` folder. New in 0.6.1.

Raticode picks up outside edits to files while it runs. Renames keep a source's identity when it can tell. Changed or missing sources show citation warnings and keep their historical evidence.

When an update changes how Raticode reads a format, such as the HTML section headings above, it re-reads those documents once. Citations on a document's current version are found again by their exact quote. Citations pinned to an older version keep that version's passages. A quote that can't be found again shows as needing relocation, with the passage it came from.

## Work with Rem

1. Turn on **Settings > Memory > Second Brain**. Choose its folder first; the switch needs one.
2. In a thread, choose brains under **Thread tools > Second Brains**. Brains are on in a thread unless you turn them off.
3. A registered brain reaches Rem only when **Allow cloud processing** is on in its Brain settings. The **Settings > Memory** folder is already approved. Brains without permission show as unavailable in the thread menu. [Agent tokens](../reference/headless.md#agent-tokens) follow the same setting: a script or agent using one can't search or read a brain with **Allow cloud processing** off, and `Raticode brain list` shows that brain as `private`. A token created with `Raticode tokens create --private-brains` is the exception, for local scripts.

A private brain, one with **Allow cloud processing** off, stays private when its folder is also a project. Before every turn that uses a provider not on this computer, Raticode compares the turn's project folders with every registered brain folder:

- A project that is a private brain, or sits inside one, is refused. Rem drops it from its open projects and says why, and a task or thread in it stops. The message names the brain and the two fixes: turn on **Allow cloud processing** for it, or use a provider on localhost.
- A project that holds a private brain in one of its folders runs only in Claude Code's sandbox, which Raticode uses for self-work and reviews. The sandbox hides the brain's folder from shell commands and from Claude Code's Read, Edit, Grep and Glob tools. Any other turn in that project is refused with the same message, since nothing else can hide the folder.

A private brain can also sit inside another brain's folder. The outer brain leaves the private brain's folder out of its own index, so its notes never reach Rem or an agent token through the outer brain, even when the outer brain allows cloud processing. When you register a brain inside another one, or turn **Allow cloud processing** off for it, the outer brain drops those files at its next refresh, within a minute while Raticode is open. The **Settings > Memory** folder follows the same rule when it isn't a registered brain itself, as described below.

Organizations, threads hosted for a paired phone or desktop, and Rem's tasks get the same check. It runs again at the start of every turn, so turning **Allow cloud processing** off stops a running thread at its next turn. The **Settings > Memory** folder, and the knowledge folder a turn was given, were approved when you chose them and never count as private here.

Turns that name only a folder use the brain registered at that folder, so they get the same search, passages and citations as a thread. This covers [Organizations](../advanced/organizations.md) workers, threads hosted for a paired phone or desktop, and Rem's quick note lookups in chat. The folder was approved when you chose it, so this needs no **Allow cloud processing**, and nothing is added to the organization's saved settings or asks for approval again. Once the Library serves the folder, Raticode deletes the folder's old `.raticode/second-brain.sqlite3` index.

A folder that isn't a registered brain keeps a simple word index in `.raticode/second-brain.sqlite3` inside it. That index holds the text the Library would read: HTML without its markup, styles or scripts, and Markdown without front matter. A search for `color` doesn't match a page's CSS. It leaves out every registered brain inside the folder that has **Allow cloud processing** off: search skips that brain's folder, `read_note` refuses a path in it, and notes indexed before the brain was registered or made private drop out at the next turn. Turning **Allow cloud processing** on brings them back at the next turn.

Rem then gets these tools, limited to the selected brains:

| Tool | Purpose |
| --- | --- |
| `rules` | Read the selected knowledge and report preferences, including the report format and [theme](./themes.md). Rem's instructions for each turn hold the Second Brain rules once and only point to this tool for the report format and theme, so Rem calls it before writing a note or report. The tool server itself only names the selected brains. |
| `search` | Find documents with the Library's [query syntax](#find-and-read-evidence), or list them with a blank query. Each result is one document with its title, path, type, modified date, `origin` and up to 3 matching passages, each under its section heading and with a `matchedBy` of `content`, `title`, `heading` or `meaning`. Each result has a `status`: `current`, `replaced` with `replacedBy` naming the newer report, or `lowered` with `folder` naming a folder you [list after other results](#folder-rules). It also includes `checkBy` when one is set, `series` for an edition of a [dated report series](#dated-report-series), `source: conversation archive` for an archive file that nothing uses (see below), and `summary` once per document when it has a [summary](#document-summaries). Results come 20 at a time (`limit` takes up to 50), and `nextCursor` fetches the next page. The reply is plain text: each document opens with a `Result N: title` line (the path, for an imported document) and its fields, one per line, followed by its passages as `[blockId] passage text` lines under `## heading` lines, the same way `read_note` shows them. |
| `read_note` | Read a document or a pinned source version, with the same origin and status fields. A document [left out of search](#folder-rules) is refused. It opens a document by ID or by path: the brain-relative `path` that search and `read_note` return, or the absolute `file` that `read_note` also returns. A path outside the selected brains is refused. The document comes back as one `text`: each passage on a line that starts with its ID, `[blockId] passage text`, and a `## heading` line where a new section starts. For a long document, Rem can start at the passage search found (`blockId`), cap the text (`maxChars`) and continue from `nextBlockId`. With `summaryOnly: true` it returns only the title, `status`, `checkBy`, `summary`, the section `headings` and `textLength`, so Rem can decide whether to read the whole document. |
| `save_note` | Create a note or report and register its citations. An optional `summary` (plain text, at most 600 characters) is written into the file; see [Document summaries](#document-summaries). It never replaces a file, and refuses a name that [differs only by case](#files-whose-names-differ-only-by-case) from one already in the brain. |

- Search reads the [query syntax](#find-and-read-evidence) the Library uses. Plain words rank as before, quoted phrases must appear, `-word` leaves documents out, and field clauses such as `type:report` or `modified:2026-11..2026-11-14` narrow the results. A query with only fields lists the matching documents, newest first.
- Documents rank by their three best passages. Each passage is scored on its document's title and its own text together, with a title match counting five times a match in the text, and a document's score is the sum of its three best. A document with several matching passages comes before one with a single strong passage, and its passages are listed together, best first. Recent documents then move up a little, as [the search section](#find-and-read-evidence) describes. **Combined** search fuses this document order with the related-meaning order. This order is new in 0.6.0. Before it, documents were ordered by their single best passage, and a title match was added once.
- A passage is listed only when its own text has the words or phrases, with one exception: when only a section's heading matches, search lists that section's first passage of text instead of the bare heading, with `matchedBy` `heading`, and that passage's own text may not contain the words. It keeps its own passage ID, so a quote from it cites as usual, and its document's `matchedBy` includes `heading`. A heading with no text under it is listed as itself, with `content`. A title that matches raises its document but adds no passages. A document that matches only by its title is listed once, at its first passage, with `matchedBy` `title`. A passage match gives `content`, and related meaning gives `meaning`. `Raticode brain search` shows `content`, `title` or `meaning` in its `MATCH` column; it lists the heading itself, not the text under it. This is new in 0.6.0. Before it, every passage of a document whose title matched could be listed, each marked `content`, and a matching heading was listed as a bare heading.
- Search lists reports marked as replaced after current ones, so Rem finds the newer report first.
- Files in a folder you [list after other results](#folder-rules) come after current matches and before replaced reports. Files [left out of search](#folder-rules) aren't searched, listed or opened.
- Matching editions of a [dated report series](#dated-report-series) are listed together, newest first.
- When Rem's [conversation archive](./archive.md) is inside a selected brain, archived files that no report, citation or collection uses come after every other result, in a search and in the list a blank search returns. They stay indexed, and `read_note` still opens them by ID. An archived file that a report, citation or collection uses ranks normally.
- Turning every brain off removes Second Brain from the thread.
- Tools stay available when shell commands are off.
- Saved notes return local links and never overwrite; use a new filename for a revision.
- A note is saved once its file is written. If the library is busy then, for example during a long re-index, the reply still gives the path and link with `indexed: false` and says its citations will be recorded shortly. Raticode keeps the report's record in a private file in its data folder, and the desktop app adds it to the library within about 15 seconds of the library freeing up. Saving the same note to the same path again returns `alreadySaved` and says it was Rem's own earlier save, not a different file with that name.
- To cite a passage, Rem gives the document ID and the exact quote. The passage ID and version are optional: Raticode looks for the quote in the document's current version and uses the one passage that contains it. When no passage or more than one contains the quote, that citation is left out and the reply names the candidate passages. The note and its other citations are still saved. A research task's `rem_artifacts.save_report` takes citations the same way.
- Stored text is reference material and doesn't override your request.
- Before each chat reply, Rem also gets up to 5 notes that match your message. Each comes as its path, title, [summary](#document-summaries) when it has one, and the passage that matched, under its section heading and cut to about 1,200 characters. All the notes together stay under 8,000 characters. HTML reports come as their text, without styling or pictures. When Rem needs more of a listed note, it reads the rest with a quick note lookup before answering. When a task's worker asks Rem a question, Rem looks up notes that match the question in the same short form; when Rem decides about a task's progress, it uses notes that match the task. This shorter form is new in 0.6.0. Before it, each note sends up to 6,000 characters from the matching section on.
- Passage text in `read_note` is exactly what Raticode stored, so a quote Rem copies from a line verifies as a citation. A passage that spans several lines continues until the next `[blockId]` line. Passage offsets aren't included. This `text` reply is new in 0.6.0. Before it, `read_note` returns a list of passages with their IDs, headings and offsets, and `format: "passages"` keeps returning that list for tools written against it.
- Search's text reply carries the same documents, passages and IDs as the JSON reply, with a few things shortened. A first line gives the `brainId` and `matchedBy` when every result shares them, and a document shows `status` only when it isn't `current`. Times are shown to the second. Each passage's own `matchedBy` is left out, and an imported document's summary and passages share one wrapper. On a copy of a real brain, this made replies about 25% smaller. This text reply is new in 0.6.0. Before it, `search` returns a JSON object, and `format: "json"` keeps returning that object, unchanged, for tools written against it.
- Passages from **Imported** documents come back inside a `<raticode_attachment>` block, the same wrapper chat uses for attached files. Rem treats the text inside as reference material even when it reads like a request. Rem's quick note lookups in chat mark them the same way.
- An **Imported** document's title and section headings came from outside too, so Rem sees them only inside that block. Outside it, the document is named by its path: its `title` field is the path, and so is its `Result N:` line in search's text reply and its `title` in another result's `replacedBy` or `latest`. The real title appears only in the block's `name` attribute. Each passage's section heading is a `## heading` line at the start of its wrapped text instead of a separate `heading` field, in search's JSON reply, in `read_note` with `format: "passages"` and in chat's note lookups. With `summaryOnly: true`, `headings` holds one wrapped block of `## heading` lines, and the notes Rem gets before each chat reply give the path as the title. User-authored documents keep their title and `heading` fields. This is new in 0.6.0. Before it, an imported document's title and headings came back as plain fields outside the block.
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
- Editions of a [dated report series](#dated-report-series) show as one row: the newest edition, with the earlier ones listed under it by date.

## Manage the library

| Area | What it does |
| --- | --- |
| **Inbox** | Captured public pages, excerpts and added local files. Review or discard; discarded files move to the brain's `.raticode/trash` folder. |
| **Library health** | Extraction problems, missing metadata, duplicates, dead links, citations needing attention, [older report versions](#report-families-and-replaced-reports), [dated report series](#dated-report-series) and reports past their check-by date. On macOS and Linux it also warns when other accounts on the computer can read the brain folder or its `.git` history; **Make private** applies the owner-only rule again. Under **Backups** it lists a backup [kept because it was much smaller](#when-a-backup-is-much-smaller) than the last one, with both copies' counts. Under **Case-only twins** it lists [files whose names differ only by letter case](#files-whose-names-differ-only-by-case); **Open folder** opens the folder that holds them. |
| **Research jobs** | Summaries, digests and comparison refreshes with a visible scope, destination, provider, schedule and limits. They run while the backend runs; their output is left out of new evidence by default. |
| **Import** | Previews a brain package and verifies its files before creating a new destination. Import a copy for new identities, restore identities when the original isn't registered, or [restore over a brain whose folder is gone](#restore-a-brain-whose-folder-is-gone). Imported jobs start off; cloud access and semantic indexing need setting up again. |
| **Export** | A plain document ZIP, a research collection, or a complete brain package. Never overwrites a destination. |
| **Backups** | From 0.5.5. Saves a complete brain package to a folder of your choice on a schedule, checks each copy and keeps the last few, plus one copy from each recent week and month. See [Scheduled backups](#scheduled-backups). |

- Collection packages keep their cited sources, including evidence from other brains.
- Complete packages keep attachments and vault settings, and leave out Raticode caches and common credential files.
- A complete package can hold at most 1 GB, and no single file over 32 MB. From 0.5.5, Raticode checks the size before it starts and names the limit it would pass.
- A document ZIP has no total size limit, because Raticode writes it one file at a time. Each file can still be at most 32 MB, and a file over that is named before anything is written. New in 0.6.1.
- From 0.5.5, **Include Rem's conversation archive** decides whether the folder where Rem archives its conversations goes in a document or complete export. It shows the archive's size, and starts off when the package wouldn't fit with it. Archived conversations that a report, citation or collection uses always go in, so restored evidence still opens.
- Brain settings import and export bibliographies as CSL JSON, BibTeX and RIS.
- Second Brain's dialogs, such as **Capture to Inbox**, **Edit metadata**, **New collection** and **Create cited report**, keep what you enter. Once a field has changed, a click outside the dialog leaves it open, and Escape asks before discarding it: **Keep editing** goes back to the dialog as it was. New in 0.6.1.

### Scheduled backups

**Brain settings > Backups** keeps verified copies of a brain in another folder, so a lost or damaged disk doesn't take the brain with it.

Scheduled backups, the size check before export and the conversation archive option are new in Raticode 0.5.5.

1. Choose a **Backup folder**. It must be outside the brain. A different drive is best, including a [FAT32 or exFAT USB drive](#usb-drives-and-other-formats). Raticode creates the folder on the first backup and keeps it private to your account.
2. Turn on **Back up on a schedule**, then pick **Every day** or **Every week** (with a day) and a **Time**.
3. Set **Copies to keep** (1 to 60, 7 by default). Raticode also keeps [one copy per recent week and month](#which-copies-are-kept) on top of these.
4. Leave **Include Rem's conversation archive** off unless you need the archived conversations in the backup. A large archive can push the package past the 1 GB limit, and settings warns when a backup wouldn't fit.

**Back up now** makes a copy at any time. **Show backups** opens the folder.

Each backup:

- writes the [complete brain package](#manage-the-library) to the folder, named after the brain with the time, such as `field-notes-1a2b3c4d-backup-20261113-030000Z.zip`
- runs the package verifier on the saved copy: its structure, size limits and every file's checksum
- compares the new copy with the last verified backup, and keeps everything if it is [much smaller](#when-a-backup-is-much-smaller)
- otherwise removes this brain's older copies that [no rule keeps](#which-copies-are-kept), only after that check passes, including copies made before the brain was renamed. It never touches other files in the folder, including other brains' backups.

A copy that fails the check is deleted and doesn't count. If Raticode stops in the middle of a backup, the next backup removes the unfinished hidden file it left in the folder. The time is your computer's local time. A backup missed while the computer was off or asleep runs when Raticode is next running that day, or for weekly backups, later that week. A failed scheduled backup isn't retried until the next scheduled time. Use **Back up now** to try again sooner.

The settings show **Last verified backup** with its date, size, file count and document count. They warn when the newest verified backup is more than 7 days old, or when backups have been on for 7 days without one, and they show the error from the last failed attempt.

#### USB drives and other formats

From 0.6.0, backups and exports work on drives formatted FAT32 or exFAT, which most USB sticks and SD cards use. These formats have no hard links, so Raticode first claims the file name with an empty file, then renames the finished copy onto it in one step. Nothing else can take the name in between, and the file never holds a half-written copy.

When you choose a backup folder that already exists, Raticode writes a small hidden file there and deletes it straight away. If that fails, the folder isn't saved and settings says why. A folder that doesn't exist yet isn't checked until the first backup, so choosing one on an unplugged drive never creates it on your computer's own disk.

When a backup or export can't be written, the error names the folder and the drive's format with what to do next, instead of a system error number. For example:

- **FAT32** can't hold a file over 4 GB. Choose a folder on another drive.
- On **FAT32** and **exFAT**, "permission denied" usually means the drive is mounted for another account, since these formats have no per-file permissions. Remount it for your account.
- A drive that is full or mounted read-only says so.

#### Files whose names differ only by case

New in 0.6.0.

Linux keeps `a/Note.md` and `a/note.md` as two files, but macOS and Windows treat them as one, so a brain package can't hold both. Before Raticode reads any file for an export or backup, it checks every name and stops at the first such pair, naming both paths, for example: `a/Note.md and a/note.md differ only by letter case`. Rename or remove one of them, then try again.

**Library health** lists each pair under **Case-only twins**. **Open folder** opens the folder that holds the first file, so you can compare the two and keep one.

`save_note` and Rem's report saving refuse a new file or folder whose name differs only by case from one already there, and the error names the existing one. Raticode doesn't merge or rename twins already in a brain.

#### Which copies are kept

The weekly and monthly copies are new in 0.6.0. Before, only the last **Copies to keep** stayed.

A copy stays when any of these rules keeps it:

- it is one of the newest **Copies to keep**, counting the backup just made
- it is the newest copy from one of the last 4 calendar weeks (Monday to Sunday), counting this week
- it is the newest copy from one of the last 3 calendar months, counting this month

Raticode removes a copy only when no rule keeps it. Weeks and months follow your computer's local calendar, counted back from the backup just made. With daily backups and the default of 7, that's 10 to 12 copies.

#### When a backup is much smaller

New in 0.6.0.

A brain folder emptied by a bad sync, a mistaken delete or a drive that didn't mount would otherwise back up as a near-empty package, and after a few days the last full copy would rotate out. To stop that, Raticode compares each new backup with the last verified one: its files, its documents and its size in bytes. If the new copy holds less than half of any of them, Raticode:

- keeps the new copy, since it passed the check
- removes no older copies
- records the attempt as **kept, much smaller than the last backup**, and keeps comparing later backups with the last full one

**Brain settings > Backups** then shows both copies' files, documents and size, and **Library health** lists it under **Backups** with the same counts. Check the brain folder first. If files went missing, restore them from the last full backup with **Import brain**. If you removed them on purpose, or left out Rem's conversation archive, choose **Accept smaller size**: Raticode makes a new backup, that copy becomes the one later backups compare with, and older copies rotate out again by the rules above.

To restore, choose **Import brain**, pick a backup and choose **Restore this brain** on a new computer, or **Import as a new brain** for a separate copy. Backup settings stay on this computer: they aren't saved in the brain folder or in packages.

#### Restore a brain whose folder is gone

New in 0.6.1.

When a disk fails or a folder is deleted, the brain stays in the list as unavailable. Its backups can be restored over it:

1. Choose **Import brain**, pick one of its backups and choose **Preview import**.
2. Choose **Restore this brain here**. The preview names the missing folder and fills it in as the **Destination folder**. Keep it, or choose another new folder.
3. Choose **Restore**.

Raticode unpacks the package into the destination, then moves the brain there the way **Locate** does and indexes the restored files again. The brain keeps its ID, its brain settings, its **Backups** settings and **Last verified backup**, and every Rem thread that uses it. Reports, collections and citations stay as they were on this computer, and documents match the restored files by path.

The option shows only when the backup's brain is registered here and its folder is missing. If the folder still exists, Raticode refuses to restore over it: choose **Import as a new brain** for a separate copy instead.

### Rem's database backups

Rem's chat, tasks, runs, work threads and board are in one database, `rem-home.sqlite3`, in Raticode's [data folder](../advanced/security.md#stored-data). It isn't part of a brain, so brain backups don't include it. From 0.5.5, Raticode backs it up on its own. There is nothing to turn on.

- **When.** When Raticode starts, unless the newest copy is less than an hour old, then once a day while it runs.
- **How.** Each copy is a consistent snapshot taken with SQLite's backup API, so Rem keeps working while it's made. Raticode runs SQLite's `quick_check` on the copy and gives it its final name only if the check passes. A copy that fails is deleted.
- **Where.** The `rem-db-backups` folder in the data folder, private to your account (folder mode 0700, files 0600), named by time in UTC, such as `rem-home-20261114-030000Z.sqlite3`. Raticode keeps the 3 newest verified copies. Each one is about the size of Rem's database. A copy is skipped, and tried again an hour later, when the disk doesn't have room for it plus a tenth of its size (at least 256 MB) to spare.
- **After Clear history.** Clearing Rem's history and tasks deletes the copies taken before it and takes a fresh one, so a restore can't bring the cleared conversation back. Forgetting one memory doesn't touch the copies: the text stays in them until they rotate out, within 3 days.
- **Moved-aside folders.** The `damaged-<time>`, `replaced-<time>` and `interrupted-<time>` folders described below keep everything the database held, and Raticode never deletes them. Delete them yourself once you no longer need them.

These copies sit on the same disk as the database, so they protect against a damaged file, not a lost disk. For that, include the data folder in your own backups.

**When the database is damaged.** If `rem-home.sqlite3` is damaged when Raticode starts, Raticode:

1. moves the damaged file and its `-wal` and `-shm` files into a `damaged-<time>` folder inside `rem-db-backups`. It never deletes them.
2. restores the newest copy that still passes the check. With no usable copy, Rem starts with an empty database.
3. posts a notice in Rem's chat, on the desktop and on the phone, saying which copy was restored and when it was saved.

Workflows, the studio and phone pairing keep running. Chat, tasks and board changes made after the restored copy was saved are missing. Errors that aren't damage, such as a database another program has locked, never trigger a restore.

**When the disk is full.** A restore first copies the chosen backup to a private temporary file next to the database. Only after that copy is complete and saved to disk does Raticode move the damaged files aside and put the copy in their place. If the copy fails, for example because the disk is full, the damaged files stay where they are. Rem then opens them as they are, and its chat says the restore couldn't run because there isn't enough free disk space. The same applies when there isn't room for the scratch copy Raticode checks for damage: Rem opens the database as it is and its chat says the check couldn't run. Free up some space and restart Raticode to run the check or the restore again. If Raticode stops partway through a restore, the next start finishes it rather than starting Rem with an empty database.

Some damage is only found when a copy fails its check. Raticode then checks the database itself. If that fails too, Rem's chat says so, and the next start restores a copy as above. Until you restart, parts of Rem's chat, tasks or board may not load.

**Restore a copy yourself.** Quit Raticode, then run:

```bash
Raticode rem restore-db --list                 # the verified copies
Raticode rem restore-db                        # restore the newest one
Raticode rem restore-db --snapshot rem-home-20261114-030000Z.sqlite3
```

The command refuses while a running Raticode is using Rem's database. It moves the current files into a `replaced-<time>` folder (or `damaged-<time>` when they fail the check), and Rem posts what was restored in its chat on the next start. To undo a restore, quit Raticode and copy the files back from that folder.

### Report families and replaced reports

From 0.5.5, a report saved several times as it was revised can be retired in favor of its newest copy. Raticode groups documents in a brain that share most of their long passages into a **report family**. Short text such as headings, and passages that appear in many documents (a template notice, for example), don't count. Exact copies show as duplicates instead.

For each family, **Library health > Replaced reports** suggests one change: mark an older report as replaced by the newest one.

1. Choose **Review suggestion** to compare the two reports' dates and sizes.
2. Choose **Mark as replaced** to accept it, or **Not an older version** to keep both as current.
3. If the family has more members, the next suggestion appears.

- The mark is stored in the brain's library metadata (`.raticode/brain.json` when the brain keeps metadata in its folder), so it travels with the folder and with brain packages. Report files are never edited.
- Search lists replaced reports after current ones, and shows **Replaced by** with the newer report's name. Rem's `search` and `read_note` return the same status.
- The reader's details panel lists the family and the replacement. **Undo** makes the report current again.

### Dated report series

New in 0.6.0.

A report you save again on a schedule, such as `fleet-review-2026-11-13.html` and `fleet-review-2026-11-20.html`, is a **dated report series**. Each file is an **edition**. Documents in one brain form a series when:

- their file names match once a date at the end (`YYYY-MM-DD`) and an optional version after it (`-v2`) are removed. Case, spaces, dashes and underscores don't matter, and neither do the folder or the file type.
- the remaining name has at least two words, so `summary-2026-11-20.md` alone doesn't start one.
- there are at least two editions, and at least one of them has a date in its name.

An edition's date comes from its file name. An edition without a date in its name, such as `fleet-review.html`, uses its modified date. On the same day, a higher `-vN` is newer.

What changes:

- **Search.** Documents still rank by how well they match. When several editions of one series match, they're listed together at the place of the best-matching one, newest first. When the search names a date (`2026-11-13`, `Nov 13`, `2025`) or uses a date filter such as `modified:`, results keep their plain match order, so you can look for one edition. Rem's `search` does the same and adds `series` to each edition: the name without the date (`stem`), the `editionDate`, the number of `editions` and the newest edition's `newestDocumentId`.
- **Status.** Older editions stay `current`. They're dated records, not replaced versions. To retire a revised copy, use [replaced reports](#report-families-and-replaced-reports) instead.
- **Reports page.** Reports whose files are editions of one series show as one row with the newest edition, and the earlier editions listed under it by date.
- **Library health > Dated report series** lists each series with its date range. **Not a series** stops grouping one; **Treat as a series** undoes that. The choice is stored in the brain's library metadata (`.raticode/brain.json` when the brain keeps metadata in its folder), so it travels with the folder. Later editions with the same name follow it. Files are never changed.

### Folder rules

New in 0.6.0.

Some folders hold material you keep but rarely want first, such as old drafts or an archive. Give each folder one of three rules:

| Rule | What happens |
| --- | --- |
| **Search normally** | The default. |
| **List after other results** | The folder's files are still searched, but they rank after every current match and before [replaced reports](#report-families-and-replaced-reports). Rem's `search` and `read_note` give them `status: lowered` with `folder` naming the folder. |
| **Leave out of search** | The folder's files aren't indexed or given to Rem: its `search` doesn't find or list them, and `read_note` refuses them. [Agent tokens](../reference/headless.md#agent-tokens) can't open them or see them among another document's related files and connections, and a `save_note` or `save_report` citation that quotes one is left out with the same refusal. The Library still lists and opens them under **More filters > Left out**, each naming the rule that left it out. |

Set a rule in **Brain settings > Folders**, which lists every folder in the brain with its file count, or from the **Folder** menu on a Library search result, which sets the rule for that file's folder.

- A rule covers the folder's subfolders, unless a subfolder has its own rule. A subfolder can be searched normally inside a left-out folder.
- A file added later to a left-out folder is never indexed.
- To leave out one file, open **Edit metadata** and turn on **Leave out of search**. It stays left out when you change its folder's rule. A file left out by its folder's rule can only come back when you change that rule.
- Rules are stored in the brain's settings in its library metadata (`.raticode/brain.json` when the brain keeps metadata in its folder), so they travel with the folder and with brain packages. Files are never moved or changed. Raticode 0.5.5 and earlier can't open a brain folder or package that has folder rules. Set every folder back to **Search normally** first to open it there.

### Check-by dates

From 0.5.5, to check a report again later, set an optional **Check by** date in **Edit metadata**. From that day on, **Library health > Check-by dates** reminds you. **Mark checked** clears the date, or you can set the next one. Reports marked as replaced don't get reminders. Like the replaced-by mark, the date is stored in the brain's library metadata.

### Document summaries

New in 0.6.0.

A document can carry a short summary that Rem reads before deciding whether to open it. Rem's `search` returns it once per document, in a search and in the list a blank search returns, and `read_note` with `summaryOnly: true` returns it with the document's headings and length.

- **Where it comes from.** When Raticode indexes a document it takes the summary, without asking a model, from the first of these it finds:
  1. a `summary:` or `description:` field in a Markdown note's front matter;
  2. an HTML document's `<meta name="description">`;
  3. the first paragraph under a heading named **Short answer**, **Summary**, **TL;DR**, **Abstract** or **Key findings**.

  Otherwise the document has no summary. A summary longer than 600 characters is cut at a word.
- **Written into the file.** When Rem saves with `second_brain.save_note`, `reports.save_report` or a research task's `rem_artifacts.save_report`, it can pass `summary`: plain text of at most 600 characters. Raticode writes it into the file, as a `summary:` front-matter field in Markdown or a `<meta name="description">` in HTML, Slides and a PDF's HTML source, so it travels with the file, packages and backups. A longer summary is refused and nothing is saved.
- **In the app.** The reader's **Details** view shows the summary and where it came from. The **Reports** page shows each report's summary under its name.
- **Your own words.** **Edit metadata** can set a summary. It's stored in the brain's library metadata, like the check-by date, and the file isn't changed. Clear it to go back to the file's own summary.
- **Never cited.** A summary describes a document; it isn't one of its passages. It doesn't match searches as a passage, and a citation that quotes only the summary is left out. A summary taken from a **Summary** section is still that section's text, so the paragraph itself can be cited.

### Dead links

Select **Dead links** in Library health to see each link problem as a list of the note, the link as written and the reason:

| Reason | Meaning | Fix |
| --- | --- | --- |
| **Absolute path inside the brain** | A full path to a file in this brain. It works here, but breaks when the brain moves or syncs. | **Make links relative** |
| **Missing file** | Nothing exists at the link's path. | **Repair link**: choose the file it should point to |
| **Temporary folder** | Points to `/tmp` or a similar folder that gets cleared, whether or not the file is still there. | **Repair link** after copying the file into the brain |
| **Outside the brain** | Points to a file outside the brain that no longer exists. Links to existing files in other folders aren't listed. | **Repair link** |

Library health checks link targets and folder permissions on disk when you open it, when a brain finishes indexing, and otherwise at most once a minute. A linked file you delete or move outside Raticode can take up to a minute to appear here while the page is already open.

When notes link to their own brain by full path, Library health offers **Preview repair**. The preview counts every such link, shows each change and writes nothing. **Rewrite N links** then makes them relative to their notes. The repair stops if the notes changed since the preview, never touches links to `/tmp` or outside the brain, and finds nothing to change when run again. Notes saved by Rem already get relative links (see [Reports](./reports.md#links-in-saved-reports)).

## Memory settings

**Settings > Memory > Second Brain** turns Second Brain on for Rem. While it's off, no thread gets Second Brain tools and Thread tools hides the brain list.

Its folder also serves the older single-folder setup: with no registered brains, Rem searches Markdown, HTML and text there with a folder-local index. Registered brains and the per-thread list give the multi-brain behavior.

These three are separate and can be turned on independently:

- the [conversation archive](./archive.md) keeps chat history. If it sits inside your brain folder, **Delete permanently** and **Purge archive copies** remove its files there, but not copies you already committed to Git. See [Delete permanently](./archive.md#delete-permanently).
- Second Brain stores knowledge you write
- [report themes](./themes.md) set how output looks

See also [Reports](./reports.md).
