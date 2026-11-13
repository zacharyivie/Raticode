---
title: Rattish language
description: Define readable, validated workflows in workflow.rattish.
pageClass: docs-layout
---

# Rattish language

Rattish is the text format for Raticode workflows. One file holds the workflow, its nodes and the routes between them.

## A complete workflow

```rattish
Rattish: 1

Workflow:
  name: Write a note
  timeout: 2m

Node save-note:
  type: write-file
  path: reports/note.txt
  content: A repeatable process starts with one useful step.
  finish: pass
```

| Part | What it does |
| --- | --- |
| `Rattish: 1` | Picks the language version |
| `Workflow:` | Names the process and can set limits such as `timeout` |
| `Node save-note:` | One step, with a stable ID and a built-in `type` |

The file writes `reports/note.txt` only when you run it.

## Source conventions

- Save files as `.rattish`, indented with two spaces.
- Use kebab-case for node IDs and field names.
- Keywords are case-insensitive, and `format` normalizes their spelling. Prompts, commands, paths, model IDs and data strings keep their case.
- Comments start with `#`.
- Quote a string that would otherwise read as a literal. `memory: "none"` is the string; unquoted `none` means no value.

Use a block string for a multiline command or prompt:

```text
prompt: |
  Explain the failing tests.
  Give the smallest useful fix.
```

Rattish looks like YAML but isn't YAML. Each node's contract decides which fields are legal, so a guessed field fails validation.

## Workflow location

Create a registered workflow with `Raticode rattish create "Name" --project PROJECT --format json`, then edit the `workflow.sourcePath` it returns. The CLI also accepts standalone `.rattish` files.

- A registered workflow resolves relative paths from its project root.
- An unregistered file resolves them from its own folder.
- Absolute and home-relative paths also work, and file nodes follow OS permissions.

Prefer project-relative paths so the workflow moves with the project. Review any path outside it.

## Routes and data

| Field or reference | Meaning |
| --- | --- |
| `to` | Where execution goes next |
| `needs` | What must finish before a node is ready |
| `with` | Values bound for one node activation |
| `input.<name>` | A workflow input |
| `node.<id>.output` | A node's successful result. Status and error have their own namespaces. |

Read [Routes](./routes.md), [Inputs and bindings](./inputs.md) and [Loops](./loops.md) before adding more complex control flow.

## Find the exact contract

```bash
Raticode rattish docs --format json
Raticode rattish format WORKFLOW_PATH
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

The installed docs and contracts match the CLI that compiles your workflow. When an example written for another version disagrees, trust the CLI's diagnostics. The [node reference](../nodes/index.md) lists all 24 built-in types.

Write new workflows in Rattish. The older TOML workflow commands remain only for compatibility.
