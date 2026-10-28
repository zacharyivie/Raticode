---
title: approval-gate
description: Wait for a human decision and route the result explicitly.
pageClass: docs-layout
---

# approval-gate

Wait for a human decision and route the result explicitly.

## When to use it

Use this node before a consequential operation that should require review. It is separate from a provider's native tool approvals.

## Example

```rattish
Rattish: 1

Workflow:
  name: Request approval

Node step:
  type: approval-gate
  message: Is the generated report ready to publish?
  decision-timeout: 10m
  timeout-decision: reject
  notify: false
  finish: pass
```

`message` is required. `subject` can identify the artifact and `approvers` records intended reviewers. With no `decision-timeout`, the gate has no authored decision deadline. A deadline can produce `timeout` or `reject`, as configured.

A rejected or timed-out decision is a successful gate output with `approved: false`, not an operation exception. Branch on `node.step.output.approved` before any action that should require approval. An unconditional route would proceed after rejection too. Notification is optional and defaults off.

If the gate is stopped, or its run ends for another reason while the decision is still pending, the request is closed as cancelled. A cancelled request no longer times out and refuses late decisions.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `message` | string | Required | Decision prompt or explanatory text. |
| `decision-timeout` | string or null | Not set | Optional human-decision deadline. |
| `timeout-decision` | `reject`, `timeout` | `"timeout"` | Decision recorded when the deadline expires. |
| `approvers` | list of string | `[]` | Intended approver identities. |
| `notify` | boolean | `false` | Request an approval notification. |
| `notification-title` | string | `"Raticode approval needed"` | Approval notification title. |
| `subject` | string or null | Not set | Optional approval subject. |

## Inputs and output

Bind these declared ports through `with`:

| Port | Value type |
| --- | --- |
| `message` | string |
| `subject` | string |

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `decision` | `approved`, `rejected`, `timeout` |
| `approved` | boolean |
| `decided_by` | string |
| `notes` | string |
| `message` | string |
| `subject` | string or null |

For example, refer to `node.step.output.decision` from a compatible downstream binding.

## Preflight and limits

The approval request store must be writable. Approval still requires someone to answer through the supported runtime UI; compilation cannot provide the decision.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
