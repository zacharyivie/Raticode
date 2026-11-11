---
title: Source control
description: Review diffs, stage changes, create commits, and work with branches and worktrees.
pageClass: docs-layout
---

# Source control

Review your changes before committing them, with staged and unstaged files shown separately. Source control has three tabs: **History**, where it opens, **Changes** and **Branches**.

## Inspect a change

Open Source control in a Git project. Select a file to open its diff. Staged changes compare HEAD with the index; unstaged changes compare the index with the working file. New and deleted text files have an empty side. Images and PDFs show both versions side by side with their sizes; other binary files say that a text comparison isn't available.

Staged and deleted-file views are read-only. Edit the working file to make a correction, then review its new diff.

## Stage and commit

Use **Stage** or **Unstage** on individual files, or **Stage all**, **Unstage all** and **Discard all** on a group. The commit box commits only staged changes; Ctrl+Enter (Cmd+Enter on macOS) in the message box commits. Review the staged set, write a message, and commit when it matches your intended change.

The Rem generation button drafts a message from the change context; it does not replace reviewing the diff. Configure the template, the provider and model that write messages, and **Auto commit changes** in **Settings > Rem > Commit messages**. The default has a `fix`, `feat`, `test`, or `chore` subject followed by one to eight concise change bullets.

## History

**History** draws the branch graph with branch and tag labels and their ahead and behind counts. Each commit shows its author, time, short ID with a copy button, and its added and removed lines (or how many binary files changed); click a commit to expand its full message and files. Filter by branch, or by message, author, branch name or ID, and use **Load more history** for older commits, up to 5,000.

Right-click a commit for **Open commit diff**, **Soft reset**, **Hard reset**, **Checkout new branch here**, **Create worktree here** and **Detach at commit**. A hard reset discards uncommitted changes; check before choosing it.

## Branches and remote work

Branch switching uses Git's checks. If changes would be overwritten, **Stash & switch** saves them, including untracked files. Saved stashes are listed under **Stashes** in Branches: **Preview stash** shows its diff and any conflicts, and **Apply stash** restores it while keeping the stash as a backup. **Discard** and **Discard stashes** delete them. Resolve conflicts before continuing.

**Pull** uses fast-forward-only behavior. Divergence requires a deliberate merge or rebase in the terminal. **Push** uses the branch's upstream; **Publish** selects an existing remote for a branch without one. Git's credential helper owns remote authentication.

Ahead and behind counts come from local tracking references. Reading status does not fetch a remote, so counts can be stale until you fetch or pull.

## Revert deliberately

Reverting unstaged changes restores the index version. Reverting staged changes refuses a file that also has unstaged edits. New files go to the operating system trash. Save editor buffers first; a revert affects disk files, not an unsaved draft you still intend to keep.

## Worktrees

A worktree gives another branch a separate working folder. Use worktree controls to create isolated review or implementation directories; click a worktree to switch the project to it. Removing a worktree that has uncommitted changes asks you to confirm **Discard changes and remove**, which permanently deletes those changes and untracked files but keeps the branch. Inspect and preserve its changes first.

## Merge and rebase a branch

Right-click a branch or worktree, or use its merge icon, and choose **Merge**, **Squash merge**, **Fast-forward only**, **Merge with a merge commit** or **Rebase**. **Target branch** is a list you can type in: typing filters it, and Enter, a click or an exact name picks a branch. With nothing typed, **Recommended** comes first: the checked-out branch, the branch's upstream, protected branches (see below) and each remote's default branch, such as `origin/main`. Local branches follow, then remote branches, each group in alphabetical order. Escape, or leaving the field with a partial name, keeps the branch you had.

A rebase can use a remote branch as its target, so you can rebase a branch onto `origin/main` without updating local `main` first. The remote branch is as of your last fetch; Raticode does not fetch for you. Merges change their target, so they only go into local branches, and remote branches show as **Rebase only** for them. Every operation still checks for conflicts in a separate copy first, refuses to run over uncommitted changes, and stops if either branch moved since that check.

**Clean up branches** (the eraser in Branches) lists local branches that have no commits of their own, with their worktrees. A branch qualifies when a newer branch contains it, or when a protected branch is on the same commit. Protected branches are `main`, `master`, the remote's default branch and anything under **Always keep**, which accepts names and patterns such as `release/*` for this project or all projects. Protected branches are never deleted. Cleanup also keeps the current worktree and the main folder, worktrees with uncommitted or untracked files, a merge or rebase in progress, locked worktrees, worktrees a program is working in (Linux only), worktrees whose branch has no commits yet, and worktrees Raticode's own tasks are using. Ignored files such as builds and a linked `node_modules` go with the worktree folder; a link's target is left alone. Each branch is checked again right before deletion, and one that moved, gained files or is no longer contained is kept and reported.

The Worktrees list, branches, Changes and an open History view update on their own when worktrees are added, removed or pruned, including by agents, swarms and `git worktree` in a terminal, and when commits or staged changes land. Raticode watches only the repository's Git metadata, not your files. Edits to working files show up within a few seconds. **Refresh source control** reads everything again on demand.

If a merge or rebase stops on conflicts, a conflict panel lists the files with **Continue**, **Abort** and **Resolve conflicts with Rem**, which hands the conflicts to Rem in a thread.

[Swarms](../advanced/swarms.md) use review and assignment worktrees to keep concurrent agent work separate. A worktree is still part of the same repository and shares its Git metadata.
