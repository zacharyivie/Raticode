---
title: File watchers
description: Start Rattish workflows when files change, with file triggers or an outside watcher.
pageClass: docs-layout
---

# File watchers

To start a `workflow.rattish` when files change, add a [file trigger](./triggers.md#file-changes).

::: info The older watcher service is gone from 0.6.1
Raticode 0.6.0 and earlier also had `Raticode watch` commands, which watched files for old TOML workflows only. From 0.6.1 those commands and the watcher service are gone. [Convert the old workflow](./rattish.md#convert-a-toml-workflow), and the converter prints the `Raticode rattish triggers add file` command that watches its folder again.
:::

## Connect a Rattish workflow

A file trigger handles event selection and debounce for you. You can also have an external file-watching tool call `Raticode rattish run WORKFLOW_PATH`. That tool then owns event selection and debounce, and Raticode owns validation and execution.

Start with one watched input folder and a separate output folder. A workflow that writes into its own watched glob can start itself again. Add exclusions, bounded concurrency and clear failure handling before leaving a watcher unattended.

## Events and loops

A Rattish `loop` can read `trigger-events`. A [file trigger](./triggers.md#file-changes) supplies one item per changed file. Declaring that source doesn't start a watcher, and the plain CLI `run` command takes workflow inputs, not trigger events.

## Useful checks

- The watched path exists and its glob matches the files you mean.
- Output and generated files sit outside the watched pattern.
- You've decided whether rapid edits should merge into one run or start separate runs.
- The process has project access and provider credentials.
- After a failed triggered run, read its run log.

See [Loops](./loops.md), [Schedules](./schedules.md) and [Runs](../studio/runs.md).
