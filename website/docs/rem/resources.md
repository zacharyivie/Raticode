---
title: Tools, skills and MCP
description: Select command execution, web search, skills, and MCP connections for Rem.
pageClass: docs-layout
---

# Tools, skills and MCP

Give each thread the tools and reference material needed for its task.

## Open the resource menu

Use **New thread tools, skills & MCP** before creating a conversation, or **Thread tools, skills & MCP** in an existing thread. Defaults in **Settings > Rem** are copied into new threads. Each thread can then change its own selections, which survive provider changes.

| Section | What it supplies |
| --- | --- |
| **Built-in** | **Run commands** (shell commands, scripts and tests), **Search the web**, **Subagents** (hand parts of a task to helper agents) and **Browser** (Rem's control of tabs in the [Browser plugin](../studio/browser.md#let-rem-use-the-browser), off by default) |
| **Second Brains** | Search and save in a [Second Brain](../knowledge/second-brain.md); shown when Second Brain is turned on |
| **Skills** | Folders containing a `SKILL.md`, from Rem's shared skill library, indexed and read on demand |
| **MCP servers** | **Local** servers (an executable with separate arguments) or **HTTP** servers |

Rem can set up skills and MCP servers for you. New ones appear switched off until you turn them on in a thread.

Complete or remove invalid resource entries before sending. Raticode validates the selected configuration rather than launching with a half-filled endpoint.

## Configure a skill

Select the folder containing the skill instructions. Rem receives a compact index and reads the relevant file when needed. A selected folder does not cause its entire contents to be pasted into every prompt.

Skills describe procedures; their presence does not grant arbitrary filesystem access. Provider-installed skills and native defaults can still exist separately from the Raticode selection.

## Configure MCP

For HTTP, use the intended server URL without embedding credentials in it. For a local server, supply the executable and each argument separately. Raticode launches that program directly; it does not interpret the text as a shell command.

Server authentication and the provider's MCP support still matter. Use the server's own setup instructions for credentials. A server name alone does not establish that its tools are available or allowed in the current turn.

## Add skills and MCP servers from the CLI

The `Raticode rem` commands manage the same shared library from a terminal:

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

`skill path` prints Raticode's own skills folder, where `skill new` creates skills. `skill remove` unregisters a skill but leaves its files in place. Run `Raticode rem --help` for every option. Entries added this way appear in **Thread tools**, switched off until you turn them on per thread.

## When changes take effect

Idle changes apply to the next message. During a response, resource changes apply when the next steering instruction restarts the invocation, or on the next ordinary turn. Toggling a setting alone does not reconfigure an already running process.

## Global and project scope

A global thread can keep knowledge tools, selected MCP servers, and skill references without a project. Native editing and command permissions are restricted according to the provider until project selection. An MCP tool keeps its own authority; disabling the shell does not inherently disable a selected knowledge-save tool.

Resource policies are adapter-specific and are not an OS sandbox for every native integration. See [Permissions](../advanced/security.md), [Second Brain](../knowledge/second-brain.md), and [Swarms](../advanced/swarms.md).

