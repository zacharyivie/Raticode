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
