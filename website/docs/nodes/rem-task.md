---
title: rem-task
description: Run one of Rem's existing tasks and wait for its result.
pageClass: docs-layout
---

# rem-task

Run one of Rem's existing tasks and wait for its result.

## When to use it

Use this node to chain Rem tasks: each task runs after the previous one finishes, and can be given the previous task's result. The run is the same as pressing Run on the task in Rem, with Rem's worker, independent review, project access checks and task thread.

## Example

```rattish
Rattish: 1

Workflow:
  name: Draft then publish

Node draft:
  type: rem-task
  task-id: "1c308150-37b0-4402-8613-fe427332f64e"
  to: publish

Node publish:
  type: rem-task
  task-id: "c6419914-1515-4326-a670-f110a59dcb71"
  instructions: "Use what the draft step produced: {{draft}}"
  with:
    draft: node.draft.output.result
  needs: draft
```

In the workflow editor, choose the task by title from the Rem task list; you don't need to copy its ID. Quote IDs written by hand.

`instructions` are added to the task's goal for this run only, as a follow-up would be. The task itself isn't changed, so its next scheduled run uses its own goal.

## Waiting, failures and stopping

With `wait: true` (the default) the node waits until Rem finishes the run:

- A run that succeeds passes its result to the next steps.
- A run that fails, couldn't finish, was stopped in Rem or ended with an unknown result fails the node with the run's state and summary. Use `allow-fail` and a `when failed` route to continue anyway.
- A task that needs your answer fails the node with `needs_user: true` in the error details. Answer it in Rem, then run the workflow again.

When the node times out or the workflow is stopped, Rem stops the run the node started.

With `wait: false` the node succeeds as soon as the run is queued and passes on `state: queued`. Stopping the workflow afterwards doesn't stop that run.

A task that is already running for something else isn't taken over: the node fails with `RATTISH_REM_BUSY`. A resumed workflow whose node had started a run that is still going waits for that run instead of starting a second one; its output then has `attached: true`.

Rem must be running. With no Raticode backend running Rem, the node fails with `RATTISH_REM_UNAVAILABLE` before queueing anything. If Rem stops for more than a minute while the node waits, the node fails and stops the run it started.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `task-id` | string | Required | The Rem task to run. |
| `instructions` | string | `""` | Extra instructions for this run only. |
| `wait` | boolean | `true` | Wait for the run to finish and fail the node if it doesn't succeed. |

## Inputs and output

Bind this declared port through `with`:

| Port | Value type |
| --- | --- |
| `instructions` | string |

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `task_id`, `run_id`, `thread_id` | string |
| `title` | string |
| `state` | string: `succeeded`, or `queued` when not waiting |
| `succeeded` | boolean |
| `summary` | string, Rem's short summary of the run |
| `result` | string, the worker's final answer (first 20,000 characters) |
| `artifacts` | list of report and file paths the run saved |
| `attached` | boolean |

A failed node keeps the same fields in its error output, and its error details hold `task_id`, `run_id`, `state`, `thread_id` and `needs_user`.

## Errors

| Code | Meaning |
| --- | --- |
| `RATTISH_PREFLIGHT_REM_TASK_UNAVAILABLE` | The task doesn't exist or was deleted; checked before the workflow starts. |
| `RATTISH_REM_TASK_UNAVAILABLE` | The task was deleted after the workflow started. |
| `RATTISH_REM_UNAVAILABLE` | Rem isn't running, or stopped while the node waited. |
| `RATTISH_REM_BUSY` | The task is already running for something else. |
| `RATTISH_REM_TASK_FAILED`, `RATTISH_REM_TASK_BLOCKED`, `RATTISH_REM_TASK_OUTCOME_UNKNOWN` | The run didn't succeed. |
| `RATTISH_REM_TASK_CANCELLED` | The run was stopped in Rem (error kind `cancelled`). |

## Preflight and limits

The task must exist in this Raticode data folder's Rem. Rem checks the task's project and folder access when the run starts, so a task whose project you removed from Rem ends as couldn't finish, as it would from Rem. Runs use Rem's worker and reviewer models and count toward your provider usage.

Continue with [rem-prompt](./rem-prompt.md) or the [Node reference](./index.md).
