---
title: "Starter: Notify on condition"
description: A starter workflow that runs a check command and sends a desktop or webhook notification only when its output matches a pattern.
pageClass: docs-layout
---

# Notify on condition

Runs a check command and notifies you only when its output matches a regular expression. The default warns when the project's disk is at least 90% full. It calls no model.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from notify-on-condition --project .
```

On Windows, its shell steps run in Git for Windows' bash (`shell: bash`), so install [Git for Windows](https://git-scm.com/downloads/win) first. Until you do, **Start from** says **Needs Git for Windows** and preflight stops the run with a link to the download. Windows support for starters is new in Raticode 0.6.0.

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `check-command` | string | A `df` command that prints `ALERT: disk is N% full` at 90% or more | Checks something and prints what it found |
| `alert-pattern` | string | `ALERT` | Regular expression. A match sends the notification |
| `title` | string | `Raticode check needs attention` | Notification title |
| `channel` | `desktop` or `webhook` | `desktop` | Where the notification goes |

## How it works

1. `check` runs `check-command` with Bash and keeps the last 4,000 characters of output. If the command fails, it adds `The check command exited with code N.` Add `|exited with code` to `alert-pattern` to be told about that too.
2. When the output matches and `channel` is `desktop`, `notify-desktop` shows a desktop notification with the output as its body.
3. When it matches and `channel` is `webhook`, `post-webhook` posts `{"text": "TITLE\nOUTPUT"}` to the URL in the `ALERT_WEBHOOK_URL` secret. Slack and Microsoft Teams incoming webhooks accept that body.
4. Otherwise `all-clear` records that nothing matched.

## The webhook secret

`post-webhook` reads its URL from `secret["ALERT_WEBHOOK_URL"]`. Desktop alerts don't need it, so the binding has an empty default and the workflow runs without it. To use webhooks, save the URL once:

```bash
Raticode rattish secrets set ALERT_WEBHOOK_URL
```

With `channel` set to `webhook` and no secret saved, `post-webhook` fails the run because the URL is empty. Records show `[REDACTED]` wherever the URL appears. See [Secrets](../workflows/inputs.md#secrets).

## Trigger

Check every 15 minutes:

```bash
Raticode rattish triggers add cron PATH/workflow.rattish --cron "*/15 * * * *"
```

## Test set

| Case | What it checks |
| --- | --- |
| `alert` | Matching output sends a desktop notification with the title |
| `all-clear` | Output that doesn't match sends nothing |
| `failed-check` | A check command that fails sends a notification when the pattern asks for it |

The test set calls no model. `alert` and `failed-check` show real desktop notifications titled "Raticode starter test".

See [Starter workflows](./index.md) and the [notification](../nodes/notification.md) and [http-request](../nodes/http-request.md) nodes.
