---
title: Runner queue
description: Separate submission from execution with the existing runner service and understand its Rattish boundary.
pageClass: docs-layout
---

# Runner queue

The runner queue separates asking for a run from the worker process that executes it.

::: warning Rattish compatibility boundary
The current `Raticode runner queue` path resolves workflows through the legacy loader. Registered Rattish workflows use local execution or an explicit `Raticode rattish run` invocation on the target machine. Selecting Runner queue does not add Rattish support to that loader.
:::

## Queue concepts

| Concept | Responsibility |
| --- | --- |
| Submission | Record the workflow request and parameters |
| Labels | Match a request to an appropriate runner |
| Runner | Claim and execute eligible work |
| Status | Inspect queued, running, and terminal records |
| Cancellation | Request that queued or active work stop |

A submitted run hasn't succeeded yet. It can wait forever if no matching worker is available.

## Existing CLI controls

| Command | What it does |
| --- | --- |
| `Raticode runner list` | List known runners |
| `Raticode runner status` | Inspect work, optionally by queued run ID |
| `Raticode runner register --name NAME --label LABEL` | Register a local runner record |
| `Raticode runner start` | Start a worker. `--once` handles at most one eligible run. |
| `Raticode runner cancel RUN_ID` | Ask for a run to be cancelled |
| `Raticode runner queue WORKFLOW` | Submit a legacy workflow ID or TOML path, with optional `--priority`, `--label` and `--param KEY=JSON` |

Each command's `--help` lists its workspace-root, provider capability, priority and label options. Registering a runner doesn't install a provider or copy a project to another machine.

## Remote Rattish execution

To run a Rattish workflow on another machine:

1. Install the standalone CLI there.
2. Make the project and the resources it needs available on that machine.
3. Run check and preflight there.
4. Run `Raticode rattish run` with the source path.

This is a way to deploy the CLI. The local queue is not a network transport. [Desktop fleet](../advanced/fleet.md) is a separate experimental feature for paired devices, with grants local to each target.

## Review failures

Check label matching, whether the project is on that machine, interpreter paths, provider sign-in and the worker's own logs. Cancelling doesn't undo effects that already happened. Keep the evidence from interrupted work before you submit it again.
