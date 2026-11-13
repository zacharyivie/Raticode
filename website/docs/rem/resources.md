---
title: Tools, skills and MCP
description: Select command execution, web search, skills, and MCP connections for Rem.
pageClass: docs-layout
---

# Tools, skills and MCP

Give each thread the tools and reference material its task needs.

## Open the resource menu

Use **New thread tools, skills & MCP** before starting a conversation, or **Thread tools, skills & MCP** in an existing thread. New threads copy the defaults from **Settings > Rem**; after that, each thread keeps its own choices, even when you change provider.

| Section | What it gives Rem |
| --- | --- |
| **Built-in** | **Run commands** (shell commands, scripts and tests), **Search the web**, **Subagents** (hand parts of a task to helper agents), and **Browser**: control of tabs in the [Browser plugin](../studio/browser.md#let-rem-use-the-browser), off by default |
| **Second Brains** | Search and save in a [Second Brain](../knowledge/second-brain.md). Shown when Second Brain is on. |
| **Skills** | Folders with a `SKILL.md`, from Rem's shared skill library |
| **MCP servers** | **Local** servers (a program plus separate arguments) or **HTTP** servers |

- Rem can set up skills and MCP servers for you. New ones start switched off until you turn them on in a thread.
- Finish or remove half-filled entries before sending. Raticode checks the configuration and won't launch with an incomplete server.

## Skills

Pick the folder that holds the skill's instructions. Rem gets a short index and reads the relevant file only when it needs it, so a skill doesn't bloat every prompt.

A skill describes a procedure. It doesn't grant file access. Skills installed in the provider itself can exist alongside Raticode's selection.

## MCP servers

- **HTTP:** use the server's URL, without credentials in it.
- **Local:** give the program and each argument separately. Raticode starts the program directly; it doesn't pass the text to a shell.

Set up the server's own sign-in with its instructions. The provider must support MCP too, and a server's name in the list doesn't prove its tools are available in the current turn.

## Add skills and MCP servers from the CLI

The `Raticode rem` commands manage the same shared library:

```bash
Raticode rem resources
Raticode rem skill list
Raticode rem skill path
Raticode rem skill new NAME
Raticode rem skill add PATH
Raticode rem skill remove PATH
Raticode rem mcp list
Raticode rem mcp add NAME ...
Raticode rem mcp remove NAME
```

- `skill path` prints Raticode's own skills folder, where `skill new` creates skills.
- `skill remove` unregisters a skill and leaves its files.
- Entries added here appear in **Thread tools**, off until you turn them on per thread.
- `Raticode rem --help` lists every option.

## When changes take effect

- **Idle thread:** on the next message.
- **During a response:** when a steering message restarts the provider, or on the next normal turn. Toggling a setting doesn't reconfigure a process that's already running.

## Global and project scope

A global thread can use knowledge tools, MCP servers and skills without a project. The provider limits native editing and commands until you pick a project.

An MCP tool keeps its own powers: turning off **Run commands** doesn't turn off a knowledge tool that saves notes. These settings are per provider adapter, not an operating-system sandbox. See [Permissions](../advanced/security.md), [Second Brain](../knowledge/second-brain.md), and [Swarms](../advanced/swarms.md).
