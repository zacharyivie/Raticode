---
title: Import and export
description: Share portable Raticode workflow bundles and review them before execution.
pageClass: docs-layout
---

# Import and export

Use a `.raticode` bundle to move a workflow and its packaged resources into another project.

## Export a workflow

Select a registered workflow and choose its export action. Pick an output directory and inspect the resulting bundle path. The export packages source and eligible workflow resources; it is not an export of your entire project or a backup of Rem conversations.

Keep the original `workflow.rattish` and its referenced scripts in source control when you want ordinary Git review. Bundles are useful for portable handoff to another installation.

## Import a bundle

Choose Import in Workflows, select a `.raticode` bundle, and choose its destination project. Raticode previews the workflow identity and destination before creating the imported workflow. Inspect that preview rather than assuming the bundle uses the same ID on every machine.

After importing, open the source and graph. Read commands, agent prompts, child workflow references, file destinations, and network requests. Importing is not permission to run them.

## Recheck the receiving machine

Compile and run preflight in the destination environment. That machine still needs the required interpreters, provider CLIs, API profiles, and credentials. An export does not transfer your provider login or resolve secret references into plaintext.

If a path or external resource cannot be included, arrange it explicitly on the receiving machine. Avoid expecting an arbitrary absolute path from the sender's computer to exist on yours.

## What to share separately

| Item | Sharing method |
| --- | --- |
| Workflow and packaged resources | `.raticode` workflow bundle |
| Project code | Your repository or chosen file-transfer method |
| Report design instructions | `.raticode-theme.json` export |
| Conversation history | Configured conversation archive |
| Provider login | Authenticate independently on the destination |

See [Report themes](../knowledge/themes.md), [Conversation archive](../knowledge/archive.md), and [Validation and preflight](../workflows/validation.md).

