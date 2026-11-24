---
title: Validation and preflight
description: Separate language errors from missing execution resources and verify a workflow without running it.
pageClass: docs-layout
---

# Validation and preflight

First check what the workflow means, then check whether this machine can run it. None of these commands runs a node.

## The three checks

| Command | Checks | Executes nodes? |
| --- | --- | --- |
| `Raticode rattish format` | Canonical source spelling and layout | No |
| `Raticode rattish check` | Grammar, contracts, types, bindings, and graph semantics | No |
| `Raticode rattish preflight` | Compilation plus deployment resources and readiness | No |

Run them on the real `workflow.rattish` path:

```bash
Raticode rattish format WORKFLOW_PATH
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

`format` rewrites the file. Use `format --check` in CI, or `format --stdout` to see the canonical text without changing anything.

## Compiler diagnostics

These are source problems. Fix them before you look at credentials or installed commands:

- Unknown fields and missing required fields
- Invalid provider identifiers
- Incompatible bindings
- Unreachable failure routes
- Contradictory endpoints

Each diagnostic has a stable code, a severity and a source location. JSON output includes `ok`, the source path, cache information and the diagnostic list. A warning can appear alongside `ok: true`, so read the list even when the check passes.

### Fix hints

::: info Changed in 0.6.0
Fix hints, the plain `ready to run` wording and the grouped repeats below are new in Raticode 0.6.0. Raticode 0.5.5 prints the bare error, for example `Additional properties are not allowed ('contents' was unexpected)`, and preflight says `deployment ready`.
:::

Common mistakes come with a hint under the error, in `suggestions` in JSON output and as `suggestion:` lines in text output. In the Studio editor the hint shows when you hover over the underlined text, and the workflow list's **Invalid** line and the phone's **Run** reason include the "Did you mean" part:

| You wrote | The hint |
| --- | --- |
| `contents: hi` on a `write-file` node | Did you mean 'content'? Then every field the node takes. The error points at the field's own line. |
| `type: write_file` or `Node my_step:` | Did you mean 'write-file'? Rattish names use hyphens, not underscores. |
| `type: write-fil` | Did you mean 'write-file'? |
| `provider: claude` | Did you mean 'claude-code'? Then every provider Raticode knows. |
| `to: sumary` | Did you mean 'summary'? |
| `needs: greet` on a node nothing routes to | Node 'summary' never runs: it waits for 'greet', but no route reaches it. Add `to: summary` to the node that should start it. |
| A file without `Rattish: 1` | Start the file with the line 'Rattish: 1'. |

```text
workflow.rattish:7:3: error RATTISH_UNKNOWN_FIELD: Node 'greet' has no field 'contents'.
  suggestion: Did you mean 'content'?
  suggestion: write-file fields: append, content, create-dirs, encoding, overwrite, path.
```

Codes don't change, so scripts that read them keep working.

## Preflight diagnostics

Preflight checks the current machine every time it runs. Typical failures:

- Bash or Python isn't installed
- A script or file can't be read
- A provider executable is missing, or a profile doesn't fit
- A secret is neither saved in Raticode nor set in the environment, or the keychain holding it is locked. The message names it and says how to [add it](./inputs.md#secrets).
- A destination can't be written

Text output starts with `ready to run on this computer` or `not ready to run on this computer`. When the same problem stops several nodes, such as a missing Jev key, text output prints it once and lists the other lines: `(also line 20)`. JSON output keeps one diagnostic per node.

A valid workflow can still fail preflight on another machine, and a signed-in provider can't fix a source error. Passing preflight doesn't prove a network request will succeed or that an agent will do what you meant.

## Exit codes

`check` and `preflight` use these codes. `run` adds two cases.

| Code | Meaning |
| --- | --- |
| `0` | No error diagnostics |
| `1` | Language or readiness errors. For `run`, also a failed execution. |
| `2` | Source, artifact or usage failure. For `run`, also invalid CLI or workflow input. |

`Raticode rattish inspect-ir WORKFLOW_PATH` shows the validated internal representation. Don't edit that output as source.

## Validate examples for your environment

File and script examples need the files they reference. Agent examples need the chosen provider and a signed-in account. Review warnings about missing local setup even when the example itself is valid.

Next: [Runs and approvals](../studio/runs.md) or [Troubleshooting](../reference/troubleshooting.md).
