---
title: Runner queue
description: Separate submission from execution with the existing runner service and understand its Rattish boundary.
pageClass: docs-layout
---

# Runner queue

The runner queue separates a request to run a workflow from the worker process that executes it.

::: warning Rattish compatibility boundary
The current `gof runner queue` path resolves workflows through the legacy loader. Registered Rattish workflows use local execution or an explicit `gof rattish run` invocation on the target machine. Selecting Runner queue does not add Rattish support to that loader.
:::

## Queue concepts

| Concept | Responsibility |
| --- | --- |
| Submission | Record the workflow request and parameters |
| Labels | Match a request to an appropriate runner |
| Runner | Claim and execute eligible work |
| Status | Inspect queued, running, and terminal records |
| Cancellation | Request that queued or active work stop |

Submission is not successful execution. A queued record can wait indefinitely when no eligible worker is available.

## Existing CLI controls

`gof runner list` lists known runners. `gof runner status` inspects work, optionally by queued run ID. `gof runner register --name NAME --label LABEL` registers a local runner record. `gof runner start` starts a worker, and `--once` processes at most one eligible run. `gof runner cancel RUN_ID` requests cancellation.

Use each command's `--help` for workspace-root, provider capability, priority, and label options. Registration does not install a provider or copy a project to another machine.

## Remote Rattish execution

Install the standalone CLI on the target machine, make the project and its required resources available there, and invoke `gof rattish run` with the source path. Validate and preflight on that machine first.

This is a CLI deployment pattern; it does not imply that the current local queue is a network transport. [Desktop fleet](../advanced/fleet.md) is a separate experimental paired-device feature with target-local grants.

## Review failures

Check label matching, project availability, interpreter paths, provider authentication, and the worker's own logs. Cancellation does not roll back completed effects. Preserve evidence of interrupted work before submitting it again.

