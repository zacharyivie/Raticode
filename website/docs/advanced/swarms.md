---
title: Swarms
description: Coordinate a team of coding agents with an orchestrator, assignments, evidence, and review.
pageClass: docs-layout
---

# Swarms

A swarm is a persistent agent team working toward one project goal. Its orchestrator assigns work and reviews results while you keep control of execution.

::: warning Experimental
Swarms is off by default. Turn it on in **Settings > Developer > Feature Preview** to show **Swarms** in the sidebar.
:::

## Create a team

Open a project and choose **Swarms** in the sidebar. Create a swarm, write its charter, and define the members. A team has exactly one orchestrator; other members can specialize in implementation, testing, review, or research.

Each member has its own provider, model, effort, instructions, resources, and project workspace. Make those choices for the role rather than giving every agent identical access. Saving a team does not start it. Review the charter and configuration before starting a task.

**Review and start** shows the team, each agent's access, the run limits, the integration checks and the Git settings before anything runs. Agents set to `danger-full-access` (Codex) or `bypassPermissions` (Claude Code) run without a sandbox or approval prompts: they can change any file you can, including your original checkouts, and run commands such as `git push`. The roster marks them, and you have to confirm them each time a run starts. A paired phone can't start a swarm that has such agents.

Swarms coordinate conversational work. A [Rattish workflow](../workflows/rattish.md) is a separately authored execution graph with explicit node contracts and routes.

## Assignments and conversations

The team view contains member conversations and a task board. Send a message to the orchestrator or everyone. A message can request work; an informational update need not create another assignment.

The orchestrator creates assignments, milestones, and acceptance criteria. Workers report evidence and proposed completion; they cannot accept their own assignments. Review file changes and verification results before accepting an outcome.

Progress reflects accepted assignment weights and active work. Cancelled assignments are excluded. It is a measure of completion, not a prediction of remaining time.

## Concurrency and workspace isolation

The default allows three concurrent member turns, with a 60-second check interval. The check only wakes the orchestrator when the board has new activity.

Every run has limits, set under **Team setup → Limits and checks**:

| Limit | Default | When reached |
| --- | --- | --- |
| Agent turns per run | 200 | The run pauses. |
| Running time | 8 hours, not counting pauses | The run pauses. |
| One turn | 60 minutes | The turn is stopped and its work waits for your review. |
| Turns without progress | 6 in a row | The run pauses until you record a changed plan. |
| Repairs per assignment | 3 | The orchestrator must ask you before repairing again. |
| Token limit | Off | The run pauses at that many reported tokens. |

If the orchestrator is asked to unblock an idle run and changes nothing, the run pauses as stalled instead of asking again. A provider that fails five times in a row, or reports a sign-in, quota or setup error, pauses the run until you fix it and resume. **Execution details** shows turns, running time and reported tokens for each agent against these limits. Teams saved before limits existed get the defaults on their next run.

**Integration checks** are commands you set, one per line, that run in the review worktree before the combined result is accepted. Without them, only checks the orchestrator writes for its own milestones gate the result.

In Git projects, managed assignments can use separate review worktrees. This preserves individual changes for review. In folders without Git, writers must share the workspace and are serialized. Worktree isolation prevents some file conflicts; it does not isolate shell processes or provider-native child agents at the operating-system level. Agents are not given write access to Raticode's data folder, which holds the swarm records and other agents' worktrees.

A run starts from a snapshot of your checkout, including uncommitted and untracked files. Untracked files that look like credentials, such as `.env`, `*.pem` or `id_rsa`, are left out; **Execution details** lists them.

Local managed Git work is enabled by default. Remote Git publishing is a separate choice and requires local Git management, a working `gh` installation, and its own authentication. Review the destination and publishing behavior before enabling it.

## Pause, stop, and recover

**Pause** prevents new turns while current turns finish, and you can resume later. **Stop** asks for confirmation, cancels running agents, archives unfinished work and removes the run's worktrees. The review branch stays, but a stopped run can't be resumed. Neither action reverses files already changed or commands already completed.

After an application restart, runs that were working stay paused, even when other agents had independent work queued. Inspect interrupted work in the human inbox before resuming. Bounded retries preserve the workspace; uncertain failures require **Retry** or **Dismiss**, rather than an automatic duplicate launch.

**Delete swarm** in Team setup removes a team and its run history once no run is active. Review worktrees and `raticode/swarm/…` branches stay in your repositories.

## Manage through Rem

**Settings > Rem > Swarm access** is on by default and applies to the next message after a change. It is always off for Rem's unattended [self-work](../rem/board.md#self-work). With access enabled, Rem can inspect and manage teams within its project scope. Inspection remains possible in read-only sessions and with shell tools disabled; mutations still follow session permissions.

Ask Rem to inspect an existing team, explain its blocked assignments, or propose a charter. Give an explicit instruction before starting execution. Rem can't answer an agent's approval request, waive verification, or repair past the repair limit; those decisions stay with you in the swarm's human inbox and dashboard. Rem can start a team with full-access agents only after you confirm them in the conversation, and its actions are recorded as Rem's. See [Resources](../rem/resources.md), [Usage](../rem/usage.md), and [Source control](../studio/source-control.md).
