---
title: Source control
description: Review diffs, stage changes, create commits, and work with branches and worktrees.
pageClass: docs-layout
---

# Source control

Review a change before you commit it. Staged and unstaged files are listed separately.

Source control has three tabs:

| Tab | What it's for |
| --- | --- |
| **History** (opens first) | The branch graph and past commits |
| **Changes** | Diffs, staging and the commit box |
| **Branches** | Branches, stashes, worktrees, merge and rebase |

## Inspect a change

Open Source control in a Git project and select a file to see its diff.

- **Staged** changes compare HEAD with the index.
- **Unstaged** changes compare the index with the working file.
- New and deleted text files show one empty side.
- Images and PDFs show both versions side by side, with their sizes. Other binary files say a text comparison isn't available.

Staged and deleted-file views are read-only. To correct something, edit the working file and review its new diff.

## Stage and commit

- **Stage** and **Unstage** act on one file. **Stage all**, **Unstage all** and **Discard all** act on a group.
- The commit box commits staged changes only.
- Ctrl+Enter (Cmd+Enter on macOS) in the message box commits.

<AppScreenshot name="source-control" caption="Changes with one staged file. Its diff marks the added line, and the commit message is ready." />

The Rem button drafts a message from the change. It doesn't replace reading the diff.

Set up messages in **Settings > Rem > Commit messages**: the template, the provider and model that write them, and **Auto commit changes**. The default template is a `fix`, `feat`, `test` or `chore` subject followed by one to eight short change bullets.

## History

**History** draws the branch graph with branch and tag labels and their ahead and behind counts.

- Each commit shows its author, time, short ID (with a copy button), and lines added and removed, or how many binary files changed.
- Click a commit to expand its full message and files.
- Filter by branch, or by message, author, branch name or ID.
- **Load more history** loads older commits, up to 5,000.

Right-click a commit for **Open commit diff**, **Soft reset**, **Hard reset**, **Checkout new branch here**, **Create worktree here** and **Detach at commit**.

::: warning
**Hard reset** discards uncommitted changes.
:::

## Branches and remote work

**Switching branches** uses Git's own checks. If changes would be overwritten, **Stash & switch** saves them, untracked files included.

**Stashes** are listed under **Stashes** in Branches:

- **Preview stash** shows its diff and any conflicts.
- **Apply stash** restores it and keeps the stash as a backup.
- **Discard** and **Discard stashes** delete them.

Resolve conflicts before you continue.

**Remotes:**

- **Pull** is fast-forward only. If the branches have diverged, merge or rebase on purpose in the terminal.
- **Push** uses the branch's upstream. **Publish** picks an existing remote for a branch that has none.
- Git's credential helper handles remote sign-in.
- Ahead and behind counts come from local tracking refs. Raticode doesn't fetch to read status, so counts can be stale until you fetch or pull.

## Revert deliberately

- Reverting unstaged changes restores the index version.
- Reverting staged changes refuses a file that also has unstaged edits.
- New files go to the operating system's trash.

A revert changes files on disk, not unsaved editor drafts. Save any draft you want to keep first.

## Worktrees

A worktree gives another branch its own working folder, for isolated review or implementation.

- Create one from the worktree controls, or from a commit with **Create worktree here**.
- Click a worktree to switch the project to it.
- Removing a worktree with uncommitted changes asks you to confirm **Discard changes and remove**. That permanently deletes those changes and untracked files, but keeps the branch. Save what you need first.

## Merge and rebase a branch

Right-click a branch or worktree, or use its merge icon, then choose **Merge**, **Squash merge**, **Fast-forward only**, **Merge with a merge commit** or **Rebase**.

**Target branch** is a list you can type in:

- Typing filters it. Enter, a click or an exact name picks a branch.
- Escape, or leaving the field with a partial name, keeps the branch you had.
- With nothing typed, the order is:
  1. **Recommended**: the checked-out branch, the branch's upstream, protected branches (see below) and each remote's default branch, such as `origin/main`.
  2. Local branches, alphabetical.
  3. Remote branches, alphabetical.

Rebase and merge targets differ:

- **Rebase** can target a remote branch, so you can rebase onto `origin/main` without updating local `main`. The remote branch is as of your last fetch; Raticode doesn't fetch for you.
- **Merges** change their target, so they only go into local branches. Remote branches show as **Rebase only**.

Every operation first checks for conflicts in a separate copy. It refuses to run over uncommitted changes and stops if either branch moved since that check.

If a merge or rebase stops on conflicts, a conflict panel lists the files with **Continue**, **Abort** and **Resolve conflicts with Rem**. The last one hands the conflicts to Rem in a thread.

## Clean up branches

**Clean up branches** (the eraser in Branches) lists local branches with no commits of their own, together with their worktrees. A branch qualifies when a newer branch contains it, or when a protected branch is on the same commit.

**Protected branches are never deleted:** `main`, `master`, the remote's default branch, and anything under **Always keep**. **Always keep** takes names and patterns such as `release/*`, for this project or all projects.

Cleanup also keeps:

- the current worktree and the main folder
- worktrees with uncommitted or untracked files
- a merge or rebase in progress
- locked worktrees
- worktrees a program is working in (Linux only)
- worktrees whose branch has no commits yet
- worktrees Raticode's own tasks are using

Ignored files such as builds and a linked `node_modules` go with the worktree folder; a link's target is left alone. Each branch is checked again just before deletion. One that moved, gained files or is no longer contained is kept and reported.

## Live updates

The Worktrees list, branches, Changes and an open History view update on their own when:

- worktrees are added, removed or pruned, including by agents, swarms and `git worktree` in a terminal
- commits or staged changes land

Raticode watches only the repository's Git metadata, not your files. Edits to working files show up within a few seconds. **Refresh source control** reads everything again.

[Swarms](../advanced/swarms.md) use review and assignment worktrees to keep parallel agent work apart. A worktree is still part of the same repository and shares its Git metadata.
