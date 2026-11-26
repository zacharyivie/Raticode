# Rattish 1 specification

This directory contains the normative source-language definition for Rattish 1. Planning
documents explain design history and proposals. When the two disagree, an accepted rule
in this directory controls the compiler.

The specification is still a review draft. Files mark unresolved contract decisions where
the current runtime cannot be translated mechanically.

## Documents

- [lexical-spec.md](lexical-spec.md) defines source encoding, layout, comments, tokens,
  strings, identifiers, numbers, durations, and embedded JSON.
- [grammar.ebnf](grammar.ebnf) defines document, declaration, value, route, binding, and
  predicate syntax.
- [static-semantics.md](static-semantics.md) defines symbol resolution, types, contracts,
  defaults, references, bindings, routes, paths, and compile-time diagnostics.
- [node-contracts.md](node-contracts.md) records the Rattish names for every current
  executable node type and maps their fields into the new language.
- [output-contracts.md](output-contracts.md) defines node completion fields, the Rattish
  JSON Schema profile, binding compatibility, and public workflow interfaces.
- [lowering.md](lowering.md) defines the exact transformation from a valid semantic AST
  to version 1 JSON IR.
- [diagnostics.md](diagnostics.md) defines stable diagnostic codes and their machine
  representation.
- [editor-protocol.md](editor-protocol.md) defines recovering document analysis, partial
  graph projection, metadata, and revision-checked Studio saves.

The first machine-readable artifacts now live beside the specification:

- [`../schemas/ast.schema.json`](../schemas/ast.schema.json) defines the source-faithful
  strict-parser AST.
- [`../schemas/node-contract.schema.json`](../schemas/node-contract.schema.json) defines
  the common contract format for built-in and future plugin nodes.
- [`../schemas/ir.schema.json`](../schemas/ir.schema.json) defines immutable executor input.
- [`../schemas/diagnostic.schema.json`](../schemas/diagnostic.schema.json) defines compiler,
  preflight, runtime, and export diagnostics.
- [`../schemas/run.schema.json`](../schemas/run.schema.json) defines persisted execution
  results, node-completion events and the run's model usage per node.
- [`../schemas/workspace-registry.schema.json`](../schemas/workspace-registry.schema.json)
  defines persistent cross-project workflow discovery records.
- [`../schemas/workflow-metadata.schema.json`](../schemas/workflow-metadata.schema.json)
  defines versioned, non-execution Studio layout and editor state.
- [`../contracts/`](../contracts/) contains the first representative built-in contracts.

## Current command-line slice

```bash
Raticode rattish create "Review PR" --project ./project-folder
Raticode rattish list
Raticode rattish check ./workflow.rattish
Raticode rattish format ./workflow.rattish
Raticode rattish compile ./workflow.rattish
Raticode rattish inspect-ir ./workflow.rattish
Raticode rattish preflight ./workflow.rattish
Raticode rattish run ./workflow.rattish --input count=3
```

`create` allocates a globally unique static workflow ID, creates
`PROJECT/.raticode/ID/workflow.rattish` with metadata and ignore defaults, compiles it, and
adds it to the application workflow registry. `list` reads that registry and groups
workflows by their outer project folder. Both accept `--format json`.

Both commands accept `--format json`. Exit status `0` means no error diagnostics. A
language or deployment-preflight error returns `1`. Source, artifact, or command-usage
failures return `2`. Warnings do not change a successful exit status.

`format` canonicalizes a grammatically valid source file and preserves comments. Use
`format --check` in CI or `format --stdout` to avoid changing the file. `compile` validates
and publishes IR only to Raticode's internal cache. `inspect-ir` prints that validated IR
for debugging and never creates a source-side IR file.

`check` compiles and caches valid IR internally. `preflight` uses that artifact but checks
the current machine on every invocation. `run` performs both steps and executes the IR.
Registered workflows store compiled artifacts and versioned run logs in their
`.raticode/<workflow-id>/` folder. Standalone `.rattish` files without a registered workflow
folder use the data folder. None of these commands writes IR beside the
source file.

