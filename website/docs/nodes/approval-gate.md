---
title: approval-gate
description: Wait for a human decision and route the result explicitly.
pageClass: docs-layout
---

# approval-gate

Wait for a human decision and route the result explicitly.

## When to use it

Put it before a step that should need review. It is separate from a provider's own tool approvals.

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

- `message` is required. `subject` names the thing under review, and `approvers` records who should review it.
- Without `decision-timeout`, the gate waits with no deadline. With one, the deadline produces `timeout` or `reject`, whichever you set.
- A rejection or timeout is still a successful gate run, with `approved: false`. Branch on `node.step.output.approved` before the guarded step. A route without a condition goes ahead after a rejection too.
- `notify` is off by default.
- If the gate is stopped, or its run ends while the decision is pending, the request closes as cancelled. A cancelled request no longer times out and refuses late decisions.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

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

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `message` | string |
| `subject` | string |

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `decision` | `approved`, `rejected`, `timeout` |
| `approved` | boolean |
| `decided_by` | string |
| `notes` | string |
| `message` | string |
| `subject` | string or null |

Downstream, select a field as `node.step.output.decision`.

## Preflight and limits

The approval request store must be writable. Someone still has to answer in the app or another supported place. Compiling can't supply the decision.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
