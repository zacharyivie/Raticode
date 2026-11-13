---
title: Rem's home and tasks
description: Talk to Rem in one ongoing conversation, hand it tasks, and see how worker threads and independent review check the work.
pageClass: docs-layout
---

# Rem's home and tasks

The rat-face **Rem** entry at the top of the sidebar opens Rem's home. It holds one ongoing conversation with Rem and the list of work Rem is responsible for. There is no thread picker.

- Rem answers simple questions itself.
- For research, a code change or an errand, it starts a linked **worker thread**.
- Your own threads live in the [Rem pane](./chat.md).

<AppScreenshot name="rem-home" eager caption="Rem's home: the conversation in the middle, and under Coming up the scheduled tasks with their next run times." />

## Talk to Rem

Enter sends; Shift+Enter adds a line. While Rem replies, the header says what it's doing. With Claude Code as the chat provider, the reply appears as it's written.

Rem's chat provider, model and effort are set in **Settings > Rem**: **Rem chat provider**, or the **Default provider** when it has none of its own. See [Providers, models and effort](./providers.md).

### What a reply can contain

| Part | What it does |
| --- | --- |
| **Show more** | Long replies fold to a preview. Rem's newest message starts open. |
| **Details** | An expandable section with tables, task lists, code and simple charts. |
| Suggested replies | Up to three you can tap. |
| Task card | The task's type, phase and checks, with **Open thread**. Under the newest update about a finished task: **Open report**, **Retry review** and **Run again**. |
| Files | Rem can send you files, such as a saved report. |

A task update's message is a few sentences, at most 700 characters, and a chat reply's is at most 4,000. If Rem writes more, the message stops after the last full sentence that fits and the rest opens **Details**. If Rem can't write a task update at all, for example because its provider is down, the update shows the opening of the task's result, with the rest of the result under **Details**.

### Ticket pills

A [board ticket](./board.md) key such as `REM-5`, or an organization's `ENG-18`, shows as a pill with the ticket's status mark, key and current title. Click it, or Tab to it and press Enter, to open the ticket on the global board.

- A task card lists the tickets its task created, worked on or changed as pills. The task's details list the same tickets under **Tickets**.
- A deleted ticket keeps its key and old title, struck through. A renamed ticket's pill shows its new title.
- Only keys of real tickets become pills. A made-up key, text inside code, or part of a longer word (`UTF-8`, `v2-REM-5`) stays as typed.

<AppScreenshot name="ticket-pills" caption="Ticket keys in Rem's reply become pills with each ticket's status mark and title. The task card below lists the ticket it works on." />

### Work Rem starts on its own

