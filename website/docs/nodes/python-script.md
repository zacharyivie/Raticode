---
title: python-script
description: Execute a project Python file without embedding its implementation in Rattish.
pageClass: docs-layout
---

# python-script

Execute a project Python file without embedding its implementation in Rattish.

## When to use it

Use this node for processing data, checking an artifact, or running Python automation already maintained in the repository.

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

Create `scripts/check.py` before preflight. A minimal fixture can contain `print("checked")`. Arguments are an ordered string list. `with.stdin` supplies standard input, and other bindings become uppercase environment variables.

The node uses the `python` executable on PATH. Activate the intended environment before launching the CLI, or ensure the desktop can see the interpreter and dependencies you need. A packaged workflow backend does not automatically install third-party dependencies for your script.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `script-path` | string | Required | Readable script source path. |
| `args` | list of string | `[]` | Ordered argument strings. |
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
| `script_path` | string |

For example, refer to `node.step.output.stdout` from a compatible downstream binding.

## Preflight and limits

Preflight requires a readable script and a Python interpreter. Import failures, script bugs, and unsuccessful exit codes can still occur at runtime.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
