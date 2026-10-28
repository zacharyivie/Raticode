---
title: Documentation
description: Learn Raticode, from your first project to repeatable workflows and agent teams.
pageClass: docs-layout
---

# The Raticode guide

Build workflows you can inspect. Work with Rem in your project. Keep the files, conversations, and execution on machines you control.

Raticode brings a code editor, visual workflow graph, coding agent, terminal, and source control into one desktop workspace. Its `gof` CLI runs Rattish workflows without a desktop window. You can start with a shell command and add agents, branches, file operations, and review steps as your process grows.

## Start with a working project

New to Raticode? Follow [Getting started](./getting-started.md) to install the desktop app, connect a provider, open a project, and create a workflow. You can also complete [Your first workflow](./first-workflow.md) without an AI subscription.

| I want to… | Read this |
| --- | --- |
| Install the app or CLI | [Installation](./installation.md) |
| Connect a coding provider | [Provider setup](./setup.md) |
| Work with Rem | [Chat and threads](./rem/chat.md) |
| Build reusable automation | [Rattish language](./workflows/rattish.md) |
| Choose the right workflow step | [Node reference](./nodes/index.md) |
| Save knowledge and reports | [Second Brain](./knowledge/second-brain.md) |
| Coordinate a team | [Swarms](./advanced/swarms.md) |
| Fix a setup problem | [Troubleshooting](./reference/troubleshooting.md) |

## Find your way around

The left sidebar is the wiki's page directory. Its sections follow the way you use Raticode: setup, the studio, Rem, workflows, knowledge, and teams. Expand **Node reference** for a separate page on every built-in node. On a phone, choose **Browse docs** to open the same directory.

Use the search button in the navbar to search page content. The outline on the right jumps between headings in the current article. Previous and next links continue through the guide.

## What runs where

| Area | Where it runs | What you supply |
| --- | --- | --- |
| Desktop studio and workflow engine | Your computer | Project folders and required local programs |
| Rem and agent nodes | Through your configured provider | Its CLI login, account, or API profile |
| CLI workflows | The machine running `gof` | Files, interpreters, and provider access on that machine |
| Mobile controller and fleet | Paired desktop backends | Experimental setup and explicit device grants |

Raticode does not require a Raticode account. AI providers and connected tools have their own accounts and data policies. Local execution does not mean an AI request is offline.

::: info Current product documentation
These guides describe the current Raticode source and Rattish 1 contracts. The [published release](https://github.com/zacharyivie/Raticode/releases/latest) may precede a recently added feature. Organizations and device networking are experimental. Mobile is coming soon for public download. The automation guides identify the remaining legacy scheduler, watcher, and queue boundaries.
:::

