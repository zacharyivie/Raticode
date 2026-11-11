---
title: Keyboard shortcuts
description: Navigate the studio, edit files, control the graph, and compose Rem messages.
pageClass: docs-layout
---

# Keyboard shortcuts

These are the default bindings. **Settings > Keybindings** shows your current assignments and lets you change them.

`Mod` means Command on macOS and Ctrl on Windows or Linux. Bindings explicitly labelled Ctrl use Ctrl on every platform. Comma-separated chords are pressed in sequence.

## Application and workspace

| Action | Default |
| --- | --- |
| Open settings | Mod+, |
| Open file | Mod+O |
| Open project folder | Mod+K, Mod+O |
| Open a new tab in the Browser plugin, in the default browser | Ctrl+J |
| Reopen closed browser tab | Mod+Shift+T |
| Show graph editor | Mod+1 |
| Show code editor | Mod+2 |
| Toggle project pane | Ctrl+B |
| Toggle Rem | Ctrl+L |
| Run workflow | Mod+Enter |
| Toggle bottom panel | Mod+` |
| New terminal | Ctrl+T |
| Close terminal | Ctrl+W |
| Dictate a message | Mod+Alt+V (press again to finish) |

Run starts executable work. Check which workflow is active before using its shortcut. Shortcuts yield to focused controls when their input handling takes precedence.

## Code editor

| Action | Default |
| --- | --- |
| New file | Mod+N |
| Save active file | Mod+S |
| Close active file | Mod+W |
| Find in file | Mod+F |
| Toggle word wrap | Alt+Z |

Editor-only commands require a relevant editor to be focused. The in-app **File**, **Edit**, **Selection**, **View**, **Terminal** and **Help** menus list further editor shortcuts, such as Ctrl+H to replace, Ctrl+/ to toggle a comment and Shift+Alt+F to format.

## Graph editor

| Action | Default, with graph focused |
| --- | --- |
| Select all nodes | Mod+A |
| Delete selection | Delete |
| Zoom in | = |
| Zoom out | - |
| Fit workflow | F |

## Integrated browser

Inside the Browser plugin, `Ctrl+Tab` and `Ctrl+Shift+Tab` move between tabs of every browser, and the file close shortcut closes the selected web tab. `Ctrl+T` inside a tab opens a new tab in the same browser.

These always work in a browser tab:

| Action | Default |
| --- | --- |
| Focus the address bar | Alt+D |
| Find in page | Mod+F |
| Reload | Mod+R |
| Back / Forward | Alt+Left / Alt+Right |

These work only while the browser has focus, so they don't clash with app shortcuts:

| Action | Default |
| --- | --- |
| Focus the address bar | Mod+L |
| Reload / hard reload | F5 / Mod+Shift+R |
| Developer tools | F12 |
| Print | Mod+P |
| Bookmark the page | Mod+D |
| History | Ctrl+H (Cmd+Y on macOS) |
| Reopen closed tab | Mod+Shift+T |
| New private tab | Mod+Shift+N |
| Zoom in / out / reset | Mod+= / Mod+- / Mod+0 |

## Source control

Ctrl+Enter (Cmd+Enter on macOS) in the commit message box commits the staged changes.

## Rem composer

Enter sends a message, or submits a steering message while a turn is running. Shift+Enter inserts a newline. From the new-thread composer, Ctrl+Enter starts a thread in the background.

The composer owns these keys even if a global command has a similar binding. See [Chat and threads](../rem/chat.md) for steering and stop behavior.

## Customize

Search Keybindings for an action, capture the new combination, and check for conflicts. Clearing a binding removes that shortcut without removing the underlying command. Restore defaults when you want the documented mappings back.
