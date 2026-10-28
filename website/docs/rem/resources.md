---
title: Tools, skills and MCP
description: Select command execution, web search, skills, and MCP connections for Rem.
pageClass: docs-layout
---

# Tools, skills and MCP

Give each thread the tools and reference material needed for its task.

## Open the resource menu

Use **New thread tools, skills & MCP** before creating a conversation, or **Thread tools, skills & MCP** in an existing thread. Defaults in **Settings > Rem** are copied into new threads. Each thread can then change its own selections, which survive provider changes.

| Resource | What it supplies |
| --- | --- |
| Command execution | Provider command tools when supported by its permissions |
| Web search | Provider web capability where its adapter supports it |
| Skill folders | Instructions indexed by path and read on demand |
| HTTP MCP server | Tools from an explicitly configured server endpoint |
| Local stdio MCP server | Tools from a local executable with separate arguments |

Complete or remove invalid resource entries before sending. Raticode validates the selected configuration rather than launching with a half-filled endpoint.

## Configure a skill

Select the folder containing the skill instructions. Rem receives a compact index and reads the relevant file when needed. A selected folder does not cause its entire contents to be pasted into every prompt.

Skills describe procedures; their presence does not grant arbitrary filesystem access. Provider-installed skills and native defaults can still exist separately from the Raticode selection.

## Configure MCP

For HTTP, use the intended server URL without embedding credentials in it. For a local server, supply the executable and each argument separately. Raticode launches that program directly; it does not interpret the text as a shell command.

Server authentication and the provider's MCP support still matter. Use the server's own setup instructions for credentials. A server name alone does not establish that its tools are available or allowed in the current turn.

## When changes take effect

Idle changes apply to the next message. During a response, resource changes apply when the next steering instruction restarts the invocation, or on the next ordinary turn. Toggling a setting alone does not reconfigure an already running process.

## Global and project scope

A global thread can keep knowledge tools, selected MCP servers, and skill references without a project. Native editing and command permissions are restricted according to the provider until project selection. An MCP tool keeps its own authority; disabling the shell does not inherently disable a selected knowledge-save tool.

Resource policies are adapter-specific and are not an OS sandbox for every native integration. See [Permissions](../advanced/security.md), [Second Brain](../knowledge/second-brain.md), and [Swarms](../advanced/swarms.md).

