---
title: copy-file
description: Copy a file, directory, or symlink to a reviewed destination.
pageClass: docs-layout
---

# copy-file

Copy a file, directory, or symlink to a reviewed destination.

## When to use it

Use it to make a working copy or deliverable and keep the original. To rename without keeping the original, use [move-file](./move-file.md).

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

- Create the source before preflight, or add `needs` on the step that creates it.
- By default the node creates missing parent folders and won't overwrite an existing destination.
- Output describes the source, destination and resource kind. It doesn't return file content.
- Check folder copies carefully. A folder copies much more than one file, and symlink settings matter when a link points elsewhere.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `source-path` | string | Required | Source resource path. |
| `destination-path` | string | Required | Destination resource path. |
| `create-dirs` | boolean | `true` | Allow creation of missing destination parents. |
| `overwrite` | boolean | `false` | Allow replacing an existing destination. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `source_path` | string |
| `destination_path` | string |
| `source_name` | string |
| `destination_name` | string |
| `destination_directory` | string |
| `path_kind` | `file`, `directory`, `symlink` |

Downstream, select a field as `node.step.output.source_path`.

## Preflight and limits

The source must be readable and the destination must fit its write settings. With `needs`, preflight skips the existence check, because an earlier step may create the source. A missing source then fails when the node runs. Checks on folder nesting and resource type block invalid copies. Everything else is up to the OS permissions.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
