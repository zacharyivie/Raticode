---
title: Permissions and local data
description: Understand project scope, provider permissions, secret references, and data stored on your machine.
pageClass: docs-layout
---

# Permissions and local data

Raticode keeps its working state on your machine and shows which tools each turn can use. The provider you choose still decides where its requests go and how its own permissions work.

## Permission layers

Each layer is separate. Turning one off doesn't turn off the others.

| Layer | What it controls |
| --- | --- |
| Project selection | The folder the studio and project tools work in |
| Rem resources | Shell, web, skills, MCP servers and app tools offered to a turn |
| Provider permissions | The provider's own approval mode and execution policy |
| Workflow preflight | Whether declared executables, paths, credentials and contracts are ready |
| Organization or device grants | What one organization employee or one paired device may do |

- Turning off Rem's shell doesn't limit an MCP server you added separately. Provider-native tools can have their own policy.
- Choosing a project isn't an operating-system sandbox.
- A read-only session can look at supported state but can't change it through Raticode's management tools.
- Selecting a tool isn't an instruction. Rem still needs you to ask before it runs a workflow.

## Paths and side effects

- Registered Rattish workflows resolve relative paths from their project root. A standalone file resolves them from its own folder.
- Absolute paths, `~` and `..` are allowed, subject to each node's and the operating system's checks. Review paths before you run.
- Commands and agents can change files, call services and start processes. Preflight doesn't simulate those effects, and **Stop** doesn't undo finished changes.
- Review imported workflows, and keep a copy of work you care about, before running them.

## Credentials

- Sign in to coding-provider CLIs through their own login. API-backed providers need their configured credentials.
- In workflows, refer to secrets by name, such as `secret["SERVICE_TOKEN"]`, which reads an environment variable. Don't paste the value into source, reports or screenshots.
- Device identities and organization secrets use the platform's credential store. Organization tools can grant a secret reference without putting the value in task text.
- Ordinary workflow environment variables aren't an encrypted vault, and projects and the chat archive aren't encrypted at rest.

## Stored data

Workflow source and editor metadata live in each project's `.raticode/` folder. Everything else is in Raticode's data folder, which keeps its original `gofer` name so existing data stays in place:

| Platform | Default data folder |
| --- | --- |
| Linux | `$XDG_DATA_HOME/gofer`, or `~/.local/share/gofer` |
| macOS | `~/Library/Application Support/gofer` |
| Windows | `%APPDATA%\gofer` |

- Some CLI commands accept a different data folder.
- The desktop app's own profile, which holds working chat state, is stored apart from the backend files.
- Swarms and organizations keep their own saved state.
- An enabled [conversation archive](../knowledge/archive.md) writes a separate copy to the folder you choose. [Second Brain](../knowledge/second-brain.md) indexes your knowledge folder.

Back up every location you rely on. Deleting one copy doesn't always delete the others.

## Network use

Raticode needs no Raticode account or cloud service. These still use the network:

- requests to your AI providers
- web search, when a thread has it on
- remote MCP servers
- model downloads
- Tailscale, if you turn it on for the phone
- HTTP request nodes in workflows

See [Provider setup](../setup.md), [Tools and resources](../rem/resources.md) and [Troubleshooting](../reference/troubleshooting.md).
