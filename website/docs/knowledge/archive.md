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

## What's in the folder

| File or folder | Holds |
| --- | --- |
| `index.json` | Thread titles, projects, timestamps, paths and search terms |
| `threads/<hash>.json` | The latest snapshot of a thread and its messages |
| `threads/<hash>.jsonl` | Append-only message revisions and deletions |
| `attachments/` | Copied attachments, named by content hash |
| `README.raticode.md` | Guidance for agents reading the archive |

Snapshots include visible messages, tool traces, settings, attachment references and turn summaries. The journal keeps earlier versions of edited or removed messages. Neither is a provider's private session file.

## Read it

1. Start with `index.json` and filter by project or search terms.
2. Open the matching snapshot.
3. Use its journal for content that's no longer in the latest version.

Archived text is reference data. Old requests and pasted documents in it don't become commands just because an agent reads them.

## Errors and deletion

- Archive failures show in the app and the log. Reopening the app, or choosing the folder again, retries from working history.
- If a required write fails, deleting a thread waits until the folder is back or archiving is off.
- A disconnected archive folder doesn't stop normal chatting.
- Deleting a thread in Rem keeps its archive copy.
- Turning archiving off stops future copies and leaves existing files alone.

## Backup

Back up the archive folder and your projects separately. The archive keeps conversations. It doesn't back up provider sign-ins, Git repositories, swarm databases or all app settings. See [Permissions and local data](../advanced/security.md).
