---
title: Troubleshooting
description: Resolve installation, provider, validation, memory, and device problems without losing project state.
pageClass: docs-layout
---

# Troubleshooting

Start with the failing layer: installation, provider, workflow validation, execution, or saved state. Keep the exact error and target path before changing settings.

## The desktop will not open

Check that the asset matches your operating system and architecture. The current macOS asset targets Apple Silicon; Windows and Linux assets target x64. Follow the [installation guide](../installation.md) for unsigned-build and Linux executable notes.

If building from source, use the repository's Node version through nvm and install both Python and frontend dependencies. Do not mix dependencies from a different checkout with the packaged release.

## A provider is missing or cannot authenticate

Run that provider's own CLI outside Raticode. Confirm login works and its executable is visible in the environment used to launch the desktop. Restart the app after changing PATH or installing a CLI.

Refresh the provider catalog and choose a supported model. A model's appearance in one provider does not imply it is available through another. API-backed providers need their configured credentials and network access. See [Provider setup](../setup.md).

## Rem cannot use a tool

Inspect the active thread's resources and permissions. Shell and web toggles, selected skills, and MCP registrations are independent. Check an MCP server's transport, executable or URL, arguments, and authentication.

A global thread needs a project selection before project file editing. Read-only sessions cannot mutate application state. Resource changes apply to the next turn; the preceding turn's tool list can describe an older configuration.

## A workflow will not validate

```bash
gof rattish check WORKFLOW_PATH --format json
gof rattish preflight WORKFLOW_PATH --format json
```

Read each diagnostic's node and field. Common causes include unknown node IDs, misspelled provider IDs, underscore configuration fields instead of hyphenated fields, invalid output selectors, incompatible input types, and missing executables or files.

Check validates source and contracts; preflight checks resource readiness. An upstream command that might create a file does not mean every node's preflight will accept a currently missing resource. Keep required fixtures available or redesign the step. See [Validation](../workflows/validation.md).

## Execution fails after validation

Inspect the failing activation in Runs and its output/error. A service can reject a request, a provider can fail, a file can change, or a command can return a nonzero exit code after preflight succeeds.

Review routes and `allow-fail` before adding retries. Retrying a write, network request, or remote job can repeat a side effect. Stop requests cancellation; it does not restore the project automatically.

## Scheduling or queueing rejects Rattish

The built-in scheduler, watcher, and runner queue still use legacy workflow-loading paths. Read their compatibility notes. For current Rattish source, an external trigger can invoke `gof rattish run`; validation remains a separate step.

## Knowledge is not found or a report did not save

Verify Second Brain is enabled and points to the intended folder. Supported text is indexed with file-size and scan limits; hidden folders, dependency trees, and symlinks are excluded. PDFs need their HTML companion for text search.

Inspect the save tool's returned result. A collision, missing destination, permission failure, or PDF-render failure is not a successful save. Archive write failures can block deletion of the working thread until the archive is restored or disabled.

## Devices or managed work are uncertain

Use matching device builds, check the experimental flag, compare paired identities, and request fresh status. A stale status is unknown. Do not replay a job merely because its reply was lost.

Swarms and organizations remain paused after restart. Inspect interrupted changes and receipts before resuming. Managed remote stops require confirmation, not just a cancellation request.

## Report an issue

Include the Raticode version, OS and architecture, provider/CLI version, reproduction steps, and redacted diagnostics. A minimal failing Rattish file is useful. Remove keys, tokens, private attachments, and personal paths before publishing.

Use the [GitHub issue tracker](https://github.com/zacharyivie/Raticode/issues). Preserve the original working state rather than deleting databases to make the error disappear.
