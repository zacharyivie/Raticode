---
title: Schedules
description: Schedule Rattish workflows with cron triggers or with an outside scheduler.
pageClass: docs-layout
---

# Schedules

To run a `workflow.rattish` on a schedule, add a [cron trigger](./triggers.md#cron-schedules). Schedules fire only while a scheduler process is running.

::: info The older scheduler is gone from 0.6.1
Raticode 0.6.0 and earlier also had `Raticode schedule` commands, which scheduled old TOML workflows only. From 0.6.1 those commands and their scheduler are gone, and Raticode no longer reads `schedules.db` in the data folder. The file stays on disk. [Convert the old workflow](./rattish.md#convert-a-toml-workflow), and the converter prints the `Raticode rattish triggers add cron` command that schedules it again.
:::

## Schedule a Rattish command externally

An OS scheduler or CI system can run a workflow directly. This suits machines where Raticode's scheduler won't be running:

```bash
Raticode rattish run /absolute/project/.raticode/WORKFLOW_ID/workflow.rattish --format json
```

Before you turn the job on:

- Use the absolute path to `Raticode` if the job doesn't inherit your interactive `PATH`.
- Set up the same project files, provider sign-in, environment variables and secrets on that machine. Saved [workflow secrets](./inputs.md#secrets) stay on the computer that saved them.
- Run check and preflight in that same environment.
- Decide what happens when a slow run overlaps the next one.
- Keep the job's output and exit status for diagnosis.

An external schedule runs real work.

## Check the environment

Scheduled runs usually fail for one of these reasons:

- A different working directory
- Missing `PATH` entries
- A sleeping computer
- A provider login that isn't available in the background

Saving a schedule doesn't install dependencies or wake the computer.

See the [CLI reference](../reference/cli.md) and [Validation and preflight](./validation.md). Organization routines are a separate experimental feature, described in [Organizations](../advanced/organizations.md).
