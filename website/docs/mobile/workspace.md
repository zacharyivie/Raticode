---
title: Projects, boards and workflows on the phone
description: Read files and Git history, work the global board and Organizations, run workflows and browse the web from Raticode Mobile.
pageClass: docs-layout
---

# Projects, boards and workflows on the phone

These pages need **Use Raticode from this phone** in the computer's [device settings](./pairing.md#choose-what-the-phone-can-do). The browser is the exception: it works on its own.

Find each page in **More**, or put it on the bottom bar. When the computer is too old for a page, the page says so.

## Files

**Files** lists the projects open in Raticode on your computer. Browse one folder at a time, with a breadcrumb trail and a name filter.

Tap a file to open it full screen:

- code and text with line numbers, highlighting, wrapping and find
- previews of Markdown, HTML (no scripts, nothing loaded from the internet) and SVG
- PDFs page by page, zoomable pictures, video and sound
- other formats offer **Open in another app** and **Save to phone**

Files is read-only.

- Large text files show their first 256 KB. **Show all** fetches the rest, up to 10 MB.
- Other files over 10 MB can't come to the phone.
- Files that look like secrets, such as `.env`, show a lock and never leave the computer.

### Open files from other apps

*New in Raticode Mobile 0.36.0.* Raticode can also open files that are already on the phone, from Files, Downloads, a chat app or anything else that opens files. **More › Open with Raticode** lists what it can open:

| Kind | Types |
| --- | --- |
| Web pages | HTML and XHTML |
| PDF documents | PDF |
| Pictures | PNG, JPEG, GIF, WebP, BMP, HEIC, AVIF and icons |
| SVG drawings | SVG |
| Videos | MP4, WebM, 3GP, MOV, MKV and Ogg video |
| Sound | MP3, M4A, AAC, WAV, Ogg, Opus, FLAC and AIFF |
| Markdown | Markdown |
| Text and code | Plain text, CSV, CSS, XML, JSON, JavaScript, Python, Java, Kotlin, C, shell scripts, YAML and TOML |

The first time you open one of these, Android asks which app to use. Choose Raticode, then **Always**, to make Raticode the default viewer for that kind of file. **Android default apps settings** and **Raticode's Open by default settings** on the same page take you to Android's settings; clear Raticode's defaults there to stop.

