# Rattish 1 static semantics

## Scope

This document defines the checks a conforming Rattish 1 compiler performs after parsing and
before emitting IR. Execution behavior belongs in the execution semantics. Node-specific
fields, defaults, inputs, outputs, effects, and preflight requirements belong in versioned
node contracts.

A compiler may report several diagnostics in one run. It must not emit candidate IR while
any error remains. Warnings do not block IR emission.

## Compilation context

Compilation receives:

- parsed Rattish source and source spans;
- the project root;
- the installed workflow ID, when compiling an installed workflow;
- the Rattish language version;
- built-in and plugin node contracts;
- provider contracts;
- referenced workflow public contracts;
- external schema contents.

Provider executables, credentials, prompt files, script files, and working directories are
not required for compilation unless their contents define a static contract. Deployment
preflight checks those resources.

Every contract or file that changes lowering becomes part of the compilation fingerprint.

## Name roles and normalization

The compiler canonicalizes Rattish identifiers to lowercase only when they appear in an
identifier role. Identifier roles include:

- language keywords and field names;
- node IDs and route targets;
- workflow input and output names;
- `needs` and Break loop references;
- built-in and qualified plugin node types;
- provider IDs;
- values that a contract marks as a case-insensitive closed enum.

The compiler preserves strings, model names, paths, URLs, commands, prompts, opaque
strategy names, JSON keys, JSON string values, and JSON member selectors.

The concrete syntax tree retains every source spelling. The semantic AST stores both the
source span and canonical value for each normalized identifier.

Two declarations in one namespace that canonicalize to the same name are an error.

## Symbol tables

The compiler builds complete symbol tables before resolving references. Declaration order
does not affect resolution. Rattish 1 has separate namespaces for:

- nodes;
- workflow inputs;
- workflow outputs;
- workflow variables, when declared;
- schemas, when named schemas are supported;
- provider and node contracts supplied by the compile context.

A reference must resolve to exactly one symbol of the expected kind. Missing, ambiguous,
and wrong-kind references are errors.

## Workflow declaration

A document contains exactly one Workflow declaration. `name` is required and must be a
nonempty String after trimming for validation. The compiler preserves the authored display
value.

A workflow may have no Node declarations. Such a workflow compiles successfully. Preflight
reports that it has nothing to execute.

The installed workflow ID does not come from source. Creation and import allocate it in
the registry, then pass it to the compiler. Renaming the workflow does not change that ID.

## Value types

Rattish 1 workflow data uses these semantic types:

```text
String
Integer
Number
Boolean
Null
Path
List<T>
Object
```

Object shapes and nested collection types use Rattish Schema Profile 1. Integer is a
subtype of Number. Path has string source and runtime representation, but it remains a
distinct semantic type. Path is assignable to String. A general String is not assignable
to Path without a field contract that performs path validation.

Null participates in a data type only through the nullable `anyOf` form defined by Schema
Profile 1. The compiler does not insert null into undeclared types.

Duration and closed Enum are configuration types. They are not general workflow payload
types.

The compiler performs no implicit string-to-number, string-to-Boolean, or scalar-to-list
conversion.

## Node contract selection

Every Node declares `type`. The compiler canonicalizes the type name and loads exactly one
compatible node contract. An unknown type or missing contract is an error.

The selected contract determines:

- operation-specific legal and required fields;
- mutually exclusive fields;
- configuration types and defaults;
- input ports that receive same-named `with` locals;
- the successful output schema;
- possible error kinds;
- effects and preflight requirements;
- the runtime handler ID and contract version.

Common node fields come from the Rattish language contract. A node-specific contract cannot
redefine their meaning.

Unknown fields are errors. A field repeated after case normalization is an error.

## Defaults

The compiler applies defaults after contract selection and before type and control-flow
analysis. Source omission and an explicit `none` remain distinguishable until the field
contract decides whether `none` is legal.

The compiler writes every execution-relevant default into IR. The UI and formatter must
read defaults from the same versioned contracts instead of maintaining another list.

Agent `provider` is required. The compiler resolves omitted model and effort values from
the selected provider contract and writes the resolved values into IR. A provider need not
be installed or authenticated, but its compile-time contract must be available.

