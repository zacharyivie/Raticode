---
title: File watchers
description: Watch files with Rattish triggers, and understand the older watcher service.
pageClass: docs-layout
---

# File watchers

To start a `workflow.rattish` when files change, add a [file trigger](./triggers.md#file-changes).

::: warning Rattish compatibility boundary
The `Raticode watch` service discovers legacy `.toml` workflows in its data directory. It doesn't discover `workflow.rattish` sources. Use `Raticode rattish triggers add file` instead.
:::

## Existing watcher service

For existing legacy registrations:

- `Raticode watch list` shows each path, glob, mode, concurrency, recursion and debounce.
- `Raticode watch start` runs the watchers in the foreground. Ctrl+C stops them.
- `--poll-interval` sets how often the filesystem is scanned. It isn't the workflow's timeout.

Watchers work only while that process runs. It isn't an OS service, and a sleeping computer may not handle every event right away.

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
