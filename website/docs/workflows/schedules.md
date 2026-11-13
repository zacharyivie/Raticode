---
title: Schedules
description: Schedule Rattish workflows with triggers, and understand the older scheduler.
pageClass: docs-layout
---

# Schedules

To run a `workflow.rattish` on a schedule, add a [cron trigger](./triggers.md#cron-schedules). Schedules fire only while a scheduler process is running.

::: warning Rattish compatibility boundary
The `Raticode schedule` commands load the legacy workflow configuration. `Raticode schedule add` doesn't accept `workflow.rattish`. Use `Raticode rattish triggers add cron` instead.
:::

## Schedule a Rattish command externally

An OS scheduler or CI system can run a workflow directly. This suits machines where Raticode's scheduler won't be running:

```bash
Raticode rattish run /absolute/project/.raticode/WORKFLOW_ID/workflow.rattish --format json
```

Before you turn the job on:

- Use the absolute path to `Raticode` if the job doesn't inherit your interactive `PATH`.
- Set up the same project files, provider sign-in, environment variables and secrets on that machine.
- Run check and preflight in that same environment.
- Decide what happens when a slow run overlaps the next one.
- Keep the job's output and exit status for diagnosis.

An external schedule runs real work.

## Existing built-in schedules

These commands manage workflows that already use the legacy scheduler:

| Command | What it does |
| --- | --- |
| `Raticode schedule list` | Show registered jobs |
| `Raticode schedule start --foreground` | Run the scheduler in this process |
| `Raticode schedule start` | Start it in the background |
| `Raticode schedule stop` | Stop the background scheduler |
| `Raticode schedule remove WORKFLOW_ID` | Remove a registration |

The scheduler uses APScheduler with a SQLite job store. Jobs stay registered when it stops, but they can't run until it starts again. A saved job is not an installed operating-system service.

## Check the environment

Scheduled runs usually fail for one of these reasons:

- A different working directory
- Missing `PATH` entries
- A sleeping computer
- A provider login that isn't available in the background

Saving a schedule doesn't install dependencies or wake the computer.

See the [CLI reference](../reference/cli.md) and [Validation and preflight](./validation.md). Organization routines are a separate experimental feature, described in [Organizations](../advanced/organizations.md).
