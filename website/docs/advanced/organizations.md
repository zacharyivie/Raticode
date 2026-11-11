---
title: Organizations
description: Configure persistent agent organizations, supervised task queues, budgets, routines, and review.
pageClass: docs-layout
---

# Organizations

Organize long-running work around employees, projects, goals, and an explicit review process.

::: warning Experimental
Organizations is off by default. Turn it on in **Settings > Developer > Feature Preview**; Raticode restarts its backend to apply the change. Turning it off hides the feature and keeps its data. A backend started without the desktop app reads `RATICODE_EXPERIMENTAL_ORGANIZATIONS=1` instead. Evaluate supervised tasks before relying on unattended work.
:::

## Find your way around

Open **Organizations** in the sidebar. Each organization's home has five tabs:

| Tab | What it holds |
| --- | --- |
| **Organization** | The org chart (drag to pan, Ctrl+scroll or Ctrl+plus/minus to zoom), mission and goals |
| **Projects** | Owned project folders with their milestones and epics |
| **Issues** | The ticket board. Tickets have keys such as `RAT-12` and belong to epics. |
| **Workers** | The organization's agents (the employees below), their roles and status |
| **Knowledge** | Shared notes and decisions, with evidence-checked claims |

Opening a ticket, project, worker or note fills the main area, with a back button at the top left; Escape returns to the list. The header has **Start** or **Pause**, **Find anything** (Ctrl+K), a working-hours pill, the current work schedule block and a gear for **Settings, history and operations**. On the phone, the same five sections are under the **Orgs** tab.

## Define the organization

Organizations is global: you do not need to select a project to open it or manage it through Rem. Create the organization, then configure employees, reporting lines, teams, goals, initiatives, and owned projects.

A project can be owned by only one organization. Each employee (shown as a worker) has a persona, responsibilities, a manager, instructions, provider/model/effort choices, a permission mode, workspace allowlists, resources, budgets, and run limits. Define narrow roles and review those settings before starting work.

**Limit ticket actions by project** on a worker sets, per project, whether it may create, edit, move or cancel tickets, so a worker can manage tickets in one project and not another. **Check-ins** set how often an idle worker checks in (see below). **Hold this worker** stops one worker without pausing the organization.

**Working hours** are weekly windows in one time zone, set from the header pill. Workers only start and keep working inside them. Work that closing time cuts off goes to **Blocked**, waiting for working hours, and back to **To do** by itself when they open, ahead of other work.

## Workers run like small Rems

