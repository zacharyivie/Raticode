---
title: delete-file
description: Remove a selected file, directory, or symlink with an explicit trash and recursion policy.
pageClass: docs-layout
---

# delete-file

Remove a selected file, directory, or symlink with an explicit trash and recursion policy.

## When to use it

Use this node only when cleanup is part of the reviewed workflow. Avoid broad generated paths or unreviewed directory targets.

## Example

```rattish
Rattish: 1

Workflow:
  name: Delete a resource

Node step:
  type: delete-file
  path: scratch/obsolete.txt
  use-trash: true
  missing-ok: true
  recursive: false
  finish: pass
```

Trash is enabled by default. Set `use-trash: false` only for deliberate permanent deletion. `recursive` defaults to false, and `missing-ok` defaults to false. This example allows an already absent temporary file.

Output distinguishes `trashed`, `deleted`, and `missing`, and includes a trash path when applicable. Do not assume every platform or runtime has usable trash storage.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `use-trash` | boolean | `true` | Use trash rather than permanent removal. |
| `recursive` | boolean | `false` | Permit recursive operation. |
| `missing-ok` | boolean | `false` | Allow an absent target. |

## Inputs and output

This node has no declared input ports. Configure its authored fields directly.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `path_kind` | `file`, `directory`, `symlink`, `missing` |
| `disposition` | `trashed`, `deleted`, `missing` |
| `trash_path` | string or null |

For example, refer to `node.step.output.path` from a compatible downstream binding.

## Preflight and limits

The target must satisfy the missing/recursive policy, and trash must be available when requested. When the node has `needs`, preflight skips the existence check because an upstream step may create the target earlier in the run; the node then reports a missing target when it runs. Stop or a downstream failure does not undo deletion.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
