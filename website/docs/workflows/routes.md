---
title: Routes, branches and joins
description: Route successes and failures, join branches, and bound repeated workflow execution.
pageClass: docs-layout
---

# Routes, branches and joins

Routes say where execution goes. Readiness says what a node waits for. Rattish keeps the two apart.

## Routes and readiness

- `to` sends an activation to another node.
- `needs` lists what must finish before a node can begin. A dependency alone doesn't create a route.

```rattish
Rattish: 1

Workflow:
  name: Two reports

Node prepare:
  type: write-file
  path: reports/prepared.txt
  content: Ready
  to:
    - left
    - right

Node left:
  type: write-file
  path: reports/left.txt
  content: Left result
  needs: prepare
  to: join

Node right:
  type: write-file
  path: reports/right.txt
  content: Right result
  needs: prepare
  to: join

Node join:
  type: write-file
  path: reports/done.txt
  content: Both branches are ready.
  needs:
    - left
    - right
  finish: pass
```

How this runs:

- `prepare` routes to both branches, so `left` and `right` become eligible.
- A route target without `needs` also runs when the workflow starts. Give each target `needs` on its source so it runs only after that source. Otherwise the compiler warns with `RATTISH_ROUTE_TARGET_AUTO_STARTS`.
- `join` waits until everything in its `needs` is ready.
- Don't depend on a branch that can't be reached. The compiler checks that the required node can take part in the same activation lineage.

## Conditional routes

Routes can have `when` predicates and an `otherwise` fallback.

- Every matching conditional route fires. A list is not a first-match switch.
- `otherwise` matches only when no conditional route from that completion matched.
- Unconditional routes fire independently.

To route `when failed`, set `allow-fail: true` on the node. Without it, the compiler rejects the route as unreachable (`RATTISH_UNREACHABLE_FAILURE_ROUTE`).

```rattish
Rattish: 1

Workflow:
  name: Check repository status

Node check:
  type: bash-command
  command: git status --short
  allow-fail: true
  to:
    - record-error when failed
    - record-success otherwise

Node record-error:
  type: write-file
  path: reports/status.txt
  content: Could not inspect repository status.
  needs: check
  finish: fail

Node record-success:
  type: write-file
  path: reports/status.txt
  content: Repository status checked.
  needs: check
  finish: pass
```

Structured predicates such as `node.review.output.approved` need a successful producer with a compatible output schema. They don't read an agent's prose as a Boolean.

## Cycles and limits

A route can loop back to an earlier node. Set a workflow or node `max-runs`, a timeout, or both, so a repair and review cycle can't repeat forever.

| Setting | What repeats |
| --- | --- |
| `retry-count` | The operation, inside one node run |
| A routed cycle | The whole node, as new node runs |

## Start and finish

- `start: true` is an optional start assertion and layout hint. A start node can't have `needs`.
- `finish: pass` and `finish: fail` mark endpoints. An endpoint can't have outgoing routes.

There are no start, pass or fail node types. Use these fields instead.

See [Loops](./loops.md), [Inputs and bindings](./inputs.md) and [Runs](../studio/runs.md).
