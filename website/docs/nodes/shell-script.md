---
title: shell-script
description: Run a bash or PowerShell script stored in the project, with explicit arguments and environment values.
pageClass: docs-layout
---

# shell-script

Run a bash or PowerShell script stored in the project, with explicit arguments and environment values.

## When to use it

Use it for shell logic you want in a reviewed file that several workflows can call. By default it runs the script with bash, on every platform.

## Example

```rattish
Rattish: 1

Workflow:
  name: Run a shell script

Node step:
  type: shell-script
  shell: bash
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
| `env` | object | `{}` | Additional string environment values. Unquoted names are read lowercased, so quote a name whose case matters: `"DEBUG": "1"`, not `DEBUG: "1"`. |
| `shell` | `auto`, `bash` or `powershell` | `auto` | The program that runs the script. See below. |

## Which shell runs the script

- `auto` runs `bash` from `PATH`, as shell-script always has. On Windows that can be the WSL launcher in System32.
- `bash` runs bash. On Windows it's Git for Windows' bash, found the same way as for [bash-command](./bash-command.md#which-shell-runs-the-command), and never the WSL launcher. Use it for scripts that should run on Windows too.
- `powershell` runs the script with `-File` in Windows PowerShell, or in `pwsh` on macOS and Linux. PowerShell wants a `.ps1` file.

The `shell` field is new in Raticode 0.6.0.

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
| `truncated` | boolean |

Downstream, select a field as `node.step.output.stdout`.

## Output limit

Each of `stdout` and `stderr` holds at most 2,000,000 bytes, counted separately. When the script writes more:

- `stdout` keeps its first 2,000,000 bytes and drops the rest. Nothing is added to it, so a step that reads it through `with: stdin:` gets only the script's own bytes.
- `stderr` keeps its start and its last 64 KiB, since errors usually print last.
- `truncated` is `true`. It's `false` when both streams fit.
- The run's timeline and the node inspector's **Activation history** show a **truncated** entry that names the step and the 2,000,000-byte limit.

When the script exits with a nonzero status, the step's error is the end of `stderr`, or, when `stderr` is empty, the end of `stdout`, read from the real end even when `stdout` was cut.

The `truncated` field is new in 0.6.2. Before it, the two streams shared one 2,000,000-byte budget, a long `stdout` could push out the error text in `stderr`, and a note marking the cut was written into the output. Because the step's contract changed, **Run from here** can't reuse this step from a run made before the change; the step runs again once and later runs reuse it.

## Preflight and limits

The shell must be installed and the script must be readable. When `shell: bash` finds no bash on Windows, preflight fails and links to the [Git for Windows download](https://git-scm.com/downloads/win). The script runs with the workflow's permissions and can do anything they allow.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
