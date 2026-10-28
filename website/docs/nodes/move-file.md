---
title: move-file
description: Move or rename a file, directory, or symlink, removing it from its original location.
pageClass: docs-layout
---

# move-file

Move or rename a file, directory, or symlink, removing it from its original location.

## When to use it

Use this node to publish a prepared artifact to its final location. Choose copy-file when you need the source to remain.

## Example

```rattish
Rattish: 1

Workflow:
  name: Move a resource

Node step:
  type: move-file
  source-path: notes/input.txt
  destination-path: reports/moved-input.txt
  overwrite: false
  finish: pass
```

Create the source before preflight, or give the node `needs` on the step that creates it. The destination policy defaults to creating parents and refusing overwrite. Successful output includes both resolved paths and the resource kind.

Moving is a side effect: a later node failure does not automatically put the resource back. Review paths and preserve a recovery copy when appropriate.

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

The source and its parent must permit moving, and the destination must satisfy the directory/overwrite policy. When the node has `needs`, preflight skips the existence check because an upstream step may create the source earlier in the run; the node then reports a missing source when it runs. Invalid containment or type combinations fail.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
