# Rattish language design

## Scope

**Accepted.** Rattish will be the only future first-class workflow authoring language.
Existing TOML workflows will need an export path to `.rattish`. The executor will not accept
Rattish directly and will not retain TOML as an equal authoring path.

Rattish is an indentation-sensitive language with a deliberately small fixed grammar. It
may resemble YAML, but it will not accept general YAML syntax or use YAML semantics.

The eventual language definition must contain:

- A lexical specification
- Normative EBNF
- Static semantic rules
- Execution semantic rules
- A lowering specification from source AST to JSON IR
- Stable diagnostic codes
- Valid and invalid conformance examples

## Source encoding and layout

**Accepted.** Rattish source is UTF-8 text. Newlines are significant. One indentation level
is two columns. In leading indentation, one tab is equivalent to two spaces. Mixed tabs
and spaces are accepted when they resolve to a valid two-column level. The formatter and
integrated editor emit two spaces rather than tab characters. Tabs inside string content
remain literal content.

Indentation creates blocks through lexer-generated `INDENT` and `DEDENT` tokens.

## Language version

**Accepted.** Source declares its Rattish language version with this directive:

```rattish
Rattish: 1
```

Defaults and semantics are frozen by language version. A newer compiler may continue to
compile an older language version, but it must not silently apply newer defaults.

## Identifiers

**Accepted.** Keywords, field names, declaration identifiers, and references use
kebab-case and compare case-insensitively.

**Accepted normalization rules:**

- Identifiers contain ASCII letters, digits, and hyphens.
- An identifier starts with an ASCII letter.
- Consecutive hyphens and a trailing hyphen are invalid.
- The compiler canonicalizes identifiers to lowercase.
- `Review-Code`, `review-code`, and `REVIEW-CODE` resolve to the same identifier.
- Two declarations that canonicalize to the same identifier are an error.
- The formatter emits lowercase canonical identifiers.
- The recoverable source tree retains the user's spelling until formatting is requested.

The compiler does not normalize the source buffer or ordinary values. Strings, prompts,
commands, paths, URLs, model names, JSON keys, JSON string values, and structured-output
member selectors preserve case. Within `node.Review.output.pullRequestId`, the namespace
and node ID compare case-insensitively while `pullRequestId` selects an exact JSON key.
Bracket selectors address keys that are not Rattish identifiers:

```rattish
node.review.output["Display Name"]
node.review.output["result.value"]
```

Display values such as a workflow name are strings and may contain spaces and Unicode:

```rattish
Workflow:
  name: "Review pull request"

Node review-code:
  type: agent
```

## Workflow identity

**Accepted.** A workflow receives an ID when created or imported. The registry allocates
the lowercase slug of the workflow name, adding a one-up suffix when necessary:

```text
review-pr
review-pr-2
review-pr-3
```

The allocated ID remains static when the display name changes. Importing an exported
workflow performs normal creation and allocates an ID against the destination registry.

The source does not need to expose the installed workflow ID. IR generation receives the
allocated ID as compile context.

## Declaration order

**Accepted.** Declaration order has no execution meaning. References may point forward
or backward in the file. The compiler resolves the complete symbol table before it
analyzes control flow.

## Draft source shape

**Accepted.** The workflow declaration uses a block with `name` as an ordinary field:

```rattish
Workflow:
  name: Review PR
```

The declaration does not place the display name in its header. The following larger
example shows the current design. The normative grammar lives in
[`../spec/grammar.ebnf`](../spec/grammar.ebnf).

```rattish
Rattish: 1

Workflow:
  name: "Review PR"
  timeout: none

Node plan:
  type: agent
  provider: codex
  profile: codex-default
  prompt-path: "./prompts/plan.md"
  start: true

  to:
    - implement-api
    - implement-ui

Node implement-api:
  type: agent
  provider: codex
  profile: codex-default
  prompt-path: "./prompts/implement-api.md"
  max-runs: 10

  needs:
    - plan

  to: review

Node implement-ui:
  type: agent
  provider: codex
  profile: codex-default
  prompt-path: "./prompts/implement-ui.md"
  max-runs: 10

  needs:
    - plan

  to: review

Node review:
  type: agent
  provider: codex
  profile: codex-default
  prompt-path: "./prompts/review.md"
  output-schema-path: "./schemas/review-result.json"
  allow-fail: true

  needs:
    - implement-api
    - implement-ui

  to:
    - implement-api when not node.review.output.api-approved
    - implement-ui when not node.review.output.ui-approved
    - complete when node.review.output.approved
    - review-error when failed

Node review-error:
  type: notification
  needs:
    - review
  finish: fail

Node complete:
  type: notification
  needs:
    - review
  finish: pass
```

