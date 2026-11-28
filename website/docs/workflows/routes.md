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

### Conditions on untyped data

Some values have no declared type, such as an `http-request` node's `json` or a member of an input declared only as an object. From 0.6.2, a condition on them compiles with any operator, and the values' JSON types decide when the route runs:

```rattish
Rattish: 1

Workflow:
  name: Sort an issue

Node fetch:
  type: http-request
  method: get
  url: https://example.com/issue
  to:
    - triage when node.fetch.output.json.labels contains "bug"
    - archive when node.fetch.output.json.state == "closed"
    - review otherwise

Node triage:
  type: write-file
  path: reports/issue.txt
  content: A bug to triage.
  needs: fetch

Node archive:
  type: write-file
  path: reports/issue.txt
  content: Closed.
  needs: fetch

Node review:
  type: write-file
  path: reports/issue.txt
  content: Needs a look.
  needs: fetch
```

- `==` and `!=` never match values of different JSON types. `true` doesn't equal `1`, and `"10"` doesn't equal `10`. `10` equals `10.0`.
- `<`, `<=`, `>` and `>=` need two numbers or two strings. `matches` needs two strings. `contains` looks for a string in a string or among an object's keys, and for any value in a list.
- A missing value makes any comparison false. A null value makes `<`, `<=`, `>`, `>=`, `contains` and `matches` false. Either way `otherwise` can catch it.
- Any other mix stops the run with `RATTISH_RUNTIME_PREDICATE_TYPE_MISMATCH`, such as `>` on the text `"10"`. The error names the node, the route target, the operator and both types. It never shows the values.

When both sides have a declared type, the compiler still checks them, so `node.fetch.output.status == "200"` is a compile error (`RATTISH_PREDICATE_TYPE_MISMATCH`): `status` is a number. The same goes for `contains` with a declared string or object on the left and a declared non-string on the right, such as `node.fetch.output.body contains node.fetch.output.status` (from 0.6.2).

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
