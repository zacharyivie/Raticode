---
title: Your first workflow
description: Create and validate a two-step Rattish workflow without an AI provider.
pageClass: docs-layout
---

# Your first workflow

Build a two-step workflow that writes a greeting and a summary, and see how source, graph and runs fit together.

It uses file nodes only, so you need no coding provider and no shell interpreter. When you run it, it writes `reports/hello.txt` and `reports/summary.txt` in your project.

## Create a registered workflow

Open a project in the studio and create a workflow in **Workflows**. The CLI equivalent:

```bash
Raticode rattish create "Hello Raticode" --project ./my-project --format json
```

Use a real project folder. The reply's `workflow.sourcePath` is the file to edit. Raticode picks the ID, so don't assume it's the name without spaces.

## Edit the source

Replace the contents of the returned `workflow.rattish` with:

```rattish
Rattish: 1

Workflow:
  name: Hello Raticode

Node write-report:
  type: write-file
  path: reports/hello.txt
  content: Hello from Raticode!
  to: write-summary

Node write-summary:
  type: write-file
  path: reports/summary.txt
  content: The greeting is ready in hello.txt.
  needs: write-report
  finish: pass
```

| Line | What it does |
| --- | --- |
| `to: write-summary` | Routes from the greeting writer to the summary writer |
| `needs: write-report` | The summary waits until the greeting is written |
| `finish: pass` | The summary is a successful end point |

Both writers create missing parent folders and replace existing files by default.

Open the graph view: two nodes joined by one route. Moving nodes only changes editor layout; the steps themselves live in the Rattish source.

## Validate without executing

Replace `WORKFLOW_PATH` with the returned source path:

```bash
Raticode rattish format WORKFLOW_PATH
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

- `check` should return `ok: true`.
- `preflight` checks the destination paths and their write rules.
- Neither command writes the reports.

## Run when you are ready

Check the paths and the overwrite behavior, then choose **Run** in the studio. From the CLI:

```bash
Raticode rattish run WORKFLOW_PATH --format json
```

This one does write the files. Check the run status, then open `reports/hello.txt`. It should say `Hello from Raticode!`.

## Make the next change

Change the writer's `content`, save, validate and run again. The whole process is one text file you can review and rerun.

Next:

- [Inputs and bindings](./workflows/inputs.md): replace the fixed greeting with a workflow input.
- [Agent node](./nodes/agent.md): have an agent write a report about a project.
