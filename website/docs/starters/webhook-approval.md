---
title: "Starter: Webhook approval"
description: A starter workflow that holds a local webhook call at an approval gate and runs a command only after you approve.
pageClass: docs-layout
---

# Webhook approval

Another program on this computer calls a local webhook. Raticode shows you the request and waits. The workflow runs your command only if you approve; a rejection, or no answer within 24 hours, runs nothing.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from webhook-approval --project .
Raticode rattish triggers add webhook PATH/.raticode/webhook-approval/workflow.rattish
```

On Windows, its shell steps run in Git for Windows' bash (`shell: bash`), so install [Git for Windows](https://git-scm.com/downloads/win) first. Until you do, **Start from** says **Needs Git for Windows** and preflight stops the run with a link to the download. Windows support for starters is new in Raticode 0.6.0.

The trigger command prints the URL and a token once. Call it like this:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"action":"publish-docs","ref":"main"}' http://127.0.0.1:8767/rattish-hooks/TRIGGER_ID
```

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `action-command` | string | `printf 'Approved request: %s\n' "$REQUEST_JSON"` | Command to run after you approve. It gets the webhook body as JSON in `REQUEST_JSON` |
| `decision-note` | string | empty | Extra text shown with each approval request |

Set them on the trigger, for example `--input 'action-command="./scripts/publish.sh"'`.

## How it works

1. `read-request` takes the webhook body from `trigger.events[0].payload`. A run with no body, such as one you start by hand, fails here before asking anything.
2. `approve` is an `approval-gate` that shows the body, sends a desktop notification and waits up to 24 hours. No answer counts as a rejection.
3. If you approve, `act` runs `action-command` with the body in `REQUEST_JSON`. Otherwise `record-rejection` notes that nothing ran.

Treat the body as untrusted. It reaches your command only as an environment variable, never as part of the command text. Parse it in your script, for example with `jq`, and check each field before you use it.

Approve or reject in **Runs**, or with `Raticode rattish approve` and `Raticode rattish reject`. See [Runs and approvals](../studio/runs.md).

## Test set

The cases supply the webhook body with `triggerEvents`, the way a webhook trigger would.

| Case | What you do | What it checks |
| --- | --- | --- |
| `approved` | Approve the request whose body says "Approve this one" | The command ran with the body |
| `rejected` | Reject the request whose body says "Reject this one" | Nothing ran, and the run says so |
| `no-webhook-body` | Nothing | A run without a body fails before asking for approval |

The test set calls no model. The first two cases wait for you to answer in **Runs**.

See [Starter workflows](./index.md), [Triggers](../workflows/triggers.md#webhooks) and the [approval-gate node](../nodes/approval-gate.md).
