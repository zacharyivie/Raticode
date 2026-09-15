# Rattish implementation plan

This plan is intentionally staged. Each phase should produce artifacts that can be
reviewed before later layers depend on them.

## Phase 0: approve the language contract

Status: in progress. The first lexical specification, EBNF, and built-in node inventory
are available in [`../spec/`](../spec/). The initial AST, node-contract, diagnostic, and
IR schemas are available in [`../schemas/`](../schemas/), with the first executable
conformance slice in [`../conformance/`](../conformance/).

Deliverables:

- Resolve [07-open-questions.md](07-open-questions.md)
- Normative lexical specification
- Normative EBNF
- Static semantics document
- Execution semantics document
- Initial built-in node catalog
- Initial JSON IR schema
- Lowering rules for every source construct
- Valid and invalid conformance fixtures

Exit criteria:

- Each accepted example has one expected canonical AST and IR.
- Each invalid example has expected diagnostic codes and source spans.
- No executor behavior required by Rattish remains defined only in implementation code.

## Phase 1: compiler foundation

Status: complete for the accepted Rattish 1 syntax. The lexer token stream and source AST
retain source spelling, spans, and comment trivia used by parser recovery and formatting.

Deliverables:

- Token and source-span model
- Strict lexer
- Strict parser
- Concrete syntax tree
- Semantic AST
- Canonical formatter
- Machine-readable diagnostic model
- `gof rattish check`
- `gof rattish format`

Verification:

- Grammar conformance tests
- Lexer and parser error-location tests
- Comment-preservation tests
- Case-insensitive identifier tests
- Formatter idempotence tests

## Phase 2: semantic analysis and node contracts

Status: complete for the approved built-in catalog. Common semantic analysis, workflow
public inputs and outputs, and 22 machine contracts exist. Every contract compiles and has
a runtime handler. `workflow` calls compile referenced interfaces, validate bindings,
preflight recursively, and execute child workflows. The resource slice includes `file`,
`folder`, `open-resource`, and
`prompt-file`. The HTTP slice includes compilation, bindings, network preflight, retries,
execution, structured outputs, and conformance fixtures. Rattish uses immutable node
outputs and `with` bindings instead of a `set-variable` node. The notification slice covers
desktop, webhook, and email configuration, preflight, delivery, and safe public output.
Approval gates, common LLM tasks, local retrieval, loop activation lineages, and scoped
Break behavior are implemented with frozen conformance fixtures.

Deliverables:

- Symbol tables and name resolution
- Built-in node contract registry
- Built-in success-output contracts and Rattish Schema Profile 1
- Default expansion
- Type and reference analysis
- Case-sensitive JSON member selector analysis
- Referenced workflow public-interface analysis
- Route and readiness analysis
- Cycle and limit analysis
- Effect declarations
- External schema loading and validation

Verification:

- Forward-reference and cycle tests
- Asymmetric route and readiness tests
- Structured-output predicate tests
- Allowed-failure output-availability tests
- Schema subtype and binding-default tests
- JSON member selector case tests
- Legacy subflow-to-workflow conversion fixtures
- Every built-in node contract has valid and invalid fixtures

## Phase 3: JSON IR

Status: complete for implemented contracts. Version 1 lowering, invariant validation,
canonical serialization, dependency fingerprints, atomic caching, `compile`, and
`inspect-ir` are implemented.

Deliverables:

- Version 1 JSON IR schema
- AST-to-IR lowering
- Semantic IR invariant validator
- Canonical JSON serialization
- Source and dependency fingerprinting
- Atomic IR publication
- Registry mapping from source to IR
- `gof rattish compile`
- `gof rattish inspect-ir`

Verification:

- Compiler emits no IR on errors
- Every emitted IR passes schema and invariant validation
- Equivalent accepted source forms produce canonical equivalent IR
- Changed compiler dependencies invalidate the cache
- Failed publication preserves the last valid artifact

## Phase 4: executor transition

Status: in progress. The completion-token scheduler, joins, cycles, failures, retries,
timeouts, limits, nested workflow execution, loop fan-out, scoped Break, and run artifacts
are implemented. Durable
in-progress state, background execution, cancellation recovery, and schedule continuity
remain.

Deliverables:

