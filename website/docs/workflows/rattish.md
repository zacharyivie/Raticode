---
title: Rattish language
description: Define readable, validated workflows in workflow.rattish.
pageClass: docs-layout
---

# Rattish language

Rattish is the text format for Raticode workflows. One file holds the workflow, its nodes and the routes between them.

## A complete workflow

```rattish
Rattish: 1

Workflow:
  name: Write a note
  timeout: 2m

Node save-note:
  type: write-file
  path: reports/note.txt
  content: A repeatable process starts with one useful step.
  finish: pass
```

| Part | What it does |
| --- | --- |
| `Rattish: 1` | Picks the language version |
| `Workflow:` | Names the process and can set limits such as `timeout` |
| `Node save-note:` | One step, with a stable ID and a built-in `type` |

The file writes `reports/note.txt` only when you run it.

## Source conventions

- Save files as `.rattish`, indented with two spaces.
- Use kebab-case for node IDs and field names.
- Keywords are case-insensitive, and `format` normalizes their spelling. Prompts, commands, paths, model IDs and data strings keep their case.
- Unquoted map keys are read lowercased too. Quote a key whose case matters, such as an environment variable or URL parameter name (`"DEBUG": "1"`), or write the map as JSON. `check` warns when `env`, `params` or MCP `arguments` would change a name, and fails when two keys in one map are the same once read, such as `Path` and `path`.
- Comments start with `#`.
- Quote a string that would otherwise read as a literal. `memory: "none"` is the string; unquoted `none` means no value.

Use a block string for a multiline command or prompt:

```text
prompt: |
  Explain the failing tests.
  Give the smallest useful fix.
```

Rattish looks like YAML but isn't YAML. Each node's contract decides which fields are legal, so a guessed field fails validation.

## Workflow location

Create a registered workflow with `Raticode rattish create "Name" --project PROJECT --format json`, then edit the `workflow.sourcePath` it returns. The CLI also accepts standalone `.rattish` files.

- A registered workflow resolves relative paths from its project root.
- An unregistered file resolves them from its own folder.
- Absolute and home-relative paths also work, and file nodes follow OS permissions.

Prefer project-relative paths so the workflow moves with the project. Review any path outside it.

## Routes and data

| Field or reference | Meaning |
| --- | --- |
| `to` | Where execution goes next |
| `needs` | What must finish before a node is ready |
| `with` | Values bound for one node activation |
| `input.<name>` | A workflow input |
| `node.<id>.output` | A node's successful result. Status and error have their own namespaces. |

Read [Routes](./routes.md), [Inputs and bindings](./inputs.md) and [Loops](./loops.md) before adding more complex control flow.

## Workflow limits

Limits in the `Workflow:` block apply to each run. Leave one out, or set it to `none`, for no limit.

```rattish
Workflow:
  name: Nightly review
  timeout: 2h
  max-runs: 50
  max-tokens: 400000
  max-cost-usd: 2.50
```

| Field | Ends the run when |
| --- | --- |
| `timeout` | The run has been working this long |
| `max-runs` | Another node run would go over this total |
| `max-tokens` | The model calls of the run have used more tokens than this |
| `max-cost-usd` | The estimated cost of the run's model calls is over this many US dollars |

