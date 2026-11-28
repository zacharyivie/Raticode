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

## One view says it stopped working

From 0.6.2, if something breaks while a view draws itself, only that view is replaced. You'll see a message like **Posts stopped working** with the error in one line, and the activity rail, Rem and your other views keep running. This covers Rem, Organizations, Second Brain, Kanban, Posts, Workflow runs, Swarms, the editor, the browser, a workflow's graph and the threads pane.

- **Reload this view** draws the view again from scratch. Switching to another view and back does the same.
- **Copy details** copies the full error report: the stack, the view's name, the Raticode version and your system.
- **Report** opens a new GitHub issue with that report filled in.

The error is also written to the app log under **Settings > Developer**. If the view breaks again after a reload, report it. The whole-window **Something snapped.** page appears only for errors outside every view, and its **Reload Raticode** button reloads the whole window.

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

## An old TOML workflow is missing from Workflows

From 0.6.1, Raticode doesn't load workflows written as TOML files. Workflows shows a notice while `.toml` workflow files are left in the data folder. Choose **Convert to Rattish**, or run `Raticode rattish convert FILE.toml --project DIR`. See [Convert a TOML workflow](../workflows/rattish.md#convert-a-toml-workflow).

Their schedules and file watches don't run any more either. The converter prints the [trigger](../workflows/triggers.md) commands that bring them back.

## A trigger didn't fire

- Run `Raticode rattish triggers status`.
- Triggers fire only while Raticode or its background scheduler is running and the computer is awake.
- `Raticode rattish triggers history --problems` lists missed, failed, skipped and interrupted runs with the reason.
- **Workflow changed since you approved it** means the workflow, or a prompt file, script or child workflow it runs, changed after you approved it. Open **Review changes** in the workflow's Triggers section, or run `Raticode rattish triggers approve ID`. See [Approved versions](../workflows/triggers.md#approved-versions).

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

Choose **Help › Report a Problem** in the desktop app (from 0.6.2). It opens a new [GitHub](https://github.com/zacharyivie/Raticode/issues) issue with the Raticode version, your OS and architecture, and the Electron version filled in. **Help › About Raticode** shows the same details with a **Copy** button. On the phone, use **More › Help › Report a problem** (from Raticode Mobile 0.38.0). In **What happened**, add:

- the provider CLI's version, if a provider is involved
- steps to reproduce
- log lines from **Settings > Developer**, and a minimal failing Rattish file if you have one

Remove keys, tokens, private attachments and personal paths first. Keep your working state as it is; don't delete databases to make an error go away.

For help that doesn't belong in a public issue, email [support@raticode.dev](mailto:support@raticode.dev).
