---
title: Settings
description: Find application, provider, device, memory, and editor settings in Raticode.
pageClass: docs-layout
---

# Settings

Open Settings from the app menu or use the configured shortcut. Search by a control's name to find it across categories.

## Categories

| Category | Controls and related guide |
| --- | --- |
| General | Application behavior and file autosave; [editor guide](../studio/editors.md) |
| Devices | Experimental networking and paired-device management; [pairing guide](../advanced/mobile.md) |
| Appearance | App colors and visual preferences; report styling is configured separately under Memory |
| Editor | Code font, wrapping, indentation, and related editor behavior |
| Browser | Home and search behavior; [integrated browser](../studio/browser.md) |
| Terminal | Terminal behavior and appearance; [terminal guide](../studio/terminal.md) |
| Providers | Provider selection, catalog settings, and supported permission preferences |
| Usage | Remaining allowances and usage measured in Raticode; [usage guide](../rem/usage.md) |
| Rem | Chat and assistant preferences; [chat guide](../rem/chat.md) |
| Memory | Second Brain, conversation archive, report output, and report themes |
| Developer | Development and diagnostic controls |
| Layout | Workspace and pane presentation |
| Keybindings | Assign, clear, or restore application shortcuts |

Availability can depend on the platform, enabled feature flags, and installed provider capabilities. Use the labels in your installed app rather than assuming every development feature is in the current public build.

## Provider settings and thread choices

Provider configuration supplies available choices and permissions. The active thread's provider, model, and effort picker determines its next turn. Changing a picker does not itself send a message. See [Providers, models and effort](../rem/providers.md).

Resource selections belong to the thread. Check the shell, web, skills, and MCP selections there when diagnosing a missing tool, rather than changing unrelated editor settings.

## Memory settings

Enable Second Brain only after selecting a knowledge root. Set an archive folder separately if you want portable conversation copies. Choose report format and theme for future reports; those settings do not turn on archiving or rewrite existing files.

See [Second Brain](../knowledge/second-brain.md), [Conversation archive](../knowledge/archive.md), and [Report themes](../knowledge/themes.md).

## Diagnostics and logs

**Settings > Developer** shows application and desktop data directories, the current log path, runtime versions, and backend state. You can open those folders, read recent logs, copy diagnostics, open developer tools, or restart the backend.

The desktop writes timestamped JSON records to `app.jsonl` in Electron's logs directory. Use the exact path displayed in Settings. Backend output, renderer warnings, uncaught errors, and application errors share this log. Rotation keeps the current file and three backups of up to 5 MB each.

Common credential fields are redacted, but inspect diagnostics before publishing them. Logs are separate from the conversation archive and are not a complete transcript. Restarting the backend can interrupt work; inspect active turns and runs first.

## Make a reversible change

Change one relevant setting, inspect its effect, and keep the previous value when investigating a problem. Do not erase the desktop profile as the first troubleshooting step: it can contain working chat state and unrelated preferences.

Use [Keyboard shortcuts](./shortcuts.md) for default bindings and [Troubleshooting](./troubleshooting.md) for common failure paths.
