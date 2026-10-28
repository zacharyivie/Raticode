---
title: Validation and preflight
description: Separate language errors from missing execution resources and verify a workflow without running it.
pageClass: docs-layout
---

# Validation and preflight

Check what the workflow means, then check whether the machine can execute it.

## The three checks

| Command | Checks | Executes nodes? |
| --- | --- | --- |
| `gof rattish format` | Canonical source spelling and layout | No |
| `gof rattish check` | Grammar, contracts, types, bindings, and graph semantics | No |
| `gof rattish preflight` | Compilation plus deployment resources and readiness | No |

Run them against the actual `workflow.rattish` path:

```bash
gof rattish format WORKFLOW_PATH
gof rattish check WORKFLOW_PATH --format json
gof rattish preflight WORKFLOW_PATH --format json
```

`format` changes the source. Use `format --check` in CI, or `format --stdout` to inspect canonical text without modifying the file.

## Compiler diagnostics

Unknown fields, invalid provider identifiers, incompatible bindings, missing required fields, unreachable failure routes, and contradictory endpoints are source problems. Fix them before investigating credentials or command installation.

Diagnostics have stable codes, severities, and source locations. JSON output includes `ok`, the source path, cache information, and the diagnostic list. A warning can coexist with `ok: true`; read it rather than treating success as an empty review.

## Preflight diagnostics

Preflight checks the current machine each time. Examples include a missing Bash or Python interpreter, an unreadable script or file, an unavailable provider executable, an incompatible profile, missing secret values, and an unwritable destination.

A valid workflow can fail preflight on another machine. Conversely, an authenticated provider cannot fix a source contract error. Preflight does not prove a network request will succeed or that an agent will produce the result you intend.

## Exit codes

For check and preflight, `0` means no error diagnostics, `1` means language or readiness errors, and `2` means source, artifact, or usage failure. Run also reports failed execution with `1`; invalid CLI or declared workflow input uses `2`.

Use `gof rattish inspect-ir WORKFLOW_PATH` to inspect validated internal representation. Do not edit that output as the workflow source.

## Validate examples for your environment

File and script examples need their referenced resources. Agent examples need the selected provider and account. A warning about missing local setup belongs in your review even when a documentation example is structurally correct.

Next: [Runs and approvals](../studio/runs.md) or [Troubleshooting](../reference/troubleshooting.md).

