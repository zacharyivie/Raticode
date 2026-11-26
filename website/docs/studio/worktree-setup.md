---
title: Project setup for new worktrees
description: Install dependencies, link folders and copy files into every worktree Rem, Organizations and Swarms create, after you approve the setup.
pageClass: docs-layout
---

# Project setup for new worktrees

Rem, Organizations and Swarms often give an agent its own Git worktree, so several can work on one project at once, and a workflow with [**Run in a worktree**](../workflows/rattish.md#run-in-a-worktree) on gets one for each run (from 0.6.0). A fresh worktree only has the files Git tracks: no installed packages, no virtual environment, no local `.env`. A project's setup file tells Raticode how to prepare each new worktree before the agent or workflow starts.

Worktree setup is new in Raticode 0.5.5.

## The setup file

Commit `.raticode/worktree-setup.json` at the root of the project folder:

```json
{
  "setup": ["npm ci --prefix frontend"],
  "link": [".venv", "frontend/node_modules"],
  "copy": [".env"],
  "timeoutSeconds": 900,
  "os": {
    "windows": { "setup": "npm.cmd ci --prefix frontend" }
  }
}
```

| Field | What it does |
| --- | --- |
| `setup` | A command, or a list of commands run in order. Each runs in the new worktree's project folder: with `sh` on macOS and Linux, and `cmd` on Windows. Setup stops at the first command that fails. |
| `link` | Paths in the project folder to link into the worktree, such as installed packages. The worktree uses the same files, so nothing is installed twice. |
| `copy` | Paths in the project folder to copy into the worktree, such as a local settings file the worktree may change. |
| `timeoutSeconds` | The time limit for all the commands together, from 1 to 7200 seconds. The default is 900 (15 minutes). |
| `os` | Overrides for `linux`, `macos` or `windows`. A field set there replaces the same field above on that system; fields it leaves out keep their values. |

Links and copies are made first, then the commands run. Paths are relative to the project folder and can't leave it or point into `.git`. A path that is already in the worktree is left as it is.

Commands get these environment variables:

- `RATICODE_PROJECT_ROOT`: the project folder the worktree was made from.
- `RATICODE_WORKTREE_PATH`: the new worktree's project folder.
- `RATICODE_WORKTREE_SETUP`: set to `1`.

### Keep linked and copied files out of Git

Every link and copy must be ignored by Git, so it never gets committed with the agent's work. Raticode removes one that isn't and reports the step as failed. For a linked folder, write the `.gitignore` line without a trailing slash (`node_modules`, not `node_modules/`), because Git sees the link as a file. Anything your commands create, such as a build folder, should be ignored too, or it shows up as a change on the branch.

## Approve the setup

A repository you cloned could carry a harmful setup command, and setup runs as you, outside the agent's sandbox. So setup only runs once you've approved it, for each project, and again whenever the file changes.

1. The first time a worktree of the project is made, its setup doesn't run. The run says **Project setup is waiting for your approval**, and the agent is told to prepare what it needs itself.
2. Select **Review setup**. Raticode shows what runs, links and copies on this computer, and the whole file.
3. Select **Approve this setup**. Worktrees made from then on run it.

Approval belongs to the project, not to one folder: approving from any worktree of a repository covers all of them. You approve the exact text you reviewed. If the file changed while you were reading it, Raticode asks you to review it again.

When the file changes later, worktrees keep running the version you approved, and the run says **Ran the earlier approved setup**. Rem, Organizations and Swarms run while you're away, so no setup file you haven't seen runs. Review and approve the new version to switch to it. **Stop running setup** removes the approval.

::: warning Approval covers the setup file, not the files its commands run
A command such as `npm ci`, `uv sync`, `make` or `./scripts/setup.sh` runs whatever the worktree's own `package.json` scripts, `Makefile` or script say at that moment, and a change to those needs no new approval. In a Swarm's integration worktree, that includes changes from the agents' branches, so setup runs there only when the Swarm has integration checks, which run the agents' code anyway. Approve only commands whose project files you'd trust to run as you.
:::

Approvals are kept in Raticode's data folder on this computer, readable only by your account. They aren't committed with the project.

## Where setup runs

| Created by | Which worktrees | Where the result shows |
| --- | --- | --- |
| [Rem](../rem/home.md) | A task's temporary worktree, when another task is already changing the project | The task's status in its sheet |
| [Raticode's project check](../rem/home.md#raticode-runs-the-project-s-check-itself) | The fresh worktree each run of the project's decided check and proving test gets (not in a released version yet). Only links and copies: the setup's commands would run files the worker committed outside the sandbox. When setup fails or isn't approved, the check fails without running; when it has commands, the check runs in the task's folder instead. In both cases the proving test isn't run | The check's receipt in the review, with the reason |
| [Organizations](../advanced/organizations.md) | Each ticket's worktree | The ticket's **Did** section and the organization's activity |
| [Swarms](../advanced/swarms.md) | Each agent's assignment worktree, the worktree integration checks run in, and the review worktree before the first checks run in it | The attempt under **Assignment attempts and checks**, or **Results and branches** |

Setup runs only when a worktree is made. A worktree that already exists, such as one kept for a paused task, isn't set up again. Worktrees of an organization's shared repository and worktrees you or a worktree skill make yourself don't run it.

When setup fails, the run continues. The result shows each step, with the end of each command's output, and the agent is told what failed.

## From the command line

`Raticode projects setup PATH` shows a project's setup file, whether you approved it, and what it runs on this computer. Without a path, it lists projects whose setup is waiting for your approval. Approving stays in the app, where the commands are shown in full.
