---
title: Chat and threads
description: Use Rem conversations, steering, search, forks, and provider changes without losing context.
pageClass: docs-layout
---

# Chat and threads

Threads are your own conversations with a coding agent through Rem. Each thread keeps its own conversation, scope, resources and provider settings. For Rem's single ongoing conversation, tasks and worker threads, see [Rem's home and tasks](./home.md).

## Start a conversation

Threads live in the Rem pane. **Settings > Layout > Rem pane shows** switches that pane between **Threads** (the default) and Rem's home.

Choose **New thread**, check the project scope and composer settings, then send.

| Key | Does |
| --- | --- |
| Enter | Send |
| Shift+Enter | New line |
| Ctrl+Enter | Start the thread in the background |

Unsent text in a thread's composer, or in a new thread's, is kept on this computer until you send or clear it, so it's still there after a restart, an update or a reload. Attachments aren't kept. Deleting a thread removes its unsent text.

<AppScreenshot name="rem-thread" caption="A thread in the Rem pane, scoped to its project. Show thoughts expands the steps the agent took; the composer below picks the provider, model and effort." />

## Organize threads

The list groups threads as **Pinned**, **Unread**, **Read** (your seven most recently read), **Running** and **Archived**, ordered by message activity. Rem's worker threads appear here too, labelled with who wrote each instruction (**Rem** or **You**).

- Name a thread for its task, pin ongoing work and archive finished work.
- Archiving marks a thread read; new activity brings it back.
- **Settings > General > Delete archived threads** removes archived threads after 7, 30 or 90 days. A [conversation archive](../knowledge/archive.md) keeps their copies.
- With an archive folder set, **Thread options > Delete permanently** also removes the thread's archive copy. See [Delete permanently](../knowledge/archive.md#delete-permanently).
- **Mark as read** on an unread row, or **Mark all read** on the Unread header, clears the blue dot.
- **Search threads** finds a title or text anywhere in saved history, including older messages not loaded yet.

### Select several threads

- Ctrl- or Cmd-click to pick threads, Shift-click to pick a range.
- Click a section's name, such as Unread, to select everything in it. The chevron still opens and closes Running and Archived.
- Right-click, press Shift+F10, or use the bar above the list to mark the selection read or unread, pin, archive, restore or delete it.
- Esc clears the selection.

The phone app offers the same actions with a long press.

## Rem's long conversation

Rem's own conversation, on its home, opens with its newest 200 messages (from 0.5.5); **Show earlier messages** at the top, or scrolling up to it, loads 40 more at a time. Search there still jumps to any message, back to the first. See [Earlier messages](./home.md#earlier-messages).

## Switch models when sending

Pick a provider, model and effort in the composer. Your next message uses them, and a divider above it records the change:

```text
switched to Codex - gpt-6.1-sol - high
```

- When the provider has accounts, an **Account** menu sits under the picker. Changing it also adds a divider naming the account; see [Choose a thread's account](./providers.md#choose-a-thread-s-account).
- Changing the picker without sending records nothing. Thirty changes then one send record only the last.
- Going back to the settings you last sent with adds no divider.
- The thread keeps its project, resources and visible history.

Each provider keeps its own native session. Switching back to a provider restores its session and gives it the turns it missed. Hidden tool state stays with its provider. Native continuation doesn't make earlier context free of token cost.

## Read thoughts and results

The answer appears word by word as the provider writes it. When Rem then runs a tool, the text so far moves into the thoughts above.

- Tool activity and intermediate thoughts sit in collapsible groups. **Settings > Rem > Auto-hide thoughts** folds finished groups after the reply.
- Turn summaries show timing and, where supported, file changes.
- When Rem used the web, cards under the reply list the pages it cited, read and found, with titles and thumbnails. YouTube links become video cards. Rem can also show images from pages it read.
- Cards and images open in Raticode's browser. The backend fetches previews from public addresses only, never from this computer or your local network.
- A link in a reply, or in a Markdown file, that points to a folder outside the open project asks before opening that folder as a project, because Rem can work in any project you open. The question names the folder the link really leads to, after any symlinks, and Raticode gets no access to it unless you choose **Open project**. Folders opened this way aren't added to recent projects.

The thread follows new text while you're at the bottom. Scroll up to read and it stays where you are; messages that arrive meanwhile are counted on a **new messages** button over the bottom of the conversation, such as **2 new messages**.

- Click the button to jump to the newest message and follow new text again. Scrolling back to the bottom yourself clears it too.
- Earlier messages loaded above what you're reading never count, and neither do thought groups.
- Opening another thread starts the count again.

## Steer a running response

While Rem works, type a new instruction and choose **Steer**, or press Enter.

- The draft clears once the server accepts it.
- The provider picker stays locked during the response.
- Steering can restart the provider process with the same settings.

**Stop** asks the provider to cancel. It doesn't undo finished edits or outside effects, so check what was done before continuing. If a provider error offers **Resume task**, that continues the task on purpose.

## Notifications while Raticode is in the background

When the Raticode window isn't focused, the desktop shows a notification for:

| When | Notification |
| --- | --- |
| A thread's turn finishes | **Reply in** *thread name* |
| A thread's turn fails | **Turn failed in** *thread name* |
| Rem replies in its own chat | **Rem replied** |
| Rem waits on your approval in a browser tab | *Rem* **needs your approval in** *tab name* |
| A Rem task finishes | The result and the task's name |

- Notifications name the thread or tab, never what Rem or the agent wrote, like the phone's default.
- Each turn and each approval request notifies once.
- Clicking one brings Raticode forward and opens that thread, Rem's chat, the tab or the task.
- Turns in Rem's worker threads aren't announced on their own; the task's notification covers them.
- Nothing shows while the window has focus.
- **Settings > General > Notify me when Raticode is in the background** turns all of them off. It's on by default.

Workflow [notification](../nodes/notification.md) nodes and workflow shortcuts send their own notifications, which this switch doesn't change. For alerts on your phone, see [Alerts and notifications](../mobile/rem.md#alerts-and-notifications).

## Edit, resend or fork

Edit your latest message to replace the conversation from that point and send it again. This starts a new native branch for the revised history. It doesn't undo workspace changes from the original turn.

**Fork thread from here**, on a message or reply, tries another approach in a separate thread.

- A fork from your message resends that request once.
- The original thread stays as it was.
- The new thread starts its own provider session.

See [Attachments](./attachments.md), [Tools, skills and MCP](./resources.md), and [Conversation archive](../knowledge/archive.md).
