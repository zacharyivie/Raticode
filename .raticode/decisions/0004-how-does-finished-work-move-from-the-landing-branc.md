---
id: D-4
title: "How does finished work move from the landing branch to a release, and what may agents do with the shared branches?"
status: accepted
date: 2026-11-21
decided_by: the user
supersedes: []
superseded_by: null
applies_to: ["github/workflows/**", "docs/releasing.md", "mobile/android/docs/releasing.md", "scripts/release-candidate.py", "src/gofer/ui/rem_home_landing.py", "src/gofer/ui/ticket_patch.py"]
forbids: []
signals: []
---

## Context

Releases are built when stage is pushed, published by a tag after a squash merge into main, and the website deploys from main only. The user runs the desktop app from the test branch and moves test only when restarting. Self-work, reviews and organization employees run with git push blocked.

## Decision

Work lands on local dev only. A release goes dev to stage (the push builds the candidate), stage is squash merged into main, and a vX.Y.Z or mobile-vX.Y.Z tag publishes. The user's app runs from test. Agents never push, fetch-reset or force-move dev, test, stage or main.

## Options we didn't choose

Workers pushing dev: rejected, pushes are the user's call. Releasing from dev: rejected, candidates come from stage.

## Consequences

Agents finish on local dev and say what needs pushing. Release work (stage, main, tags, website deploy) is always the user's step.

## How we check it

Unattended runs get the push-blocked git environment. Reviewers fail any task result that pushed, fetched over, or moved test, stage or main.
