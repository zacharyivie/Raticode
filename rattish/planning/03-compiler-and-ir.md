# Rattish compiler and IR

## Compiler boundary

**Accepted.** The Python compiler is the authority for Rattish grammar, static semantics,
normalization, default expansion, and JSON IR construction. It emits IR only after all
compile errors have been resolved.

The executor accepts only supported JSON IR. It does not parse Rattish, assign authoring
defaults, create slugs, repair references, or guess missing configuration.

## Compiler stages

The proposed pipeline is:

```text
UTF-8 source
  -> lexer
  -> concrete syntax tree
  -> source AST
  -> declaration and name resolution
  -> type and reference analysis
  -> control-flow and join analysis
  -> effect and resource analysis
  -> normalization and default expansion
  -> versioned JSON IR construction
  -> independent structural and semantic IR validation
  -> atomic artifact publication
```

### Lexer

The lexer recognizes identifiers, keywords, punctuation, indentation, newlines, comments,
strings, duration literals, and embedded JSON boundaries. It records source spans for
every token.

### Parser

The strict parser accepts only the normative EBNF and produces a source AST. A separate
recovery mode produces error nodes for the editor. Strict compilation never treats a
recovered error node as valid syntax.

### Name resolution

The compiler builds complete, case-insensitive symbol tables before resolving references.
This permits forward references and cycles. Duplicate canonical identifiers are errors.
JSON object member selectors remain case-sensitive. The semantic AST stores normalized
Rattish identifiers separately from preserved string and JSON selector values.

### Type and reference analysis

The analyzer validates input bindings, structured output fields, predicate operands,
optional outputs after allowed failures, and node-specific fields. It must not defer a
statically knowable mismatch to runtime.

The analyzer follows [`../spec/static-semantics.md`](../spec/static-semantics.md) and
Rattish Schema Profile 1. External schemas must exist during compilation. It resolves
project-local `$ref` values, rejects remote references, embeds the resolved schema in IR,
and fingerprints every schema dependency.

### Control-flow analysis

The analyzer validates route targets, readiness declarations, inferred starts, branches,
joins, terminal markers, cycles, run limits, and unreachable or contradictory rules.

### Normalization

The compiler canonicalizes identifiers, resolves language-version defaults, expands node
contract defaults, normalizes durations, and constructs explicit edges and join policies.

## IR role

The `.rattish` file remains the authoring source of truth. IR is an immutable execution
artifact for one exact source revision and compile context.

Users should not edit IR. The IR can be verbose because it is optimized for validation
and execution rather than authoring.

The review-draft machine format is
[`../schemas/ir.schema.json`](../schemas/ir.schema.json). Normative lowering rules are in
[`../spec/lowering.md`](../spec/lowering.md).

## Required IR properties

The JSON IR should include at least:

- IR version
- Rattish language version
- Compiler version
- Installed workflow ID
- Workflow display name
- Source fingerprint
- Project root association or project-relative path policy
- Fully expanded workflow defaults
- Fully expanded node defaults
- Canonical node IDs and operation types
- Explicit routes and predicates
- Explicit readiness requirements and predecessor lists
- Activation-lineage and activation-group policies
- Locked-node pending-signal coalescing and readiness-event ordering
- Per-node concurrency and activation-group queue policy
- Input and output contracts
- Embedded normalized output schemas
- Resolved child-workflow public-interface and compiled-dependency fingerprints
- Provider and model declarations without resolved secrets
- Run limits and timeouts
- Declared filesystem and network effects
- Source spans or a source map reference
- Plugin contract identifiers and versions when plugins exist

IR must never contain resolved secret values.

## IR validation

The compiler performs two checks before publication:

1. Structural validation against a versioned JSON Schema.
2. Semantic invariant validation using the same invariant library used by compiler
   analysis and defensive executor loading.

JSON Schema alone cannot prove reference resolution, graph consistency, join correctness,
or type compatibility.

The executor validates IR version and invariants again before starting. This protects the
runtime from malformed IR produced outside the compiler or damaged at rest.

## Source and dependency fingerprint

**Proposed.** A compilation fingerprint covers every input that can change emitted IR:

- Canonical Rattish source content
- Language version
- Compiler version
- Allocated workflow ID
- External schema contents and local schema references
- Referenced workflow public contracts
- Plugin contracts and versions
- Project configuration that changes defaults or lowering

Prompt contents do not need to invalidate compilation when IR stores a prompt path. They
do affect deployment readiness and may need a separate runtime resource fingerprint.

## Registry mapping and cache

The workflow registry should record:

```json
{
  "workflowId": "review-pr-2",
  "sourcePath": "/projects/review-pr/workflow.rattish",
  "projectRoot": "/projects/review-pr",
  "sourceHash": "sha256:...",
  "languageVersion": 1,
  "compilerVersion": "0.2.1",
  "irVersion": 1,
  "irPath": ".../compiled/review-pr-2.json"
}
```

Compiled IR belongs in managed application data rather than the portable workflow folder.

## Source change behavior

**Accepted in principle.** Gofer tracks `.rattish` changes and recompiles after a debounce.

**Proposed transaction:**

1. Detect a source content hash change.
2. Mark the installed workflow dirty.
3. Parse and compile the new revision.
4. Write candidate IR to a temporary file.
5. Validate the candidate structurally and semantically.
6. Atomically publish the artifact and update the registry.
7. Run deployment preflight and record readiness separately.

If compilation fails, Gofer retains the last valid IR but marks it stale. New runs are
blocked until current source compiles. A run already using an immutable IR revision may
finish.

## Deployment preflight

Compilation validates portable language and graph correctness. Preflight checks resources
on the current machine, including:

- Prompt and script files
- Working directories
- Provider and model availability
- Provider profiles
- Executables
- Credentials and secret readiness
- Filesystem permissions
- Network policy
- Plugin runtime handlers

Preflight is not required to render a compiled workflow in the graph editor. The workflow
assistant should not claim completion until both compilation and preflight succeed for the
current environment. An intentionally blank prompt produces a warning rather than a
preflight error.

## Diagnostics

Every diagnostic should contain:

- Stable code
- Severity
- Human-readable message
- File path
- Start and end source positions
- Declaration and field identity when known
- Structured details
- Suggested corrections when safe

Example:

```json
{
  "code": "RATTISH_UNRESOLVED_NODE",
  "severity": "error",
  "message": "Node 'publish' routes to unknown node 'build'.",
  "file": "workflow.rattish",
  "start": {"line": 18, "column": 9},
  "end": {"line": 18, "column": 14},
  "suggestions": ["build-app", "build-docs"]
}
```

The CLI must support machine-readable diagnostics for agent correction loops.

## Proposed CLI

```bash
gof rattish check workflow.rattish
gof rattish check workflow.rattish --json
gof rattish compile workflow.rattish --output workflow.json
gof rattish format workflow.rattish
gof rattish explain workflow.rattish
gof rattish inspect-ir workflow.json
gof workflow preflight workflow.rattish
```

The exact commands and whether direct IR output is exposed outside development mode remain
open. Normal users should not need to locate or manage IR files.
