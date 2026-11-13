---
title: Import and export
description: Share portable Raticode workflow bundles and review them before execution.
pageClass: docs-layout
---

# Import and export

A `.raticode` bundle moves a workflow and its packaged resources into another project or onto another computer.

## Export a workflow

Select a registered workflow, choose its export action, pick an output folder and note the bundle's path.

- The bundle holds the workflow source and the resources it can package.
- It isn't an export of your whole project, or a backup of Rem conversations.
- For normal Git review, keep `workflow.rattish` and its scripts in source control. Bundles are for handing a workflow to another installation.

## Import a bundle

Choose **Import** in Workflows, select a `.raticode` bundle and choose the destination project. Raticode checks the bundle, adds it and shows the workflow. The ID can differ from the sender's.

Before running it, open the source and graph and read:

- commands and agent prompts
- child workflow references
- file destinations
- network requests

Importing a workflow isn't permission to run it.

## Check the receiving computer

Compile and run preflight on the destination. It still needs its own interpreters, provider CLIs, API profiles and credentials.

- A bundle doesn't carry your provider login or turn secret references into plain text.
- If a path or outside resource can't be packaged, set it up on the receiving computer. Don't expect an absolute path from the sender's computer to exist there.

## What to share separately

| Item | How to share it |
| --- | --- |
| Workflow and packaged resources | `.raticode` workflow bundle |
| Project code | Your repository or another file transfer |
| Report design | `.raticode-theme.json` export |
| Conversation history | The conversation archive |
| Provider login | Sign in again on the destination |

See [Report themes](../knowledge/themes.md), [Conversation archive](../knowledge/archive.md), and [Validation and preflight](../workflows/validation.md).
