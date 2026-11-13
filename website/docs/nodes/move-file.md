---
title: move-file
description: Move or rename a file, directory, or symlink, removing it from its original location.
pageClass: docs-layout
---

# move-file

Move or rename a file, directory, or symlink, removing it from its original location.

## When to use it

Use it to move a finished file to its final place. To keep the source, use [copy-file](./copy-file.md).

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

- Create the source before preflight, or add `needs` on the step that creates it.
- By default the node creates missing parent folders and won't overwrite an existing destination.
- Output includes both resolved paths and the resource kind.
- A later failure doesn't move anything back. Check the paths, and keep a backup copy when it matters.

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

The source and its parent folder must allow the move, and the destination must fit the folder and overwrite settings. With `needs`, preflight skips the existence check, because an earlier step may create the source. A missing source then fails when the node runs. Invalid nesting or resource types fail.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
