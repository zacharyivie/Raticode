---
title: File watchers
description: Understand file-triggered automation, debounce, and the current Rattish watcher boundary.
pageClass: docs-layout
---

# File watchers

Watchers respond to filesystem changes instead of requiring a manual run.

::: warning Rattish compatibility boundary
The current `gof watch` service discovers legacy `.toml` workflows in its data directory. It does not discover project-registered `workflow.rattish` sources. Rattish 1 does not define a watch declaration. These docs do not create TOML as a workaround.
:::

## Existing watcher service

For already compatible watcher registrations, `gof watch list` displays the path, glob, mode, concurrency, recursion, and debounce. `gof watch start` runs watchers in the foreground. Ctrl+C stops that process. `--poll-interval` controls filesystem scan frequency, not the workflow's timeout.

The watcher only operates while that process is running. It does not create an OS service or guarantee that a sleeping computer will process every event immediately.

## Connect a Rattish workflow

Use a filesystem service you explicitly configure to call `gof rattish run WORKFLOW_PATH`. The external tool owns event selection and debounce; Raticode owns that invoked workflow's validation and execution.

Start with one watched input directory and a separate output directory. Writing reports back into the same watched glob can retrigger the workflow. Add exclusions, bounded concurrency, and clear failure handling before using the watcher unattended.

## Events and loops

A Rattish `loop` can consume `trigger-events` supplied by a runtime caller. Declaring that source does not start a filesystem watcher. The ordinary CLI `run` command accepts workflow inputs, not a general trigger-event feed.

## Useful checks

- Confirm the watched path exists and its glob matches the intended files.
- Keep output and generated artifacts outside the watched input pattern.
- Decide whether rapid edits should coalesce or produce separate work.
- Verify the executing process has project access and provider credentials.
- Inspect run logs after a failed triggered execution.

See [Loops](./loops.md), [Schedules](./schedules.md), and [Runs](../studio/runs.md).