`max-tokens` and `max-cost-usd` are new in 0.5.5. They count every attempt a node makes, including its retries and the calls of any [fallback provider](../nodes/agent.md#fallback-providers) it switched to, and a run that crosses one never switches to a fallback. When a limit ends a run, the run fails with a `budget` error that says which limit it crossed, and it can't be resumed; raise the limit and start a new run.

### What a limit covers inside an agent node

An [agent](../nodes/agent.md) node runs a whole provider session: Claude Code, for example, can make many model calls and use tools before it answers. Before this change, Raticode counted that session as one call and checked the limits only when it ended, so a single agent node could run far past a limit. Now:

| Limit | Provider | What Raticode enforces |
| --- | --- | --- |
| `max-cost-usd` | Claude Code | Raticode starts Claude Code with `--max-budget-usd` set to what is left of the run's budget, less 10% headroom. If Claude Code stops at that cap, the run ends with `RATTISH_COST_LIMIT_EXCEEDED`, not a node failure. |
| `max-tokens` | Claude Code, OpenCode, Copilot | These providers report tokens while the session runs. Raticode adds them to the run's total as they come in and stops the node once the total passes the limit. |
| `max-cost-usd` | Every other provider | Checked when each call ends, as before. One call can go past it. |
| `max-tokens` | Codex, Cursor, Grok, Antigravity, Anthropic API, OpenAI API | These report usage only when the call ends, so the limit is still checked then. One call can go past it. |

Some overshoot remains. Claude Code checks its cap after each model call, and the live count moves only when the provider reports, so a node can go a little past a limit before it stops. Agents that start at the same time each get the run's remaining budget, so together they can pass it. A `max-budget-usd` that a provider profile already passes stays in place when it is smaller than the run's remaining budget.

The cost is the provider's own estimate for each call. Claude Code reports one; some providers, such as Codex, report tokens only. Raticode never counts a missing value as zero, so with `max-cost-usd` set, a call that doesn't report its cost ends the run, and with `max-tokens` set, so does a call that doesn't report its tokens. This includes [jev](../nodes/jev.md) nodes, which report tokens only, and a [fallback provider](../nodes/agent.md#fallback-providers) such as Codex: in a workflow with `max-cost-usd`, the call finishes and then the run ends. For a provider that reports tokens only, use `max-tokens`. A call that fails without reporting anything, such as a rate-limited or signed-out provider answering with an error, doesn't end the run, so its retries and fallback providers still run. [Runs and approvals](../studio/runs.md#model-usage-and-cost) shows where to see what each run used.

The [starter workflows](../starters/index.md) that use an agent (Test and fix, Summarize new files and Daily report) set a small `max-cost-usd` as an example. Change it or set it to `none` in the workflow's **General** settings.

## Run in a worktree

::: info New in 0.6.0
`workspace: worktree` is new in Raticode 0.6.0.
:::

By default a run works in the project folder, so its file edits land there straight away. With `workspace: worktree`, each run works in a git worktree of its own instead, and you decide afterwards whether to keep what it changed. In the Studio, turn on **Run in a worktree** in the workflow's **Workspace** settings.

```rattish
Workflow:
  name: Test and fix
  workspace: worktree
```

| Value | Where each run works |
| --- | --- |
| `project` | The project folder. This is the default. |
| `worktree` | A new git worktree of the project's repository, one per run |

What a run does:

1. Raticode adds a worktree at the project's current commit, on a new branch named `raticode/run/<workflow>-<run>`. Worktrees live in Raticode's data folder, under `rattish-worktrees`.
2. It runs the project's approved [worktree setup](../studio/worktree-setup.md) there, to install packages or link folders. If setup fails, the run fails before any step starts. A setup file you haven't approved doesn't run, and the run goes on without it.
3. Every step works inside the worktree: relative paths, each step's working folder (`working-dir`), and an absolute `working-dir` inside the repository all point at the same place in the worktree, so a shell step and an agent step see the same files. A project in a subfolder of its repository works in the same subfolder of the worktree.
4. When the run ends, whether it passed, failed or was stopped, Raticode commits everything the steps changed on the run's branch.
5. A run that changed nothing removes its worktree and branch. Otherwise both stay until you **Land** or **Discard** the changes from the run's [Changes tab](../studio/runs.md#review-a-worktree-run-s-changes), or with `Raticode rattish runs land RUN_ID` or `Raticode rattish runs discard RUN_ID`.

Land moves the changes into the branch the project was on when the run started. It fast-forwards that branch, or records a merge commit after checking that the two merge without conflicts. It refuses while that branch's folder has uncommitted changes to tracked files, never pushes, and then removes the worktree and branch. A conflict changes nothing and names the files.

Things to know:

- A worktree has only what git tracks. Uncommitted edits in the project folder, including files a step reads such as a `prompt-path` file or a workflow it calls, aren't in it. Commit them first. The workflow file itself is read from the project folder.
- Installed packages, virtual environments and `.env` files aren't tracked either. Add a [worktree setup file](../studio/worktree-setup.md) that links or installs them. Preflight warns with `RATTISH_PREFLIGHT_WORKTREE_SETUP_MISSING` when the project has none.
- The project must be in a git repository with at least one commit. Otherwise preflight fails with `RATTISH_PREFLIGHT_WORKTREE_UNAVAILABLE`.
- A workflow keeps at most 10 run worktrees waiting to be landed or discarded. Past that, triggered runs are skipped, and the trigger's history says why. A run you start yourself fails with `RATTISH_WORKTREE_LIMIT`. Land or discard some runs to make room.
- Runs whose changes wait are never removed by the [clean-up of old runs](../studio/runs.md#old-runs).
- Deleting the workflow removes its waiting run worktrees and their `raticode/run/` branches, and their changes are lost. The Studio confirmation and `Raticode workflow rm` say how many first, and deleting refuses while a worktree run is still running. When Raticode starts, it also removes the worktree folders of workflows that are no longer registered, such as one whose folder you deleted yourself, once nothing in them has changed for a day.
- A [triggered run](./triggers.md) checks the worktree's copies of the scripts, prompts and child workflows its approval covers. The worktree has the committed text, so a file you edited and approved but didn't commit fails its step there. Commit approved edits.
- A sandboxed Claude Code agent step can commit on the run's branch, but it can't move any other branch or tag. Of the repository's shared `.git` folder its shell may write only new objects, the run's own worktree folder and the `raticode/run/` branches, and git's automatic clean-up (`gc.auto`) is off for the step. Moving another branch, for example `git update-ref refs/heads/main HEAD`, fails. A commit also prints a harmless error about `packed-refs.lock`. See [Sandbox and network](../nodes/agent.md#sandbox-and-network).
- A [test set](./tests.md) always runs in the project folder, where its `prepare-command` sets things up.
- **Resume**, **Run again** and **Run from here** start a new run, with a new worktree at the project's current commit. Files the earlier run changed are only there if you landed them first.
- If the project wasn't on a branch when the run started (a detached HEAD), Land can't know where to go. Merge the run's branch yourself, then discard the run.

## Find the exact contract

```bash
Raticode rattish docs --format json
Raticode rattish format WORKFLOW_PATH
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

The installed docs and contracts match the CLI that compiles your workflow. When an example written for another version disagrees, trust the CLI's diagnostics. The [node reference](../nodes/index.md) lists all 27 built-in types.

## Convert a TOML workflow

Raticode 0.6.0 and earlier also ran workflows written as TOML files in the data folder. From 0.6.1, Raticode doesn't load, list, run, schedule or watch them. Convert each one to `workflow.rattish` to keep using it:

```bash
Raticode rattish convert ~/.local/share/gofer/my-workflow.toml --project /path/to/project
Raticode rattish convert my-workflow.toml --output converted/workflow.rattish
```

- `--project DIR` writes the workflow into that project and registers it, so it shows up in Workflows. `--output FILE` only writes the file.
- The converter maps each node, route and agent, then checks the result as `Raticode rattish check` would.
- Some settings live outside the file in Rattish. For a schedule, a file watch, a webhook or a plain-text secret, it prints the command to run, such as `Raticode rattish triggers add cron ...` or `Raticode rattish secrets set NAME`. It never writes a secret into `workflow.rattish`.
- It lists, node by node, anything it couldn't convert, such as subflows and components, and exits 1. `--allow-partial` writes the rest anyway. `--format json` prints the same report as JSON.
- It never changes or deletes the `.toml` file.

In the desktop app, Workflows shows a notice while TOML workflow files are left in the data folder, such as "2 old TOML workflows aren't loaded any more". Choose **Convert to Rattish** next to a file and pick the project folder it goes into.

The old `schedules.db`, `runner-queue.db` and `workflow-revisions` folder stay in the data folder untouched. Raticode doesn't read them any more, and you can delete them once your workflows are converted.

Converting is new in 0.6.1.
