---
title: Documentation
description: Learn Raticode, from your first project to repeatable workflows and agent teams.
pageClass: docs-layout
---

# The Raticode guide

Build workflows you can inspect. Work with Rem in your project. Keep the files, conversations, and execution on machines you control.

Raticode brings a code editor, visual workflow graph, coding agent, terminal, and source control into one desktop workspace. Its `Raticode` CLI runs Rattish workflows without a desktop window. You can start with a shell command and add agents, branches, file operations, and review steps as your process grows.

## Start with a working project

New to Raticode? Follow [Getting started](./getting-started.md) to install the desktop app, connect a provider, open a project, and create a workflow. You can also complete [Your first workflow](./first-workflow.md) without an AI subscription.

| I want to… | Read this |
| --- | --- |
| Install the app or CLI | [Installation](./installation.md) |
| Connect a coding provider | [Provider setup](./setup.md) |
| Hand work to Rem | [Rem's home and tasks](./rem/home.md) |
| Chat in your own threads | [Chat and threads](./rem/chat.md) |
| See every ticket in one place | [Global board and self-work](./rem/board.md) |
| Build reusable automation | [Rattish language](./workflows/rattish.md) |
| Choose the right workflow step | [Node reference](./nodes/index.md) |
| Save knowledge and reports | [Second Brain](./knowledge/second-brain.md) |
| Run an agent organization | [Organizations](./advanced/organizations.md) |
| Coordinate a team | [Swarms](./advanced/swarms.md) |
| Use Raticode from your phone | [Raticode Mobile](./mobile/index.md) |
| Fix a setup problem | [Troubleshooting](./reference/troubleshooting.md) |
| Report a bug or get help | [Support](./support.md) |
| Know what leaves your computer | [Privacy](./privacy.md) |

## Find your way around

The left sidebar is the wiki's page directory. Its sections follow the way you use Raticode: setup, the studio, Rem, workflows, knowledge, teams, and the phone app. Expand **Node reference** for a separate page on every built-in node. On a phone, choose **Browse docs** to open the same directory.

Use the search button in the navbar to search page content. The outline on the right jumps between headings in the current article. Previous and next links continue through the guide.

## What runs where

| Area | Where it runs | What you supply |
| --- | --- | --- |
| Desktop studio and workflow engine | Your computer | Project folders and required local programs |
| Rem and agent nodes | Through your configured provider | Its CLI login, account, or API profile |
| CLI workflows | The machine running `Raticode` | Files, interpreters, and provider access on that machine |
| Raticode Mobile and fleet | Requests run on the paired computer | Phone and desktop pairing turned on, and per-device switches in **Settings > Devices** |

Raticode does not require a Raticode account. AI providers and connected tools have their own accounts and data policies. Local execution does not mean an AI request is offline.

::: info Current product documentation
This website is the reference for Raticode, desktop and mobile. The guides describe the current Raticode source and Rattish 1 contracts, so the [published release](https://github.com/zacharyivie/Raticode/releases/latest) may precede a recently added feature. Rattish workflows start on a schedule, file change, or webhook through [triggers](./workflows/triggers.md). The automation guides identify the remaining legacy scheduler, watcher, and queue boundaries.
:::

## Feature status

| Status | Features |
| --- | --- |
| **Available** in the desktop app | The studio (editors, Source control, terminal, the Browser plugin), Rem's home and tasks, threads, the global Kanban board and self-work, workflows and triggers, reports, and the `Raticode` CLI |
| **Experimental**, off until you turn it on in **Settings > Developer > Feature Preview** | Phone and desktop pairing (and with it the desktop fleet), [Organizations](./advanced/organizations.md), [Swarms](./advanced/swarms.md), [Second Brain](./knowledge/second-brain.md) |
| **Platform limits** | Raticode (gecko) runs on Linux only. Desktop builds are for Apple Silicon Macs, x64 Windows and x64 Linux. Windows builds are not code-signed. Mac builds from 0.5.3 on are signed with Raticode's Apple Developer ID and notarized by Apple; 0.5.2 and earlier are not ([installation](./installation.md#macos)). Wake computer from the phone needs a Linux computer. |
| **Not released yet** | [Raticode Mobile](./mobile/index.md) is built and documented here, but has no public download. Signed Windows builds are planned. The AUR packages `raticode-bin` and `raticode-cli-bin` are ready but not published yet ([Arch Linux](./installation.md#arch-linux-aur)). |

