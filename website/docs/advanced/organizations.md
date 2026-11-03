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

## Define the organization

Organizations is global: you do not need to select a project to open it or manage it through Rem. Create the organization, then configure employees, reporting lines, teams, goals, initiatives, and owned projects.

A project can be owned by only one organization. Each employee has instructions, provider/model/effort choices, workspace allowlists, resources, budgets, and run limits. Define narrow roles and review those settings before starting work.

## Where employees work

By default each task runs in its own Git worktree on an `org/<task>` branch, created from the project's current commit. Employees never edit your project folder, so several employees can work at once and your own uncommitted changes are safe. A task's branch and worktree appear on the task; when the task is done or cancelled, Raticode commits anything left in the worktree, removes it, and keeps the branch for you to review and merge from Source Control. Folders that aren't Git repositories run in place.

**Settings > Where employees work > Directly in the project folder** turns this off. Employees then take turns in the folder itself, and a run starts only when the folder has no uncommitted changes.

`git push` fails inside employee runs unless you clear **Block git push in employee runs**. This guards against mistakes; an employee with unrestricted shell access could undo it.

## Tasks and independent review

Operations contains the work queue, task filters, activity, routines, execution destinations, and recovery controls. A task has an assignee, priority, dependencies, plan, evidence, and review state.

Plan approval and completion acceptance are separate decisions. Tasks can have an independent reviewer, and reviews can accept, reject, or request revision. Completion of a dependency can wake waiting work. A cancelled task stays cancelled until explicitly reopened.

The queue explains why a task is waiting, including employee holds, unmet dependencies, review, workspace contention, or budget limits. Blocked tasks say why and what to do next. Start work preserves individual employee holds; **Resume all employees** is a separate action.

**Start work** first shows what will run: the tasks that can start, backlog tasks that won't, plans still waiting for approval, employees and their permission modes, and the monthly limits. Nothing starts until you confirm. Approve a plan from the task itself, where its description is visible. Keep planned work in **Backlog**; Start never runs it until you move it to **To do**.

**Pause** stops running tasks and returns them to To do; they run again on the next Start, and the interrupted run doesn't count against the task's run limit. Delegated work (swarms, paired desktops, HTTPS gateways) stays blocked for review instead.

Open a run under **Runs and artifacts** on a task to read what the employee did, or choose **Show input** to see exactly what it was given: its instructions, the task and conversation, its tools and working folder. Inputs are stored compressed and readable only by you, and Raticode checks each against the run's fingerprint. Each run records the provider, model, effort and permission mode it used, whether it ran in its own worktree, a fingerprint of everything the employee was given, and, for worktree runs, the commit it ended at and whether it left uncommitted changes.

## Audit trail

Activity is append-only: entries and task conversations can't be edited or deleted, and each entry is linked to the one before it by a hash. Every 30 seconds, and after each successful check, Raticode also appends the latest link to `organization-audit-anchors.jsonl` in its data folder, outside the database. **Configuration history > Verify audit trail** checks the configuration history, the activity chain and those anchors, and reports the first entry that doesn't match. The anchors catch entries removed from the end of the log or a rebuilt database, unless the anchor file was changed too. Entries written before an upgrade added linking are counted but can't be checked. Every change to work is recorded, including changes the scheduler makes on its own, such as blocking a task that reached its run limit. Retry keys for repeated requests are kept for 30 days.

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
- raise or remove a run, token or dollar limit, raise an operating limit, or make heartbeats more frequent;
- allow `git push` or let employees edit the project folder directly.

New or changed heartbeats must be at least 15 minutes apart. A shorter heartbeat saved before this rule keeps working until it is edited.

Employees can lift only blocks that an employee set. Work that Raticode, you or Rem blocked for review, and work you cancelled, stays stopped until you or Rem reopen it. Only your reopen gives a task that hit its run limit a fresh allowance.

Requests expire after a day, asking twice returns the same request, and a request is applied at most once. A change that would fail anyway, such as claiming a folder another organization owns, is refused right away instead of waiting. Rem can't store credential values; enter them under **Operations > Protected credentials**. A paired phone can't start an organization whose active employees run without a sandbox.

See [Swarms](./swarms.md), [Desktop fleet](./fleet.md), and [Permissions and local data](./security.md).