## Values and types

**Accepted.** Rattish supports native, unescaped JSON values where a field accepts JSON.
The grammar permits any strict JSON value. Each field contract narrows the values it
accepts. For example, an output schema requires an object even though the lexer can read
an array or scalar JSON value.

**Accepted Rattish 1 value types:**

- String
- Integer
- Number
- Boolean
- Path
- List
- Object
- Null

Path is a distinct semantic type with string source syntax. This lets the compiler apply
project-root, portability, and filesystem rules without adding a second path literal.
Duration and identifier-valued enums are configuration types declared by field contracts;
they are not JSON workflow payload types.

## Inline JSON

**Accepted in principle.** Fields such as an output schema may contain native JSON rather
than an escaped string:

```rattish
output-schema: {
  "type": "object",
  "properties": {
    "approved": {"type": "boolean"},
    "findings": {
      "type": "array",
      "items": {"type": "string"}
    }
  },
  "required": ["approved", "findings"],
  "additionalProperties": false
}
```

**Accepted.** Inline JSON follows strict JSON syntax. It has no comments or trailing
commas. The Rattish lexer recognizes a balanced JSON value and delegates its contents to
a strict JSON parser while preserving source spans.

## Output schemas

**Accepted.** A node may declare an inline JSON output schema or a path to a JSON schema:

```rattish
output-schema: {"type": "object"}
```

```rattish
output-schema-path: "./schemas/result.json"
```

**Accepted rules:**

- The two fields are mutually exclusive.
- Schemas use JSON Schema Draft 2020-12.
- A schema file is a compiler dependency and must exist during compilation.
- The compiler validates and embeds the resolved schema into IR.
- Remote schema references are forbidden.
- Local references must resolve within the project and become part of the compilation
  fingerprint.

## Prompts

**Accepted.** Agent nodes support either an inline prompt or a prompt path:

```rattish
prompt: "Review the implementation."
```

```rattish
prompt-path: "./prompts/review.md"
```

Both may be omitted. An omitted prompt lowers to an empty string. A blank inline prompt
with no prompt path produces a warning, not an error. Supplying both fields is a compile
error.

**Accepted.** Every Agent node declares a provider. When model or effort is omitted, the
compiler takes the value from that provider's versioned contract and writes the resolved
value into IR. The provider does not need to be installed or authenticated for source
compilation.

A prompt path is validated during deployment preflight. Unlike a schema file, it does not
need to exist for source compilation because its content does not define static workflow
types.

## Paths and project root

**Accepted.** Every installed workflow has a project folder. All relative execution paths
resolve from that folder.

**Accepted.** The default project folder is the directory containing `workflow.rattish`.
Portable source retains project-relative paths rather than machine-specific absolute
paths.

Paths that escape the project folder require explicit external filesystem permission.

## Defaults

**Accepted.** Defaults belong to the language and node contracts, not to the UI. A field
with a declared default is optional. The compiler expands every execution-relevant
default into IR. The UI reads the same contracts and displays the same defaults.

Known defaults include:

```text
allow-fail = false
node timeout = none
workflow timeout = none
node max-runs = none
workflow max-runs = none
node max-concurrency = 1
prompt = ""
```

**Accepted.** Rattish does not impose a run limit by default. Authors may add workflow or
node `max-runs` when a bounded execution is desired. A configured limit fails the workflow
when the next activation would exceed it.

## String forms

**Accepted.** Rattish 1 supports bare single-line strings, double-quoted strings, and the
`|` literal block string:

