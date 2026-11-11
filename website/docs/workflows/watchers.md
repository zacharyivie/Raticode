---
title: File watchers
description: Watch files with Rattish triggers, and understand the older watcher service.
pageClass: docs-layout
---

# File watchers

Watchers respond to filesystem changes instead of requiring a manual run.

To start a `workflow.rattish` when files change, add a [file trigger](./triggers.md#file-changes).

::: warning Rattish compatibility boundary
The `Raticode watch` service discovers legacy `.toml` workflows in its data directory. It doesn't discover `workflow.rattish` sources. Use `Raticode rattish triggers add file` instead.
:::

## Existing watcher service

For already compatible watcher registrations, `Raticode watch list` displays the path, glob, mode, concurrency, recursion, and debounce. `Raticode watch start` runs watchers in the foreground. Ctrl+C stops that process. `--poll-interval` controls filesystem scan frequency, not the workflow's timeout.

The watcher only operates while that process is running. It does not create an OS service or guarantee that a sleeping computer will process every event immediately.

## Connect a Rattish workflow

A file trigger handles event selection and debounce for you. An external filesystem service can also call `Raticode rattish run WORKFLOW_PATH`. That tool then owns event selection and debounce, and Raticode owns the workflow's validation and execution.

Start with one watched input directory and a separate output directory. Writing reports back into the same watched glob can retrigger the workflow. Add exclusions, bounded concurrency, and clear failure handling before using the watcher unattended.

## Events and loops

A Rattish `loop` can consume `trigger-events`. A [file trigger](./triggers.md#file-changes) supplies one item per changed file. Declaring that source doesn't start a watcher by itself, and the ordinary CLI `run` command accepts workflow inputs, not trigger events.

## Useful checks

- Confirm the watched path exists and its glob matches the intended files.
- Keep output and generated artifacts outside the watched input pattern.
- Decide whether rapid edits should coalesce or produce separate work.
- Verify the executing process has project access and provider credentials.
- Inspect run logs after a failed triggered execution.

See [Loops](./loops.md), [Schedules](./schedules.md), and [Runs](../studio/runs.md).

