---
title: loop
description: Create runtime iterations from a count, rows, directory files, trigger events, or an infinite source.
pageClass: docs-layout
---

# loop

Create runtime iterations from a count, rows, directory files, trigger events, or an infinite source.

## When to use it

Use it when the work items are known only at runtime. The loop sends each iteration into its body. It is not a scheduler and not an agent mode.

## Example

```rattish
Rattish: 1

Workflow:
  name: Iterate work

Node step:
  type: loop
  source:
    type: count
    count: 3
    max-concurrency: 1
    fail-fast: true
  to: record

Node record:
  type: bash-command
  command: echo iteration-complete
  needs: step
```

### Sources

| `source.type` | Notes |
| --- | --- |
| `count` | Defaults to 1. Zero is allowed. |
| `tabular` | Needs a path. |
| `directory` | Needs a path. Defaults to glob `*` without file content. |
| `trigger-events` | Can also include content. |
| `infinite` | Needs a break, timeout or run limit. |

### Concurrency

- Every source defaults to one iteration at a time and `fail-fast: false`.
- `source.max-concurrency` limits iterations. The shared node `max-concurrency` limits how often one node runs at once. Both apply, and a body node's own limit also defaults to 1.
- To run three iterations at once, set `max-concurrency: 3` on the loop source and on each body node. The compiler warns when a body node would hold a parallel loop back.
- Keep limits low for nodes that write the same files.

```rattish
Node items:
  type: loop
  source: {"type": "count", "count": 6, "max-concurrency": 3}
  to: work

Node work:
  type: bash-command
  command: sleep 1
  needs: items
  max-concurrency: 3
```

### Iteration data

- Inside the body, bind from `node.step.output`, not a `loop.*` alias.
- Fields depend on the source. Count items carry `index`. Directory items have `path`, `name` and `directory`, plus `file_content` when requested. For other sources, check the compiled contract first.
- Directory and tabular files are opened when the loop starts, after upstream nodes finish.
- Zero matching files succeeds with zero body runs.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `source` | object | Required | Iteration source; its nested fields depend on type. |

## Inputs and output

No input ports. Set its fields directly.

The source returns an object, and each body run gets its own iteration data. Source types don't all share the same fields.

## Preflight and limits

File-based sources need a path, but the path is checked only when the loop starts. The runtime caps source size and total reads. [Loops and fan-out](../workflows/loops.md) covers the lifecycle and concurrency in detail.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
