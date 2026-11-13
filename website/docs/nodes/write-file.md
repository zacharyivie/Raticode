---
title: write-file
description: Create, replace, or append a text file using authored content or a bound input.
pageClass: docs-layout
---

# write-file

Create, replace, or append a text file using authored content or a bound input.

## When to use it

Use it for reports and small generated files. Check the path and the overwrite setting before you run it.

## Example

```rattish
Rattish: 1

Workflow:
  name: Write text

Node step:
  type: write-file
  path: reports/message.txt
  content: Hello from Raticode!
  overwrite: false
  finish: pass
```

- By default the node creates missing parent folders and replaces an existing file. The example sets `overwrite: false` to keep an existing report.
- Turn on `append` only when you mean to add to an existing file.
- A value bound to `with.stdin` replaces `content` for that run of the node.
- Writes that don't append replace the file atomically and refuse a symlink destination.
- Output reports what the node did and the byte and character counts.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `content` | string | `""` | Authored text to write. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `create-dirs` | boolean | `true` | Allow creation of missing destination parents. |
| `overwrite` | boolean | `true` | Allow replacing an existing destination. |
| `append` | boolean | `false` | Append instead of replacing text. |

## Inputs and output

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `stdin` | string |

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `action` | `created`, `replaced`, `appended` |
| `bytes_written` | integer (min 0) |
| `characters_written` | integer (min 0) |

Downstream, select a field as `node.step.output.path`.

## Preflight and limits

The destination must fit the folder and overwrite settings, and the encoding must be supported. With `needs`, a missing parent folder is allowed, because an earlier step may create it. Validation never writes the file.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
