---
title: Runs and approvals
description: Validate workflows, inspect run timelines, review outputs, and resolve approval steps.
pageClass: docs-layout
---

# Runs and approvals

A run records the steps that actually happened: branch choices, node failures and repeated activations.

## Before starting

1. Save the workflow.
2. Review its commands, destinations, agent prompts and overwrite settings.
3. Validate it: the source compiles.
4. Check preflight: this machine can run it.

Neither check runs the workflow.

In the desktop app, run Rattish workflows with **Local**. Run preview's **Remote** target queues the run for the legacy runner queue, which loads workflows through the legacy loader; see [Runner queue](../workflows/runner-queue.md).

## Start and inspect

Choose **Run**. **Run preview** opens first and lists:

- each node's side effects
- the provider CLIs its agent nodes need
- the order the nodes run in

If the workflow declares inputs, fill in values that match their schemas. Then choose **Run workflow**.

<AppScreenshot name="run-preview" caption="Run preview for a four-node workflow. Nothing runs until you choose Run workflow." />

Open the bottom panel:

| View | Use it for |
| --- | --- |
| Problems | Source errors and readiness diagnostics |
| Run Timeline | The sequence of node activations and their outcomes |
| Runs | Current and saved run records |
| Terminal | Commands you run manually |

Select a run to see each node's output and errors. A failed node isn't always a failed run: with `allow-fail: true`, a failure can route to a recovery branch on purpose. Check the workflow's final outcome separately from any one node's result.

<AppScreenshot name="studio-workflow-run" caption="A finished run. run-tests shows its allowed failure, which routed to fix; the other nodes succeeded, and Run Timeline lists every activation." />

## Repeated nodes

Cycles and loops can run a node more than once. Each activation records which branch or loop iteration started it. One box on the graph doesn't mean the node ran once.

## Stop and side effects

**Stop** on the selected run asks it to cancel. Cancelling doesn't undo a file write, command or network request that already finished, so check the workspace before you restart.

## Approval steps

An `approval-gate` node asks you for a decision.

- Read its message and subject before you approve or reject.
- Routes can branch on the decision, including a timeout.
- Provider tool approvals are separate. The coding CLI's own permissions control those.

Fields are in the [approval-gate reference](../nodes/approval-gate.md); conditional routes are in [Routes](../workflows/routes.md).

Run files for a workflow in a project are kept in its workflow folder. Standalone sources keep them in app storage.