```rattish
name: Review PR
prompt-path: "./prompts/review #1.md"
prompt: |
  Review the implementation.
  Return structured findings.
```

- A bare string consumes the remainder of one logical line, excluding an unquoted comment,
  and trims surrounding whitespace.
- A double-quoted string uses JSON escape sequences.
- A `|` block string preserves embedded newlines after removing its common block
  indentation.
- A block ends at the first nonblank line whose indentation is equal to or less than the
  field containing `|`. Blank lines inside the block do not end it.
- Single-quoted, triple-quoted, and folded multiline strings are omitted from Rattish 1.

Field contracts resolve whether a bare token is a string, identifier, Boolean, number,
duration, or keyword. Values containing comment markers or meaningful leading or trailing
spaces should use quotes or a block string.

## Comments

**Accepted.** `#` begins a comment outside double-quoted strings, literal block strings,
and embedded JSON. A comment continues to the end of its physical line. Strict embedded
JSON does not gain comment syntax.

## Empty workflows

**Accepted.** A document containing a valid version directive and Workflow declaration
but no Node declarations parses and compiles. Deployment preflight reports that it has
nothing to execute. The graph editor may render it as an empty workflow.

## Grammar and node contracts

**Accepted.** The EBNF defines document structure, declarations, layout, common control
fields, values, bindings, routes, and predicates. It does not enumerate every field owned
by every node type. Versioned node contracts perform that validation. Built-in and future
plugin node contracts use the same interface, so adding a plugin does not require a new
parser.

For example, `effort` is outside the prompt because it returns to the node field's
indentation:

```rattish
Node review:
  prompt: |
    Review the implementation.

    Return structured findings.
  effort: high
```

**Accepted.** A nonempty block string produces exactly one trailing newline. Source blank
lines inside the block remain content, while blank lines after its final content do not add
extra trailing newlines.

## Node-local bindings and inputs

**Accepted, revised.** A `with` block defines typed locals for one node activation. A local
whose name matches a declared input port is also delivered to that port. Other locals remain
available to configuration templates. A compact binding is available when the value is
guaranteed to exist:

```rattish
with:
  pr-number: input.pr-number
```

A possibly absent cyclic or allowed-failure value declares an explicit default:

```rattish
with:
  findings:
    from: node.review.output.findings
    default: []
```

The compiler verifies the source value and default against the destination input type. It
does not invent an empty string, empty collection, or null value when a source is absent.

Boolean expressions may produce locals, and configuration strings interpolate local names:

```rattish
with:
  all-good: node.review.output.coverage > 80
message: Coverage accepted: {{all-good}}
```

## References and predicates

**Accepted core reference namespaces:**

```text
input.<name>
node.<node-id>.output.<field>
node.<node-id>.status
node.<node-id>.error.<field>
trigger.events
secret["EXACT_ENV_NAME"]
```

An exact reference preserves its value type. A reference embedded in a string becomes
text.

A node may bind an output from a non-direct ancestor. The runtime selects the latest
successful output in the same activation lineage at the moment the consumer starts.
Loop iteration values use `node.<loop-id>.output.<field>`. Workflow inputs use
`input.<name>`; Rattish does not provide duplicate `loop.*` or `workflow.*` aliases.

Prompt interpolation is a separate, deliberately small template mechanism. An Agent
prompt may interpolate only names declared in that node's `with` block. Authors place
graph references in `with` rather than reaching into the graph directly from prompt text.

**Accepted predicate operators for Rattish 1:**

```text
==  !=  <  <=  >  >=
contains
matches
and  or  not
exists
is null
is not null
```

Boolean references may be predicates without `== true`. `and` binds more tightly than
`or`, and parentheses override precedence. The compiler validates regular expressions in
`matches`. Arithmetic, general function calls, and implicit string-to-number conversion
are out of scope for Rattish 1.

## Normative grammar

The Rattish 1 grammar is maintained in [`../spec/grammar.ebnf`](../spec/grammar.ebnf).
Lexical rules live in [`../spec/lexical-spec.md`](../spec/lexical-spec.md). Node-specific
fields are validated by versioned contracts rather than enumerated in the parser grammar.
