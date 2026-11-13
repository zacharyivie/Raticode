---
title: folder
description: Output a folder's path and name for other steps to use.
pageClass: docs-layout
---

# folder

Output a folder's path and name for other steps to use.

## When to use it

Use it to hand a folder location to another step. To process each file, use a directory [loop](./loop.md). To build a search index, use [local-vectorize](./local-vectorize.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Describe a folder

Node step:
  type: folder
  path: notes
  finish: pass
```

- Create the folder before preflight, or add `needs` on the step that creates it.
- Bind fields such as `node.step.output.folder_path` downstream.
- The node doesn't list the folder's files or attach them.
- Passing a folder path gives no extra access. The step that uses it keeps its own access rules.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `path` | string | Required | Target filesystem path. |

## Inputs and output

No input ports. Set its fields directly.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `path` | string |
| `folder_path` | string |
| `folder_name` | string |
| `parent_path` | string |
| `directory` | string |

Downstream, select a field as `node.step.output.path`.

## Preflight and limits

The folder must exist and be accessible. With `needs`, preflight skips the existence check, because an earlier step may create it. A missing folder then fails when the node runs. Its contents can still change before later steps read them.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
