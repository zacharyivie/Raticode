---
title: CLI reference
description: Use the Raticode CLI to author, validate, inspect, and deliberately run Rattish workflows.
pageClass: docs-layout
---

# CLI reference

The desktop and CLI share workflow contracts. Use the CLI when you want reviewable commands, machine-readable diagnostics, or an external automation entry point.

## Find commands

```bash
Raticode --help
Raticode rattish --help
Raticode rattish docs --format json
```

The last command returns the installed language documentation, contracts, provider definitions, and schemas. These resources describe the installed compiler without requiring a source checkout.

## Rattish commands

| Command | Purpose | Executes nodes? |
| --- | --- | --- |
| `Raticode rattish create "Name" --project PATH --format json` | Register a workflow and return its allocated ID and source path | No |
| `Raticode rattish list --format json` | List registered Rattish workflows | No |
| `Raticode rattish format PATH` | Format an existing source file | No |
| `Raticode rattish check PATH --format json` | Parse and perform static checks | No |
| `Raticode rattish compile PATH --format json` | Compile validated source to JSON IR | No |
| `Raticode rattish inspect-ir PATH --format json` | Inspect the compiled representation | No |
| `Raticode rattish preflight PATH --format json` | Check required runtime resources | No |
| `Raticode rattish run PATH --format json` | Execute the workflow | **Yes** |

Run help on an individual command for its current options. `PATH` is the source file; use the exact source path returned by create for a registered workflow.

`format` edits its target. Other authoring and inspection commands do not execute workflow nodes. Run can write files, call providers, and contact services, according to the authored source.

## Inputs and limits

```bash
Raticode rattish run WORKFLOW_PATH --input 'message="Hello"' --format json
```

Input values after `=` are JSON. Repeat the option for multiple public inputs. Check the input schema and workflow limits before running it. The [inputs guide](../workflows/inputs.md) contains a complete example.

For shell automation, preserve the exit status and machine-readable result. Do not treat a printed message as proof that every node succeeded. A failed request with an uncertain external outcome may need inspection before retrying.

## Provider profiles and health

```bash
Raticode provider --help
Raticode provider list
Raticode doctor --json
```

Provider profiles are reusable deployment settings. Discover supported subscription and permission options in `Raticode provider profile create --help`; authenticate native CLIs separately. A profile does not override a Rattish node's explicit provider, model, or effort.

Doctor checks installation health. Its workflow-specific legacy option is not a replacement for `Raticode rattish preflight`.

## Other command families

| Family | Guide |
| --- | --- |
| `Raticode schedule` | [Schedules](../workflows/schedules.md) |
| `Raticode watch` | [File watchers](../workflows/watchers.md) |
| `Raticode runner` | [Runner queue](../workflows/runner-queue.md) |

These subsystems currently retain legacy workflow-loader boundaries. Use the guides to check compatibility; do not substitute a Rattish path into an older command and assume it will work. New workflow source belongs in `workflow.rattish`.
