---
title: Global board and self-work
description: See Rem's and every organization's tickets on one board, and let Rem work through its own tickets while you're away.
pageClass: docs-layout
---

# Global board and self-work

The **Kanban** sidebar plugin shows one board with Rem's tickets beside every [organization's](../advanced/organizations.md). **Self-work** is what Rem does on its own once you've gone quiet. It's set up in **Settings > Rem** and works whether or not the board is turned on. Scripts can use both through [`Raticode tickets` and `Raticode selfwork`](../reference/cli-rem.md#tickets).

## The board

Rem's tickets have keys like `REM-12` and move through **Backlog**, **To do**, **In progress**, **In review**, **Blocked**, **Done** and **Cancelled**. Organization tickets stay owned by their organization: changes you make on the board go through the organization's usual approvals and permissions, and reading the board never starts an organization.

Once a ticket's task is running, the ticket follows it on its own:

- **In progress** while the task works, and again when it resumes in its thread, is retried, or its review asks for changes.
- **In review** when the work is finished and its independent review is checking it.
- **Blocked** whenever the task is waiting on something or can't go on: it needs your answer, it waits for an outside event before the review checks again, it was stopped, it failed or errored, or the review left checks unresolved. An organization ticket also goes to Blocked while it waits for other tickets, when a pause or closing time cuts its run off, or when its review rejects the work.
- **Done** only when the review accepts the work. Rem can't mark its own tickets done by hand.

A run that is being stopped doesn't move its ticket until it has stopped, and the same state seen again never moves a ticket twice. If you move a ticket yourself, it stops following its last run.

- **New ticket** adds a ticket for Rem or an organization. **Search tickets** and the avatars above the board filter it.
- You can hand one of Rem's tickets to an organization. An organization's ticket can't be handed to Rem; create a new one instead.
- Rem can create, edit, move and assign tickets when you ask it in chat, but it can't approve its own proposals or mark a ticket done. Its reply names each ticket by key, which shows as a pill you can click to open it here.
- Typing a ticket's key in Rem's chat, such as `REM-12`, shows it as a pill with the ticket's title; see [Talk to Rem](./home.md#talk-to-rem).

## History and comments

Open a ticket to see **History and comments** under its details: a timeline, oldest first, with who wrote each entry and when.

Raticode adds an entry each time a ticket moves to **Blocked**, back to **In progress**, to **In review**, to **Done**, or is cancelled. Each entry says why, from the run and review records: the question a task is waiting on, the error a task failed with, which checks the review left unresolved, or the results the work reported. A long result stays folded under **Show the full text**, a review's checks are listed as passed or failed, and **Open its thread** goes to the task's thread. Assigning a ticket, and moving it out of Backlog or To do, add no entry. The same change seen twice is written once.

You can add your own comment at the bottom. On an organization's ticket, the timeline is that ticket's conversation, so it also shows its workers' messages and reviews. Tickets that existed before this timeline start with an empty one.

## People and projects

The **People** view lists Rem and every organization worker, like members of a ticket tool. Each person has the projects they may work in on their own. Only you can change these, on the computer or a phone. Someone with no projects does no self-work. Work you ask Rem for directly isn't limited by Rem's assignments. An organization worker with projects works only in them, including tickets you give it; see [Workers run like small Rems](../advanced/organizations.md#workers-run-like-small-rems). Rem's own projects can also be chosen under **Rem's projects** in **Settings > Rem**, so you don't need the board for them.

## Self-work

Self-work is on by default but does nothing until Rem has at least one assigned project. It starts after 15 minutes without a message from you (in Rem's chat, on any device, or in a worker thread). Your next message ends the session: running work finishes, and nothing new starts.

With the board on, Rem fills its free threads in a session, up to the maximum, in this order:

1. Its own **Blocked** tickets in its assigned projects, since that work was already under way. Rem checks each one's records once per block. If it can do something, it does it through the task's usual path: it retries a review that was interrupted, follows up in the task's thread after a failure, or starts a ticket again that couldn't start before. If the ticket needs you (an answer, something you stopped, or something outside this computer), it stays Blocked and Rem comments with exactly what's needed.
2. **To do** tickets in its assigned projects, by priority.
3. If **Pull backlog tickets when threads are free** is on (off by default), the backlog tickets it ranks as most worth doing. A ticket Rem judges no longer makes sense stays in Backlog with the reason.
4. Research with any threads left: each run studies one assigned project for new features, reliability and security, or speed and upkeep, saves a report, and proposes tickets.

Proposals wait in Backlog, marked as proposed, until you choose **Approve for To do** or **Decline**. Nothing proposed runs until you approve it.

Everything self-work starts also shows in Rem's chat, with the same task cards and updates as work you ask for, each card listing its tickets. Several jobs started at once share one message. When you move a ticket to **In progress** yourself, its card says so. Alerts on the phone for self-work still come once, from the self-work report, not again for each chat update.

### Settings

Self-work's settings are in **Settings > Rem**, under **Self-work**. Settings you chose on the board before are kept.

- **Work on my own after 15 quiet minutes** turns self-work on or off.
- **Max threads** (2 by default) is how many tasks Rem keeps going at once. **Rem task workers** in the same tab caps it.
- **Pull backlog tickets when threads are free** (off by default). It needs the board.
- **Rem's projects** are where Rem may work on its own.
- What's running now, with links to each thread, and the latest report of tickets added, work done, blocked tickets Rem picked back up and ones still waiting for you.

The board's sidebar shows the same status and report, with **Self-work settings** to open them. Rem's chat shows **While you were away** when the report has something new.

### With the board turned off

You can hide the **Kanban** plugin in **Settings > Plugins** and keep self-work. With the board off, Rem leaves tickets alone: it doesn't start To do, Backlog or Blocked tickets, rank or merge the backlog, or write proposals. It uses its threads for research tasks in its projects instead, and lists the ideas each one finds in the report. The research tasks show in Rem's chat with their task cards and updates, like everything self-work starts; the update names the ideas found. Each project is researched from each angle at most once a day, so quiet spells don't repeat it. **While you were away** then has **See the report**, which opens **Settings > Rem**.

Turning the board off or on while Rem is working is safe. Ticket work that already started finishes and updates its ticket, and still counts toward the maximum threads. Research that finishes after the board is turned off adds its ideas to the report and doesn't create tickets. With the board back on, Rem takes up To do tickets again as threads come free.

### Limits and safety

- Research runs at most 6 times a session and, with the board on, stops while 12 proposals are waiting.
- Each piece of work is an ordinary [Rem task](./home.md#how-work-is-checked) with independent review. A ticket is done only after its run is accepted. A failed run blocks the ticket with the reason. Rem gets a blocked ticket going on its own at most once, until you move it; if it blocks again, Rem leaves it for you with a comment instead of retrying.
- Unattended runs can't push, publish, deploy, send messages outside the computer, spend money, change credentials, or start organizations or workflows. `git push` is blocked for them, and they get no browser, subagents, swarms or configured MCP servers.
- Clearing Rem's history removes the self-work report but keeps tickets and settings.

On the phone, the same board is under **More › Global board**, and self-work's settings are under **Desktop settings › Rem self-work**; see [Projects, boards and workflows on the phone](../mobile/workspace.md#global-board-and-self-work).
