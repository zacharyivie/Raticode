# Rattish migration and compatibility

## TOML transition

**Accepted.** TOML will not remain a first-class authoring language. Existing workflows
need an export command that produces a Rattish project folder and `.rattish` source.

The exporter should:

1. Load and validate the existing TOML workflow using the legacy loader.
2. Convert its typed workflow model into a Rattish semantic AST.
3. Emit formatted Rattish source.
4. Move or copy referenced project-owned assets into the workflow folder when authorized.
5. Create `workflow.metadata.json` from existing canvas metadata.
6. Create `.raticodeignore` defaults.
7. Compile the emitted source and compare the resulting behavior with the legacy model.
8. Report fields or behavior that cannot be represented exactly.

The conversion must not silently discard legacy behavior. Unsupported constructs produce
blocking diagnostics or explicit migration annotations for user review.

## Legacy workflow composition

**Accepted.** Rattish has no `subflow` node. The exporter converts both legacy `workflow`
and `subflow` operations to the Rattish `workflow` node.

- Legacy `workflow_id` becomes `workflow-id`.
- A legacy component source becomes `workflow-path`.
- `parameter_bindings` and `input_bindings` merge into `with`.
- A name present in both binding maps is a blocking migration error.
- Component inputs and outputs become the referenced workflow's public interface.
- Per-call `output_contract` selectors move to public output declarations in the child
  workflow. The exporter reports incompatible call-site contracts instead of choosing one.

## Cutover policy

The implementation needs a release policy for:

- When new workflow creation switches to Rattish
- How long legacy TOML execution remains available for migration
- Whether Studio offers one-click conversion
- Whether schedules and watchers pause until conversion succeeds
- How backups and rollback work

These are product decisions and remain open.

## Source versioning

Rattish source declares its language version. The compiler should retain frontends for
supported historical language versions. Recompiling old source directly to current IR is
safer than repeatedly migrating cached IR.

An explicit source migration command can update language versions:

```bash
gof rattish migrate workflow.rattish --to 2
```

Source migration should:

- Preserve comments through the concrete tree
- Create a revision or backup
- Show a source diff
- Require explicit acceptance
- Compile and preflight the result
- Never rewrite source silently during application startup

## IR versioning

**Accepted.** IR and executor compatibility are versioned together. The executor rejects
unsupported IR versions.

**Proposed.** Normal installed workflows recompile `.rattish` source after an application
upgrade instead of migrating their cached IR. IR migrators remain necessary for:

- Active or suspended workflow runs
- Checkpoints and replay state
- Historical execution snapshots
- Portable compiled artifacts if they are introduced later
- Recovery when source is missing

Each IR migration is a pure step from one version to the next. Migration writes a new
artifact, validates it, and atomically swaps references only after success. Original data
remains available for rollback.

## Runtime-state migration

Long-running workflows make runtime state more sensitive than cached IR. Stored state may
contain:

- Active node executions
- Completion tokens
- Partial joins
- Activation lineage
- Retry state
- Run counters
- Approvals
- Agent memory references

Every persisted structure needs a version. Upgrade code must either migrate it safely or
mark the run as requiring recovery. It must not reinterpret an old partial join using new
execution semantics.

## Plugin compatibility

Plugin nodes are deferred, but the core design should reserve qualified operation types
and versioned node contracts:

```rattish
type: acme-release/publish
plugin-version: 2
```

Plugins should extend the semantic node catalog rather than the Rattish grammar. A future
contract may declare fields, defaults, inputs, outputs, effects, preflight checks, and the
runtime handler. The compiler must have a compatible contract to compile the node, and the
executor must have a compatible handler to run it.
