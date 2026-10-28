---
title: workflow
description: Reuse another workflow through its declared public inputs and outputs.
pageClass: docs-layout
---

# workflow

Reuse another workflow through its declared public inputs and outputs.

## When to use it

Choose this node for a maintained sub-process with a stable interface. It does not expose the child's private internal node results to the parent.

## Example

```rattish
Rattish: 1

Workflow:
  name: Call a child workflow

Node step:
  type: workflow
  workflow-path: child/workflow.rattish
  version: 1
  with:
    message: Hello from the parent.
  finish: pass
```

Select exactly one of `workflow-id` or `workflow-path`. The optional `version` chooses the public interface version, not the application release version. The compiler resolves and freezes the selected child interface.

`with` binds the child's public inputs. Parent output fields are the child's declared public outputs. Changing a child interface can invalidate callers, so validate both after editing. The child source shown below must exist before checking the parent.

## Child source

Save this as `child/workflow.rattish` relative to the parent's project/source root before validating the parent:

```rattish
Rattish: 1

Workflow:
  name: Save child message
  interface-version: 1
  inputs:
    message:
      schema: {"type": "string"}
      required: true
  outputs:
    saved-path:
      from: node.save.output.path
      schema: {"type": "string"}

Node save:
  type: write-file
  path: reports/child-message.txt
  with:
    stdin: input.message
  finish: pass
```

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `workflow-id` | string | One target required | Registered child workflow ID; excludes workflow-path. |
| `workflow-path` | string | One target required | Child source path; excludes workflow-id. |
| `version` | integer or null (min 1) | Not set | Requested child interface version. |

## Inputs and output

Input ports and output fields come from the referenced child workflow's public interface.

Successful output follows the child's declared public output schema.

## Preflight and limits

Compilation requires a resolvable child interface. Preflight recursively checks its resources. Nested calls do not remove the need to review the child's commands, paths, and provider settings.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
