---
title: break
description: Close pending iterations of a named loop without cancelling branches already running.
pageClass: docs-layout
---

# break

Close pending iterations of a named loop without cancelling branches already running.

## When to use it

Use a break node inside the targeted loop's body when a condition says enough work has been done. Use workflow Stop when you mean cancellation of the whole run.

## Example

```rattish
Rattish: 1

Workflow:
  name: Stop pending iterations

Node repeat:
  type: loop
  source:
    type: infinite
    max-concurrency: 1
  to: step

Node step:
  type: break
  loop: repeat
  message: Enough work has been done.
```

The target must be an existing loop in the applicable activation lineage. A break node cannot have outgoing `to` routes. It stops pending iterations of that loop activation; active branches finish, and completion routes settle after they do.

This example breaks an infinite loop as soon as its first body activation reaches the break node. Real workflows usually route to it conditionally. The message explains the decision in the result.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `loop` | string | Required | Owning loop node ID. |
| `message` | string | `""` | Decision prompt or explanatory text. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `loop` | string |
| `message` | string |

For example, refer to `node.step.output.loop` from a compatible downstream binding.

## Preflight and limits

The compiler validates the loop target and control-flow placement. There is no separate executable or credential dependency.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
