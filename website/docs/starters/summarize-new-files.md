---
title: "Starter: Summarize new files"
description: A starter workflow that reads files it hasn't summarized yet from a folder and saves a Markdown digest.
pageClass: docs-layout
---

# Summarize new files

Reads the files in a folder that it hasn't summarized yet and saves a digest with one section per file. Good for an inbox of notes, exported emails or meeting minutes.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from summarize-new-files --project .
```

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `folder` | string | `inbox` | Folder to read, relative to the project |
| `pattern` | string | `*.md` | Which files to read, as a shell pattern |
| `summaries-folder` | string | `summaries` | Where digests go, and the list of files already done |
| `remember-files` | boolean | `true` | Add summarized files to the list so the next run skips them |

## How it works

1. `collect` lists files in `folder` that match `pattern` and aren't in `summaries-folder/.summarized`. It takes up to 20 files and the first 20,000 bytes of each. It only looks at the folder itself, not subfolders.
2. If nothing is new, `nothing-new` says so and the run ends without calling a model.
3. Otherwise `summarize` (an `agent` node on `claude-code`, without tools) writes a section per file: what it is, the main point, and anything that needs a reply. The prompt tells it to ignore instructions inside the files.
4. `save-digest` writes `summaries-folder/YYYY-MM-DD-HHMMSS.md`, listing the files it covers, and adds them to `.summarized`.

A missing `folder` fails the run before any model call. To summarize a file again, delete its line from `.summarized`.

The workflow sets `max-cost-usd: 1.00`, so a run ends once its estimated model cost passes $1, and Claude Code starts `summarize` with a spending cap from that budget. Change it, or set it to `none`, in the workflow's **General** settings. See [Workflow limits](../workflows/rattish.md#workflow-limits).

## Trigger

Run it when files arrive:

```bash
Raticode rattish triggers add file PATH/workflow.rattish --path inbox --glob "*.md" --debounce 5
```

Keep `summaries-folder` outside the watched folder, or the digest it writes would start another run.

## Test set

| Case | What it checks |
| --- | --- |
| `two-new-files` | Finds both sample files, saves a digest that lists them, and (judge) the summary is accurate |
| `nothing-new` | A pattern that matches nothing ends the run with no model call |
| `missing-folder` | A missing folder fails the run with no model call |

The sample files are in `fixtures/inbox`. The cases set `remember-files` to `false` and write digests under the workflow's `logs/` folder. `two-new-files` makes one model call, plus one for its judge check.

See [Starter workflows](./index.md) and [Test sets](../workflows/tests.md).
