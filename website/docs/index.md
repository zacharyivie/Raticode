---
title: Documentation
description: Learn Raticode, from your first project to repeatable workflows and agent teams.
pageClass: docs-layout
---

# The Raticode guide

Raticode is a desktop workspace for agentic coding: an editor, a visual workflow graph, the Rem coding agent, a terminal and source control. The `Raticode` CLI runs the same Rattish workflows without a window. Your files, conversations and runs stay on machines you control.

## Start with a working project

New here? [Getting started](./getting-started.md) covers install, provider, project and a first workflow. [Your first workflow](./first-workflow.md) works without an AI subscription.

| I want to… | Read this |
| --- | --- |
| Install the app or CLI | [Installation](./installation.md) |
| Connect a coding provider | [Provider setup](./setup.md) |
| Hand work to Rem | [Rem's home and tasks](./rem/home.md) |
| Chat in your own threads | [Chat and threads](./rem/chat.md) |
| See every ticket in one place | [Global board and self-work](./rem/board.md) |
| Build reusable automation | [Rattish language](./workflows/rattish.md) |
| Run a workflow on a schedule, file change or webhook | [Triggers](./workflows/triggers.md) |
| Choose the right workflow step | [Node reference](./nodes/index.md) |
| Share a workflow with another computer | [Import and export](./studio/sharing.md) |
| Save knowledge and reports | [Second Brain](./knowledge/second-brain.md) |
| Run an agent organization | [Organizations](./advanced/organizations.md) |
| Coordinate a team | [Swarms](./advanced/swarms.md) |
| Use Raticode from your phone | [Raticode Mobile](./mobile/index.md) |
| Find a setting | [Settings](./reference/settings.md) |
| Fix a setup problem | [Troubleshooting](./reference/troubleshooting.md) |
| Report a bug or get help | [Support](./support.md) |
| Know what leaves your computer | [Privacy](./privacy.md) |

The sidebar lists every page, including one per built-in node under **Node reference**. On a phone, open it with **Browse docs**.

## What runs where

| Area | Where it runs | What you supply |
| --- | --- | --- |
| Desktop studio and workflow engine | Your computer | Project folders and required local programs |
| Rem and agent nodes | Through your configured provider | Its CLI login, account, or API profile |
| CLI workflows | The machine running `Raticode` | Files, interpreters, and provider access on that machine |
| Raticode Mobile and fleet | Requests run on the paired computer | Phone and desktop pairing turned on, and per-device switches in **Settings > Devices** |

There is no Raticode account. AI providers and connected tools have their own accounts and data policies, and an agent request still goes to its provider even when the workflow runs locally.

::: info Docs track the current source
These pages cover Raticode desktop and mobile as they are in the source. A recently added feature may not be in the [latest release](https://github.com/zacharyivie/Raticode/releases/latest) yet.
:::

## Feature status

| Status | Features |
| --- | --- |
| **Available** in the desktop app | The studio (editors, Source control, terminal, the Browser plugin), Rem's home and tasks, threads, the global Kanban board and self-work, workflows and triggers, reports, and the `Raticode` CLI |
| **Experimental**, off until you turn it on in **Settings > Developer > Feature Preview** | Phone and desktop pairing (and with it the desktop fleet), [Organizations](./advanced/organizations.md), [Swarms](./advanced/swarms.md), [Second Brain](./knowledge/second-brain.md) |
| **Platform limits** | Desktop builds: Apple Silicon Macs, x64 Windows, x64 Linux. Mac builds are signed and notarized from 0.5.3 on ([installation](./installation.md#macos)); Windows builds are not code-signed. Raticode (gecko) and waking a computer from the phone need Linux. |
| **Not released yet** | Signed Windows builds are planned. The AUR packages `raticode-bin` and `raticode-cli-bin` are ready but unpublished ([Arch Linux](./installation.md#arch-linux-aur)). |

