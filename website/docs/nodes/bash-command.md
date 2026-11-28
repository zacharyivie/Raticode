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
- `shell` picks the program that runs the command. The default, `auto`, uses PowerShell on Windows and bash elsewhere. Set `shell: bash` when the command is bash, so it runs the same everywhere.
- A nonzero exit code fails the node. For planned recovery, set `allow-fail: true` and add a failure route.
- `with.stdin` supplies standard input. Other bindings become uppercase environment variables, with structured values encoded as JSON.
- Never paste untrusted values into the command text unchecked.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `command` | string | Required | Command text executed by the runtime shell. |
| `working-dir` | string or null | Not set | Working directory resolved from the workflow project. |
| `env` | object | `{}` | Additional string environment values. Unquoted names are read lowercased, so quote a name whose case matters: `"DEBUG": "1"`, not `DEBUG: "1"`. |
| `shell` | `auto`, `bash` or `powershell` | `auto` | The program that runs `command`. See below. |

## Which shell runs the command

| `shell` | Windows | macOS and Linux |
| --- | --- | --- |
| `auto` | Windows PowerShell (`powershell.exe -Command`) | `bash -c` |
| `bash` | Git for Windows' bash | `bash -c` |
| `powershell` | Windows PowerShell | `pwsh -Command`, if PowerShell is installed |

On Windows, `shell: bash` never uses the `bash.exe` in `C:\Windows\System32` or `WindowsApps`. That one starts WSL, which runs the command inside a Linux distribution with different paths, or fails when none is installed. Raticode looks for Git for Windows next to `git.exe` on `PATH`, in the folder its installer records, and in its default install folders, then takes any other `bash.exe` on `PATH`, such as MSYS2's. If it finds none, preflight fails and links to the [Git for Windows download](https://git-scm.com/downloads/win).

The `shell` field is new in Raticode 0.6.0. In the Studio it's the **Shell** field on the node's **Action** tab.

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
| `truncated` | boolean |

Downstream, select a field as `node.step.output.stdout`.

## Output limit

Each of `stdout` and `stderr` holds at most 2,000,000 bytes, counted separately. When the command writes more:

- `stdout` keeps its first 2,000,000 bytes and drops the rest. Nothing is added to it, so a step that reads it through `with: stdin:` gets only the command's own bytes.
- `stderr` keeps its start and its last 64 KiB, since errors usually print last.
- `truncated` is `true`. It's `false` when both streams fit.
- The run's timeline and the node inspector's **Activation history** show a **truncated** entry that names the step and the 2,000,000-byte limit.

When the command exits with a nonzero status, the step's error is the end of `stderr`, or, when `stderr` is empty, the end of `stdout`, read from the real end even when `stdout` was cut.

The `truncated` field is new in 0.6.2. Before it, the two streams shared one 2,000,000-byte budget, a long `stdout` could push out the error text in `stderr`, and a note marking the cut was written into the output. Because the step's contract changed, **Run from here** can't reuse this step from a run made before the change; the step runs again once and later runs reuse it.

## Preflight and limits

Preflight checks that the shell and the working directory exist. A missing bash fails with `RATTISH_PREFLIGHT_RESOURCE_MISSING`, and on Windows its details include `install_url`. Validation doesn't run the command, predict its exit status or limit what it does.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
