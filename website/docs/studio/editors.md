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

### Add a node

*New in Raticode 0.6.0. Up to 0.5.5, Add node adds a command node straight away.*

Open the node picker in any of these ways:

- **Add node** (the **+** button) in the graph toolbar.
- **Add node** in the middle of an empty graph.
- **Tab** while the graph has focus, for example after you click a node or an empty part of the graph. Shift+Tab still moves focus out of the graph.

The picker groups nodes by what they do: **Run code**, **Files**, **AI**, **Web**, **Approvals and notices**, **Flow control** and **Other workflows**. Each node has a one-line description and a book icon that opens its page in the [node reference](../nodes/index.md).

- Type to search names, descriptions and group names. Up and Down choose a node, Enter adds it and Escape closes the picker.
- If a node is selected, the new node goes to its right and the selected node routes on to it: Raticode adds the new node to the selected node's `to:` list and keeps the routes that were there. With nothing selected, the new node is added without a route.
- A node with `finish:` set, or a `break` node, can't have outgoing routes, so a node added after one isn't routed.
- New nodes are named after their type, such as `read-file-1`. Rename one in the inspector's **ID** field.

Every choice is an edit to `workflow.rattish` that Raticode checks before saving. If the workflow compiled before, an edit that would stop it compiling isn't saved, and the toolbar says why. Each node starts with fields that compile, such as an `echo` command or, for a `break` node, the loop it follows. A `workflow` node needs another workflow in the project with an interface version, and a `rem-task` node needs one of Rem's tasks.

The inspector's **Type** field opens the same picker, with the node's current type marked. Choosing a different type keeps the node's ID, routes and shared settings such as `needs` and `timeout`, drops the old type's own fields and fills in starting fields for the new type.

The list, groups and descriptions come from the installed Rattish node contracts, so a node type that Raticode adds appears in the picker without anything else changing.

## Understand diagnostics

**Problems** lists errors and warnings with their source locations.

- When part of the source is invalid, the graph still shows the pieces it can recover. That doesn't mean the workflow is ready to run.
- An unknown node field is a compile error. A missing provider binary is a preflight problem.

[Validation and preflight](../workflows/validation.md) explains the difference.

## Save and external changes

Save before a Git operation or a command that reads the file.

A workflow save checks the version on disk first, so an old editor buffer can't quietly overwrite a newer edit made elsewhere. If they conflict, compare both versions and choose what to keep.

[Keyboard shortcuts](../reference/shortcuts.md) covers graph navigation and panes. The [Rattish guide](../workflows/rattish.md) explains the source format.

