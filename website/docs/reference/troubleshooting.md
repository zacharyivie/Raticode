---
title: Troubleshooting
description: Resolve installation, provider, validation, memory, and device problems without losing project state.
pageClass: docs-layout
---

# Troubleshooting

Find the layer that's failing: installation, provider, workflow validation, a run, or saved state. Copy the exact error and the file path before you change anything.

## The desktop app won't open

- Check the download matches your system. The macOS build is for Apple Silicon; Windows and Linux builds are x64. See [Installation](../installation.md) for unsigned-build prompts and making the AppImage executable.
- Building from source: select the repository's Node version with nvm and install both the Python and frontend dependencies. Don't mix dependencies from another checkout with a packaged release.

## A provider is missing or can't sign in

1. Run the provider's own CLI outside Raticode and check its login works.
2. Make sure its executable is on the PATH that Raticode starts with. Restart Raticode after changing PATH or installing a CLI.
3. Use **Refresh providers** and pick a supported model. A model listed for one provider isn't necessarily available through another.

API-backed providers need their credentials and network access. See [Provider setup](../setup.md).

## Rem can't use a tool

- Check the thread's own tools and permissions. Shell, web, skills and each MCP server are switched on separately.
- For an MCP server, check its transport, command or URL, arguments and sign-in.
- A Global thread needs a project before it can edit project files.
- Read-only sessions can't change app state.
- Tool changes apply from the next turn. An earlier turn's tool list can show the old setup.

## A workflow won't validate

```bash
Raticode rattish check WORKFLOW_PATH --format json
Raticode rattish preflight WORKFLOW_PATH --format json
```

Read each diagnostic's node and field. Common causes:

- an unknown node ID or a misspelled provider ID
- underscores in field names that use hyphens
- an invalid output selector or mismatched input types
- a missing executable or file

`check` validates the source and contracts; `preflight` checks that resources exist. A file an earlier step would create still counts as missing at preflight, so keep needed files in place or change the step. See [Validation](../workflows/validation.md).

## A run fails after validation

Open the failing step in **Runs** and read its output and error. Services, providers, files and commands can all fail after preflight passed.

Look at routes and `allow-fail` before adding retries. Retrying a write, a network request or a remote job can repeat its side effect. **Stop** cancels the run but doesn't restore your project.

## Schedules or the runner queue reject a Rattish workflow

`Raticode schedule`, `Raticode watch` and the runner queue still use the older workflow format. For a `workflow.rattish`, use [triggers](../workflows/triggers.md), or call `Raticode rattish run` from another tool.

## A trigger didn't fire

- Run `Raticode rattish triggers status`.
- Triggers fire only while Raticode or its background scheduler is running and the computer is awake.
- `Raticode rattish triggers history --problems` lists missed, failed, skipped and interrupted runs with the reason.

## Knowledge isn't found, or a report didn't save

- Check Second Brain is on and points at the right folder.
- Supported text files are indexed within size and scan limits. Hidden folders, dependency folders and symlinks are skipped. PDFs need their HTML companion for text search.
- Read what the save tool returned. A name collision, missing folder, permission error or failed PDF render means nothing was saved.
- If the archive can't be written, deleting the working thread is blocked until you fix or turn off the archive.

## A phone or paired computer seems stuck

- **Phone won't pair or connect:** see [Pair and connect: troubleshooting](../mobile/pairing.md#troubleshooting). Check **Phone and desktop pairing**, the computer's firewall, the same (non-guest) Wi-Fi, **Set desktop address**, and the phone's 64 MB history share.
- **Status looks wrong:** use matching versions on both devices, check the Feature Preview switch, compare the paired identities and ask for fresh status. Old status means unknown, not up or down.
- **A reply was lost:** don't send the job again just because the reply didn't arrive.
- **After a restart:** swarms and organizations stay paused. Check interrupted changes and receipts before resuming. Stopping managed work on another computer needs confirmation, not just a cancel request.

## Report an issue

Open an issue on [GitHub](https://github.com/zacharyivie/Raticode/issues) with:

- the Raticode version, your OS and architecture, and the provider CLI's version
- steps to reproduce
- diagnostics from **Settings > Developer**, and a minimal failing Rattish file if you have one

Remove keys, tokens, private attachments and personal paths first. Keep your working state as it is; don't delete databases to make an error go away.
