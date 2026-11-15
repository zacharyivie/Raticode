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
- **Filters stay put.** Whose tickets, the search, the person you picked, **Closed** and the Board or People view are kept on this computer. They're still set when you come back to the board or restart Raticode. A filter that's on is highlighted, and **Clear filters** turns them all off. If a saved organization or person is gone, that filter shows everyone again and the others stay. Each [organization's board](../advanced/organizations.md#find-your-way-around) keeps its own filters, and the phone keeps its own.
- Organization tickets stay owned by their organization. Your changes go through its usual approvals and permissions, and reading the board never starts an organization.
- You can hand one of Rem's tickets to an organization. An organization's ticket can't go to Rem; create a new one instead.
- Rem can create, edit, move and assign tickets when you ask in chat. It can't approve its own proposals or mark a ticket done. The one exception is [auto-approval](#let-rem-approve-its-own-tickets), which only you can turn on.
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
- **Same board only.** Rem's tickets wait for Rem's tickets, and an organization's for its own. For an organization's ticket this is the same list as its dependencies in the [Organizations](../advanced/organizations.md#tasks-and-independent-review) tab.
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
- the feature branch a project's worktree skill had the worker create. Those usually land on `dev` by themselves; the sheet still shows their patch.

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
- **Turn it on or off** in [Settings](#settings), or tell Rem: "turn on self-work", "stop self-work", "is self-work on?". See [Turn it on or off from the chat](#turn-it-on-or-off-from-the-chat).
- **Set a time** by telling Rem: "enable self-work until 7 PM tonight", "self-work from 11 PM to 7 AM". See [Timed self-work](#timed-self-work).

With the board on, Rem fills its free threads, up to the maximum, in this order. A ticket that [waits for another](#tickets-that-wait-for-other-tickets) is skipped until every ticket it waits for is Done.

1. **Its own Blocked tickets** in its projects, once per block, since that work was already under way. Rem retries an interrupted review, follows up in the task's thread after a failure, or restarts a ticket that couldn't start. A ticket that needs you (an answer, something you stopped, something outside this computer) stays Blocked, and Rem comments with exactly what's needed.
2. **To do tickets** in its projects, by priority.
3. **Backlog tickets** it ranks as most worth doing, only if **Pull backlog tickets when threads are free** is on (off by default). A ticket Rem judges no longer makes sense stays in Backlog with the reason.
4. **Research** with any threads left. Each run studies one project for new features, reliability and security, or speed and upkeep. It saves a report and proposes tickets. Research repeats in rounds; see [Limits and safety](#limits-and-safety).

Proposals wait in Backlog, marked as proposed, until you choose **Approve for To do** or **Decline**. Nothing proposed runs until you approve it, unless you turn on [auto-approval](#let-rem-approve-its-own-tickets).

### Let Rem approve its own tickets

**Approve its own backlog tickets** in **Settings > Rem** lets self-work skip the approval step for the tickets its research proposes. It's off by default.

- **What it does.** New ideas from self-work research go to Backlog already approved instead of waiting for you. With **Pull backlog tickets when threads are free** on, Rem then ranks them with the rest of the backlog and works on the best ones. With pulling off, they wait in Backlog until you move them to To do.
- **How they're marked.** Each one has an **Auto-approved** badge on the board and the phone, and its history starts with an **Auto-approved by self-work** entry saying you haven't reviewed it. The report and Rem's chat update say how many it approved.
- **They're still yours.** Edit, move or cancel them like any ticket. Rem still can't mark its own tickets Done; they finish through the usual independent review.
- **No cap on the number.** It keeps approving new ideas until self-work ends: when you send a message, a [window](#timed-self-work) ends or you turn self-work off. How much it researches is set by the usual [limits](#limits-and-safety), and your [token and rate limits](#self-work-limits) stop it starting new work.
- **What's still left out.** An idea that repeats a ticket on the board, in an organization or one you declined isn't added. A research run may close one of these tickets as a duplicate, never one you wrote. When Rem ranks the backlog it can leave one out as no longer making sense, like any other.
- **Its other limits still apply**: Rem's projects, **Max threads**, the [token and rate limits](#self-work-limits), Blocked tickets and To do first, and [your work first](#your-work-comes-first).

Turning it on asks you to confirm what it allows. Only you can change it: in **Settings > Rem**, on a paired phone, or with `Raticode selfwork set --auto-approve --confirm-auto-approve` from your own terminal. Rem can't turn it on from the chat, and neither can its workers or agent tokens.

When you change it:

- **Turned on**: proposals already waiting stay waiting for you. Only ideas found from then on are approved, including those from research already running.
- **Turned off**: new ideas wait for your approval again, including those from research still running. Tickets it already approved stay as they are.

### Your work comes first

Work you ask for counts against **Max threads** ahead of self-work. Self-work makes room only when your work and self-work together don't fit, and then exactly one self-work task per task of yours that doesn't fit:

| Max threads | Self-work running | You start | What pauses |
| --- | --- | --- | --- |
| 4 | 4 | 1 task | The lowest-priority self-work task |
| 4 | 4 | 2 tasks | The two lowest-priority ones |
| 4 | 2 | 1 task | Nothing: a thread is still free |


- **What counts as yours**: a chat request that starts a task or a follow-up, a task you start or run, a ticket you move to **In progress**, and your scheduled tasks.
- **What pauses first**: the lowest priority. Research and backlog ranking go before any ticket, then tickets from **Low** up to **Critical**. Among equals, one that hasn't started goes first, then the most recently started. Your own tasks never pause, and neither does self-work you resumed yourself.
- **How it pauses**: like [pausing a task](./home.md#pause-and-resume-a-task). The run, its thread and any worktree are kept, and it frees its thread. Its card reads **Paused for your work**, and its ticket shows **Blocked** with a comment saying why.
- **When it resumes**: as soon as a thread is free again, for example when one of your tasks finishes, even while you're still here. The highest priority picks up first, before Rem starts anything new. It continues in the same thread, so nothing is done twice. You can also resume it yourself at any time, or move its ticket to **In progress**.

Rem says so once in the chat ("Paused 1 self-work task so your work runs first: …"), and the self-work status and report say how many are paused. A ticket you pause yourself stays paused until you resume it.

### Turn it on or off from the chat

Tell Rem in your own words, typed or spoken, on the desktop or the phone:

| You say | What happens |
| --- | --- |
| "turn on self-work", "start self-work" | Self-work turns on |
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
| **Approve its own backlog tickets** | Off | Self-work's new ideas go to Backlog approved until self-work ends, instead of waiting for you. Asks you to confirm. See [Let Rem approve its own tickets](#let-rem-approve-its-own-tickets). |
| **Tokens per session** | 0 (no limit) | Self-work starts nothing new once its runs used this many tokens since it started this time. See [Self-work limits](#self-work-limits). |
| **Tokens per day** | 0 (no limit) | The same, counting from midnight on this computer, across every session that day |
| **Rate limit use** | 0 (no limit) | Self-work starts nothing new while a stored rate-limit window of Rem's task provider is at least this percent full |
| **Rem's projects** | None | Where Rem may work on its own |

<AppScreenshot name="selfwork-settings" caption="Self-work in Settings > Rem: when Rem starts on its own, how many threads it keeps going, which projects it may touch, and its latest report." />

Under the switch: the [self-work window](#timed-self-work) running now and up to five planned ones, each with when it ends and what the switch does after. **End now** ends the running one and posts its report; **Cancel** drops a planned one. During a window the switch shows on, with a note when it goes back to off afterwards. **Last self-work period** sums up the last [report](#the-report-when-self-work-stops).

Below the settings: what's running now, with links to each thread (paused ones are marked), and the latest report. The report lists tickets added, work done, blocked tickets Rem picked back up and ones still waiting for you.

The board's sidebar shows the same status and report, when a window ends and the next planned one, with **Self-work settings** to open them. Rem's chat shows **While you were away** when the report has something new.

### Self-work limits

The three limits under **Limits** in **Settings > Rem** cap what self-work spends while you're away. Each is off at 0.

- **When one is reached**, self-work starts nothing new: no tickets, backlog, research, ranking or picking blocked work back up, and paused self-work stays paused. Work already running finishes normally and updates its ticket.
- **The report says why.** Rem adds one line to the self-work report, such as "Stopped starting new work: self-work used 512k tokens this session, past its limit of 500k. Running work finishes normally." The status line in Settings and on the board shows the same reason.
- **A session** runs from when self-work starts after your quiet minutes (or a [window](#timed-self-work) opens) until you send a message. The next session starts counting again from zero.
- **The token limits** count only self-work's own runs, by the tokens providers reported. Calls a provider doesn't report don't count toward them. Work you ask for, including a ticket you start yourself, isn't limited and doesn't count.
- **Rate limit use** reads the windows Raticode last stored for the provider Rem's tasks use: the last reading in **Settings > Usage** and the limits recent turns reported. A window whose reset time has passed doesn't count. Self-work starts again on its own once the window resets or you raise the limit.

From the CLI: `Raticode selfwork set --expected-revision N --session-token-limit 500000 --daily-token-limit 2000000 --rate-limit-percent 80`.

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

- Research runs in rounds of at most 6, each project and angle once a round. When a round is done and nothing else is left, the next round starts an hour after the last one began, so a long session or [timed window](#timed-self-work) keeps its threads busy. It starts only if the last round found something new; otherwise research is done until self-work starts again.
- With the board on, research stops while 12 proposals are waiting, unless [auto-approval](#let-rem-approve-its-own-tickets) is on. Proposals waiting for your approval are never started on their own, so when self-work runs out of work its status says how many are waiting.
- Each piece of work is an ordinary [Rem task](./home.md#how-work-is-checked) with independent review. A ticket is done only after its run is accepted; a failed run blocks the ticket with the reason.
- Rem restarts a blocked ticket on its own at most once, until you move it. If it blocks again, Rem leaves it for you with a comment.
- Unattended runs can't push, publish, deploy, send messages outside the computer, spend money, change credentials, or start organizations or workflows. `git push` is blocked for them.
- They get no browser, subagents, swarms, fleet tools, Organizations tools or configured MCP servers. What they keep: Rem's report saving, the [sleep tool](./home.md#sleeping-tasks), and Second Brain when it's on (reviews don't get Second Brain).
- Clearing Rem's history removes the self-work report but keeps tickets and settings.

### Claude Code's sandbox

When a self-work run or any [review](./home.md#how-work-is-checked) uses Claude Code, its shell commands run in Claude Code's operating-system sandbox. Codex runs these in its own sandbox already.

- **No network for the shell.** Commands such as `curl`, `git fetch`, `gh` or `npm install` can't reach any host. Web search and page reading still work when the task has web access, since those are separate tools.
- **Credentials are hidden.** The shell can't see `~/.ssh`, `~/.claude`, `~/.gnupg`, cloud and package-manager logins, Raticode's own data folder, or a headless backend's [secrets folder](../reference/headless.md#secrets-without-a-keychain). Claude Code's file-reading tool is kept from the same credential folders, because the sandbox only covers shell commands.
- **Research and reviews can't change the project.** Test and tool caches go to a private scratch folder that's removed when the turn ends. Self-work that develops or maintains a project can change only that project and, for a worktree, its repository's `.git` folder.
- **Empty files can appear in the project while a command runs.** To protect files like `.bashrc`, `.gitconfig`, `.mcp.json` and `.claude/settings.json`, the sandbox puts an empty read-only placeholder in the project folder for each one that doesn't exist, and Claude Code removes them when the command ends. They can show as new files in Source Control during a long command. If a run is stopped mid-command, Raticode removes any placeholders left behind when the turn ends.
- **Queued tests still wait for a slot**, but run inside the agent's own memory limits rather than getting a limit of their own.
- **If the sandbox can't start, the run doesn't.** Rem blocks the ticket, or marks the task **Couldn't finish**, with the reason, such as a missing `bubblewrap` package.

| System | Sandbox |
| --- | --- |
| Linux and WSL2 | Needs `bubblewrap` and `socat` installed |
| macOS | Built in |
| Windows | Not available. Self-work and reviews with Claude Code are blocked; use Codex for Rem's tasks, or run Raticode in WSL2 |

The worker of a task you ask Rem for runs without the sandbox, as before. Its review still uses it.

On the phone, the board is under **More › Global board**, and self-work's settings are under **Desktop settings › Rem self-work**; see [Projects, boards and workflows on the phone](../mobile/workspace.md#global-board-and-self-work).
