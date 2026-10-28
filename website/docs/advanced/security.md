---
title: Permissions and local data
description: Understand project scope, provider permissions, secret references, and data stored on your machine.
pageClass: docs-layout
---

# Permissions and local data

Raticode keeps its working state on your machine and makes tool access visible. The provider you choose still determines where its requests go and how its permissions work.

## Separate the permission layers

| Layer | What it controls |
| --- | --- |
| Project selection | The workspace used by the studio and project tools |
| Rem resources | Shell, web, skills, MCP, and selected application tools exposed to a turn |
| Provider permissions | The provider's approval mode and native execution policy |
| Workflow preflight | Readiness of declared executables, paths, credentials, and contracts |
| Organization or device grants | Access allowed to a specific employee or trusted remote request |

Disabling Rem's shell resource does not disable a separately configured MCP server's abilities. Provider-native tools can have their own policy. A project picker is not an operating-system sandbox.

Read-only sessions can inspect supported state but cannot mutate through application management tools. An editable session still needs an instruction to perform the requested work; selecting a resource is not the same as asking it to execute a workflow.

## Paths and side effects

Registered Rattish workflows resolve relative paths from their project root. Standalone files resolve relative paths from their source directory. Absolute paths, home expansion, and parent traversal are supported subject to the node and operating-system checks, so review paths before execution.

Commands and agents can change files, call services, and launch processes. Preflight does not simulate those effects, and Stop does not roll back completed changes. Review imported workflows and preserve recoverable work before running them.

## Credentials

Authenticate coding-provider CLIs through their own supported login flow. API-backed providers need their configured credentials. Workflows can use named environment-backed references such as `secret["SERVICE_TOKEN"]`; do not paste the actual value into source, reports, or screenshots.

Device identities and organization secret values have dedicated platform credential storage. Organization tools can grant a secret reference without storing the value in task text. Ordinary workflow environment variables are not automatically an encrypted vault, and the whole project or chat archive is not encrypted at rest by this feature.

## Stored data

Project workflow source and editor metadata live under the project's `.raticode/` directory. App state uses the platform's Gofer data directory by default:

| Platform | Default backend data location |
| --- | --- |
| Linux | `$XDG_DATA_HOME/gofer`, or `~/.local/share/gofer` |
| macOS | `~/Library/Application Support/gofer` |
| Windows | `%APPDATA%\gofer` |

Some CLI commands allow a custom data directory. The desktop's browser-profile storage, including working chat state, is separate from backend files. Swarms and organizations maintain their own persisted state.

An enabled [conversation archive](../knowledge/archive.md) creates a separate copy in the chosen folder. [Second Brain](../knowledge/second-brain.md) indexes a configured knowledge root. Back up each location you rely on; deleting one copy does not necessarily delete another.

## Network behavior

Raticode does not require a Raticode cloud account. Provider requests, enabled web search, remote MCP servers, model downloads, optional relays, and explicit HTTP workflow nodes can still use the network. Local state does not make every feature offline.

See [Provider setup](../setup.md), [Tools and resources](../rem/resources.md), and [Troubleshooting](../reference/troubleshooting.md).