Each worker looks after its own tickets in a loop, much like Rem does in [self-work](../rem/board.md#self-work), and stays inside the projects you gave it. You choose a worker's projects in the board's **People** view. Only you can change them.

While the organization runs and working hours are open, a worker with projects:

1. Looks at its own **Blocked** tickets first. Each blocked spell gets one look, in a check-in that runs ahead of its To do work. The worker gets a ticket going again if it can and says what changed. If the ticket needs you, it stays Blocked with a comment saying what's needed.
2. Works its **To do** tickets, by priority. Tickets that fit the current [work schedule](#work-schedule) block move up a level.
3. Checks in when it has nothing to do. A check-in plans follow-ups of its own tickets in To do and proposes new work that fits the current block as **Backlog** tickets. Backlog never runs until you move it, and tickets that need approval wait for it.

Check-ins come hourly unless the worker's **Check-ins** setting says otherwise. Each check-in in a row that creates nothing doubles the wait, up to eight times, and real work resets it. They only happen during working hours, and run, token and dollar limits apply to them like any other run.

A worker with projects works only there. That covers tickets you give it, tickets it creates, the projects it can open threads in, and tickets it comments on, apart from its own. A ticket you move to a project its worker doesn't have goes to **Blocked**, saying to assign the worker or give the ticket to someone who is. A worker with no projects does no self-work.

Workers manage the tickets assigned to them, and a manager can also manage the tickets of people who report to them. A worker can hand a ticket to someone else in the same organization who works in that project, but must say why: the reason is saved as a comment on the ticket. Tickets can't move to another organization's workers.

Workers can't create, change, start or pause organizations, swarms, workers, schedules or limits. Their organization tool only offers ticket, comment, review, knowledge and evidence actions, and Raticode refuses anything else. Their swarm and fleet tools only read. Configuration stays with you, and with Rem through the approval requests below.

::: tip Organizations made before this change
Nothing about an existing organization is reset. Workers you never gave projects to keep working on the tickets you give them anywhere in the organization's folders, as before, but they don't self-work until you assign their projects. Once you do, they're bounded to them.
:::

## Work schedule

Working hours say *when* workers may work. The work schedule says *what* they should favor while they do. It's a list of named blocks, each with a focus and optional keywords, set from **Work schedule** on the Organization tab or the block pill in the header.

Each block repeats on one cadence:

| Cadence | Repeats | Example |
| --- | --- | --- |
| Daily | every day | 08:00–17:00 New feature work |
| Weekly | on the weekdays you pick | Fridays 13:00–17:00 Weekly release |
| Monthly | on a day of the month, or the 1st to 4th or last of a weekday | 1st Monday 09:00–12:00 Dependency updates and audit |
| Quarterly | the same, in the 1st, 2nd or 3rd month of each quarter | 1st Tuesday of the quarter, 09:00–17:00 Security review |

An end time at or before the start runs past midnight, so 17:00–00:00 and 22:00–06:00 both work. The same start and end makes a whole day. Day 29, 30 or 31 lands on the last day of shorter months, so day 31 means "the last day". Quarters start in January, April, July and October.

When blocks overlap, one wins:

1. The block that comes round less often wins: quarterly, then monthly, then weekly, then daily. A Friday release beats daily feature work on Friday afternoons.
2. With the same cadence, the shorter block wins. A one-hour nightly release at 00:00 beats overnight gardening from 00:00 to 08:00.
3. Two overlapping blocks with the same cadence and length can't be saved, because neither would win.

Times that no daily or weekly block covers during working hours are listed under the schedule, as are blocks that fall outside working hours. Workers use their usual priorities in those gaps.

The schedule uses the organization's time zone, the same one as working hours. Blocks keep their clock times through daylight saving changes, so on those days a block can run an hour shorter or longer. A start time the clocks skip moves an hour later, and a time that happens twice uses the first one.

**What workers do with it.** Every run gets the current block, when it ends and what comes next. Tickets whose title, description, epic or project mention one of the block's keywords move up one priority level, but never past a ticket two levels above them. Check-ins propose work that fits the focus.

**When a block changes.** Running work is never stopped. A run that started under one block finishes under it, and the Organization tab shows which block each running worker started under. The next ticket a worker picks, and its next check-in, follow the new block. Workers are told to wrap up and leave a handoff comment rather than start something large near the end of their block. Saving a new schedule doesn't stop running work either. Working hours still stop or finish work at closing time as you chose.

**Suggested schedule.** **Start from the suggested schedule** fills the editor with New feature work (08:00–17:00), Application hardening (17:00–00:00), Nightly release (00:00–01:00), Process improvement and gardening (00:00–08:00), a Friday afternoon weekly release, a monthly dependency audit and a quarterly security review. Edit or delete any of them, then save. Raticode never applies it by itself, and existing organizations have no schedule until you save one.

## Shared repository and knowledge

Each organization has its own Git repository in Raticode's data folder, outside every project. It holds what the whole organization shares: knowledge entries as Markdown, decisions, notes and other files. A run works in its own worktree of it on a `shared/` branch, and accepting the ticket merges that branch into `main`. The repository has no remote, and `git push` is blocked there. A ticket lists the shared files its run changed.

Claims on tickets and knowledge entries carry evidence that Raticode checks itself: a quote must appear in the cited entry, a file is fingerprinted, a commit must exist, and a check command is rerun only when it's one of the project's configured checks or you run it. Changed evidence marks the claim stale.

## Where employees work

By default each task runs in its own Git worktree on an `org/<task>` branch, created from the project's current commit. Employees never edit your project folder, so several employees can work at once and your own uncommitted changes are safe. A task's branch and worktree appear on the task; when the task is done or cancelled, Raticode commits anything left in the worktree, removes it, and keeps the branch for you to review and merge from Source Control. Folders that aren't Git repositories run in place.

**Settings > Where employees work > Directly in the project folder** turns this off. Employees then take turns in the folder itself, and a run starts only when the folder has no uncommitted changes.

`git push` fails inside employee runs unless you clear **Block git push in employee runs**. This guards against mistakes; an employee with unrestricted shell access could undo it.

## Tasks and independent review

Operations contains the work queue, task filters, activity, routines, execution destinations, and recovery controls. A task has an assignee, priority, dependencies, plan, evidence, and review state.

Plan approval and completion acceptance are separate decisions. Tasks can have an independent reviewer, and reviews can accept, reject, or request revision. Completion of a dependency can wake waiting work. A cancelled task stays cancelled until explicitly reopened.

The queue explains why a task is waiting, including employee holds, unmet dependencies, review, workspace contention, or budget limits. Blocked tasks say why and what to do next. Start work preserves individual employee holds; **Resume all employees** is a separate action.

**Start** (in the header; **Start work** in settings) first shows what will run: the tasks that can start, backlog tasks that won't, plans still waiting for approval, employees and their permission modes, and the monthly limits. Nothing starts until you confirm. Approve a plan from the task itself, where its description is visible. Keep planned work in **Backlog**; Start never runs it until you move it to **To do**.

**Pause** stops running tasks and moves them to **Blocked**, waiting for the organization to start again; the next Start puts them back in To do to run, and the interrupted run doesn't count against the task's run limit. Delegated work (swarms, paired desktops, HTTPS gateways) stays blocked for review instead.

Open a run under **Runs and artifacts** on a task to read what the employee did, or choose **Show input** to see exactly what it was given: its instructions, the task and conversation, its tools and working folder. Inputs are stored compressed and readable only by you, and Raticode checks each against the run's fingerprint. Each run records the provider, model, effort and permission mode it used, whether it ran in its own worktree, a fingerprint of everything the employee was given, and, for worktree runs, the commit it ended at and whether it left uncommitted changes.

## Audit trail

Each status change of a ticket (to Blocked, back to work, to Review, to Done, or cancelled) also adds a short entry to its conversation saying why, which the [global board](../rem/board.md#history-and-comments) shows as the ticket's history. Activity is append-only: entries and task conversations can't be edited or deleted, and each entry is linked to the one before it by a hash. Every 30 seconds, and after each successful check, Raticode also appends the latest link to `organization-audit-anchors.jsonl` in its data folder, outside the database. **Configuration history > Verify audit trail** checks the configuration history, the activity chain and those anchors, and reports the first entry that doesn't match. The anchors catch entries removed from the end of the log or a rebuilt database, unless the anchor file was changed too. Entries written before an upgrade added linking are counted but can't be checked. Every change to work is recorded, including changes the scheduler makes on its own, such as blocking a task that reached its run limit. Retry keys for repeated requests are kept for 30 days.

## Routines and evidence

Routines use cron with a timezone, intervals, or authenticated triggers. Configure overlap as skip or queue, and missed occurrences as skip or coalesce. A routine can queue work while the organization is paused; execution still requires Start.

Source-linked memory records a file hash, author, verification time, and optional expiry. Changed or expired evidence becomes stale, leaves the verified-memory context, and queues a recheck. This avoids presenting an old conclusion as verified against new source.

Rehearsal estimates queue order using copied state and a supplied clock. It calls no provider and does not predict future review decisions, external effects, or dollar costs.

## Budgets and destinations

Set monthly run, token and dollar limits at the organization, employee and initiative level, plus a run limit per task. New organizations start with 200 runs a month; 0 means unlimited. Codex and several other providers don't report dollar cost, so a dollar budget pauses their work after the first run; use a token or run limit for them. A run already in progress can go past what's left. Operations shows how many employees can actually work at once and why.

Each employee run can create at most 5 tasks for others (**Tasks one run can create**), nested at most 3 levels deep (**Delegation depth**). Native provider children and unrestricted shell processes are not sandboxed by organizational accounting.

Managed destinations can include local workflows, swarms, paired desktops, or an explicitly trusted HTTPS job gateway. Workflow validation previews inputs, resources, source and IR hashes, and completion checks. Launch authorization binds that reviewed contract. Editing a task plan clears its authorization; previewing it does not run it.

## Recovery and history

Every organization starts paused after a restart, and the pause reason says so. Interrupted or uncertain external work retains its receipt and reservation; it is not silently replayed. Use **Check stopped executions**, or **Confirm stopped after inspection** with evidence when automatic confirmation is impossible.

Configuration history supports revision-aware restore previews. Restoring configuration does not undo file changes, spending, or task history. Permission changes revoke affected claims, while changes to owned workspaces pause the organization.

**Archive organization** (in Settings, while paused) hides a company and releases its project folders for other organizations. History and work records are kept; **Restore organization** reclaims the folders.

**Export template** creates a reusable company definition, not an operational backup. An operational backup is a consistent SQLite snapshot; it excludes OS-keyring credential values. Restore with the application closed and retain the original database for rollback.

## Use Rem

When enabled, organization management tools are available in both global and project threads. Ask Rem to inspect an organization before changing it, and state which configuration or task you want changed. Read-only and plan sessions cannot mutate organizations, and workflow execution still requires authorization.

Rem can't start an organization by itself. The Organizations tab shows a request, and nothing changes until you approve it, when Rem asks to:

- start the organization, resume all employees, or archive it;
- approve a task's plan, remove a task's approval or review step, accept a result that needs independent review, authorize a workflow, or confirm that interrupted outside work has stopped;
- turn on a routine or webhook, or make an enabled routine run more often;
- re-enable or repoint a revoked credential;
- add or resume employees, change a permission mode to anything that can write, give employees more tools, skills, folders or credentials, or add project folders or execution destinations;
- raise or remove a run, token or dollar limit, raise an operating limit, make heartbeats more frequent, or widen working hours;
- allow `git push` or let employees edit the project folder directly.

New or changed heartbeats must be at least 15 minutes apart. A shorter heartbeat saved before this rule keeps working until it is edited.

Employees can lift only blocks that an employee set. Work that Raticode, you or Rem blocked for review, and work you cancelled, stays stopped until you or Rem reopen it. Only your reopen gives a task that hit its run limit a fresh allowance.

Requests expire after a day, asking twice returns the same request, and a request is applied at most once. A change that would fail anyway, such as claiming a folder another organization owns, is refused right away instead of waiting. Rem can't store credential values; enter them under **Operations > Protected credentials**. A paired phone can't start an organization whose active employees run without a sandbox.

See [Swarms](./swarms.md), [Desktop fleet](./fleet.md), and [Permissions and local data](./security.md).