An Agent profile is deployment configuration only. It may supply environment and secret
references, but it does not supply or override provider, model, or effort. Profile
existence and provider compatibility are preflight checks rather than compile-time
requirements.

## References

Core reference roots are:

```text
input
node
trigger
secret
```

Each root has a context-specific schema. A reference must be legal in its field's declared
reference mode.

For `node.<node-id>`, the compiler resolves `node`, the node ID, and the control members
`output`, `status`, and `error` case-insensitively. Selectors after `output` and `error`
preserve case and follow the referenced JSON Schema. Bracket selectors address arbitrary
JSON keys and array indexes.

`trigger.events` is the immutable list of event objects supplied when the run starts.
Looping over those events uses a Loop node with `source.type: trigger-events`; fields from
one iteration are read through `node.<loop-id>.output`. `secret["EXACT_ENV_NAME"]` names an
environment value without reading it during compilation. Secret names are case-sensitive,
must use bracket notation, and lower to IR without their resolved values.

Rattish 1 does not define `workflow.*` or `loop.*` aliases. Workflow inputs use `input.*`.
Called workflow outputs and Loop iteration values use `node.<node-id>.output.*`.

A node may reference a non-direct ancestor. The referenced node must be able to execute in
the same activation lineage. At runtime the reference selects the latest successful value
available when the consumer starts.

A reference to normal output is optional when some valid path can reach the consumer
before the producer has succeeded. An allowed failure does not create a successful output.

Secret references carry a secret-qualified String type. They cannot appear in predicates,
public workflow outputs, logs, source maps containing resolved values, or IR as resolved
secret text.

Rattish also permits plaintext strings in credential-shaped fields. Static analysis warns
when a field name or value pattern suggests a credential. It does not reject the source.
The compiler and exporter must never resolve a secret reference into source, IR, or a
portable bundle.

## Local bindings and input delivery

A node's `with` block defines its activation-local symbol table. Every binding is available
to configuration templates in that node. A binding whose canonical name matches a declared
input port is also delivered to that port. Node contracts with open environment input ports
continue to deliver each binding as an environment variable while retaining the local value.
An unmatched local name is valid.

A compact binding contains one reference or literal:

```rattish
with:
  diff: node.collect.output.diff
```

An expanded binding contains `from` and may contain `default`:

```rattish
with:
  findings:
    from: node.review.output.findings
    default: []
```

Compact bindings may contain Boolean expressions:

```rattish
with:
  all-good: node.review.output.coverage > 80
```

The compiler lowers expressions to typed IR and never evaluates them as Python. References
inside expressions follow the same availability and schema rules as direct references.

When a binding matches an input port, the compiler proves that the source schema is
assignable to the destination input schema.
If the source may be absent, the binding requires a default. The compiler validates the
default against the destination schema. A default applies only when the source is absent,
not when it is present with JSON null.

The runtime resolves and snapshots all bindings when the node execution starts. It then
renders the node configuration from that snapshot before invoking the node handler.

For a Bash node, `stdin` is a reserved input port. Other binding names map to environment
variables using uppercase ASCII and hyphen-to-underscore conversion. Binding variables
override the node's explicit `env` entries, which override the inherited process
environment. Ambient environment contents do not participate in compilation.

## Configuration templates

Author-facing string configuration fields support templates. A placeholder may name only a
local declared in that node's `with` block. Direct graph, secret, trigger, and workflow
references inside template text are errors. Authors bind those values through `with`, then
interpolate the local name.

An exact placeholder preserves the local's JSON type. A placeholder embedded in other text
renders strings directly and other JSON values as compact canonical JSON. Template rendering
recurses through string values in lists and objects. Agent prompt files and Prompt File
templates render after their file contents are loaded but use the same local snapshot.

The compiler proves that an exact placeholder's inferred schema is assignable to a typed
configuration field. Preflight defers checks that require a concrete runtime-rendered value;
the node handler still applies path, network, and configuration policy after rendering.

For inline configuration, the compiler validates every placeholder. For `prompt-path`, preflight
loads the file and validates its placeholders because prompt files are not required to
exist during portable source compilation.

`{{{{` and `}}}}` emit literal `{{` and `}}` delimiters.

Supplying both `prompt` and `prompt-path` is an error. Supplying neither lowers to an empty
prompt and produces a warning.

