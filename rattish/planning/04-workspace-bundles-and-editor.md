# Rattish workspace, bundles, and editor

## Workflow folder

**Accepted.** Creating a workflow in a selected repository creates a workflow folder under
that repository's `.raticode` directory. The folder name is the allocated static workflow
ID. The selected outer project folder is the root for relative execution paths; the
workflow folder is the portable source and asset container. Multiple workflows in one
repository are siblings and the UI groups them by repository.

Proposed initial layout:

```text
project-folder/
`-- .raticode/
    `-- review-pr/
        |-- workflow.rattish
        |-- workflow.metadata.json
        |-- .raticodeignore
        |-- prompts/
        |   |-- implement.md
        |   `-- review.md
        |-- schemas/
        |   `-- review-result.json
        `-- scripts/
            `-- collect-context.py
```

Documentation should encourage authors to keep workflow-owned prompts, schemas, scripts,
and supporting assets in this folder.

Every created workflow is also recorded in a versioned application registry. Registry
entries carry the static workflow ID, display name, outer project root, workflow root,
entry point, and creation time. This registry is discovery metadata, not workflow source;
`workflow.rattish` remains the authored source of truth. The UI uses it to find workflows even
when a different project folder is currently open and groups entries by outer project root.

The new-workflow dialog asks for the owning project folder and uses the native directory
picker in the desktop app. Project selection belongs to creation rather than a global
workspace switcher, so the workflow rail has no persistent folder control at its bottom.
Each project group owns its path actions. Its context menu can copy the project path or open
the folder in the operating system's file explorer, with the same menu available from a
keyboard-accessible action button on the folder row.

## Source and metadata separation

**Accepted.** `workflow.rattish` contains execution behavior. Non-execution UI state belongs in
`workflow.metadata.json`.

Metadata may include:

- Canvas node positions
- Group positions and styling
- Zoom and pan
- Folded source declarations
- Editor layout preferences

Metadata must not affect compilation, preflight, or execution. Missing or corrupt metadata
does not invalidate the workflow. The UI may recreate it.

Metadata has its own version and migration path. A possible initial shape is:

```json
{
  "metadataVersion": 1,
  "canvas": {
    "nodes": {
      "implement": {"x": 120, "y": 80},
      "review": {"x": 480, "y": 80}
    },
    "zoom": 1.0,
    "pan": {"x": 0, "y": 0}
  },
  "editor": {
    "foldedDeclarations": []
  }
}
```

The compiler never reads this file.

## Bundle format

**Accepted.** Import and export use a ZIP-like `.raticode` archive. A bundle contains the
Rattish source, metadata, and permitted project assets. Import extracts the bundle into a
new allocated workflow folder and recompiles its source.

Import must never trust bundled compiled IR. The destination compiler creates new IR using
the destination workflow ID, project root, compiler version, and installed contracts.

The archive format needs its own versioned manifest. The manifest should list file paths,
sizes, content hashes, bundle version, Rattish language version, and entry-point source.

## `.raticodeignore`

**Accepted.** Each workflow folder contains `.raticodeignore` with reasonable defaults
that exclude secrets and runtime configuration.

**Proposed syntax.** Use gitignore-compatible path patterns for familiarity. Maintain a
hard exclusion list that user negation rules cannot reinclude.

Suggested defaults:

```gitignore
.env
.env.*
*.pem
*.key
*.p12
*.pfx
credentials.json
secrets.json

.git/
.venv/
venv/
node_modules/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/

*.log
logs/
checkpoints/
compiled/
```

The exporter should reject paths that escape the project through symlinks, prevent archive
path traversal, apply file and total-size limits, and show an export manifest. Filename and
content checks should warn about likely credentials, but such checks cannot prove that a
bundle contains no secrets.

Plaintext credentials are legal Rattish source, just as they are legal text in a Dockerfile
or `.env` file. The editor shows a warning squiggle for suspected plaintext credentials,
and export repeats the warning before creating a bundle. Neither check rejects valid
Rattish. Export never substitutes resolved profile values or secret-reference values into
the source or bundle.

## Integrated text and graph editor

**Accepted.** Gofer Studio will provide graph and text views of the same Rattish source.
Agent edits open the text editor and display a diff for user review.

Text editing frequently produces temporarily invalid syntax. The editor therefore needs
separate representations:

```text
live text buffer
  -> recoverable concrete syntax tree
  -> partial semantic projection
  -> graph with valid and broken elements

strict valid source tree
  -> semantic AST
  -> compiler
  -> immutable IR
```

The recoverable tree retains comments, whitespace, quoting, source spans, and explicit
error nodes. The semantic AST contains only language meaning. Comments do not belong only
to the semantic AST because that would lose placement and formatting.

## Debounced updates

After text changes:

1. Update the live source buffer.
2. Reparse after a short debounce.
3. Project valid declarations into the graph.
4. Gray out broken nodes and fields.
5. Display unresolved routes as broken edges.
6. Run strict compilation only when the strict parse has no errors.
7. Publish new IR only after all compiler checks pass.

The parser may reparse the full file initially if performance is sufficient. The source
model should not require an incremental parser in the first implementation, but its source
spans and recovery behavior should leave room for one.

## Graph edits

Graph edits should patch precise source ranges in the concrete tree and then reparse. They
should not serialize the entire semantic AST after every change.

Examples:

- Changing a field replaces that field's value.
- Creating an edge updates `to`. The editor changes `needs` only when the user makes that
  edge a readiness requirement.
- Renaming a node updates references and its metadata key transactionally.
- Adding a node inserts a formatted declaration at a stable source boundary.
- Deleting a declaration preserves unrelated standalone comments.

The editor needs an optimistic concurrency check. If the on-disk source changes after the
UI loads it, the UI must not overwrite the newer revision without resolving the conflict.

## Agent changes

**Proposed.** Workflow-assistant changes use this flow:

1. Agent proposes a source patch.
2. Studio opens the text view with a diff.
3. The user accepts or rejects the patch.
4. Accepted text updates the live buffer.
5. Recovery parsing updates the graph.
6. Strict compilation runs.
7. Deployment preflight runs after compilation.
8. The assistant reports diagnostics and readiness.

Whether users may configure automatic acceptance is a separate product and safety decision.

## Comment preservation

**Proposed attachment rules:**

- A comment immediately before a declaration belongs to that declaration.
- An end-of-line comment belongs to that field.
- A comment separated by a blank line is standalone.
- Removing a declaration removes attached comments only after the editor shows them in the
  proposed change.
- Formatting preserves comment text and attachment.

These rules need an editor prototype before they become normative.

## Background runtime and missed schedules

**Accepted product constraint.** Closing Gofer Studio must not stop active workflows. The
executor and scheduler need a background service whose lifetime is independent of the UI.
The UI connects to that service to inspect, start, stop, and edit workflows.

**Accepted.** Missed scheduled occurrences are skipped. If a daily workflow misses 30
occurrences while the computer is off, restarting Gofer launches none of those missed runs
and waits for the next scheduled time.

A future language version may add an explicit catch-up or retry policy. Rattish 1 has no
automatic missed-run replay.
