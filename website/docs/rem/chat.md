---
title: Chat and threads
description: Use Rem conversations, steering, search, forks, and provider changes without losing context.
pageClass: docs-layout
---

# Chat and threads

Rem helps you build workflows, edit code, debug, and understand a project. Each thread keeps its own conversation, scope, resources, and provider settings.

## Start a conversation

Open Rem and send a message to create a thread, or use the new-thread control. Review the project scope and composer settings first. Enter sends; Shift+Enter inserts a line break. Threads are organized into pinned, active, and archived lists, ordered by message activity.

Name a thread for its task, pin ongoing work, and archive finished work to keep the active list readable. Search threads to find a title or text anywhere in saved history, including older messages that are not currently loaded.

## Switch models when sending

Choose a provider, model, and effort in the composer. Your next message uses that selection. A change adds a divider above the sent message:

```text
switched to Codex - gpt-6.1-sol - high
```

Unsent picker changes add no history entries. Returning to the previously submitted settings adds no divider. Thirty changes followed by one send record only the final selection. The same thread keeps its project, resources, and visible history.

Each provider retains its own native session. Switching back restores that provider's session and supplies the turns it missed. Hidden provider tool state stays with that provider. Native continuation does not promise free historical tokens.

## Read thoughts and results

Tool activity and intermediate thoughts appear in collapsible groups. **Settings > Rem > Auto-hide thoughts** collapses completed groups after the reply. Reopen them to inspect evidence. Turn summaries show timing and supported file-change information.

## Steer a running response

While Rem works, type a new instruction and choose **Steer**, or press Enter. The draft clears after the server confirms acceptance. Provider selection stays locked during the running response. Steering can interrupt and restart the provider process with the same selected settings.

**Stop** requests cancellation separately. It does not remove completed edits or external effects. Inspect completed work before continuing an interrupted task. If a provider error offers **Resume task**, that is an explicit continuation request.

## Edit, resend, or fork

Edit the latest user message to replace the conversation from that point and send it again. Resending starts a new native branch for the revised history. It does not undo workspace changes from the original turn.

Use **Fork thread from here** on a user message or reply to explore another approach in a separate thread. A user-message fork resends that selected request once. The parent remains intact, and the new thread starts an independent provider session.

See [Attachments](./attachments.md), [Tools, skills and MCP](./resources.md), and [Conversation archive](../knowledge/archive.md).

