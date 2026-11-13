---
title: notification
description: Send a desktop, Slack, Teams, webhook, or email notification from a workflow.
pageClass: docs-layout
---

# notification

Send a desktop, Slack, Teams, webhook, or email notification from a workflow.

## When to use it

Use it to tell a person or service how something turned out. When the next step must wait for a decision, use [approval-gate](./approval-gate.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Send a notification

Node step:
  type: notification
  title: Raticode workflow
  body: The report is ready for review.
  channel: desktop
  urgency: normal
  finish: pass
```

| Channel | Needs |
| --- | --- |
| `desktop` (default) | A working system notifier |
| `slack`, `teams`, `webhook` | A webhook URL |
| `email` | Sender, recipients and SMTP host. Port 587 and STARTTLS are the defaults. |

- Ports can bind the title, body, channel, destination, headers, payload or credentials.
- Use named secret references. Don't put webhook secrets or SMTP passwords in the source.
- Network channels have a request timeout and an HTTP retry and status policy. Retries can send the same notification twice.
- `sent: true` means the channel accepted it, not that anyone read it.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `title` | string | `"Raticode notification"` | Notification title. |
| `body` | string | `""` | Notification text or HTTP request body. |
| `channel` | `desktop`, `slack`, `teams`, `webhook`, `email` | `"desktop"` | Notification destination channel. |
| `urgency` | `low`, `normal`, `critical` | `"normal"` | Desktop notification urgency. |
| `webhook-url` | string or null | Not set | Configured network notification endpoint. |
| `headers` | object | `{}` | String HTTP headers. |
| `payload` | any JSON value | Not set | Authored notification payload. |
| `email-from` | string or null | Not set | Email sender. |
| `email-to` | list of string | `[]` | Email recipient list. |
| `smtp-host` | string or null | Not set | SMTP server host. |
| `smtp-port` | integer (min 1, max 65535) | `587` | SMTP server port. |
| `smtp-username` | string or null | Not set | SMTP authentication username. |
| `smtp-password` | string or null | Not set | SMTP credential; prefer a protected binding. |
| `smtp-starttls` | boolean | `true` | Request STARTTLS for SMTP. |
| `request-timeout` | string | `"30s"` | Per-request timeout. |
| `retry` | object | `{"attempts": 1, "backoff": "0s", "retry-on-statuses": []}` | Attempt count, backoff, and retry-on-statuses map. |
| `expected-statuses` | list of integer | `[200, 201, 202, 204]` | Accepted HTTP status codes. |
| `network-allowlist` | list of string | `[]` | Configured network destination constraints. |

## Inputs and output

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `title` | string |
| `body` | string |
| `channel` | `desktop`, `slack`, `teams`, `webhook`, `email` |
| `urgency` | `low`, `normal`, `critical` |
| `webhook-url` | string |
| `headers` | object |
| `payload` | any JSON value |
| `email-from` | string |
| `email-to` | list of string |
| `smtp-host` | string |
| `smtp-port` | integer (min 1, max 65535) |
| `smtp-username` | string |
| `smtp-password` | string |
| `smtp-starttls` | boolean |

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `sent` | constant `true` |
| `title` | string |
| `body` | string |
| `channel` | `desktop`, `slack`, `teams`, `webhook`, `email` |
| `urgency` | `low`, `normal`, `critical` |

Downstream, select a field as `node.step.output.sent`.

## Preflight and limits

The channel must be set up and reachable. Headless sessions may have no desktop notifier. Network channels need a connection and their own credentials.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
