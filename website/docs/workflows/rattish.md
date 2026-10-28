---
title: Rattish language
description: Define readable, validated workflows in workflow.rattish.
pageClass: docs-layout
---

# Rattish language

Rattish is the text format for current Raticode workflows. One source file defines the workflow, its nodes, and the routes between them.

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

This source writes a file only when executed. The version directive chooses the language version. The Workflow block names the process and can set execution limits. Each Node block has a stable ID and a built-in `type`.

## Source conventions

Use `.rattish` files, two-space indentation, and kebab-case node IDs and field names. Keywords are case-insensitive; formatting canonicalizes their spelling. Prompts, commands, paths, model IDs, and data strings preserve case. Comments begin with `#`.

Quote a string that would otherwise be a language literal. For example, `memory: "none"` selects the string value; unquoted `none` represents absence.

Use a block string for a multiline command or prompt:

```text
prompt: |
  Explain the failing tests.
  Give the smallest useful fix.
```

Rattish is its own language. YAML-looking layout does not mean every YAML construct or guessed field is accepted. The node contract decides the legal fields.

## Workflow location

Create a registered workflow with `gof rattish create "Name" --project PROJECT --format json`. Edit its returned `workflow.sourcePath`. Standalone `.rattish` files are also accepted by the CLI.

Registered workflows resolve relative paths from their registered project root. For an unregistered source, its directory is the default root. Authored filesystem paths can also be absolute or home-relative; workflow file nodes follow OS permissions. Prefer project-relative resources for portability and review any path outside the project.

## Routes and data

`to` routes execution, `needs` declares readiness, and `with` binds values for a node activation. Workflow inputs use `input.<name>`. A node's successful result uses `node.<id>.output`; its status and error have separate namespaces.

Read [Routes](./routes.md), [Inputs and bindings](./inputs.md), and [Loops](./loops.md) before adding more complex control flow.

## Find the exact contract

```bash
gof rattish docs --format json
gof rattish format WORKFLOW_PATH
gof rattish check WORKFLOW_PATH --format json
gof rattish preflight WORKFLOW_PATH --format json
```

Installed docs and machine contracts match the CLI doing the compilation. Its diagnostics take precedence over an example for a different version. The [node reference](../nodes/index.md) lists all 22 built-in types.

Current authoring uses Rattish. Older TOML workflow commands are compatibility features and should not be used to create new workflows for these guides.
