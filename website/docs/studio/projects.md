---
title: Projects and workspaces
description: Open projects, manage workflow folders, and keep editor and Rem scope clear.
pageClass: docs-layout
---

# Projects and workspaces

A project is a folder on your machine. It gives Raticode a place to read files, run commands, and register workflows.

## Open a folder

Choose **Open project folder** and use the native folder picker. Recent projects make it easy to return to folders you have already opened. The active project appears in the workspace controls; check it before running a workflow or terminal command.

Raticode can keep editor files and workflow tabs open while you move between projects. An open tab is a reference to a file, not a request for Rem to inspect it.

## Where workflows live

Registered workflows live beneath their project:

```text
my-project/
  .raticode/
    <workflow-id>/
      workflow.rattish
      workflow.metadata.json
  src/
  reports/
```

`workflow.rattish` is the executable source. Metadata records graph positions and editor state. Compiled artifacts and run records are managed internally. Edit the source through the studio or CLI; do not hand-edit compiled IR to change behavior.

Use `gof rattish list --format json` to inspect registered workflow paths. Copy the actual source path when scripting instead of constructing one from a title.

## Rem has its own scope

A project-scoped thread keeps its project when you change the active editor. Check the scope shown in Rem before requesting a modification. A global thread can research across available resources and select an open project for project work.

Scope selection does not clone a conversation or change its identity. Provider and model choices also remain part of the same thread. See [Chat and threads](../rem/chat.md).

## Filesystem grants

The desktop uses selected folders and files as grants for its file operations. A native folder picker is the normal way to grant access. Opening a project does not install its dependencies or authenticate its providers.

Provider sandboxes, workflow path rules, and desktop file grants have different responsibilities. Avoid interpreting the resource picker as a restriction on every process the OS user can start.

For isolated parallel Git work, use [worktrees in Source control](./source-control.md) or [Swarms](../advanced/swarms.md).

