---
title: Rem on the phone
description: Talk to Rem, follow threads and tasks, attach and share files, use your voice and get alerts on Android.
pageClass: docs-layout
---

# Rem on the phone

Rem's chat, threads and tasks on the phone are the same ones as on your computer. Messages you send from the phone run on the computer, under the same rules as anything you type there.

Most of this page needs **Use Raticode from this phone** in the computer's [device settings](./pairing.md#choose-what-the-phone-can-do). Thread sync alone gives you the thread list and thread chats.

## Rem's chat

**Rem** (first on the bottom bar by default) opens Rem's chat, which works like [Rem's chat on the desktop](../rem/chat.md).

- The top shows Rem's status, such as "1 thing needs you" or "Working on 2 things". Tap it (it has a **›**) to open Rem's **Tasks**.
- The arrow at the top left, or Back, returns to the chat with your unsent message and scroll position kept.
- The brain icon next to the magnifying glass opens [What Rem remembers](../rem/home.md#what-rem-remembers), the same list as on the computer.
- With the keyboard open, the message box sits on the keyboard and the chat stays above it, showing the newest message if you were at the end. Long messages grow the box to four lines, then it scrolls.
- The phone holds Rem's newest messages. **Show earlier messages** loads older ones from the computer, a page at a time.
- **Stop Rem** asks the computer to cancel the current reply.

### Search the chat

The magnifying glass searches Rem's whole chat on the computer, as [on the desktop](../rem/home.md#search-the-conversation): your messages, Rem's replies, task updates, task titles and file names, newest first, with matches highlighted.

- Tap a result to jump to it. Older messages load first if needed, and folded messages open.
- Back, or the arrow at the top left, closes search and leaves the chat as it was.
- Offline, or with an older computer, search covers only the messages on the phone and says so.

### Replies

- Long replies fold to a preview with **Show more**. Rem's thoughts fold under **Show thoughts**.
- A reply can have a **Details** section with tables, code (with **Copy**) and simple charts.
- Up to three suggested replies sit under Rem's answer. Tap one to send it.
- Each message's **⋯** menu has **Reply** and Copy. On Rem's replies it also shows the model and time.
- Links open in Raticode's [browser](./workspace.md#browser). Links to files Rem wrote on your computer, such as a report, download and open on the phone. Only files in an open project or the Second Brain folder can come to the phone.

### Tickets

A board ticket key such as `REM-5` shows as a pill with the ticket's status, key and title, as on the computer. Tap it to open the ticket on the phone's **Global board**.

- Keys of tickets not on the board, and keys inside code, stay plain text.
- Task cards and task pages list the tickets the task created, worked on or changed. A deleted ticket keeps its key and old title, struck through.

## Tasks

Rem's **Tasks** screen shows how many tasks there are and has **New task**. Tasks are grouped as on the computer:

<AppScreenshot name="phone-rem" phone caption="Rem's Tasks on the phone use the computer's groups and status colors. Demo content from a test desktop." />

| Group | Shows |
| --- | --- |
| **Working on** | Tasks running now, with their Plan, Work, Check and Report steps, checks and time so far |
| **Needs you** | Tasks waiting on you. Headed **Updates** when nothing waits on you. |
| **Coming up** | Scheduled tasks, and tasks waiting for other tasks |
| **Paused** | Paused runs (**Paused**, or **Paused for your work**) and paused schedules |
| **Done** | Finished tasks. Starts folded. |

Each task's icon uses the computer's words and colors: a turning icon while it works, a clock while it waits to start, amber when it needs you, red when it didn't finish, a square when stopped and a check when done.

- **Finished tasks** show a card with their checks, saved reports and the web pages the work used. **Open report**, **Retry review** and **Run again** work as on the computer.
- **Pause** and **Resume** on a task's page pause the run going and resume it in the same thread, as on the computer. **Pause schedule** and **Resume schedule** are for repeating tasks. A paused run's page says why it waits, and its chat card shows the step it reached with ⏸. See [Pause and resume a task](../rem/home.md#pause-and-resume-a-task). An older computer doesn't offer pausing a run.
- **A task that needs you** shows each failed or unresolved check with the reviewer's reason. Passed checks fold under **Passed**. An older computer shows only the counts.
- **A [sleeping](../rem/home.md#sleeping-tasks) task** shows a moon under **Working on**, what it waits for and its next check. Its page shows its wakes and wait budget, with **Wake now**. An older computer shows only why it is waiting, without **Wake now**.
- **Back** from a task returns to where you opened it: the list, or the task's card in the chat.
- A one-time task leaves the list once it finishes and needs nothing from you. A failed one stays until you dismiss it.

### Tasks that wait for others

A task that [waits for other tasks](../rem/home.md#start-a-task-after-another) shows under **Coming up** with what it waits for. Its page lists them under **Starts after**, with where each stands.

- If one of them didn't succeed, the task shows **On hold** under **Needs you**, with **Retry “…”** and **Start now**.
- [Other start conditions](../rem/home.md#start-at-a-time-or-after-what-is-running), such as a time, "after everything running is done" or the end of a self-work window, are listed too. Tasks running when you asked open when tapped; times and windows can't be opened or retried.
- A request you queued behind other tasks shows **Queued until “…” finished**.
- An older computer doesn't send this, and its tasks look as before.

### Work threads and self-work

- A task's work thread shows each instruction from Rem or you as a labeled card, the worker's steps under **Show thoughts**, and each reply in its own card with **Copy Markdown**. Long items fold to **Show more**; the newest reply starts open.
- Tell Rem "turn on self-work", "stop self-work" or "is self-work on?", typed or by voice, and the reply says what it is now. See [Turn it on or off from the chat](../rem/board.md#turn-it-on-or-off-from-the-chat).
- Tell Rem "self-work until 7 PM" or "self-work from 11 PM to 7 AM" to set a [timed window](../rem/board.md#timed-self-work). **Desktop settings › Rem self-work** shows the running window, planned ones and the last period; the switch ends a running window. Set or cancel windows in the chat or on the computer.
- When self-work stops, Rem's [report](../rem/board.md#the-report-when-self-work-stops) shows in the chat with each task and how it went. Tap one to open it.
- When you ask for work while self-work fills Rem's threads, self-work pauses to make room; its cards read **Paused for your work**. See [Your work comes first](../rem/board.md#your-work-comes-first).
- Work Rem started on its own during self-work shows in the chat with its task cards, and jobs started together are grouped under one line. Its alerts come from **Proposals are waiting**, **A task finishes** and **A task needs you**, once each.

## Threads

**Threads** has the desktop's sections: **Pinned**, **Unread**, **Read**, **Running** and **Archived**, with search across saved messages.

- **Select several:** long-press a thread, then mark them read or unread, pin, archive, restore or delete them.
- **Start a thread:** send from the list. The computer names it from your first message. Before sending, pick Global or a project, and the provider, model, effort and permissions.
- **Thread tools:** **Thread settings > Resources** turns the thread's tools, skills and MCP servers on or off.
- In a thread, earlier long replies fold to **Show more**, and the newest starts open.

## Attachments and the camera

The paperclip offers:

- **Photos and videos**
- **Camera**: the photo goes into the message and isn't saved to your gallery
- Android's file browser at **Recent files**, **Downloads** or **All files**

Attach up to eight files, 10 MB each. Tap a thumbnail to preview it. Files Rem sends appear under its reply with **Download**, **Save** and **Open**. Rem sees photos as it does on the desktop; see [Images in Rem's chat](../rem/attachments.md#images-in-rem-s-chat).

## Long text

A Rem message from the phone holds up to 4,000 characters. Longer text goes as a text file attached to the message, so it doesn't use up the limit.

- **Pasting** 2,000 characters or more into Rem's message box attaches them as a file named after their first words, such as `pasted-text-meeting-notes-from-tuesday.txt`. A shorter paste that would take the message past 4,000 characters is attached the same way. Anything you typed stays in the box.
- The file shows as a chip with its size. Under it are the text's first words and its length. **Put back** returns the text to the message box if it fits, and the chip's **×** removes it.
- From 3,500 characters, a counter shows under the box, such as "3,612 / 4,000". **Attach as file** moves everything in the box into a `long-message` file.
- Text sent to the box from another screen with **Ask Rem** follows the same rules.
- In the [voice assistant](#voice), spoken messages of 2,000 characters or more go as a `spoken-text` file. The panel shows their first words and length. Dictation over 4,000 characters is too long for the computer to tidy, so you get it as heard and tap send.
- Rem reads the file like any text file attached on the computer; see [Attach files](../rem/attachments.md#attach-files). The message shows the file in Rem's chat on the phone and on the computer, and you can tap it to read the text.

A long text file can't be attached while the phone isn't connected to the computer, when the message already has eight files, or when the computer's Raticode is too old to take files in Rem's chat. In those cases the box stops at 4,000 characters and says how many weren't added and why. A voice message that still fits in one message is sent as normal text. A longer one isn't sent; the panel says why.

## Ask Rem about something you're reading

**Ask Rem** puts context into Rem's message box without sending it: the project, file path and lines, a commit ID, or a page's title and address, plus any text you selected (up to 2,000 characters). Add your question and send.

It's in the text-selection menu in Files, on files, hunks and commits in Source control, in the browser menu (**Ask Rem about this page**), and on long-pressed developer log entries. Files that look like secrets don't offer it.

## Share to Rem

Pick **Rem** in any app's share sheet to send a link, text, pictures, videos or PDFs to Rem's chat on your computer.

- A sheet shows the text, which you can edit, and each file. Nothing goes until you tap **Send**.
- **Send** doesn't work while the phone is locked.
- Limits: 8 files, 10 MB each, 40 MB in total.
- A file whose name doesn't match its type gets the type's extension added, so a picture named `run.exe` reaches Rem as `run.exe.png`.

Shared text can contain instructions you didn't write. Check it before sending: Rem treats everything you send as your request.

## Voice

| Feature | What it does |
| --- | --- |
| **Dictate** | In the message box's menu. Uses Android's on-device speech recognition (Android 12 or newer, with an offline speech model). Nothing goes to an online speech service. |
| **Tidy voice messages** | Turn it on in the computer's **Settings > Rem**. A fast model fixes punctuation, filler words and misheard names before the text appears for you to edit. If it's off or slow, you get your words as heard. |
| **Voice assistant** | Android 12 or newer, set up under **More › Voice assistant**. Make Raticode the phone's digital assistant, then hold the power button or use the assist gesture to open Rem's voice panel over any app. The **Talk to Rem** Quick Settings tile opens it too. You check what was heard and tap send. On a locked phone nothing is sent until you unlock. |
| **Phone commands** | In voice mode, these run on the phone, offline: calls and texts to people you saved, timers, alarms, directions, calendar and email drafts, opening apps, the flashlight, music and volume. Calls and texts show a short countdown with **Cancel**. Texts go straight out only while Raticode is the phone's digital assistant, as Google Play's SMS rules require; otherwise the text opens as a draft in your messages app. Anything else goes to Rem. |
| **Phone action cards** | When you ask Rem for a timer, alarm, event, place, call, text or email, its reply can include cards that open your Clock, Calendar, Maps, dialer, messages or email app with the details filled in. You save, call or send there. |

**More › People Rem can reach** keeps the people you picked from Android's contact picker. Their numbers stay on the phone and are never synced.

## Alerts and notifications

In the phone's connection settings, under **Background & notifications**:

- **Rem chat notifications:** Rem's replies and task updates on requests from this phone, with a **Reply** button. Alerts stay generic unless you turn on **Show Rem's words**.
- **Also alert me about:** **A task needs you** (any task, wherever it started), **A task finishes**, and **Proposals are waiting** (once per self-work round). These need an up-to-date computer.
- **Quiet hours:** silence alerts for a time range without losing them.
- **Desktop response notifications:** a generic alert when any reply arrives.
- **Progress:** with background sync on, a task you start from the phone shows its progress in one ongoing notification, as a Live Update on Android 16.1 and newer.

Alerts need a connection: the app open, or **Enable background sync** on. There's no push service.
