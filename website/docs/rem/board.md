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

- **New ticket** adds a ticket for Rem or an organization. A ticket for Rem can get a **Done when** right away, one check per line. Once you've typed or chosen something in it, a click outside leaves it open and Escape asks before discarding it. **Search tickets** and the avatars above the board filter it.
- Click a ticket, or focus it and press Enter or Space, to open it. Keyboard focus moves into the open ticket, and the ticket stays highlighted on the board. Escape or **×** closes it and puts focus back on that ticket, or on the board's first ticket if it has left the board. Escape doesn't close it while you're typing a reason to decline it. A screen reader reads each ticket's key and title, then the lines it shows, such as why it's blocked, its objective and **Your approval**.
- **Edit a ticket.** An open ticket of Rem's shows its **Priority** and **Kind** (Build, Research or Maintain). While it isn't running or paused, and is in **Backlog**, **To do** or **Blocked**, **Edit** at the top changes its **Title**, **Description**, **Priority**, **Kind** and **Done when** (one check per line). Proposals can be edited too, before you approve them. **Save** sends only the fields you changed. If the ticket changed since you opened the form, or a run started on it, Raticode says why and keeps what you typed. Fields you didn't touch follow the newest copy of the ticket. If a field you changed was also changed somewhere else, such as by Rem or on the phone, the form says so and **Save** is refused instead of undoing that change. Escape closes the form. With unsaved changes, Escape, **Cancel**, **×** or opening another ticket asks before discarding them. A description longer than the board shows (4,000 characters) stays as it is. An organization's tickets are edited in their own [Organizations](../advanced/organizations.md) sheet. On the phone it's **Edit** on the ticket's page; see [A ticket's page](../mobile/workspace.md#a-ticket-s-page). New in Raticode 0.6.2 and Raticode Mobile 0.38.0.
- **Formatted text.** An open ticket shows its description, **Why Rem suggests this** and **Result** as formatted text, like Rem's chat: code, bold, lists and links. Images don't load there; an image shows as its description. A description longer than about 12 lines starts folded, with **Show all** under it, so **Done when**, **Waits for** and **Move** stay in view. An organization ticket's **Details** and an epic's **About** are formatted the same way. Titles stay plain text. New in 0.6.1.
- **Undo after a move or cancel.** Dragging a ticket, or using a ticket's **Move** buttons, shows a notice at the bottom of the board that names the ticket and where it went, such as "REM-12 cancelled." or "REM-12 moved to Backlog." **Undo** on the notice moves it back to the column it came from. A move that starts work (**Start now**, **Resume**, or a drag to **In progress**) and **Decline** get a notice without Undo, and so does a move out of **In review** or **Blocked**, since a ticket only gets there from its run. A notice with Undo stays for at least 10 seconds and doesn't close while the pointer is on it or it has focus; others close after 4 seconds, and **×** closes any of them. Errors stay until you dismiss them. New in 0.6.1.
- **Filters stay put.** Whose tickets, the search, the person you picked, **Closed** and the Board or People view are kept on this computer. They're still set when you come back to the board or restart Raticode. A filter that's on is highlighted, and **Clear filters** turns them all off. If a saved organization or person is gone, that filter shows everyone again and the others stay. Each [organization's board](../advanced/organizations.md#find-your-way-around) keeps its own filters, and the phone keeps its own.
- Organization tickets stay owned by their organization. Your changes go through its usual approvals and permissions, and reading the board never starts an organization.
- You can hand one of Rem's tickets to an organization. An organization's ticket can't go to Rem; create a new one instead.
- Rem can create, edit, move and assign tickets when you ask in chat. It can't approve its own proposals or mark a ticket done. The one exception is [auto-approval](#let-rem-approve-its-own-tickets), which only you can turn on.
- Rem's replies name tickets by key. A key such as `REM-12` in Rem's chat shows as a pill you can click to open the ticket; see [Ticket pills](./home.md#ticket-pills).
- **Mod+P** finds a ticket from anywhere in Raticode. [Quick open](../reference/shortcuts.md#command-palette-and-quick-open) searches the board's tickets by key, such as `REM-12` or `rem12`, or by title, and Enter opens the ticket here. New in 0.6.2.

### Tickets follow their task

Once a ticket's task is running, the ticket moves by itself:

| Column | When |
| --- | --- |
| **In progress** | The task is working, or resumes in its thread, is retried, or its review asks for changes |
| **In review** | The work is finished and its independent review is checking it |
| **Blocked** | The task waits on you or an outside event (including a [sleeping](./home.md#sleeping-tasks) task), was stopped, failed or errored, or its review left checks unresolved. With [auto mode](#auto-mode-no-review-at-the-ticket-level) on, only for work that's impossible without you |
| **Done** | Only when the review accepts the work. Rem can't mark its own tickets done by hand. |

An organization ticket also goes to **Blocked** while it waits for other tickets, when a pause or closing time cuts its run off, or when its review rejects the work.

- A run that's being stopped doesn't move its ticket until it has stopped.
- The same state seen twice never moves a ticket twice.
- If you move a ticket yourself, it stops following its last run.

### Usage on each ticket

Each ticket shows the tokens its runs used, for Rem's tickets and organization tickets alike. Cards show a short total such as **12.4k tokens**, and an open ticket shows the full count under **Usage**.

- Raticode records every model call with the run and thread that made it. A ticket's total adds up every call from every run it had, including retries and its independent reviews.
- Some providers don't report their usage. Those calls are counted but their tokens are **unknown**, so a card says **12.4k tokens + 1 unknown** or **Tokens unknown**, never a total that looks complete when it isn't.
- An organization run that ran on another computer counts once, with what that run reported. The Organizations tab shows the same total on each ticket, under **Did**.
- `Raticode tickets show REM-12` prints the same total.

### Tickets that wait for other tickets

A ticket can list the tickets it waits for, so work happens in the right order. Use it when one piece of work only makes sense after another, such as starter workflows that need a trigger feature first.

- **On the card.** A waiting ticket shows **Waits for:** and the title of the first ticket that isn't Done yet, with **and N more** when there are others. The line goes away once every one of them is Done.
- **Edit the list** in the ticket's **Waits for** section: pick a ticket from **Wait for a ticket…**, or remove one with its **×**. You can change the list even while the ticket runs. On the phone it's the same section on the ticket's page, and from a terminal it's `Raticode tickets edit REM-7 --waits-for REM-3`; see [Tickets from the CLI](../reference/cli-rem.md#tickets).
- **Same board only.** Rem's tickets wait for Rem's tickets, and an organization's for its own. For an organization's ticket this is the same list as its dependencies in the [Organizations](../advanced/organizations.md#tickets-and-independent-review) tab.
- **Self-work waits.** Self-work doesn't start, pick back up or pull a ticket from Backlog until every ticket it waits for is **Done**. An organization holds its ticket in **Blocked** the same way. You can still start a waiting ticket yourself with **Start now**.
- **When one is cancelled,** the waiting ticket stays where it is and shows a notice instead: the cancelled ticket's key and title, and that it won't start on its own. Its history gets the same note. You decide: remove the cancelled ticket from the list, restore it, or cancel or start the waiting ticket. A ticket can't start waiting for a ticket that's already cancelled.
- **When one is closed as a duplicate,** tickets that waited for it wait for the ticket it was merged into instead, with a note in their history. If that would make a ticket wait for itself or in a loop, it keeps the old link and gets the cancelled notice above.
- **No loops.** A ticket can't wait for itself, and the board refuses a change that would make tickets wait for each other in a circle. The message names the loop, such as `REM-1 → REM-3 → REM-1`.
- A ticket waits for at most 20 others.
- **Rem sets it too.** When you ask Rem in chat to add a ticket "after REM-4", it fills in the list. Research proposals can say which ticket must come first, and that list is kept when you approve them. Rem only keeps tickets that are really on the board.

## Review and land

::: info New in 0.5.5
Reviewing and landing from the ticket is new in Raticode 0.5.5.
:::

When a ticket's work is on a branch, its sheet has a **Changes** section with the branch, the branch it lands in, the size of the patch and whether it merges cleanly. The branch is:

- an organization ticket's own `org/` branch;
- the temporary `rem/` branch a Rem task used because another task was changing the same project;
- the feature branch a project's worktree skill had the worker create. Those usually land on the project's [landing branch](./home.md#changes-land-on-the-project-s-branch) by themselves; the sheet still shows their patch.

**Review changes** widens the sheet and shows every changed file side by side, in the same view as a commit in [Source control](../studio/source-control.md), with the same search and file filters. Files the worker changed but didn't commit are included and marked **Not committed yet**.

**Line comments.** Click a line number on either side, or use a file's **Comment** button to type the line, and write what should change. Add a note if you like, then **Request changes**. The comments and note go back to the same worker as one request for changes:

- a Rem ticket continues in its own thread;
- an organization ticket in review is sent back with a review that asks for changes, and a done ticket is reopened with your comments.

**Accept** lands the branch in the branch it came from (for example `dev`), after you confirm:

1. Raticode checks the branch is still exactly what you reviewed. If the worker changed it since, Accept says so and you review again.
2. It checks the two branches merge without conflicts, without touching any folder.
3. It commits any files the worker left uncommitted, then fast-forwards the base or records a merge commit. If the base is checked out in a folder, that folder must have no uncommitted changes, and it is updated there.
4. The ticket moves to **Done** with a comment naming the commit. Nothing is pushed.

If the branches conflict, nothing changes. The sheet lists the conflicting files and offers **Ask … to rebase**, which sends the worker a request to rebase onto the current base and resolve them.

Only you can press Accept. Rem, self-work, Auto mode and organization workers can't land a branch, and Rem can't ask for it through a consent request. An organization ticket in review also offers **Accept without landing**, which marks it done and leaves the branch as it is.

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
- **Start it now** with **Start self-work** in Rem's side pane, or tell Rem "start self-work now". It starts within seconds instead of waiting for quiet minutes, and keeps going while you chat until you turn it off. See [Start self-work now](#start-self-work-now). New in 0.6.1.
- **Turn it on or off** in [Settings](#settings), or tell Rem: "turn on self-work", "stop self-work", "is self-work on?". Turned on, it waits for 15 quiet minutes as usual. See [Turn it on or off from the chat](#turn-it-on-or-off-from-the-chat).
- **Set a time** by telling Rem: "enable self-work until 7 PM tonight", "self-work from 11 PM to 7 AM". See [Timed self-work](#timed-self-work).

With the board on, Rem fills its free threads, up to the maximum, in this order. A ticket that [waits for another](#tickets-that-wait-for-other-tickets) is skipped until every ticket it waits for is Done. While the [Objectives](#objectives) list has open objectives, only tickets that advance one of them start, and research looks only for work on them; see [Self-work honors the objectives](#self-work-honors-the-objectives).

1. **Its own Blocked tickets** in its projects, once per block, since that work was already under way. Rem retries an interrupted review, follows up in the task's thread after a failure, or restarts a ticket that couldn't start. A ticket whose thread ran in the wrong project moves to the right one first; see [Tickets filed under the wrong project](#tickets-filed-under-the-wrong-project). A ticket that needs you (an answer, something you stopped, something outside this computer) stays Blocked, and Rem comments with exactly what's needed. A question shows as **Waiting on your choice:** with Rem's recommended answer; answer it in the [Decisions list](home.md#the-decisions-list) and the ticket's work continues. With [auto-approval](#auto-mode-no-review-at-the-ticket-level) on, only work that's impossible without you stays Blocked.
2. **To do tickets** in its projects, by priority. With several open objectives, tickets of the same priority [take turns among them](#objectives-take-turns).
3. **Backlog tickets** it ranks as most worth doing, taking turns among the open objectives, only if **Pull backlog tickets when threads are free** is on (off by default). A ticket Rem judges no longer makes sense stays in Backlog with the reason. A ranking run scores up to 40 tickets at a time, each on its own: a reason over 500 characters is cut off, and only a ticket whose ID isn't exact or whose impact or relevance isn't a whole number from 1 to 5 is left unranked. The report lists it as **Didn't use the ranking of “…”** with the reason, and it isn't pulled until a later ranking judges it. A ranking whose skip or objective can't be read is kept with that field left out, and the report says **Used the ranking “…” only in part**. If the answer has no ranking Rem can read, the run goes back to its worker with the exact error before any review, as research does.
4. **Research** with any threads left. Each run studies one project for new features, reliability and security, speed and upkeep, or design and module depth. It saves a report and proposes tickets. Research repeats in rounds; see [Limits and safety](#limits-and-safety).

About once a week, and when a session ends, a [guardrail retro](#guardrail-retro) takes a thread first when the same failure has come back in two or more runs.

Each proposal comes with a finish line: one to three statements a reviewer can check, such as a command that passes or a screen that shows a named thing. They become the ticket's **Done when**, one review check each. Statements that can't be checked, like "works well" or "is cleaner", are dropped. New in 0.6.0.

An open ticket shows its **Done when** under the description, one item per line, on the computer and on the phone. A proposal shows it too, so you can judge the finish line before you approve. Organization tickets show theirs the same way. Change it with **Edit** in the open ticket (see [The board](#the-board)), by asking Rem, or with `Raticode tickets edit REM-12 --done-when "…"` from a script. New in 0.6.1; **Edit** is new in 0.6.2.

#### Design and module depth

The design and module depth run looks for shallow modules: code whose interface is nearly as complicated as what it hides, so its callers would be simpler without it. New in 0.6.0.

- **It starts from git, not a guess.** Before the run starts, Raticode reads the last 30 days of the project's history on its landing branch (`dev` for a project that has one) and ranks files by their length times how many commits changed them. The top ten go into the run's instructions as a table, shown in its task details. No model is used for the ranking.
- **The deletion test.** For each file near the top, the run asks: if this module were deleted and its code moved into its callers, would the callers get simpler? If so, it's a candidate for deepening, which means hiding more behind a smaller interface.
- **At most two tickets.** Each deepening ticket sketches the interface before and after the change, names the files it changes, and its **Done when** includes "The project's decided check passes and public behavior is unchanged".
- **It waits its turn.** A deepening ticket [waits for](#tickets-that-wait-for-other-tickets) the open tickets in the same project that name the same files, and self-work runs only one deepening ticket at a time, so two refactors never move code under each other. You can still start one yourself.

Proposals wait in Backlog, marked as proposed, until you choose **Approve for To do** or **Decline**. Nothing proposed runs until you approve it, unless you turn on [auto-approval](#let-rem-approve-its-own-tickets).

### Objectives

**Objectives** is a short checklist of the goals that guide a whole self-work session, such as "Improve reliability of the Second Brain tools" or "Raise UX quality of the workflow studio". It sits above the task list in Rem's side pane, folds away with its heading, and stays folded until you open it again. New in 0.6.0.

- **They're goals, not tasks.** An objective names an area and a direction that many tasks can move forward, often across several sessions. Something one task could finish (fix a bug, add a button, one ticket or one file) belongs on the board instead.
- **Open-ended objectives are yours.** An objective you write, or one Rem writes from your own words, can stay open-ended, like "Optimize performance of file handling", and doesn't have to be finished before self-work ends. Every objective Rem writes on its own has a **Done when** finish line; see [What Rem may write](#what-rem-may-write).
- **You set them by telling Rem.** When you turn self-work on, set a window or say what it should focus on ("self-work until 7 PM, focus on the studio's UX and Second Brain reliability"), Rem turns your words into objectives and replaces the open ones. Done ones stay on the list as history. Ask for open-ended work, such as "start self-work and focus on hardening existing logic before the release", and Rem writes it open-ended, as you said it; name a finish line and Rem records it as **Done when**.
- **No instructions means no change.** When self-work starts and you said nothing about what to work on, the current objectives stay in force.
- **An empty list gets Rem's own.** If the list has no open objectives when a session starts and you gave no instructions, Rem writes two to four objectives of its own from its projects, their open tickets and recent work, each with a **Done when** finish line, and says so in the chat. Self-work starts nothing new until it has, usually a few seconds, so its first work already follows them. If Rem can't write any, self-work goes on as it would with no objectives.
- **Rem adds objectives only when none are open.** While any objective is open, whether you wrote it, Rem wrote it from your words or Rem picked it itself, and whether it is open-ended or has a finish line, Rem never adds one on its own: the open ones are what self-work works toward. When nothing is left to start and research found nothing new, Rem checks off any open objective whose **Done when** is met. Only if that leaves none open does it add one to three new objectives, each with a finish line (at most twice a session), and start a new round of research guided by them. Your open-ended objectives can't be checked off by Rem, so while one is open self-work stays with them and says it's out of work. Only you add more while objectives are open, in the list or by asking Rem in the chat.
- **Every task reads them fresh.** Self-work reads the list again just before each task it starts, so a change you make now applies to the next task. Tasks get the open objectives in their instructions: ticket work as context for that ticket only, ranking to judge how much each ticket advances them, and research to propose only work that does.
- **Which objective a task serves.** Every ticket self-work starts while objectives are open serves one, and the task that works on it shows that objective under its name in the side pane. Research for an objective shows it the same way. Each objective shows how many running tasks are on it.

#### Self-work honors the objectives

While the list has at least one open objective, self-work only works toward it. New in 0.6.0.

- **Only related tickets start.** Self-work starts a To do, Backlog or Blocked ticket only when it advances an open objective. A ticket that doesn't stays where it is, whatever its priority. The ticket's card says **Not on an objective: self-work leaves it**, its sheet says why, and the self-work report lists it once. Move it to **In progress** yourself to start it anyway.
- **How Rem decides.** A ticket advances an objective when research proposed it for that objective, when a backlog ranking tied it to one, or when Rem's chat model judged it does. Before starting anything, Rem checks every waiting ticket it hasn't judged against the current objectives in one quick call to its own model, which doesn't use a thread. Being in the same project or touching the same code isn't enough; when in doubt, the ticket is left alone. The judgment is redone when you change the open objectives or edit the ticket. If the check fails, those tickets don't start, and Rem tries again 10 minutes later.
- **The card shows the objective.** A ticket that advances one shows that objective on its card and in its sheet, on the computer and on the phone.
- **Ranking sees only related tickets.** Backlog ranking runs only for tickets that advance an objective, so unrelated tickets aren't ranked or pulled.
- **No related ticket means new ones.** Research runs once per objective and project it concerns, titled like **Find work for “Improve Raticode UI responsiveness” in Raticode**, in place of the usual features, reliability, speed and design angles. Every ticket it proposes serves that objective. The same check tells Rem which of its projects each objective concerns, so research doesn't run where an objective can't apply. If none of Rem's projects fits any open objective, self-work says so and starts nothing.
- **Approval still applies.** With [auto-approval](#let-rem-approve-its-own-tickets) off, the new tickets wait in Backlog for you. With it on, they're approved, ranked and worked like any other related ticket.
- **No open objectives, no gate.** With an empty list, or every objective checked off, self-work picks tickets and research angles as described above.
- **Work already running isn't stopped.** The gate decides what starts. A task running when you change the objectives finishes.

#### Objectives take turns

New in 0.6.1.

With two or more open objectives, self-work shares its threads among them instead of spending most of them on one. Each time it starts a ticket or a research run, it picks the objective with the least work so far: the tasks running for it now plus the tasks self-work started for it this session.

- **To do** tickets still go by priority first. Among tickets of the same priority, the objective with the least work goes next.
- **Backlog** tickets go to the objective with the least work, its best-ranked ticket first. A ticket ranked a little lower can start before a better-ranked one, so an objective with many well-ranked tickets can't take every thread.
- **Research** takes one project of each objective before a second project of any, starting with the objective with the least work.
- **You can see the share.** Each objective in the side pane shows how many running tasks are on it.

Edit the list yourself at any time: **+** adds one, the checkbox checks one off or reopens it, and each row's menu has **Edit** and **Remove**.

Checking off, reopening and removing show at once, and Raticode saves them in the background in the order you made them, so you can check off several in a row without waiting. Reopening one before its check was saved cancels both. If a change can't be saved, its row goes back and the side pane says why. When Rem, the phone or the CLI changed the list first, none of your waiting changes are saved, since they were made on the old list: look at the list again and redo them. New in 0.6.2.

#### An objective's details

New in 0.6.1.

Select an objective's text, or focus it and press Enter or Space, to open its details under the row. One row opens at a time, and Escape or a second select closes it. The details show:

- **The full text.** A long objective isn't cut off at two lines while its row is open.
- **Its finish line.** **Done when:** and the criteria, or **Open-ended** when it has none, which means Rem never checks it off.
- **How it was met.** Once it's done: who checked it off, Rem or you, and the evidence Rem gave.
- **Where it came from.** **Stale** for one done in an earlier session, and **Written by Rem** for one Rem wrote.

Pointing at a row with the mouse shows the same details as a tooltip.

On the phone, the board's **Self-work** section lists the objectives; tap one to open the same details under it, one at a time. With a desktop that has [each project's objectives](#each-project-s-objectives-and-level-of-effort), you can also add, check off, reopen and remove objectives, globally or in a project's section, and set each project's level of effort; with an older desktop, tell Rem in its chat. See [Global board and self-work](../mobile/workspace.md#global-board-and-self-work). New in 0.6.1; changing them from the phone is new in Raticode 0.6.2 and Raticode Mobile 0.38.0.

#### Order, sessions and stale objectives

New in 0.6.1.

- **Newest first, open ones on top.** Open objectives always sit above done ones, and each group lists the newest first. Objectives Rem writes together keep the order it wrote them in. The order is fixed, so rows have no **Move up** or **Move down**.
- **The count is for this session.** The number by the heading is done/total for the current self-work session, such as **1/3**, or just the total while none are done.
- **Done ones go stale when a session ends.** A self-work session ends when you turn self-work off or its window ends. Every objective checked off by then becomes stale: it no longer counts in the heading in later sessions, and it moves into a folded **Stale** group below the current objectives, so this session's done ones stay apart from earlier ones. Open the group to see them, faded, with how many there are beside it.
- **Unfinished ones carry over.** An objective still open when a session ends stays open in the next one and never goes stale. Reopen a stale one with its checkbox and it's current again.
- **The newest 30 load first.** The side pane loads the first 30 objectives in this order. Scroll to the bottom of the list, or select **Show more**, to load 30 more. Stale ones load only while their group is open. Raticode keeps up to 500 objectives, dropping the oldest stale ones first. `Raticode selfwork objectives` does the same from a terminal; see [Rem and self-work from the CLI](../reference/cli-rem.md#self-work).

**When Rem checks one off.** You can check off anything. Rem checks one off only when it has written doneness criteria (**Done when**) and it can say how they were fully met; that evidence is kept with the objective. An open-ended objective has no criteria, so Rem never checks it off. If you tell Rem an objective is done, it checks it off with your word as the reason.

### Each project's objectives and level of effort

New in 0.6.2.

Under **Objectives**, each of Rem's projects has its own folding section, so you can tell Rem what to do in several projects at once. The list at the top holds the global objectives, which steer every project. A project's section shows its name, how many of its own objectives are done, its **Level of effort** and how many threads it holds now against its share, such as **3 of 3 threads**. The same sections are in Rem's side pane and in **Settings > Rem**, under **Self-work**.

- **A project's objectives steer only that project.** Its tickets must advance one of its own objectives or a global one to start, and research for its objectives runs only in that project. Another project's objectives never pull its tickets, even when Rem's check thinks a ticket would help them. A project with no open objective of its own, while no global one is open, picks tickets and research angles as usual.
- **Add one in the section.** **+** in a project's section adds an objective to that project. Check, edit and remove work as in the global list. Tell Rem in the chat too: "for agent-trading, focus on cutting drawdowns" sets agent-trading's own objectives and leaves the others alone.
- **Ask Rem for a project's objectives.** **Ask Rem** in a project's section has Rem write three to five new objectives for that project. See [Ask Rem for a project's objectives](#ask-rem-for-a-project-s-objectives).
- **Stale ones fold under each project.** A project's objectives done in earlier sessions sit in their own folded **Stale** group at the end of its section, with how many there are, as in the global list. Open it to see them; each section remembers whether its group is open. The same fold is in Settings > Rem.
- **Long lists page per project.** A section loads its project's newest 30 objectives first. When the project has more, **Show more** at the end of its list, or of its open **Stale** group, loads the next 30 for that project alone. Settings > Rem shows the first 30 and says **More in Rem's side pane**.
- **Move one with Edit.** Editing an objective shows **Applies to**: **Every project** or one of Rem's enabled projects. Pick another and save, and the objective moves to that section. It keeps its ID, so the tasks and tickets working on it still count toward it. If the list it would move to already has the same open objective, or is full (30 open), Raticode says so and the objective stays where it was. Rem's chat edits change only the words; they never move one.
- **Only enabled projects have a section.** Turn a project off under **Rem's projects** and its section goes away. Its objectives and level of effort are kept and come back when you turn it on again.

#### Ask Rem for a project's objectives

New in 0.6.2.

Open a project's section and select **Ask Rem**. Rem reads what the project is now: its docs (such as `README.md` and `AGENTS.md`), its folders, its recent commits, its open tickets and every objective that steers it. Then it adds three to five new objectives to that project's own list. One press never adds more than five, and never more than the 30 open objectives a list holds.

- **Broad enough for a whole session.** Each one names an area and a direction that self-work can follow for a whole session, potentially several dozen tickets, such as "Improve UX across the project's features", "Audit unit tests and rewrite or remove unhelpful ones" or "Harden error handling and recovery in the sync code". Nothing one or a few tickets could finish: an objective that starts like a single task (fix, add, rename, upgrade, implement, create, delete, move or make), names a ticket key or a file, or repeats or rewords an open objective is sent back to Rem once with the reason, and anything still like that is left out.
- **Open-ended, because you asked.** They have no **Done when**, like the ones you write or ask for in the chat, so Rem never checks them off and they stay until you check them off or remove them. They don't count toward the six open objectives Rem may keep of its own. Rem's own rules don't change: on its own it still writes objectives only with a finish line.
- **It takes a minute or so.** While Rem writes, the button reads **Asking Rem…** and the section says Rem is reading the project. Then it says how many it added, or why it couldn't, such as the model's provider being out of allowance. A project can have one ask at a time. An ask started on the phone or from the CLI shows the same way in the side pane and Settings > Rem.
- **Rem's tool-free model writes them.** No worker or task runs, and nothing in the project changes. Edit, move or remove the new objectives like any others.

The phone has the same button in each project's section, and `Raticode selfwork objectives ask --project NAME` does it from a terminal.

#### Level of effort

A project's **Level of effort** is its share of self-work's threads, in percent. With **Max threads** at 10, Raticode at 70% and agent-trading at 30%, Raticode holds up to 7 threads and agent-trading 3.

- **Threads, not tasks.** What counts is how many threads a project holds at once. Raticode can finish five 20-minute tickets one after another in one thread while agent-trading runs the same three long tasks; each project still holds its own share.
- **The numbers total 100.** New projects split evenly. Change one project's number and the others share the rest in the proportions they had. A project added later starts with an even share, and the others scale around it.
- **Rounding.** Each project gets the whole threads its share covers. A thread left over goes to the project closest to its next one, and between two equally close projects to the larger share: 70/30 of 3 threads is 2 and 1.
- **Unused threads are lent.** A project with nothing it can start lends its threads to the others. When it has work again, the next thread that frees goes to it; nothing running is stopped to give threads back.
- **Your work still comes first.** The shares split the threads your own running tasks leave free. Work that paused for yours picks up first, whatever its project's share.
- **See it.** Settings > Rem, each project's section and the self-work status line show threads by project while self-work runs, such as "Threads by project: Raticode 7 of 7, agent-trading 3 of 3."

Set it in a project's section, under **Rem's projects** in Settings > Rem, on the phone, or with `Raticode selfwork set --effort agent-trading=30`. Rem reads the levels but doesn't change them.

#### What Rem may write

Rem's own objectives must be short goals of at least three words that don't name a ticket key or a single file, and it keeps at most six open ones of its own. Workers and agent tokens can read the list but not change it.

- **A finish line for everything Rem picks itself.** An objective Rem writes on its own initiative, when the list is empty at a session's start, when self-work runs out of work with every objective done, or in a chat turn you didn't start (such as a task finishing) while no objective is open, needs a concrete, checkable **Done when** of at least three words, such as "Every Second Brain tool error found in this session's research is fixed with a regression test". One without it is refused. If Rem's answer leaves a finish line out, Rem is asked once more, and anything still without one is left out; the chat names it. An open-ended goal Rem picked for itself kept most of self-work on one thing for as long as it ran, which is why open-ended objectives are reserved for you. New in 0.6.1.
- **From your words, open-ended is fine.** When you tell Rem in the chat what self-work should work on, its objectives may stay open-ended. So may the ones [Ask Rem](#ask-rem-for-a-project-s-objectives) writes when you press it in a project's section.
- **Older open-ended ones get a finish line.** An open-ended objective Rem wrote on its own before this rule is given a finish line when the next session starts, and the chat says what it wrote. Self-work starts nothing new until then, usually a few seconds. An objective you wrote or asked for, and one you made open-ended by clearing its **Done when**, stays as it is.
- **On its own, Rem only adds, and only to an empty list.** On its own it can only add objectives while none is open, check them off and give one of its own a finish line; editing, removing or reopening takes your message or your own click.

### Let Rem approve its own tickets

**Approve its own backlog tickets** in **Settings > Rem** lets self-work skip the approval step for the tickets its research proposes. It's off by default.

- **What it does.** New ideas from self-work research go to Backlog already approved instead of waiting for you. With **Pull backlog tickets when threads are free** on, Rem then ranks them with the rest of the backlog and works on the best ones. With pulling off, they wait in Backlog until you move them to To do.
- **How they're marked.** Each one has an **Auto-approved** badge on the board and the phone, and its history starts with an **Auto-approved by self-work** entry saying you haven't reviewed it. The report and Rem's chat update say how many it approved.
- **They're still yours.** Edit, move or cancel them like any ticket. Rem still can't mark its own tickets Done; they finish through the usual independent review.
- **No cap on the number.** It keeps approving new ideas until self-work ends: when you send a message, a [window](#timed-self-work) ends or you turn self-work off. How much it researches is set by the usual [limits](#limits-and-safety), and your [token and rate limits](#self-work-limits) stop it starting new work.
- **Ideas from a paired desktop always wait for you.** When you send a waiting self-work research or Guardrail pass to a paired desktop with [Run there](home.md#run-waiting-work-on-a-paired-desktop), what it proposes comes back as proposals waiting for your approval, never auto-approved. Each one shows **From** and that desktop's name on the board and the phone, its history starts with a **Proposed on** entry naming the desktop, and `Raticode tickets list` shows `(proposal from Desk B)`. Passes that ran on this computer are approved as usual. A backlog ranking that ran there approves nothing either way: a ranking only orders tickets already on the board. New in 0.6.1.
- **Ideas that contradict a decision aren't added.** An idea that contradicts one of the project's [decisions](home.md#how-the-work-is-held-to-decisions) never reaches the board, approval on or off; see [Self-work keeps to decisions](#self-work-keeps-to-decisions). New in 0.6.0.
- **What's still left out.** An idea that repeats a ticket on the board, in an organization or one you declined isn't added. A research run may close one of these tickets as a duplicate, never one you wrote. When Rem ranks the backlog it can leave one out as no longer making sense, like any other.
- **Its other limits still apply**: Rem's projects, **Max threads**, the [token and rate limits](#self-work-limits), Blocked tickets and To do first, and [your work first](#your-work-comes-first).
- **It's Rem's auto mode.** With it on you don't review anything at the ticket level; see below.

Turning it on asks you to confirm what it allows. Only you can change it: in **Settings > Rem**, on a paired phone, or with `Raticode selfwork set --auto-approve --confirm-auto-approve` from your own terminal. Rem can't turn it on from the chat, and neither can its workers or agent tokens.

When you change it:

- **Turned on**: proposals already waiting stay waiting for you. Only ideas found from then on are approved, including those from research already running.
- **Turned off**: new ideas wait for your approval again, including those from research still running. Tickets it already approved stay as they are.

#### Auto mode: no review at the ticket level

With **Approve its own backlog tickets** on, Rem's board tickets don't wait for your review, approval or sign-off. **Blocked** is kept for work that's impossible without you. New in 0.6.0.

- **What still blocks.** Missing credentials or an account, a payment, or an action only you can take. Rem comments with exactly what's needed, as before.
- **What doesn't.** A worker asking you to confirm, approve or review something, or to run a command its sandbox can't run, gets Rem's answer instead: choose the reasonable default and finish. A blocked ticket that only waited on such a question starts again on its own.
- **Decisions.** A question with a recommended answer that can be undone is decided by Rem, recorded as provisional and reused by later work; see [Auto mode decides for you](home.md#auto-mode-decides-for-you). One that can't be undone, or that would change a recorded decision, waits for you while the work goes on without it.
- **Checks only a person or another machine can prove.** The reviewer marks a check that needs something this computer's sandbox can't do (namespaces, a real phone, a push to CI, your confirmation) as unverifiable, not failed. If nothing else is wrong, the work is accepted and its **Done** entry starts with **Not verified here (auto mode):** and what wasn't run. A real defect still goes back to the worker.
- **No sign-off criteria.** Rem doesn't write checks like "make sure you're OK with it" for tasks or tickets it creates. A finish line you wrote yourself is kept as you wrote it.
- **Repeated blocks.** Rem follows up once in the task's thread, then starts the ticket again in a new thread, up to three times in all. After that it stops and leaves the ticket Blocked with what kept failing, so it can't loop.
- **The Done rule doesn't change.** Rem still can't move its own ticket to Done by hand; work reaches Done only through the independent review.
- **Organizations** have their own Auto mode. With it on, workers are told the same: don't block a ticket waiting for your approval or a hand check, only for credentials, an account, a payment or an action only you can take.

Turning auto mode off brings back the usual behavior: questions wait for your answer, and unproven checks block the ticket.

### Self-work keeps to decisions

New in 0.6.0.

Self-work never works around the project's [decisions](home.md#rem-s-decisions). A ticket that contradicts a decision in force is one self-work won't do. It counts as contradicting one when it names that decision as a conflict, or when its title, description or finish line contains one of a [decision record's](home.md#decision-records) signal words as a whole word, in any case. A decision without signal words is only caught when the ticket names it.

- **New ideas.** Research doesn't put an idea that contradicts a decision on the board, approval on or off. The report lists it as **Didn't add “…”: it contradicts D-4**, and Rem's chat update for that research run says the same.
- **Backlog and To do.** A ticket that contradicts a decision is never pulled or started. It moves to **Blocked** with a red **Violates D-4** badge on its card, and its sheet has a **Violates decisions** section with the decision, the reason and what to change. Its history gets a **Blocked: violates decisions** entry. Proposals still waiting for your approval stay as they are.
- **Moving it to In progress.** The same check runs when you start a ticket by hand: it's blocked and marked instead of starting.
- **During the work.** If a decision changes while a task runs, the check before review fails the work for a ticket that now contradicts it. The task stops blocked rather than going round repairs.
- **Getting it going again.** Edit the ticket so it no longer contradicts the decision, or change, retire or forget the decision in the Decisions list. Self-work checks it again and moves it back to Backlog or To do, with a **No longer violates decisions** entry. Until one of those changes it stays in Blocked, and self-work's own unblocking leaves it alone. Moving it back by hand without changing it only blocks it again.
- **No exceptions.** Self-work can't edit, replace, turn down, retire or forget a decision, and a request that would weaken one waits for you even in auto mode (see [Who can change a decision](home.md#who-can-change-a-decision)).

The phone shows the same badge and section, and `Raticode tickets list` adds **(violates D-4)** after the title; `tickets show` prints the reason.

### Tickets filed under the wrong project

A ticket's run is rooted in the ticket's project, and its worker can only write there. When a ticket's code lives in another project (say a Second Brain idea whose code is in the Raticode repository), Rem fixes it on its own, with or without auto mode. New in 0.6.0.

- **Spotting it.** A write was refused for a path inside another of Rem's projects, or the run built nothing because its folder couldn't hold the work and the worker or reviewer names the project it needs. Rem ends that run at once, without a review or repair sent back to the thread that can't write. A result that reports work landed on `dev` always goes to review, and reports or notes the worker linked in another project don't count as evidence.
- **Fixing it.** Rem moves the ticket to the project its files are in, adds a **Moved to the project its code is in** entry to its history, and starts it again in a new thread rooted there. Findings never go back to the old thread.
- **Preventing it.** New research proposals, and To do, Backlog and Blocked tickets self-work is about to start, are filed under the assigned project that holds the files they name, when their own project holds none of them. A backlog ranking that would leave a ticket out only because it's in the wrong project moves it instead.
- **Limits.** Only Rem's assigned projects are candidates, and a ticket moves at most twice, so two projects can't pass it back and forth. With no clear match, the ticket stays where it is.

### Guardrail retro

New in 0.6.0.

When the same failure keeps coming back, self-work proposes a lasting fix instead of repairing it again each time.

- **When.** Once a week while self-work runs, and when a self-work session ends (you come back, or a [timed window](#timed-self-work) closes) if the last look was more than about a day ago. Turning self-work off doesn't start one. It needs the board on and counts toward self-work's [limits](#self-work-limits).
- **What it reads.** Raticode's own code, with no model call, reads the last 14 days of run records: [project check](home.md#raticode-runs-the-project-s-check-itself) failures with the end of their output, tests that flaked (failed, then passed on a rerun of the same commit), review send-backs and blocks with their reasons, runs stopped for repeating the same unresolved result, why tickets were blocked, tickets moved because a run was in the [wrong project](#tickets-filed-under-the-wrong-project), your **Request changes** comments, and the reviewer's notes on finished runs, accepted ones included (notes, and should-fix findings kept after their one send-back). It groups them by cause, such as one failing test, one lint rule, one failed review check, one flaky test, or reviewer notes in one file, and keeps only causes seen in two or more runs. A task blocked because it failed counts toward **blocked: Task failed** and, when [Verified work](./usage.md#verified-work) names why, toward a narrower cause with that reason, such as **blocked: Task failed (provider error)**, so two runs that failed the same way group together. A **flaky test** cause's fix is a ticket to stabilize that test, never to retry or skip it. A standards note seen in two runs is usually best fixed by a lint rule or a test, so the reviewer doesn't have to catch it again.
- **Fixes that held, and fixes that came back.** Each look also reads the Guardrail tickets that reached Done, with the cause each one fixed, its project and when its timeline shows it moving to Done. For a cause one of them fixed in the same project, runs from before that point stop counting. If fewer than two runs are left, the fix held and the cause is left out. If two or more are left, the cause came back: the retro run gets only those runs, with the earlier ticket's key and title, and is asked to strengthen or replace that fix instead of proposing it again. The done ticket's history, on the desktop and the phone, gets one entry, **Came back in N runs after this landed**, listing the run IDs. Looking again at the same runs adds nothing; a new run adds one more entry.
- **Catch-all causes aren't judged.** Some causes say only that something failed, not why: **blocked: Task failed**, **blocked: Task stopped**, **blocked: Outcome unknown**, **repeat: the same unresolved result after a follow-up**, and a review failing a develop task's default check, **Implement the requested behavior and demonstrate it works**. A fix filed under one of these isn't judged held or came back: its runs keep counting, and its ticket gets no **Came back** entry. When a narrower cause is listed for the same runs, the retro run is told to file its fix under that one.
- **Causes the last retro left out.** The retro run says why it left out each listed cause it doesn't propose a fix for: **fixed**, naming the commit or the `REM-n` ticket that fixed it, or **not a pattern**, with a one-sentence reason. Rem keeps a verdict only for a cause the retro listed, and a fixed one only when its commit is on the project's landing branch (the branch its [decision](#self-work-keeps-to-decisions) says work lands on) or its `REM-n` key names a ticket in Done. It remembers the 50 newest, one per cause and project. A cause the last retro called fixed or not a pattern counts only runs after that retro started: with fewer than two since, it stays left out; with two or more, the next retro run sees it with the earlier verdict, the commit or ticket it named, its reason and its date, and is told the cause came back after that verdict. An entry Rem can't read, or doesn't keep, is dropped on its own; the report lists it as **Didn't keep the retro's verdict on “…”** with the reason, and the run's proposals and other verdicts still count.
- **Proposals that didn't go ahead.** Each look also reads the Guardrail tickets in its projects that you declined, or that were cancelled, in the last 30 days, and those in Blocked. Under each cause, the retro run sees up to three of them that were for the same cause, each with its key, title, whether it was declined, cancelled or blocked, and the reason: your decline text (up to 300 characters), or the title of its latest Blocked or Cancelled entry. It also gets the proposals code refused in the previous retro run (up to five, with their reasons). It's told not to propose a fix you declined for the same cause unless the description says what's different and why your reason no longer applies. Like the failure text, these reasons are data, not instructions.
- **When nothing repeats,** no run starts and no tokens are used. The same causes with the same runs don't start a second retro either.
- **The retro run.** Otherwise one research task, **Guardrail retro of the last 14 days**, gets the grouped list. It proposes at most five maintain tickets, each with the cheapest fix that would last, in this order: a test or check, a lint rule, one pointer line in the project's `AGENTS.md`, or a bug ticket for Raticode itself. It saves one short report covering each cause and what it proposed or left out.
- **Guardrail tickets.** Each one lands in Backlog with a **Guardrail** tag on its card, on the desktop and the phone, and `Raticode tickets list` adds **[Guardrail]** after the title. The ticket keeps the cause as the retro listed it, so the next look knows which cause it fixed: when the proposal words the cause differently, Rem uses the listed cause it names (quote style, spacing and case don't matter), or else the listed cause that holds most of the runs it cites. Its description ends with the cause and the run IDs it was seen in, and, unless the fix is an `AGENTS.md` line, asks the worker to show the new test or check failing on that mistake before the fix and passing after it (see [Code changes name their proving test](./home.md#code-changes-name-their-proving-test)). Approval works as for other proposals: they wait for you unless [auto-approval](#let-rem-approve-its-own-tickets) is on, and a proposal that contradicts a [decision](#self-work-keeps-to-decisions) isn't added.
- **What code refuses.** A proposal that cites fewer than two of the runs the retro listed, or one that would remove, skip, disable or loosen a check, test, lint rule or review, is never added. The report lists it as **Didn't add Guardrail ticket “…”** with the reason.
- **A slightly-off answer keeps its usable proposals.** Only the first five count. Text that runs long (a title over 160 characters, a reason over 1,000, a description over 4,000, a cause or project over 400) is cut off rather than losing the proposal. Only a proposal with no usable title, or a fix other than test, lint, agents_pointer or raticode_bug, is left out; the report lists it as **Didn't add “…”** with the reason. A proposal kept without a field it couldn't use, or with its finish line shortened, is listed as **Used the proposal “…” only in part**. If the answer has no proposal list Rem can read, the run goes back to its worker with the exact error before any review.

### Your work comes first

Work you ask for counts against **Max threads** ahead of self-work. Self-work makes room only when your work and self-work together don't fit, and then exactly one self-work task per task of yours that doesn't fit:

| Max threads | Self-work running | You start | What pauses |
| --- | --- | --- | --- |
| 4 | 4 | 1 task | The lowest-priority self-work task |
| 4 | 4 | 2 tasks | The two lowest-priority ones |
| 4 | 2 | 1 task | Nothing: a thread is still free |

The computer counts too. Organization workers and phone turns share its worker limit, so your task can wait for a worker while self-work is still under **Max threads**. Then self-work pauses one task, the lowest-priority one with a worker, for each task of yours that waits and has no free worker on Rem's computers. For example, with a worker limit of 3, two organization workers and one self-work task running, the task you start pauses that self-work task. Your task gets the freed worker, not an organization (see [Where work runs](./home.md#where-work-runs)).

- **What counts as yours**: a chat request that starts a task or a follow-up, a task you start or run, a ticket you move to **In progress**, and your scheduled tasks. Self-work you resume or follow up on, in the chat or in its thread, is yours from then on: it never pauses for **Max threads**, however many you resumed at once, and it doesn't wait behind your other tasks for a worker. *Changed in 0.6.2:* before this, only self-work you resumed from a pause counted as yours, so self-work you followed up on after it was blocked could pause again for your other tasks.
- **What pauses first**: the lowest priority. Research and backlog ranking go before any ticket, then tickets from **Low** up to **Critical**. Among equals, one that hasn't started goes first, then the most recently started. Your own tasks never pause, and neither does self-work you resumed yourself.
- **How it pauses**: like [pausing a task](./home.md#pause-and-resume-a-task). The run, its thread and any worktree are kept, and it frees its thread. Its card reads **Paused for your work**, and its ticket shows **Blocked** with a comment saying why.
- **When it resumes**: as soon as a thread is free again, for example when one of your tasks finishes, even while you're still here. The highest priority picks up first, before Rem starts anything new. It continues in the same thread, so nothing is done twice. You can also resume it yourself at any time, or move its ticket to **In progress**.

Rem says so once in the chat ("Paused 1 self-work task so your work runs first: …"), and the self-work status and report say how many are paused. A ticket you pause yourself stays paused until you resume it.

### Start self-work now

Starting self-work and turning it on are different (new in 0.6.1):

- **Start self-work now** begins work within seconds, without waiting for 15 quiet minutes. If self-work is off, it turns on. If it's already on and waiting, it stops waiting. It keeps starting work while you chat, until you turn it off or its [stop condition](#stop-condition-and-latest-time) is met. Turned on again later, it waits for quiet minutes as usual.
- **Turning it on** only flips the switch: self-work then starts after 15 minutes without a message from you.

Starting now changes only when work starts. The thread limit, [Objectives](#objectives), Rem's projects, [allowance pacing and limits](#self-work-limits), [your own work going first](#your-work-comes-first) and any stop condition all apply as usual. A [timed window](#timed-self-work) running now keeps its end time.

Start it now in any of these ways:

- Tell Rem "start self-work now", "start self-work" or "begin self-work", typed or spoken, on the desktop or the phone. Rem's reply says what happened, such as "Self-work was already on. I'm starting it now instead of waiting for 15 quiet minutes: it starts work on its next check, in a few seconds." If something holds it back, such as no projects chosen or a limit reached, the reply says so instead.
- Click **Start self-work** in Rem's side pane, or **Start now** under it while self-work waits for quiet minutes (see below).
- Run `Raticode selfwork start`.

### Start or stop from Rem's side pane

On the desktop, the button under Rem's name in the side pane starts and stops self-work. New in 0.6.1.

- **Start self-work** [starts self-work now](#start-self-work-now) and keeps it going until you stop it. It works toward the [Objectives](#objectives) as they are; the button doesn't add, change or check off any.
- While self-work is on but waiting for 15 quiet minutes, the line under the button ends with **Start now**, which starts it without the wait.
- **Stop self-work** turns it off, and ends a [timed window](#timed-self-work) that's running. Work already running finishes, and Rem posts [the report](#the-report-when-self-work-stops).
- While self-work is on, the button shows what it's doing (**Waiting**, **Working** or **Paused**), and the line under it says what's running and, when one is set, its [stop condition and latest time](#stop-condition-and-latest-time).
- It's the same switch as in [Settings](#settings) and the chat, and it follows changes made in the chat, on the phone or with `Raticode selfwork`. While a window runs, the button shows self-work as on.

### Turn it on or off from the chat

Tell Rem in your own words, typed or spoken, on the desktop or the phone:

| You say | What happens |
| --- | --- |
| "turn on self-work", "enable self-work" | Self-work turns on and starts after 15 quiet minutes |
| "start self-work now", "start self-work", "begin self-work" | Self-work [starts now](#start-self-work-now), turning on first if it's off |
| "stop self-work", "turn off self-work", "pause self-work" | Self-work turns off |
| "is self-work on?" | Rem says whether it's on and what it's doing |

Raticode makes the change itself and Rem's reply states the setting as it now is, for example "Self-work is off now, so I won't start anything on my own." It's the same switch as in [Settings](#settings), so the desktop, the phone and `Raticode selfwork status` all show it right away.

- Only the on/off switch changes. The thread limit, backlog pulling, auto-approval and Rem's projects stay as they are, and Rem can't change its own projects.
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
- Self-work that was [paused for your work](#your-work-comes-first) still picks up when a thread is free, since it had already started.
- The switch goes back to what it was, unless you said it should be on or off afterwards.
- Rem posts [what self-work did](#the-report-when-self-work-stops).

### Stop condition and latest time

Tell Rem what "done" means for this self-work session, and it stops by itself once that's true. You can add a time it stops by at the latest, whatever happens. *New in 0.6.0.*

| You say | What happens |
| --- | --- |
| "stop self-work when you finish REM-205 to REM-212" | Self-work stops once those tickets are Done |
| "stop when REM-205 to REM-212 are complete, but NLT 12AM" | Stops when they're Done, or at midnight, whichever comes first |
| "turn on self-work until the audit tickets are done" | Turns self-work on with that condition |
| "stop self-work no later than 11 PM" | A latest time alone, like an end time |
| "change the stop condition to REM-205 to REM-209" | Replaces the condition |
| "clear the stop condition" | Self-work runs until you turn it off or its window ends |

- **In your words.** The condition is free text. Rem keeps your words and decides from them, so name what you can check: tickets, tasks or objectives. Ticket ranges like "REM-205 to REM-212" are read as every ticket in between.
- **How Rem decides.** After each self-work task finishes, Rem's model reads your condition with the board, task and run records as they are then, and answers met or not met with a sentence of evidence. It's careful: it only says met when the records clearly show it, and a ticket counts as done only when it's in **Done**. If the check fails, self-work carries on and Rem tries again 10 minutes later.
- **While you're busy too.** Rem also checks when the records change without a self-work task finishing, for example when you move a ticket to Done yourself, at most every 10 minutes. It never checks twice for one finished task, and it doesn't check when nothing changed.
- **When it's met.** Self-work stops as if you'd turned it off: nothing new starts, tasks already running finish as usual, and Rem posts [the report](#the-report-when-self-work-stops) with a line saying the condition was met and the evidence. While Rem checks, a freed thread waits a few seconds, so it doesn't start work that would run on after the condition was met.
- **The latest time** works like a [window's](#timed-self-work) end: in the computer's time zone unless you name one, and refused when it has passed, doesn't exist or happens twice because the clocks change, or is more than 7 days away. Rem asks when it's unclear, such as "by 12" without AM or PM. 12AM is midnight.
- **Whichever comes first.** The condition, the latest time and a window's end each stop self-work, and it stops once. A window you planned for later still starts.
- **One session only.** The condition and the latest time belong to the self-work running now. You set them while self-work is on, or in the same sentence that turns it on. When self-work stops, for any reason, they're cleared, and **Settings > Rem** shows how the last one went. The next session starts without one.
- **Only you change it.** Rem sets, changes or clears them only when your own message asks for it, typed or dictated on the desktop or the phone. To change or clear one you set, or move the time later, Rem has to quote your words asking for it, and Raticode checks them against your message. A task result, a ticket or a workflow can't change them.
- **Restarts.** Both are saved with Rem's settings, so they survive a restart of Raticode or the computer and a clock change. A check that was running when Raticode quit runs again. **Clear history** keeps them but not Rem's past checks.

### The report when self-work stops

When self-work stops, because a window ended, its [stop condition or latest time](#stop-condition-and-latest-time) came, or you turned it off in the chat, Settings, the phone or the CLI, Rem posts one report in its chat. It says when self-work ran and what it started, finished, paused or blocked, with the tickets it created, moved or proposed as [ticket pills](./home.md#ticket-pills). Each task line names the objective it served, and a last line lists the tickets self-work left alone because they don't advance an open objective. Below it, each task it worked on shows how it went; select one to open it, or its thread if the task is gone.

- The report is built from Raticode's own run and ticket records, not from what Rem remembers.
- Above the task list, the report card shows how many tickets accepted in the period passed review first time, such as **3 of 4 tickets passed review first time.**, from [Verified work](./usage.md#verified-work). That line is only on the card, not in the report's text, so Rem never reads it back. *New in 0.6.0.*
- Ask "write a report of what you did while self-work ran" at any time. Rem reports the period so far, or the last one.
- It lives in Rem's chat and isn't saved as a file. **Settings > Rem** shows its one-line summary under **Last self-work period**.
- Self-work ending because you sent a message doesn't post one. **While you were away** covers that.
- With a [stop condition](#stop-condition-and-latest-time), the report ends with whether it was met and Rem's evidence. A session stopped by its condition or latest time always gets a report, even if it did nothing else.
- When Rem looks back through its chat for something else, past reports come after other matches and show only their first lines, so their long task lists don't crowd out what you asked about. Ask about self-work, or reply to a report, and Rem reads it in full. *New in 0.6.0.*

### In Rem's chat

Everything self-work starts shows in Rem's chat, with the same task cards and updates as work you ask for. Each card lists its tickets.

- Several jobs started at once share one message.
- When you move a ticket to **In progress** yourself, its card says so.
- Phone alerts for self-work come once, from the self-work report, not again for each chat update.

### Settings

Self-work's settings are in **Settings > Rem**, under **Self-work**. Settings you chose on the board before are kept.

| Setting | Default | What it does |
| --- | --- | --- |
| **Work on my own after 15 quiet minutes** | On | Turns self-work on or off. Telling Rem works too. To start without waiting, see [Start self-work now](#start-self-work-now). |
| **Max threads** | 2 | How many tasks Rem keeps going at once. **Worker limit** in Settings › Computers caps it, and self-work starts only as many as [Rem's computers have room for](#room-on-rem-s-computers). |
| **Pull backlog tickets when threads are free** | Off | Lets Rem start backlog tickets. Needs the board. |
| **Approve its own backlog tickets** | Off | Self-work's new ideas go to Backlog approved until self-work ends, instead of waiting for you. Asks you to confirm. See [Let Rem approve its own tickets](#let-rem-approve-its-own-tickets). |
| **Tokens per session** | 0 (no limit) | Self-work starts nothing new once its runs used this many tokens since it started this time. See [Self-work limits](#self-work-limits). |
| **Tokens per day** | 0 (no limit) | The same, counting from midnight on this computer, across every session that day |
| **Rate limit use** | 0 (no limit) | Self-work starts nothing new while a stored rate-limit window of the account it would run on is at least this percent full |
| **Allowance use** | Off | How self-work uses a weekly allowance: **Off**, **Daily cap** or **Pace to reset**. One choice, so the two never apply at once. See [Daily cap on the weekly allowance](#daily-cap-on-the-weekly-allowance) and [Pace to reset](#pace-to-reset). *New in 0.6.0.* |
| **Weekly allowance per day** | 14 when you choose Daily cap | With **Daily cap**: self-work starts nothing new once it used this percent of the weekly allowance of the account it would run on today: Claude Code's default sign-in, or one of your [accounts](./providers.md#self-work-on-accounts). A Grok account counts its credits, with the cap scaled to the credits period. *New in 0.6.0.* |
| **Aim by reset** and **Finish before reset** | 97% and 3 hours | With **Pace to reset**: how much of the allowance to have used when it resets, and how many hours before the reset to get there. *New in 0.6.0.* |
| **Accounts** | Rem's setting | Which account the runs self-work starts use, one choice per provider with accounts. See [Which account self-work uses](#which-account-self-work-uses). *New in 0.6.0.* |
| **Rem's projects** | None | Where Rem may work on its own. Each chosen project shows its **Level of effort** and threads now against its share. |
| **Level of effort** | Even split | Each of Rem's projects' share of self-work's threads; they total 100%. See [Level of effort](#level-of-effort). *New in 0.6.2.* |

<AppScreenshot name="selfwork-settings" caption="Self-work in Settings > Rem: when Rem starts on its own, how many threads it keeps going, which projects it may touch, and its latest report." />

Under the switch: the [self-work window](#timed-self-work) running now and up to five planned ones, each with when it ends and what the switch does after. **End now** ends the running one and posts its report; **Cancel** drops a planned one. During a window the switch shows on, with a note when it goes back to off afterwards. **Last self-work period** sums up the last [report](#the-report-when-self-work-stops).

**When to stop** shows the [stop condition and latest time](#stop-condition-and-latest-time) of the self-work running now and Rem's last check, such as "Last check 8:40 PM today: not met. REM-210 is still in review." **Edit** changes the condition or sets a new latest time (leave the time empty to keep the current one), and **Clear** removes both. With self-work on and no stop set, **Set when to stop** adds one. After self-work stops, it says how the last one went.

Below the settings: what's running now, with links to each thread (paused ones are marked), and the latest report. The report lists tickets added, work done, blocked tickets Rem picked back up and ones still waiting for you.

The board's sidebar shows the same status and report, when a window ends and the next planned one, with **Self-work settings** to open them. Rem's chat shows **While you were away** when the report has something new.

### Self-work limits

The limits under **Limits** in **Settings > Rem** cap what self-work spends while you're away. Each is off at 0.

- **When one is reached**, self-work starts nothing new: no tickets, backlog, research, ranking or picking blocked work back up, and paused self-work stays paused. Work already running finishes normally and updates its ticket.
- **The report says why.** Rem adds one line to the self-work report, such as "Stopped starting new work: self-work used 512k tokens this session, past its limit of 500k. Running work finishes normally." The status line in Settings and on the board shows the same reason.
- **A session** runs from when self-work starts after your quiet minutes (or a [window](#timed-self-work) opens) until you send a message. Self-work you [started now](#start-self-work-now) keeps one session while you chat, until it goes off. The next session starts counting again from zero.
- **The token limits** count only self-work's own runs, by the tokens providers reported. Calls a provider doesn't report don't count toward them. Work you ask for, including a ticket you start yourself, isn't limited and doesn't count.
- **Rate limit use** reads the windows Raticode last stored for the provider Rem's tasks use: the last reading in **Settings > Usage** and the limits recent turns reported. A window whose reset time has passed doesn't count. Self-work starts again on its own once the window resets or you raise the limit.
- **On an account.** When **Settings > Rem > Accounts** runs Rem's tasks on one of the provider's [accounts](./providers.md#self-work-on-accounts), **Rate limit use**, the daily cap and Pace to reset read that account instead of the default sign-in. With **Choose by usage**, self-work skips accounts at a limit and starts nothing only when none is left. A signed-in account whose window is full counts as at a limit, so self-work stops rather than moving its runs to the default sign-in; it uses the default sign-in only when no account is signed in. *New in 0.6.0.*

From the CLI: `Raticode selfwork set --expected-revision N --session-token-limit 500000 --daily-token-limit 2000000 --rate-limit-percent 80`.

### Room on Rem's computers

*New in Raticode 0.6.0.*

Organization workers and phone turns share Rem's computers. Before self-work starts a ticket, research or ranking, or picks paused work back up, it checks how much room [placement](./home.md#where-work-runs) gives Rem right now: the free workers on every computer Rem's placement may use, less Rem's tasks already queued for one. It starts no more than that, so a ticket it moves to **In progress** gets a worker straight away instead of waiting for one.

- **Full computers.** With no room, self-work starts nothing new and says which computers are full, such as "Computers full: Workstation 4 of 4 running". Work already running finishes normally, and self-work goes on by itself once a worker is free. The status line in Settings and on the board, **Desktop settings › Rem self-work** on the phone and `Raticode selfwork status` show the reason, and the self-work report says it once per session.
- **No reading, no new work.** If Raticode can't read its computers, self-work starts nothing new and says why, such as "Waiting for the computers' reading: …", as queued tasks do.

### Daily cap on the weekly allowance

*New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*

Choose **Daily cap** under **Allowance use**. **Weekly allowance per day** then caps how much of your Claude Code weekly allowance self-work uses in one day, however many times it starts. Set it to 11 and self-work stops starting new work once it has used about 11% of the week's allowance today, so you can leave self-work on every night and keep the rest for your own work. Choosing Daily cap starts at 14%, about a seventh of the week. Setting the percent to 0 turns it off.

- **What counts.** Claude reports one percent for your whole account's weekly allowance, not per app or task, so Raticode can't read self-work's share directly. Instead it watches that percent and adds up how much it went up while self-work had work running. Rises while self-work runs nothing don't count.
- **Your own use can count.** Anything else that uses the same Claude account while self-work runs, such as Claude Code in a terminal, Rem's chat or a task you started, counts toward the cap too. Raticode can't tell them apart in Claude's number, so the cap errs on the side of stopping early. Self-work usually runs while you're away, so this matters most during a [window](#timed-self-work) that doesn't wait for quiet minutes.
- **The day** starts at midnight in this computer's time zone. The count is kept with Rem's other settings, so it carries across separate self-work sessions, windows and restarts of Raticode, and starts again from zero at midnight.
- **At the cap**, self-work starts nothing new, the same as the other limits: tasks already running finish, so the day can end a little over the cap. The status line in Settings and on the board says why, the self-work report gets one line such as "Stopped starting new work: self-work used about 11.2% of the Claude Code weekly allowance today, reaching its daily cap of 11%. It starts again after midnight. Running work finishes normally.", and the [report when self-work stops](#the-report-when-self-work-stops) repeats it with today's total.
- **Readings.** The percent comes from **Settings > Usage** (Raticode reads it about every 10 minutes) and from the limits Claude Code reports with its replies. With the cap on, self-work starts new work only with a reading from the last 30 minutes, and asks for a new one when the last is older than 10 minutes. Readings arrive every few minutes at best, so the count can lag a little behind.
- **No reading, no new work.** If Raticode has no current reading (Claude's usage page can't be reached, it's rate-limited, or you're signed out), self-work starts nothing new and says so: "Starting nothing new: the daily cap needs a Claude Code weekly allowance reading from the last 30 minutes…". It goes on by itself once a reading comes in. It never runs past the cap without saying so; turn the cap off to run without it.
- **Which allowance.** With the default sign-in, the cap reads the Claude Code weekly allowance of the account Claude Code is signed in to. When Rem's tasks run on one of a provider's [accounts](./providers.md#self-work-on-accounts), it reads that account's weekly allowance (Claude Code or Codex) and keeps a separate count for each account: a run on `work` counts against `work`. A Grok account reports its billing period's credits instead of a weekly allowance, so the cap counts how much of those credits self-work used today, against the cap's share of a week moved to the credits period: with a 10% cap and a 30-day period, the day's limit is about 2.3% of the month's credits (10% × 7 ÷ 30). Messages then name the credits, such as "Used today: about 1.2% of work's monthly credits, of a daily cap of 2.3% (the 10% weekly cap over a 30-day period)." With **Choose by usage**, self-work skips an account at the cap and starts the task on another; it starts nothing only when every account is at a limit. If Rem's tasks run on a provider's default sign-in other than Claude Code, use **Rate limit use** for that provider.
- **Today's use** shows under the setting, such as "Used today: about 4% of 11% of the Claude Code weekly allowance. Resets at midnight.", or "...of work's weekly allowance" on an account. Telling Rem "is self-work on?" includes it too, such as "Today self-work used about 12% of work's weekly allowance; its daily cap is 15%."

From the CLI: `Raticode selfwork set --expected-revision N --daily-allowance-percent 11`. **Desktop settings › Rem self-work** on the phone has the same setting.

### Pace to reset

*New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*

Choose **Pace to reset** under **Allowance use** to leave self-work on all week and end each week close to the whole allowance without running out. Self-work starts new work only while the account is under a pace line: a straight line from 0% when the weekly window starts to the aim (97% by default) a few hours before the reset (3 by default), then flat until the reset.

**Aim by reset** takes any share of the allowance from 1% to 100%. Keep 97% to use nearly the whole week, or choose 50% to give self-work half the week and keep the other half for yourself; the pace line, the status line and the hold message all follow the aim you choose.

- **Behind the line**, self-work fills its threads, up to **Max threads**. Time lost earlier, such as a night with Raticode closed, is made up once it can run again.
- **Ahead of the line**, nothing new starts. Tasks already running finish, and your own work is never held: tickets you start, tasks you ask for and Rem's chat run whatever the pace says. Self-work's report says once per session that it is holding, such as "Holding new work: Codex's weekly allowance is 60% used, 3.4% ahead of its pace line (56.6%). Next start around 18:20. Running work finishes normally."
- **Room for running work.** Each self-work task already running keeps 1.5 points of room under the line for the cost it hasn't shown yet, so a new task starts only when the line is that far ahead of the reading for every running task plus the new one.
- **Readings only.** It reads only the percent and reset time the provider reports (**Settings > Usage**, and the limits recent replies carry). It never estimates an allowance from token counts. A percent that flaps by a point counts as the highest reading of the last 30 minutes, so starts don't flicker.
- **Your use counts.** The percent is the whole account's, so your own use on any device counts. Self-work simply waits longer; what the aim leaves (3 points at the default 97%) and the hours before the reset are yours.
- **No reading, no new work.** Without a reading from the last 30 minutes, self-work starts nothing new and says so ("Starting nothing new: Pace to reset needs a reading of Codex's weekly allowance from the last 30 minutes…"), and asks for a new reading. It goes on once one comes in. When one window's reading is fresh and another's is older, the older one still counts at its last percent, since use only rises until a reset.
- **A moved reset** (a paid reset, or a provider change) starts a new line from the new window. A window that hasn't started yet, such as Codex at 0% with a reset that moves while nobody uses it, or one with no reset time, lets one task start at a time. A window with no reset time stops starting work once it reaches your aim.
- **Several weekly windows.** Where Claude reports per-model weekly windows beside the overall one, self-work paces against whichever is furthest ahead of its line. A 5-hour window at 90% or more holds new work until it resets, when Raticode has a reading of it.
- **Accounts.** Each account has its own line from its own reset time. With **Choose by usage**, self-work skips accounts with no room under their line and starts nothing only when none has room. A Grok account paces its credits over the billing period. Metered API keys have no allowance, so the pace holds nothing back on them.
- **The other limits stay.** Token limits, **Rate limit use**, windows and the stop condition still stop self-work on top of the pace. Settings warns when **Rate limit use** is set below the aim, because it would stop self-work first.
- **Status.** Under the setting, and in `Raticode selfwork status`, a line such as "Pacing Codex's weekly allowance: 40% used, line 48.5%. Up to 2 tasks can start. Resets Thu 05:00."

It runs only while Raticode is open, because readings come from the app. With [self-work windows](#timed-self-work) that cover only part of the week, the line still assumes every hour is available and catches up inside the window, bounded by **Max threads**. Pace to reset hasn't been checked against a live account for a whole week yet.

From the CLI: `Raticode selfwork set --expected-revision N --allowance-mode pace --pace-aim 97 --pace-lead 3`. `--allowance-mode off` or `daily` switches back. **Desktop settings › Rem self-work** on the phone has the same choice.

### Which account self-work uses

*New in Raticode 0.6.0.* When a provider has at least one [account](./providers.md#several-accounts) added in **Settings > Providers**, **Settings > Rem > Self-work** shows an **Accounts** group with one choice per provider:

| Choice | What self-work's runs use |
| --- | --- |
| **Rem's setting** | Whatever **Settings > Rem > Accounts** says for the provider. The default, shown with that setting, such as **Rem's setting (Choose by usage)** |
| **Default sign-in** | The provider's usual sign-in, even when Rem's setting names an account |
| **Choose by usage** | The provider's signed-in account with the most room left when the run starts |
| An account | That account |

- **Only self-started work.** The choice applies to the tickets, research and other tasks self-work starts on its own, and to their reviews. Rem's chat, your threads, tasks you create and a ticket you move to **In progress** yourself keep using Rem's setting.
- **The order.** A run self-work starts uses the task's own account first, if the task names one. Then self-work's choice for its provider. Then **Settings > Rem > Accounts**. A run stays on the account it started on, so a change applies to runs that start after it.
- **A removed account** stays shown as "removed" until you choose something else. Until then self-work's runs use the default sign-in, as Rem does when its own account is removed.
- Use it to keep self-work on a spare account, such as a second Claude subscription, while your own work stays on your main one.

From the CLI: `Raticode selfwork set --expected-revision N --account claude_code=spare --account codex=auto`. An empty value, such as `codex=`, goes back to Rem's setting; `@default` is the default sign-in. `Raticode rem settings export` and `apply` carry the same choices as `self_work_accounts`. **Desktop settings › Rem self-work** on the phone has the same choices.

### With the board turned off

You can hide the **Kanban** plugin in **Settings > Plugins** and keep self-work. Rem then leaves tickets alone:

- It doesn't start To do, Backlog or Blocked tickets, rank or merge the backlog, or write proposals.
- It spends its threads on research in its projects and lists the ideas each run finds in the report.
- The research shows in Rem's chat with task cards and updates, which name the ideas found.
- Each project is researched from each angle at most once a day, so quiet spells don't repeat it. With open [objectives](#self-work-honors-the-objectives), each objective takes the place of the angles, and the ideas are only for it.
- **While you were away** has **See the report**, which opens **Settings > Rem**.

Switching the board off or on mid-work is safe:

- Ticket work already started finishes, updates its ticket and counts toward the maximum threads.
- Research that finishes after the board is turned off adds its ideas to the report and creates no tickets.
- With the board back on, Rem takes up To do tickets again as threads come free.

### Limits and safety

- Research runs in rounds of at most 8, each project and angle once a round. When a round is done and nothing else is left, the next round starts an hour after the last one began, so a long session or [timed window](#timed-self-work) keeps its threads busy. It starts only if the last round found something new; otherwise research is done until self-work starts again.
- Each research run starts where the last one of its project and angle stopped. It records where the project's work stands: the tip of its `dev` branch when it has one, since finished work lands there whichever branch the project folder has checked out, or else the folder's current commit. Its instructions (shown in its task details) name the last report that project and angle saved, the commit that run started at (as "the commits on dev after" it, for a project with `dev`) and, with the board on, up to 30 of the project's tickets done, cancelled or declined in the last 14 days. It reads on from that report and the commits after it, and doesn't propose declined work again. Research for an [objective](#self-work-honors-the-objectives) starts from the last report for that objective in that project.
- A research run ends its answer with a list of at most five proposals; any after the fifth are ignored. A title, reason or description that runs long (over 160, 1,000 and 4,000 characters) is cut off rather than losing the idea. An idea with no usable title, a type other than develop, research or maintain, or a list of tickets it waits for or decisions it contradicts that can't be read whole is left out; the report lists it as **Didn't add “…”** with the reason. Those two lists are never emptied to keep the idea, because the decision guardrail and the board's order depend on them. When another field can't be used, such as the objective, the idea is kept without it and the report says **Used the proposal “…” only in part** with what was left out. New in 0.6.0. If the answer has no list Rem can read, the run goes back to its worker with the exact error before any review, within the same two repairs as a missing report.
- With the board on, research stops while 12 proposals are waiting, unless [auto-approval](#let-rem-approve-its-own-tickets) is on. Proposals waiting for your approval are never started on their own, so when self-work runs out of work its status says how many are waiting.
- Each piece of work is an ordinary [Rem task](./home.md#how-work-is-checked) with independent review. A ticket is done only after its run is accepted; a failed run blocks the ticket with the reason.
- Rem restarts a blocked ticket on its own at most once, until you move it (three times with [auto mode](#auto-mode-no-review-at-the-ticket-level) on). If it blocks again, Rem leaves it for you with a comment.
- A review whose files keep changing (another task landing on the same checkout) waits 1, 5 and 15 minutes and checks again before it blocks. A locked database counts as a temporary failure and is retried like a provider outage.
- Unattended runs can't push, publish, deploy, send messages outside the computer, spend money, change credentials, or start organizations or workflows. `git push` is blocked for them.
- They get no browser, subagents, swarms, fleet tools, Organizations tools or configured MCP servers. What they keep: Rem's report saving, the [sleep tool](./home.md#sleeping-tasks), and Second Brain when it's on (reviews don't get Second Brain).
- Clearing Rem's history removes the self-work report but keeps tickets and settings.

### Claude Code's sandbox

When a self-work run or any [review](./home.md#how-work-is-checked) uses Claude Code, its shell commands run in Claude Code's operating-system sandbox. Codex runs these in its own sandbox already, which from 0.6.0 also denies Raticode's [credential folders](../advanced/security.md#credential-folders-no-agent-can-reach). From 0.6.0, Codex's commands there can also write Raticode's data folder only where Claude Code's sandbox can; see [What Codex self-work, reviews and organization workers can write](../advanced/security.md#what-codex-self-work-reviews-and-organization-workers-can-write).

- **No network for the shell.** Commands such as `curl`, `git fetch`, `gh` or `npm install` can't reach any host. Web search and page reading still work when the task has web access, since those are separate tools.
- **Credentials are hidden.** The shell can't see `~/.ssh`, `~/.claude`, `~/.gnupg`, cloud and package-manager logins, Raticode's own data folder, or a headless backend's [secrets folder](../reference/headless.md#secrets-without-a-keychain). Claude Code's file-reading tool is kept from the same credential folders, because the sandbox only covers shell commands.
- **The project's own Claude Code settings aren't loaded.** A project's `.claude/settings.json` and `settings.local.json` can name hooks, and Claude Code runs hooks outside its sandbox, so a cloned repository could otherwise run anything as you. These runs load only your own Claude Code settings. The project's `CLAUDE.md` files still reach the agent: Raticode passes the ones in the project folder and the folders above it, up to but not including your home folder, as instructions (Claude Code already loads your own `~/.claude/CLAUDE.md`), and lists the ones in subfolders for the agent to read when it works there. Workflow agent steps that use Claude Code work the same way, and a profile that passes its own `--setting-sources` is refused.
- **Research and reviews can't change the project.** Test and tool caches go to a private scratch folder that's removed when the turn ends, except for a worker's [evidence folder](./attachments.md#where-workers-save-evidence). Self-work that develops or maintains a project can change only that project and, for a worktree, its repository's `.git` folder.
- **Empty files can appear in the project while a command runs.** To protect files like `.bashrc`, `.gitconfig`, `.mcp.json` and `.claude/settings.json`, the sandbox puts an empty read-only placeholder in the project folder for each one that doesn't exist, and Claude Code removes them when the command ends. They can show as new files in Source Control during a long command. If a run is stopped mid-command, Raticode removes any placeholders left behind when the turn ends.
- **Leftover temp files are cleared for you.** Claude Code points the shell's temp folder at `/tmp/claude-<uid>`, and the rest of `/tmp` and `/var/tmp` is read-only to it. On many Linux systems `/tmp` is held in memory, so browser profiles and builds that tests leave there take RAM until they're deleted. On Linux, Raticode clears them itself, outside the sandbox, when it starts, every hour and after a turn ends. It removes an entry in `/tmp/claude-<uid>`, or a test run's folder in `raticode-tests-<uid>` under `/tmp` or `/var/tmp`, only when it's yours, no process uses it, and nothing in it has changed for 3 hours (1 hour once that folder's file system is 80% full) or since a turn that's still running began. A folder holding a socket or pipe gets 12 hours. Links are removed without following them. The sandbox isn't widened for this. A Raticode started from inside a worker never clears these folders. New in 0.6.1.
- **Queued tests still wait for a slot**, but run inside the agent's own memory limits rather than getting a limit of their own.
- **If the sandbox can't start, the run doesn't.** Rem blocks the ticket, or marks the task **Couldn't finish**, with the reason, such as a missing `bubblewrap` package.

| System | Sandbox |
| --- | --- |
| Linux and WSL2 | Needs `bubblewrap` and `socat` installed |
| macOS | Built in |
| Windows | Not available. Self-work and reviews with Claude Code are blocked; use Codex for Rem's tasks, or run Raticode in WSL2 |

The worker of a task you ask Rem for runs without the sandbox, as before. Its review still uses it.

On the phone, the board is under **More › Global board**, where the **Self-work** section also lists the objectives and each Rem ticket shows the objective it advances, and self-work's settings are under **Desktop settings › Rem self-work**; see [Projects, boards and workflows on the phone](../mobile/workspace.md#global-board-and-self-work).
