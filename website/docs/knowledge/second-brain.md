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
| `search` | Find documents and indexed passages. Each result has a `status`: `current`, or `replaced` with `replacedBy` naming the newer report. It also includes `checkBy` when one is set. |
| `read_note` | Read a document or a pinned source version, with the same status fields |
| `save_note` | Create a note or report and register its citations |

- Search lists reports marked as replaced after current ones, so Rem finds the newer report first.
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
| **Library health** | Extraction problems, missing metadata, duplicates, dead links, citations needing attention, [older report versions](#report-families-and-replaced-reports) and reports past their check-by date. On macOS and Linux it also warns when other accounts on the computer can read the brain folder or its `.git` history; **Make private** applies the owner-only rule again. |
| **Research jobs** | Summaries, digests and comparison refreshes with a visible scope, destination, provider, schedule and limits. They run while the backend runs; their output is left out of new evidence by default. |
| **Import** | Previews a brain package and verifies its files before creating a new destination. Import a copy for new identities, or restore identities when the original isn't registered. Imported jobs start off; cloud access and semantic indexing need setting up again. |
| **Export** | A plain document ZIP, a research collection, or a complete brain package. Never overwrites a destination. |
| **Backups** | From 0.5.5. Saves a complete brain package to a folder of your choice on a schedule, checks each copy and keeps the last few. See [Scheduled backups](#scheduled-backups). |

- Collection packages keep their cited sources, including evidence from other brains.
- Complete packages keep attachments and vault settings, and leave out Raticode caches and common credential files.
- A complete package can hold at most 1 GB, and no single file over 32 MB. From 0.5.5, Raticode checks the size before it starts and names the limit it would pass.
- From 0.5.5, **Include Rem's conversation archive** decides whether the folder where Rem archives its conversations goes in a document or complete export. It shows the archive's size, and starts off when the package wouldn't fit with it. Archived conversations that a report, citation or collection uses always go in, so restored evidence still opens.
- Brain settings import and export bibliographies as CSL JSON, BibTeX and RIS.

### Scheduled backups

**Brain settings > Backups** keeps verified copies of a brain in another folder, so a lost or damaged disk doesn't take the brain with it.

Scheduled backups, the size check before export and the conversation archive option are new in Raticode 0.5.5.

1. Choose a **Backup folder**. It must be outside the brain. A different drive is best. Raticode creates the folder on the first backup and keeps it private to your account.
2. Turn on **Back up on a schedule**, then pick **Every day** or **Every week** (with a day) and a **Time**.
3. Set **Copies to keep** (1 to 60, 7 by default).
4. Leave **Include Rem's conversation archive** off unless you need the archived conversations in the backup. A large archive can push the package past the 1 GB limit, and settings warns when a backup wouldn't fit.

**Back up now** makes a copy at any time. **Show backups** opens the folder.

Each backup:

- writes the [complete brain package](#manage-the-library) to the folder, named after the brain with the time, such as `field-notes-1a2b3c4d-backup-20261113-030000Z.zip`
- runs the package verifier on the saved copy: its structure, size limits and every file's checksum
- removes this brain's older copies past **Copies to keep** only after that check passes, including copies made before the brain was renamed. It never touches other files in the folder, including other brains' backups.

A copy that fails the check is deleted and doesn't count. If Raticode stops in the middle of a backup, the next backup removes the unfinished hidden file it left in the folder. The time is your computer's local time. A backup missed while the computer was off or asleep runs when Raticode is next running that day, or for weekly backups, later that week. A failed scheduled backup isn't retried until the next scheduled time. Use **Back up now** to try again sooner.

The settings show **Last verified backup** with its date, size and file count. They warn when the newest verified backup is more than 7 days old, or when backups have been on for 7 days without one, and they show the error from the last failed attempt.

To restore, choose **Import brain**, pick a backup and choose **Restore this brain** on a new computer, or **Import as a new brain** for a separate copy. Backup settings stay on this computer: they aren't saved in the brain folder or in packages.

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

### Check-by dates

From 0.5.5, to check a report again later, set an optional **Check by** date in **Edit metadata**. From that day on, **Library health > Check-by dates** reminds you. **Mark checked** clears the date, or you can set the next one. Reports marked as replaced don't get reminders. Like the replaced-by mark, the date is stored in the brain's library metadata.

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
