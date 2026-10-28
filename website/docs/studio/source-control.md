---
title: Source control
description: Review diffs, stage changes, create commits, and work with branches and worktrees.
pageClass: docs-layout
---

# Source control

Review your changes before committing them, with staged and unstaged files shown separately.

## Inspect a change

Open Source control in a Git project. Select a file to open its diff. Staged changes compare HEAD with the index; unstaged changes compare the index with the working file. New and deleted text files have an empty side. Images show the versions visually, and other binary files show their sizes.

Staged and deleted-file views are read-only. Edit the working file to make a correction, then review its new diff.

## Stage and commit

Use **Stage** or **Unstage** on individual files. The commit box commits only staged changes. Review the staged set, write a message, and commit when it matches your intended change.

The Rem generation button drafts a message from the change context; it does not replace reviewing the diff. Configure the template in **Settings > Rem > Commit messages**. The default has a `fix`, `feat`, `test`, or `chore` subject followed by one to eight concise change bullets.

## Branches and remote work

Branch switching uses Git's checks. If changes would be overwritten, **Stash and switch** saves them, including untracked files. **Apply latest stash** restores the changes on the chosen branch and keeps the stash as a backup. Resolve conflicts before continuing.

**Pull** uses fast-forward-only behavior. Divergence requires a deliberate merge or rebase in the terminal. **Push** uses the branch's upstream; **Publish** selects an existing remote for a branch without one. Git's credential helper owns remote authentication.

Ahead and behind counts come from local tracking references. Reading status does not fetch a remote, so counts can be stale until you fetch or pull.

## Revert deliberately

Reverting unstaged changes restores the index version. Reverting staged changes refuses a file that also has unstaged edits. New files go to the operating system trash. Save editor buffers first; a revert affects disk files, not an unsaved draft you still intend to keep.

## Worktrees

A worktree gives another branch a separate working folder. Use worktree controls to create isolated review or implementation directories. Git prevents removal of a dirty worktree. Inspect and preserve its changes before cleanup.

[Swarms](../advanced/swarms.md) use review and assignment worktrees to keep concurrent agent work separate. A worktree is still part of the same repository and shares its Git metadata.
