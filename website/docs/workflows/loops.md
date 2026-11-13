---
title: Loops and fan-out
description: Iterate counts, rows, files, or trigger events with explicit concurrency and failure behavior.
pageClass: docs-layout
---

# Loops and fan-out

Use a loop when the amount of work depends on a count, a table or the files present at run time.

## Choose a source

| `source.type` | Work items |
| --- | --- |
| `count` | A configured number of iterations |
| `tabular` | Rows from a supported tabular file |
| `directory` | Files matching a glob in a directory |
| `trigger-events` | Event objects supplied when the run starts |
| `infinite` | Iterations until the loop is stopped or limited |

Agent nodes don't fan out on their own. Put the source on a `loop` node and route its activations into the work you want repeated.

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

Two separate concurrency limits apply:

| Setting | Limits |
| --- | --- |
| `source.max-concurrency` | Iterations of this loop running at once |
| The node's `max-concurrency` | Activations of that one node running at once |

Start with one at a time when iterations write to the same files.

## Use iteration data

- Bind from `node.<loop-id>.output` in the loop body. There is no `loop.*` alias.
- The available fields depend on the source type. Confirm a selector with the loop contract and the compiler before using it in a command or prompt.
- Directory and tabular sources open when the loop activates, so an earlier node can create them.
- An empty folder, or a glob that matches nothing, succeeds with zero iterations. A missing or unreadable folder fails.

## Failure and break

Decide on `fail-fast` up front: should one failed item stop the rest, or do you want per-item recovery routes? Retries inside a body node don't change how many items the source produces.

A `break` node targets its loop's ID.

- It closes that loop activation's pending iterations.
- Iterations already running finish. `break` can't cancel them.
- The break's own routes settle after those iterations finish.

Infinite sources and routed cycles need explicit limits. A loop isn't a background scheduler; it belongs to one workflow run.

See [Loop reference](../nodes/loop.md), [Break reference](../nodes/break.md) and [Routes](./routes.md).
