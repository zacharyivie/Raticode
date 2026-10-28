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
| Open browser | Ctrl+J |
| Show graph editor | Mod+1 |
| Show code editor | Mod+2 |
| Toggle project pane | Ctrl+B |
| Toggle Rem | Ctrl+L |
| Run workflow | Mod+Enter |
| Toggle bottom panel | Mod+` |
| New terminal | Ctrl+T |
| Close focused terminal | Ctrl+W |

Run starts executable work. Check which workflow is active before using its shortcut. Shortcuts yield to focused controls when their input handling takes precedence.

## Code editor

| Action | Default |
| --- | --- |
| New file | Mod+N |
| Save active file | Mod+S |
| Close active file | Mod+W |
| Find in file | Mod+F |
| Toggle word wrap | Alt+Z |

Editor-only commands require a relevant editor to be focused. Standard text-editing actions are also available through the editor and application menus.

## Graph editor

| Action | Default, with graph focused |
| --- | --- |
| Select all nodes | Mod+A |
| Delete selected nodes | Delete |
| Zoom in | = |
| Zoom out | - |
| Fit workflow | F |

## Rem composer

Enter sends a message, or submits a steering message while a turn is running. Shift+Enter inserts a newline. From the new-thread composer, Ctrl+Enter starts a thread in the background.

The composer owns these keys even if a global command has a similar binding. See [Chat and threads](../rem/chat.md) for steering and stop behavior.

## Customize

Search Keybindings for an action, capture the new combination, and check for conflicts. Clearing a binding removes that shortcut without removing the underlying command. Restore defaults when you want the documented mappings back.
