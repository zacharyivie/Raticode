# Rattish 1 lowering to IR version 1

## Authority

This document defines how a valid Rattish 1 semantic AST becomes IR version 1. The schema
for the result is [`../schemas/ir.schema.json`](../schemas/ir.schema.json).

Lowering never repairs source. The compiler emits no IR when lexing, parsing, name
resolution, contract validation, type analysis, or control-flow analysis reports an
error. Warnings do not block lowering and do not become executor instructions.

## Compile context

Lowering receives these values outside the Rattish source:

- installed workflow ID;
- project root and entry-point path;
- compiler name and version;
- provider contracts required by Agent nodes;
- node contracts and their versions;
- referenced workflow interfaces;
- resolved external schemas;
- source and dependency fingerprints.

The compiler must not inspect ambient environment-variable values while lowering. It may
record deployment references such as an Agent profile name, but it must not resolve
profile environment values or secret values into IR.

## Canonical ordering and JSON

IR uses snake_case keys and UTF-8 JSON. Canonical serialization follows RFC 8785 JSON
Canonicalization Scheme rules. The compiler sorts language collections that have no
execution order:

- nodes by canonical node ID;
- `needs` and incoming route sources by canonical node ID;
- bindings by canonical binding name;
- workflow inputs and outputs by canonical name;
- dependencies by kind, ID, version, and path;
- effects and error kinds lexically.

The compiler sorts routes by target ID, route mode, and canonical predicate JSON. Every
matching route still fires. Route order has no execution meaning.

Source maps remain separate from semantic collections. Moving a declaration changes its
source span but does not change the normalized node record.

## Fingerprints

Every fingerprint is lowercase SHA-256 with the `sha256:` prefix.

`source.source_fingerprint` hashes the exact normalized UTF-8 source after newline
normalization. `source.compilation_fingerprint` hashes a canonical JSON object containing:

- the source fingerprint;
- installed workflow ID;
- Rattish version;
- compiler version;
- every resolved contract fingerprint;
- every external schema fingerprint;
- every referenced workflow-interface fingerprint;
- provider-contract fingerprints;
- compile-context settings that change emitted IR.

Prompt and script contents are runtime resources and do not enter the compilation
fingerprint when IR retains their paths.

## Workflow declaration

The Workflow block lowers into `workflow`:

| Rattish field | IR field | Rule |
|---|---|---|
| external installed ID | `id` | Required compile-context value. Source cannot override it. |
| `name` | `name` | Preserve decoded string content. |
| `interface-version` | `interface_version` | Positive integer, or `null` when no public version was declared. |
| `max-runs` | `max_runs` | Positive integer, or `null` for `none` and omission. |
| `timeout` | `timeout_ms` | Duration converted to integer milliseconds, or `null`. |
| `inputs` | `inputs` | Canonical names, resolved schemas, required flags, and explicit defaults. |
| `outputs` | `outputs` | Canonical names, resolved references, and embedded schemas. |

Each output reference stores its canonical root, symbol, channel, case-sensitive selector
path, selected source schema, and possible-absence flag. The declared public schema remains
separate. The runtime resolves all outputs after execution reaches quiescence and before it
publishes a passing result.

A workflow timeout counts elapsed time while the current workflow worker remains active.
Running operations, readiness waits, joins, and concurrency queues all count. Rattish IR
records this as active-processing time, but IR version 1 does not yet persist consumed time
or resume an interrupted run. A future durable runner must checkpoint consumed time and
must not count time while the background service or computer is stopped.

An omitted workflow `max-runs` or `timeout` becomes JSON null. Rattish 1 imposes no hidden
run or time limit.

## Common node fields

Every Node becomes one `nodes` entry. The compiler canonicalizes its ID and node type,
then embeds the selected runtime handler and contract identity.

| Rattish field | IR location | Default or transformation |
|---|---|---|
| operation fields | `configuration` | Convert field names from kebab-case to snake_case and apply contract defaults. |
| `allow-fail` | `execution.allow_fail` | `false` |
| `timeout` | `execution.timeout_ms` | `null`, otherwise integer milliseconds |
| `max-runs` | `execution.max_runs` | `null` |
| `max-concurrency` | `execution.max_concurrency` | `1` |
| `retry-count` | `execution.retry_count` | `0` |
| `retry-delay` | `execution.retry_delay_ms` | `1000` |
| `start` | `execution.start_declared` | `false` |
| inferred start | `execution.initial_activation` | `true` exactly when `needs` is empty |
| `finish` | `execution.finish` | `null`, `pass`, or `fail` |
| `needs` | `readiness.needs` | Canonical, resolved node IDs |
| `with` | `bindings` | Resolved and typed binding records |
| `to` | `routes` | Resolved route records and lowered predicates |
| contract preflight declarations | `preflight_checks` | Canonically sorted check IDs, severities, and descriptions |

