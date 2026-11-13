---
title: workflow
description: Reuse another workflow through its declared public inputs and outputs.
pageClass: docs-layout
---

# workflow

Reuse another workflow through its declared public inputs and outputs.

## When to use it

Use it for a maintained sub-process with a stable interface. The parent sees only the child's public outputs, never its internal node results.

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

- Set exactly one of `workflow-id` or `workflow-path`.
- `version` picks the child's interface version, not a Raticode release. The compiler resolves the child interface and freezes it.
- `with` binds the child's public inputs. The node's output fields are the child's public outputs.
- Changing a child's interface can break its callers. Validate both after an edit.
- The child source below must exist before you check the parent.

## Child source

Save this as `child/workflow.rattish`, relative to the parent's project or source root:

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

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `workflow-id` | string | One target required | Registered child workflow ID; excludes workflow-path. |
| `workflow-path` | string | One target required | Child source path; excludes workflow-id. |
| `version` | integer or null (min 1) | Not set | Requested child interface version. |

## Inputs and output

Input ports and output fields come from the child workflow's public interface. Output follows the child's declared output schema.

## Preflight and limits

Compiling needs a child interface it can resolve. Preflight checks the child's resources too. You still need to review the child's commands, paths and provider settings.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
