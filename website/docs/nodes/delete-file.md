---
title: delete-file
description: Remove a selected file, directory, or symlink with an explicit trash and recursion policy.
pageClass: docs-layout
---

# delete-file

Remove a selected file, directory, or symlink with an explicit trash and recursion policy.

## When to use it

Use it only when cleanup is a reviewed part of the workflow. Avoid broad generated paths and folders you haven't checked.

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

- `use-trash` is on by default. Set it to `false` only when you mean to delete for good.
- `recursive` and `missing-ok` both default to `false`. The example allows the temporary file to be gone already.
- Output says `trashed`, `deleted` or `missing`, with the trash path when there is one.
- Not every platform or runtime has a usable trash.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |
| `use-trash` | boolean | `true` | Use trash rather than permanent removal. |
| `recursive` | boolean | `false` | Permit recursive operation. |
| `missing-ok` | boolean | `false` | Allow an absent target. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `path_kind` | `file`, `directory`, `symlink`, `missing` |
| `disposition` | `trashed`, `deleted`, `missing` |
| `trash_path` | string or null |

Downstream, select a field as `node.step.output.path`.

## Preflight and limits

The target must fit the `missing-ok` and `recursive` settings, and trash must be available when you ask for it. With `needs`, preflight skips the existence check, because an earlier step may create the target. A missing target then fails when the node runs. Stopping the run or a later failure doesn't undo a deletion.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
