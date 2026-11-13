---
title: bash-command
description: Run one command and use its exit status, standard output, and standard error in the workflow.
pageClass: docs-layout
---

# bash-command

Run one command and use its exit status, standard output, and standard error in the workflow.

## When to use it

Use it for a short check, or any command short enough to live in the workflow source. Once it grows big enough for its own file, use [shell-script](./shell-script.md) or [python-script](./python-script.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Run a command

Node step:
  type: bash-command
  command: echo workflow-ready
  working-dir: .
  finish: pass
```

- The working directory defaults to the project root.
- The runtime picks the platform shell, which is PowerShell on Windows. Keep commands portable, or note which platform they need.
- A nonzero exit code fails the node. For planned recovery, set `allow-fail: true` and add a failure route.
- `with.stdin` supplies standard input. Other bindings become uppercase environment variables, with structured values encoded as JSON.
- Never paste untrusted values into the command text unchecked.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `command` | string | Required | Command text executed by the runtime shell. |
| `working-dir` | string or null | Not set | Working directory resolved from the workflow project. |
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

Downstream, select a field as `node.step.output.stdout`.

## Preflight and limits

Preflight checks that the shell and the working directory exist. Validation doesn't run the command, predict its exit status or limit what it does.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
