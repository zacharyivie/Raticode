---
title: prompt-file
description: Render a text template to a file, and output both its path and the rendered text.
pageClass: docs-layout
---

# prompt-file

Render a text template to a file, and output both its path and the rendered text.

## When to use it

Use it when you want to review or reuse a generated prompt as a file before a provider call.

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

- Use inline `template` or `template-path`, not both.
- `variables` holds string values. Extra `with` bindings add more locals. Refer to them as `{{name}}`.
- By default the node creates missing parent folders and replaces an existing file. Set `overwrite: false` to keep an existing prompt.
- Output includes the rendered `content`, `prompt`, `path` and the input map.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

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

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `content` | string |
| `prompt` | string |
| `inputs` | object |

Downstream, select a field as `node.step.output.path`.

## Preflight and limits

A template file must be readable, and the output path and encoding must be valid. Rendering never calls a provider.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
