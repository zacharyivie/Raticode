---
title: prompt-file
description: Render a text template to a file and expose both its path and rendered text.
pageClass: docs-layout
---

# prompt-file

Render a text template to a file and expose both its path and rendered text.

## When to use it

Use this node when a generated prompt is worth reviewing or reusing as an artifact before a later provider call.

## Example

```rattish
Rattish: 1

Workflow:
  name: Render a prompt file

Node step:
  type: prompt-file
  output-path: prompts/review.txt
  template: Review the {{topic}} implementation.
  variables:
    topic: source control
  finish: pass
```

Choose an inline `template` or a `template-path`; do not author both template sources. `variables` provides string values, and open `with` bindings make additional locals available. Template references use `{{name}}`.

Parent directories are created and the destination can be replaced by default. Set `overwrite: false` when you want to preserve an existing prompt. Output includes rendered `content`, `prompt`, `path`, and the input map.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `output-path` | string | Required | Destination for rendered text. |
| `template` | string | `""` | Inline template text. |
| `template-path` | string or null | Not set | Readable template source file. |
| `variables` | object | `{}` | Authored string template locals. |
| `encoding` | string | `"utf-8"` | Text encoding. |
| `create-dirs` | boolean | `true` | Allow creation of missing destination parents. |
| `overwrite` | boolean | `true` | Allow replacing an existing destination. |

## Inputs and output

Additional local bindings are supported. See [Inputs and bindings](../workflows/inputs.md) for delivery and type rules.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `content` | string |
| `prompt` | string |
| `inputs` | object |

For example, refer to `node.step.output.path` from a compatible downstream binding.

## Preflight and limits

A selected template file must be readable, and the output path and encoding must be valid. Rendering a prompt does not invoke a provider.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
