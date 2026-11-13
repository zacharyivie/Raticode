---
title: open-resource
description: Ask the operating system to open a file, folder, URL or application.
pageClass: docs-layout
---

# open-resource

Ask the operating system to open a file, folder, URL or application.

## When to use it

Use it to show someone a local file or open a page when a workflow finishes. To read a web response inside the workflow, use [http-request](./http-request.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Open a resource

Node step:
  type: open-resource
  target: https://github.com/zacharyivie/Raticode
  resource-type: url
  finish: pass
```

- `resource-type` is `auto`, `file`, `folder`, `url` or `app`. Set it when the guess could go wrong.
- `args` passes arguments to an application, where supported.
- Opening a URL launches the configured app. The node doesn't return the page, and a successful launch doesn't mean anyone read it.
- A URL must start with `http`, `https` or `mailto`. Open a local file with `resource-type: file` instead of a `file://` address.
- Targets other than `app` can't start with `-` or contain control characters, because a target filled in from a trigger, a loop row or another node could otherwise pass options to the system opener.
- Headless machines may have no desktop opener.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `target` | string | Required | File, folder, URL, or application to open. |
| `resource-type` | `auto`, `file`, `folder`, `url`, `app` | `"auto"` | Interpretation of the resource target. |
| `args` | list of string | `[]` | Ordered argument strings. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `target` | string |
| `resource_type` | `file`, `folder`, `url`, `app` |

Downstream, select a field as `node.step.output.target`.

## Preflight and limits

The target must match its resource type, and the platform opener or application must be available.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
