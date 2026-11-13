---
title: Global board and self-work
description: See Rem's and every organization's tickets on one board, and let Rem work through its own tickets while you're away.
pageClass: docs-layout
---

# Global board and self-work

- The **Kanban** sidebar plugin is one board with Rem's tickets beside every [organization's](../advanced/organizations.md).
- **Self-work** is what Rem does on its own once you've gone quiet. It's set up in **Settings > Rem** and works with or without the board.
- Scripts can use both through [`Raticode tickets` and `Raticode selfwork`](../reference/cli-rem.md#tickets).

## The board

Rem's tickets have keys like `REM-12`. The columns are **Backlog**, **To do**, **In progress**, **In review**, **Blocked**, **Done** and **Cancelled**.

<AppScreenshot name="board" caption="The board after Rem added five tickets for a project. The sidebar shows the self-work state and Rem's latest report." />

- **New ticket** adds a ticket for Rem or an organization. **Search tickets** and the avatars above the board filter it.
- Organization tickets stay owned by their organization. Your changes go through its usual approvals and permissions, and reading the board never starts an organization.
- You can hand one of Rem's tickets to an organization. An organization's ticket can't go to Rem; create a new one instead.
- Rem can create, edit, move and assign tickets when you ask in chat. It can't approve its own proposals or mark a ticket done.
- Rem's replies name tickets by key. A key such as `REM-12` in Rem's chat shows as a pill you can click to open the ticket; see [Ticket pills](./home.md#ticket-pills).

### Tickets follow their task

Once a ticket's task is running, the ticket moves by itself:

| Column | When |
| --- | --- |
| **In progress** | The task is working, or resumes in its thread, is retried, or its review asks for changes |
| **In review** | The work is finished and its independent review is checking it |
| **Blocked** | The task waits on you or an outside event (including a [sleeping](./home.md#sleeping-tasks) task), was stopped, failed or errored, or its review left checks unresolved |
| **Done** | Only when the review accepts the work. Rem can't mark its own tickets done by hand. |

An organization ticket also goes to **Blocked** while it waits for other tickets, when a pause or closing time cuts its run off, or when its review rejects the work.

- A run that's being stopped doesn't move its ticket until it has stopped.
- The same state seen twice never moves a ticket twice.
- If you move a ticket yourself, it stops following its last run.

## History and comments

Open a ticket to see **History and comments** under its details: a timeline, oldest first, with who wrote each entry and when. You can add your own comment at the bottom.

Raticode adds an entry each time a ticket moves to **Blocked**, **In progress**, **In review** or **Done**, or is cancelled. Each entry says why, from the run and review records:

- the question a task is waiting on
- the error a task failed with
- which checks the review left unresolved
- the results the work reported

<AppScreenshot name="ticket-history" caption="A ticket's history: each move with the reason taken from its run or review, the checks that passed or failed, and your comment asking for a change." />

A long result stays folded under **Show the full text**. A review's checks are listed as passed or failed, and **Open its thread** goes to the task's thread.

- Assigning a ticket, or moving it out of Backlog or To do, adds no entry. The exception is an organization worker [pulling a ticket from Backlog](../advanced/organizations.md#pull-from-backlog), which adds a **Pulled into To do** entry.
- The same change seen twice is written once.
- On an organization's ticket, the timeline is that ticket's conversation, so it also shows its workers' messages and reviews. A move whose reason is the worker's report or a review just above names the move without repeating that text.
- Tickets older than this timeline start with an empty one.

## People and projects

The **People** view lists Rem and every organization worker, like members of a ticket tool. Each person has the projects they may work in on their own.

- Only you can change someone's projects, on the computer or a phone.
- Someone with no projects does no self-work.
- Work you ask Rem for directly isn't limited by Rem's projects.
- An organization worker with projects works only in them, including on tickets you give it. See [Workers run like small Rems](../advanced/organizations.md#workers-run-like-small-rems).
- You can also pick Rem's projects under **Rem's projects** in **Settings > Rem**, without the board.

## Self-work

Self-work is on by default but does nothing until Rem has at least one project.

- **It starts** after 15 minutes without a message from you: in Rem's chat, on any device, or in a worker thread. In a [timed window](#timed-self-work) it starts right away.
- **It ends** with your next message. Running work finishes, and nothing new starts.
- **Turn it on or off** in [Settings](#settings), or tell Rem: "turn on self-work", "stop self-work", "is self-work on?". See [Turn it on or off from the chat](#turn-it-on-or-off-from-the-chat).
- **Set a time** by telling Rem: "enable self-work until 7 PM tonight", "self-work from 11 PM to 7 AM". See [Timed self-work](#timed-self-work).

With the board on, Rem fills its free threads, up to the maximum, in this order:

1. **Its own Blocked tickets** in its projects, once per block, since that work was already under way. Rem retries an interrupted review, follows up in the task's thread after a failure, or restarts a ticket that couldn't start. A ticket that needs you (an answer, something you stopped, something outside this computer) stays Blocked, and Rem comments with exactly what's needed.
2. **To do tickets** in its projects, by priority.
3. **Backlog tickets** it ranks as most worth doing, only if **Pull backlog tickets when threads are free** is on (off by default). A ticket Rem judges no longer makes sense stays in Backlog with the reason.
4. **Research** with any threads left. Each run studies one project for new features, reliability and security, or speed and upkeep. It saves a report and proposes tickets.

Proposals wait in Backlog, marked as proposed, until you choose **Approve for To do** or **Decline**. Nothing proposed runs until you approve it.

### Your work comes first

Work you ask for counts against **Max threads** ahead of self-work. When your work and self-work together don't fit, the newest self-work makes room:

- **What counts as yours**: a chat request that starts a task or a follow-up, a task you start or run, a ticket you move to **In progress**, and your scheduled tasks.
- **What pauses**: self-work that hasn't started goes first, then research, then tickets, newest first. Self-work you resumed yourself is left running.
- **How it pauses**: like [pausing a task](./home.md#pause-and-resume-a-task). The run, its thread and any worktree are kept, and it frees its thread. Its card reads **Paused for your work**, and its ticket shows **Blocked** with a comment saying why.
- **When it resumes**: once there's room again and you've been away 15 minutes, before Rem starts anything new. It continues in the same thread, so nothing is done twice. You can also resume it yourself at any time, or move its ticket to **In progress**.

Rem says so once in the chat ("Paused 1 self-work task so your work runs first: …"), and the self-work status and report say how many are paused. A ticket you pause yourself stays paused until you resume it.

### Turn it on or off from the chat

Tell Rem in your own words, typed or spoken, on the desktop or the phone:

| You say | What happens |
| --- | --- |
| "turn on self-work", "start self-work" | Self-work turns on |
| "stop self-work", "turn off self-work", "pause self-work" | Self-work turns off |
| "is self-work on?" | Rem says whether it's on and what it's doing |

Raticode makes the change itself and Rem's reply states the setting as it now is, for example "Self-work is off now, so I won't start anything on my own." It's the same switch as in [Settings](#settings), so the desktop, the phone and `Raticode selfwork status` all show it right away.

- Only the on/off switch changes. The thread limit, backlog pulling and Rem's projects stay as they are, and Rem can't change its own projects.
- Turning it off doesn't stop work already started. Pause or stop those tasks if you want.
- A workflow's Rem prompt can't turn self-work on or off unless its author let the node act.

### Timed self-work

Tell Rem when self-work should run, and it turns on and off by itself:

| You say | What happens |
| --- | --- |
| "enable self-work until 7 PM tonight" | On now, until 7 PM |
| "turn on self-work from 11 PM to 7 AM" | On from 11 PM until 7 AM the next morning |
| "stop self-work at 7 AM" | Self-work keeps going now and turns off at 7 AM |
| "self-work until all running tasks finish" | On until the tasks running now have ended |
| "cancel the self-work window" | Drops the planned window |

- **Time zone.** Times are in the computer's time zone unless you name one, such as "until 7 PM Asia/Irkutsk". Rem's reply and Settings show the zone.
- **Right away.** Inside a window Rem doesn't wait for 15 quiet minutes, unless you ask it to. [Your work still comes first](#your-work-comes-first).
- **Refused times.** Rem says why and changes nothing when a time has already passed, doesn't exist or happens twice because the clocks change, when a window is longer than 7 days, or when it starts more than 7 days ahead.
- **Overlaps.** A window that starts now replaces the end of the one running, making it longer or shorter. A later window that overlaps the running one extends it. A later window that overlaps other planned windows replaces them.
- **Ending early.** Turning the switch off, in Settings, on the phone or by telling Rem "stop self-work", ends the running window now.

When a window ends:

- Nothing new starts. Self-work already started finishes as usual.
- Self-work that was [paused for your work](#your-work-comes-first) stays paused until self-work is on again.
- The switch goes back to what it was, unless you said it should be on or off afterwards.
- Rem posts [what self-work did](#the-report-when-self-work-stops).

### The report when self-work stops

When self-work stops, because a window ended or you turned it off in the chat, Settings, the phone or the CLI, Rem posts one report in its chat. It says when self-work ran and what it started, finished, paused or blocked, with the tickets it created, moved or proposed as [ticket pills](./home.md#ticket-pills). Below it, each task it worked on shows how it went; select one to open it, or its thread if the task is gone.

- The report is built from Raticode's own run and ticket records, not from what Rem remembers.
- Ask "write a report of what you did while self-work ran" at any time. Rem reports the period so far, or the last one.
- It lives in Rem's chat and isn't saved as a file. **Settings > Rem** shows its one-line summary under **Last self-work period**.
- Self-work ending because you sent a message doesn't post one. **While you were away** covers that.

### In Rem's chat

Everything self-work starts shows in Rem's chat, with the same task cards and updates as work you ask for. Each card lists its tickets.

- Several jobs started at once share one message.
- When you move a ticket to **In progress** yourself, its card says so.
- Phone alerts for self-work come once, from the self-work report, not again for each chat update.

### Settings

Self-work's settings are in **Settings > Rem**, under **Self-work**. Settings you chose on the board before are kept.

| Setting | Default | What it does |
| --- | --- | --- |
| **Work on my own after 15 quiet minutes** | On | Turns self-work on or off. Telling Rem works too. |
| **Max threads** | 2 | How many tasks Rem keeps going at once. **Rem task workers** in the same tab caps it. |
| **Pull backlog tickets when threads are free** | Off | Lets Rem start backlog tickets. Needs the board. |
| **Rem's projects** | None | Where Rem may work on its own |

<AppScreenshot name="selfwork-settings" caption="Self-work in Settings > Rem: when Rem starts on its own, how many threads it keeps going, which projects it may touch, and its latest report." />

Under the switch: the [self-work window](#timed-self-work) running now and up to five planned ones, each with when it ends and what the switch does after. **End now** ends the running one and posts its report; **Cancel** drops a planned one. During a window the switch shows on, with a note when it goes back to off afterwards. **Last self-work period** sums up the last [report](#the-report-when-self-work-stops).

Below the settings: what's running now, with links to each thread (paused ones are marked), and the latest report. The report lists tickets added, work done, blocked tickets Rem picked back up and ones still waiting for you.

The board's sidebar shows the same status and report, when a window ends and the next planned one, with **Self-work settings** to open them. Rem's chat shows **While you were away** when the report has something new.

### With the board turned off

You can hide the **Kanban** plugin in **Settings > Plugins** and keep self-work. Rem then leaves tickets alone:

- It doesn't start To do, Backlog or Blocked tickets, rank or merge the backlog, or write proposals.
- It spends its threads on research in its projects and lists the ideas each run finds in the report.
- The research shows in Rem's chat with task cards and updates, which name the ideas found.
- Each project is researched from each angle at most once a day, so quiet spells don't repeat it.
- **While you were away** has **See the report**, which opens **Settings > Rem**.

Switching the board off or on mid-work is safe:

- Ticket work already started finishes, updates its ticket and counts toward the maximum threads.
- Research that finishes after the board is turned off adds its ideas to the report and creates no tickets.
- With the board back on, Rem takes up To do tickets again as threads come free.

### Limits and safety

- Research runs at most 6 times a session. With the board on, it stops while 12 proposals are waiting.
- Each piece of work is an ordinary [Rem task](./home.md#how-work-is-checked) with independent review. A ticket is done only after its run is accepted; a failed run blocks the ticket with the reason.
- Rem restarts a blocked ticket on its own at most once, until you move it. If it blocks again, Rem leaves it for you with a comment.
- Unattended runs can't push, publish, deploy, send messages outside the computer, spend money, change credentials, or start organizations or workflows. `git push` is blocked for them, and they get no browser, subagents, swarms or configured MCP servers.
- Clearing Rem's history removes the self-work report but keeps tickets and settings.

On the phone, the board is under **More › Global board**, and self-work's settings are under **Desktop settings › Rem self-work**; see [Projects, boards and workflows on the phone](../mobile/workspace.md#global-board-and-self-work).
