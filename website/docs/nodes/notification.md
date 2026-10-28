---
title: notification
description: Send a desktop, Slack, Teams, webhook, or email notification from a workflow.
pageClass: docs-layout
---

# notification

Send a desktop, Slack, Teams, webhook, or email notification from a workflow.

## When to use it

Use this node to notify a person or service of an outcome. Use an approval gate when the next step must wait for a decision.

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

Desktop is the default channel and needs a usable platform notifier. Slack, Teams, and webhook channels require a configured webhook URL. Email requires its sender, recipient list, and SMTP host; port 587 and STARTTLS are the defaults.

Compatible ports can bind title, body, channel, destination, headers, payload, or protected credentials. Use named secret references instead of embedding webhook secrets or SMTP passwords in source.

Network channels have a request timeout and HTTP retry/status policy. Repeated sends can duplicate notifications. `sent: true` records successful delivery to the channel, not proof a recipient read it.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

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

Bind these declared ports through `with`:

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

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `sent` | constant `true` |
| `title` | string |
| `body` | string |
| `channel` | `desktop`, `slack`, `teams`, `webhook`, `email` |
| `urgency` | `low`, `normal`, `critical` |

For example, refer to `node.step.output.sent` from a compatible downstream binding.

## Preflight and limits

The channel must be configured and available. Headless sessions may have no desktop notifier; network channels need their own connectivity and credentials.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