`run --input NAME=JSON` accepts one declared workflow input per option. A file input takes a
JSON string path, relative to the current folder; `run` checks that the file exists and fits
the input before it starts, and exits `2` when it doesn't. The run artifact
records input names, not supplied values. Exit status `0` means the workflow passed. A
preflight or workflow failure returns `1`. Invalid CLI input or invalid declared workflow
input returns `2`.

### Resuming a run

`Raticode workflow resume ID --run-id RUN`, the desktop's **Resume** and a paired phone's
**Resume** resume a `failed`, `stopped` or `interrupted` run as a new run. The earlier run's
artifact is never changed. The new artifact names it in `resumed_from.run_id`.

The new run replays the earlier run's saved node records in their saved order. The k-th start
of a node in one activation group and lineage takes that key's k-th record, so routes, joins,
loops and cycles rebuild the scheduler state the earlier run had. Records with outcome
`success` or `allowed_failure` are reused without calling the node again; they keep their
original times and carry `reused_from_run_id`, and their `node_completed` event has
`reused: true`. Everything else runs for real: the failed activation, activations a stop or
crash cut off (`resumed_from.rerun_nodes`, which may have partly run), and the work they lead
to. Live work is held until nothing more can be replayed. After that a record is still reused
for a matching start, but never in a lineage where live work has completed, since that work's
results may differ. `resumed_from.reused_runs` and `not_reused_runs` count the outcome. Budgets
count reused records like any other run; the workflow timeout starts again. Usage limits
(`max-tokens`, `max-cost-usd`) count only the calls the resumed run makes itself, and a run that
ended at one of them can't be resumed.

A run is refused, with a sentence that says why, when:

- it passed, is still running, or never started its nodes (`preflight_failed`,
  `invalid_inputs`);
- its error comes from the workflow itself (`workflow_control`, `budget` or
  `output_validation` errors, or `RATTISH_RUN_RECORD_INVALID`), so a replay would end the same
  way;
- it had inputs or trigger events and their values weren't saved: it ran before Raticode
  saved them (no `saved_values`), or they were too large (`saved_values.saved` is `false`);
- it has no `compiled_snapshot`, the source or compilation fingerprint changed since, or the
  source no longer compiles;
- an agent with `memory: run` has a reused record, because that memory isn't saved;
- another run already resumed it (resume that run instead), or a resume of it is in progress.
  A resume whose steps never started (`preflight_failed` or `invalid_inputs`) redid nothing and
  doesn't count; it keeps `resumed_from` as history.

### Saved inputs and trigger events

A run that takes inputs or trigger events saves them beside its artifact as
`<run_id>.inputs`, created owner-only (0600). The artifact's `saved_values` names the saved
inputs, the `withheld_inputs` and the number of `trigger_events`. An input whose schema
contains `format: password` is never saved and is listed as withheld; workflow secrets are read
from the secret store at run time and are never inputs. A file input saves the path of the
run's own copy in `<run_id>.files`; Resume, Run again and run-from link or copy that copy into
the new run's folder and refuse, naming the input, when it is gone. Up to 256 trigger events
are saved, and a webhook body up to 1 MiB. A run whose values don't fit saves none of them: `saved` is `false`
with a `reason`. Runs with `invalid_inputs` keep nothing. Deleting a run deletes its
`.inputs` file and its `.files` folder.

Resume uses the saved values. Whoever resumes gives each withheld input again; nothing else may
be supplied. **Run again** (`rattish runs rerun RUN_ID`) starts a new run of the current source
with the same saved values and names the earlier run in `rerun_of`. It works for a run with any
status except `invalid_inputs`.

Resume runs preflight and honours the active-run limit, approvals and `expected_revision` like
a new run. The legacy `--from-node`, `--only-node`, `--skip-cache` and `--force` options are
refused for Rattish runs.

### Run from a node

`rattish run PATH --from NODE [--reuse RUN_ID]` (the Studio's **Run from here**, and
`rattish start WORKFLOW_ID --from NODE` through a backend) starts a new run of the current
source that reuses an earlier run's results for the nodes upstream of `NODE`:

- Upstream means every node `NODE` depends on, directly or through others: a `needs` entry, an
  incoming route, or a binding or route condition that reads that node. Upstream nodes that
  `NODE` also leads back to, through a cycle, are not reused.
