---
title: Settings
description: Find application, provider, device, memory, and editor settings in Raticode.
pageClass: docs-layout
---

# Settings

Open Settings with **Help > Settings**, the gear (**Open settings**) in the toolbar, or Mod+,. Search by a control's name to find it across categories. **Reset all settings** restores the defaults.

## Categories

| Category | Controls and related guide |
| --- | --- |
| General | Application data directory, initial sidebar, file autosave, default run target (local or runner queue), checking for updates at startup, and **Delete archived threads** after 7, 30 or 90 days |
| Devices | Microphone and level, **Dictation** (press or hold), **Send after dictation**, **Speech recognition** (the built-in recognizer on macOS and Windows, **Test speech recognition** on macOS, and **Download offline model** with its progress), and, with pairing turned on, paired phones and computers; [dictation](../rem/attachments.md#dictate-a-message), [pairing](../mobile/pairing.md) |
| Appearance | **Color theme** and **Reduced motion**; report styling is configured separately under Memory |
| Editor | Font size, line height, tab size, word wrap, minimap, autosave delay, and whether Markdown and HTML files open in preview or edit mode; [editor guide](../studio/editors.md) |
| Browser | **Default browser for new tabs** (Raticode (chromium), Raticode (gecko), Firefox, Chrome or Edge), home page, search URL, downloads, and what Rem may do in the browser; [Browser](../studio/browser.md) |
| Terminal | Shell, font, cursor and scrollback; [terminal guide](../studio/terminal.md) |
| Providers | Enabled providers, executable overrides, allowed and denied models, defaults, and **Refresh providers**; [provider setup](../setup.md) |
| Usage | Remaining allowances and usage measured in Raticode; [usage guide](../rem/usage.md) |
| Rem | **Self-work** (on or off, max threads, backlog pulling, Rem's projects and the latest report), commit messages, **Tidy voice messages**, default scope, avatar and thoughts, **Swarm access**, **Rem task workers**, **Queue tests and builds**, default resources, and the default and Rem chat provider, model and effort; [Rem's home and tasks](../rem/home.md) |
| Memory | Second Brain, conversation archive, report output, and report themes |
| Developer | Feature Preview switches for every experimental feature (phone and desktop pairing, Organizations, Swarms, Second Brain), plus diagnostic controls |
| Layout | Arrangement, which side the project and Rem panes sit on, what the Rem pane shows (threads or Rem's home), terminal location, startup panes, pane sizes and **Reset layout** |
| Plugins | Which plugins show in the plugin rail and their order, with **Lock plugin order**. **Browser** has no side pane; hiding it removes its icon, and links, Rem's tabs and `Ctrl+J` still open it |
| Keybindings | Assign, clear, or restore application shortcuts; [shortcuts](./shortcuts.md) |

Availability can depend on the platform, enabled feature flags, and installed provider capabilities. Use the labels in your installed app rather than assuming every development feature is in the current public build.

## Provider settings and thread choices

Provider configuration supplies available choices and permissions. The active thread's provider, model, and effort picker determines its next turn. Changing a picker does not itself send a message. See [Providers, models and effort](../rem/providers.md).

Resource selections belong to the thread. Check the shell, web, skills, and MCP selections there when diagnosing a missing tool, rather than changing unrelated editor settings.

## Memory settings

Enable Second Brain only after selecting a knowledge root. Set an archive folder separately if you want portable conversation copies. Choose report format and theme for future reports; those settings do not turn on archiving or rewrite existing files.

See [Second Brain](../knowledge/second-brain.md), [Conversation archive](../knowledge/archive.md), and [Report themes](../knowledge/themes.md).

## Diagnostics and logs

**Settings > Developer** opens with the **Application log**: a running list of messages from the desktop app, its backend and phone connections, newest first. Turn levels (Errors, Warnings, Info, Debug) on or off, pick one source, or search. Debug is hidden until you turn it on. **Following** pauses the list so it stops moving while you read; it shows how many entries arrived meanwhile. Entries with a traceback open to show the rest. **Include earlier launches** adds the end of the log from before this launch, which helps after a crash. **Copy shown** copies the entries that match your filters as plain text.

To work with particular entries, press on a row and drag across the rows you want, or use Shift-click to extend and Ctrl-click (Cmd-click on a Mac) to add or remove one row. Arrow keys with Shift, and Ctrl+A for every row shown, work from the keyboard. Selecting pauses the list so rows stay still; clearing the selection (Escape) resumes it. Right-click the selection, or use the bar above the list, to:

- **Copy** the entries as plain text, oldest first, with tracebacks included.
- **Ask Rem** about them. Raticode closes Settings, attaches the entries to Rem's message box as a text file with the app version and log path, and puts the cursor there so you can type your question. Anything you had already typed stays. Nothing is sent until you send it. With the Rem plugin turned off, the entries go to the open thread instead. Very large selections keep the newest entries and say how many were left out.

Below the log, the page shows application and desktop data directories, the current log path, runtime versions, and backend state. You can open those folders, copy diagnostics, open developer tools, or restart the backend.

The desktop writes timestamped JSON records to `app.jsonl` in Electron's logs directory (on a Mac, `~/Library/Logs/Raticode`). Use the exact path displayed in Settings. Each record has a level (`debug`, `info`, `warn` or `error`) and a source. Rotation keeps the current file and three backups of up to 5 MB each. The live view keeps the newest 5,000 entries of the current launch. If one source writes more than 200 messages in a second, the extra ones are skipped and an entry says how many.

What the log includes:

- Desktop app warnings and errors, window crashes and page errors, and built-in browser problems.
- Backend messages at every level, with the name of the code that wrote them, and anything else the backend prints, such as a crash before logging started.
- Phone and paired-desktop connections: when the device listener starts and on which address, Tailscale changes, each sign-in and disconnect with its reason and duration, reconnects after a gap, and repeated sign-in failures (summarized, not one line per retry). It also warns when the computer's network address changes while phones are still offered the old one.

What it leaves out: Electron and Chromium's own native messages, such as GPU or GLib warnings, only reach a terminal. On a Mac, quit Raticode and start `/Applications/Raticode.app/Contents/MacOS/Raticode` from Terminal to see them. Problems on the phone's side that never reach the computer, such as the wrong Wi-Fi network or a firewall that blocks the device port, appear only on the phone. If the phone times out and nothing appears in the log, check the computer's firewall (on a Mac: System Settings > Network > Firewall).

Raticode leaves message and conversation content, pairing codes, keys and phone addresses out of the log on purpose; a phone's address is described only by its kind, such as Tailscale or the local network. Common credential fields are redacted. An error's details can still quote a file path or a value from the step that failed, so inspect diagnostics before publishing them. Logs are separate from the conversation archive and are not a complete transcript. Restarting the backend can interrupt work; inspect active turns and runs first.

## Make a reversible change

Change one relevant setting, inspect its effect, and keep the previous value when investigating a problem. Do not erase the desktop profile as the first troubleshooting step: it can contain working chat state and unrelated preferences.

Use [Keyboard shortcuts](./shortcuts.md) for default bindings and [Troubleshooting](./troubleshooting.md) for common failure paths.
