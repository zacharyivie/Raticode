---
title: Terminal
description: Run commands in the integrated terminal and configure shell and display settings.
pageClass: docs-layout
---

# Terminal

A real shell next to the editor, for installing dependencies, running tests and anything else you want to run yourself.

## Open a session

Open the bottom panel and pick **Terminal**. If **Settings > Layout > Terminal location** is **Editor tab**, it opens as its own editor tab instead.

- Start a new session when you need a separate process.
- Sessions are grouped by project. Rename sessions and groups, and drag sessions between groups.
- When Git opens an editor from the terminal, for example for a commit message, it opens in a Raticode editor tab.
- Check the current folder before running commands, especially after switching projects or opening a worktree.

The terminal doesn't follow Rem's **Run commands** setting. Turning shell tools off for a provider doesn't stop you typing commands in your own terminal.

## Shell and display

In **Settings > Terminal**:

- Pick the shell for new sessions: the account's login shell, Bash or Zsh. Windows uses PowerShell. A change applies to new terminals only.
- Set font size, line height, blinking cursor and scrollback size. Scrollback is display history, not a permanent record of every command's output.

## Find in a terminal

*New in Raticode 0.6.2.*

With a terminal focused, press **Ctrl+Shift+F** on Windows or Linux, or **Command+F** on macOS. You can also right-click in the terminal and choose **Find**. A find bar opens at the top right of that terminal and searches its output and scrollback.

- Type to search. The first match is selected and every match is highlighted. The bar shows which match is selected and how many there are, such as **2 of 5**, or **No results**. Counting stops at 1,000, shown as **1000+**.
- **Enter** or **Next** goes to the next match and **Shift+Enter** or **Previous** to the one before. Both wrap around.
- **Match case** finds only text with the same capital letters. It's off at first.
- If you selected text on one line before opening Find, the bar starts with that text.
- **Escape** or **Close** closes the bar and puts focus back in the terminal. The selected match stays selected, so you can copy it.
- New output is searched as it arrives while the bar is open.

Plain **Ctrl+F** still goes to the shell, where it moves the cursor forward. In the rest of Raticode, Ctrl+Shift+F opens the **Search** plugin; in a terminal it opens Find instead. Change the key in **Settings > Keybindings**, under **Find in terminal**.

## Open links in output

*New in Raticode 0.6.2.*

Web addresses and file paths in terminal output are links. Hold Ctrl (Cmd on macOS) and click one to open it. A plain click never opens a link, so you can still click and drag to select text. Hover over a link to see where it goes.

- `http://` and `https://` addresses open the same way a link in Rem's chat does. Other kinds of address, such as `file:` or `javascript:`, are never links. The same goes for links a program marks up in its output.
- A file path opens in the code editor. When the output names a line, the file opens at that line. These forms are read on every platform:

  | Output | Opens |
  | --- | --- |
  | `src/app.py:42` | line 42 |
  | `./frontend/x.jsx:10:5` | line 10, column 5 |
  | `File "/home/ana/app.py", line 42, in main` (a Python traceback) | line 42 |
  | `src/app.ts(42)` | line 42 |
  | `src/app.ts(42,5): error` (TypeScript, MSBuild and other compilers) | line 42, column 5 |

  A relative path is looked up in the terminal's current folder first, then in the project folder. A traceback path can have spaces because it's in quotes. Placeholders such as `File "<stdin>"` stay plain text.
- On Windows, paths with a drive letter or backslashes work too, such as `C:\Users\ana\proj\src\app.py:42`, `src\app.py:42:5` or `.\app.py:3`. Network paths such as `\\server\share\file.txt` don't become links.
- A path becomes a link only when the file exists. Folders and paths that don't exist stay plain text.
- Opening a file outside the open project asks first. The question is about where the link really leads, so a project file that is a symlink to a file elsewhere asks too and names the real file. Raticode gets no access to that file's folder unless you choose **Open file**.

Terminal output can come from anything you run, so check where a link goes before you open it.

## Ask Rem about selected output

*New in Raticode 0.6.0.*

Right-click in a terminal for **Copy**, **Select all** and **Ask Rem about selection**. Select some output first, such as an error and the lines before it, then choose **Ask Rem about selection**. The selected text goes to Rem's message box as a file, with the shell and folder named. Nothing is sent until you write your question and send it.

- Up to the last 200 lines, cut to 8 KB, are kept.
- The text is marked as untrusted terminal output, so Rem treats anything in it as data, not instructions.
- Selected text can include secrets a command printed. Check it before you send.

## Use the CLI

If `Raticode` is on your PATH, you can check a workflow without running it:

```bash
Raticode rattish check PATH_TO_WORKFLOW --format json
Raticode rattish preflight PATH_TO_WORKFLOW --format json
```

- Get the real source path from `Raticode rattish list --format json`.
- If `Raticode` isn't found, see [Installation](../installation.md).
- For source development, pick the project's Node version with nvm before npm commands.
- Provider CLIs started in a terminal use that shell's environment. The desktop app's provider discovery can see a different PATH.

## Stop a process

- Use the shell's interrupt shortcut for a command in the foreground.
- Closing a session ends it.
- That's different from stopping a workflow or steering Rem; use the run's **Stop** or Rem's **Stop** for those.

See [Keyboard shortcuts](../reference/shortcuts.md) and [Runs and approvals](./runs.md).
