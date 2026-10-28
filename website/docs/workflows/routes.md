---
title: Routes, branches and joins
description: Route successes and failures, join branches, and bound repeated workflow execution.
pageClass: docs-layout
---

# Routes, branches and joins

Separate where execution goes from the conditions a node needs before it can begin.

## Routes and readiness

`to` sends an activation to another node. `needs` declares one-time readiness requirements. A dependency does not by itself replace an explicit route.

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
  to: join

Node right:
  type: write-file
  path: reports/right.txt
  content: Right result
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

The outgoing routes make both branches eligible. The join waits for its declared readiness conditions. Do not use a dependency on an unreachable branch: the compiler checks whether the required node can participate in the activation lineage.

## Conditional routes

Routes can have `when` predicates and an `otherwise` fallback. All matching conditional routes are eligible. A list is not a first-match switch. `otherwise` matches only when no conditional route from that completion matched; unconditional routes fire independently.

For operation failures, set `allow-fail: true` before routing `when failed`. Otherwise the operation failure ends the workflow rather than following a recovery edge.

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
  finish: fail

Node record-success:
  type: write-file
  path: reports/status.txt
  content: Repository status checked.
  finish: pass
```

Structured output predicates such as `node.review.output.approved` require a successful producer and a declared compatible output schema. They do not read an agent's prose as a Boolean.

## Cycles and limits

A route can return to an earlier node. Set a workflow or node `max-runs` limit, a timeout, or both so a repair/review cycle cannot repeat forever accidentally. `retry-count` repeats an operation attempt inside a node run; a routed cycle creates additional node runs.

## Start and finish

`start: true` is an optional start assertion and presentation hint; it cannot have `needs`. `finish: pass` and `finish: fail` mark endpoints and prohibit outgoing routes. Use these fields instead of inventing start, pass, or fail node types.

See [Loops](./loops.md), [Inputs and bindings](./inputs.md), and [Runs](../studio/runs.md).

