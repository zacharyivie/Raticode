---
title: file
description: Expose a file path, name, extension, and parent metadata without reading its text.
pageClass: docs-layout
---

# file

Expose a file path, name, extension, and parent metadata without reading its text.

## When to use it

Use a file node when an agent or script needs a reviewed file location. Use read-file when the downstream step needs the contents themselves.

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

Create `notes/input.txt` before preflight. Bind fields such as `node.step.output.file_path` or `node.step.output.file_extension` downstream. Passing a path does not automatically attach its contents to an agent prompt.

The output is metadata, not a directory listing or binary payload.

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
| `file_path` | string |
| `file_name` | string |
| `file_stem` | string |
| `file_extension` | string |
| `parent_path` | string |
| `directory` | string |

For example, refer to `node.step.output.path` from a compatible downstream binding.

## Preflight and limits

The selected file must exist and be readable. The node does not create the file.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
