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

## Choices and typed replies

*New in Raticode 0.6.0 and Raticode Mobile 0.36.0.*

Give the gate `options` to ask a question with named answers instead of Approve and Reject. Add `reply` when you want the person to type something back.

```rattish
Rattish: 1

Workflow:
  name: Pick a release channel

Node channel:
  type: approval-gate
  message: Where should this build go?
  options:
    - Stable
    - Beta
    - Cancel
  reject-options:
    - Cancel
  reply: optional
  to:
    - publish-stable when node.channel.output.choice == "Stable"
    - publish-beta when node.channel.output.choice == "Beta"

Node publish-stable:
  type: bash-command
  command: echo "Publishing to stable"
  needs: channel

Node publish-beta:
  type: bash-command
  command: echo "Publishing to beta"
  needs: channel
```

- `options` lists 2 to 8 answer labels, each up to 60 characters. The app, the phone's **Approvals** page and `Raticode rattish approve WORKFLOW RUN_ID NODE_ID --choice LABEL` show or take one answer per label instead of Approve and Reject. Labels must differ by more than capital letters. The CLI matches a label whatever its capitals.
- The output's `choice` is the label picked. `approved` is `true` for every option except those in `reject-options`, and `decision` is `approved` or `rejected` to match, so routes on `approved` keep working.
- `reject-options` may only name labels from `options`.
- `reply` is `none` (the default), `optional` or `required`. With `optional` or `required` a reply box appears next to the answers, and the text comes back as the output's `reply`. A required reply has to be filled in before an answer is accepted. On the CLI, pass it with `--reply TEXT`. A gate whose `reply` is `none` refuses a reply; use `--notes` for a note on the decision instead.
- `reply` also works without `options`, next to Approve and Reject.
- Timeouts work the same: the deadline records `timeout` or `rejected`, `choice` is null and `reply` is empty. Route on `node.channel.output.approved` or `decision` to catch them.
- The compiler checks routes that compare `node.<id>.output.choice` with `==` or `!=`. A label that isn't in `options`, or a gate without options, fails with `RATTISH_APPROVAL_CHOICE_UNKNOWN`. A label that differs only in capitals gets a suggestion, because routes compare labels exactly.
- A phone running an older Raticode Mobile doesn't list gates that have options or a required reply. Answer those on the computer, or update the app.

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
| `options` | list of 2 to 8 strings, or null | Not set | Answer labels shown instead of Approve and Reject. |
| `reject-options` | list of string | `[]` | Options that record a rejection (`approved: false`). |
| `reply` | `none`, `optional`, `required` | `"none"` | Ask for a typed reply with the answer. |

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
| `choice` | string or null: the option picked, null without options or on a timeout |
| `reply` | string: the typed reply, empty when none was given |

Downstream, select a field as `node.step.output.decision`.

## Preflight and limits

The approval request store must be writable. Someone still has to answer in the app or another supported place. Compiling can't supply the decision.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