[Self-work](./board.md#self-work) shows in the chat like work you asked for: a task card when it starts and an update when it finishes.

- Cards started together share one line, such as "Started 3 things while you were away: 2 tickets and 1 research run."
- Ranking and research updates name the tickets they proposed, left out or closed.
- The **While you were away** card sums up what self-work did since you last looked.
- When self-work stops, after a [timed window](./board.md#timed-self-work) or when you turn it off, Rem posts [a report](./board.md#the-report-when-self-work-stops) listing each task it worked on and how it went. Select one to open it.

### Project folders and files

Rem can open or create a local project folder when your request needs one. It never opens a project in places that hold sign-ins or system state. It sends you a file from one of those places only when your own message names that exact file.

Those places, on Linux, macOS and Windows:

- SSH, GPG and cloud tool folders, and coding agents' sign-ins
- Browser profiles: Chrome, Chromium, Edge, Brave, Vivaldi, Opera and Firefox
- macOS keychains and cookies; Windows saved credentials and protected keys
- Raticode's own data

Files whose names usually hold secrets, such as `.env`, keys and certificates (`.key`, `.pem`, `.p12`), follow the same rule wherever they are. A request from a paired phone never gets keys, certificates or password stores, even when it names the exact file, and a phone that asks for them is locked until you unlock it on the computer. See [Suspicious requests lock the phone](../mobile/pairing.md#suspicious-requests-lock-the-phone).

### Search the conversation

Click the magnifying glass in the chat header, or press Ctrl+F (Cmd+F on a Mac) in Rem's chat. Search covers the whole conversation, back to its first message.

- It looks in your messages, Rem's replies and their **Details**, task updates, task card titles and attached file names.
- It ignores case, and every word you type must appear in the message. The desktop matches words itself; no model reads them.
- Results come newest first, with the time and a highlighted snippet. A match found only in a task title or file name says so. **Show more results** loads the next 50.
- Click a result, or press Enter for the newest, to jump there. The message unfolds and the matching words flash.
- **Done** or Escape closes search without moving the chat. Your scroll position and unsent message, with its attachments, stay as they were.

## Tasks

The sidebar groups Rem's tasks:

| Group | Holds |
| --- | --- |
| **Working on** | Running tasks (spinner and phase) and queued ones (clock) |
| **Needs you** | Runs that failed, need your answer or ended unclear, and tasks on hold. It reads **Updates** when none of them needs an answer from you. |
| **Coming up** | Scheduled tasks, tasks waiting for others, and tasks with no schedule |
| **Paused** | Paused runs (**Paused**, or **Paused for your work**) and tasks whose schedule you paused (**Schedule paused**) |
| **Done** | Tasks whose last run finished |

Each task's menu has **Edit**, **Pause** or **Resume** for a run, **Run now** (or **Run again**, or **Stop current run**), **Pause schedule** or **Resume schedule**, **Dismiss** for runs that need you, and **Delete**.

- **Pause** pauses the run that is going. See [Pause and resume a task](#pause-and-resume-a-task).
- **Pause schedule** stops future runs of a repeating task. A run already going carries on.
- **Stop** cancels the current run. A stop that can't be confirmed shows as an unknown outcome and isn't retried automatically.
- **Delete** removes the task but keeps its history.

### Pause and resume a task

Any running or waiting task can be paused, whether you asked for it or self-work started it. Use its menu or task sheet, the phone, `Raticode rem runs pause`, or ask Rem ("pause that task", "resume the audit").

| | Pausing a run | Pausing a schedule |
| --- | --- | --- |
| **What stops** | The run going now | Future runs of a repeating task |
| **What it keeps** | The same run, its thread, the provider's session and any worktree | The task |
| **Its thread slot** | Freed while paused | Not affected |
| **Undo** | **Resume** | **Resume schedule** |

**How pausing works.** Raticode stops the worker's current turn right away, the same way **Stop** does, but keeps the run instead of ending it. No provider can freeze a turn halfway, so this is stop-and-resume for every provider:

- Work finished before the pause stays: edits, commits and saved reports.
- A command running at that moment is cut off, not continued.
- **Resume** sends a new turn into the same thread. The worker is told it was paused and checks what it already did before carrying on.
- The provider continues its saved session when it has one, and otherwise reads the thread so far.
- A run paused while its work was being reviewed resumes with a fresh review of the saved work. A run paused before it started simply starts.

**While a run is paused:**

- It shows **Paused** in the task list, on its chat card and on the phone, at the step it reached.
- It isn't finished and doesn't need you, so it never shows as **Done** or **Needs you**. Its board ticket moves to **Blocked** with a "Paused" comment saying why.
- A task that [waits for it](#start-a-task-after-another) keeps waiting.
- Scheduled times for a repeating task pass without starting a second run.
- Restarting Raticode keeps it paused. A pause asked for just before a restart settles as paused, not stopped.

**Resuming** continues the same run, never a fresh copy. **Run again** and a follow-up message in its thread also resume it. **Stop** ends a paused run.

### Add or edit a task

Choose **+**, or just ask Rem in the chat. The task sheet has:

| Field | What it sets |
| --- | --- |
| **What should Rem do?** | The worker's instructions |
| **Kind of work** | **Research**, **Maintain** or **Develop**. Rem also creates **Errand** and **Reminder** tasks, shown read-only. |
| **Project** | The folder the work happens in. Develop tasks for standalone tools can run without one. |
| **When** | **Once**, **Daily**, **Weekly** or a **Custom** cron schedule |
| **Start after** | One-time tasks only: tasks this one waits for. See [below](#start-a-task-after-another). Rem can also set [other start conditions](#start-at-a-time-or-after-what-is-running). |
| **Done when** | Acceptance criteria. Leave it empty and Rem proposes them, tagged **Suggested**. |

Edits apply when you save. If the task is running, the button reads **Save and update** and Rem updates the running thread.

### Start a task after another

A one-time task can wait for other tasks and start by itself once all of them succeed. Ask in your own words ("after the audit finishes, start four threads to fix what it finds"), or pick tasks under **Start after**. The button then reads **Queue task**.

<AppScreenshot name="rem-task-waiting" caption="A task waiting for another one. Its sheet shows the task it waits for and where that task stands, with Start now to skip the wait." />

**While it waits**, the task sits under **Coming up** with an hourglass and a line such as "Waiting for “Audit the CLI” to finish". Its sheet lists each task it waits for and where that task stands. Tap a title to open it.

**When they all succeed**, Rem starts it the usual way and posts a chat card. It still waits for a free worker under [Capacity](#capacity), works only in its own project and gets the same review. Its worker is told what the earlier tasks found, with their summaries and saved reports.

A task it waits for being paused only delays it.

**If one fails**, or is stopped, blocked, deleted, has its schedule paused or will never run, the waiting task moves to **Needs you** as **On hold** with the reason. Rem says so once in the chat. Then:

- **Retry “…”** runs that task again, and the waiting task goes back to waiting.
- **Start now** starts the waiting task straight away.
- **Delete** drops it.

**When the next step depends on the results**, Rem doesn't guess the split up front. It holds your request and answers it once the first task succeeds, using its results. The request shows as **Queued until “…” finished**, with Rem's answer below.

Rules:

- A task can't wait for itself, and tasks can't wait for each other in a loop.
- Repeating tasks can't wait. A one-time task can wait for a repeating one: it waits for that task's next run, or counts its last success when no run is coming.
- Before a task starts you can change or clear what it waits for. Clearing it starts the task. A task that already started keeps what it waited for.
- Waiting is saved with the task. Closing or restarting Raticode never starts a task twice or loses one that was waiting.

### Start at a time, or after what is running

Tell Rem when a one-time task should start:

| You say | It waits until |
| --- | --- |
| "at 7 AM" | That time, in the computer's time zone unless you name one |
| "after the audit finishes" | The named tasks succeed. One failing puts it [on hold](#start-a-task-after-another). |
| "after everything running is done" | Every Rem task queued or running when you asked has ended, however it ended: done, failed, blocked or stopped. Work started later, and tasks already paused when you asked, don't count. A listed task that's paused later keeps it waiting until it's resumed and ends. |
| "whenever nothing is running" | No other Rem task is queued or running. This is checked live, and while the task is ready, self-work starts nothing new so it gets its turn. |
| "after the self-work window ends" | The running [self-work window](./board.md#timed-self-work), or the next one, ends |

- **Combined**, every condition must be met, so "at 7 AM, after everything running is done" starts at whichever comes later.
- **While it waits**, the task shows under **Coming up** with a line such as "Waiting for 2 running tasks to finish and until 7:00 AM Fri (Asia/Irkutsk)". Its sheet lists each condition. Tasks open when you select them; times and windows are plain lines.
- **Start now** starts it straight away and drops every condition.
- It starts once, and the conditions are saved with it, so a restart doesn't lose or repeat it.

## How work is checked

Each run of a work task goes through **Plan**, **Work**, **Check** and **Report**.

1. **Work.** A worker thread does the work with the task's provider and the project's permissions. Rem answers the worker's routine questions. It asks you only for a missing fact or something only you can authorize, such as publishing, deploying, spending money or deleting something permanently.
2. **Check.** A separate, read-only **review** checks every acceptance criterion against files, command results and saved reports. Evidence it can't confirm doesn't pass. Research must save a report.
3. **Repair.** If the review fails, the work goes back to the worker, up to two times. After that the task moves to **Needs you** with the reason.

Task cards show how many checks passed, failed or haven't run.

A task shows **Plan** only until its work starts. A [sleeping](#sleeping-tasks) task stays at **Work**. A run stays at **Check** when it waits to be checked again: you retried the review, the review hit a temporary provider problem, Raticode restarted mid-review, or the proof depends on a later event.

A finished run keeps its result on every card, on the computer and the phone, even after its task is deleted.

### Seeing which checks failed

When a run needs you, or is being fixed after review, open the task. Under the run's summary the sheet lists:

| List | Shows |
| --- | --- |
| **Failed** | Each failed check by name, with the reviewer's reason |
| **Unresolved** | Checks the review couldn't confirm, and why. A pass with no backing, such as no recorded test run, lands here. |
| **Not checked** | Checks the review didn't reach |
| **Passed** | Folded away; click to see each check and its evidence |

<AppScreenshot name="rem-task-checks" caption="A run that couldn't finish. The task lists each check by outcome, with the reviewer's reason for every failed or unresolved one. This group reads Updates because nothing here needs an answer from you." />

Long reasons show three lines; **Show more** opens the rest. Before a review runs, the sheet only counts the checks that haven't run. The phone's task screen shows the same lists (it needs an up-to-date computer).

**Retry review** re-checks saved work without redoing it, for when a review was interrupted. A worker thread keeps one transcript across runs of the same task, with its steps folded under **Show thoughts**.

### Changes land on dev

This applies when a project has a worktree skill on in **Settings > Thread tools** whose description says its branches land on `dev`, such as `worktree-dev-flow` ("…then rebases onto dev and fast-forwards dev"). A develop or maintain task then:

1. makes its change on its own branch
2. rebases onto local `dev`, reruns its checks and fast-forwards `dev`, without asking, in whichever folder has `dev` checked out with no uncommitted changes
3. pushes nothing

Before accepting, Rem checks the worker's branches. One with commits not on `dev`, or uncommitted changes, goes back to the worker. If repairs don't land it, the task moves to **Needs you** naming the branch.

- Branches other tasks are working on at the same time don't count.
- A request to keep the work off `dev` ("don't merge it into dev") skips the check.
- A worktree skill that doesn't mention `dev`, such as a general "use git worktrees" skill, stops Rem making its own worktree but never turns this check on, even if the repository has a `dev` branch.

## Long waits and time limits

Rem stops a worker's turn when it has been silent for **20 minutes**, and any turn after **2 hours** in total, even an active one. Silent means no output from the provider. A test, build or browser run that goes through **Queue tests and builds** counts as activity while it runs or waits for a slot (not on Windows). Background shell jobs, `sleep`, `gh run watch` and other quiet commands don't count, and stopping the turn ends them too.

A stopped turn leaves the task **Couldn't finish** with "showed no activity for 20 minutes" or "reached Rem's 120-minute turn limit". Its thread, provider session and worktree are kept, so asking Rem to continue picks up where it was. Reviews have the same limits.

### Waiting for CI and other outside jobs

Workers are told to check outside jobs in the foreground in short rounds: a status check about every minute, stopping at the first failed job and after at most **9 minutes**, then check again. For anything longer they put the task to sleep.

### Sleeping tasks

*New in Raticode 0.5.4 and Raticode Mobile 0.34.0.*

A worker waiting for something outside Raticode, such as a 40-minute CI run, can put its task to **sleep**. It says what it waits for and when to check again, then ends its turn. Rem then:

- frees the task's worker, so other tasks can run, and keeps its thread, provider session and worktree
- wakes it at that time in the same thread, telling the worker its wait is over so it checks again
- reviews the work only after the worker's final answer. Sleeping is never a result.

| Limit | Value |
| --- | --- |
| One sleep | 5 to 120 minutes |
| All sleeps of one run | 12 hours by default; the worker can ask for up to 8 days |
| Wakes per run | 60 |

A sleep that would pass either limit is refused, and the worker is told to finish or report what is still pending. A sleep found over a limit when its turn ends stops the task as **Couldn't finish**, with "Waited 12 hours for … and it never finished". Asking Rem to continue gives it a fresh budget.

**While it sleeps**, the task stays under **Working on** with a moon, what it waits for and its next check. The task shows how many times it woke and how much of its wait budget is used.

- **Wake now** wakes it straight away. It starts as soon as a worker is free. Sleeping work Rem started on its own doesn't count against self-work's thread limit.
- **Stop current run** ends it, as for any task.
- **Pause** wins over a sleep. A paused sleeping task keeps its wait. On **Resume** it wakes at once if its check time has passed, otherwise it sleeps until then. Time paused doesn't use its wait budget.
- A message in its thread, a follow-up from Rem or an edit to the task wakes it with that message instead. The wait budget starts again.
- After a restart it keeps sleeping, and one whose time passed wakes on the next check. If Raticode closed between the worker asking to sleep and its turn ending, the task still sleeps, and the worker is told to check what it already did.
- Tasks that [wait for it](#start-a-task-after-another) keep waiting.
- A linked board ticket moves to **Blocked** ("Blocked: sleeping") while it sleeps and back to **In progress** when it wakes. Each stretch of sleeps adds one entry of each to its timeline, however often it wakes.

Workers get the sleep tool in work and repair turns. Reviews keep their own wait for a future event (see [How work is checked](#how-work-is-checked)). A sleep starts only when a turn ends: no provider can be paused in the middle of a turn. A provider that can't use Raticode's task tools gets only the instructions for checking in short rounds.

The phone shows sleeping tasks and **Wake now** once both apps are updated. `Raticode rem tasks wake TASK_ID` wakes one from the [CLI](../reference/cli-rem.md#tasks-and-runs).

## Capacity

**Settings > Rem > Rem task workers** sets how many Rem tasks (and phone turns) run at once. 0 picks a number from this computer's cores and memory. Paused runs don't take a slot.

Work you ask for goes ahead of [self-work](./board.md#your-work-comes-first): when both don't fit, self-work pauses to make room.

**Queue tests and builds** makes tests, builds and browser test runs from all of Rem's tasks wait for a free slot, so many agents can work without overloading the computer.

## What Rem remembers

**What Rem remembers**, in the Rem options menu, lists the facts and preferences Rem saved. You can add, edit, forget or copy them, and pick a mode:

| Mode | Uses them | Saves new |
| --- | --- | --- |
| **On** | Yes | Yes |
| **Paused** | Yes | No |
| **Off** | No | No |

A reply that saved or changed a memory shows a note with **Undo**. Text that looks like a password, key or token is never saved.

**Clear history and tasks**, in the same menu, asks first. It stops Rem's running work and deletes its conversation, tasks, memories and worker threads. Your own threads, project files and saved reports stay.

See also [Global board and self-work](./board.md), [Chat and threads](./chat.md) and, on the phone, [Rem on the phone](../mobile/rem.md).
