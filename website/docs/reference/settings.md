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
| General | **Data folder**, initial sidebar, file autosave, **Check for updates** (on startup and, from 0.5.5, every 10 minutes; a newer version shows an **Update** button next to the check button), and **Delete archived threads** after 7, 30 or 90 days |
| Devices | Microphone and level, **Dictation** (press or hold), **Send after dictation**, **Dictate to Rem from any app** (off by default), **Show Rem your screen** (off by default), **Speech recognition**, **Workflow shortcuts** (run a workflow from any app with a key), and paired phones and computers once pairing is on. See [dictation](../rem/attachments.md#dictate-a-message), [dictating from any app](../rem/attachments.md#dictate-from-any-app), [showing Rem your screen](../rem/attachments.md#show-rem-your-screen) and [pairing](../mobile/pairing.md). |
| Computers | *New in 0.6.0.* Your computers in order, this computer's **Worker limit** (0 is Auto), and where Rem and each organization run their work. See [Where work runs](../rem/home.md#where-work-runs). |
| Appearance | **Color theme** and **Reduced motion**. Report styling is under Memory. |
| Editor | Font size, line height, tab size, word wrap, minimap, autosave delay, and whether Markdown and HTML open in preview or edit mode. See [editors](../studio/editors.md). |
| Browser | **Default browser for new tabs**, **Default web browser** (make Raticode the system's browser, from 0.6.0), **Links from other apps open in**, a section per browser including **Raticode (gecko) > Let Rem use Raticode (gecko)**, home page, search URL, downloads, and what Rem may do in the browser. See [Browser](../studio/browser.md). |
| Terminal | Shell, font, cursor and scrollback. See [terminal](../studio/terminal.md). |
| Providers | From 0.6.0, two parts: **Coding apps**, then **For workflows only** (local model servers and classification models, folded with a one-line summary). Enabled providers, executable overrides, [which models show in model menus](../rem/providers.md#hide-models-from-menus), defaults, and **Refresh providers**. See [provider setup](../setup.md). From 0.5.5, **Classification models** holds the [Jev](../workflows/jev.md) key, its on/off switch and **Check key**. **Local model servers** adds, checks and removes [local model server](../setup.md#local-model-servers) profiles for workflow agent nodes (from 0.5.5). |
| Usage | Remaining allowances, with a card per provider account and its weekly pace, **Limit alerts** (80% and 95% by default), **Tokens in Raticode** and **Cost at API prices**. See [usage](../rem/usage.md). |
| Rem | **Self-work** (on or off, max threads, backlog pulling, **Approve its own backlog tickets**, **Limits** including **Weekly allowance per day**, self-work's own **Accounts**, Rem's projects and the latest report), **Accounts** (which provider account Rem's work uses), commit messages including **Remove Co-authored-by lines**, **Tidy voice messages**, default scope, avatar and thoughts, **Swarm access**, **Queue tests and builds**, default resources, and the provider, model and effort for new threads and for Rem's chat. See [Rem's home](../rem/home.md). |
| Memory | Second Brain, conversation archive, **Purge archive copies** (Never by default, or 7, 30 or 90 days after deletion), report output and report themes |
| Developer | The application log, diagnostics, and **Feature Preview** switches for experimental features (phone and desktop pairing, Organizations, Swarms, Second Brain, Posts) |
| Layout | Arrangement, which side the project and Rem panes sit on, what the Rem pane shows (threads or Rem's home), terminal location, startup panes, pane sizes and **Reset layout** |
| Plugins | Which plugins show in the plugin rail, their order, and **Lock plugin order**. Hiding **Browser** removes its icon; links, Rem's tabs and `Ctrl+J` still open it. |
| Keybindings | Assign, clear or restore shortcuts. See [shortcuts](./shortcuts.md). |

**Speech recognition** uses the built-in recognizer on macOS, and on Windows until the offline model is downloaded. It has **Test speech recognition** on macOS and **Download offline model** (about 475 MB) for the offline model. See [speech recognition](../rem/attachments.md#speech-recognition).

Some settings depend on your platform, Feature Preview switches and installed providers. Go by the labels in your installed app.

## Providers and thread choices

- Provider settings decide which providers, models and permissions are available.
- Each thread's own provider, model and effort picker decides its next turn. Changing the picker doesn't send anything. See [Providers, models and effort](../rem/providers.md).
- Tools belong to the thread too. When a tool is missing, check the thread's shell, web, skills and MCP choices first.

## Memory

- Turn on Second Brain only after choosing a knowledge folder.
- Set an archive folder separately if you want portable copies of conversations.
- **Purge archive copies** removes the archive copies of deleted threads 7, 30 or 90 days after deletion. **Never (keep everything)** is the default. A purge only changes the archive folder: it can't remove copies already in Git commits or other backups. See [Purge archive copies after a while](../knowledge/archive.md#purge-archive-copies-after-a-while).
- **Delete archived threads** under General removes threads from Rem but keeps their archive copies.
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
