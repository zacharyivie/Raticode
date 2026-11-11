---
title: Chat and threads
description: Use Rem conversations, steering, search, forks, and provider changes without losing context.
pageClass: docs-layout
---

# Chat and threads

Threads are your own conversations with a coding agent through Rem. Each thread keeps its own conversation, scope, resources, and provider settings. For Rem's single ongoing conversation, tasks and worker threads, see [Rem's home and tasks](./home.md).

## Start a conversation

Threads live in the Rem pane. **Settings > Layout > Rem pane shows** switches that pane between **Threads** (the default) and Rem's home. Use **New thread** there, review the project scope and composer settings, then send. Enter sends; Shift+Enter inserts a line break; Ctrl+Enter starts the thread in the background.

Threads are grouped as **Pinned**, **Unread**, **Read** (your seven most recently read), **Running** and **Archived**, ordered by message activity. Rem's worker threads appear here too, labelled with who wrote each instruction (**Rem** or **You**).

Name a thread for its task, pin ongoing work, and archive finished work to keep the active list readable. Archiving marks a thread read, and new activity brings it back. **Settings > General > Delete archived threads** can remove archived threads automatically after 7, 30 or 90 days. **Mark as read** on an unread row, or **Mark all read** on the Unread header, clears the blue dot. Ctrl- or Cmd-click threads to select several, Shift-click to select a range, or click a section's name, such as Unread, to select every thread in it; the chevron still opens and closes Running and Archived. Right-click, press Shift+F10, or use the bar above the list to mark the selection read or unread, pin, archive, restore or delete it. Esc clears the selection. The phone app offers the same actions with a long press. Search threads to find a title or text anywhere in saved history, including older messages that are not currently loaded.

## Switch models when sending

Choose a provider, model, and effort in the composer. Your next message uses that selection. A change adds a divider above the sent message:

```text
switched to Codex - gpt-6.1-sol - high
```

Unsent picker changes add no history entries. Returning to the previously submitted settings adds no divider. Thirty changes followed by one send record only the final selection. The same thread keeps its project, resources, and visible history.

Each provider retains its own native session. Switching back restores that provider's session and supplies the turns it missed. Hidden provider tool state stays with that provider. Native continuation does not promise free historical tokens.

## Read thoughts and results

Rem's answer appears word by word as the provider writes it. When Rem then runs a tool, the text it wrote so far moves into the thoughts above.

When Rem used the web, cards under the reply list the pages it cited, read and found, with titles and thumbnails. YouTube links become video cards. Raticode's backend fetches these previews from public addresses only, never from this computer or your local network. Cards and images open in Raticode's browser. Rem can also show images from pages it read.

Tool activity and intermediate thoughts appear in collapsible groups. **Settings > Rem > Auto-hide thoughts** collapses completed groups after the reply. Reopen them to inspect evidence. Turn summaries show timing and supported file-change information.

## Steer a running response

While Rem works, type a new instruction and choose **Steer**, or press Enter. The draft clears after the server confirms acceptance. Provider selection stays locked during the running response. Steering can interrupt and restart the provider process with the same selected settings.

**Stop** requests cancellation separately. It does not remove completed edits or external effects. Inspect completed work before continuing an interrupted task. If a provider error offers **Resume task**, that is an explicit continuation request.

## Edit, resend, or fork

Edit the latest user message to replace the conversation from that point and send it again. Resending starts a new native branch for the revised history. It does not undo workspace changes from the original turn.

Use **Fork thread from here** on a user message or reply to explore another approach in a separate thread. A user-message fork resends that selected request once. The parent remains intact, and the new thread starts an independent provider session.

See [Attachments](./attachments.md), [Tools, skills and MCP](./resources.md), and [Conversation archive](../knowledge/archive.md).