The compiler derives `readiness.incoming_route_sources` from all resolved routes. It sets
the readiness latch and pending-signal policy to the constants required by the IR schema.

`finish: pass` and `finish: fail` have no routes. `finish: fail` still runs the node's
operation. After completion, the executor fails the workflow and cancels other work.

## Configuration values and defaults

The selected node contract owns operation-field defaults. The language contract owns
common-field defaults. Lowering writes both sets explicitly.

The compiler distinguishes omission from explicit `none` until the contract accepts or
rejects the value. Once accepted, an absent optional runtime value becomes JSON null.
`null` remains an authored JSON null and is not interchangeable with `none` during static
analysis.

Duration values become integer milliseconds. Relative Path values become normalized
project-relative strings. Absolute, home-relative, and parent-traversing paths retain
their authored access; runtime filesystem operations follow OS permissions.

Opaque strings such as model and strategy names preserve their authored spelling.

## Bindings

Each `with` entry becomes one binding record containing:

- canonical binding name;
- literal or resolved reference source;
- proven source schema;
- destination input-port schema;
- an explicit default record;
- resolved runtime delivery instructions.

Compact bindings with no default lower to `{"present": false}`. An expanded default,
including JSON null, lowers to `{"present": true, "value": ...}`. The runtime applies a
default only when the source is absent.

A reference records its root, resolved symbol, control channel, case-sensitive JSON path,
optional-output status, and resolved schema. IR never stores the text of a resolved
secret.

The runtime snapshots bindings when a node execution starts. Node output references read
the latest successful value in the same activation lineage at that moment.

Compiler and loader entry points return immutable `ValidatedRattishIR`. Runtime entry
points reject ordinary mappings. Loading cached or external IR also checks installed node
and provider contract fingerprints. Workflow-backed nodes require resolved child
interface and compilation fingerprints before the loader marks the document valid.

## Agent nodes

An Agent requires `provider`. The compiler resolves omitted `model` and `effort` from the
versioned provider contract. It writes the final provider ID, provider-contract version
and fingerprint, model, effort, and optional profile name into `resolutions.provider`.

Profiles remain deployment configuration. They cannot change the compiled provider,
model, or effort.

Supplying neither `prompt` nor `prompt-path` lowers to `configuration.prompt: ""` and
`configuration.prompt_path: null`, with `RATTISH_BLANK_AGENT_PROMPT` as a warning.

Without a declared output schema, `output.schema` is String. With `output-schema` or
`output-schema-path`, the compiler embeds the resolved author schema. The two source
fields are mutually exclusive.

Every `with` value enters the node's activation-local symbol table. Contract-matching names
also retain their declared delivery, including workflow inputs, stdin, and environment
variables. Unmatched names lower with `local_binding` delivery and use their inferred source
schema as the destination schema. Configuration placeholders resolve only local names.

Boolean binding expressions lower to an `expression` source containing the same typed tree
used by route predicates. Their source schema is Boolean. The runtime evaluates the tree
against the node activation snapshot before rendering configuration.

## Command and script nodes

`bash-command` has one reserved input port, `stdin`. It lowers to `stdin` delivery with
UTF-8 encoding.

Every other binding name becomes an environment variable. The compiler converts ASCII to
uppercase and hyphens to underscores. `build-mode` therefore becomes `BUILD_MODE`.
Structured values use compact canonical JSON. Runtime precedence is:

```text
inherited process environment
node configuration env
with binding environment
```

Later entries replace earlier values. Ambient variables do not affect compilation.

The successful output schema has required `stdout`, `stderr`, and `exit_code` fields.

`python-script` and `shell-script` use the same binding delivery and environment
precedence. Their explicit `args` retain author order. The runtime invokes the resolved
project-relative `script_path` through `python` or `bash`, respectively. Their successful
output adds the resolved `script_path` to the command output fields.

## Runtime handlers and deployment preflight

Each compiled node names a versioned-contract `runtime_handler`. Executors dispatch by
that immutable handler ID, not by matching the authored node type. A handler returns a
success value or a structured failure. The executor validates every successful value
against the output schema embedded in IR before publishing it as node output. A schema
violation is a runtime failure, never a partially valid output.

