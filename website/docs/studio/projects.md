---
title: Projects and workspaces
description: Open projects, manage workflow folders, and keep editor and Rem scope clear.
pageClass: docs-layout
---

# Projects and workspaces

A project is a folder on your computer. Raticode reads its files, runs commands in it and keeps its workflows there. It lists the workflows in its `.raticode` folder and, from 0.5.5, [every other `workflow.rattish` in it](#workflows-anywhere-in-the-project).

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

## Workflows anywhere in the project

From 0.5.5, opening a project also lists every other `workflow.rattish` below it, at any depth. Open the Raticode repository, for example, and **Workflows** shows each starter in `rattish/starters/<id>/` beside the workflows in `.raticode/`.

```text
my-project/
  .raticode/review/workflow.rattish      listed, as before
  automations/nightly/workflow.rattish   listed
  workflow.rattish                       listed, shown as "Project folder"
  node_modules/pkg/workflow.rattish      skipped
```

- **Telling them apart.** A workflow found outside `.raticode` shows its folder under its name in the list and in the editor's path bar. On the phone, the folder starts its description.
- **IDs.** Its ID comes from its folder name (`jev-demo`). If that ID is taken, the folder's path in the project is used (`rattish-starters-jev-demo`), then a number. A workflow keeps its ID once it has one. Delete or move its file and the old entry goes away the next time the project is checked.
- **Running it.** It runs, opens in the studio, takes tests, triggers and inputs like any other workflow, and relative paths start from the project folder. Its compiled build, run records, test results and agent memory are kept in Raticode's data folder, so running it never writes into your source tree. Editing its graph layout saves `workflow.metadata.json` next to it.
- **Deleting it.** **Delete** removes only its `workflow.rattish` and `workflow.metadata.json`. Other files in that folder stay.
- **Skipped folders.** Raticode never searches version control folders (`.git`), dependencies and virtual environments (`node_modules`, `.venv`, `venv`, any folder with a `pyvenv.cfg`), build output (`build`, `dist`, `.next`, `.vitepress` and similar), caches (`__pycache__`, `.cache`, any folder with a `CACHEDIR.TAG`), editor folders (`.idea`, `.vscode`), the folders of a workflow inside `.raticode`, and Raticode's own copies of projects: `.worktrees`, `rem-worktrees`, `organization-worktrees` and any Git worktree checked out inside the project, so a worktree copy of a workflow isn't listed twice. Raticode's own data folder is skipped too.
- **Links.** It doesn't follow folder or file links, and refuses a registered path that passes through one.
- **Limits.** The search stops 12 folders deep, after 20,000 folders or at 500 workflows. On the Raticode repository it takes about 16 ms. `Raticode rattish list --project PATH` reports `discoveryLimited` when a limit was hit.

The same search runs when you open a project and every 30 seconds while it's open. To run it from a terminal:

```bash
Raticode rattish list --project ./my-project --format json
```

Each workflow in the reply has `relativeFolder` and `managed` (`true` for `.raticode`).

Starter test sets name their sample files under `.raticode/<id>/`, so run a starter's tests on a copy made with **Start from** or `rattish create --from`, not on the starter in place.

## Rem keeps its own scope

- A project-scoped thread keeps its project when you switch editor files. Check the scope shown in Rem before asking for changes.
- A global thread can research across available resources and pick an open project for project work.
- Changing scope, provider or model keeps the same thread; it doesn't copy the conversation. See [Chat and threads](../rem/chat.md).

## File access

The desktop app treats folders and files you pick as permission for its own file operations. The native folder picker is the normal way to give that permission.

Provider sandboxes, workflow path rules and the app's file permissions each cover different things. The resource picker doesn't restrict every process your user account can start.

For parallel Git work in isolation, use [worktrees in Source control](./source-control.md) or [Swarms](../advanced/swarms.md). A [setup file](./worktree-setup.md) prepares the worktrees Rem, Organizations and Swarms create.
