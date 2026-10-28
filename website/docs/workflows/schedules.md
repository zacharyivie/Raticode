---
title: Schedules
description: Understand Raticode's persisted scheduler and the current Rattish scheduling boundary.
pageClass: docs-layout
---

# Schedules

A schedule repeats work at configured times while a scheduler process is available.

::: warning Rattish compatibility boundary
The current `gof schedule` commands load the legacy workflow configuration. Rattish 1 has no cron declaration, and `gof schedule add` is not a supported registration command for `workflow.rattish`. Keep new workflow source in Rattish.
:::

## Schedule a Rattish command externally

For current Rattish workflows, configure your OS scheduler or CI system to run:

```bash
gof rattish run /absolute/project/.raticode/WORKFLOW_ID/workflow.rattish --format json
```

Use an absolute executable path if the scheduled environment does not inherit your interactive PATH. Configure the same project resources, provider authentication, environment variables, and secrets on the executing machine. Test check and preflight under that environment before enabling the job.

An external schedule executes real work. Choose overlap behavior deliberately so a slow run does not collide with the next occurrence. Keep the process output and exit status for diagnosis.

## Existing built-in schedules

For a workflow already using the compatible legacy scheduler, `gof schedule list` shows registered jobs. `gof schedule start --foreground` runs the scheduler in the current process; `gof schedule start` starts its background mode. `gof schedule stop` stops that background scheduler. `gof schedule remove WORKFLOW_ID` removes a registration.

The scheduler uses APScheduler and a persisted SQLite job store. Jobs remain registered when the scheduler stops, but they cannot execute while their scheduler process is unavailable. Do not interpret a saved job as an installed operating-system service.

## Check the environment

Scheduling problems often come from a different working directory, missing PATH entries, a sleeping computer, or a provider login unavailable in the background environment. Saving a schedule does not install dependencies or wake a computer.

Use [CLI reference](../reference/cli.md) and [Validation and preflight](./validation.md). Organization routines are a separate experimental feature described in [Organizations](../advanced/organizations.md).

