---
title: read-file
description: Read a text file and pass its content and path metadata to downstream nodes.
pageClass: docs-layout
---

# read-file

Read a text file and pass its content and path metadata to downstream nodes.

## When to use it

Use this node when another step needs actual text. The file node returns metadata only and is not a substitute for reading contents.

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

Create `notes/input.txt` before preflight, or give the node `needs` on the step that creates it. UTF-8 with strict decoding is the default. Select the encoding that matches the file; suppressing decoding failures can conceal damaged input. File reads are bounded by the runtime resource limits.

Bind `node.step.output.content` downstream to consume the text. Output selectors preserve underscore field names such as `file_name`.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `errors` | string | `"strict"` | Text decoding error policy. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `content` | string |
| `path` | string |
| `file_name` | string |
| `file_stem` | string |
| `file_extension` | string |
| `directory` | string |

For example, refer to `node.step.output.content` from a compatible downstream binding.

## Preflight and limits

The path must exist and be readable, and the encoding/error policy must be supported. When the node has `needs`, preflight skips the existence check because an upstream step may create the file earlier in the run; the node then reports a missing file when it runs.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
