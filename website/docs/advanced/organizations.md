---
title: Organizations
description: Configure persistent agent organizations, supervised task queues, budgets, routines, and review.
pageClass: docs-layout
---

# Organizations

An organization runs long-term work with agent employees (shown as workers), projects, goals and an explicit review process.

::: warning Experimental
Organizations is off by default. Turn it on in **Settings > Developer > Feature Preview**; Raticode restarts its backend to apply it. Turning it off hides the feature and keeps its data. A backend started without the desktop app reads `RATICODE_EXPERIMENTAL_ORGANIZATIONS=1` instead. Try supervised tasks before relying on unattended work.
:::

## Find your way around

Open **Organizations** in the sidebar. You don't need a project selected. Each organization has five tabs:

| Tab | Holds |
| --- | --- |
| **Organization** | Mission and counts, milestones, what needs you, recent activity, projects, work schedule and working hours |
| **Projects** | Owned project folders with their milestones and epics |
| **Issues** | The ticket board. Tickets have keys such as `RAT-12` and belong to epics. |
| **Workers** | The org chart, with roles and status. Drag to pan; Ctrl+scroll or Ctrl+plus/minus zooms. |
| **Knowledge** | Shared notes and decisions, with evidence-checked claims |

<AppScreenshot name="organizations" caption="Workers shows who reports to whom and what each worker is doing. This demo organization is paused, so everyone is idle." />

- Opening a ticket, project, worker or note fills the main area. The back button at the top left, or Escape, returns to the list.
- The header has **Start** or **Pause**, **Find anything** (Ctrl+K), a working-hours pill, the current work schedule block, and a gear for **Settings, history and operations**.
- On the phone, the same five sections are under the **Orgs** tab.

## Set up an organization

Create the organization, then add employees, reporting lines, teams, goals, initiatives and owned projects. A project folder can belong to only one organization.

Each employee has:

- a persona, responsibilities, a manager and instructions
- provider, model and effort
- a permission mode, workspace allowlists and resources
- budgets and run limits

Give each employee a narrow role and review these settings before starting work.

| Worker setting | What it does |
| --- | --- |
| **Limit ticket actions by project** | Per project, whether the worker may create, edit, move or cancel tickets |
| **Check-ins** | How often an idle worker checks in (hourly by default) |
| **Hold this worker** | Stops one worker without pausing the organization |

**Working hours**, set from the header pill, are **Around the clock (24/7)** or weekly windows in one time zone. Workers start and keep working only inside them. **At closing time** chooses what happens to running work:

- **Stop running work.** It goes to **Blocked**, waiting for working hours, then back to **To do** by itself when they open, ahead of other work.
- **Let running work finish.** Nothing new starts.

## Workers run like small Rems

Each worker looks after its own tickets in a loop, like Rem's [self-work](../rem/board.md#self-work), inside the projects you gave it. Choose a worker's projects in the board's **People** view; only you can change them.

While the organization runs and working hours are open, a worker with projects:

