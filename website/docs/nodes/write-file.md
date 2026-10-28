---
title: write-file
description: Create, replace, or append a text file using authored content or a bound input.
pageClass: docs-layout
---

# write-file

Create, replace, or append a text file using authored content or a bound input.

## When to use it

Use this node for reports and small generated artifacts. Review its path and replacement behavior before running it.

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

The default creates missing parent directories and allows replacement. This example sets `overwrite: false` so an existing report is preserved. Enable `append` only when adding text to an existing artifact is intended.

Binding `with.stdin` replaces authored `content` for that activation. Non-append writes use an atomic replacement path and refuse a symlink destination. Output reports the actual action and byte/character counts.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `content` | string | `""` | Authored text to write. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `create-dirs` | boolean | `true` | Allow creation of missing destination parents. |
| `overwrite` | boolean | `true` | Allow replacing an existing destination. |
| `append` | boolean | `false` | Append instead of replacing text. |

## Inputs and output

Bind these declared ports through `with`:

| Port | Value type |
| --- | --- |
| `stdin` | string |

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `action` | `created`, `replaced`, `appended` |
| `bytes_written` | integer (min 0) |
| `characters_written` | integer (min 0) |

For example, refer to `node.step.output.path` from a compatible downstream binding.

## Preflight and limits

The destination must satisfy the directory and overwrite policy, and the encoding must be supported. When the node has `needs`, a missing parent directory is allowed because an upstream step may create it. Validation never creates the report.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
