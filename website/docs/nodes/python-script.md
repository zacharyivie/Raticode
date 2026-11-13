---
title: python-script
description: Execute a project Python file without embedding its implementation in Rattish.
pageClass: docs-layout
---

# python-script

Run a Python file from the project, keeping its code out of the workflow source.

## When to use it

Use it to process data, check an artifact, or run Python automation the repository already has.

## Example

```rattish
Rattish: 1

Workflow:
  name: Run a Python script

Node step:
  type: python-script
  script-path: scripts/check.py
  args:
    - --quick
  finish: pass
```

- Create `scripts/check.py` before preflight. `print("checked")` is enough.
- `args` is an ordered list of strings.
- `with.stdin` supplies standard input. Other bindings become uppercase environment variables.
- The node runs the `python` on PATH. Activate the right environment before you start the CLI, or make sure the desktop can see the interpreter and packages you need.
- The packaged backend doesn't install third-party packages for your script.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `script-path` | string | Required | Readable script source path. |
| `args` | list of string | `[]` | Ordered argument strings. |
| `env` | object | `{}` | Additional string environment values. |

## Inputs and output

Input ports, bound through `with`:

| Port | Value type |
| --- | --- |
| `stdin` | string |

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output fields, with their exact selector names:

| Field | Value type |
| --- | --- |
| `stdout` | string |
| `stderr` | string |
| `exit_code` | integer |
| `script_path` | string |

Downstream, select a field as `node.step.output.stdout`.

## Preflight and limits

Preflight needs a readable script and a Python interpreter. Import errors, bugs and nonzero exits still show up only when it runs.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
