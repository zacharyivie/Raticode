---
title: Keyboard shortcuts
description: Navigate the studio, find any command or file from the keyboard, edit files, control the graph, compose Rem messages, and run a workflow from any app.
pageClass: docs-layout
---

# Keyboard shortcuts

These are the default bindings. **Settings > Keybindings** shows your current assignments and lets you change them.

`Mod` means Command on macOS and Ctrl on Windows or Linux. Bindings explicitly labelled Ctrl use Ctrl on every platform. Comma-separated chords are pressed in sequence.

## Application and workspace

| Action | Default |
| --- | --- |
| Show all commands, the [command palette](#command-palette-and-quick-open) | Mod+Shift+P |
| [Quick open](#command-palette-and-quick-open) a file, workflow, Rem thread or Settings section | Mod+P |
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
| Dictate to Rem from any app, once turned on in **Settings > Devices** (from 0.5.5) | Ctrl+Alt+Space, in every app ([details](../rem/attachments.md#dictate-from-any-app)) |
| Ask Rem about your screen, once **Show Rem your screen** and **Dictate to Rem from any app** are on in **Settings > Devices** (from 0.5.5) | Ctrl+Alt+Shift+Space, in every app ([details](../rem/attachments.md#show-rem-your-screen)) |
| Run a workflow, once you add a shortcut from its menu | The key you choose, in every app ([details](#run-a-workflow-from-any-app)) |

Run starts executable work. Check which workflow is active before using its shortcut. Shortcuts yield to focused controls when their input handling takes precedence.

## Command palette and quick open

*New in Raticode 0.6.0.*

Press **Mod+Shift+P** for the command palette. It lists every command in **Settings > Keybindings** with the key it has now, plus commands that have no key:

- **New workflow**, **All workflow runs** and **Pause all triggers** (it reads **Resume all triggers** while they're paused).
- A **Go to** entry for every plugin in the activity rail: **Go to Rem**, **Go to Workflows**, **Go to File explorer**, **Go to Browser tabs**, **Go to Search**, **Go to Source control**, **Go to Kanban**, **Go to Posts**, **Go to Swarms**, **Go to Organizations** and **Go to Second Brain**. Each opens the plugin as its rail button does, and Go to Rem opens Rem's home. Common words find them too, such as `git` for Source control, `tickets` for Kanban and `find` for Search. Posts, Swarms, Organizations and Second Brain appear only once they're on in **Settings > Developer > Feature Preview**. A plugin you hid from the rail stays in the list, greyed out, until you show it again in **Settings > Plugins**.
- **Settings:** followed by a section name, such as **Settings: Devices**, opens Settings at that section.

Press **Mod+P** for quick open. It searches the open project's files, its workflows, the titles of your Rem threads on this computer and the Settings sections. Files come from git: tracked files and new files git doesn't ignore. A folder outside git is read directly, skipping `node_modules`, `.venv`, `dist` and `build`. Quick open shows at most 20,000 files and says so when a project has more.

Both work the same way:

- Type a few letters in order, such as `tgp` for **Toggle project pane** or `app jsx` for `App.jsx`. Words can come in any order. Matching letters are highlighted.
- The items you chose last come first, marked **recent**. Each list keeps its own recent items.
- Up and Down move, Enter chooses and Escape closes. Page Up and Page Down move eight rows. Pressing the same key again closes the list, and the other key switches to the other list.
- A command that can't run right now stays in the list, dimmed, and the bottom line says why, for example **Open a file in the code editor first.** Commands that work only in other apps, such as **Dictate to Rem from any app**, show their key but don't run from here.
- **Run workflow** names the workflow it will run, in its row and in the bottom line (**Enter runs** followed by the workflow's name), before you press Enter. It runs the workflow whose tab is in front, the same as Mod+Enter.

Both keys give way when a terminal or the code editor has focus: the terminal sends them to the shell and the editor keeps its own keys. Click outside first, or choose **View > Command Palette...** or **File > Quick Open...** from the menus. In a web page, Mod+P prints the page, as the [Browser](#browser) table below says, while Mod+Shift+P still opens the command palette.

Change either key in **Settings > Keybindings**, under **Show all commands** and **Quick open**. They accept a two-key sequence, like **Open project folder**.

The palette is for you at the keyboard. It gives Rem no new actions.

## Menus

Pop-up menus work the same way with the keyboard. These include the right-click menus for terminal groups, terminals, editor tabs and project folders in the **Workflows** list, Rem's **Scoped to** project menu, and Second Brain's menus, such as a document's **⋯** menu:

- On a row that has a right-click menu, focus a button in the row and press the Menu key or Shift+F10 to open it.
- The first item you can choose has focus when the menu opens. Up and Down move and wrap around, and Home and End jump to the first and last items. Items that are dimmed are skipped.
- Enter or Space chooses an item. Escape closes the menu and puts focus back on what opened it.

New in 0.6.1.

## Tabs and choices

A row of tabs, or a set of buttons where you pick one, is a single Tab stop. Tab lands on the one that's chosen. Examples are the bottom panel's views, a task's **Kind of work** and **How often**, and Settings choices such as **Allowance use**:

- Left and Right move to the next or previous one and wrap around. In a set of choices, Up and Down work too.
- Home and End jump to the first and last.
- The one you move to is chosen at once. A setting saves straight away, as it does when you click it.

New in 0.6.1.

## Run a workflow from any app

*New in Raticode 0.6.0.*

Give a workflow its own key and run it from whatever app you're in, while Raticode is running, even minimized or closed to the tray. Shortcuts stop working when you quit Raticode.

### Add a shortcut

1. In the **Workflows** list, open the workflow's menu (the **⋮** button, or right-click it) and choose **Add shortcut…**.
2. Click **Record a key** and press the combination. The dialog says what pressing it will do.
3. Choose **Save shortcut**.

Each workflow has one shortcut. **Add shortcut…** on a workflow that has one changes its key or removes it.

The key follows the same rules as [Dictate from any app](../rem/attachments.md#dictate-from-any-app): one combination, not a sequence, with Ctrl, Alt or the system key, except F1 to F24 on their own or with Shift. Raticode refuses a key that **Dictate to Rem from any app** or **Ask Rem about your screen** uses in **Settings > Keybindings**, and a key another workflow's shortcut has. If you later give a dictation shortcut a workflow's key, the dictation shortcut keeps it, and **Settings > Devices** says which workflow shortcut needs a new key.

### What a press does

- **A workflow with no text input runs straight away**, as a run you started, the same as pressing **Run**. Inputs that have defaults use them.
- **A workflow with a plain text input opens a small Run sheet** on top of the app you're in. The text box is filled from the clipboard, with a warning that copied text can contain instructions you didn't write; read it before you run. Enter runs it, Shift+Enter adds a line and Escape closes the sheet. See [Fill an input from a shortcut](../workflows/inputs.md#fill-an-input-from-a-shortcut) for which input gets the text.
- A workflow with a required input the shortcut can't fill, such as a number, a choice or a password with no default, can't have a shortcut. Run it from Raticode instead.

For a ready example, copy a link and press the shortcut of the [Summarize a link](../starters/summarize-a-link.md) starter, which reads the page and sends a summary to your phone.

A desktop notice says the run started, and another reports whether it finished, failed or stopped. Clicking either opens the run in Raticode. If a step waits for your [approval](../nodes/approval-gate.md), a notice says so. A run that doesn't start, for example because its inputs don't match their schema, says why in the Run sheet or a notice.

### Only the version you approved runs

A shortcut runs a workflow from another app, often without you looking at Raticode, so it runs only the version you approved. The approval covers the same files as a [trigger's](../workflows/triggers.md#approved-versions): the workflow file, its agent prompt files, `prompt-file` templates, Python and shell scripts, and child workflows with their own such files.

- **Adding a shortcut** approves the workflow as it is at that moment.
- **Changing its key** approves nothing. A change that is waiting for review keeps waiting.
- **Saving the workflow in the Studio window** approves the workflow file you saved and nothing else. A prompt file, script or child workflow that changed since your last approval still waits for review.
- **Anything else that changes those files**, such as Rem, a worker, another editor or a `git pull`, needs your review before the shortcut runs the workflow again.

When you press the key of a changed workflow, nothing starts. A desktop notice says **&lt;workflow&gt; changed since you added its shortcut**. Clicking it opens **Settings > Devices** with that shortcut's **Review changes**: each changed file with a diff, and **Approve this version**, which approves exactly what you reviewed. If a diff was cut short, or a file is binary or too large to show, it asks a second time and names those files so you can open them first. The shortcut's row in **Settings > Devices** shows **Changed: review** until you approve.

A shortcut added before shortcuts asked for approval says **&lt;workflow&gt; needs your approval before its shortcut runs it**, and its row shows **Not approved: review**. Approve it once in the same way.

Only you can approve. Rem and workers can't: the app refuses their requests, and `Raticode rattish shortcuts approve` refuses to run inside an agent turn.

### Manage shortcuts

**Settings > Devices > Workflow shortcuts** lists every shortcut with its key, what it does and whether the key works in other apps on this desktop. A shortcut whose workflow changed shows **Changed: review**. Click a key to record a new one, or choose **Remove**. Removing a shortcut keeps the workflow.

From a terminal:

```bash
Raticode rattish shortcuts list --format json
Raticode rattish shortcuts approve SHORTCUT_ID
Raticode rattish shortcuts remove SHORTCUT_ID
```

`list` says which shortcuts are waiting for review. `approve` prints the diff and asks before approving; `--yes` approves without asking, and a change it couldn't show in full also needs `--unseen`. These work without a running backend. A running Raticode lets go of a removed key within half a minute. Adding a shortcut needs the app, which records the key. Shortcuts belong to this computer and are kept in `workflow-shortcuts.json` in the [data folder](./settings.md).

### How each system handles it

On Windows, macOS and Linux on X11, Raticode registers the key itself. On Linux on Wayland, Raticode asks your desktop to add it, the same way as the [dictation shortcut](../rem/attachments.md#linux-on-wayland):

- **KDE Plasma** lists it as **Run** followed by the workflow's name under **System Settings > Keyboard > Shortcuts > Raticode**.
- **GNOME 48 or later and other portal desktops** may show a prompt the first time, and again when you add another shortcut.
- **GNOME before 48** gets a custom shortcut named **Raticode: Run** followed by the workflow's name.
- **Other desktops**: Settings shows a command, `Raticode --run-workflow-shortcut=ID`, to bind to a key in the desktop's own settings. It reaches the running Raticode and only presses that shortcut.

Only the KDE route of the dictation shortcut has been tried on a real desktop. The workflow shortcut routes are tested against simulated desktops.

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
| Add node, from a searchable picker | Tab ([details](../studio/editors.md#add-a-node)) |
| Select all nodes | Mod+A |
| Delete selection | Delete |
| [Undo the last graph edit](../studio/editors.md#undo-and-redo-graph-edits) | Mod+Z |
| [Redo a graph edit](../studio/editors.md#undo-and-redo-graph-edits) | Mod+Shift+Z, or Ctrl+Y on Windows |
| Zoom in | = |
| Zoom out | - |
| Fit workflow | F |

## Browser

In the Browser plugin, `Ctrl+Tab` and `Ctrl+Shift+Tab` move between tabs of every browser, and the file close shortcut closes the selected web tab. `Ctrl+T` inside a tab opens a new tab in the same browser.

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
| Back / Forward on macOS | Cmd+[ / Cmd+] |
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
