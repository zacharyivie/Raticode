---
title: folder
description: Expose a directory path and name as a workflow resource.
pageClass: docs-layout
---

# folder

Expose a directory path and name as a workflow resource.

## When to use it

Use this node to make a directory location available to another step. Use a directory loop for per-file iteration or local-vectorize to build a retrieval index.

## Example

```rattish
Rattish: 1

Workflow:
  name: Describe a folder

Node step:
  type: folder
  path: notes
  finish: pass
```

Create the directory before preflight, or give the node `needs` on the step that creates it. Bind fields such as `node.step.output.folder_path` downstream. This node does not recursively list or attach every file in the directory.

A folder path is context for a consumer, not permission to ignore that consumer's own access controls.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `folder_path` | string |
| `folder_name` | string |
| `parent_path` | string |
| `directory` | string |

For example, refer to `node.step.output.path` from a compatible downstream binding.

## Preflight and limits

The directory must exist and be accessible. When the node has `needs`, preflight skips the existence check because an upstream step may create it earlier in the run; the node then reports a missing directory when it runs. Its contents can still change before later steps read them.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