- IR loader and version gate
- Completion-token execution model
- Activation lineage
- Activation-group coalescing
- Per-node FIFO activation queues and concurrency limits
- Concurrent `to` fan-out
- `needs` readiness and join behavior
- Locked-node signal accumulation and coalescing
- Atomic completion, routing, readiness, and scheduling order
- Allowed failure propagation
- PASS, FAIL, and inferred terminal behavior
- Run limits and timeout behavior
- Unresolved-join detection
- Persistent execution-state versions
- Background runtime service independent of Studio lifetime
- Missed-schedule skipping

Verification:

- Event-ordering tests for timing-sensitive activations
- Cycle tests with structured-output routing
- Cross-iteration join-isolation tests
- Same-group fan-out coalescing tests
- Locked cross-group coalescing tests
- Before-unlock and after-unlock arrival tests
- Cross-group FIFO queue and explicit concurrency tests
- Fatal and tolerated failure tests
- Long-running node and timeout tests
- Quiescence and unresolved-join tests
- Cancellation and recovery tests
- Studio-close continuity and missed-schedule tests

## Phase 5: preflight and runtime resources

Status: implemented for the current built-in contracts. Provider, prompt, script,
interpreter, filesystem, handler, empty workflow, recursively referenced workflow, HTTP
target, notification-channel, approval store, local retrieval, loop-source, and secret
reference checks exist. Plugin-defined checks remain future work.

Deliverables:

- Environment-independent compiler validation
- Deployment preflight service
- Provider and model readiness checks
- Prompt and script path checks
- Secret readiness
- Filesystem and network policy checks
- Machine-readable readiness diagnostics

Verification:

- A workflow compiles without its provider installed.
- The same workflow reports not ready on that machine.
- Blank prompts warn but do not fail compilation or preflight.
- Missing required runtime assets fail preflight with source-linked diagnostics.

## Phase 6: workspace and bundle format

Status: in progress. Workflow folders and cross-project registration exist. Ignore-file
parsing, manifests, export, import, and archive defenses remain.

Deliverables:

- Workflow-folder creation
- Versioned metadata file
- `.raticodeignore` parser
- Versioned `.raticode` manifest
- Safe exporter and importer
- Archive size and traversal defenses
- Import recompilation and ID allocation

Verification:

- Secrets and hard-excluded paths do not enter bundles.
- Symlinks cannot escape the project root.
- Import ignores or rejects compiled artifacts.
- Import allocates stable instance identity and recompiles source.
- Export and import preserve execution behavior and editor metadata.

## Phase 7: Studio editor

Status: backend foundation implemented. Registered source can be opened, analyzed with
parser recovery and partial graph projection, and saved with revision-conflict protection.
Versioned editor metadata has independent validation and revisioned saves. The Studio text
editor, graph mutation layer, overlays, and diff-review interface remain.

Deliverables:

- Rattish text editor
- Recovery parser integration
- Partial graph projection
- Error overlays and broken-edge display
- Source-range graph mutations
- Comment-preserving edits
- Agent-proposed diff review
- Dirty, compiled, stale, ready, and running status display

Verification:

- Incomplete typing does not destroy the last valid AST or IR.
- Valid declarations remain visible beside broken declarations.
- Graph edits produce minimal source changes.
- External file edits cannot be overwritten without conflict handling.
- Agent changes remain pending until accepted under the chosen policy.

## Phase 8: TOML migration and cutover

Status: not started.

Deliverables:

- Legacy TOML-to-Rattish exporter
- Behavioral comparison report
- Studio conversion flow
- Schedule, watcher, and runtime-state transition policy
- Backup and rollback support
- Documentation and workflow-builder skill generated from approved contracts

Verification:

- Representative legacy workflows convert and compile.
- Unsupported legacy behavior blocks conversion with precise diagnostics.
- Converted workflows preserve schedules, paths, prompts, and graph behavior.
- The workflow-builder skill uses Rattish commands and validates every authored change.

## Documentation generation

The final authoring skill should derive field names, defaults, node contracts, examples,
and compiler commands from versioned compiler metadata where practical. Handwritten
guidance should focus on workflow design and correction procedures. Generated material
reduces drift between compiler behavior and agent instructions.
