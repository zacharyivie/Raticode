---
title: shell-script
description: Run a Bash script stored in the project, with explicit arguments and environment values.
pageClass: docs-layout
---

# shell-script

Run a Bash script stored in the project, with explicit arguments and environment values.

## When to use it

Use it for shell logic you want in a reviewed file that several workflows can call. It always runs the script with Bash, unlike [bash-command](./bash-command.md), which uses the platform shell.

## Example

```rattish
Rattish: 1

Workflow:
  name: Run a shell script

Node step:
  type: shell-script
  script-path: scripts/check.sh
  args:
    - --quick
  finish: pass
```

- Create `scripts/check.sh` before you validate the example. `echo checked` is enough.
- `args` is an ordered list of strings, not one shell command.
- `with.stdin` supplies standard input. Other bindings become uppercase environment variables.
- A nonzero exit fails the node. The output includes the resolved script path.

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

Bash must be on PATH and the script must be readable. The script runs with the workflow's permissions and can do anything they allow.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
