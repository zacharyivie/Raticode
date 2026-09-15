# Raticode naming

Use these spellings when adding documentation, UI, integrations, or release files.

| Use | Spelling |
| --- | --- |
| Product name, window titles, prose | Raticode |
| JavaScript components and PascalCase identifiers | Raticode |
| JavaScript camelCase identifiers | raticode |
| Python identifiers and Rattish runtime handlers | raticode |
| Environment variables and constants | RATICODE |
| Paths, URI schemes, storage keys, temporary files | raticode |
| Desktop release artifact prefix | Raticode |
| macOS application bundle | Raticode.app |

New Rattish workflows live at `.raticode/<workflow-id>/workflow.rattish`. Their
export rules are in `.raticodeignore`. Portable workflows use `.raticode`
archives with a `raticode.bundle.json` manifest and `raticode-workflow`
format identifier. Rattish schema IDs use `urn:raticode:rattish:schema:`;
these identify bundled schemas and do not require a website or network request.

## Existing installations

Previous brand spellings are confined to two compatibility resources:
`src/gofer/utils/brand_compat.py` and `frontend/electron/brand-compat.json`.
They let existing workflow folders and bundles remain readable. Exporting an
older workflow writes the current manifest and ignore-file names. Importing an
older bundle installs it under the current workspace directory. Its ignore rules
still apply, including rules excluding private configuration.

If a workspace directory has already been renamed on disk, registry reads recover
the new paths while preserving workflow IDs and creation times. Existing workspace
folders are not moved automatically. Reopen the project to discover current and previous layouts.

Preferences, text zoom, and the selected project/view load from previous storage
keys when the current keys do not exist. Current preferences take precedence.
An existing packaged desktop profile remains in use when no current profile exists;
an explicit `--user-data-dir` takes precedence. Embedded browser tabs use a new
partition, so websites may require signing in again.

The Python import package `gofer`, the `gof` executable, `GOFER_*` configuration,
`gofer-flow` distribution/package identifiers, and `com.goferflow.desktop` app ID
remain stable. This keeps installations, CLI callers, secrets, scheduler data,
and OS application identity compatible. These are technical identifiers rather
than product display names.

Repository links use the existing `zacharyivie/gofer-flow` GitHub redirect, also
used by this checkout's origin. Renaming the hosted repository is separate from
editing this checkout. Release preparation uses the actual repository identity
provided by GitHub Actions. A release must be built again after this rename;
previous binaries and draft candidates are not Raticode builds.

## Verify naming

Run `python scripts/check-branding.py`. It checks tracked files and visible
untracked files, including hidden project files, for the previous spelling outside
the two compatibility resources. It also verifies desktop display names, release
artifact prefixes, schema IDs, and the Arch desktop/icon checksums.

Ignored historical reports, downloaded packages, build outputs, dependency trees,
Git history, installed applications, and notes outside this repository are not
editable release sources. Rebuild packages and refresh the Arch binary checksums
before publishing. Historical release evidence should retain its original names.
