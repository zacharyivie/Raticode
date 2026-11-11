---
title: Terminal
description: Run commands in the integrated terminal and configure shell and display settings.
pageClass: docs-layout
---

# Terminal

Keep a real shell alongside the editor for dependency installation, tests, and commands you want to run yourself.

## Open a session

Open the bottom panel and select Terminal, or, if **Settings > Layout > Terminal location** is **Editor tab**, use its own editor tab. Create a new session when you need a separate process. Sessions are grouped by project; rename a session or group, and drag sessions between groups. When Git opens an editor from the terminal, such as for a commit message, it opens in a Raticode editor tab. Check the current directory before running commands, especially after switching projects or opening a worktree.

The terminal is independent of Rem's command-execution toggle. Disabling shell tools for a provider does not prevent you from typing a command in your own terminal.

## Choose a shell

In **Settings > Terminal**, choose the account's system login shell, Bash, or Zsh for new sessions. Windows uses PowerShell. Changing the shell setting applies to new terminals; it does not replace an already running process.

Set font size, line height, blinking cursor, and scrollback capacity in the same section. Scrollback is retained display history, not a full permanent record of every command's output.

## Use the CLI

If `Raticode` is on your PATH, you can validate the current workflow without running it:

```bash
Raticode rattish check PATH_TO_WORKFLOW --format json
Raticode rattish preflight PATH_TO_WORKFLOW --format json
```

Use the actual source path returned by `Raticode rattish list --format json`. If `Raticode` is not found, see [Installation](../installation.md).

For source development, select the project's Node version through nvm before npm commands. Provider CLIs launched in a terminal use that shell's environment; desktop discovery can have a different PATH.

## Stop a process

Use the shell's interrupt shortcut for a foreground command. Closing a terminal session ends that session; it is not the same action as stopping a workflow or steering Rem. Use the run's own Stop action or Rem's Stop control for those operations.

See [Keyboard shortcuts](../reference/shortcuts.md) and [Runs and approvals](./runs.md).