1. **Checks its Blocked tickets first**, once per blocked spell. It restarts what it can and says what changed. A ticket that needs you stays Blocked, with a comment saying what's needed.
2. **Works its To do tickets** by priority. Tickets that fit the current [work schedule](#work-schedule) block move up a level.
3. **Pulls accepted Backlog tickets** when it has nothing else it can start. See [Pull from Backlog](#pull-from-backlog).
4. **Checks in when idle.** It plans follow-ups in To do and proposes new work for the current block as **Backlog** tickets. Proposals stay in Backlog until you accept them; tickets that need approval wait for it.

Check-ins:

- come hourly unless the worker's **Check-ins** setting says otherwise
- double their wait after each check-in in a row that creates nothing, up to eight times; real work resets this
- happen only during working hours, and count against run, token and dollar limits like any run

### Where a worker may work

A worker with projects works only there: on tickets you give it, tickets it creates, threads it opens and tickets it comments on (apart from its own).

- A ticket you move to a project its worker doesn't have goes to **Blocked**, saying to assign the worker or give the ticket to someone who is.
- A worker with no projects does no self-work.
- Workers manage tickets assigned to them. A manager can also manage tickets of people who report to them.
- A worker can hand a ticket to someone in the same organization who works in that project, but must say why. The reason is saved as a comment. Tickets can't move to another organization.

### What workers can't do

Workers can't create, change, start or pause organizations, swarms, workers, schedules or limits. Their organization tool offers only ticket, comment, review, knowledge and evidence actions, and Raticode refuses anything else. Their swarm and fleet tools only read. Configuration stays with you, and with Rem through [approval requests](#use-rem).

::: tip Organizations made before workers had projects
Nothing is reset. Workers you never gave projects keep working on tickets you give them anywhere in the organization's folders, as before, but they don't self-work until you assign projects. Once you do, they stay inside them.
:::

## Pull from Backlog

A worker with nothing it can start pulls one **Backlog** ticket into **To do**, and it runs from there like any other ticket. This is on for every organization unless you turn it off: **Backlog pulls** on the organization page, or **Workers pull accepted Backlog tickets** in Settings.

A worker pulls a ticket only when all of these hold:

- the organization is running, inside working hours, and under its run, token and dollar limits
- the worker isn't held or running another ticket, has no To do ticket it can start, and has no Blocked tickets waiting for its look
- the organization has a free run slot under **Maximum concurrent employees**; each worker gets one ticket per pass
- the ticket is assigned to that worker, or has no assignee and is in one of the worker's projects (a worker needs projects, and its ticket permissions must let it move and edit tickets there)
- every ticket it depends on is **Done**, and its epic or milestone isn't cancelled
- the ticket is under its run limit and not waiting for a start date

These tickets always stay in Backlog:

| Ticket | Why it stays |
|---|---|
| A worker's proposal | Workers never pull their own ideas. Move it to **To do** to accept it. |
| One that needs plan approval | It waits until you approve the plan. |
| One with a request waiting for you | It waits for your decision on that request. |
| One someone moved back to Backlog | Moving it back is a choice, so workers leave it until someone moves it out again. |

When several tickets qualify, higher priority goes first, then tickets that fit the current [work schedule](#work-schedule) block, then the oldest. A ticket goes to its own worker before anyone takes it unassigned.

Each pull adds a **Pulled into To do** entry to the ticket's timeline, naming the worker and why, and one line to the organization's activity. A Backlog ticket's details say whether a worker will pull it, or what it waits for.

Rem can turn pulling off, but turning it on from Rem needs your [approval](#use-rem).

::: tip Staging work you don't want started yet
Set **Require approval before work starts** on the ticket, or turn Backlog pulls off. Dependencies already hold a ticket until the tickets it waits for are done.
:::

## Work schedule

Working hours say *when* workers may work. The work schedule says *what* they should favor while they do.

It's a list of named blocks, each with a focus and optional keywords. Set it from **Work schedule** on the Organization tab, or from the block pill in the header. <AppScreenshot name="org-work-schedule" caption="A work schedule filled from the suggested one. The block on now also shows next to the organization's name in the header." />

Each block repeats on one cadence:

| Cadence | Repeats | Example |
| --- | --- | --- |
| Daily | Every day | 08:00–17:00 New feature work |
| Weekly | On the weekdays you pick | Fridays 13:00–17:00 Weekly release |
| Monthly | On a day of the month, or the 1st to 4th or last of a weekday | 1st Monday 09:00–12:00 Dependency audit |
| Quarterly | Like monthly, in the 1st, 2nd or 3rd month of each quarter | 1st Tuesday, 09:00–17:00 Security review |

### Times and dates

- An end time at or before the start runs past midnight: 17:00–00:00 and 22:00–06:00 both work.
- The same start and end makes a whole day.
- Day 29, 30 or 31 lands on the last day of shorter months, so day 31 means "the last day".
- Quarters start in January, April, July and October.
- The schedule uses the organization's time zone, the same as working hours. Blocks keep their clock times through daylight saving changes, so on those days a block can be an hour shorter or longer. A skipped start time moves an hour later; a time that happens twice uses the first.

### When blocks overlap

1. The rarer cadence wins: quarterly, then monthly, then weekly, then daily. A Friday release beats daily feature work on Friday afternoons.
2. With the same cadence, the shorter block wins. A one-hour nightly release at 00:00 beats gardening from 00:00 to 08:00.
3. Two overlapping blocks with the same cadence and length can't be saved, because neither would win.

The editor lists times during working hours that no daily or weekly block covers, and blocks that fall outside working hours. In those gaps workers use their usual priorities.

### What workers do with it

- Every run is told the current block, when it ends and what comes next.
- Tickets whose title, description, epic or project mention one of the block's keywords move up one priority level, but never past a ticket two levels above them.
- Check-ins propose work that fits the block's focus.

**When a block changes**, running work keeps going under the block it started in; the Organization tab shows which. The next ticket and check-in follow the new block. Near a block's end, workers are told to wrap up and leave a handoff comment instead of starting something large. Saving a new schedule doesn't stop running work either. Only working hours stop work, at closing time.

**Start from the suggested schedule** fills the editor with:

- New feature work, 08:00–17:00
- Application hardening, 17:00–00:00
- Nightly release, 00:00–01:00
- Process improvement and gardening, 00:00–08:00
- A Friday afternoon weekly release
- A monthly dependency audit
- A quarterly security review

Edit or delete any of them, then save. Raticode never applies it by itself, and an organization has no schedule until you save one.

## Shared repository and knowledge

**Shared repository** on the Organization tab is off until you choose **Turn on**. It gives the organization its own Git repository in Raticode's data folder, outside every project, for what the whole organization shares: knowledge entries as Markdown, decisions, notes and other files.

- **Turn off** stops using it; its folder and history stay on disk.
- A run works in its own worktree of it on a `shared/` branch. Accepting the ticket merges that branch into `main`.
- The repository has no remote, and `git push` is blocked there.
- A ticket lists the shared files its run changed.

Claims on tickets and knowledge entries carry evidence that Raticode checks itself:

| Evidence | Check |
| --- | --- |
| Quote | Must appear in the cited entry |
| File | Fingerprinted |
| Commit | Must exist |
| Check command | Rerun only if it's one of the project's configured checks or you run it |

Changed evidence marks the claim stale.

A project's **Checks** (a name and a command, such as `npm test`) are the commands Raticode may run to check claims. Until you turn on **Run them after every run without asking**, you start them from the ticket. Turn that on only for projects whose workers you trust to run code.

A check command that isn't one of the project's checks was written by the worker. Its claim says so and shows the whole command, every line of it. Characters that would change how the command looks without showing up themselves, such as direction marks or zero-width spaces, are spelled out as `⟨U+202E⟩` with a warning. **Run this check** then asks you to read the command and confirm with **Run the worker's command**. It runs in the ticket's worktree as you, with your access.

## Where employees work

By default each task runs in its own Git worktree on an `org/<task>` branch, made from the project's current commit.

- Employees never edit your project folder, so several can work at once and your uncommitted changes are safe.
- The task shows its branch and worktree.
- When the task is done or cancelled, Raticode commits anything left in the worktree, removes it, and keeps the branch for you to review and merge in [Source control](../studio/source-control.md).
- Folders that aren't Git repositories run in place.

**Settings > Where employees work > Directly in the project folder** turns worktrees off. Employees then take turns in the folder, and a run starts only when it has no uncommitted changes.

`git push` fails inside employee runs unless you clear **Block git push in employee runs**. This guards against mistakes; an employee with unrestricted shell access could get around it.

## Tasks and independent review

**Operations** holds the work queue, task filters, activity, routines, execution destinations and recovery controls. A task has an assignee, priority, dependencies, plan, evidence and review state.

- Approving a plan and accepting a result are separate decisions.
- An independent reviewer can accept, reject or request revision.
- A finished dependency can wake waiting work.
- A cancelled task stays cancelled until someone reopens it.
- The queue says why a task is waiting: employee holds, unmet dependencies, review, workspace contention or budget limits. Blocked tasks say why and what to do next.

### Start and Pause

**Start** (in the header; **Start work** in settings) first shows what will run, and nothing starts until you confirm:

- tasks that can start, and backlog tasks that won't
- plans still waiting for approval
- employees and their permission modes
- the monthly limits

Approve a plan from the task itself, where its description is visible. Start never runs **Backlog** tickets directly, but once the organization runs, free workers [pull accepted ones into To do](#pull-from-backlog) unless you turn that off. Start keeps individual employee holds; **Resume all employees** is a separate action.

**Pause** stops running tasks and moves them to **Blocked**, waiting for the next Start, which puts them back in To do. The interrupted run doesn't count against the task's run limit. Delegated work (swarms, paired desktops, HTTPS gateways) stays blocked for review instead.

### See what a run did

Under **Runs and artifacts** on a task, open a run to read what the employee did, or choose **Show input** to see exactly what it was given: instructions, task and conversation, tools and working folder.

- Inputs are stored compressed, readable only by you, and checked against the run's fingerprint.
- Each run records the provider, model, effort and permission mode, whether it ran in its own worktree, and a fingerprint of everything it was given.
- Worktree runs also record the commit they ended at and whether they left uncommitted changes.

## Audit trail

Each status change of a ticket (to Blocked, back to work, to Review, to Done, or cancelled) adds a short entry to its conversation saying why. The [global board](../rem/board.md#history-and-comments) shows these as the ticket's history.

- When the reason is already a comment just above, such as the worker's report or your review, the entry only names the move instead of repeating that text. The ticket's **Conversation** shows these moves as one-line entries, on the computer and on your phone, and its count is the number of entries it shows.
- The same comment written twice by the same person within two minutes, such as a retried request, shows once. Tickets from before this change show the same way, and nothing stored is edited or removed.

- Activity is append-only. Entries and task conversations can't be edited or deleted.
- Each entry is linked to the one before it by a hash.
- Every 30 seconds, and after each successful check, Raticode appends the latest link to `organization-audit-anchors.jsonl` in its data folder, outside the database. This catches entries removed from the end of the log, or a rebuilt database, unless the anchor file was changed too.
- **Configuration history > Verify audit trail** checks the configuration history, the activity chain and the anchors, and reports the first entry that doesn't match.
- Entries written before an upgrade added linking are counted but can't be checked.
- Every change to work is recorded, including the scheduler's own, such as blocking a task that reached its run limit.
- Retry keys for repeated requests are kept for 30 days.

## Routines and evidence

**Routines** run on cron with a time zone, on an interval, or from authenticated triggers.

- Overlap: skip or queue. Missed occurrences: skip or coalesce.
- A routine can queue work while the organization is paused; it still needs Start to run.

**Source-linked memory** records a file hash, author, verification time and optional expiry. Changed or expired evidence becomes stale, drops out of the verified-memory context and queues a recheck, so an old conclusion never passes as verified against new source.

**Rehearsal** estimates queue order from copied state and a clock you supply. It calls no provider and doesn't predict review decisions, outside effects or dollar costs.

## Budgets and destinations

Monthly limits can be set for the organization, each employee and each initiative:

| Limit | Default |
| --- | --- |
| Runs per month | 200 for new organizations; 0 means unlimited |
| Tokens per month | None |
| Dollars per month | None |
| **Tasks one run can create** | 5 |
| **Delegation depth** | 3 levels of nested tasks |

- Each task also has its own run limit.
- Codex and several other providers don't report dollar cost, so a dollar budget pauses their work after the first run. Use a token or run limit for them.
- A run already in progress can go past what's left.
- Operations shows how many employees can actually work at once, and why.
- Native provider sub-agents and unrestricted shell processes aren't covered by the organization's accounting.

**Managed destinations** can be local workflows, swarms, paired desktops, or an HTTPS job gateway you explicitly trust. Workflow validation previews inputs, resources, source and IR hashes, and completion checks. Launch authorization binds that reviewed contract. Editing a task plan clears its authorization; previewing doesn't run it.

## Recovery and history

- After Raticode restarts, an organization pauses until you start it, and the pause reason says so. With **Keep running after Raticode restarts** (in the working hours editor) it carries on by itself, and work that was mid-run is flagged for you to reopen.
- Interrupted or uncertain outside work keeps its receipt and reservation; it's never silently replayed. Use **Check stopped executions**, or **Confirm stopped after inspection** with evidence when automatic confirmation isn't possible.
- **Configuration history** previews a restore against the current revision. Restoring configuration doesn't undo file changes, spending or task history.
- Permission changes revoke affected claims. Changing owned workspaces pauses the organization.
- **Archive organization** (in Settings, while paused) hides it and frees its project folders for other organizations. History and work records stay; **Restore organization** reclaims the folders.
- **Export template** creates a reusable company definition, not a backup.
- An operational backup is a consistent SQLite snapshot without credential values from the system keyring. Restore it with the app closed and keep the original database for rollback.

## Use Rem

When Organizations is on, Rem's organization tools work in global and project threads. Ask Rem to inspect an organization before changing it, and say which configuration or task to change. Read-only and plan sessions can't change organizations, and running a workflow still needs your authorization.

Rem can't start an organization by itself. When Rem asks for any of these, the Organizations tab shows a request and nothing changes until you approve it:

- start the organization, resume all employees, or archive it
- approve a plan, remove a task's approval or review step, accept a result that needs independent review, authorize a workflow, or confirm that interrupted outside work has stopped
- turn on a routine or webhook, or make an enabled routine run more often
- re-enable or repoint a revoked credential
- add or resume employees, give a permission mode that can write, or give employees more tools, skills, folders or credentials; add project folders or execution destinations
- let a worker do more with tickets, or change who a worker reports to
- raise or remove a run, token or dollar limit, raise an operating limit, make heartbeats more frequent, or widen working hours
- allow `git push`, or let employees edit the project folder directly
- turn on the shared repository, keep the organization running after restarts, or write organization knowledge into the Second Brain folder
- add claim-check commands, or run project checks automatically after every run

Requests:

- expire after a day; asking twice returns the same request, applied at most once
- are refused right away if they'd fail anyway, such as claiming a folder another organization owns

### Approve several requests at once

Each request has its own **Approve** and **Decline**. When two or more wait, **Needs you** on the **Organization** section (and the banner in Settings) also shows **Approve all**. The sidebar shows how many requests wait in each organization.

1. **Approve all N** opens the exact list it will approve, in the order it applies them. Nothing changes yet.
2. **Approve these N** approves only those requests. One that arrives after the list opened isn't included; the list says so, and it keeps waiting. **Cancel** or **Esc** closes the list.
3. The result lists each request as **Approved**, **Failed** (tried and refused, then closed, as with its own Approve) or **Left waiting** (not tried).

The order keeps one approval from breaking the next:

| Order | Requests | Why |
|---|---|---|
| 1 | Configuration changes, restores and **Resume all employees** | Oldest organization revision first. Each was made for the revision Rem saw. |
| 2 | Ticket and other changes | In the order Rem asked. |
| 3 | Starting the organization, then archiving | Last, so work starts on the settings you just approved. |

Raticode reads the organization again before each request:

- **A change made for an older revision** is left waiting instead of undoing newer changes. This happens when two configuration changes expect the same revision: only the first applies. Decline the other and ask Rem again.
- **A ticket that changed after Rem asked** is left waiting the same way.
- **Starting the organization** is left waiting if anything else in the list didn't apply. Check the organization, then approve the start by itself.

Only your own click approves here. Rem, workers and agent tokens can't use Approve all, the same as with each request's **Approve**. There are two exceptions, both described below: [telling Rem to approve](#tell-rem-to-approve), and [Auto mode](#auto-mode).

### Tell Rem to approve

Your word in Rem's chat counts as an approval. Say something like "approve those", "approve the start request for the Desk" or "approve everything waiting in the Desk", on the computer or the phone, and Rem approves exactly what you named, in any organization, with Auto mode on or off.

There are no magic words. Rem judges whether you're approving, and your okay can be implied when the conversation makes it painfully obvious. For example, "sure, go for it" right after Rem asks whether to approve the start request counts. When it's only possible or unclear, Rem asks instead. Rem notes in a few words why it took your message as an okay, and that note is kept with each approval.

- Rem uses Approve all's order and checks: configuration changes first, starting last, re-reading the organization before each. Its reply lists each request as **Approved**, **Failed** or **Left waiting**, including dangerous ones you told it to approve.
- "Approve all of them" covers the requests that were waiting when you sent the message. One that arrives later keeps waiting.
- Each request records **Rem, on your instruction** and the ID of your message, and the activity log shows the same.
- Only a message you typed or spoke counts. Rem won't approve because a worker, a ticket, a file, a task result, a workflow or its own proposal says so. Workers and agent tokens still can't approve.

## Auto mode

::: warning Off by default
Auto mode lets Rem decide this organization's requests without you. Turn it on only for organizations whose charter and limits you trust Rem to follow.
:::

With Auto mode on, what would wait for you goes to a Rem review step instead:

- Rem's own requests (the list in [Use Rem](#use-rem))
- plans and worker proposals waiting for approval
- finished tickets waiting for your review, when no worker is their reviewer

Rem reads each one with the organization's mission and instructions, what it changes, and the revision it expects, then decides:

| Decision | What happens |
|---|---|
| **Approved** | It applies as asked. |
| **Approved with changes** | It applies a safer version, for example a sandboxed permission instead of full access, or a ticket rewritten without the risky part. |
| **Declined** | It's closed with Rem's reason and, when there is one, a safer change Rem recommends. A declined ticket review goes back to its worker with the reason. |
| **Stopped for you** | The organization pauses. See below. |
| **Left for you** | It waits for you, and the organization keeps working. |

Something dangerous doesn't stop the organization by itself. Rem first declines it or makes it safe. Raticode checks each decision before applying it: a dangerous request can't be approved as asked, and a changed version must no longer be dangerous. When a ticket or request mentions live trading, a brokerage, real money, deploying, publishing, deletion or spending, Rem can approve it only by saying why none of that happens (for example, the ticket forbids live orders); that explanation is kept with the decision. Otherwise it declines or changes it. Dangerous means live trading, real money or a brokerage or trading API; running workers without a sandbox or with full access; adding or changing credentials; `git push`, publishing or deploying; irreversible deletion; more project folders or access; or spending beyond the budget.

The organization stops only when:

- something is dangerous and Rem can't make it safe without stopping everything, or
- it can't go on without you: a missing credential, hardware that isn't available, no internet access, or a sign-in only you can complete. Rem failing to answer three times in a row (for example, its model is signed out) counts too.

An ordinary or merely uncertain request never stops it; Rem decides it or recommends a change. When it stops, the organization pauses with the reason and what you need to do, the request waits in **Needs you**, and the organization's page and sidebar show **Auto · stopped**. Approve or decline that request, or start the organization again, to lift the stop. If it was running, answering the request starts it again.

These stay yours, even in Auto mode. They wait for you while work goes on:

- raising or removing a budget, run or token limit, or an operating limit
- changing the `git push` policy or how workers' folders are isolated
- turning Auto mode on or off

Turning it on or off:

- **Computer:** the **Auto mode** panel on the organization's page, or in Settings. **Turn on…** explains what it allows and asks you to confirm.
- **Phone:** the Auto mode card on the organization's page, with the same confirmation. An older computer shows an update note instead.
- **CLI:** `Raticode org auto-mode ORG_ID --on --yes` or `--off`; without either, it shows the setting, why it stopped and Rem's recent decisions. It refuses inside a Raticode agent turn.

Rem, workers and agent tokens can't turn it on or off, and restoring an older revision doesn't change it. The panel shows **Auto** beside the name, what Rem is reviewing, and Rem's recent decisions with their reasons; every decision is also in the activity log as made by Rem in Auto mode. A decision is saved before it's applied, so after a restart Raticode applies it once without asking Rem again.

Other rules:

- New or changed heartbeats must be at least 15 minutes apart. A shorter heartbeat saved before this rule keeps working until edited.
- Employees can lift only blocks an employee set. Work that Raticode, you or Rem blocked for review, and work you cancelled, stays stopped until you or Rem reopen it. Only your reopen gives a task that hit its run limit a fresh allowance.
- Rem can't store credential values; enter them under **Operations > Protected credentials**.
- A paired phone can't start an organization whose active employees run without a sandbox.

See [Swarms](./swarms.md), [Desktop fleet](./fleet.md), and [Permissions and local data](./security.md).
