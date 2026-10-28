---
title: Your first workflow
description: Create and validate a two-step Rattish workflow without an AI provider.
pageClass: docs-layout
---

# Your first workflow

Create a greeting and a summary, and see how Raticode connects source, graph, and execution.

This example uses file nodes, so it needs neither a coding provider nor a shell interpreter. It writes `reports/hello.txt` inside the chosen project when you run it.

## Create a registered workflow

Open a project in the studio and create a workflow in Workflows. From the CLI, the equivalent is:

```bash
gof rattish create "Hello Raticode" --project ./my-project --format json
```

Use a real project folder. The JSON response's `workflow.sourcePath` is the exact file to edit. The ID is allocated by Raticode; do not assume it is the display name with spaces removed.

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

`to` routes execution from the greeting writer to the summary writer. `needs` requires the first writer to complete before the second is ready. `finish: pass` marks the summary as a successful endpoint. Both writers create missing parent directories and replace existing files by default.

Open the graph view. You should see two nodes connected by one route. Layout changes are editor metadata; the executable steps remain in Markdown-independent Rattish source.

## Validate without executing

In the following commands, replace `WORKFLOW_PATH` with the returned source path:

```bash
gof rattish format WORKFLOW_PATH
gof rattish check WORKFLOW_PATH --format json
gof rattish preflight WORKFLOW_PATH --format json
```

`check` should return `ok: true`. Preflight checks the destination paths and their write policies. Neither validation command creates the reports.

## Run when you are ready

Review the path and the overwrite behavior, then choose **Run** in the studio. To execute it from the CLI:

```bash
gof rattish run WORKFLOW_PATH --format json
```

This command performs the file write. It is separate from validation. Inspect the run status, then open `reports/hello.txt` in the editor. It should contain `Hello from Raticode!`.

## Make the next change

Change the writer's `content`, save, validate, and run again. You now have a repeatable process defined by a reviewable text file.

Next, learn [Inputs and bindings](./workflows/inputs.md) to replace the fixed greeting with a workflow input, or use an [agent node](./nodes/agent.md) to generate a report from a project.
