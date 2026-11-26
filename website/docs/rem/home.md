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

Unsent text in Rem's composer is kept on this computer until you send or clear it, so it's still there after a restart, an update or a reload. Attachments, screenshots and the message you're replying to aren't kept.

Rem's chat provider, model and effort are set in **Settings > Rem**: **Rem chat provider**, or the **Default provider** when it has none of its own. A model left on **Automatic** there uses that app's **Default model** from **Settings > Providers**, and the app's **Default effort** applies until you pick an effort. A model or effort Settings > Rem names wins. See [Which default wins](./providers.md#which-default-wins).

When that app can't run on this computer, Rem's welcome screen says so in place of its suggestions, by name: *Rem's app, Codex, isn't installed on this computer. Choose another in Settings › Rem.* An app that is turned off in Settings > Providers gets the same line, and one that needs sign-in says *needs you to sign in* and points to Settings > Providers. **Open Settings › Rem** goes straight to the provider setting Rem uses. In Settings > Rem, the saved app stays chosen with a label such as **Codex (not installed)** or **Codex (turned off)** and a warning under the list, instead of showing another app in its place.

Rem's chat rules are the same on every reply, so they travel as the system prompt and only your message, the chat history and records change from one reply to the next. Claude Code reads them from a private file for that one reply. Other providers get them at the top of the prompt.

### What a reply can contain

| Part | What it does |
| --- | --- |
| **Show more** | Long replies fold to a preview. Rem's newest message starts open. |
| **Details** | An expandable section with tables, task lists, code and simple charts. |
| Suggested replies | Up to three you can tap. |
| Task card | The task's type, phase and checks, with **Open thread**. Under the newest update about a finished task: **Open report**, **Retry review** and **Run again**. |
| Files | Rem can send you files, such as a saved report. |

A task update's message is a few sentences, at most 700 characters, and a chat reply's is at most 4,000. If Rem writes more, the message stops after the last full sentence that fits and the rest opens **Details**. If Rem can't write a task update at all, for example because its provider is down, the update shows the opening of the task's result, with the rest of the result under **Details**.

### Quick answers

*New in Raticode 0.6.0.*

Rem answers factual questions about your own work in the chat, without starting a worker thread. While it writes the reply it can ask Raticode to look something up, and the header says what it is reading, such as "Checking the project's Git status".

