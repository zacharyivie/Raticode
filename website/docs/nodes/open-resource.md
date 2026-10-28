---
title: open-resource
description: Ask the platform to open a file, folder, URL, or selected application.
pageClass: docs-layout
---

# open-resource

Ask the platform to open a file, folder, URL, or selected application.

## When to use it

Use this node to present a local artifact or open a destination after a workflow completes. Use HTTP request for reading a web response inside the workflow.

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

`resource-type` can be `auto`, `file`, `folder`, `url`, or `app`. Set it explicitly when the interpretation matters. `args` supplies arguments for supported application opening.

Opening a URL launches the configured application; it does not return the page content. A successful launch is not proof that a person read it. Headless machines may lack a usable desktop opener.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `target` | string | Required | File, folder, URL, or application to open. |
| `resource-type` | `auto`, `file`, `folder`, `url`, `app` | `"auto"` | Interpretation of the resource target. |
| `args` | list of string | `[]` | Ordered argument strings. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `target` | string |
| `resource_type` | `file`, `folder`, `url`, `app` |

For example, refer to `node.step.output.target` from a compatible downstream binding.

## Preflight and limits

The target must match its resource type, and the platform opener or application must be available.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
