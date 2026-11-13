---
title: Settings
description: Find application, provider, device, memory, and editor settings in Raticode.
pageClass: docs-layout
---

# Settings

Open Settings from **Help > Settings**, the gear (**Open settings**) in the toolbar, or `Mod+,`. Search for a control by name to find it in any category. **Reset all settings** restores the defaults.

<AppScreenshot name="settings-browser" eager caption="Settings lists its categories on the left. Search at the top finds a control in any of them." />

## Categories

| Category | What's there |
| --- | --- |
| General | Data directory, initial sidebar, file autosave, default run target (local or runner queue), **Check for updates at startup**, and **Delete archived threads** after 7, 30 or 90 days |
| Devices | Microphone and level, **Dictation** (press or hold), **Send after dictation**, **Speech recognition**, and paired phones and computers once pairing is on. See [dictation](../rem/attachments.md#dictate-a-message) and [pairing](../mobile/pairing.md). |
| Appearance | **Color theme** and **Reduced motion**. Report styling is under Memory. |
| Editor | Font size, line height, tab size, word wrap, minimap, autosave delay, and whether Markdown and HTML open in preview or edit mode. See [editors](../studio/editors.md). |
| Browser | **Default browser for new tabs**, home page, search URL, downloads, and what Rem may do in the browser. See [Browser](../studio/browser.md). |
| Terminal | Shell, font, cursor and scrollback. See [terminal](../studio/terminal.md). |
| Providers | Enabled providers, executable overrides, allowed and denied models, defaults, and **Refresh providers**. See [provider setup](../setup.md). |
| Usage | Remaining allowances and usage measured in Raticode. See [usage](../rem/usage.md). |
| Rem | **Self-work** (on or off, max threads, backlog pulling, Rem's projects and the latest report), commit messages, **Tidy voice messages**, default scope, avatar and thoughts, **Swarm access**, **Rem task workers**, **Queue tests and builds**, default resources, and the provider, model and effort for new threads and for Rem's chat. See [Rem's home](../rem/home.md). |
| Memory | Second Brain, conversation archive, report output and report themes |
| Developer | The application log, diagnostics, and **Feature Preview** switches for experimental features (phone and desktop pairing, Organizations, Swarms, Second Brain) |
| Layout | Arrangement, which side the project and Rem panes sit on, what the Rem pane shows (threads or Rem's home), terminal location, startup panes, pane sizes and **Reset layout** |
| Plugins | Which plugins show in the plugin rail, their order, and **Lock plugin order**. Hiding **Browser** removes its icon; links, Rem's tabs and `Ctrl+J` still open it. |
| Keybindings | Assign, clear or restore shortcuts. See [shortcuts](./shortcuts.md). |

**Speech recognition** uses the built-in recognizer on macOS and Windows, with **Test speech recognition** on macOS and **Download offline model** for the offline model.

Some settings depend on your platform, Feature Preview switches and installed providers. Go by the labels in your installed app.

## Providers and thread choices

- Provider settings decide which providers, models and permissions are available.
- Each thread's own provider, model and effort picker decides its next turn. Changing the picker doesn't send anything. See [Providers, models and effort](../rem/providers.md).
- Tools belong to the thread too. When a tool is missing, check the thread's shell, web, skills and MCP choices first.

## Memory

- Turn on Second Brain only after choosing a knowledge folder.
- Set an archive folder separately if you want portable copies of conversations.
- Report format and theme apply to future reports. They don't turn on archiving or change existing files.

See [Second Brain](../knowledge/second-brain.md), [Conversation archive](../knowledge/archive.md) and [Report themes](../knowledge/themes.md).

## Application log

**Settings > Developer** opens on the **Application log**: messages from the desktop app, its backend and phone connections, newest first.

- **Filter:** turn levels (Errors, Warnings, Info, Debug) on or off, pick a source, or search. Debug is hidden until you turn it on.
- **Pause:** **Following** stops the list moving while you read and counts the entries that arrive meanwhile.
- **Details:** entries with a traceback open to show the rest.
- **History:** **Include earlier launches** adds the end of the log from before this launch. Useful after a crash.
- **Copy shown** copies every entry that matches your filters, as plain text.

### Select entries

Drag across rows, Shift-click to extend, or Ctrl-click (Cmd-click on a Mac) to add or remove a row. From the keyboard, use Shift with the arrow keys, or Ctrl+A for every row shown. Selecting pauses the list; Escape clears the selection and resumes it.

Right-click the selection, or use the bar above the list, to:

- **Copy** the entries as plain text, oldest first, with tracebacks.
- **Ask Rem** about them. Settings closes and the entries are attached to Rem's message box as a text file, with the app version and log path. Add your question; nothing is sent until you send it. Text you'd already typed stays. With the Rem plugin off, the entries go to the open thread. Very large selections keep the newest entries and say how many were left out.

### What the log covers

| Included | Not included |
| --- | --- |
| Desktop app warnings and errors, window crashes, page errors and built-in browser problems | Electron and Chromium's own native messages, such as GPU or GLib warnings. They only reach a terminal. |
| Backend messages at every level, with the name of the code that wrote them, and anything else the backend prints, such as a crash before logging started | Problems on the phone that never reach the computer, such as the wrong Wi-Fi network or a firewall blocking the device port |
| Phone and paired-desktop connections: the listener's address, Tailscale changes, sign-ins and disconnects with reason and duration, reconnects, repeated sign-in failures (summarized), and a warning when the computer's address changes while phones still use the old one | Message and conversation content, pairing codes, keys and phone addresses. A phone's address is shown only by kind, such as Tailscale or local network. |

To see native messages on a Mac, quit Raticode and start `/Applications/Raticode.app/Contents/MacOS/Raticode` from Terminal. If the phone times out and nothing appears in the log, check the computer's firewall (on a Mac: **System Settings > Network > Firewall**).

Common credential fields are redacted, but an error can still quote a file path or a value from the failing step. Read diagnostics before you publish them. The log isn't the conversation archive or a full transcript.

### Log files

- The desktop writes JSON records to `app.jsonl` in Electron's logs folder (on a Mac, `~/Library/Logs/Raticode`). Settings shows the exact path.
- Each record has a level (`debug`, `info`, `warn` or `error`) and a source.
- Rotation keeps the current file and three backups of up to 5 MB each.
- The live view keeps the newest 5,000 entries of this launch. If one source writes more than 200 messages in a second, the rest are skipped and an entry says how many.

### Diagnostics

Below the log, the page shows the application and desktop data folders, the log path, runtime versions and backend state. You can open those folders, copy diagnostics, open developer tools or restart the backend. Restarting can interrupt work, so check running turns and runs first.

## Change one thing at a time

When something goes wrong, change one setting, check the effect, and note the old value. Don't erase the desktop profile as a first step: it holds working chat state and other preferences.

See [Keyboard shortcuts](./shortcuts.md) and [Troubleshooting](./troubleshooting.md).
