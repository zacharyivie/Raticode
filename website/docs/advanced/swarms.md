---
title: Swarms
description: Coordinate a team of coding agents with an orchestrator, assignments, evidence, and review.
pageClass: docs-layout
---

# Swarms

A swarm is a lasting team of agents working toward one project goal. Its orchestrator assigns work and reviews results; you decide when it runs.

::: warning Experimental
Swarms is off by default. Turn it on in **Settings > Developer > Feature Preview** to show **Swarms** in the sidebar.
:::

Swarms coordinate conversational work. A [Rattish workflow](../workflows/rattish.md) is different: a graph you write, with explicit node contracts and routes.

## Create a team

1. Open a project and choose **Swarms** in the sidebar.
2. Create a swarm and write its charter.
3. Add members. A team has exactly one orchestrator; others can focus on implementation, testing, review or research.

Each member has its own provider, model, effort, instructions, resources and project workspace. Fit these to the role rather than giving every agent the same access. Saving a team doesn't start it.

### Review and start

**Review and start** shows the team, each agent's access, the run limits, the integration checks and the Git settings before anything runs.

::: warning Full-access agents
Agents set to `danger-full-access` (Codex) or `bypassPermissions` (Claude Code) run without a sandbox or approval prompts. They can change any file you can, including your original checkouts, and run commands such as `git push`.

- The roster marks them, and you confirm them each time a run starts.
- A paired phone can't start a swarm that has them.
:::

## Assignments and conversations

The team view has member conversations and a task board. Send a message to the orchestrator or to everyone. A message can ask for work; an update for information doesn't need to create an assignment.

- The orchestrator creates assignments, milestones and acceptance criteria.
- Workers report evidence and propose completion. They can't accept their own assignments.
- Review file changes and test results before accepting an outcome.
- Progress counts accepted assignment weights and active work, leaving out cancelled assignments. It measures completion, not time left.

## Limits

Up to three member turns run at once. Every 60 seconds a check wakes the orchestrator, but only when the board has new activity.

Each run has limits, set under **Team setup → Limits and checks**:

| Limit | Default | When reached |
| --- | --- | --- |
| Agent turns per run | 200 | The run pauses. |
| Running time | 8 hours (pauses don't count) | The run pauses. |
| One turn | 60 minutes | The turn stops and its work waits for your review. |
| Turns without progress | 6 in a row | The run pauses until you record a changed plan. |
| Repairs per assignment | 3 | The orchestrator must ask you before repairing again. |
| Token limit | Off | The run pauses at that many reported tokens. |

The run also pauses when:

- the orchestrator is asked to unblock an idle run and changes nothing (paused as stalled, instead of asking again)
- a provider fails five times in a row, or reports a sign-in, quota or setup error. Fix it, then resume.

**Execution details** shows each agent's turns, running time and reported tokens against these limits. Teams saved before limits existed get the defaults on their next run.

**Integration checks** are commands you set, one per line. They run in the review worktree before the combined result is accepted. Without them, only checks the orchestrator writes for its own milestones gate the result.

## Workspaces and Git

- **Git projects:** managed assignments can use separate review worktrees, which keeps each change reviewable.
- **Folders without Git:** writers share the workspace and take turns.
- Worktrees prevent some file conflicts. They don't isolate shell processes or a provider's own sub-agents at the operating-system level.
- Agents get no write access to Raticode's data folder, which holds the swarm records and other agents' worktrees.

A run starts from a snapshot of your checkout, including uncommitted and untracked files. Untracked files that look like credentials, such as `.env`, `*.pem` or `id_rsa`, are left out; **Execution details** lists them.

Local managed Git work is on by default. Publishing to a remote is a separate choice: it needs local Git management, a working `gh` and its own sign-in. Check the destination and publishing behavior before turning it on.

## Pause, stop and recover

| Action | Effect |
| --- | --- |
| **Pause** | No new turns; current turns finish. You can resume later. |
| **Stop** | Asks first, cancels running agents, archives unfinished work and removes the run's worktrees. The review branch stays. A stopped run can't resume. |
| **Delete swarm** | In Team setup, once no run is active. Removes the team and its run history. Review worktrees and `raticode/swarm/…` branches stay in your repositories. |

Neither Pause nor Stop undoes files already changed or commands already run.

After the app restarts, runs that were working stay paused, even when other agents had work queued. Check interrupted work in the human inbox before resuming. Bounded retries keep the workspace. An uncertain failure needs **Retry** or **Dismiss**; Raticode doesn't launch a duplicate on its own.

## Manage through Rem

**Settings > Rem > Swarm access** is on by default and applies from the next message after you change it. It's always off for Rem's unattended [self-work](../rem/board.md#self-work).

With access on, Rem can inspect and manage teams within its project scope. It can still inspect in read-only sessions and with shell tools off; changes follow the session's permissions.

- Ask Rem to inspect a team, explain blocked assignments or propose a charter. Tell it explicitly before it starts a run.
- Rem can't answer an agent's approval request, waive verification, or repair past the repair limit. Those stay with you in the swarm's human inbox and dashboard.
- Rem doesn't ask you again before starting a team with full-access agents. The run's activity records that Rem started them and names those agents.
- Rem's actions are recorded as Rem's.

See [Resources](../rem/resources.md), [Usage](../rem/usage.md), and [Source control](../studio/source-control.md).
