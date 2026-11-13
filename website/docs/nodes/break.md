---
title: break
description: Close pending iterations of a named loop without cancelling branches already running.
pageClass: docs-layout
---

# break

Close pending iterations of a named loop without cancelling branches already running.

## When to use it

Put it inside the loop's body for when a condition says enough work is done. To cancel the whole run, use the workflow's Stop instead.

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
  needs: repeat
  loop: repeat
  message: Enough work has been done.
```

- `loop` must name a loop this node runs inside, in the same activation lineage.
- A break node can't have outgoing `to` routes.
- It cancels that loop's pending iterations. Running branches finish first, then the loop's completion routes fire.
- The example stops an infinite loop on its first iteration. Real workflows usually route to a break on a condition.
- `message` records why the loop stopped in the result.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `loop` | string | Required | Owning loop node ID. |
| `message` | string | `""` | Decision prompt or explanatory text. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `loop` | string |
| `message` | string |

Downstream, select a field as `node.step.output.loop`.

## Preflight and limits

The compiler checks the loop target and where the break sits in the graph. It needs no executable or credentials.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
