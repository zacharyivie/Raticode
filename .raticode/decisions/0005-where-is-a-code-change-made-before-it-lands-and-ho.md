---
id: D-5
title: "Where is a code change made before it lands, and how does it land?"
status: accepted
date: 2026-11-21
decided_by: the user
supersedes: []
superseded_by: null
applies_to: ["AGENTS.md", "src/gofer/ui/rem_home_landing.py", "src/gofer/ui/ticket_patch.py"]
forbids: []
signals: []
---

## Context

The main checkout sits on the branch the user's app runs from. Parallel workers each need an isolated checkout.

## Decision

Each change gets its own git worktree under .worktrees/<slug>, branched from local dev. Commit there, rebase onto dev, then fast-forward dev so history stays linear. The main checkout stays on the user's running branch and is never edited or used for scratch files.

## Options we didn't choose

Editing in the main checkout: rejected, it is the running app. Merge-commit landing: rejected, dev stays linear.

## Consequences

Every change is made in its own worktree under .worktrees/<slug>, rebased onto dev and fast-forwarded in. The main checkout is never edited.

## How we check it

Reviewers fail work made in the main checkout, or landed on dev with a merge commit.
