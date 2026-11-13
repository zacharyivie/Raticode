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

## Preflight diagnostics

Preflight checks the current machine every time it runs. Typical failures:

- Bash or Python isn't installed
- A script or file can't be read
- A provider executable is missing, or a profile doesn't fit
- A secret value is missing
- A destination can't be written

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
