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

## Find the exact contract

```bash
Raticode rattish docs --format json
Raticode rattish format WORKFLOW_PATH
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

The installed docs and contracts match the CLI that compiles your workflow. When an example written for another version disagrees, trust the CLI's diagnostics. The [node reference](../nodes/index.md) lists all 25 built-in types.

Write new workflows in Rattish. The older TOML workflow commands remain only for compatibility.
