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
- With **Replace** open and text in **Replace with**, each result shows the matched text struck through next to what replaces it, with `$1` and `$&` filled in for a regular expression. After **Replace all**, select **Undo** next to the "Replaced" message to put the files back. Undo skips and names any file that changed after the replace. It covers only the latest replace, is lost when Raticode restarts, and isn't offered for a replace larger than about 20 MB.
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

A text field in the inspector saves when you leave it. If Raticode can't save the value, for example because the workflow would stop compiling, the field keeps what you typed and says why under it. Fix the text and leave the field to try again, or press Escape or **Use saved value** to put the saved value back. New in 0.6.1.

List fields, such as an approval gate's approvers or a script's args, show one row per item, with buttons to add, remove and move rows. Name and value fields, such as an HTTP request's headers or a script's `env`, show a Name column and a Value column. A name that is empty or used twice is marked on its row, and the field doesn't save until you fix it. The rows save when you leave the field or press Enter; removing or moving a row saves at once. A value can be a <span v-pre>`{{...}}`</span> placeholder, typed as text. Turn on **Edit as JSON** to edit the whole field as JSON instead. A value the rows can't show, such as a nested object written in code, always opens as JSON. Fields with their own nested settings, such as `retry` or `output-schema`, stay JSON. New in 0.6.1.

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

### Undo and Redo graph edits

*New in Raticode 0.6.1.*

Each workflow you have open keeps its own list of graph edits: adding, deleting, duplicating and renaming nodes, changing a node's type, and changing its needs, routes and fields. To step back or forward through them:

- Press **Mod+Z** to undo and **Mod+Shift+Z** to redo while the graph has focus. On Windows, **Ctrl+Y** also redoes.
- Use the **Undo** and **Redo** buttons beside **Delete selected node** in the graph toolbar. Hover one to see which edit it acts on, for example **Undo delete node fetch**. A button is dimmed when there's nothing to undo or redo.
- Choose **Edit > Undo** or **Edit > Redo** while the graph is in front, or **Undo graph edit** and **Redo graph edit** in the command palette.

Undo saves `workflow.rattish` as it was before the edit. In a text field, Rem's message box or the code editor, Mod+Z undoes typing there instead, and so does **Edit > Undo** when one of them had focus before you opened the menu.

- Undo and Redo work only while `workflow.rattish` is still the version that edit saved. If you changed it in the code editor, or Rem or another program changed it, the toolbar says **workflow.rattish changed since that edit, so it can't be undone here** and both lists are cleared.
- Moving a node isn't an edit to `workflow.rattish`, so it isn't undone.
- Each list keeps the last 50 edits. It's cleared when you close the workflow or quit Raticode.

## Understand diagnostics

**Problems** lists errors and warnings with their source locations.

- When part of the source is invalid, the graph still shows the pieces it can recover. That doesn't mean the workflow is ready to run.
- An unknown node field is a compile error. A missing provider binary is a preflight problem.

[Validation and preflight](../workflows/validation.md) explains the difference.

## Save and external changes

Save before a Git operation or a command that reads the file.

A workflow save checks the version on disk first, so an old editor buffer can't quietly overwrite a newer edit made elsewhere. If they conflict, compare both versions and choose what to keep.

[Keyboard shortcuts](../reference/shortcuts.md) covers graph navigation and panes. The [Rattish guide](../workflows/rattish.md) explains the source format.