The same default applies to **Open** on a file Rem or your computer sent, such as a report: with Raticode as the default for HTML or PDF, it opens in this viewer, which then reads *Opened from Raticode*. Your [app lock](./locks.md#app-lock) covers it like any other Raticode screen, and while the app lock or child lock is on the viewer hides the file and says Raticode is locked. Raticode's other files on the phone, such as camera photos for Rem or Developer log hand-offs, never open here.

What you open this way is treated as untrusted:

- The file opens in a viewer of its own, read only, up to 50 MB. Text shows its first 2 MB.
- Web pages and SVG drawings show without running their scripts, loading anything from the internet or reading other files, like HTML previews in **Files**.
- The viewer has no **Ask Rem**. Nothing you open this way goes to Rem, a thread or your computer.
- Raticode reads only the file the other app gave it, with Android's one-time permission for that file. It asks for no storage permission, so apps that hand over a plain file path instead of sharing the file can't open it in Raticode.

## Source control

**Source control** lists your open projects with their branch, commits to push or pull, changed files and any merge or rebase in progress. Each project has the desktop's three views:

| View | Shows |
| --- | --- |
| **History** | The branch graph with branch and tag labels, and each commit's author, time, ID and added and removed lines. Filter by branch, message, author or ID; tap a commit for its files and diffs. |
| **Changes** | Conflicts, staged and other changes. Tap a file to read its diff. |
| **Branches** | Local branches and how they track their upstream, then worktrees |

It's read-only: stage, commit, push, pull and switch branches on the computer. See [Source control](../studio/source-control.md).

## Organizations

The **Orgs** tab shows each [organization](../advanced/organizations.md) with its **Organization**, **Projects**, **Issues**, **Workers** and **Knowledge** sections.

- The list marks each organization that is waiting on you with **N need you** (Rem's requests, results to review, blocked tickets and plans to approve), shows why a paused one stopped, such as a reached limit, and puts archived ones last.
- A new organization's **Organization** page shows **Get started** until it has run once. Add workers and project folders in Raticode on your computer; **Add** opens a new ticket here.
- **Start** asks you to confirm and says when nothing will run yet: no workers, every worker held, no project folder or no open tickets.
- You create organizations in Raticode on your computer. It only needs a name.
- **Issues** keeps each organization's project, worker and **Needs me** filters, and the column you were on, after you leave it or close the app. A filter that's on is highlighted, and **Clear filters** turns them off. A removed project or worker falls back to all.
- An open organization shows **Offline** beside its state while the phone isn't connected. A strip under the header says when the page shows older information: offline, or after a refresh failed. After a failed refresh, **Try again** asks your computer again.

**Organization** shows the [work schedule](../advanced/organizations.md#work-schedule): the block on now and its focus, the next block and when it starts, warnings about gaps, and which block each running worker started under.

- Tap the card to change blocks, their cadence (daily, weekly, monthly or quarterly), times, keywords and time zone.
- With no schedule yet, **Use suggested schedule** fills one in for you to edit.
- Nothing is saved until you tap **Save**, and the computer checks the schedule again before saving.
- Offline, you see the schedule you last loaded and can't edit it. An older computer asks you to update it.

A worker's page shows its model, account, access, check-in interval and manager as tiles, and below them which tickets it may change in each project. When the worker's provider has accounts, tap the **Account** tile to pick another one or the default sign-in. See [worker accounts](../advanced/organizations.md#worker-accounts). An older computer doesn't show the choice.

**Needs you** on the same page shows Rem's requests with **Approve** and **Decline**. Approve asks you to confirm first. With two or more waiting, **Approve all** lists exactly what it will approve and approves only those, in the [same safe order as the computer](../advanced/organizations.md#approve-several-requests-at-once); a request that arrives after the list opens keeps waiting. A card then shows each request as **Approved**, **Failed** or **Left waiting**. The phone approves exactly what you could approve at the computer, including requests that let workers run without a sandbox or add secrets. An older computer that can't take decisions from the phone asks you to update it, and an older computer may leave unsandboxed requests waiting for you there.

The **Auto mode** card turns [Auto mode](../advanced/organizations.md#auto-mode) on or off. Turning it on asks you to confirm what it allows. With it on, the organization shows **Auto**; requests Rem is deciding say **Rem is reviewing** and aren't in Approve all; the card lists Rem's recent decisions with their reasons; and if Auto mode stopped the organization, a card says why and what to do. An older computer shows an update note instead of the switch. You can also [tell Rem to approve](../advanced/organizations.md#tell-rem-to-approve) in its chat on the phone.

**Backlog pulls** on the same page turns [workers pulling accepted Backlog tickets into To do](../advanced/organizations.md#pull-from-backlog) on or off. A Backlog ticket's details say whether a worker will pull it or what it waits for. Offline the switch is disabled, and an older computer that doesn't have the setting asks you to update it.

## Global board and self-work

**More › Global board** shows every organization's tickets and Rem's own, one column at a time. Filter by whose tickets, who they're assigned to and which project. Like the computer's board, it has a **Blocked** column for work that is waiting, paused, stopped or failed. A paused ticket shows **Paused** or **Paused for your work**; moving it to **In progress** resumes it.

- Add a ticket for Rem or an organization, assign it, or hand one of Rem's tickets to an organization.
- Long-press a ticket to move it.
- After a move, a notice at the bottom of the board or the ticket's page names the ticket and where it went, such as "REM-12 moved to Backlog.", "REM-12 cancelled." or "REM-12 started." **Undo** on the notice moves the ticket back to the column it came from, as on [your computer's board](../rem/board.md). A move that starts or resumes work, and a move out of **In review** or **Blocked**, gets a notice without Undo, and so does any move of a paused ticket. A notice with Undo stays for about 10 seconds and others for about 4, longer if your phone's accessibility settings ask for more time; **×** closes it. If your computer refuses a move, it says why instead. *Not in a released version yet.*
- The filters, the column you were on and the open section (**Board**, **Proposed**, **People** or **Self-work**) stay after you leave the board or close the app. A filter that's on is highlighted, and **Clear filters** turns them off. If a saved organization, person or project is gone, that filter shows everything again and the others stay. The phone keeps its own filters, separate from your computer's.
- Open a ticket to see it at a glance (see [A ticket's page](#a-ticket-s-page)).
- **History and comments** on a ticket's page lists each status change with its reason, oldest first, and comments from you, Rem and workers. Long results stay folded until you tap **Show the full text**. Add a comment at the bottom.
- Offline, a ticket shows the history you last loaded. An older computer asks you to update it instead.
- Tapping a ticket pill in Rem's chat opens that ticket here. A ticket no longer on the board says so.
- A ticket that contradicts one of the project's decisions shows a red **Violates D-4** badge on its card, and its page shows a **Violates decisions** chip with the reason in the box at the top. Self-work won't start it until you change the ticket or the decision; see [Self-work keeps to decisions](../rem/board.md#self-work-keeps-to-decisions). An older computer sends no marker, so nothing shows. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- One of Rem's open tickets shows its [self-work objective](../rem/board.md#self-work-honors-the-objectives) on one short line of its card: the objective it advances, or **Not on an objective: self-work leaves it** while objectives are open. Its page has an **Objective** section with the same words as your computer's sheet, such as why self-work leaves it and **Move it to In progress to start it anyway.** An older computer sends neither, so nothing shows. *Not in a released version yet.*
- A ticket that [waits for other tickets](../rem/board.md#tickets-that-wait-for-other-tickets) shows **Waits for:** on its card. Its page lists them in **Waits for**, where you add one with **Wait for a ticket…** or remove one with its **×**. If one was cancelled, the card and page show a notice instead. An older computer sends no list, so nothing shows.

### A ticket's page

A ticket's page is laid out to be read in a few seconds. Nothing from the earlier layout was removed; long text is folded. *New in Raticode Mobile 0.36.0.*

- **At the top:** the key, who owns the ticket, a large title, and colored chips for its column, **Running**, **Paused** and **High** or **Critical** priority.
- **Column strip:** a strip of dots shows where the ticket is between **Backlog** and **Done**. A blocked or paused ticket shows the reason in a tinted box.
- **Auto-approved:** this is a chip. Tap it to read why self-work approved the ticket.
- **From** a desktop's name: this chip marks a proposal from a self-work pass that ran on a paired desktop through **Run there**. Such a proposal always waits for your approval, even with auto-approval on. Tap it to read why. The card shows the same label, and the self-work report says **To approve, from** that desktop. Not in a released version yet.
- **Assigned to:** shows the assignee with their avatar. **Change** picks someone else, and **Their projects** opens their page. When a Rem ticket can't be handed to an organization right now, **Why can't I change this?** explains.
- **Facts:** **Column**, **Priority**, **Project**, **Kind**, **Waits for** and **Added** show as tiles. Tap **Column** to move the ticket.
- **Done when:** the ticket's finish line, one check per line, above **Why Rem suggests it**. A proposal shows it before you approve. An older computer doesn't send it, so nothing shows. *Not in a released version yet.*
- **Folded text:** **Why Rem suggests it** and **Last result** show their first sentence, with **Show more** for the rest. **Details** folds when it's long.
- **Waits for** and **Waited on by:** each lists tickets with a status dot. Tap one to open it. **Waited on by** shows which tickets are waiting for this one.
- **Pinned actions:** **Move** stays pinned at the bottom of the screen, with **Open** *organization* on an organization's ticket. A proposal pins **Approve** and **Decline** instead.
- **Cancel ticket:** one of Rem's tickets that isn't done, running or paused also pins **Cancel ticket**. It doesn't ask first: the notice that follows has **Undo**. A cancelled ticket pins **Restore to Backlog** instead of **Move**. *Not in a released version yet.*

An organization's ticket in **Orgs › Issues** uses the same layout. Its first two actions are pinned at the bottom, such as **Accept** and **Request changes** for work to review, or **Approve plan**. The others, and **Cancel ticket**, are under **⋯**. Assignee, priority, due date, project, milestone and epic show as tiles, and tapping one opens its page.

The board also has:

| Section | What it does |
| --- | --- |
| **Proposed** | Rem's ideas, with **Approve** and **Decline**. Nothing runs until you approve it. |
| **People** | Rem and every organization's agents. Tap someone to choose the projects they may work in during self-work. |
| **Self-work** | What Rem is doing on its own, where it can work, its objectives, and the report of tickets added and work done. **Self-work settings** opens its settings. |

**Objectives** sits above the report on the **Self-work** section and lists the [objectives](../rem/board.md#objectives) that guide the self-work session, in the computer's order: open ones first, newest first. *Not in a released version yet.*

- Each row shows the objective and whether it's **Open** or **Done**. Tap it for its **Done when** line (or that it's open-ended), who checked it off and the evidence they gave. One row opens at a time, and a second tap closes it.
- The count by the heading is this session's done/total, such as **1/3**, as in Rem's side pane on your computer.
- Objectives finished in earlier sessions are in a folded **Stale** group with how many there are. Tap it to open it.
- With no objectives for this session, the card says so in the computer's words.
- The phone shows the first 30, as the side pane does; the card says how many more are on your computer.
- The list is read-only here. To change it, tell Rem in its chat, typed or spoken. An older computer sends no objectives, so the card doesn't show.

### Self-work settings

**Desktop settings › Rem self-work** holds them, so they work even when the board is off on the computer.

- Turn self-work on or off, set the maximum threads, and choose whether Rem pulls from Backlog when To do is empty. Telling Rem "turn on self-work" or "stop self-work" changes the same switch.
- **Approve its own backlog tickets** lets self-work's new ideas go to Backlog already approved until self-work ends, instead of waiting for you. Turning it on shows what it allows and asks you to confirm; turning it off doesn't. Those tickets carry an **Auto-approved** badge on the board and on their page, and the report says how many it approved. A computer without this setting shows no switch. See [Let Rem approve its own tickets](../rem/board.md#let-rem-approve-its-own-tickets).
- **Allowance use** chooses **Off**, **Daily cap** or **[Pace to reset](../rem/board.md#pace-to-reset)**. With Pace to reset, **Aim by reset** (any percent from 1 to 100) and **Finish before reset** step with − and +, and the computer's pace line shows under them, in amber while self-work holds new work, with a warning when **Rate limit use** is below the aim. A computer without the choice shows only the daily cap. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Weekly allowance per day** (with Daily cap chosen) sets the [daily cap on the weekly allowance](../rem/board.md#daily-cap-on-the-weekly-allowance) with − and +; the change is saved when you stop tapping. Under it is today's use in the computer's words, naming the [account](../rem/providers.md#self-work-on-accounts) self-work runs on when it uses one, in amber when the computer has no current usage reading and self-work starts nothing new. A computer without the cap shows no row. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Accounts** appears when a provider on the computer has at least one account added. Tap a provider to choose which account self-work's runs use: Rem's setting, the default sign-in, choose by usage or one account. Chat, threads and tasks you create keep using Rem's setting. A computer without this setting shows no group. See [Which account self-work uses](../rem/board.md#which-account-self-work-uses). *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- The status line says how many self-work tasks are paused to make room for your work.
- See the report. Its counts (tickets added, work done, duplicates merged, notes) show as tiles, and its summary is folded to the first sentence, with **Show more**. **Where Rem can work** opens the board's **People**.
- Rem's chat shows what happened while you were away, with a task card for each job.

With the board off on the computer:

- the page says so, and backlog pulling is unavailable
- Rem's research ideas appear in the report instead of as proposals; the research jobs still show in Rem's chat
- tapping **While you were away** opens **Rem self-work**

An older computer without the board shows an update note instead. See [Global board and self-work](../rem/board.md) for the computer side.

## Workflows

**More › Workflows** lists the workflows in your open projects. Open one to see its graph, steps, run history and [triggers](#triggers).

- **Run**, **Stop**, **Resume** and **Run again with the same inputs** ask first, then run on the computer with its usual checks.
- **Stop** reaches a run however it started, including one the computer's background trigger scheduler started. The run shows **Stop requested** until it ends.
- You can't edit workflows from the phone, except **Use this model** after [Try another model](#try-another-model).
- When a workflow is missing a [secret](../workflows/inputs.md#secrets), the reason under **Run** names it and says where to add it on the computer. Secret values never travel to the phone, and the phone can't save them.
- When a workflow has an error or fails [preflight](../workflows/validation.md#preflight-diagnostics), the reason under **Run** names the first problem, such as `Line 5: No node contract is available for 'write-fil'. Did you mean 'write-file'?` or a missing Jev key. Fix it on the computer. The computer writes this reason, so it's new in Raticode 0.6.0; Raticode 0.5.5 only says to open the workflow on the computer.

### All runs

::: info New in 0.35.0
The **All runs** list needs Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the tab doesn't appear.
:::

**All runs** at the top of **More › Workflows** lists the runs of every workflow in your open projects, newest first, like the computer's [Workflow runs](../studio/runs.md#every-workflow-s-runs) view. Each run shows its workflow, status, when it started, how long it took, whether it was started by hand, a trigger, the phone or Rem, and its tokens and estimated cost. Chips filter by time (24 hours, 7 days, 30 days or all time), status and workflow, and the line above the list totals what matches. Tap a run to open it in its workflow. The phone shows the newest 60 runs; older ones are on the computer. A run whose model step moved to a [fallback provider](../nodes/agent.md#fallback-providers) reads **switched provider**, and in the run's steps that step says which provider it left, why, and which one answered. That needs Raticode 0.5.5 on the computer; the phone app needs no update. A model step also names the provider app version it started, such as *Provider: Claude Code 2.3.1*; that needs a computer with Raticode 0.6.0 or later, and the phone app needs no update.

### Run with inputs

::: info New in 0.35.0
Filling in a workflow's inputs on the phone is new and needs Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, start workflows that need inputs on the computer.
:::

When a workflow has [inputs](../workflows/inputs.md), **Run** opens a sheet with a field for each one. The fields come from the same input schema as the computer's Run dialog:

- text, whole numbers and numbers
- a yes/no switch
- a row of choices, or a menu when there are more than six
- a JSON box for lists, objects and anything else

Fields start at the workflow's defaults. Leave an optional field blank to use its default. An input whose schema says `"format": "password"` is masked, and its default stays on the computer.

**Run** marks any field that needs fixing, such as a missing required value, a number out of range or JSON that doesn't parse, and sends nothing until they're fixed. The computer then checks the values against the schema again before anything starts. They also go through the same [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone) as everything else the phone sends.

### Share to a workflow

::: info New in 0.36.0
Share to a workflow is new in Raticode Mobile 0.36.0 and Raticode 0.6.0. It needs both: a computer older than 0.6.0 doesn't say which inputs take text, so the phone asks you to update it.
:::

Pick **Run a workflow** in any app's share sheet to send a link, text or one file into one of your workflows. Several files at once are left out; share them to [Rem](rem.md#share-to-rem) instead, or one at a time.

1. Raticode opens **Run a workflow** with what you shared at the top. It lists the workflows in your open projects that have a text input: a plain text [input](../workflows/inputs.md), not a choice, a number or a password.
2. Tap a workflow. If it has more than one text input, choose the one that gets the shared text. The phone remembers that choice for the workflow and uses it next time; **Change input** picks another.
3. The workflow's [Run sheet](#run-with-inputs) opens with the shared text in that input and the other fields at their defaults. Edit anything, then tap **Run**.

Nothing reaches the computer until you tap **Run**. The run goes through the same run request, schema check and [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone) as a run you fill in by hand. **Cancel** or Back on the list, or **Discard** on the workflow, drops the shared text. The phone keeps it in memory only, so it's also gone if Android closes Raticode.

The three workflows you most recently ran from a share appear as direct-share shortcuts at the top of Android's share sheet. Picking one opens that workflow's Run sheet with the remembered input filled in. Unpairing the phone forgets the remembered inputs and removes the shortcuts.

Shared text can contain instructions you didn't write, such as a web page telling an AI step to do something else. Read it in the Run sheet before you run: the workflow treats it as your input.

For a workflow that's ready for shared links, create the [Summarize a link](../starters/summarize-a-link.md) starter on the computer. It reads the page and sends a short summary back to the phone as a workflow message.

#### Share a file

*New in Raticode Mobile 0.36.0 and Raticode 0.6.0. A computer older than 0.6.0 doesn't list file inputs, so the phone asks you to update it.*

Share one file, such as a PDF, a photo or a spreadsheet, to **Run a workflow**:

1. The list shows the workflows with a [file input](../workflows/inputs.md#file-inputs) the file fits: one that accepts its kind, with a size limit it's under. Text the other app shared along with the file is left out.
2. Tap a workflow. If more than one of its file inputs fits, choose one; the phone remembers it, as it does for text.
3. The Run sheet shows the file's name in that input. A file input can only be filled from a share, so a workflow that needs a file says so when you open its Run sheet any other way.
4. Tap **Run**. Only then does the phone read the file and upload it to your computer, over the same encrypted connection and in the same way as [Share to Rem](rem.md#share-to-rem). The run request then goes through the usual schema check and [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone).

The computer checks the file against the input again, copies it into the run's folder, readable only by your account, and deletes the upload. If the upload doesn't finish, the workflow's card says why and you can tap **Run** again. A file can be at most the size the phone can send, 10 MB with an older computer, as for Rem.

### Resume and run again

When a run failed, **Resume from failed step** starts a new run on the computer. It reuses the steps that finished and runs the failed step again, then the steps after it. A stopped or interrupted run shows **Resume** and continues from where it stopped. The confirmation lists the steps that run again.

::: info New in 0.35.0
**Run again with the same inputs**, and resuming runs started with inputs or by a trigger, need Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the **Run again** button doesn't appear and runs started with inputs can't be resumed.
:::

**Run again with the same inputs** starts a new run of the workflow as it is now, with the inputs and trigger events the shown run used. The computer keeps those values in the run's folder; they never travel to the phone. See [Saved inputs and trigger events](../studio/runs.md#saved-inputs-and-trigger-events).

The computer decides whether a run can be resumed or run again. When it can't, the reason shows under the buttons. The computer doesn't save password inputs (`"format": "password"`), and the phone can't fill them in when it resumes or runs again, so for such a run the reason names those inputs: use **Resume** or **Run again** on the computer, where the app asks for them.

### Try another model

::: info New in 0.36.0
Try another model on the phone is new in Raticode Mobile 0.36.0 and needs Raticode 0.6.0 or later on the computer; with an older computer the button doesn't appear.
:::

**Tests · Try another model** on a workflow's page opens its [test set](../workflows/tests.md#try-another-model), the same action as the computer's **Tests** panel:

- Pick a step that calls a model, a provider, and a model. The computer's providers and their models appear as chips, and you can type any model name.
- **Check cost** shows the expected cost for the current model and the candidate first. **Run both** stays off until it has.
- Each case runs on the computer with the current model, then the candidate. If a finished result of the same version and test set already covers the cases, the current model's side reuses it and costs nothing. The workflow, its triggers and their approvals don't change, and judge checks keep their own model.
- A table then shows each case side by side: pass or fail, cost and time for the current model against the candidate, marked **Fixed**, **Broke** or **Changed** where they differ, with totals. The page refreshes while the try runs.
- **Use this model** asks first, then writes the step's `provider:` and `model:` through the computer's editor, like a graph edit. Every trigger on the workflow then asks for approval again. It's off while the try runs and once the workflow changed since it.

Adding or editing test cases stays on the computer.

### Ask Rem about a failed run

*New in Raticode Mobile 0.36.0; needs Raticode 0.6.0 or later on the computer.*

A failed or interrupted run shows **Ask Rem about this failure** on its card. The phone asks the computer for a short summary of the run, the same one the desktop attaches, and puts it in Rem's message box as a file. Nothing is sent: write your question and send it.

- The summary names the workflow file, the run, the node that ended it and its type, the provider app version a model step started (and whether it changed since the last passing run), the error, the end of that node's output (up to 200 lines, cut to 8 KB), preflight problems and the run's inputs. Password inputs show as a mask.
- Folder paths read as the project's name and "Raticode data", as elsewhere on the phone.
- Everything from the run is marked as untrusted, so Rem treats it as data, not instructions.
- With an older computer the button doesn't appear. If the computer can't read the run, the reason shows under the buttons.

See [Ask Rem about a failure](../studio/runs.md#ask-rem-about-a-failure).

### Triggers

::: info New in 0.35.0
Triggers on the phone need Raticode Mobile 0.35.0 and Raticode 0.5.5. With an older computer, the **Triggers** tab and the pause switch don't appear.
:::

A workflow's **Triggers** tab lists its [triggers](../workflows/triggers.md): each one's kind (schedule, file change or webhook) and what it watches, when it next runs, and how its last start went, such as **Succeeded**, **Failed** or **Skipped**, with the computer's reason. A webhook's address and token stay on the computer.

- Each trigger has its own switch. Turning one off happens at once.
- Turning one on asks first, because it then starts the workflow on your computer without asking each time it fires. Like everything the phone sends, it goes through the [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone), so a locked phone can't turn triggers back on. The computer refuses an unconfirmed request.
- Adding, changing and deleting triggers stays on the computer.
- A trigger with a [daily limit](../workflows/triggers.md#daily-limit) shows how much of it today has used, and once it is used up, that its starts are skipped until midnight. The **Pause all triggers** card shows the computer-wide limit the same way. When a limit is reached, the phone gets one Workflow message that day. Limits are set on the computer. This is new in Raticode 0.6.0 and Raticode Mobile 0.36.0.
- Each trigger shows its [Time limit](../workflows/triggers.md#time-limit), such as **Time limit: 6 h** or **No time limit**. It is set on the computer. This is new in Raticode 0.6.0 and Raticode Mobile 0.36.0; with an older computer the line doesn't appear.
- A triggered run that hit a provider's [usage limit](../workflows/triggers.md#usage-limits) shows as **Waiting for usage limit**, as the trigger's last result and in the workflow's runs, until the computer resumes it after the reset. Whether a trigger waits is set on the computer. This is new in Raticode 0.6.0 and Raticode Mobile 0.36.0.

#### Approve a changed workflow

*New in Raticode Mobile 0.36.0; needs Raticode 0.6.0 or later on the computer.*

A trigger runs only the workflow version you [approved](../workflows/triggers.md#approved-versions). When the workflow, or a prompt file, script or child workflow it runs, changed since then, the trigger's card says **Workflow changed since you approved it** and its starts are skipped. The phone also gets one Workflow message about it; tapping it opens the workflow.

- **Review changes** lists the changed files. Tap one to see its diff, in the same view as Source control.
- **Approve this version** approves exactly what the list showed. If the workflow changed again since, the computer refuses it and the trigger keeps waiting; open **Review changes** again.
- The phone shows up to 60,000 characters of diff. When a file's diff was cut, or the file is binary or too large to show, **Approve this version** asks a second time and names those files. **Open** shows the whole file in the file viewer, when [Files](#files) is available on the phone. **Approve without seeing all** approves anyway.
- Like everything the phone sends, approving goes through the [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone) first, so a locked phone can't approve.
- Turning on a trigger whose workflow changed approves nothing. The phone says so before you confirm, then opens **Review changes**.
- Both apps need the update. With an older computer, triggers show no approval state, and an older phone can't show **Review changes**: approve on the computer or with `Raticode rattish triggers approve`.

**Pause all triggers** at the top of **More › Workflows** pauses every trigger on the computer, the same switch as in its Workflow runs view. Pausing happens at once; resuming asks first and says how many triggers come back on. Each trigger keeps its own switch, so resuming restores exactly the ones that were on. While paused, the computer records scheduled times as skipped and doesn't catch them up. See [Pause all triggers](../workflows/triggers.md#pause-all-triggers).

## Approvals and swarms

With **Allow approvals and swarm controls** on:

- **Approvals** answers workflow and swarm approvals. A workflow approval with [options](../nodes/approval-gate.md#choices-and-typed-replies) shows one button per option instead of Approve and Reject, and asks for a reply when the workflow wants one. *New in Raticode Mobile 0.36.0, with Raticode 0.6.0 on the computer.* Older app versions don't list approvals with options or a required reply; answer those on the computer.
- **Swarms** shows each [swarm's](../advanced/swarms.md) status as a colored dot with its progress, each agent's state and the board, with pause, resume, stop and messages.
- Long board posts and approval summaries show a few lines until you tap **Show more**.

With **Use Raticode from this phone** you can also start swarms, including ones whose agents have full access, and start organizations or release a held worker that runs without a sandbox. Starting there counts as your confirmation, as it does at the computer.

## Social posts

*New in Raticode Mobile 0.36.0; needs Raticode 0.6.0 or later on the computer. [Posts](../advanced/posts.md) is experimental and untested, and may change: a Feature Preview on the computer, and marked **Experimental** in More and at the top of the phone's Posts page.*

**More › Posts** shows the computer's [Posts](../advanced/posts.md) queue, grouped like its lanes: **Needs your review**, **Due now**, **Needs attention**, **Scheduled** and **Done**. With an older computer the page asks you to update it.

Each post shows who proposed it, when it goes out and to which accounts, its warnings, and notes such as another post going to the same account an hour earlier. Each account's text appears as it will be posted, with a marker where LinkedIn, Facebook or YouTube cut it with **see more**, the character count against the platform's limit, and X's cost. **Why Rem proposed this** shows the reason and the pages it drew on.

- **Approve**, **Post now**, **Ask for changes**, **Decline** and **Cancel post** each ask once more and say what they do. Asking for changes needs a note, which the proposer reads when it revises the post.
- An approval covers the exact version the phone showed. If the post changed on the computer in the meantime, the computer refuses the approval and the list refreshes, so you see the new version first.
- The phone shows the first 4,000 characters of each text and can't show images, videos or a post's options, such as a video title or a LinkedIn link preview title. When a post has more than the phone shows, approving says so and asks you to check it on the computer first.
- **Publish now** sends a due or stuck account's post, such as a [LinkedIn tap to publish](../advanced/posts.md#linkedin-tap-to-publish). For Patreon, Buy Me a Coffee and TikTok, **Open** goes to the site's post page and **Mark posted** takes the link to the post you made.
- **Publishing** at the top pauses or resumes all publishing, like **Pause all publishing** on the computer. Resuming asks first.

The phone has your trust here, as at the computer, so a tap is your approval. Rem, tasks and workflows still can't approve: they reach Posts only through their propose-only tools. Like everything the phone sends, decisions go through the [suspicious-request check](pairing.md#suspicious-requests-lock-the-phone), and the post's activity log records them as yours, from the phone. The phone can't write or edit posts, connect accounts or change Posts settings; account logins never travel to it.

## Desktop settings, Usage and the Developer log

- **Desktop settings** changes the computer's appearance, Rem defaults, editor, terminal and web browser settings. **Rem › Remove Co-authored-by lines from commits** saves on the computer straight away, even when the Raticode window is closed. See [Remove Co-authored-by lines](../studio/source-control.md#remove-co-authored-by-lines).
- **Usage** shows provider usage, as in [Usage and allowances](../rem/usage.md). At the top, one card per provider account shows its windows with a reset countdown, its sign-in state (for the default Claude Code sign-in and each Claude Code, Codex or Grok account), when it was last checked and, when Rem chooses Claude Code, Codex or Grok accounts by usage, which one Rem uses next; see [Accounts at a glance](../rem/usage.md#accounts-at-a-glance). A Codex or Grok account that needs sign-in has **Sign in** on its card, which [signs it in from the phone](../rem/providers.md#sign-an-account-in-from-the-phone). *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.* A card with a weekly window also charts this week's readings against an even pace and says whether the account lasts until the reset; see [Weekly pace](../rem/usage.md#weekly-pace). Under **Tokens in Raticode**, pick 24h, 7d, 30d, 3mo, 6mo, 1Y, 5Y, All or Custom; Custom opens a date range picker. The phone remembers the choice. The stacked chart shows each provider's tokens per bucket, in the same [provider colors](../rem/usage.md#provider-colors) as the desktop; tap a bar, or use **Earlier** and **Later**, to read its tokens and each provider's share. The **By provider** and **By account** chips above the chart split it [by account](../rem/usage.md#by-provider-or-by-account) instead, each account in a shade of its provider's color with its own pattern and named like **Claude Code · work**. The phone remembers this choice too; a computer without the account split keeps the chart by provider. A computer still on 0.5.5 shows its last 30 days only and says so. Below the chart, **Cost at API prices** shows the period's [estimated cost at API list prices](../rem/usage.md#cost-at-api-prices) and each provider's share. It appears only when the computer's Raticode has the estimate. Under it, **Verified work** shows the same numbers as the desktop's [Verified work](../rem/usage.md#verified-work) for the chosen period: how many accepted tickets passed review first time, repairs per ticket, blocked tickets and why, project check failures on the first try, flaky check runs, tokens per accepted ticket and how often you stepped in. A self-work report in Rem's chat shows its one line, such as **3 of 4 tickets passed review first time.** Both appear only when the computer's Raticode has them. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Usage › See computers** lists each computer Raticode can run work on: the computer you're connected to and each desktop paired with it. Each one shows its state, running workers with what kinds they are (such as *4: 2 organization workers, 1 Rem task, 1 swarm turn*; a computer on an older Raticode shows only the number), worker limit, room for new workers, and CPU and memory pressure; [Computers and their load](../rem/home.md#computers-and-their-load) explains the numbers. Use − and + to change the connected computer's **Worker limit**, where Auto picks a number from its cores and memory; the change is saved when you stop tapping. A computer on an older Raticode says to update it instead. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Usage › See computers › Where work runs** shows the strategy and the computers it runs on for Rem and each organization, and lets you change the strategy: **Fill one computer, then the next**, **Spread evenly** or **Only computers I pick**. With **Only computers I pick**, tick the computers and mark any that another placement may also use as **Shared**; nothing is sent until at least one computer is ticked. If the computer refuses a change, the reason shows under that row. A placement whose work can't start anywhere, such as one with only paired desktops picked, shows the computer's warning under it. The order of computers is set on the computer, in **Settings › Computers**. The tab shows only when the connected computer's Raticode has it. [Where work runs](../rem/home.md#where-work-runs) explains the rules. *New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*
- **Developer log** follows the computer's application log. Long-press an entry to copy it or **Ask Rem** about it.

## Browser

**Browser** runs over the phone's own internet connection, not through your computer. It has two built-in browsers, both inside Raticode, with tabs, bookmarks, history, find in page, and Raticode's bar in reach:

| Browser | Built on | Extras |
| --- | --- | --- |
| **Raticode (chromium)** | Android's WebView | — |
| **Raticode (gecko)** | Mozilla's engine | Profiles, tracking protection and Firefox extensions under **Extensions**. **Import from Firefox** brings over extensions, settings and bookmarks (not passwords or your Mozilla account). |

In Raticode (gecko), **Profiles** in the menu lists your profiles. Each has its own tabs, favorites, history, sign-ins and site data. **Delete** on a profile asks first: **Delete profile** removes all of that from the phone for good, and **Keep** leaves it. The built-in **Personal** profile can't be deleted.

### Tabs and the default browser

<AppScreenshot name="phone-browser-tabs" phone caption="One tab list for both built-in browsers, with your computer's open tabs below." />

- The **Tabs** button lists the tabs of both built-in browsers together, each marked **chromium** or **gecko**. Tap any tab to switch. The other browser's tabs stay open, and music in one keeps playing while you use the other.
- **New tabs open in**, at the top of the list, picks the browser for a new tab, a private tab, a reopened tab or one of your computer's tabs. It starts at your default.
- **Browser settings and privacy › Default browser** sets the default. Raticode (gecko)'s settings have the same choice. A choice saved by an earlier version carries over: **Raticode's browser** is now **Raticode (chromium)** and **Gecko** is now **Raticode (gecko)**.

### Make Raticode the phone's default browser

*New in Raticode Mobile 0.36.0.* Raticode can be the browser Android uses for links you tap in other apps. Open **More › Open with Raticode** and choose **Make Raticode the default browser**. Android asks you to confirm in its own dialog, or opens its default-apps settings where you pick Raticode. Raticode never changes the default by itself, and you can switch back in Android's settings.

**Links from other apps open in**, on the same page, picks **Raticode (chromium)**, the default, or **Raticode (gecko)**. Each link opens in a new tab and the Browser page comes to the front. It works before the phone is paired with a computer too. As in Raticode (chromium), `http` links are opened as `https`.

- While the [child lock](locks.md) is on, a link doesn't open; Raticode says so.
- With app lock on, the link opens after you unlock Raticode.
- A link's tab is like one you opened yourself. Rem sees its page only when you send it with **Ask Rem**.

**Firefox**, **Chrome** or **Edge** as the default open pages in their own apps, with their own sign-ins and data, which Raticode never sees. Android doesn't let one app draw inside another, so Raticode can't list or switch their tabs. **Open this page in**, in the tab list, sends the current page to one of them. **Use Raticode (chromium) instead** and **Use Raticode (gecko), with Firefox extensions** switch back.

### Firefox extensions

In Raticode (gecko), **Extensions** in the menu searches Firefox Add-ons and lists what you've installed. Each extension shows what it can access before it's added, and only extensions Mozilla has signed install.

- **Open** shows the extension's popup over the page you're viewing, the same window its toolbar button opens in Firefox. On a new tab's start page the popup opens too, with no page behind it.
- An extension with no popup but with a settings page opens that page in a new tab. If it has both, **Settings** opens the settings page.
- An extension whose button has no window of its own does its work on the page you're viewing, and Raticode says so.
- **Nothing to open** marks an extension that has neither, such as one that works on every page by itself.
- Extensions don't run in private tabs. **Open** in a private tab says so and opens nothing. Switch to a regular tab first.
- The switch turns an extension off without removing it. **Remove** asks first, then **Remove extension** uninstalls it with its settings and data, and **Keep** leaves it. Profiles share the same extensions.

### Privacy and downloads

- **New private tab** is in both built-in browsers. Private tabs keep no history, and their cookies and site data are removed when the last private tab closes.
- Only secure (https) pages open, and certificate errors can't be skipped.
- Sites ask each time before using the camera or microphone.
- Downloads ask first, including files a page builds itself, such as a chat you export from Open WebUI (up to 25 MB in Raticode (chromium)).
- Only the tab on screen can ask a question, such as a download, a sign-in or another app. A new question never replaces one that's open; Raticode says **Answer the open question first, then try again**.
- In Raticode (gecko), at most three questions wait at once; more are answered **No**.
- Download names lose invisible characters, so a name can't hide its real extension.

### Your computer's tabs

With **Use Raticode from this phone**, a new tab also lists your computer's open browser tabs, each named with the browser it runs in there (Raticode (chromium), Raticode (gecko), Firefox, Chrome or Edge). **Open on your computer** opens the page in a background tab of the computer's [Browser plugin](../studio/browser.md).

### Tabs that stop while you're away

Android ends a browser's page processes to free memory while you use other apps or the screen is off. A page can also crash or get stuck. In Raticode (chromium) and Raticode (gecko) you don't get a "stopped working" message for this:

- The tab reloads by itself when you come back to it, at the same address. Tabs you aren't looking at reload when you open them.
- Raticode (chromium) tabs keep their Back and Forward history. Raticode (gecko) tabs keep it too, except private tabs, which reopen at their page.
- A stuck Raticode (chromium) page you're looking at gets about ten seconds, then reloads. Raticode (gecko) stops stuck scripts on its own.
- A page that was playing stops when its process ends; press play again after it reloads. Downloads keep going, since Android's download manager runs them.
- A page that stops again right after reloading gets three automatic reloads within two minutes, each a little later than the last. After that the tab shows **This page keeps stopping** with a **Reload** button.
- Known limit: a private Raticode (gecko) tab sometimes stays blank after Android ends its page. It stays private and keeps its address; close it and open the page again.

### Full-screen video

Tap a video's full-screen button in Raticode (chromium) or Raticode (gecko) and the video takes the whole screen. Raticode's bottom bar, the browser controls, the media bar and Android's status and navigation bars go away, and the screen turns to landscape even with auto-rotate off.

- With auto-rotate on, turn the phone over to flip the picture. A portrait video, such as a clip filmed on a phone, stays upright when the page reports its size.
- Swipe in from an edge to see Android's bars for a moment.
- **Back**, or the player's own exit button, leaves full screen. The page doesn't reload, the video keeps playing, and the screen follows how you hold the phone again.
- Switching to another tab or Raticode page, for example from a link or a notification, also leaves full screen.
- If you leave Raticode during full screen, it's still full screen when you come back.
- With [App lock](locks.md#app-lock) on, the lock screen shows upright with Android's bars, and the video returns to full screen once you unlock. The [Child lock](locks.md#child-lock) keeps the video full screen, with its handle on top.
- Firefox, Chrome and Edge handle full-screen video in their own apps.

### Media controls

While a page in Raticode (chromium) or Raticode (gecko) plays sound, a media bar above Raticode's pages shows the title, a picture when the page has one, and **Previous**, **Play/Pause** and **Next**. Sound keeps playing after you leave Raticode. The same controls appear on the lock screen, in the notification shade and for headsets, cars and watches, with **Stop** to end every page's sound.

- The media bar hides while the **Browser** page shows the playing tab; the lock-screen and notification controls stay. On another tab or page it comes back. If another tab is also playing, it shows that one.
- Only the buttons the page offers right now appear. Pages that don't name their track show the page title or the site's name.
- After a pause the controls stay for 30 seconds, so **Play** still works from the lock screen (5 seconds when the page can't resume). Closing the tab removes them at once.
- A private tab shows only **Private tab** on the lock screen and in the shade, with no title, site or picture.
- **Show what's playing on the lock screen**, in either browser's settings, is on by default. Off, the lock screen shows only **Playing in Raticode's browser** until you unlock. With [App lock](locks.md#app-lock) on, the lock screen always hides it.
- While the [Child lock](locks.md#child-lock) is on, controls outside Raticode do nothing and say so; the sound keeps playing.
- On Android 13 and newer the controls appear even with Raticode's notifications off, as Android allows for media. Android only lets Raticode start them while it's on screen, so a page that starts playing after you've left plays without them.
