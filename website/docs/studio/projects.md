---
title: Projects and workspaces
description: Open projects, manage workflow folders, and keep editor and Rem scope clear.
pageClass: docs-layout
---

# Projects and workspaces

A project is a folder on your computer. Raticode reads its files, runs commands in it and keeps its workflows there.

## Open a folder

Choose **Open Project** (**File > Open Project...**, or **Open project...** in the project dropdown) and pick a folder. Recent projects are listed for a quick return.

- The workspace controls show the active project. Check it before running a workflow or terminal command.
- Editor files and workflow tabs can stay open while you move between projects. An open tab is just a file reference, not a request for Rem to look at it.
- Opening a project doesn't install its dependencies or sign in its providers.

## Where workflows live

```text
my-project/
  .raticode/
    <workflow-id>/
      workflow.rattish
      workflow.metadata.json
  src/
  reports/
```

| File | Holds |
| --- | --- |
| `workflow.rattish` | The workflow source that runs |
| `workflow.metadata.json` | Graph positions and editor state |

Raticode manages compiled output and run records itself. Change a workflow through the studio or CLI, never by editing compiled IR.

For scripts, get real paths from `Raticode rattish list --format json` rather than building them from a title.

## Rem keeps its own scope

- A project-scoped thread keeps its project when you switch editor files. Check the scope shown in Rem before asking for changes.
- A global thread can research across available resources and pick an open project for project work.
- Changing scope, provider or model keeps the same thread; it doesn't copy the conversation. See [Chat and threads](../rem/chat.md).

## File access

The desktop app treats folders and files you pick as permission for its own file operations. The native folder picker is the normal way to give that permission.

Provider sandboxes, workflow path rules and the app's file permissions each cover different things. The resource picker doesn't restrict every process your user account can start.

For parallel Git work in isolation, use [worktrees in Source control](./source-control.md) or [Swarms](../advanced/swarms.md).
