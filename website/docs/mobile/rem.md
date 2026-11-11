---
title: Rem on the phone
description: Talk to Rem, follow threads and tasks, attach and share files, use your voice and get alerts on Android.
pageClass: docs-layout
---

# Rem on the phone

Rem's chat, threads and tasks on the phone are the same ones as on your computer. Messages you send from the phone run on the computer, under the same rules as anything you type there. Most of this page needs **Use Raticode from this phone** in the computer's [device settings](./pairing.md#choose-what-the-phone-can-do); thread sync alone gives you the thread list and thread chats.

## Rem's chat and tasks

**Rem** (first on the bottom bar by default) opens to Rem's chat, which looks and works like the desktop's [Rem chat](../rem/chat.md). The top of the screen shows Rem's status, such as "1 thing needs you" or "Working on 2 things"; tap it (it has a **›**) to open Rem's **Tasks**. The arrow at the top left, or the phone's Back gesture, returns to the chat with your unsent message and scroll position as you left them.


- The magnifying glass at the top searches Rem's whole chat on the computer, as [on the desktop](../rem/home.md#search-the-conversation): your messages, Rem's replies, task updates, task titles and file names, newest first, with the count and the matching words highlighted. Tap a result to jump to it. If it is older than the newest messages the phone holds, the phone loads the earlier messages first, opens the message if it is folded and scrolls to the words. The arrow at the top left, or Back, closes search with the chat and your unsent message as they were. Offline, or with a computer that hasn't been updated, search covers only the messages on the phone and says so.
- The phone holds Rem's newest messages. Above them, **Show earlier messages** loads older ones from the computer, a page at a time.
- Long replies fold to a preview with **Show more**, Rem's thoughts fold under **Show thoughts**, and replies can have an expandable **Details** section with tables, code with a Copy button and simple charts.
- Up to three suggested replies sit under Rem's answer; tapping one sends it. Each message's **⋯** menu has **Reply**, Copy and, on Rem's replies, the model and time.
- A board ticket key such as `REM-5` in Rem's chat shows as a pill with the ticket's status mark, key and title, as on the computer. Tap it to open the ticket on the phone's **Global board**. Keys of tickets that aren't on the board, and keys in code, stay as text. Task cards and a task's page list the tickets the task created, worked on or changed; a deleted ticket keeps its key and old title, struck through.
- Work Rem started on its own during self-work shows in the chat with its task cards and updates, with jobs started together grouped under one line. Its phone alerts still come from **Proposals are waiting**, **A task finishes** and **A task needs you**, once each.
- Running tasks show their Plan, Work, Check and Report steps with their checks and time so far. Finished tasks show a card with their checks, saved reports and the web pages the work used. **Open report**, **Retry review** and **Run again** use the same actions as on the computer. Open a task that needs you to see which checks failed or are unresolved, each with the reviewer's reason, as on the computer; passed checks are folded under **Passed**. An older computer shows only the check counts.
- Rem's **Tasks** screen shows how many tasks there are and has **New task**. The list uses the computer's groups: **Working on**, **Needs you** (headed **Updates** when nothing waits on you), **Coming up**, **Paused** and **Done**, which starts folded. Each task's icon and status use the computer's words and colors: a turning icon while it works, a clock while it waits to start, amber when it needs you, red when it didn't finish, a square when it was stopped and a check when it's done. A task's page starts with the same status and each run's outcome. Back from a task returns to wherever you opened it: the task list, or the task's card in the chat. A one-time task leaves the list once it finishes and needs nothing from you; a failed one stays until you dismiss it.
- A task that [waits for other tasks](../rem/home.md#start-a-task-after-another) shows under **Coming up** with what it waits for, and its page lists each of those tasks under **Starts after** with where it stands. When one of them didn't succeed, the task shows **On hold** under **Needs you**, and its page and its chat card offer **Retry “…”** and **Start now**. A request you queued behind other tasks shows **Queued until “…” finished** under it. An older computer doesn't send this, and its tasks look as before.
- A task's work thread shows each instruction from Rem or from you as a labeled card, the worker's steps folded under **Show thoughts**, and each reply in its own card with **Copy Markdown**. Long instructions and earlier long replies fold to a preview with **Show more**; the newest reply starts open.
- Links open in Raticode's [browser](./workspace.md#browser). Links to files on your computer that Rem wrote, such as a report path, download and open on the phone. Only files in an open project or the Second Brain folder can come to the phone.
- **Stop Rem** asks the computer to cancel the current reply.

## Threads

**Threads** shows the desktop's sections: **Pinned**, **Unread**, **Read**, **Running** and **Archived**, with search across saved messages. Long-press a thread to select several, then mark them read or unread, pin, archive, restore or delete them. In a thread, earlier long replies fold to a preview with **Show more** and the newest starts open, as in Rem's chat. Send from the list to start a new thread; the computer names it from your first message. Before sending you can pick Global or a project, the provider, model, effort and permissions, and **Thread settings > Resources** turns the thread's tools, skills and MCP servers on or off.

## Attachments and the camera

The paperclip offers **Photos and videos**, **Camera** (the photo goes into the message and isn't saved to your gallery), and Android's file browser at **Recent files**, **Downloads** or **All files**. Attach up to eight files, 10 MB each. Each shows a thumbnail or file-type tile you can tap to preview. Files Rem sends you appear under its reply with **Download**, **Save** and **Open**. Rem sees attached photos the same way as on the desktop; see [Images in Rem's chat](../rem/attachments.md#images-in-rem-s-chat).

## Ask Rem about something you're reading

**Ask Rem** puts context into Rem's message box without sending it: the project, file path and lines, a commit ID, or a page's title and address, plus any text you selected (up to 2,000 characters). You add your question and send it yourself. It's in the text-selection menu in Files, on files, hunks and commits in Source control, in the browser's menu (**Ask Rem about this page**), and on long-pressed developer log entries. Files that look like they hold secrets offer no Ask Rem.

## Share to Rem

Pick **Rem** in any app's Android share sheet to send a link, text, pictures, videos or PDFs to Rem's chat on your computer. A sheet shows the text (which you can edit) and each file; nothing goes until you tap **Send**, and Send refuses while the phone is locked. Limits: 8 files, 10 MB each, 40 MB together. Shared text can carry instructions you didn't write, so check it before sending: Rem treats everything you send as your request.

## Dictation and the voice assistant

- **Dictate**, in the message box's menu, uses Android's on-device speech recognition (Android 12 or newer with an offline speech model). Nothing is sent to an online speech service.
- With **Tidy voice messages** turned on in the computer's **Settings > Rem**, what you dictate is cleaned up by a fast model (punctuation, filler words, misheard names) before it appears for you to edit. If tidying is off or slow, your words appear as heard.
- **Voice assistant** (Android 12 or newer, set up under **More › Voice assistant**): choose Raticode as the phone's digital assistant app, then hold the power button or use the assist gesture to open Rem's voice panel over any app. The **Talk to Rem** Quick Settings tile opens it too. You check what was heard and tap send; on a locked phone nothing is sent until you unlock.
- **Phone commands in voice mode** run on the phone itself, offline: calls and texts to people you saved, timers, alarms, directions, calendar and email drafts, opening apps, the flashlight, music and volume. Calls and texts show a short countdown with **Cancel**. Anything that isn't a clear command goes to Rem.
- **Phone action cards:** when you ask Rem for a timer, alarm, event, place, call, text or email, its reply can include cards that open your own Clock, Calendar, Maps, dialer, messaging or email app with the details filled in. You save, call or send there. **More › People Rem can reach** keeps the people you've picked from Android's contact picker; their numbers stay on the phone and are never synced.

## Alerts and notifications

In the phone's connection settings under **Background & notifications**:

- **Rem chat notifications** alert you to Rem's replies and task updates on requests from this phone, with a **Reply** button. Alerts stay generic unless you turn on **Show Rem's words**.
- **Also alert me about**: **A task needs you** (any task, wherever it started), **A task finishes**, and **Proposals are waiting** (once per self-work round). These need an up-to-date computer.
- **Quiet hours** silence alerts for a time range without losing them.
- **Desktop response notifications** give a generic alert when any reply arrives.
- While background sync is on, a task you start from the phone shows its progress in one ongoing notification, as a Live Update on Android 16.1 and newer.

Alerts need a connection: the app open, or **Enable background sync** on. There is no push service.
