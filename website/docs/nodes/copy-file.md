---
title: copy-file
description: Copy a file, directory, or symlink to a reviewed destination.
pageClass: docs-layout
---

# copy-file

Copy a file, directory, or symlink to a reviewed destination.

## When to use it

Use this node to preserve an original while producing a working copy or deliverable. For renaming without keeping the original, use move-file.

## Example

```rattish
Rattish: 1

Workflow:
  name: Copy a resource

Node step:
  type: copy-file
  source-path: notes/input.txt
  destination-path: reports/input-copy.txt
  overwrite: false
  finish: pass
```

Create the source before preflight, or give the node `needs` on the step that creates it. Missing destination parents are created by default, but an existing destination is not overwritten by default. The output describes source, destination, and resource kind rather than returning file content.

Review directory destinations carefully. Copying a directory is broader than copying one text file, and symlink behavior matters when a link points elsewhere.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `source-path` | string | Required | Source resource path. |
| `destination-path` | string | Required | Destination resource path. |
| `create-dirs` | boolean | `true` | Allow creation of missing destination parents. |
| `overwrite` | boolean | `false` | Allow replacing an existing destination. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `source_path` | string |
| `destination_path` | string |
| `source_name` | string |
| `destination_name` | string |
| `destination_directory` | string |
| `path_kind` | `file`, `directory`, `symlink` |

For example, refer to `node.step.output.source_path` from a compatible downstream binding.

## Preflight and limits

The source must be readable and the destination must meet its write policy. When the node has `needs`, preflight skips the existence check because an upstream step may create the source earlier in the run; the node then reports a missing source when it runs. Parent/child containment and resource-type checks prevent invalid transfers; ordinary filesystem access remains subject to OS permissions.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
