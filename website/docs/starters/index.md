---
title: Starter workflows
description: Seven ready-made Rattish workflows with typed inputs, a test set and a guide. Copy one into your project from the Studio or with rattish create --from.
pageClass: docs-layout
---

# Starter workflows

A starter is a finished workflow you copy into your project and adjust. Each one has typed inputs with defaults, a test set with sample files, and a guide.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

| Starter | What it does | Model calls | Suggested trigger |
| --- | --- | --- | --- |
| [Summarize new files](./summarize-new-files.md) | Reads files it hasn't seen in a folder and saves a digest | One per run with new files | File |
| [Daily report](./daily-report.md) | Runs a shell command and saves a short dated report on its output | One per run | Cron |
| [Webhook approval](./webhook-approval.md) | Holds a webhook call until you approve, then runs a command | None | Webhook |
| [Test and fix](./test-and-fix.md) | Runs the tests and lets an agent fix failures, up to three times | Up to three | None |
| [Notify on condition](./notify-on-condition.md) | Runs a check and notifies you only when its output matches | None | Cron |
| [Jev demo](./jev-demo.md) | Asks Jev a yes/no and a multiple-choice question about a message and branches on each answer | Up to two Jev calls | None |
| [Summarize a link](./summarize-a-link.md) | Reads the page behind a link shared from your phone or copied on the desktop and sends a summary to your phone | One per page | Phone share or desktop shortcut |

The starters that call a model use the `claude-code` provider. Change `provider` on the agent or common-llm-task node to use another one. **Jev demo** calls the [Jev classifier](../workflows/jev.md) instead and needs a Jev key.

## Start from a starter

In the Studio, choose **New workflow**. Under **Create new**, pick a starter in **Start from**, choose the project folder and click **Create**. The name is optional: leave it empty to use the starter's name. **Blank workflow** creates an empty one as before.

From the command line:

```bash
Raticode rattish starters                                   # list them
Raticode rattish create --from daily-report --project .     # copy one
Raticode rattish create "Morning summary" --from daily-report --project .
```

`rattish starters` lists each starter with a **Needs** line: the provider it calls and any secret it reads, or `nothing beyond Raticode`. The Studio shows the same line under **Start from** when you pick one. The **Needs** line is new in Raticode 0.6.0.

## Starters on Windows

Every starter's shell steps say `shell: bash`, and so do the command checks in their test sets. On Windows they run in the bash that comes with [Git for Windows](https://git-scm.com/downloads/win), never the WSL `bash.exe` in System32. See [bash-command](../nodes/bash-command.md#which-shell-runs-the-command) for how Raticode finds it.

Without Git for Windows, **Start from** lists those starters as **Name (Needs Git for Windows)** and links to the download, and the **Needs** line from `rattish starters` starts with `Git for Windows`. You can still create one, but preflight stops each run until bash is installed. This is new in Raticode 0.6.0.

`create --from` prints the provider the starter uses, any secret it reads, the trigger command to add, and the guide's address. `--format json` returns the same as a `starter` object.

## What gets copied

Raticode copies the starter into `PROJECT/.raticode/<workflow-id>/`:

- `workflow.rattish`, with your name in `Workflow: name`.
- `workflow.tests.json`, the [test set](../workflows/tests.md).
- `fixtures/`, sample files the test set reads, for starters that need them.

The workflow ID comes from the name, so two copies of one starter get different folders. Test cases name their sample files by folder, and Raticode points them at the new folder for you.

The copy is yours: edit it like any other workflow. Updating Raticode doesn't change it.

If you open the Raticode repository itself as a project, the starters in `rattish/starters/` show up in **Workflows** in place. Copy one before running its test set: the tests expect the copy's `.raticode/<id>/` folder.

## Secrets stay references

A starter that needs a credential reads it with a `secret["NAME"]` reference. Copying keeps the reference as text and never looks up or writes a value. Save the value once with **Workflow settings > Secrets** or `Raticode rattish secrets set NAME`. See [Secrets](../workflows/inputs.md#secrets).

## Triggers aren't copied

Triggers belong to the computer, not the workflow file, so a starter can't bring one. Each guide gives the command, for example:

```bash
Raticode rattish triggers add cron PATH/workflow.rattish --cron "0 9 * * 1-5"
```

Or add it in **Workflow settings > Triggers**. See [Triggers](../workflows/triggers.md).

## Test a starter

Open the workflow and choose **Tests** in the bottom panel, or run:

```bash
Raticode rattish test PATH/workflow.rattish --estimate   # what it will cost
Raticode rattish test PATH/workflow.rattish
```

Each case is a real run, so starters that call a model cost what those calls cost, and **Webhook approval** asks you to approve or reject. Test cases write only under the workflow's `logs/` folder, so they don't touch your own files.

Raticode's own CI copies every starter with `rattish create --from`, runs `rattish check`, and runs its test set, on Linux and, when it builds the Windows app, on Windows with Git for Windows' bash. CI has no model, so a stand-in answers agent nodes there and judge checks are skipped. A stand-in also answers jev nodes, so CI never calls Jev.
