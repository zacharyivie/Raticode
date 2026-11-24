---
title: "Starter: Daily report"
description: A starter workflow that runs a shell command, has an agent write a short report on its output, and saves it as a dated file.
pageClass: docs-layout
---

# Daily report

Runs a shell command, asks an agent for a short report on what it printed, and saves the report as `reports/YYYY-MM-DD.md`. The default command shows yesterday's commits. Point it at a build log, a test run or a status script instead.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from daily-report --project .
```

On Windows, its shell steps run in Git for Windows' bash (`shell: bash`), so install [Git for Windows](https://git-scm.com/downloads/win) first. Until you do, **Start from** says **Needs Git for Windows** and preflight stops the run with a link to the download. Windows support for starters is new in Raticode 0.6.0.

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `command` | string | `git log --since="1 day ago" --stat` | Shell command the report is about. It runs in the project folder |
| `focus` | string | `What changed, what failed, and what needs attention today.` | What the report should pay attention to |
| `reports-folder` | string | `reports` | Folder for the dated reports |

## How it works

1. `collect` runs `command` with Bash and records its exit code and the last 30,000 characters of output. A failing command doesn't stop the run: the report says it failed.
2. `write-report` (an `agent` node on `claude-code`, without tools) writes a report under 250 words: the overall state, what changed, what failed and what needs attention.
3. `save-report` writes `reports-folder/YYYY-MM-DD.md`. A second run on the same day replaces that day's report.

The workflow sets `max-cost-usd: 0.50`, so a run ends once its estimated model cost passes $0.50, and Claude Code starts `write-report` with a spending cap from that budget. Change it, or set it to `none`, in the workflow's **General** settings. See [Workflow limits](../workflows/rattish.md#workflow-limits).

## Trigger

Run it every weekday at 9:00:

```bash
Raticode rattish triggers add cron PATH/workflow.rattish --cron "0 9 * * 1-5" --timezone Europe/London
```

Set `--input 'command="..."'` on the trigger to give scheduled runs a different command.

## Test set

| Case | What it checks |
| --- | --- |
| `healthy-build` | The output and exit code reach the report, the report file is saved, and (judge) it reports the passing build |
| `failed-command` | A command that exits with code 3 still produces a report, and (judge) the report flags the failure |

Each case makes one model call, plus one per judge check. Reports from the tests go under the workflow's `logs/` folder.

See [Starter workflows](./index.md) and [Triggers](../workflows/triggers.md).
