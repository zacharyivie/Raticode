---
title: Getting started
description: Install Raticode, connect a coding provider, open a project, and build your first workflow.
pageClass: docs-layout
---

# Getting started

Go from a fresh installation to a project where Rem can help and a workflow you can run again.

## 1. Install Raticode

Open the [download section](/#download) and choose your desktop platform. The current published artifacts cover Windows x64, Linux x64, and macOS Apple Silicon. The desktop packages include the workflow backend; you do not need to install Python or Node.js to use a packaged app.

- **Windows:** run the setup executable. Leave **Add gof CLI to my user PATH** enabled if you want terminal access. Open a new terminal after installation.
- **macOS:** open the disk image, copy Raticode into Applications, then open it. Follow the release's signing guidance if macOS blocks first launch.
- **Linux:** install the `.deb` or `.rpm`, or mark the AppImage executable and open it.

The [installation guide](./installation.md) has platform commands, CLI-only setup, and source development instructions.

## 2. Connect a provider

Install and sign in to one supported coding CLI using its own setup instructions. Raticode discovers local providers; it does not create a provider account for you.

Open **Settings > Providers**. Enable the provider you want to use and check its executable and model catalog. If it is not found, select its executable path and refresh discovery. Then open **Settings > Rem** to choose the provider used by new conversations.

Start with the provider you already use. See [Provider setup](./setup.md) for the CLI names and authentication checks. You can skip this step while learning command and file nodes.

## 3. Open a project

Use **Open project folder** and select a real project directory. The folder becomes the working context for files, source control, terminal sessions, and project-scoped Rem threads. Selecting a folder also gives the desktop permission to access it.

Check the project name before asking Rem to edit anything. A conversation keeps its own scope when you switch the editor to another project. See [Projects and workspaces](./studio/projects.md).

## 4. Send a first message

Open Rem, choose the provider and model in the composer, and review its permission mode. Open **New thread tools, skills & MCP** to inspect the resources the first turn will receive.

A useful first request is:

```text
Read this project and explain its entry points, how to run it,
and where its tests live. Summarize your findings before changing files.
```

Press Enter to send. Shift+Enter adds a line break. Rem records the conversation in its thread; choosing another model later keeps that thread and its resource selections.

## 5. Create a workflow

Open Workflows and create one in the project, or ask Rem:

```text
Create a Rattish workflow in this project that writes a short text report.
Validate it and check preflight. Do not execute it yet.
```

Follow [Your first workflow](./first-workflow.md) for an example that writes two local files without calling a provider. Registered sources live in `.raticode/<workflow-id>/workflow.rattish`.

## 6. Validate, review, then run

Validation checks the language and graph. Preflight checks the machine's resources. Neither runs the workflow. Review any command, file write, agent prompt, and external request before choosing **Run**.

Open the bottom panel to inspect **Problems**, **Run Timeline**, and **Runs**. If a step fails, use its diagnostic rather than guessing from the final status.

Continue with [Code and graph editors](./studio/editors.md), [Chat and threads](./rem/chat.md), or [Rattish language](./workflows/rattish.md).
