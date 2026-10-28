---
title: shell-script
description: Run a Bash script stored in the project, with explicit arguments and environment values.
pageClass: docs-layout
---

# shell-script

Run a Bash script stored in the project, with explicit arguments and environment values.

## When to use it

Keep repeatable shell logic in a reviewed source file and call it from several workflows. This node runs the script through Bash; it is different from the platform command shell used by a command node.

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

Create `scripts/check.sh` before validating this example. A minimal script can contain `echo checked`; make it handle the supplied arguments if they matter. Arguments are passed as an ordered list, not as a single shell command.

`with.stdin` supplies standard input. Other bindings become uppercase environment variables. An unsuccessful exit fails the node, and output includes the resolved script path.

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

Bash must be on PATH and the script must be readable. The script runs with workflow permissions and can perform any effects allowed by that environment.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
