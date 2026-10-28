---
title: Loops and fan-out
description: Iterate counts, rows, files, or trigger events with explicit concurrency and failure behavior.
pageClass: docs-layout
---

# Loops and fan-out

Use a loop when the amount of work depends on a count, a table, or the files available at runtime.

## Choose a source

| `source.type` | Work items |
| --- | --- |
| `count` | A configured number of iterations |
| `tabular` | Rows from a supported tabular file |
| `directory` | Files matching a glob in a directory |
| `trigger-events` | Event objects supplied when the run starts |
| `infinite` | Iterations until the loop is stopped or limited |

Agent nodes do not own fan-out. Put the iteration source on a `loop` node and route its activations into the work you want repeated.

## A count loop

```rattish
Rattish: 1

Workflow:
  name: Three checks
  max-runs: 12

Node repeat:
  type: loop
  source:
    type: count
    count: 3
    max-concurrency: 1
    fail-fast: true
  to: check

Node check:
  type: bash-command
  command: echo checked
  needs: repeat
```

`source.max-concurrency` sets overlapping iterations. The common node `max-concurrency` controls overlapping activations of that node. They are separate limits. Start with sequential work when iterations write to the same files.

## Use iteration data

Bind from `node.<loop-id>.output` in the body. Rattish has no `loop.*` alias. Available iteration fields depend on the source type. Use the loop contract and compiler to confirm a selector before putting it in a command or prompt.

Directory and tabular sources are opened when the loop activates, so an earlier node can create them. An empty directory or a glob matching no files succeeds with zero body activations. A missing or unreadable directory fails at activation.

## Failure and break

Set `fail-fast` deliberately. Decide whether one failed item should prevent further work or whether you need per-item recovery routes. Retries inside a body node do not change the number of authored source items.

A `break` node targets the owning loop ID. It closes that loop activation's pending iterations; already running branches finish. There is no option to cancel running iterations through `break`. Its completion routes settle after those branches finish.

Infinite sources and routed cycles need explicit limits. A loop is not a background scheduler: it still belongs to one workflow run.

See [Loop reference](../nodes/loop.md), [Break reference](../nodes/break.md), and [Routes](./routes.md).

