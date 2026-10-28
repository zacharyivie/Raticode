---
title: Code and graph editors
description: Edit project files and Rattish workflows in synchronized code and graph views.
pageClass: docs-layout
---

# Code and graph editors

Use code to state exact behavior and the graph to understand how the steps connect.

## Work with project files

Open Files in the project sidebar and select a file. The code workspace supports tabs, file search, find in the current file, and split editing. Save with your platform's standard Save shortcut. Unsaved text belongs to the editor until it is written to disk; external tools operate on the disk version.

In **Settings > Editor**, choose font size, line height, indentation, wrapping, minimap visibility, and autosave delay. **Settings > General > Autosave files** controls automatic saves.

Markdown files can open in Preview or Edit mode. HTML files can open in Browse or Edit mode. Select the defaults in Editor settings; switch to source when you need to modify the document rather than read its rendered output.

## Edit a workflow

Open a registered workflow. Its source and graph represent the same Rattish document. Use the graph's node controls and inspector for supported edits, or edit `workflow.rattish` directly for exact fields and routes.

The graph is a projection of source, not a second executable definition. Node positions and presentation belong to `workflow.metadata.json`. Moving a node should not change a command or route.

## Understand diagnostics

Problems show errors and warnings with their source locations. A partially invalid buffer can still display the graph pieces the editor can recover. That display does not mean the full workflow is ready to execute.

Validation distinguishes syntax and contract errors from machine readiness. A missing provider binary belongs to preflight; an unknown node field belongs to compilation. Follow [Validation and preflight](../workflows/validation.md) when the error is unclear.

## Save and external changes

Save before using Git operations or running a command that reads the file. Workflow saves check the current disk revision so an old editor buffer cannot silently replace a newer external edit. If a conflict appears, inspect both versions and decide which changes to retain.

Use [Keyboard shortcuts](../reference/shortcuts.md) for graph navigation and pane controls. The [Rattish guide](../workflows/rattish.md) explains the source format.

