---
title: Swarms
description: Coordinate a team of coding agents with an orchestrator, assignments, evidence, and review.
pageClass: docs-layout
---

# Swarms

A swarm is a persistent agent team working toward one project goal. Its orchestrator assigns work and reviews results while you keep control of execution.

## Create a team

Open a project and choose **Swarms** in the sidebar. Create a swarm, write its charter, and define the members. A team has exactly one orchestrator; other members can specialize in implementation, testing, review, or research.

Each member has its own provider, model, effort, instructions, resources, and project workspace. Make those choices for the role rather than giving every agent identical access. Saving a team does not start it. Review the charter and configuration before starting a task.

Swarms coordinate conversational work. A [Rattish workflow](../workflows/rattish.md) is a separately authored execution graph with explicit node contracts and routes.

## Assignments and conversations

The team view contains member conversations and a task board. Send a message to the orchestrator or everyone. A message can request work; an informational update need not create another assignment.

The orchestrator creates assignments, milestones, and acceptance criteria. Workers report evidence and proposed completion; they cannot accept their own assignments. Review file changes and verification results before accepting an outcome.

Progress reflects accepted assignment weights and active work. Cancelled assignments are excluded. It is a measure of completion, not a prediction of remaining time.

## Concurrency and workspace isolation

The default allows three concurrent member turns, with a 60-second check interval. Events also wake reconciliation; an idle team does not need empty provider turns merely to poll. There is no default cumulative time or turn limit, so choose providers and monitor their usage deliberately.

In Git projects, managed assignments can use separate review worktrees. This preserves individual changes for review. In folders without Git, writers must share the workspace and are serialized. Worktree isolation prevents some file conflicts; it does not isolate shell processes or provider-native child agents at the operating-system level.

Local managed Git work is enabled by default. Remote Git publishing is a separate choice and requires local Git management, a working `gh` installation, and its own authentication. Review the destination and publishing behavior before enabling it.

## Pause, stop, and recover

**Pause** prevents new turns while current turns finish. **Stop** requests cancellation. Neither action reverses files already changed or commands already completed.

After an application restart, swarms remain paused. Inspect interrupted work before resuming. Bounded retries preserve the workspace; uncertain failures require **Retry** or **Dismiss**, rather than an automatic duplicate launch.

## Manage through Rem

Rem's Swarm access resource is enabled by default and applies on the next turn after a change. With access enabled, Rem can inspect and manage teams within its project scope. Inspection remains possible in read-only sessions and with shell tools disabled; mutations still follow session permissions.

Ask Rem to inspect an existing team, explain its blocked assignments, or propose a charter. Give an explicit instruction before starting execution. See [Resources](../rem/resources.md), [Usage](../rem/usage.md), and [Source control](../studio/source-control.md).
