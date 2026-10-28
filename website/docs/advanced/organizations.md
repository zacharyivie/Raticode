---
title: Organizations
description: Configure persistent agent organizations, supervised task queues, budgets, routines, and review.
pageClass: docs-layout
---

# Organizations

Organize long-running work around employees, projects, goals, and an explicit review process.

::: warning Experimental
Organizations is disabled by default. Quit the app completely and launch it with `RATICODE_EXPERIMENTAL_ORGANIZATIONS=1` in its environment to opt in. Removing the variable and restarting hides the feature while preserving its data. Evaluate supervised tasks before relying on unattended work.
:::

## Define the organization

Organizations is global: you do not need to select a project to open it or manage it through Rem. Create the organization, then configure employees, reporting lines, teams, goals, initiatives, and owned projects.

A project can be owned by only one organization. Each employee has instructions, provider/model/effort choices, workspace allowlists, resources, budgets, and turn controls. Define narrow roles and review those settings before starting work.

## Tasks and independent review

Operations contains the work queue, task filters, activity, routines, execution destinations, and recovery controls. A task has an assignee, priority, dependencies, plan, evidence, and review state.

Plan approval and completion acceptance are separate decisions. Tasks can have an independent reviewer, and reviews can accept, reject, or request revision. Completion of a dependency can wake waiting work. A cancelled task stays cancelled until explicitly reopened.

The queue explains why a task is waiting, including employee holds, unmet dependencies, review, workspace contention, or budget limits. Start work preserves individual employee holds; **Resume all employees** is a separate action.

## Routines and evidence

Routines use cron with a timezone, intervals, or authenticated triggers. Configure overlap as skip or queue, and missed occurrences as skip or coalesce. A routine can queue work while the organization is paused; execution still requires Start.

Source-linked memory records a file hash, author, verification time, and optional expiry. Changed or expired evidence becomes stale, leaves the verified-memory context, and queues a recheck. This avoids presenting an old conclusion as verified against new source.

Rehearsal estimates queue order using copied state and a supplied clock. It calls no provider and does not predict future review decisions, external effects, or dollar costs.

## Budgets and destinations

Configure turn reservations and supported dollar limits at the relevant organization, employee, initiative, and task levels. Operations shows both configured concurrency and the service ceiling of eight turns.

Missing cost coverage under a dollar limit can pause further work. Observed dollar limits can overshoot while a turn is already in flight. Native provider children and unrestricted shell processes are not sandboxed by organizational accounting.

Managed destinations can include local workflows, swarms, paired desktops, or an explicitly trusted HTTPS job gateway. Workflow validation previews inputs, resources, source and IR hashes, and completion checks. Launch authorization binds that reviewed contract. Editing a task plan clears its authorization; previewing it does not run it.

## Recovery and history

Every organization starts paused after a restart. Interrupted or uncertain external work retains its receipt and reservation; it is not silently replayed. Use **Check stopped executions**, or **Confirm stopped after inspection** with evidence when automatic confirmation is impossible.

Configuration history supports revision-aware restore previews. Restoring configuration does not undo file changes, spending, or task history. Permission changes revoke affected claims, while changes to owned workspaces pause the organization.

**Export template** creates a reusable company definition, not an operational backup. An operational backup is a consistent SQLite snapshot; it excludes OS-keyring credential values. Restore with the application closed and retain the original database for rollback.

## Use Rem

When enabled, organization management tools are available in both global and project threads. Ask Rem to inspect an organization before changing it, and state which configuration or task you want changed. Read-only and plan sessions cannot mutate organizations, and workflow execution still requires authorization.

See [Swarms](./swarms.md), [Desktop fleet](./fleet.md), and [Permissions and local data](./security.md).
