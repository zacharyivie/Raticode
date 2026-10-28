---
title: Runs and approvals
description: Validate workflows, inspect run timelines, review outputs, and resolve approval steps.
pageClass: docs-layout
---

# Runs and approvals

A workflow run records the actual steps taken, including branch choices, node failures, and repeated activations.

## Before starting

Save the workflow and review its commands, destinations, agent prompts, and overwrite settings. Validate it, then check preflight. Compilation proves the source is accepted; preflight checks whether this machine can run it. Neither executes the workflow.

For a registered Rattish workflow, the supported desktop execution target is local. The legacy runner queue is a separate path described in [Runner queue](../workflows/runner-queue.md).

## Start and inspect

Choose **Run** for the workflow you intend to execute. If it declares inputs, supply values matching their schemas. Open the bottom panel:

| View | Use it for |
| --- | --- |
| Problems | Source errors and readiness diagnostics |
| Run Timeline | The sequence of node activations and their outcomes |
| Runs | Current and saved run records |
| Terminal | Commands you run manually |

Select a run and inspect individual node outputs and errors. A routed failure can be expected behavior when `allow-fail: true` sends it to a recovery branch. The final workflow outcome and the result of one node are different facts.

## Repeated nodes

Cycles and loops can activate a node more than once. The runtime records activation lineage so repeated work can be related to the branch or loop iteration that created it. Do not interpret a graph node's single visual location as proof it ran only once.

## Stop and side effects

Use the selected run's Stop action to request cancellation. A completed file write, command, or network request is not undone by cancellation. Inspect the workspace before restarting interrupted automation.

## Approval steps

An `approval-gate` creates a decision request. Review its message and subject before approving or rejecting. Workflow routes can inspect the returned decision, including timeout. Provider tool approvals are a separate system controlled by the coding CLI's permissions.

See the [approval-gate reference](../nodes/approval-gate.md) for fields and [Routes](../workflows/routes.md) for conditional decisions. Registered workflows retain their managed run artifacts in their workflow folder; standalone sources use application storage.