## Paths and schema files

The directory containing `workflow.rattish` is the default project root for an unregistered
source. An installed workflow uses the project root frozen in its registry entry. Relative Path values
resolve from that root. Authored paths may be absolute, home-relative, or traverse outside the project.
Filesystem access follows OS permissions, including through symlinks. The compiler
preserves that access in IR.

An external schema file must exist during compilation. Project-local `$ref` values are
allowed and must resolve within the project folder. Remote `$ref` values are errors. The
compiler validates the fully resolved result against Rattish Schema Profile 1, embeds it in
IR, and fingerprints every contributing file.

Prompt and script paths are runtime resources rather than static type definitions. Their
existence is a preflight check. Loop directory and tabular inputs are checked at
activation because upstream nodes may create them. An empty directory, or a glob
with no matching files, succeeds with zero iterations and no loop body activations.

## Routes

Every `to` target must resolve to a Node. Duplicate routes from one node to the same target
under the same condition are errors.

An unconditional route is eligible after success or allowed failure. A `when failed` route
requires `allow-fail: true`; otherwise it is unreachable and is a compile error. Structured
output predicates are eligible only after success.

Every conditional route may match. Route lists do not use first-match behavior.
`otherwise` matches when no conditional route from that completion matched. Unconditional
routes fire independently and do not suppress it. An unconditional route and an
`otherwise` route from the same node to the same target are contradictory and form an
error.

Predicate operands must have compatible types. Ordering operators accept Number operands
or compatible String operands. `contains` accepts a String, List, or Object on its left.
`matches` accepts String operands and a compile-time valid regular expression on its right.
Boolean references may stand alone as predicates.

Failure-detail predicates read the common error contract. Each node contract declares the
stable error `kind` values it may produce.

## Readiness requirements

Every `needs` entry must resolve to a Node. Duplicate entries are errors. A requirement
does not need a direct route to its consumer. Static reachability analysis must show that
the required node can participate in the same possible activation lineage. If no such
lineage exists, the requirement is an error.

The compiler represents loop ancestry as an ordered path. `root / outer / inner` is a
descendant of `root / outer`, but it is not compatible with `root / inner / outer` or a
sibling path. This ordering prevents requirements from joining values produced by a
different nesting structure.

An incoming route to a locked node remains pending until every requirement has completed
at least once. The executor applies the timing and coalescing rules in the execution
semantics.

The compiler reports control-flow paths that can leave a started requirement set
permanently incomplete when it can prove the condition statically. Runtime quiescence
handles data-dependent unresolved joins.

## Terminals and Break

`start: true` is illegal with `needs`. `finish: pass` and `finish: fail` are illegal with
`to`. `finish: fail` is illegal with `allow-fail: true`.

A Break node requires `loop`, which must resolve to a Loop node. It cannot declare `to`.
Control-flow analysis must find a possible activation lineage from the named loop through
the Break node. An invalid or unrelated loop target is an error.

## Workflow calls

A Workflow node declares exactly one of `workflow-id` and `workflow-path`. The compiler
resolves the referenced workflow's public interface. An unresolved child contract is a
compile error because input and output compatibility cannot be proven.

The Workflow node's `with` bindings must supply every required public input by name. Extra
locals remain available to the Workflow node's own configuration and are not forwarded to
the child. Its successful output is an Object whose properties are the child's declared
public outputs.

A public workflow output requires `from` and `schema`. The compiler proves that the source
is assignable to the declared public schema. Internal child nodes and undeclared child
outputs do not enter the caller's symbol table.

The source may be a workflow input or a node `output`, `status`, or `error` reference.
Secret and execution-local roots are forbidden. A valid source may still be absent at
runtime when control flow does not execute its producer or an allowed-failure producer has
never succeeded. Public outputs have no implicit default, so absence fails the workflow.

Rattish has no Subflow node or component-binding mode.

## Diagnostics

Every diagnostic contains:

- a stable code;
- severity `error` or `warning`;
- a primary source span;
- a short message;
- optional related spans;
- optional structured details and suggested edits.

Diagnostics use the `RATTISH_` prefix. Formatting and wording may improve within one
language version, but code meaning must remain stable. Conformance fixtures assert codes
and spans rather than complete prose.
