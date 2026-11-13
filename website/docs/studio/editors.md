---
title: Code and graph editors
description: Edit project files and Rattish workflows in synchronized code and graph views.
pageClass: docs-layout
---

# Code and graph editors

Write exact behavior in code. Use the graph to see how the steps connect.

## Work with project files

Open **File explorer** in the project sidebar and select a file.

- The code workspace has tabs, find in the current file, split editing, and project-wide search and replace (the **Search** plugin).
- Save with your platform's usual Save shortcut. Until you save, other tools see the version on disk, not your edits.
- Markdown files open in Preview or Edit mode, and HTML files in Browse or Edit mode. Switch to source to change the document.

| Setting | Where |
| --- | --- |
| Font size, line height, indentation, wrapping, minimap, autosave delay, Markdown and HTML default modes | **Settings > Editor** |
| Automatic saves on or off | **Settings > General > Autosave files** |

## Edit a workflow

Open a workflow. The source and the graph are two views of the same Rattish document.

- Use the graph's node controls and inspector for the edits they support.
- Edit `workflow.rattish` directly for exact fields and routes.

<AppScreenshot name="graph-inspector" caption="Select a node to edit it in the Node inspector. Its tabs are General, Action, Inputs, Run and Routes." />

The graph is drawn from the source; it isn't a second definition that runs. Node positions and display settings live in `workflow.metadata.json`, so moving a node never changes a command or route.

<AppScreenshot name="workflow-source" caption="Source on the graph's path bar opens workflow.rattish in its own editor tab." />

## Understand diagnostics

**Problems** lists errors and warnings with their source locations.

- When part of the source is invalid, the graph still shows the pieces it can recover. That doesn't mean the workflow is ready to run.
- An unknown node field is a compile error. A missing provider binary is a preflight problem.

[Validation and preflight](../workflows/validation.md) explains the difference.

## Save and external changes

Save before a Git operation or a command that reads the file.

A workflow save checks the version on disk first, so an old editor buffer can't quietly overwrite a newer edit made elsewhere. If they conflict, compare both versions and choose what to keep.

[Keyboard shortcuts](../reference/shortcuts.md) covers graph navigation and panes. The [Rattish guide](../workflows/rattish.md) explains the source format.

