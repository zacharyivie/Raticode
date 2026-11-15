---
title: "Starter: Test and fix"
description: A starter workflow that runs your tests, lets an agent fix failures, and tests again, up to three fixes.
pageClass: docs-layout
---

# Test and fix

Runs your tests. If they fail, an agent reads the output and edits the code, and the tests run again. It stops when they pass, or fails the run after three fixes.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from test-and-fix --project .
```

::: warning The agent edits files
The `fix` node runs `claude-code` with the Read, Edit, Write, Glob and Grep tools in your project folder. Run it on a branch or in a worktree, and review the changes before you keep them.
:::

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `test-command` | string | `npm test` | Runs the tests and exits 0 when they pass. It runs in the project folder |
| `prepare-command` | string | empty | Runs once before the first test run, such as `npm ci` |
| `instructions` | string | `Fix the code so the tests pass. Don't change or delete the tests, and keep each change as small as possible.` | What the agent may change |

## How it works

1. `prepare` runs `prepare-command` if you set one.
2. `run-tests` runs `test-command` and keeps the last 20,000 characters of output. It always finishes and ends its output with `::tests:: passed` or `::tests:: failed with exit code N`.
3. When the tests pass, `done` ends the run. Otherwise `fix` gets the instructions and the output, edits the code, and routes back to `run-tests`.
4. `fix` has `max-runs: 3` and `run-tests` has `max-runs: 4`. A fourth failure fails the run.

Change the limits in the source if three fixes is too many or too few.

The workflow also sets `max-cost-usd: 2.00`, so a run ends once its estimated model cost passes $2. Claude Code starts `fix` with a spending cap from what is left of that budget. Raise it, or set it to `none`, in the workflow's **General** settings. See [Workflow limits](../workflows/rattish.md#workflow-limits).

## Test set

| Case | What it checks |
| --- | --- |
| `already-passing` | Passing tests end the run with no model call |
| `fixes-a-typo` | The agent fixes a misspelled greeting in a sample script, the tests pass, the test file is unchanged, and (judge) the agent explains the fix |

`fixes-a-typo` copies `fixtures/greeting` to the workflow's `logs/test-scratch/` folder first, with `prepare-command`, and tells the agent to edit only that copy. It makes at least one model call with tools, plus one for the judge check.

See [Starter workflows](./index.md), [Routes and cycles](../workflows/routes.md#cycles-and-limits) and the [agent node](../nodes/agent.md).
