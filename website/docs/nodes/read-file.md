---
title: read-file
description: Read a text file and pass its content and path metadata to downstream nodes.
pageClass: docs-layout
---

# read-file

Read a text file and pass its content and path metadata to downstream nodes.

## When to use it

Use it when a later step needs the file's text. The [file](./file.md) node returns metadata only.

## Example

```rattish
Rattish: 1

Workflow:
  name: Read text

Node step:
  type: read-file
  path: notes/input.txt
  encoding: utf-8
  errors: strict
  finish: pass
```

- Create `notes/input.txt` before preflight, or add `needs` on the step that creates it.
- The default is UTF-8 with strict decoding. Pick the file's real encoding. Ignoring decode errors can hide damaged input.
- The runtime's resource limits cap how much a read can load.
- Bind `node.step.output.content` downstream to use the text. Selectors keep underscores, as in `file_name`.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `errors` | string | `"strict"` | Text decoding error policy. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `content` | string |
| `path` | string |
| `file_name` | string |
| `file_stem` | string |
| `file_extension` | string |
| `directory` | string |

Downstream, select a field as `node.step.output.content`.

## Preflight and limits

The path must exist and be readable, and the encoding and error policy must be supported. With `needs`, preflight skips the existence check, because an earlier step may create the file. A missing file then fails when the node runs.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
