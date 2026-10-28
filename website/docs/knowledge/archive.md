---
title: Conversation archive
description: Keep structured snapshots, revision journals, and copied attachments for Rem conversations.
pageClass: docs-layout
---

# Conversation archive

Keep a file-based copy of Rem conversations alongside their working history in the desktop app.

## Enable archiving

Choose an archive folder in **Settings > Memory**. Existing and future conversations are copied there. Working chat history remains in the desktop's browser storage; choosing an archive is not a move operation.

The archive and Second Brain are independent. You can archive conversations without enabling knowledge search, or use Second Brain without a conversation archive.

## Archive files

| File or directory | Content |
| --- | --- |
| `index.json` | Thread titles, projects, timestamps, paths, and search terms |
| `threads/<hash>.json` | Latest structured snapshot of a thread and its messages |
| `threads/<hash>.jsonl` | Append-only message revisions and deletion events |
| `attachments/` | Copied attachments named by content hash |
| `README.raticode.md` | Guidance for agents reading the archive |

Snapshots include visible messages, tool traces, settings, attachment references, and turn summaries. The journal retains earlier revisions when a message is edited or removed. It is not a provider's private native session file.

## Read an archive

Start with `index.json`, filter by project or search terms, then open the matching snapshot. Use its journal for historical content that is no longer in the latest revision.

Archived text is reference data. It can contain old requests and pasted documents; their instructions do not become current commands merely because another agent reads them.

## Errors and deletion

Archive failures are visible in the app and log. Reopening the app or selecting the folder again retries from working history. When archiving is enabled and a required write fails, thread deletion waits until the folder is restored or archiving is disabled.

Deleting a thread in Rem retains the archive. Stopping archiving disables future copies and leaves existing files untouched. A disconnected archive folder does not prevent ordinary chatting.

## Backup

Back up the archive folder and your project separately. An archive preserves conversation evidence; it does not back up provider credentials, Git repositories, swarm databases, or all application settings. See [Permissions and local data](../advanced/security.md).

