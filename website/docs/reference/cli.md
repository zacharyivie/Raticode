---
title: CLI reference
description: Use the Raticode CLI to author, validate, inspect, and deliberately run Rattish workflows.
pageClass: docs-layout
---

# CLI reference

The desktop and CLI share workflow contracts. Use the CLI when you want reviewable commands, machine-readable diagnostics, or an external automation entry point.

## Find commands

```bash
gof --help
gof rattish --help
gof rattish docs --format json
```

The last command returns the installed language documentation, contracts, provider definitions, and schemas. These resources describe the installed compiler without requiring a source checkout.

## Rattish commands

| Command | Purpose | Executes nodes? |
| --- | --- | --- |
| `gof rattish create "Name" --project PATH --format json` | Register a workflow and return its allocated ID and source path | No |
| `gof rattish list --format json` | List registered Rattish workflows | No |
| `gof rattish format PATH` | Format an existing source file | No |
| `gof rattish check PATH --format json` | Parse and perform static checks | No |
| `gof rattish compile PATH --format json` | Compile validated source to JSON IR | No |
| `gof rattish inspect-ir PATH --format json` | Inspect the compiled representation | No |
| `gof rattish preflight PATH --format json` | Check required runtime resources | No |
| `gof rattish run PATH --format json` | Execute the workflow | **Yes** |

Run help on an individual command for its current options. `PATH` is the source file; use the exact source path returned by create for a registered workflow.

`format` edits its target. Other authoring and inspection commands do not execute workflow nodes. Run can write files, call providers, and contact services, according to the authored source.

## Inputs and limits

```bash
gof rattish run WORKFLOW_PATH --input 'message="Hello"' --format json
```

Input values after `=` are JSON. Repeat the option for multiple public inputs. Check the input schema and workflow limits before running it. The [inputs guide](../workflows/inputs.md) contains a complete example.

For shell automation, preserve the exit status and machine-readable result. Do not treat a printed message as proof that every node succeeded. A failed request with an uncertain external outcome may need inspection before retrying.

## Provider profiles and health

```bash
gof provider --help
gof provider list
gof doctor --json
```

Provider profiles are reusable deployment settings. Discover supported subscription and permission options in `gof provider create --help`; authenticate native CLIs separately. A profile does not override a Rattish node's explicit provider, model, or effort.

Doctor checks installation health. Its workflow-specific legacy option is not a replacement for `gof rattish preflight`.

## Other command families

| Family | Guide |
| --- | --- |
| `gof schedule` | [Schedules](../workflows/schedules.md) |
| `gof watch` | [File watchers](../workflows/watchers.md) |
| `gof runner` | [Runner queue](../workflows/runner-queue.md) |

These subsystems currently retain legacy workflow-loader boundaries. Use the guides to check compatibility; do not substitute a Rattish path into an older command and assume it will work. New workflow source belongs in `workflow.rattish`.
