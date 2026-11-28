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
- The **Decisions** button next to it opens [Rem's decisions](../rem/home.md#rem-s-decisions), with a badge for how many wait for you. **Waiting** shows each question with Rem's recommended answer, a line under it saying who asked it, when, and what waits on it (such as **Asked by REM-249 · 21 Nov · REM-249 waits for this**; tap it to open the task), one evidence line, and **Use this** (or **Keep** for one Rem decided in auto mode), **Change** and **Turn down**. **Decided** has **Edit answer**, **Ask again** (a new question for you while the answer stays in force; its card shows **Current answer** and **Keep current answer**), **Look again** (a [project scan](../rem/home.md#how-rem-finds-the-answers) of that decision's project, for its check command and landing branch; anything it finds waits for you and the decision stays) and **Forget**, and a decided check command shows Raticode's last run of it; **History** lists replaced and turned-down ones. Questions past a project's three waiting cards show under **Waiting** as one line, such as **2 more queued**, and each gets its card when one of those is settled. Questions from a [Plan with me](../rem/home.md#plan-with-me) plan show under the plan's title with how many are answered, such as **2 of 5 answered**, and how many more come up as you answer. Changes are applied on the computer; if the list changed there first, the phone says so and shows it again. A task update in Rem's chat that asks you something carries the same card with **Use this** and **Change**. New in Raticode 0.6.0 and Raticode Mobile 0.36.0.
  - Design decisions, the ones Raticode keeps as Markdown files in the project's repository, have their own group in **Decided**, below **How Rem works here**. **Open** shows the decision first, then the paths it applies to, what a change must never add (**Never:**), its file, and **Context**, **Options we didn't choose**, **Consequences** and **How we check it**, each folded to its first sentence. It also says when the file was edited outside Raticode, when a Rem task changed it and Raticode puts the approved text back once the checkout is clean, or why it isn't in the repository yet. Below that come the evidence, Raticode's last run of a decided check command, and **History**: the newest 10 changes, newest first, each with its date, who acted (**You**, **Rem** or **Raticode**), what happened and the note. A decided entry under **How Rem works here** has **History (n)**, which opens the same list on its card. **Edit** changes any part, with the paths as a comma-separated list, and **Replace with…** records your new decision in its place. When Rem finds decisions that conflict, its one fix waits in **Waiting** and says which decisions it replaces and why; **Use this** replaces them all. New in Raticode 0.6.0 and Raticode Mobile 0.36.0.
  - When you ask Rem to change a decision that's already made, its reply carries a card with the decision's before and after for each part that changes, and **OK** and **Cancel**. Nothing changes until you press **OK**, and the computer applies it only if the decision hasn't changed since. The changes still waiting also show in **Waiting** on the Decisions screen. See [Ask Rem to change a decision](../rem/home.md#ask-rem-to-change-a-decision). New in Raticode 0.6.0 and Raticode Mobile 0.36.0.
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
- Files a Rem message or a worker's reply in a work thread links to also show as tiles at the bottom of the message, like the files you attach. Tap one to fetch it from the computer, the same as tapping its link. Nothing comes to the phone until you tap. See [Files Rem links to](../rem/attachments.md#files-rem-links-to). *New in Raticode 0.6.2 and Raticode Mobile 0.38.0.*

### Tickets

A board ticket key such as `REM-5` shows as a pill with the ticket's status, key and title, as on the computer. Tap it to open the ticket on the phone's **Global board**.

- In a table, a ticket shows only its key, as a link, so it stays inside its column. Tap it to open the ticket. TalkBack still reads its title and status.
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
- **A task that needs you** shows each failed or unresolved check with the reviewer's reason. Passed checks fold under **Passed**, and the review's notes under **Notes**, each with its severity, axis and file, such as "Note · standards · src/app.py". An older computer shows only the counts, without **Notes**.
- **A [sleeping](../rem/home.md#sleeping-tasks) task** shows a moon under **Working on**, what it waits for and its next check. Its page shows its wakes and wait budget, with **Wake now**. An older computer shows only why it is waiting, without **Wake now**.
- **A waiting task a paired desktop has room for** shows *Desktop B has room: Run there* on its page, with **Run there**. Tapping it runs the task on that desktop, as **Run there** does on the computer, and counts as your own start. The same goes for an organization ticket on its screen, when you already granted its worker a fleet execution target there; otherwise the ticket says what to grant and offers no button. Nothing goes to a paired desktop until you tap. An older computer shows no suggestion. See [Run waiting work on a paired desktop](../rem/home.md#run-waiting-work-on-a-paired-desktop).
- **A task's page** shows its kind, schedule, project and model as tiles under its status. A Custom schedule reads in words, such as "Weekdays at 9:00", with its time zone; an unusual one shows its cron expression, and an older computer shows **Custom schedule**. **Done when** lists its checks with check marks. Its prompt is under **Prompt**, folded when long.
- **Back** from a task returns to where you opened it: the list, or the task's card in the chat.
- A one-time task leaves the list once it finishes and needs nothing from you. A failed one stays until you dismiss it.
- **Dismiss all** next to the **Needs you** (or **Updates**) heading dismisses every task update at once, with how many in its label. Tasks that ask you a question or are **On hold** stay. See [Dismiss all updates](../rem/home.md#dismiss-all-updates). An older computer doesn't offer it.

### Tasks that wait for others

A task that [waits for other tasks](../rem/home.md#start-a-task-after-another) shows under **Coming up** with what it waits for. Its page lists them under **Starts after**, with where each stands.

- If one of them didn't succeed, the task shows **On hold** under **Needs you**, with **Retry “…”** and **Start now**.
- [Other start conditions](../rem/home.md#start-at-a-time-or-after-what-is-running), such as a time, "after everything running is done" or the end of a self-work window, are listed too. Tasks running when you asked open when tapped; times and windows can't be opened or retried.
- A request you queued behind other tasks shows **Queued until “…” finished**.
- An older computer doesn't send this, and its tasks look as before.

### Work threads and self-work

- A task's work thread shows each instruction from Rem or you as a labeled card, the worker's steps under **Show thoughts**, and each reply in its own card with **Copy Markdown**. Long items fold to **Show more**; the newest reply starts open. Files a reply links to show as tiles under it, even while it's folded, including the screenshots and logs a sandboxed worker saved in its [evidence folder](../rem/attachments.md#where-workers-save-evidence). *Tiles are new in Raticode 0.6.2 and Raticode Mobile 0.38.0.*
- Tell Rem "turn on self-work", "stop self-work" or "is self-work on?", typed or by voice, and the reply says what it is now. See [Turn it on or off from the chat](../rem/board.md#turn-it-on-or-off-from-the-chat). "Start self-work now" starts it without waiting for quiet minutes; see [Start self-work now](../rem/board.md#start-self-work-now) (needs Raticode 0.6.1 or later on the computer).
- Tell Rem "self-work until 7 PM" or "self-work from 11 PM to 7 AM" to set a [timed window](../rem/board.md#timed-self-work). **Desktop settings › Rem self-work** shows the running window, planned ones and the last period; the switch ends a running window. Set or cancel windows in the chat or on the computer.
- **When to stop**, on the same page, shows the [stop condition and latest time](../rem/board.md#stop-condition-and-latest-time) of the self-work running now and Rem's last check. **Edit** sets the condition in your words and a latest time (24-hour, with Next, Today or Tomorrow), and **Clear** removes both. Telling Rem "stop when REM-12 is done, but no later than midnight" works too. A desktop without this shows nothing. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- When self-work stops, Rem's [report](../rem/board.md#the-report-when-self-work-stops) shows in the chat with each task and how it went. Tap one to open it.
- When you ask for work while self-work fills Rem's threads, the lowest-priority self-work task pauses for each task of yours that doesn't fit; its cards read **Paused for your work**, and it picks up again as soon as a thread is free. See [Your work comes first](../rem/board.md#your-work-comes-first).
- Work Rem started on its own during self-work shows in the chat with its task cards, and jobs started together are grouped under one line. Its alerts come from **Proposals are waiting**, **A task finishes** and **A task needs you**, once each.

## Threads

**Threads** has the desktop's sections: **Pinned**, **Unread**, **Read**, **Running** and **Archived**, with search across saved messages.

- **Select several:** long-press a thread, then mark them read or unread, pin, archive, restore or delete them.
- **Delete permanently:** in a thread's menu, removes the thread and its copy in the computer's [conversation archive](../knowledge/archive.md#delete-permanently). It shows when the computer's Raticode supports it and **Use Raticode from this phone** is on. Copies already committed to Git stay.
- **Start a thread:** send from the list. The computer names it from your first message. Before sending, pick Global or a project, and the provider, model, effort and permissions.
- **Account:** when the provider has accounts on the computer, an **Account** menu under the model picks Rem's setting, the default sign-in or one account, for a new thread or an open one. Accounts that need sign-in are shown but can't be picked. **Sign in** under a Codex or Grok account signs it in from the phone with a link and a one-time code; see [Sign an account in from the phone](../rem/providers.md#sign-an-account-in-from-the-phone). A Claude Code account still signs in on the computer. See [Choose a thread's account](../rem/providers.md#choose-a-thread-s-account). *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Thread tools:** **Thread settings > Resources** turns the thread's tools, skills and MCP servers on or off.
- In a thread, earlier long replies fold to **Show more**, and the newest starts open.

## Attachments and the camera

The paperclip offers:

- **Photos and videos**
- **Camera**: the photo goes into the message and isn't saved to your gallery
- Android's file browser at **Recent files**, **Downloads** or **All files**

Attach up to eight files, 10 MB each. Tap a thumbnail to preview it. Files Rem sends appear under its reply with **Download**, **Save** and **Open**. **Open** hands the file to the phone's default app for its type, such as a PDF reader for a PDF report. If you made Raticode the default for that type in [Open with Raticode](./workspace.md#open-files-from-other-apps), the report opens in Raticode's own viewer. When no app on the phone opens the type, Raticode shows the file itself if it can, and otherwise says so and offers **Save instead**. Rem sees photos as it does on the desktop; see [Images in Rem's chat](../rem/attachments.md#images-in-rem-s-chat).

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

### Share to a workflow

The share sheet also has **Run a workflow**, which puts a shared link or text into one of your workflows' text inputs instead of Rem's chat. You pick the workflow and the input, the phone remembers the input for that workflow, and the workflow's Run sheet opens filled in. Nothing runs until you tap **Run**. Your three most recent workflows appear as direct-share shortcuts. It takes a link, text or one file, and needs Raticode Mobile 0.36.0 and Raticode 0.6.0. See [Share to a workflow](workspace.md#share-to-a-workflow).

The same caution applies: shared text can contain instructions you didn't write, and the workflow treats it as your input. Read it in the Run sheet before you run.

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
- **Also alert me about:** **A task needs you** (any task, wherever it started, and Rem's questions that wait for you in [auto mode](../rem/home.md#auto-mode-decides-for-you): "Rem has a question", which opens **Decisions** on it), **A task finishes**, **Proposals are waiting** (once per self-work round), and **Workflow messages**, which a workflow sends with a [notification](../nodes/notification.md#phone-notices) node set to `channel: phone`. These need an up-to-date computer. Workflow messages also carry Raticode's own notices, such as a trigger's [daily limit](../workflows/triggers.md) and the computer's [usage limit alerts](../rem/usage.md#limit-alerts) (new in 0.6.0), grouped under "Usage alerts".
- **Workflow messages:** each one is its own alert that opens the run in **Workflows** when you tap it. Messages from the same workflow are grouped under one summary, such as "4 messages from Nightly backup", and only the summary makes a sound, once per sync. A run that sends more than 20 messages shows one "And N more from this run" alert for the rest. If your computer deleted messages before the phone read them (it keeps the newest 500), a quiet alert says how many were missed. It says only "A workflow sent a message" unless **Show Rem's words** is on, when it shows the message's title and text. A run from a trigger while the computer's window is closed reaches the phone at its next sync. Workflow messages are new in Raticode Mobile 0.35.0 and need Raticode 0.5.5 on the computer.
- **Quiet hours:** silence alerts for a time range without losing them.
- **Desktop response notifications:** a generic alert when any reply arrives.
- **Progress:** with background sync on, a task you start from the phone shows its progress in one ongoing notification, as a Live Update on Android 16.1 and newer. See [Task progress](#task-progress).

Alerts need a connection: the app open, or **Enable background sync** on. There's no push service.

### Task progress

While **Enable background sync** is on and a task you started from this phone is running, one ongoing notification shows its steps, **Plan**, **Work**, **Check** and **Report**, and how long it has been running. It updates only when the step changes.

- **Which tasks:** a task Rem starts in answer to a message you sent from this phone, and **Run now**, **Retry review** or a new task from the phone's task screens. Tasks Rem starts on its own (self-work), scheduled runs and tasks started on the computer never appear. If several of your tasks run at once, it shows the newest and says how many there are.
- **Android 16.1 and newer:** it's a Live Update, with the current step in a chip in the status bar. You can turn Live Updates off for Raticode in Android's notification settings.
- **Android 16.0 and older**, such as a Galaxy S20 on Android 13: an ordinary ongoing notification with the same steps and time.
- **What it says:** "Rem is working on your task". It names the task only when **Rem chat notifications** and **Show Rem's words** are both on, and the lock screen never names it.
- **Hiding it:** tap **Hide**, or swipe it away where Android allows. It stays away for that task, even after the app restarts. Your next task shows it again. To turn it off for good, switch off Android's **Task progress** notification category for Raticode.
- **When it goes away:** the task finishes or pauses, background sync is turned off, or the phone can't reach the computer for two minutes. It comes back if the task is still running when the phone reconnects.

While one of your tasks is running, and for up to 10 minutes after you ask for one, the phone checks on it every 15 seconds. The rest of the time it adds nothing to background sync. It needs notification permission and no update on the computer.
