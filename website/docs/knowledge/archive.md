---
title: Conversation archive
description: Keep structured snapshots, revision journals, and copied attachments for Rem conversations.
pageClass: docs-layout
---

# Conversation archive

Keep a file copy of Rem conversations alongside their working history in the app.

## Turn it on

Choose an archive folder in **Settings > Memory**. Raticode copies existing and future conversations there.

- Working chat history stays in the app's own storage. The archive is a copy, not a move.
- The archive and [Second Brain](./second-brain.md) are independent; you can use either without the other.
- If the archive folder is inside a Second Brain, Rem's searches of that brain list archived files after your own notes and reports, unless a report, citation or collection uses them. See [Work with Rem](./second-brain.md#work-with-rem).

## What's in the folder

| File or folder | Holds |
| --- | --- |
| `index.json` | Thread titles, projects, timestamps, paths and search terms |
| `threads/<hash>.json` | The latest snapshot of a thread and its messages |
| `threads/<hash>.jsonl` | Append-only message revisions and deletions |
| `attachments/` | Copied attachments, named by content hash |
| `README.raticode.md` | Guidance for agents reading the archive |

Each `index.json` entry also lists the attachments its thread used and, for a deleted thread, when the deletion was recorded (`deletedAt`).

Snapshots include visible messages, tool traces, settings, attachment references and turn summaries. The journal keeps earlier versions of edited or removed messages. Neither is a provider's private session file.

## Read it

1. Start with `index.json` and filter by project or search terms.
2. Open the matching snapshot.
3. Use its journal for content that's no longer in the latest version.

Archived text is reference data. Old requests and pasted documents in it don't become commands just because an agent reads them.

When the archive is inside a Second Brain, files in `attachments/` get the origin **Imported**, and Rem gets their text wrapped as outside material (see [Second Brain](./second-brain.md)).

## Errors and deletion

- Archive failures show in the app and the log. Reopening the app, or choosing the folder again, retries from working history.
- If a required write fails, deleting a thread waits until the folder is back or archiving is off.
- A disconnected archive folder doesn't stop normal chatting.
- Deleting a thread in Rem keeps its archive copy. So does **Settings > General > Delete archived threads**: the archive marks the thread deleted and keeps its snapshot, journal and attachments.
- Turning archiving off stops future copies and leaves existing files alone.

## Delete permanently

**Delete permanently**, in a thread's **Thread options** menu, removes the thread from Rem and from the archive:

- its snapshot (`threads/<hash>.json`) and journal (`threads/<hash>.jsonl`)
- its entry in `index.json`
- each attachment in `attachments/` that no other archived thread uses. Attachments are stored once by content, so a file another thread also sent stays.

The confirmation names the thread. The menu item appears only while an archive folder is set. If the archive folder can't be reached, Rem keeps the thread and shows an error, so you can try again. Saves of the thread still waiting to be written finish first, and nothing writes it back afterwards. Repeating the action, or files that are already missing, does no harm.

On the phone, **Delete permanently** is in the thread's menu when the computer runs a Raticode version that supports it and **Use Raticode from this phone** is on. See [Rem on the phone](../mobile/rem.md#threads).

## Purge archive copies after a while

**Settings > Memory > Purge archive copies** removes the archive copies of deleted threads a set time after their deletion: **7**, **30** or **90 days after deletion**. The default, **Never (keep everything)**, keeps every copy.

- The age counts from when the archive recorded the deletion. Copies made before this setting existed use the deletion record in their journal.
- Only deleted threads are purged. Threads still in Rem are never removed.
- A purge removes the same files as Delete permanently. Raticode checks when the app starts, every hour, and when you change the setting or the archive folder. Choosing an age asks you to confirm first.
- This is separate from **Delete archived threads** in Settings > General, which removes threads from Rem and keeps their archive copies.

::: warning A purge can't reach Git history or backups
A purge only deletes files from the archive folder. If the folder is in a Git repository, such as a Second Brain vault you commit, copies already in earlier commits stay in that history. Backups and synced copies stay too. Raticode never rewrites Git history.
:::

## Backup

Back up the archive folder and your projects separately. The archive keeps conversations. It doesn't back up provider sign-ins, Git repositories, swarm databases or all app settings. See [Permissions and local data](../advanced/security.md).