- Each node's fingerprint covers its compiled definition without its `source_span`, the
  definitions of every node it depends on, and the workflow inputs any of them read. The
  earlier run's fingerprints come from its `compiled_snapshot`. Files read at run time, such
  as a `prompt-path` file, are not covered.
- Without `--reuse`, the newest run that reached `NODE` (it has a record or a `node_started`
  event for it), isn't running, has a `compiled_snapshot` and usable saved values, and in
  which every reused node's fingerprint is unchanged is chosen. With `--reuse`, only that run
  is considered.
- An approval gate (`runtime_handler` `raticode.approval_gate`) is never reused, and neither
  is any node that depends on one. The gate runs live with the same message and asks
  again, and the run continues from the new answer. The preview's `askAgainNodes` lists every
  gate the reused run reached, since each asks again when the run gets to it.
- When a reused node changed, the run is refused and the message names the node. A `NODE`
  inside a loop body (any activation context below a loop) is refused, as is a reused agent
  with `memory: run`.
- The reused nodes' records are replayed through the resume replay path in their saved order.
  `NODE`, every node after it and every other node run live. Reused records carry
  `reused_from_run_id`, make no provider call, and add no usage to the run or its limits.
- The run uses the reused run's saved inputs and trigger events. Its withheld inputs are given
  again (`--input` on the CLI); nothing else may be supplied.
- The artifact records `replayed_from` with `run_id`, `node_id`, `reused_nodes`,
  `reused_runs` and `not_reused_runs`. It doesn't count as resuming the earlier run.

`Raticode rattish test PATH --model NODE=PROVIDER:MODEL` (repeatable) runs each test case
with another provider and model on `NODE`. The override sets `provider:` and `model:` on a
copy of the source held in memory, with the graph editor's `set_field` edits, and compiles
that copy at the same path; the file, its triggers and their approvals are not changed. A
copy that doesn't compile refuses the run. The run artifact records `model_overrides`, each
with `node`, `provider`, `model`, `fromProvider` and `fromModel`, and its
`compiled_snapshot` and fingerprints are the copy's. Such a run can't resume, rerun, start
from a node or run from a trigger. Test results record the tried models as `variant` (a
sorted `key` and the `models`); estimates and the revision comparison only use results with
the same `variant`. Judge checks keep their own provider and model.

JSON output is one object with these required fields:

| Field | Value |
|---|---|
| `command` | `check` or `preflight` |
| `source` | Resolved source path |
| `ok` | `true` when no error diagnostic exists |
| `cache` | `null` before successful compilation, otherwise `hit` and `compilation_fingerprint` |
| `diagnostics` | Complete diagnostic objects conforming to `diagnostic.schema.json` |

Initial valid, invalid, and warning fixtures live in
[`../conformance/`](../conformance/). They fix expected AST, IR, diagnostic codes, and
source spans before parser implementation begins.

## Authority boundaries

The grammar accepts the common shape of a node declaration. A versioned node contract
decides which operation fields are legal, required, optional, or mutually exclusive for a
given `type`. This split lets a future plugin add a node contract without changing the
Rattish parser.

The compiler is the authority for acceptance. The editor, formatter, skill, and executor
must not maintain independent interpretations of the language.

## Migration from Radish

The language is now Rattish. New sources use `.rattish`, the version directive
`Rattish: 1`, and the `Raticode rattish` command group. The language version remains 1.
The parser also accepts the legacy `Radish: 1` directive, case-insensitively.
Formatting emits `rattish: 1`.

The app automatically renames `.rad` files to `.rattish` during workflow discovery,
file browsing, source opening, and bundle import. Compilation also migrates legacy
source paths, including child workflow references. Migration preserves file bytes
and permissions. An old `.rad` reference resolves to its renamed `.rattish` file.
Symbolic links are not renamed. If both filenames exist, migration reports a
conflict and leaves both files intact. OS errors are reported to the caller.

Workflow IDs, creation times, metadata, and run history survive the rename.
Existing app-data storage under `radish/` remains readable at its original location;
this directory name is retained for persistence compatibility. Source modules,
compiler identifiers, schemas, diagnostic codes, editor language IDs, and API routes
use `rattish` or `RATTISH`. Rebuild the backend and frontend together when upgrading.