| You ask | Rem looks up |
| --- | --- |
| "What did my notes say about the launch?" | Your [Second Brain](../knowledge/second-brain.md): matching passages and their note paths, then one note in full |
| "What's changed in the shop project?" | That project's branch, changed files and last 10 commits |
| "Why did the nightly report task fail?" | The task's full request, checks and its last 5 runs, with results and errors |
| "What happened with the login fix last week?" | Finished one-time tasks whose title or summary has those words, newest 20 first, optionally in one project |
| "Which thread did I plan the invoice export in?" | Your threads and Rem's task threads whose title or project folder has those words, newest 20 first, optionally in one project |
| "Where is REM-12?" | The [board ticket](./board.md) with its description and latest timeline entries |
| "Did last night's workflows pass?" | Recent [workflow runs](../studio/runs.md), with their status and errors |
| "What does the table at the end of this file say?" | The rest of a file you [attached](./attachments.md#text-files-in-rem-s-chat), 24,000 characters at a time, or the part where some words first appear |

- Lookups only read. Rem can't change anything through them, and no shell or model runs for them.
- They reach only what Rem already uses: projects in Rem's list, the Second Brain when it's on for Rem, Rem's own tasks, the global board and files attached in Rem's chat.
- Rem is told to name the note, commit, task or run its answer came from, to start a task for real work, and to start one for questions a lookup can't answer.
- One reply makes at most two rounds of reads, with up to four lookups each.

#### Which tasks Rem's chat sees

To keep replies quick, the chat doesn't send Rem every task it ever ran:

- Tasks still in play are listed in full: running, queued, scheduled, repeating, paused, waiting for another task, and ones that need you, including failures you haven't dismissed. Only the first 800 or so characters of each request are shown. Rem is told to read the whole request before it changes one.
- Finished one-time tasks, including failures you dismissed, appear in one line each, and only those that finished in the last 24 hours, at most the 30 newest. Rem is told how many more there are.
- For an older task, Rem searches finished tasks by words from their title and summary, then reads the one it needs. Follow-ups and retries still work on a task the chat no longer lists.

New in 0.6.0. In 0.6.0, Rem sees every task until cleanup removes it, 30 days after it finished.

#### Which threads Rem's chat sees

Rem's chat doesn't send Rem every thread either:

- Of your own threads, global ones and those in the open projects, Rem sees the 25 you updated most recently, and how many there are in all.
- Of the task threads linked from its chat, Rem sees the 15 that best match your message, and how many there are in all.
- For any other thread, Rem searches all of them by words from the title or project folder. Reading a task thread Rem already knows still works whether or not it's listed.
- The same limits apply when Rem answers a worker's question or decides how a conversation task went.

New in 0.6.0. In 0.6.0, every reply carries all of those threads and up to 60 task threads.

### If a reply fails

If Rem can't finish a reply, for example because its provider kept failing, it changes nothing and says so. Your message stays in the chat. When it was text you typed, here or on the phone, it also appears under the error as a suggestion: tap it to put it back in the message box, then send. A message with attachments has to be sent again with them.

Before showing an error, Rem tries a failed provider call once more on its own, a couple of seconds later. Problems that trying again can't fix are shown at once: being signed out, a billing or usage limit, or an unknown model. The error names the provider and gives its reason in plain words, such as "Claude Code stopped with this error: …", and adds "Trying again once didn't help." when it did retry. A provider that's temporarily overloaded or unreachable is retried a few more times, as before.

When Rem has already used its reads and still wants more, it's asked once more to answer from what it has, instead of failing.

### Ticket pills

A [board ticket](./board.md) key such as `REM-5`, or an organization's `ENG-18`, shows as a pill with the ticket's status mark, key and current title. Click it, or Tab to it and press Enter, to open the ticket on the global board.

- A task card lists the tickets its task created, worked on or changed as pills. The task's details list the same tickets under **Tickets**.
- In a table, a ticket shows only its key, as a link, so the column stays narrow. It opens the ticket the same way. Hover over it to see the title and status; screen readers read them too.
- A deleted ticket keeps its key and old title, struck through. A renamed ticket's pill shows its new title.
- Only keys of real tickets become pills. A made-up key, text inside code, or part of a longer word (`UTF-8`, `v2-REM-5`) stays as typed.

<AppScreenshot name="ticket-pills" caption="Ticket keys in Rem's reply become pills with each ticket's status mark and title. The task card below lists the ticket it works on." />

### Work Rem starts on its own

[Self-work](./board.md#self-work) shows in the chat like work you asked for: a task card when it starts and an update when it finishes.

- Cards started together share one line, such as "Started 3 things while you were away: 2 tickets and 1 research run."
- Ranking and research updates name the tickets they proposed, left out or closed.
- Under Rem's name, the side pane has a **Start self-work** button, which reads **Stop self-work** while it's on. It starts self-work now, without waiting for quiet minutes, and runs it toward the current objectives until you stop it; see [Start or stop from Rem's side pane](./board.md#start-or-stop-from-rem-s-side-pane) (not yet released).
- The side pane lists self-work's [Objectives](./board.md#objectives) above the tasks, newest first with open ones on top and [stale ones](./board.md#order-sessions-and-stale-objectives) from earlier sessions folded below, and a task that serves one shows it under its name.
- The **While you were away** card sums up what self-work did since you last looked. When workflows ran in that time, it adds a line such as "6 workflow runs: 5 passed, 1 failed · est. $0.42". Select it to open [Workflow runs](../studio/runs.md#every-workflow-s-runs) from that moment (from 0.5.5).
- When self-work stops, after a [timed window](./board.md#timed-self-work), when [its stop condition is met](./board.md#stop-condition-and-latest-time) or when you turn it off, Rem posts [a report](./board.md#the-report-when-self-work-stops) listing each task it worked on and how it went. Select one to open it.

### Project folders and files

Rem can open or create a local project folder when your request needs one. It never opens a project in places that hold sign-ins or system state. It sends you a file from one of those places only when your own message names that exact file.

Those places, on Linux, macOS and Windows:

- SSH, GPG and cloud tool folders, and coding agents' sign-ins
- Browser profiles: Chrome, Chromium, Edge, Brave, Vivaldi, Opera and Firefox
- macOS keychains and cookies; Windows saved credentials and protected keys
- Raticode's own data

Files whose names usually hold secrets, such as `.env`, keys and certificates (`.key`, `.pem`, `.p12`), follow the same rule wherever they are. A request from a paired phone never gets keys, certificates or password stores, even when it names the exact file, and a phone that asks for them is locked until you unlock it on the computer. See [Suspicious requests lock the phone](../mobile/pairing.md#suspicious-requests-lock-the-phone).

### Earlier messages

Rem's chat opens with its newest 200 messages (from 0.5.5). However long the conversation gets, opening and refreshing it costs the same.

- **Show earlier messages**, at the top of the chat, adds the 40 messages before them. Scrolling up to the top does the same.
- What you were reading stays in place while earlier messages are added above it.
- Messages you loaded stay while new ones arrive. The **new messages** button counts only messages that arrive while you read further up.
- A task's **Conversation** updates list the messages loaded in the chat.
- The phone app pages back through Rem's chat the same way.

### Search the conversation

Click the magnifying glass in the chat header, or press Ctrl+F (Cmd+F on a Mac) in Rem's chat. Search covers the whole conversation, back to its first message.

- It looks in your messages, Rem's replies and their **Details**, task updates, task card titles and attached file names.
- It ignores case, and every word you type must appear in the message. The desktop matches words itself; no model reads them.
- Results come newest first, with the time and a highlighted snippet. A match found only in a task title or file name says so. **Show more results** loads the next 50.
- Click a result, or press Enter for the newest, to jump there. A result older than the loaded messages loads everything back to it first. The message unfolds and the matching words flash.
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

Each task's menu has **Edit**, **Pause** or **Resume** for a run, **Run now** (or **Run again**, or **Stop current run**), **Pause schedule** or **Resume schedule**, **Make it a workflow** for a repeating task that has passed twice, **Dismiss** for runs that need you, and **Delete**.

- **Pause** pauses the run that is going. See [Pause and resume a task](#pause-and-resume-a-task).
- **Pause schedule** stops future runs of a repeating task. A run already going carries on.
- **Stop** cancels the current run. A stop that can't be confirmed shows as an unknown outcome and isn't retried automatically.
- **Delete** removes the task but keeps its history.

### Dismiss all updates

**Dismiss all** in the **Needs you** (or **Updates**) group heading clears every task update in one click. Its count says how many it clears.

- **What it dismisses:** runs that failed, couldn't finish or ended unclear, the same as pressing **Dismiss** on each one.
- **What stays:** a run that asks you a question, and a task **On hold** because a task it waits for didn't succeed. They stay under **Needs you** until you answer, retry or start them. With only those left, the button is gone.
- **Nothing else changes:** no task or run is stopped, edited or deleted, and a repeating task keeps its schedule. As after a single **Dismiss**, a dismissed one-time task leaves the list. Its card stays in the chat, and Raticode removes the task and its work thread 30 days after its run finished.
- Only the updates on screen when you click are dismissed. One that arrives a moment later stays.
- The phone has the same button, and the CLI has `Raticode rem runs acknowledge-all`.

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

### Make it a workflow

*New in Raticode 0.6.0.*

A repeating task pays for a full agent run and a separate review every time. When it does the same thing each run, a [workflow](../workflows/rattish.md) can do it for less. After a **Daily**, **Weekly** or **Custom** cron task has passed twice, its menu offers **Make it a workflow**.

1. **Rem drafts it.** A normal Rem task starts in the same project. Its worker uses the workflow-builder skill to draft a `workflow.rattish` from the task's instructions and the commands and files its successful runs used. Steps that ran the same way each time become command or script steps. Agent steps stay only where a step needs judgment.
2. **The draft is checked.** It must pass `rattish check` and `rattish preflight`, and its [test set](../workflows/tests.md) gets one test case, which runs once. The task's review checks all of that like any other task.
3. **The finished card compares costs.** It shows the task's average cost per run, from the [usage ledger](./usage.md), next to the workflow's test-run cost. A cost a provider didn't report shows as unknown, never as $0.
4. **You decide.** **Switch over…** shows the workflow's files, the cron trigger it will add (the task's schedule and time zone) and **Approve and switch over**. That adds the trigger, approves exactly the version you saw through [Approved versions](../workflows/triggers.md#approved-versions), and runs **Pause schedule** on the task.

- Nothing switches without you. Rem's chat has no way to switch over, agent tokens can't reach it, and `Raticode rem tasks switch-over` refuses to run inside an agent turn.
- **Resume schedule** on the task undoes it: the task runs on its schedule again and the workflow's trigger turns off. Switching over again turns the same trigger back on.
- If the task needs open-ended judgment every run, such as research that picks new sources each time, Rem says switching won't save much and stops without a draft.
- While a draft exists, the menu doesn't offer another. Delete the drafting task to start over.
- If the workflow changed after you opened **Switch over…**, nothing switches and the new version loads for review.

From the CLI: `Raticode rem tasks make-workflow TASK_ID` and `Raticode rem tasks switch-over TASK_ID --yes` (see [Rem from the CLI](../reference/cli-rem.md)).

### Add or edit a task

Choose **+**, or just ask Rem in the chat. The task sheet has:

| Field | What it sets |
| --- | --- |
| **What should Rem do?** | The worker's instructions |
| **Kind of work** | **Research**, **Maintain** or **Develop**. Rem also creates **Errand** and **Reminder** tasks, shown read-only. |
| **Project** | The folder the work happens in. Develop tasks for standalone tools can run without one. |
| **When** | **Once**, **Daily**, **Weekly** or a **Custom** cron schedule. Custom uses crontab weekdays (0 and 7 are Sunday, so `0 9 * * 1-5` is 9:00 Monday to Friday) and saves them as day names, such as `0 9 * * mon-fri`. Use a day of the month or a weekday, not both. |
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
2. **Check.** A separate, read-only **review** checks every acceptance criterion against files, command results and saved reports. Evidence it can't confirm doesn't pass. Research must save a report. When Raticode ran the project's decided check itself, the reviewer sees that result next to the worker's receipts and trusts it over them (see [Raticode runs the project's check itself](#raticode-runs-the-project-s-check-itself)). Develop and maintain work that changes code names the test that proves it (see [Code changes name their proving test](#code-changes-name-their-proving-test)). The review also lists findings, each a blocker, should-fix or note, and for work that changes files it checks the change against the project's standards file (see [Findings and the project's standards](#findings-and-the-project-s-standards)). A Claude Code review runs its commands in [Claude Code's sandbox](./board.md#claude-code-s-sandbox).
3. **Repair.** If the review fails, or finds a blocker, the work goes back to the worker, up to two times. After that the task moves to **Needs you** with the reason. Notes never send work back and don't count toward the two.

Task cards show how many checks passed, failed or haven't run.

When the reviewer's answer can't be read, for example because it ended in prose instead of its verdict or skipped a check, Rem doesn't pay for a second review straight away. It sends the same review session one short message with what was wrong and the verdict's shape, and asks for only the verdict. The task shows **Asking the reviewer to restate its verdict** meanwhile. If that answer can't be read either, or the provider couldn't continue the session, Rem reviews the work once more in a fresh session, and only reports a problem when that answer can't be read. Each restate is kept on the run. New in 0.6.0.

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
| **Notes** | Folded away. The review's notes, and any should-fix finding after its one return, each with its axis and file. They never block the run. |

<AppScreenshot name="rem-task-checks" caption="A run that couldn't finish. The task lists each check by outcome, with the reviewer's reason for every failed or unresolved one. This group reads Updates because nothing here needs an answer from you." />

Long reasons show three lines; **Show more** opens the rest. Before a review runs, the sheet only counts the checks that haven't run. The phone's task screen shows the same lists (it needs an up-to-date computer), except **Notes**, which only the computer shows for now.

**Retry review** re-checks saved work without redoing it, for when a review was interrupted. A worker thread keeps one transcript across runs of the same task, with its steps folded under **Show thoughts**.

### Two tasks in one project

When a task starts while another is already changing the same Git project, Rem gives it a temporary worktree on a `rem/` branch and removes the worktree afterwards, keeping the branch. If the project has an approved [setup file](../studio/worktree-setup.md), it runs in that worktree before the worker starts, and the task's status shows the result.

### Changes land on the project's branch

In 0.5.5 and earlier the branch is always `dev`. From 0.6.0 it is a per-project decision.

This applies when a project has a worktree skill on in **Settings > Thread tools**, such as `worktree-dev-flow`, and a decided **landing branch** (topic `branch.integration` in [Rem's decisions](#rem-s-decisions)). Raticode has no built-in branch: a project scan finds it (see [How Rem finds the answers](#how-rem-finds-the-answers)) and you confirm it, or Rem takes the scan's answer for now in auto mode. A branch a worker suggests always waits for you. A develop or maintain task then:

1. makes its change on its own branch
2. rebases onto the local landing branch, reruns its checks and fast-forwards that branch, without asking, in whichever folder has it checked out with no uncommitted changes
3. pushes nothing

Before accepting, Rem checks the worker's branches. One with commits not on the landing branch, or uncommitted changes, goes back to the worker. If repairs don't land it, the task moves to **Needs you** naming the branch.

The landing branch must also contain the commit [Raticode's own check](#raticode-runs-the-project-s-check-itself) ran on. If it doesn't, the work that landed isn't the work that was checked, so the task goes back to the worker naming both commits. *Not in a released version yet.*

- The branch is fixed when the task starts, so changing the decision doesn't move a running task's goal.
- Branches other tasks are working on at the same time don't count.
- A request to keep the work off the branch ("don't merge it into dev", "don't land it on the integration branch") skips the check.
- A project without a decided landing branch has no landing check. Its first develop or maintain task scans for one, and the answer waits for you (or is Rem's provisional one in auto mode).
- **Projects that already landed on `dev` keep doing so.** When you update, every project where Rem already checked tasks against `dev` gets a confirmed `branch.integration` decision of `dev`, marked **carried over**, so nothing changes for them. This happens once: if you later forget that decision, it isn't brought back.

### Raticode runs the project's check itself

New in 0.6.0.

When a project has a decided check command (topic `checks.fast`), a develop or maintain task's own claim that it ran the checks isn't the proof. Before the review, Raticode runs the command itself on the task's work: the worktree branch the worker created, Rem's temporary worktree, or the project when the work was done in place.

**A fresh worktree of the commit that lands.** Raticode doesn't run the command in the task's folder (the worktree branch the worker created, Rem's temporary worktree, or the project when the work was done in place) as it is. It reads the commit that lands from the project's repository, as Raticode recorded the work: the tip of Rem's temporary worktree branch, the tip of the branch the worker's worktree skill created, or the project's own commit for work done in place. It never asks the task's folder, since a worker can point that folder's `.git` file at another commit. Raticode makes a new detached git worktree of that commit in Raticode's data folder, makes the links and copies of the project's approved [worktree setup](../studio/worktree-setup.md) there, runs the command in it and removes it afterwards. The setup's commands never run there: they run outside the sandbox and could run a script the worker committed, such as a `./setup.sh` the setup calls. Only committed files are in it, so a file the worker forgot to `git add` fails the check instead of passing it, and the worker is told so. Uncommitted edits in the task's folder aren't checked either. *Not in a released version yet.*

- **When it can't make one in a git project**, such as for a commit git can't read or check out, a repository with no commit yet, or a worktree setup that fails or isn't approved, the command doesn't run and the check fails (`ran_in`: `not_run`), with the reason sent back to the worker. It never falls back to the task's folder, where uncommitted files could help it pass. *Not in a released version yet.*
- **It runs in the task's folder only** for a project outside git, or when the project's approved worktree setup runs commands, which would run the worker's committed files outside the sandbox. The reviewer's receipt says where it ran (`ran_in`: `fresh_worktree` or `task_worktree`) and why it couldn't use a fresh worktree.
- **Nothing is left behind.** The worktree goes after the check passes, fails or is cancelled. One a crash or a backend restart left is removed at the next check and when the backend starts.
- This temporary worktree isn't one of a project's change worktrees under `.worktrees/<slug>`, and it never has a branch.

**Where it runs.** The command runs files an unattended worker just wrote, so it runs in an operating-system sandbox, the same kind a workflow's local MCP servers get:

- **No network.** It can't reach the internet or other programs on this computer. On Linux that includes your desktop session's message bus, the system message bus, keyring, SSH agent and name lookups: their sockets are hidden, since a read-only folder still lets a program connect to them.
- **Writes only to its own folder** (the fresh worktree, or the task's folder when it runs there) and a scratch folder for its temporary files and caches (`TMPDIR`, `XDG_CACHE_HOME`), which Raticode removes afterwards. Everything else, including the repository's shared `.git` folder, is read-only.
- **Credentials hidden.** The credential folders in your home folder (such as `~/.ssh`, `~/.gnupg`, `~/.aws` and your providers' sign-ins), a headless backend's secrets folder and Raticode's data folder are empty inside it, even when the task's folder contains them.
- **On Linux** the sandbox is bubblewrap (`bwrap`); **on macOS** it is `sandbox-exec`. Native Windows has none.
- **Never without it.** If the sandbox can't start (Windows, bubblewrap not installed, or a system that refuses it), Raticode doesn't run the check at all. The task goes on as before: the worker's own check receipts and the review decide, and Raticode logs why. A check that needs the network or writes outside its folder fails here, so choose a command that runs offline.

- **A failure is run once more first.** When the command fails with an exit code other than 126 or 127, Raticode runs it again on the same commit, in another fresh worktree, before sending anything back. Files the failed run wrote aren't there, so they can't make the rerun pass. A command stopped at its time limit isn't rerun. *Not in a released version yet.*
- **A failure goes back to the worker** only when it repeats on that rerun, with the command's last lines of output, before any reviewer turn. After two failed repairs the task moves to **Needs you** with the result. A recheck of the same commit reuses that confirmed failure instead of running the command again.
- **A flaky check doesn't send good work back.** If the rerun passes, the task goes to review with the passing receipt, marked flaky. Raticode keeps the first run's last lines and saves the failing test names as the project's flake record. When the first failure points at a file the change edited (a failing test's own file, or a file named in its output), the receipt names those files and the reviewer reports a should-fix finding about them, since the flake may be a race the change brought in. The reviewer also sees the failed first run's last lines and, when it ran in the task's folder, the untracked or ignored files that run left there, and treats a pass that only came after the first run left a file the code then reads as no proof. [Verified work](./usage.md#verified-work) counts these runs as **Flaky check runs**, not as first-try check failures, and once the same test flakes in two or more runs the [guardrail retro](./board.md#guardrail-retro) proposes stabilizing it. *Not in a released version yet.*
- **A pass is shown to the reviewer** as a receipt Raticode produced itself: the command, its exit code, the commit it ran on, how long it took and the last lines of its output. The worker can't write or change it, so the reviewer trusts it over the worker's own receipts and summary. A worker whose only command was, say, `ls` no longer gets through on it.
- **Only the decided text runs.** Rem can't change it; only you can, with **Change…** or **Edit answer**. A worker can't pick it either: a command a worker or Rem's chat suggests waits for you even in auto mode, and only a project scan's answer is recorded for now. Your confirmed command always runs. A command the scan found and Rem decided in auto mode runs only while auto mode is on; with auto mode off it never runs unattended, and the task falls back to the worker's own check receipts and the review.
- It waits for a free slot when **Queue tests and builds** is on, and it stops after 30 minutes.
- **When the command itself is broken.** A command that can't run at all (not found, or not executable) twice in a row reopens its decision with a card saying so. A check that fails because the work broke it doesn't.
- The Decisions list shows the last result under the command, such as "Raticode's last run of it passed on 2026-11-21."
- **A new command starts fresh.** When you change the command with **Use**, **Change…** or **Edit answer** (or OK an edit you asked Rem for in chat), its failures count from zero again, and the Decisions list on desktop and phone drops a last run of the old command. Only runs of the command as it reads now show. *Not in a released version yet.*

### Code changes name their proving test

New in 0.6.0.

A develop or maintain task's worker, including a bug fix or a [Guardrail ticket](./board.md#guardrail-retro), is asked to write one of two lines in its result:

- `Proving test: <test name>` for a test that fails before the change and passes after it, or an existing test that already covers the change. The worker runs it, so its name shows in a command it ran or in the output of Raticode's own check.
- `Not testable here because <reason>` when no test can prove the change on this computer, such as one that needs a real phone or an account the computer doesn't have.

The reviewer looks for the named test, run and passing, in Raticode's own runs of it (below), Raticode's check output or the worker's command receipts. It judges a not-testable reason on its merits and turns down one a test could cover. A missing line, a test it can't find in either place, or a reason that doesn't hold fails the review, and the work goes back to the worker with what's missing. Research, conversations and errands aren't asked for one, since they don't change code.

A Guardrail ticket asks for two receipts as well: the new test or check failing on the mistake its cited runs hit before the fix, and passing after it. The reviewer looks for both. A Guardrail ticket that only adds a pointer line to `AGENTS.md` isn't asked for them, since nothing can fail on one line of text.

**Raticode runs the proving test itself.** The worker's word that its test fails before the change and passes after it isn't the proof. Before the review, Raticode runs the named test twice, in the same sandbox as [its own check](#raticode-runs-the-project-s-check-itself) and each time in a new temporary worktree it removes afterwards:

- **Before the change**: the commit the work started from (where its branch left the landing branch, found from that branch's history even after the work landed), plus only the test files the task added or changed. The new test is there; the change it tests isn't.
- **After the change**: the task's commit.

The reviewer sees both runs (each commit, exit code and last lines of output) as a receipt the worker can't write, and trusts it over the worker's own claim. A test that fails before and passes after proves the change. One that also passed before only shows an existing test still passes: the receipt says it did not fail before the change, and the reviewer accepts that only for an existing test that already covers the change, never as a bug fix's or Guardrail ticket's before-and-after receipts. One that doesn't pass after fails the review.

- **What it can run**: a pytest test written as its node ID (`tests/test_totals.py::test_rounding`) and a Node test file for `node --test` (`frontend/src/totals.test.js`, run from the folder with its `package.json`). pytest runs with the project folder's or the main repository's own `.venv` when there is one, the same Python for both runs, never one inside the task's worktree. Work whose commit adds files under `.venv` isn't run. pytest itself always loads from that environment, so a `pytest` folder in the project can't stand in for it, and a pass counts only when pytest's own results hold the named test alone: each result's real file, class path and name must match it (a test named without parameters matches each of its parametrized runs), every one of them must pass, none skipped, and no other test may appear. A run that only lists tests (`--co` in `addopts`), deselects the named test and runs another, or runs a same-named test from another file reads as *not found*.
- **What counts as a test file**: files named like `test_*.py`, `*_test.py`, `conftest.py` or `*.test.js` / `*.spec.ts`, and any file in a test folder such as `tests`, `test` or `__tests__`.
- **When it can't run it**, such as for a name that isn't one of those, a test file the work never committed, work with no commit of its own, a worktree setup with commands, a task with no landing branch or a computer where the sandbox can't start, the receipt says *not run by Raticode* with the reason, and the reviewer looks for the test in the worker's receipts and Raticode's check output as before.
- The same test on the same commits isn't run again when the work is checked again. Each run stops after 10 minutes.

### Findings and the project's standards

New in 0.6.0.

Next to its verdict on each check, the review lists **findings**: problems it saw, each on one of two axes.

- **Task** (`spec` in the reviewer's answer): the work misses or breaks something the task asked for.
- **Standards**: the change goes against the project's written standards.

For a develop or maintain task, the review is told where the project's standards file is: `CODING_STANDARDS.md` in the project's top folder, or `CONTRIBUTING.md` when there is none, or `AGENTS.md` when neither exists. Raticode gives the path, not the text, and the reviewer reads the file and judges only the lines the change added or changed. A standards file or glossary that the change edited, or that isn't committed, is flagged to the reviewer as the worker's own text: it judges the work by the landing branch's version instead. A file that is a link is named only when it leads to a file inside the project. A project with none of the three and no [glossary](#shared-words-the-project-s-glossary) gets findings on the task axis only. Research and errand reviews don't get a standards axis.

Each finding has a severity, which decides what happens to the work:

| Severity | What happens |
| --- | --- |
| **Blocker** | The work goes back to the worker with the finding, even when every check passed. It counts toward the two repairs. |
| **Should fix** | The work goes back once per run, while repairs remain. After that, should-fix findings are kept as notes and the run can finish. |
| **Note** | Never sends the work back and doesn't count toward the two repairs. Notes are saved on the run and shown under **Notes** in the task sheet, where you can read what the reviewer would tidy later. |

When the work also goes back for another reason, such as a failed check or work that hasn't landed, the findings go with it, so one repair covers both. A review whose findings list can't be read is run again rather than passed, so a blocker isn't lost to a typo.

### Shared words: the project's glossary

New in 0.6.0.

A project can list the words it uses in a `GLOSSARY.md` file in its top folder (an older `CONTEXT.md` works too when there's no `GLOSSARY.md`). A table with four columns works well: the term, what it means, its name in code, and words to avoid for it. Raticode's own source has one in its top folder.

When the file exists, Raticode points workers and the reviewer to it instead of copying it into their prompts:

- **Workers** get one line in their instructions with the file's path, asking them to read it before naming anything users see or anything new in code. None of the file's text is sent, so a long glossary costs nothing until a worker opens it.
- **The reviewer** of a develop or maintain task gets the file's path on the standards axis, next to the standards file. A new name the change adds that uses a word the glossary marks to avoid, or a second word for a term it already lists, is a standards finding. It is a **Should fix** or a **Note**, never a **Blocker**, so wording alone sends the work back at most once. Names that were already there aren't findings.

Raticode reads the file once per run, so a task that adds or edits the glossary keeps the same instructions until its next run.

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

### Provider problems and usage limits

A temporary provider problem, such as an overloaded service or a dropped connection, is retried in the same phase after 30 seconds, 2 minutes and 5 minutes. The retry starts a fresh provider session that reads the thread so far.

*New in Raticode 0.6.0.*

When a worker or review hits its provider's **usage limit** ("You've hit your usage limit", "Weekly limit reached"), retrying a few minutes later can't help. Rem waits for the limit to reset instead:

- The task goes back to waiting and holds no worker. Its status says when it tries again, such as "Waiting for the provider's usage limit to reset at 15:02 (1 of 6)".
- When the provider names the reset ("resets 3pm", "try again in 2 hours"), Rem tries again 2 minutes after it. Otherwise it tries every 30 minutes. A reset more than 6 hours away is checked again after 6 hours.
- After 6 waits it stops the task as **Couldn't finish**, saying the usage limit was still reached. Asking Rem to continue starts with a fresh count.
- An empty credit balance isn't a usage limit. It stops the task straight away, because waiting won't fix it.

## Capacity

**Worker limit** sets how many workers run on this computer at once: Rem tasks, self-work, phone turns, organization workers, swarm turns and work from other desktops. 0 is Auto, which picks a number from this computer's cores and memory. Paused runs don't take a slot. From 0.6.0 it's on this computer's row in **Settings > Computers** (see [Where work runs](#where-work-runs)), and **Settings > Rem** points there.

Work you ask for goes ahead of [self-work](./board.md#your-work-comes-first): when both don't fit, the lowest-priority self-work pauses to make room, one for each task of yours, and picks up again as soon as a thread is free.

**Queue tests and builds** makes tests, builds and browser test runs from all of Rem's tasks wait for a free slot, so many agents can work without overloading the computer.

### Computers and their load

From 0.6.0, Raticode keeps a list of the computers it can run work on: this computer and each desktop paired with it. Each one has an ID, a name, a state (online or offline), a worker limit and a load reading. Each new start of Rem work, and each new organization worker, uses the list to pick its computer (see [Where work runs](#where-work-runs)).

| Field | What it means |
| --- | --- |
| Worker limit (`max_threads`) | On this computer, the same number as **Worker limit** in Settings › Computers. 0 picks one from the cores and memory. A paired desktop reports its own; one on an older Raticode shows it as unknown. |
| Running (`running_threads`) | Workers running now: Rem tasks, phone turns, organization workers and swarm turns. `running_by_kind` splits them into `rem`, `phone`, `organizations` and `swarms` (work another desktop's organization sent counts as `organizations`). A paired desktop on an older Raticode doesn't share the split, so it shows only its total, and one older still counts only the work other computers sent it. |
| What's running (`running_work`) | This computer only: what each running worker is, oldest first, as `kind` and `label`, at most 20. The label is the Rem task's title, the organization and worker name (*Acme · Ada*), the swarm's name, *phone turn*, or *work from* and the sending desktop's name. Paired desktops share counts only, never titles, so theirs is empty (`null`). |
| CPU and memory pressure | How hard pressed the computer's CPU and memory are, from 0 to 100%. Each system has its own source (see the table below). A reading the system doesn't give is blank. |
| Workers that fit (`effective_threads`) | The worker limit, lowered while pressure is high. Up to 20% pressure changes nothing. From there to 80% the number falls in a straight line to one worker. Pressure never raises it above the limit. |
| Room (`capacity`) | New workers the computer can take now: workers that fit minus workers running. An offline computer has no room, and neither has one that is short of memory while a worker runs there (see the memory hold below). |
| Memory hold (`memory_hold`) | Why the computer takes no new worker for want of memory, such as `0.9 GB available` or `swap is 85% full`, or empty. |

Where each system's readings come from:

| System | CPU pressure | Memory pressure | Free memory |
| --- | --- | --- | --- |
| Linux | How much of the last ten seconds tasks waited for a CPU (`/proc/pressure/cpu`) | How much of the last ten seconds tasks waited for memory (`/proc/pressure/memory`) | `MemAvailable` in `/proc/meminfo` |
| macOS | The run queue beyond the core count, from the one-minute load average | The system's memory pressure level (`kern.memorystatus_vm_pressure_level`): normal is 0, warning 50 and critical 90 | Free plus inactive pages from `vm_stat` |
| Windows | Busy time from `GetSystemTimes` since the last reading, at least a second apart. 80% busy or less is 0, 90% is 50 and 100% is 100, so the workers that fit start to fall above 84% busy and reach one at 96%. The first reading after Raticode starts is blank. | Memory in use from `GlobalMemoryStatusEx`, on the same scale: 80% in use or less is 0, 90% is 50 and 100% is 100 | Available physical memory from `GlobalMemoryStatusEx` |

Swap counts toward the memory hold only on Linux. With **Queue tests and builds** on, a test or build also waits for its slot while another one runs and memory pressure is 20% or more, so on a Mac at the warning level, or on Windows with 84% or more of memory in use, they run one at a time.

Paired desktops share their worker limit, running workers and their counts by kind, pressure and memory hold with each other along with their fleet status, every 30 seconds while they can reach each other. A desktop on an older Raticode doesn't share them, and its numbers show as unknown until it's updated. A paired desktop is online while its fleet status is fresh, and offline once it stops answering. When it comes back, it's online again at the next check, but it has room for only one new worker for its first 60 seconds back (`rejoining`), so its whole limit doesn't fill at once.

See the list with [`Raticode computers list`](../reference/cli-headless.md#projects-devices-computers-and-live-threads), `GET /api/computers`, or the phone protocol's `computers.list` read. Change this computer's limit with **Worker limit** in Settings › Computers, `Raticode computers set N`, `POST /api/computers` or the phone protocol's `computers.set` action. A limit set from the CLI or the phone stays until you pick a different number in **Worker limit**. Change a paired desktop's limit in Raticode on that desktop. On the phone, open **Usage › See computers** ([Raticode Mobile](../mobile/workspace.md#desktop-settings-usage-and-the-developer-log)).

### Where work runs

From 0.6.0, Rem and each organization have a placement: the strategy for picking computers from the list above. Every start of Rem work (a task, self-work, or a run started from chat) uses Rem's placement to get a computer before its worker starts, and every organization worker uses its organization's placement the same way.

| Strategy | Where a new start goes |
| --- | --- |
| `overflow` (the default) | The first computer with room, in your order of computers: it fills one computer up to its worker limit, then moves on to the next. |
| `balanced` | The computer with room whose running workers are the smallest share of its worker limit. On a tie, the one earlier in your order. |
| `manual` | Only the computers you assign, one or more, in your order. Never any other computer. |

Assign computers only with `manual`. Each computer can go to Rem or to any organization, so Rem can have one computer, organization A two others, and organizations B and C can share a fourth. A computer is in two manual placements only when you share it on purpose: each placement that has it must mark it shared. Mark it shared in the first placement before you add it to the second. Raticode refuses a placement that names a computer it doesn't list, or a manual placement with no computers, and says why.

A desktop you unpair after assigning it stays in the placement and shows under `missing_computers`. The next save of that placement has to leave it out. When you delete an organization, its placement goes with it.

Work starts only on this computer for now, so a `manual` placement whose computers don't include this computer leaves its work nowhere to start. Raticode still saves it, and its `notice` says so, for example *Rem's work starts only on this computer for now, so with only Desktop B picked, every task waits. A waiting research or conversation task can still go to Desktop B when you pick Run there.* For an organization it says its tickets wait unless they run through a fleet execution target there. `notice` is empty for every other placement.

Your order of computers decides which one `overflow` fills first. A computer you pair later goes to the end until you move it. Raticode refuses an order that names a computer it doesn't list.

See the placements with `Raticode computers placement` or `GET /api/computers/placement`. Change one with `Raticode computers place rem manual --computer ID`, or `POST /api/computers/placement` with `consumer` (`rem` or `org:<organization ID>`), `strategy`, and for manual, `computers` and `shared`. Set the order with `Raticode computers order ID ID...`, or `POST /api/computers/placement` with `order` alone. Placements and the order are saved in `computers.json` in the data folder.

How a start gets its computer:

- Your own Rem runs go first: tasks you start or run, chat requests and follow-ups, tickets you move to **In progress** and your scheduled tasks. While one waits for a worker on a computer, nothing else claims a worker there. Self-work pauses as many of its tasks as yours need, counting everything running on the computer ([Your work comes first](./board.md#your-work-comes-first)), and organizations start no new worker and claim no ticket, showing *Waiting so your own Rem task can start first: …*. When a worker frees up, your run gets it. Self-work and organizations come after. Organization workers already running keep running, and phone turns never wait for this.
- No start takes a computer past its worker limit. Rem tasks, phone turns, organization workers, swarm turns and work sent from other desktops all count on the computer they share. Each start takes its worker at the moment it starts, counting the workers running right then, so two starts at the same moment can't both take the last one.
- Your own turns are the exception to the limit: a request from your phone, or a turn in a thread you chose to run on this desktop from another desktop. They count toward it, but wait only for your other such turns, so other work never holds them back.
- Every start waits while the computer is short of memory: less than 1.5 GB free, or swap at least 80% full. The memory hold applies to every start except your own phone and remote-thread turns: Rem tasks, self-work, your own Rem runs, organization workers, swarm turns and work another desktop's organization sends. It holds only once a worker already runs on the computer, so the first worker always starts. Waiting work shows *Waiting for free memory (0.9 GB available)*, or *Waiting for free memory (swap is 85% full)*, on the task, the organization's banner, the swarm's run and in the job status sent back to the other desktop. Workers already running keep running. A paired desktop under the hold shares it in its reading, so it has no room for this computer's work meanwhile. A desktop on an older Raticode doesn't share it and is placed as before.
- Work another desktop's organization sends here waits for room like everything else. While this computer is full it stays accepted, not running, and the job status this computer sends back says *Waiting for a free worker on Workstation* (this computer's name). It starts as soon as a worker frees up.
- An organization worker whose ticket runs on a paired desktop (an execution target of kind `fleet`) takes no worker on the computer that sent it: it only waits for that desktop's answer. The desktop that runs it counts it, so the same turn counts once. It doesn't wait for room on the sending computer either: when a ticket that needs a worker there waits for room, tickets for paired desktops behind it can still start. They still count toward the organization's **Workers at once**.
- Every [swarm](../advanced/swarms.md) member turn takes a worker on this computer before it starts and gives it back when the turn ends. A turn of an organization's swarm run counts as an organization worker. Every other turn counts under `swarms`, including turns of a swarm Rem starts from chat. A turn that can't get a worker stays queued, and the swarm's run shows *Waiting for a free worker on Studio*, with your computer's name. The swarm limits still apply as well: each team's member turns at once (three by default), and `GOFER_SWARM_MAX_CONCURRENCY` for all swarms together (8 by default). Starting or messaging a swarm never waits for its turns, so a Rem worker that starts one never holds the swarm back. Its turns start once a worker frees up, for example when Rem's own turn ends.
- Self-work reads the same room before it starts anything, so it starts no more tickets than Rem's computers have room for, and says which computers are full when there is none (see [Room on Rem's computers](./board.md#room-on-rem-s-computers)).
- An offline computer is skipped, and so is one that hasn't shared its worker limit.
- High CPU or memory pressure lowers how many workers fit (see **Workers that fit** above), so a computer can be *busy* below its worker limit, for example *Workstation is busy (2 of 4 running)*. This counts for every start: swarm turns and work sent from other desktops take their workers against the lowered number too. A computer that just came back online takes one new worker for its first 60 seconds.
- If Raticode can't read the list of computers, queued runs wait and say why, for example *Waiting for the computers' reading: …*, instead of starting without it.
- When no computer has room, the start waits, and the task shows why, naming each computer, for example *Waiting for a free worker: Workstation is full (4 of 4 running: 2 organization workers, 1 Rem task, 1 swarm turn)*. It never runs somewhere else without saying so.
- The computer is picked when a run starts and doesn't change while it runs. A run that resumes, such as after a sleep or a restart, goes back to its computer if it can still use it, and waits there if that computer is only full. If its computer is offline, unpaired or no longer assigned, a new one is picked and the run says why it moved. A run on a paired desktop never moves: its work is there, so while that desktop is offline it waits with *Waiting for Desktop B to come back; this run's work is there*.
- Each run keeps the computer it was placed on, the strategy and the reason (`placement` on the run), so you and Rem can see where it ran. A task you pinned to a computer also has `chosen_by: user`.

Rem's workers run on this computer. The one exception is a research or conversation task you pin to a paired desktop (see [Run a task on a paired desktop](#run-a-task-on-a-paired-desktop)); every other start skips paired desktops. Until you set an order, your order of computers is this computer first, then paired desktops.

#### Run a task on a paired desktop

*Experimental, new in 0.6.0. Backend, API and CLI only.* You can pin a Rem task to one computer when you create it: `Raticode rem tasks create --computer ID`, or `computer` in `POST /api/rem/tasks`, with an ID from `Raticode computers list`. The task then runs only there, whatever Rem's placement says. Pinned to this computer, any task runs here as usual. Pinned to a paired desktop:

- Only research tasks and conversation tasks can go there for now. A develop, maintain or other task pinned to a paired desktop is refused when you create or edit it: *Only research and conversation tasks can run on another computer for now; a develop task changes files, so it runs on this computer*.
- That desktop has to pass every check below for this task, including **Run threads from this desktop** on there for this computer. Pinning reuses that switch; nothing new is granted. Until every check passes, the task waits with the first one it fails, and never runs on this computer instead.
- When they pass, this computer sends the whole task: its type, title, full prompt and checks. A prompt can be up to 40,000 characters, and the prompt and checks together up to 62 KB. A longer task fails with the reason instead of being cut.
- That desktop creates a task for its own Rem in its matching project, pinned to itself, so it never sends the task on to another computer. Its Rem starts it under that desktop's own **Worker limit** and counts it as `rem`, using that desktop's own provider sign-in. While that desktop is full, the task waits there, and this computer shows why.
- This computer keeps a stand-in run that shows *Running on Desktop B*. It holds no worker here. When the task finishes there, the stand-in shows its final answer and the path of the report it saved, relative to that project, such as *Report saved on Desktop B: reports/findings.html*. The report stays on that desktop.
- **Stop** (or `Raticode rem runs cancel`) sends the stop to that desktop, which stops its run there. The stand-in shows *Stopping on Desktop B* and settles as stopped once that desktop reports the task stopped. A stand-in can't be paused. If that desktop is unpaired, Stop settles here and says the task may still be running there.
- After this computer restarts, the stand-in carries on with the same task there. It reads the task's status and never sends it again.
- That desktop's usage history keeps every provider call under its own account. This computer adds one usage row for the whole run, with the provider and model that ran it and that desktop's computer ID. It counts toward the task's own usage, never toward this computer's accounts.

Only a task you pin goes to a paired desktop. Nothing moves there on its own.

#### Run waiting work on a paired desktop

*Experimental, new in 0.6.0.* When this computer is full and a paired desktop has a free worker and passes every check below for a waiting start, the waiting work suggests it: *Desktop B has room: Run there*. Nothing starts there until you pick it. No setting starts work on a paired desktop without your pick.

- **A Rem task.** Only a research or conversation task that waits for a worker gets a suggestion, because those are the tasks a pin can hand over. It shows under the waiting reason in the task's details on the desktop, on the task's screen on the phone, and in `Raticode rem runs show RUN_ID --output text`. **Run there** (or `Raticode rem runs run-there RUN_ID`, or the phone's **Run there**) pins that run to the desktop, so it runs there as that desktop's own Rem task, as described in [Run a task on a paired desktop](#run-a-task-on-a-paired-desktop). Its placement record says `chosen_by: user`. The pick makes it your own start: self-work's pace and daily cap don't count it.
- **An organization ticket.** A ticket that waits for room shows its suggestion in the ticket's details on the desktop, on the ticket's screen on the phone, and on its own line in `Raticode org show ORG_ID --output text`. **Run there** (or `Raticode org tickets run-there ORG_ID TICKET_ID`) only points the ticket at a fleet execution target you already made on that desktop for the ticket's worker, in that project. The ticket's execution checks still apply, and nothing new is granted. Without such a target the suggestion says what to grant, such as *Desktop B has room. To run it there, grant Worker 0 a fleet execution target on Desktop B for Raticode*, and offers no **Run there**.
- A suggestion goes away when the work starts here, when that desktop no longer has room or passes the checks, or once you pick it. **Run there** is refused when the suggestion has gone or names another computer.
- Only you can pick. Rem, its Auto mode, organization workers and agent tokens are refused.

#### Whether a start could run on a paired desktop

*New in 0.6.0.* Each start checks every paired desktop for itself and skips it with the first check it fails, in the computer's own words. These checks only read what the other desktop allowed. They never grant anything. A paired desktop could take a start only when all of these hold:

| Check | Reason when it fails, for a desktop named Desktop B |
| --- | --- |
| It is paired directly with this computer, not through another desktop | *Desktop B isn't paired directly with this computer* |
| It is online | *Desktop B is offline* |
| It has shared its worker limit | *Desktop B hasn't shared its worker limit* |
| It has answered with the projects it shares | *Waiting for Desktop B to share its projects* |
| Its Raticode is new enough to say which repository each project is | *Update Raticode on Desktop B so it can say which of its projects match* |
| **Run threads from this desktop** is on there for this computer, with at least one project open | *Desktop B hasn't allowed this computer to run work there* |
| It allows the start's provider for this computer's work | *Desktop B doesn't allow Claude Code for this computer's work* |
| The start has a project, and that project is a git repository here | *This work has no project to match on Desktop B*, or *notes isn't a git repository here, so no project on Desktop B can match it* |
| Exactly one project there is the same repository | *Desktop B has no open project matching Raticode*, *Two projects on Desktop B match Raticode*, or, for a moment after it opens a project, *Waiting for Desktop B to read its projects' repositories* |
| For an organization: the owner made a fleet execution target on that desktop for the ticket's worker, for that project | *No execution target on Desktop B is granted to Worker 0*, or *Worker 0's execution target on Desktop B is for another project than Raticode* |
| For a Rem task you pinned there: it is a research or conversation task | *Only research and conversation tasks run on other computers for now* |

When every check passes, a research or conversation task you pinned to that desktop runs there. For any other start the reason is *Desktop B could run this work in its Raticode project, but work doesn't start on other computers yet*. With the default `overflow` the start runs on this computer. With `manual` and only paired desktops it waits with these reasons.

The same repository means the same root commits, so every clone matches wherever its folder is. Paired desktops send each other a hash of a project's root commits, never its folder path. When two projects there are clones of the same repository, the one whose folder has the same name wins. Otherwise neither does. A start never falls back to another project on that desktop. Desktops ask each other for their projects every 30 seconds along with their status. An older desktop that can't answer stays paired and works as before. It's listed with *Update Raticode on Desktop B*.

#### Organization workers

An organization's placement is its `org:<organization ID>` entry. Each time it would start a worker on a ticket, it asks for a computer first:

- Its **Workers at once** still caps its own workers. Placement adds the computer's worker limit on top, shared with Rem tasks, phone turns and every other organization's workers.
- Your own Rem runs go first. While one waits for a worker on a computer the organization could use, the organization claims no worker there and no ticket, even below its maximum, and shows *Waiting so your own Rem task can start first: “…”*, naming the task. Its running workers keep running.
- Organization workers run only on this computer for now, so every paired desktop is skipped with the reason for that ticket (see [Whether a start could run on a paired desktop](#whether-a-start-could-run-on-a-paired-desktop)). An organization set to `manual` with only paired desktops starts nothing and says why. A waiting ticket can still suggest a paired desktop with room, which you pick with **Run there** (see [Run waiting work on a paired desktop](#run-waiting-work-on-a-paired-desktop)).
- When no computer has room, the organization claims no ticket and shows why, for example *Waiting for a free worker: Workstation is full (4 of 4 running: 2 organization workers, 1 Rem task, 1 swarm turn)*: in a banner on its page, on its page on the phone (`placement_wait` in `organizations.read`), and on the `Waiting:` line of `Raticode org show`. The reason goes away once a worker starts or nothing is left waiting to start.
- Each run records its computer, strategy and reason (`placement` on the run's attempt record), for review.
- Delegated work to an owner-granted execution target (a swarm, a paired desktop or an HTTPS gateway) works as before; its worker on this computer counts like any other.

#### Settings › Computers

*New in 0.6.0.* Desktop **Settings › Computers** shows the same settings with buttons.

- The section starts with where work runs today: *Work starts on this computer for now. A paired desktop takes work only when you pick Run there on waiting work, or through an organization's fleet execution target.*
- **Your computers** lists this computer and each paired desktop in your order. Each row shows the name, online or offline, running workers out of the worker limit with what kinds they are, such as *4 of 4 running: 2 organization workers, 1 Rem task, 1 swarm turn*, room for new workers and, while it's online, CPU and memory pressure. A paired desktop on an older Raticode shows only its total. This computer's row has **What's running**, which opens to list each worker by name: a Rem task's title, an organization and worker, a swarm, a phone turn or work from another desktop. The up and down arrows move a computer in the order.
- This computer's row has **Worker limit**: how many workers run here at once, counting Rem tasks, self-work, phone turns, organization workers, swarm turns and work from other desktops. 0 is Auto, and the row says the number Auto picks from this computer's cores and memory. Change a paired desktop's limit in Raticode on that desktop. **Settings > Rem** has a line pointing here.
- An online paired desktop also says why no work from this computer can run there, such as *Update Raticode on Desktop B so it can say which of its projects match* or *Desktop B hasn't allowed this computer to run work there*. Once those checks pass, it lists the projects and providers it shares with this computer. The checks that depend on the start, its provider, project and worker, show on the waiting work. `GET /api/computers` has the same line as `work_reason`, and each paired desktop's `catalog`.
- **Where work runs** has a row for Rem and one for each organization, with a strategy menu: **Fill one computer, then the next** (`overflow`), **Spread evenly** (`balanced`) or **Only computers I pick** (`manual`). A change saves at once.
- **Only computers I pick** shows a checkbox for each computer, and a **Shared** checkbox beside each one you pick. Nothing is saved until at least one computer is picked.
- When Raticode refuses a change, such as a computer another placement holds without sharing, the reason shows under that row and your picks stay so you can fix them.
- A computer unpaired after you picked it shows under its row as missing. Uncheck it to save that placement again.
- An **Only computers I pick** placement without this computer shows its `notice` under its row as a warning, because its work waits everywhere (see [Where work runs](#where-work-runs)). The phone's **Where work runs** tab shows the same line under the placement, and `Raticode computers placement --output text` prints it under the table, after the placement's name.

- **Suggestions are pick-only.** A waiting Rem task or organization ticket can suggest a paired desktop with room (see [Run waiting work on a paired desktop](#run-waiting-work-on-a-paired-desktop)), but no strategy or setting here sends work to a paired desktop on its own. Automatic placement on paired desktops stays off until these two-desktop checks pass, each run between two real paired desktops:
  1. A handed-over task runs on the other desktop and its answer, report path and usage come back to the stand-in here, counted once.
  2. **Stop** here stops the run there, and the stand-in settles as stopped.
  3. After either desktop restarts, the stand-in follows the same task there and never sends it twice.
  4. While the other desktop is offline the run waits for it with the reason, and never runs here instead.
  5. The other desktop refuses work it shouldn't take (no **Run threads from this desktop**, a task type that changes files, a provider it doesn't allow), and this computer shows the refusal.
  6. Work from another desktop takes a worker under the receiving desktop's **Worker limit** and waits there while it is full, so neither desktop goes past its limit.
  7. An organization ticket runs there only through the owner's fleet execution target for its worker, and its usage and attempt record stay correct on both desktops.

On the phone, **Usage › See computers › Where work runs** shows and changes the same placements ([Raticode Mobile](../mobile/workspace.md#desktop-settings-usage-and-the-developer-log)).

## Rem's decisions

When Rem or one of its workers needs you to decide something, it brings you one recommended answer with the evidence behind it, never a bare question. Raticode records your answer under the project and a topic, so the same question isn't asked twice.

New in 0.6.0.

- **Recommended answer first.** A question shows as a card: the question, **Rem recommends** with its answer, one line of evidence (a file, a quote from a file, a command the worker ran, or a commit) and up to three other options. Raticode checks the evidence itself: a cited file must exist in the project, a quote must appear in its file, a command must have run in that task and a commit must exist. Evidence that fails the check is dropped.
- **No bare questions.** A reply that asks you something without a recommendation and checked evidence is sent back once. If it fails again, Raticode builds the card itself, titled **Rem couldn't settle this. Here is what it found.**, with what the worker found and any answer Rem had, marked as not checked. Only a question about credentials, an account, a payment or something only you can do may come without a recommendation, and then it says what Rem already tried.
- **Use this or Change.** Choose **Use this** to take the recommendation, or **Change…** to give your own answer. The task that stopped for the question continues with your answer; you don't have to repeat it in its thread. You can also answer in chat ("yes, use that", "go with pytest instead"), and Rem records it. That works only for a question still waiting for you, and only with words from your own message; a decision already made changes through [a card you press **OK** on](#ask-rem-to-change-a-decision).
- **Turn down or forget.** **Turn down** tells the task that asked to skip that part, finish everything else and say what is left. **Forget** on a question that is still waiting or queued does the same, as a withdrawn question, and its card in chat says **You withdrew this question**, on the desktop and on a paired phone.
- **Asked once.** Before a card is shown, Raticode looks up the project and topic. If it's already decided, the worker gets the recorded answer instead. Two workers asking the same thing share one card, and a project shows at most three open questions at a time. A fourth question is kept as itself, **queued** with no card yet, and the task that asked it waits on that question, not on one of the others. When you answer, turn down or forget one of the three, the oldest queued question gets its card. In chat, Rem says a new question is queued behind the ones already waiting.
- **When a decision is asked again.** Rem asks again on its own only when a file its evidence cited has changed, or a decided command failed twice in a row; the new card says which. You can also choose **Ask again** on a decision. Either way the old answer stays in force until you choose: workers keep using it, Raticode keeps running a decided check command, and the new card counts toward the project's three (it is queued behind them when they're full). Your card says **You asked again** and shows the old answer as **Current answer**, with **New answer…** to replace it and **Keep current answer** to close the card. For the check command, landing branch and decision folder, Ask again also reads the project's files again, and what they say now becomes the card's recommendation. Other decisions come back with no recommendation. To stop using an answer right away, such as a check command that no longer runs, use **Edit answer** to fix it or **Forget** it instead: Ask again keeps it running until you answer. New in 0.6.0.
- **Workers use them.** Every worker in a project gets a short index of the decisions in force for it, and Rem's chat sees them for the projects in the window. Fact decisions, such as the landing branch (`branch.integration`), the check command (`checks.fast`) and the decision folder (`decisions.folder`), are always listed in full. Each decision record follows as one line: its ID, topic, the first sentence of its decision and its file, where its paths and full text are. Records fill the rest of about 4,000 characters, the ones that match the task first; when some don't fit, the index ends by saying how many were left out and that they are in the decision folder. Reviewers still get the full text of every record whose paths a change touches. New in 0.6.0. See [How the work is held to decisions](#how-the-work-is-held-to-decisions).

### How Rem finds the answers

New in 0.6.0.

Before Rem asks you how a project works, Raticode reads the project. A **project scan** looks for three answers, with no model call, and runs nothing:

| Topic | What it looks for | Where |
|---|---|---|
| `checks.fast` | The command that shows the work is sound before it lands | Lines in agent and contributor docs (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `README.md` and similar) that say to run a command before landing, merging or opening a pull request; commands CI runs; check scripts such as `scripts/check-*`; test, check and lint scripts in `package.json`, `Makefile`, `justfile` and `pyproject.toml`; and the test command of the build tool the project uses (`tox`, `nox`, `cargo`, `go`, Gradle) |
| `branch.integration` | The branch finished work lands on | How many branches were merged or fast-forwarded into each local branch, docs that say where work lands, the enabled worktree skill's description, the remote's default branch and the branches CI runs on |
| `decisions.folder` | A folder of decision records the project already keeps | Numbered Markdown records with a status, in any folder |

Each candidate keeps the exact line it came from, which becomes the recommendation's evidence, and the next best ones become the other options. Commands that install, fix, format or publish never count as checks. Raticode adds no tool, folder or branch of its own: a project that names none gets no question.

- **When it runs.** Automatically when a project's first develop or maintain task starts, for topics that have no entry yet, so it never brings back a question you answered, turned down or forgot. And whenever you ask: **Look for answers in this project** at the foot of the Decisions list, **Look again** on a decided entry on the phone, or `Raticode rem decisions scan`.
- **What happens to what it finds.** Each answer goes through the same decisions as any of Rem's questions. With auto mode off it waits for your **Use this** or **Change…**. With auto mode on, Rem records it as its own provisional answer and keeps working.
- **Only the scan or you set these three.** Raticode acts on these answers itself: it runs the check command, checks work landed on the branch and writes records to the folder. So a worker or Rem's chat asking about `checks.fast`, `branch.integration` or `decisions.folder` never gets a provisional answer, even with auto mode on. Its question waits for your **Use this** or **Change…**, and in auto mode the worker carries on without it and says what's left. The scan, which reads only the project's own files, still records its answers as provisional in auto mode.
- **It never changes a decision.** A decision in force stays as it is. If the scan finds the same answer in files that have since changed, it only refreshes the evidence, so editing `AGENTS.md` doesn't ask you again. If it finds a different answer and the decision's evidence changed, that answer waits for you as a proposal to replace it. An answer you turned down is never brought back.

### Auto mode decides for you

With self-work on and **Approve its own backlog tickets** turned on in **Settings > Rem**, Rem confirms its own decisions, the same way it approves its own backlog tickets:

- A question whose answer can be undone is decided with Rem's recommendation straight away, marked **Rem decided (auto mode)**, recorded and reused. The work keeps going. You can still **Use this** to confirm it or **Change…** it later.
- A question whose answer can't be undone, or one that would change a decision already recorded, waits for you. In auto mode the work doesn't stop for it: the worker finishes everything that doesn't depend on it and says what's left.
- Such a question sends a **Needs you** alert, once per question, because no task stops to tell you. On your paired phone it says "Rem has a question" (with the question when **Show Rem's words** is on), and tapping it opens **Decisions** on that question. `Raticode rem notices` lists it as a `needs_you` alert with the decision's ID, question, project and Rem's recommended answer. A question the project scan files, a fix for conflicting decisions, and a queued question that moves up when you settle another send the same alert. A task that does stop for a question already alerts you through its task update, so it sends no second one.
- A worker's question about the check command, the landing branch or the decision folder waits for you the same way. Only a [project scan](#how-rem-finds-the-answers) or you set those.
- Credentials, accounts and payments still need you, as before.

With auto mode off, every question waits for your **Use this** or **Change…**.

### Who can change a decision

Rem can add new decisions. It can never edit, replace or remove one on its own: Raticode refuses it, and that covers record files in the repository too. When Rem's answer differs from a recorded one, or it thinks a decision should change, it files a proposal that names the decision it would replace, and the recorded one stays in force until you approve. Only you can confirm, change, edit, turn down, ask again, retire or forget a decision, from the **Decisions** list in Rem's side pane, the Decisions screen on a paired phone, `Raticode rem decisions` (see [CLI: Decisions](../reference/cli-rem.md#decisions)), or by asking Rem in chat and pressing **OK** (below). Agent tokens and workers can read decisions but not change them.

### Ask Rem to change a decision

New in 0.6.0.

Tell Rem in chat to change a decision that's already made, such as "update D-7 so TOML is allowed for imports" or "replace the database decision with Postgres". Rem doesn't change it. Its reply carries a card showing the change, and nothing happens until you press **OK**.

- **The card shows the before and after.** It names the decision and lists each part that changes, the old text above the new: the decision itself, its question or why, and for a [decision record](#decision-records) any of its parts, paths or signal words. Raticode works out both sides from the decision as recorded, not from Rem's wording, so the card shows exactly what OK will write.
- **OK applies it.** An edit rewrites the decision; a replacement writes a new decision in place of the old one and links them, as **Replace with…** does. A record's Markdown file follows on the landing branch, and its history notes that you confirmed it in chat. **Cancel** leaves the decision as it was.
- **On the phone too.** A message you send from a paired phone gets the same card in its reply, with **OK** and **Cancel**. Changes still waiting for you also show under **Waiting for you** in the Decisions list and on the phone's Decisions screen, and `Raticode rem decisions confirm-edit E-2` or `cancel-edit E-2` press them from the CLI.
- **Only your own request makes a card.** Rem has to quote the words in your message that ask for the change, and Raticode checks they're there. A workflow asking Rem, work queued to run after a task, a task's result, a worker's reply, a screen you shared or a file never brings a card. Self-work and auto mode don't change this: Rem still can't change a decision on its own.
- **If the decision changed meanwhile.** OK applies only while the decision is as the card showed it. If it was changed since, on the phone, the CLI, the Decisions list or by a record edit, OK is refused with the reason; press Cancel and ask Rem again. A newer request for the same decision replaces the card still waiting.
- **Removing a decision** still happens with **Forget** in the Decisions list, or **Retire** for a [decision record](#retire-a-record) whose file is in the repository.

### Plan with me

New in 0.6.0.

Ask Rem in chat to plan something with you, such as "plan offline sync with me" or "grill me on the auth design". Rem works out the decisions the work rests on and asks you about them one small batch at a time, then turns your answers into tickets. Rem never starts a plan on its own: only your own message asking for one, on the desktop or a paired phone, does.

- **Questions are decision requests.** Each question is an ordinary [decision request](#rem-s-decisions): Rem's recommended answer, evidence Raticode checked and up to three other options. A question whose evidence can't be checked is left out, and Rem's reply names it. One whose topic is already decided counts as answered.
- **A few at a time.** The project's usual limit of three waiting questions applies, so you see the first ones and the rest are queued behind them. Each queued question gets its card as you answer, turn down or forget one.
- **Always yours to answer.** Plan questions wait for you even with [auto mode](#auto-mode-decides-for-you) on. Answer them with **Use this**, **Change…** or **Turn down** in the Decisions list or on the phone, or in chat ("yes, use that").
- **Grouped under the plan.** In the Decisions list and on the phone's Decisions screen, a plan's waiting questions sit under its title with how many are answered, such as **Offline sync · 2 of 5 answered**, and a line such as **2 more come up as you answer**.
- **Then tickets.** When the last question is settled, Raticode hands the plan back to Rem once, with every answer, as a message in Rem's chat marked **Your plan's answers**. Rem files the work on your board as thin end-to-end slices: each ticket is a small change you could see working on its own, says when it is done, lists the earlier slices it waits for, and starts with the decision IDs it rests on, such as **Rests on D-12, D-14**. A slice without a way to tell it's done isn't added, and Rem's reply says so. Turned-down and forgotten questions are left out.
- **Answers go only to the work that rests on them.** Unlike the check command or the landing branch, a plan's answers aren't sent to every worker and every chat turn. A worker gets an answer when its task names that decision or the plan: a slice that starts **Rests on D-12, D-14** gets those two, and one marked **(plan P-3)** gets all of the plan's answers. Rem gets the same ones when it answers that worker's question. Rem's chat lists a plan's answers while you are still answering its questions and until it is handed back; the hand-back message carries every answer itself, and later chat turns leave them out. They still count everywhere else: they stay in the Decisions list on the desktop and the phone, and self-work research still lists every plan's answers, so an idea it names as contradicting one isn't added (see [Self-work keeps to decisions](board.md#self-work-keeps-to-decisions)). An answer Rem keeps as a [decision record](#decision-records) is listed like any other record. New in 0.6.1.

### Decision records

New in 0.6.0.

Some decisions are about how the code is built, such as "Workflows are written in Rattish, never TOML". Rem keeps those as **decision records**: short Markdown files in the project's own repository, so you, every worker and any reviewer read the same text. They are part of the same decisions as everything else, listed in one place.

- **Where they live.** In the folder the project already uses for decision records (topic `decisions.folder`, found by the [project scan](#how-rem-finds-the-answers)), or `.raticode/decisions/` when it has none. You can change the folder like any decision.
- **What a record holds.** A title, the decision in one or two sentences, and optional parts: Context, Options we didn't choose, Consequences and How we check it. It can also name **paths it applies to** (globs such as `src/rattish/**`), **paths a change must never add** (such as `**/*.toml`) and **signal words** that suggest a proposal contradicts it. Paths are relative to the project root and keep their dots: `.github/workflows/**` covers only `.github`, and `./` or `/` at the start is dropped, so `/secrets/**` means `secrets/**`. Signal words match whole words in any case, so `rust` flags "use Rust here" but not "trust". Files use front matter like [MADR](https://adr.github.io/madr/); existing records with a `## Status` section are read as they are.
- **How they reach the repository.** Raticode commits each record on the project's [landing branch](#changes-land-on-the-project-s-branch), one file per commit, as the repository's own author. It never touches your other files: a checkout of that branch moves only when it has no uncommitted changes. Otherwise the record shows **Not in the repo yet** with the reason. Raticode tries again within about a minute of the checkout becoming clean or the branch moving, and also when you change a decision or a task starts in the project. Opening or refreshing the Decisions list only reads the record files and never commits. A project with no landing branch decision keeps its records in Raticode until it has one.
- **Edits outside Raticode count.** The file is the wording that counts. Edit a record in your editor and commit it on the landing branch, and the Decisions list shows the new text, marked **Edited outside Raticode**. Workers get it on their next run. A record file you add yourself shows up as a decision record too. Its line says where it came from instead of **You decided**: **Read from the repository: added in 1a2b3c4d5e6f by Sam Lee on 2026-03-04.**, naming the commit that first added the file on the landing branch, that commit's author (never an email) and its date. Once you confirm or change it in Raticode, it says **You decided** again. `Raticode rem decisions show` says the same, and so does the phone app from its next release. New in 0.6.1. A file only becomes an existing decision when that decision is a record still waiting for its file: one naming the ID of another decision, such as the check command or the landing branch, is read in as a record of its own and never changes that decision. A change a Rem task made is never taken as yours: Raticode doesn't read it in, puts the approved text back with its own commit, and a record file a task added is ignored. Until that commit lands, for example while the landing branch checkout has uncommitted changes, the record says **The file differs from the approved text; Raticode puts it back when the checkout is clean**, and its history shows **Raticode Kept the approved text** with the file the task changed. While a task in the project is still running, a changed record waits until it has finished.
- **What each path matches.** Next to every Applies to and Never touch path, the record shows how many files tracked on the landing branch it matches, counted at the branch's newest commit. An Applies to path that matches no file shows **matches no files**, which usually means a typo or a folder that moved: until a change adds a file there, no change brings the record to a reviewer. A Never touch path that matches files shows **matches N files that exist now**: those files are already there, and any change to them fails the check below. The **Add a record**, **Edit wording** and **Replace with…** forms show the same warnings while you type, but they never stop you saving, since a path can be planned before its files exist. The phone's record page shows the counts too, and its Edit and Replace with… forms warn about the paths the record already had; a path you type there is counted once it is saved. `Raticode rem decisions show` prints each count, and `Raticode rem decisions paths` counts paths before you save them. A project with no landing branch decision has nothing to count on, so its paths show without counts. New in 0.6.0.
- **Who writes them.** Rem may add a new record: with auto mode on it is recorded as **provisional** and written straight away; otherwise it waits for your **Use this**. Rem never edits, replaces or removes a record on its own; when you ask it to in chat, the change waits for your **OK**. Raticode only rewrites a record's file after a change you made, and a worker that edits the decision folder fails Raticode's check (below).

#### Retire a record

New in 0.6.0.

A record whose file is already in the repository has **Retire** where other decisions have **Forget**, in the Decisions list, on the phone's record page and as `Raticode rem decisions retire`. Forget is refused for such a record and says to use Retire, because retiring commits a change to the file: a Forget never makes a commit you didn't ask for. Before 0.6.0, `forget` retired such a record too. Deleting it wouldn't stick: the file is still on the landing branch, so the next read of the folder would bring it back as a new decision with a new number.

- **The decision stays, closed.** It moves to **History** marked **Retired**, with a **Retired** entry in its own history. It keeps its ID, so nothing gets a new D-number.
- **The file stays and says so.** Raticode rewrites the file with `status: rejected` in its front matter and commits it on the landing branch like any other record change, as **Retire decision D-7**. The file isn't deleted, so the repository keeps a record of what was once decided. If the landing branch checkout has uncommitted changes, the file is rewritten once the checkout is clean; until then, Raticode still matches the old file to the retired decision.
- **Work stops using it.** Workers no longer get it in their list of decisions, reviewers no longer check changes against it, and its paths and signal words no longer block anything.
- **Retired is final in Raticode.** A retired record has no Forget. To bring it back, add a new record, or edit its file yourself and set `status: accepted` again: an edit you commit outside Raticode counts, as above.
- A record that isn't written yet, and every decision that isn't a record, still has **Forget**, which deletes it.

### How the work is held to decisions

New in 0.6.0.

When a develop or maintain task in a project with a landing branch finishes a turn, Raticode compares the files it changed since that branch with the decisions in force, before any review:

- **Raticode's decision check.** A change inside the decision folder, or one that adds or modifies a path a record forbids, fails with no model involved. The changes are counted from the landing branch's tip when the task started, so work the worker already fast-forwarded into that branch is still checked. Other work that landed meanwhile isn't counted against it, and a record edit you commit on the landing branch while the task runs stays yours. Every other new commit counts as the task's, including one the worker made without `git commit`. The work goes back to the worker with the record it broke; after two failed repairs the task is blocked with the reason. This needs no sandbox, because it only reads git.
- **The reviewer checks the records that apply.** Each record whose paths the change touches goes to the reviewer in full, as an added check. A record the reviewer finds broken sends the work back even when every acceptance criterion passed. One it couldn't confirm sends it back too, except in auto mode, where it is noted as not verified.
- **Conflicting decisions get one clean fix.** If two decisions can't both hold, Rem doesn't try to satisfy both. The reviewer or Rem reports the conflict with one recommended replacement, which waits for you as a single card, auto mode or not: **Use this** replaces every decision it names with the new one. Until then the work that depends on them is set aside and the rest goes on.
- **Self-work can't work around a decision.** Self-work is held to the decisions in force at four points, all checked by Raticode with no model involved. See [Self-work keeps to decisions](board.md#self-work-keeps-to-decisions) for what you see on the board.
  1. **Writing tickets.** A ticket self-work research proposes is not saved to the board when it contradicts a decision: it names the decision in its own list of conflicts, or its title, description or finish line contains one of a record's signal words. The report and Rem's chat update name the idea and the decisions it contradicted.
  2. **Pulling from Backlog.** A Backlog ticket that contradicts a decision is never moved to To do. It goes to **Blocked**, marked **Violates decisions** with the decision IDs and the reason.
  3. **Starting a ticket.** A ticket is checked again as it starts, from To do, from Backlog or because you moved it to In progress. One that contradicts a decision is blocked and marked the same way, and no worker starts.
  4. **Review.** The decision check above also fails work done for a ticket that now contradicts a decision, for example one you added while the task ran. The task stops blocked, with no repair round, and the ticket is marked.
- **No exceptions for itself.** Only you can change a decision. A request Rem files that would set one aside never applies on its own, even with auto mode on: one that names a decision in force, uses a record's signal words, covers paths a record forbids, or asks for an exception, waiver, override or bypass waits for you as a conflict, with a note saying which decision it would weaken. The worker is told to keep to the decisions meanwhile.

### The Decisions list

The **Decisions** section sits under **Objectives** in Rem's side pane and shows a count only when something waits for you. It has four parts:

- **Waiting for you**: questions with Rem's recommendation, decisions Rem made in auto mode, proposals to replace a decision and conflict fixes, each with **Use this**, **Change…** and **Turn down**. Changes you [asked Rem for in chat](#ask-rem-to-change-a-decision) show here too, with their before and after and **OK** and **Cancel**. Questions past a project's three cards show as one line, such as **2 more queued**. Questions from a [plan](#plan-with-me) sit under the plan's title with how many are answered. Under each question one line says who asked it, when, and what waits on it, such as **Asked by REM-249 · 21 Nov · REM-249 waits for this**, or **… went on without it** when the task that asked carried on, as it does with [auto mode](#auto-mode-decides-for-you) on. Who asked is a ticket key, or the task's title when it has no ticket, or **Rem's chat**, **Project scan**, **Self-work** or **you** (after Ask again). Click the line to open the task. New in 0.6.0.
- **How Rem works here**: one line per decided project fact, such as the check command and landing branch, with **Edit answer**, **Ask again** and **Forget** in its menu.
- **Design decisions**: the decision records in force. Each opens on its decision, with the paths it applies to and its other parts folded. **Edit wording** changes the text and parts (with a one-line note kept in its history); **Replace with…** writes a new decision that replaces it, and both stay linked. **Retire** closes a record whose file is in the repository (see [Retire a record](#retire-a-record)).
- **History**: replaced, turned-down and retired entries, folded.

Click any decided or closed line to open it. It shows why, who decided it and when (**You decided on 2026-11-19.**, **Rem decided (auto mode).**, that Raticode carried it over from what it already did, or which commit added a record file it read from the repository), every piece of evidence with its excerpt, Raticode's last run of a decided check command, and a folded **History** list, newest first. Each history row has its date, who acted (**You**, **Rem** or **Raticode**), what happened, such as **Asked**, **Confirmed**, **Edited**, **Replaced by D-9** or **Kept the approved text**, and the note when there is one, such as the reason you gave for an edit. Raticode keeps the last 20 entries per decision. New in 0.6.0.

It shows the current project's decisions, with a switch for all projects, **Look for answers in this project** to run a [project scan](#how-rem-finds-the-answers), and **Add a record** to write a design decision yourself. A record that isn't in the repository yet, whose file a Rem task changed (**File differs**), or that was edited outside Raticode, says so on its line. A conflict fix shows **Replaces D-3, D-5** and why they conflict. A decided check command shows Raticode's last run of it, and a decision carried over from what Raticode already did says so when you open it. On a paired phone, the Decisions screen opens from Rem's menu next to What Rem remembers, and a task update in Rem's chat carries the same card with **Use this** and **Change**.

## What Rem remembers

**What Rem remembers**, in the Rem options menu, lists the facts and preferences Rem saved. You can add, edit, forget or copy them, and pick a mode:

| Mode | Uses them | Saves new |
| --- | --- | --- |
| **On** | Yes | Yes |
| **Paused** | Yes | No |
| **Off** | No | No |

A reply that saved or changed a memory shows a note with **Undo**. Text that looks like a password, key or token is never saved.

### Project memories

A memory can belong to one project. This isn't in a release yet.

- When Rem saves a fact that names exactly one of your open projects, by its name or its folder, the memory belongs to that project. A fact that names no project, or more than one, applies everywhere.
- Rem's chat uses the memories that apply everywhere and those of your open projects. A worker task, and Rem when it answers that worker, use the memories that apply everywhere plus those of the task's own project. A task in a worktree or a subfolder of the project counts as in it. Other projects' memories are never sent.
- When there are more memories than a prompt holds (40 for Rem's chat, 20 for a worker), the ones that apply everywhere are chosen first. The project's own memories fill the space left, those sharing words with the request first, then the newest.
- In **What Rem remembers**, a project memory shows its project's name as a tag. **Show** filters the list to one project or to the memories for every project. **Edit** has an **Applies to** menu to move a memory to an open project or back to every project, and **Add** has a **For** menu.
- Memories saved before this change apply everywhere until you move them.
- On the phone, the memory list shows the same project tag. Editing a memory's words there keeps its project.

### Memories with an end date

A memory can have an end date, for a fact that only holds until a day or an event has passed. This isn't in a release yet.

- When Rem saves a fact tied to a date or an event, such as a trip, a deadline or a temporary arrangement, it proposes the last day the fact applies. The reply's note shows that day, for example "Saved to memory until Jun 30", next to **Undo**. Facts that don't run out get no end date.
- The end date counts through that whole day in your computer's timezone. After it, the memory is no longer sent to Rem's chat or to worker tasks.
- An ended memory is never deleted on its own. **What Rem remembers** shows it dimmed with an **Ended** tag and two buttons: **Keep** removes the end date so Rem uses it again, and **Forget** deletes it. A memory that hasn't ended yet shows an **Until** tag.
- Every memory row has **Set an end date**, which picks the last day Rem uses it. **No end date** removes a date you set, and correcting a memory's words keeps its date.
- An end date given as a date and time must include its timezone, the same check [organization memories](../advanced/organizations.md) use for their expiry, and a day that has already passed is refused.
- On the phone, the memory list shows the same **Until** and **Ended** tags, dims ended memories, and has **Set an end date** and **Keep**.

**Clear history and tasks**, in the same menu, asks first. It stops Rem's running work and deletes its conversation, tasks, memories and worker threads. Your own threads, project files and saved reports stay.

### If Rem's database is damaged

Raticode keeps the last 3 verified copies of Rem's database, taken at startup and once a day. If the database is damaged when Raticode starts, Raticode moves it aside without deleting it, restores the newest copy and posts a notice in Rem's chat, on the desktop and on the phone, saying which copy was restored and when it was saved. Workflows, the studio and phone pairing keep running. See [Rem's database backups](../knowledge/second-brain.md#rem-s-database-backups).

See also [Global board and self-work](./board.md), [Chat and threads](./chat.md) and, on the phone, [Rem on the phone](../mobile/rem.md).
