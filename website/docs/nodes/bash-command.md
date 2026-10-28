---
title: bash-command
description: Run one command and use its exit status, standard output, and standard error in the workflow.
pageClass: docs-layout
---

# bash-command

Run one command and use its exit status, standard output, and standard error in the workflow.

## When to use it

Use this node for a short project check or a command whose complete invocation belongs in the workflow source. Prefer a script node when the operation is large enough to deserve its own file.

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

The working directory defaults to the project root. The installed contract checks shell availability; the runtime selects the platform shell, including PowerShell on Windows. Keep portable commands portable, or document their platform requirement. A nonzero exit code fails the operation. Use `allow-fail: true` and a failure route for deliberate recovery.

Bind text to `with.stdin` for standard input. Other bindings become uppercase environment variables, with structured values encoded as JSON. Do not insert untrusted values into a shell command through unchecked interpolation.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `command` | string | Required | Command text executed by the runtime shell. |
| `working-dir` | string or null | Not set | Working directory resolved from the workflow project. |
| `env` | object | `{}` | Additional string environment values. |

## Inputs and output

Bind these declared ports through `with`:

| Port | Value type |
| --- | --- |
| `stdin` | string |

Additional local bindings are supported. See [Inputs and bindings](../workflows/inputs.md) for delivery and type rules.

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `stdout` | string |
| `stderr` | string |
| `exit_code` | integer |

For example, refer to `node.step.output.stdout` from a compatible downstream binding.

## Preflight and limits

The shell and selected directory must be available. Validation does not run the command, inspect its eventual exit status, or restrict its side effects.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