The compiler also copies each contract's preflight declarations into the node IR. A
declaration contains a stable check ID, severity, and description; it contains no
machine-specific result. Deployment preflight selects executable checks by the pair
`(runtime_handler, check_id)` and runs all declarations before starting a workflow.
Missing check implementations are preflight errors. This prevents an executor from
silently ignoring a contract requirement it does not understand.

Compilation deliberately does not run these checks. A missing file, shell, provider,
credential, or other deployment resource therefore does not invalidate Rattish source or
compiled IR. It prevents execution only in the environment where preflight fails.

The `read-file` handler resolves relative paths from `source.project_root`, applies the
contract's encoding and error-mode defaults, enforces the runtime file-size limit, and
returns the fixed output object declared by its contract.

## Break nodes

A Break node lowers its `loop` field to a canonical loop node ID in both configuration and
`control.loop_node_id`. Its control record fixes these behaviors:

- cancel queued and not-yet-started loop activations;
- do not cancel already-running branches;
- wait for running branches to settle before the loop completes;
- treat repeated Break executions in one lineage as idempotent.

Break has no bindings or routes. Its output contains the canonical loop ID and message.

## Workflow nodes

A Workflow node resolves exactly one of `workflow-id` and `workflow-path`. The compiler
loads the child's public interface during compilation.

When the source omits `version`, the compiler selects the current child interface version
and freezes it in `configuration.version` and `resolutions.workflow.interface_version`.
The resolution also stores the child workflow ID, source kind, source locator, interface
fingerprint, child compilation fingerprint, and complete input and output schemas. The
two fingerprints distinguish public compatibility from the exact executable dependency.

Workflow-node bindings use `workflow_input` delivery. The node's output schema is an
object made from the child's public outputs. Child node IDs never enter the caller IR.

## Routes and predicates

An unconditional route lowers with mode `unconditional`, a null predicate, and eligible
outcomes `success` and `allowed_failure`. A conditional route stores its normalized
predicate. An `otherwise` route has a null predicate and fires only when no conditional
route matched. Unconditional routes fire independently.

Predicate lowering:

- removes grouping nodes after preserving precedence in the tree;
- canonicalizes `and`, `or`, and `not` into recursive records;
- embeds literal operand schemas;
- resolves reference operands and their schemas;
- preserves case-sensitive JSON member selectors;
- compiles `succeeded` and `failed` into status predicates;
- records `is null`, `is not null`, `exists`, comparisons, `contains`, and `matches`
  without runtime type coercion.

## Execution policies

IR version 1 writes the accepted execution policies as constants. An executor must reject
an IR document with different values rather than inventing behavior.

An incoming signal waits while any `needs` latch remains unsatisfied. Signals coalesce
until unlock. After unlock, same-group signals coalesce and different activation groups
queue by arrival time. A node snapshots all available input values when its execution
starts.

The executor handles one producer completion in this order:

1. record completion and the latest successful output;
2. satisfy readiness latches;
3. emit every matching route;
4. schedule newly runnable targets and snapshot their inputs.

At quiescence, a partial join that cannot receive its missing completions fails with
`RATTISH_JOIN_UNRESOLVED`. Waiting alone never creates an arbitrary timeout.

## Source maps

Every node stores its declaration span. `source_map` repeats workflow and node spans as a
direct diagnostic index. Positions use zero-based UTF-8 byte offsets and one-based line
and column numbers.

Source maps contain source locations only. They must not contain resolved secret text.

## Final validation and publication

The compiler validates candidate IR against `ir.schema.json`, then runs semantic IR
invariants for reference resolution, graph consistency, schema compatibility, contract
identity, and terminal rules. It publishes IR atomically only after both passes succeed.

The executor repeats the IR version, schema, and semantic-invariant checks before starting
a run.

## Internal artifact cache

Raticode stores compiled IR in its app data directory. Authors edit the `.rattish` source,
not this artifact. The cache maps the resolved source path to one internal record and
uses a fingerprint of these compilation inputs:

- normalized source text;
- compiler version;
- compile-context workflow ID and project root;
- every available node contract identity, version, and fingerprint.

Using every available contract makes invalidation conservative. Installing or changing a
contract may cause an extra compilation, but it cannot leave IR compiled against stale
contract rules.

The tool validates cached IR against the current IR schema before reuse. A missing,
truncated, malformed, stale, or schema-invalid record is a cache miss. The compiler
rebuilds it and publishes the replacement with an atomic filesystem rename. Compilation
errors leave the last valid artifact untouched.

Deployment state does not enter this cache key. `rattish preflight` may reuse compiled IR,
but it always reruns its checks against the current environment.
