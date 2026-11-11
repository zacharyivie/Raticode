---
title: Rem's home and tasks
description: Talk to Rem in one ongoing conversation, hand it tasks, and see how worker threads and independent review check the work.
pageClass: docs-layout
---

# Rem's home and tasks

The rat-face **Rem** entry at the top of the sidebar opens Rem's home: one ongoing conversation with Rem and the list of work Rem is responsible for. There is no thread picker here. Rem answers simple questions itself and starts a linked **worker thread** for anything that needs real work, such as research, a code change or an errand. Your own threads live in the [Rem pane](./chat.md).

## Talk to Rem

Type in the message box; Enter sends and Shift+Enter adds a line. While Rem replies, the header says what it's doing, and with Claude Code as the chat provider the reply appears as it is written.

- Long replies fold to a preview with **Show more**; Rem's newest message starts open.
- A reply can have an expandable **Details** section with tables, task lists, code and simple charts, and up to three suggested replies you can tap.
- When a reply refers to work, it carries a live **task card** with the task's type, phase and checks, plus **Open thread**. Under the newest update about a finished task, **Open report**, **Retry review** and **Run again** act on that task.
- A task card also lists the [board tickets](./board.md) the task created, worked on or changed, as ticket pills. A ticket that was later deleted keeps its key and old title, struck through. The task's details in the sidebar list the same tickets under **Tickets**.
- A board ticket key in Rem's chat, such as `REM-5` or an organization's `ENG-18`, shows as a small pill with the ticket's status mark, key and current title instead of a plain link. Click it, or Tab to it and press Enter, to open that ticket on the global board. Only keys of tickets on the board become pills: a key Rem made up, a deleted ticket, text inside code or part of a longer word (such as `UTF-8` or `v2-REM-5`) stays as typed. A renamed ticket's pill shows its new title.
- Work Rem starts on its own during [self-work](./board.md#self-work) shows in the chat like work you asked for: a task card when it starts and an update when it finishes. Cards started together are grouped under one line, such as "Started 3 things while you were away: 2 tickets and 1 research run." Ranking and research updates name the tickets they proposed, left out or closed.
- Rem can open or create a local project folder when your request needs one, and it can send you files, such as a saved report, as part of a reply. It never opens a project in places that hold sign-ins or system state, and sends you a file from one only when your own message names that exact file. They include SSH, GPG and cloud tool folders, coding agents' sign-ins, browser profiles (Chrome, Chromium, Edge, Brave, Vivaldi, Opera and Firefox), the macOS keychains and cookies, Windows saved credentials and protected keys, and Raticode's own data, on Linux, macOS and Windows.
- The **While you were away** card summarizes what [self-work](./board.md#self-work) did since you last looked.

### Search the conversation

Click the magnifying glass in the chat header, or press Ctrl+F (Cmd+F on a Mac) while you're in Rem's chat, to search the whole conversation, back to its first message.

- Search looks in your messages, Rem's replies and their **Details**, task updates, task card titles and the names of attached files. It ignores case, and every word you type must appear somewhere in the message. No model reads your words; the desktop matches them itself.
- Results come newest first, with the count, the time and a short snippet with the matching words highlighted. A result found only in a task title or a file name says so. **Show more results** loads the next 50.
- Click a result, or press Enter in the box for the newest one, to jump to that message. A folded message opens, the chat scrolls to the matching words and highlights them for a moment.
- **Done** or Escape closes search without moving the chat. Your scroll position and your unsent message, with its attached files, stay as they were.

Rem's chat provider, model and effort are set in **Settings > Rem** (**Rem chat provider**, or the **Default provider** when it has none of its own). See [Providers, models and effort](./providers.md).

## Tasks

The sidebar groups Rem's tasks as **Working on**, **Needs you**, **Coming up**, **Paused** and **Done**. Running tasks show a spinner and their phase; queued ones show a clock. Each task's menu has Edit, Run now (or Run again, or Stop current run), Pause schedule or Resume, Dismiss for runs that need you, and Delete.

Choose **+** to add a task yourself, or just ask Rem in the chat. The task sheet has:

| Field | What it sets |
| --- | --- |
| **What should Rem do?** | The instructions for the worker |
| **Kind of work** | **Research**, **Maintain** or **Develop**. Rem also creates **Errand** and **Reminder** tasks, shown read-only. |
| **Project** | The folder the work happens in. Develop tasks for standalone tools can run without one. |
| **When** | **Once**, **Daily**, **Weekly** or a **Custom** cron schedule |
| **Start after** | For one-time tasks: other tasks this one waits for. See [Start a task after another](#start-a-task-after-another). |
| **Done when** | The acceptance criteria. Leave it empty and Rem proposes them; Rem's suggestions carry a **Suggested** tag. |

Edits apply when you save. If a change affects a running task, the button reads **Save and update** and Rem updates the running thread.

### Start a task after another

A one-time task can wait for other tasks and start by itself once every one of them has finished successfully. Ask Rem in your own words, for example "after the audit finishes, start four threads to fix what it finds", or pick tasks under **Start after** on a new task, where the button then reads **Queue task**.

- A waiting task sits under **Coming up** with an hourglass and says what it waits for, such as "Waiting for “Audit the CLI” to finish". Its sheet lists each task it waits for with where that task stands; tap a title to open it.
- When they all succeed, Rem starts it through the usual path, with a chat card saying so. It still waits its turn for a free worker under **Capacity**, works only in its own project and goes through the same review. Its worker is told what the earlier tasks found, with their summaries and saved reports.
- If a task it waits for fails, is stopped, is blocked, is paused, is deleted or will never run, the waiting task moves to **Needs you** as **On hold** with the reason, and Rem says so once in the chat. **Retry “…”** runs that task again, and the waiting task goes back to waiting; **Start now** starts it straight away and stops it waiting. **Delete** drops it.
- When the follow-up work depends on what the first task finds, Rem doesn't guess the split up front. It keeps your request and hands it back to itself, with the first task's results, once that task succeeds. The request then shows in the chat as yours, marked **Queued until “…” finished**, with Rem's answer below it.
- A task can't wait for itself, and tasks can't wait for each other in a loop. Repeating tasks can't wait for other tasks, but a one-time task can wait for a repeating one: it waits for that task's next run, or counts its last success when no run is coming. A task that already started keeps what it waited for; before it starts, you can change it or clear it, and clearing it starts the task.
- Waiting and starting are saved with the task, so closing or restarting Raticode never starts a task twice or loses one that was waiting.

## How work is checked

Each run of a work task goes through **Plan**, **Work**, **Check** and **Report**:

1. A worker thread does the work, using the task's provider and the project's permissions. Rem coordinates it: it answers the worker's routine questions and asks you only for a missing fact or an authorization only you can give, such as publishing, deploying, spending money or deleting something permanently.
2. A separate, read-only **review** checks every acceptance criterion against the files, command results and saved reports. Evidence that can't be confirmed doesn't pass. Research must save a report.
3. If the review fails, the work goes back to the worker for up to two repair attempts. After that the task moves to **Needs you** with the reason.

Task cards show how many checks passed, failed or haven't run yet.

A task shows **Plan** only until its work starts. A run that waits to be checked again after its work is done stays at **Check**. That happens when you retry a review, when the review hit a temporary provider problem, when Raticode restarted during the review, or while it waits for a later event its proof depends on. A finished run keeps its result on every card in the chat, on the computer and on your phone, even after its task is deleted or cleaned up.

### Changes land on dev

When a project has a worktree skill enabled in **Settings > Thread tools** whose description says its branches land on `dev` (for example `worktree-dev-flow`: "…then rebases onto dev and fast-forwards dev"), a develop or maintain task makes its change on its own branch and then has to land it: rebase onto the local `dev` branch, rerun its checks and fast-forward `dev`. The task already allows that step, so the worker doesn't stop to ask, and it may fast-forward `dev` in whichever folder has it checked out, as long as that folder has no uncommitted changes. Nothing is pushed.

Before Rem accepts the task, it checks the branches the worker committed to. A branch with commits that aren't on `dev`, or with uncommitted changes, sends the work back to the worker to land, and if it still hasn't landed after the repair attempts, the task moves to **Needs you** with the branch named. Branches other tasks are working on at the same time don't count against it. A task whose request says to keep its work off `dev`, for example "don't merge it into dev", keeps its branch and isn't checked for this. A worktree skill that doesn't mention landing on `dev`, such as a general "use git worktrees" skill, still keeps Rem from making its own worktree but never turns this check on, even when the repository has a `dev` branch.

### Seeing which checks failed

When a run needs you, or is being fixed after review, open the task. Under the run's summary, the task sheet lists:

- **Failed**: each check the review failed, by name, with the reviewer's reason.
- **Unresolved**: checks the review couldn't confirm, with why. A pass the review couldn't back up, for example with no recorded test run, lands here.
- **Not checked**: checks the review didn't reach.
- **Passed**: folded away; click it to see each passed check and its evidence.

Long reasons show three lines first; **Show more** opens the rest. Before a review has run, the sheet only says how many checks haven't run yet. The phone's task screen shows the same list (it needs an up-to-date computer).

**Retry review** re-checks saved work without repeating it, when a review was interrupted. A worker thread keeps one transcript across runs of the same task, with the worker's steps folded under **Show thoughts**.

Pause stops future runs of a scheduled task; Stop cancels the current run; Delete removes the task but keeps its history. A stop that can't be confirmed shows as an unknown outcome and isn't retried automatically.

## Capacity

**Settings > Rem > Rem task workers** sets how many Rem tasks (and phone turns) run at once; 0 picks a number from this computer's cores and memory. **Queue tests and builds** makes tests, builds and browser test runs from all of Rem's tasks wait for a free slot, so many agents can work without overloading the computer.

## What Rem remembers

**What Rem remembers**, in the Rem options menu, lists the facts and preferences Rem saved. You can add, edit, forget or copy them, and choose a mode: **On** uses and saves memories, **Paused** uses them but saves nothing new, **Off** neither uses nor changes them. A reply that saved or changed a memory shows a note with **Undo**. Text that looks like a password, key or token is never saved.

**Clear history and tasks** (in the same menu, with confirmation) stops Rem's running work and deletes its conversation, tasks, memories and worker threads. Your own threads, project files and saved reports are kept.

See also [Global board and self-work](./board.md), [Chat and threads](./chat.md) and, on the phone, [Rem on the phone](../mobile/rem.md).
