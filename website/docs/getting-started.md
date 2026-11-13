---
title: Getting started
description: Install Raticode, connect a coding provider, open a project, and build your first workflow.
pageClass: docs-layout
---

# Getting started

Six steps from a fresh install to a project Rem can help with and a workflow you can run again.

<AppScreenshot name="studio-workflow-run" eager caption="Where you'll end up: your projects and workflows on the left, the workflow graph and its run timeline in the middle, and a Rem thread on the right." />

## 1. Install Raticode

Pick your platform in the [download section](/#download). Builds cover Windows x64, Linux x64 and macOS on Apple Silicon. The desktop app includes the workflow backend, so you don't need Python or Node.js.

| Platform | Install |
| --- | --- |
| Windows | Run the setup program. Leave **Add the Raticode CLI to my user PATH** on if you want the CLI, then open a new terminal. |
| macOS | Open the disk image, copy Raticode to Applications and open it. If macOS blocks the first launch, follow the release's signing notes. |
| Linux | Install the `.deb` or `.rpm`, or make the AppImage executable and open it. |

The [installation guide](./installation.md) covers platform commands, the CLI on its own, and building from source.

## 2. Connect a provider

Install one supported coding CLI and sign in with its own instructions. Raticode finds providers on your machine; it doesn't create accounts.

1. Open **Settings > Providers**, turn on your provider, and check its executable and model list.
2. If it isn't found, set **Executable override** and choose **Refresh providers**.
3. In **Settings > Rem**, pick the **Default provider** for new tasks and threads. You can also set a separate **Rem chat provider** for Rem's own replies.

Start with the provider you already use. [Provider setup](./setup.md) lists the CLI names and how to check sign-in. You can skip this step while you learn command and file nodes.

## 3. Open a project

Choose **Open Project** (**File > Open Project...**, or **Open project...** in the project dropdown) and pick a project folder.

- The folder becomes the working context for files, source control, terminals and project-scoped Rem threads.
- Picking it also gives the desktop app permission to access it.
- A conversation keeps its own scope when you switch the editor to another project, so check the project name before asking Rem to edit anything.

More in [Projects and workspaces](./studio/projects.md).

## 4. Send a first message

Open Rem and choose the provider and model in the composer. Check its permission mode. **New thread tools, skills & MCP** shows what the first turn gets.

A good first request:

```text
Read this project and explain its entry points, how to run it,
and where its tests live. Summarize your findings before changing files.
```

Enter sends; Shift+Enter adds a line break. The thread keeps the conversation, and switching models later keeps the thread and its resource choices.

## 5. Create a workflow

Create one in **Workflows**, or ask Rem:

```text
Create a Rattish workflow in this project that writes a short text report.
Validate it and check preflight. Do not execute it yet.
```

[Your first workflow](./first-workflow.md) builds one that writes two local files and calls no provider. Workflow sources live in `.raticode/<workflow-id>/workflow.rattish`.

## 6. Validate, review, then run

- **Validation** checks the language and the graph.
- **Preflight** checks the machine's resources.
- Neither one runs the workflow.

Read every command, file write, agent prompt and external request before you choose **Run**.

The bottom panel has **Problems**, **Run Timeline** and **Runs**. If a step fails, read its diagnostic instead of guessing from the final status.

Next: [Code and graph editors](./studio/editors.md), [Chat and threads](./rem/chat.md) or [Rattish language](./workflows/rattish.md).
