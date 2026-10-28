---
title: loop
description: Create runtime iterations from a count, rows, directory files, trigger events, or an infinite source.
pageClass: docs-layout
---

# loop

Create runtime iterations from a count, rows, directory files, trigger events, or an infinite source.

## When to use it

Use a loop when the work items are known only at runtime. The loop routes each iteration into its body; it is not a scheduler or an agent-specific mode.

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

`source.type` accepts `count`, `tabular`, `directory`, `trigger-events`, and `infinite`. Count defaults to one and allows zero. Tabular and directory sources require a path. Directory defaults to glob `*` and no content; trigger events can also opt into content.

Every source defaults to one overlapping iteration and `fail-fast: false`. `source.max-concurrency` limits iterations, while the common node `max-concurrency` limits activations of an individual node. Both limits apply, and a body node's own limit also defaults to one. To run three iterations at once, set `max-concurrency: 3` on the loop source and on each body node; the compiler warns when a body node would hold a parallel loop back. Use limits appropriate for shared file writes.

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

Bind from `node.step.output` inside the body, not from a `loop.*` alias. Iteration fields depend on the source: count items carry `index`; directory items include `path`, `name`, and `directory`, with `file_content` when requested. Refer to the compiled contract before assuming fields for other source types.

Directory and tabular files are opened at activation, after upstream nodes. Zero matching files succeeds with zero body activations. Infinite work needs a break, timeout, or run limit.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `source` | object | Required | Iteration source; its nested fields depend on type. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

The source contract returns an object; the runtime exposes iteration-specific data to body activations. Do not treat every source type as if it has the same fields.

## Preflight and limits

Filesystem-backed sources require a path, but availability is checked when the loop activates. Source sizes and aggregate reads have runtime bounds. Read [Loops and fan-out](../workflows/loops.md) for lifecycle and concurrency details.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
