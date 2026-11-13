---
title: file
description: Output a file's path, name, extension and parent folder without reading it.
pageClass: docs-layout
---

# file

Output a file's path, name, extension and parent folder without reading it.

## When to use it

Use it when an agent or script needs a reviewed file location. When a later step needs the contents, use [read-file](./read-file.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Describe a file

Node step:
  type: file
  path: notes/input.txt
  finish: pass
```

- Create `notes/input.txt` before preflight.
- Bind fields such as `node.step.output.file_path` or `node.step.output.file_extension` downstream.
- Passing a path to an agent doesn't attach the file's contents to the prompt.
- Output is metadata only, never a folder listing or file bytes.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `file_path` | string |
| `file_name` | string |
| `file_stem` | string |
| `file_extension` | string |
| `parent_path` | string |
| `directory` | string |

Downstream, select a field as `node.step.output.path`.

## Preflight and limits

The selected file must exist and be readable. The node does not create the file.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
