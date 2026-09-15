# Rattish 1 output contracts

## Purpose

Every node type declares the value it can produce after successful execution. The
compiler uses that declaration to validate `with` bindings, route predicates, defaults,
and public workflow outputs. The executor validates actual values at runtime.

Rattish uses JSON Schema Draft 2020-12 for data declarations. Rattish 1 accepts a restricted
profile so the compiler can prove compatibility instead of guessing about complex schema
behavior.

## Completion fields

Every node completion exposes these namespaces:

```text
node.<node-id>.status
node.<node-id>.output
node.<node-id>.error
```

`status` is one of `success`, `failure`, or `cancelled`. `output` is the successful payload
defined by the node contract. `error` is `null` after success and otherwise follows the
common error schema.

```json
{
  "anyOf": [
    {
      "type": "object",
      "properties": {
        "kind": {"type": "string"},
        "code": {"type": "string"},
        "message": {"type": "string"},
        "details": {}
      },
      "required": ["kind", "code", "message"],
      "additionalProperties": false
    },
    {"type": "null"}
  ]
}
```

Control metadata does not become part of the node-specific output payload. This keeps a
consumer's schema focused on the data it actually receives.

`kind` is a stable, case-insensitive error category declared by the node contract. Route
predicates may inspect it. `code` is a more specific preserved string for diagnostics.

## Output lifetime

The runtime retains the most recent successful output for each node in the current run and
activation lineage. An allowed later failure updates `status` and `error` but does not
erase that output. If a node has never succeeded, `output` is absent.

When control flow can reach a binding before its producer has succeeded, the compiler
treats the source as optional. The binding must provide a compatible default or the
compiler rejects it.

A status reference is optional unless the producer must complete before the consumer
starts. An error reference is always optional because a successful completion has no
error document. Public workflow outputs keep their quiescence-time absence rules.

## Built-in contracts

Each built-in node contract is a versioned JSON document with this minimum shape:

```json
{
  "$schema": "../schemas/node-contract.schema.json",
  "contract_version": 1,
  "node_type": "read-file",
  "runtime_handler": "raticode.read_file",
  "configuration_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema"
  },
  "defaults": {},
  "computed_defaults": {},
  "input_ports": {"mode": "closed", "ports": {}},
  "success_output": {
    "kind": "fixed",
    "schema": {"$schema": "https://json-schema.org/draft/2020-12/schema"}
  },
  "error_kinds": [],
  "effects": [],
  "preflight_checks": [],
  "static_diagnostics": []
}
```

`configuration_schema` validates operation-specific fields after the parser has handled
common node fields. `success_output` defines `node.<id>.output`. `effects` declares
filesystem, network, provider, approval, or workflow-call behavior for static analysis and
preflight. `defaults` contains every field default owned by this contract version. An
optional runtime field without a default must declare `x-rattish-allow-absence: true` in
its configuration schema. Structural maps opt into recursive materialization with
`x-rattish-apply-property-defaults: true`. User-data maps do not receive a deep merge.

The compiler embeds the resolved contract ID, contract version, configuration, and output
schema in IR. A plugin must publish the same contract shape before the compiler accepts
its node type.

Resource nodes publish closed objects rather than display strings. `file` and `folder`
return resolved path components. `prompt-file` returns `path`, `content`, `prompt`, and the
interpolation `inputs`. `open-resource` returns its resolved `target` and concrete
`resource_type`.

## Agent outputs

An Agent without `output-schema` or `output-schema-path` has this output schema:

```json
{"type": "string"}
```

An Agent with either field produces structured output that must validate against the
declared schema. The two fields are mutually exclusive. `common-llm-task` follows the same
rule.

```rattish
Node review:
  type: agent
  provider: codex
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

An Agent response that does not satisfy its declared schema is an operation failure. Its
repair policy may retry before the executor publishes that failure.

## Public workflow interfaces

A workflow may declare public inputs and outputs in its Workflow block:

```rattish
Workflow:
  name: Security review
  interface-version: 1

  inputs:
    diff:
      schema: {"type": "string"}
      required: true

  outputs:
    findings:
      from: node.review.output.findings
      schema: {
        "type": "array",
        "items": {"type": "string"}
      }
```

Input and output names are Rattish identifiers and compare case-insensitively. An output's
`from` reference is required. Its schema is also required because the public interface is
a versioned boundary, even when the compiler could infer the same schema internally.

Public output references may read workflow inputs or a node's `output`, `status`, or
`error` channel. They cannot read `secret` or `trigger` roots. The compiler
validates every member and index selector and proves that the selected source schema is
assignable to the declared public schema.

Every declared public output is required when an otherwise successful workflow completes.
If its reference has no value, including when an allowed-failure producer has never
succeeded, the workflow fails with `RATTISH_WORKFLOW_OUTPUT_MISSING`. The runtime validates
the resolved value against the public schema and fails with
`RATTISH_WORKFLOW_OUTPUT_INVALID` on a mismatch. A cyclic producer contributes its latest
successful value. A later allowed failure does not erase that retained value.

`interface-version` is a positive Integer. Callers may request that exact version with
the Workflow node's `version` field. When a caller omits `version`, the compiler resolves
the current interface version and freezes that integer in IR.

A `workflow` node references another workflow in exactly one of these ways:

```rattish
Node review:
  type: workflow
  workflow-id: security-review

  with:
    diff: node.collect.output.diff
```

```rattish
Node review:
  type: workflow
  workflow-path: ./workflows/security-review/workflow.rattish

  with:
    diff: node.collect.output.diff
```

The compiler loads the child's public contract, validates every supplied and required
input, and gives the workflow node an object output whose properties are the child's
declared public outputs. Child node IDs and undeclared child outputs are not visible to the
caller.

`workflow-id` and `workflow-path` are mutually exclusive. The optional `version` field
constrains the public interface version. The compiler records the exact resolved workflow
ID, source or registry fingerprint, interface version, and interface schemas in IR.

Rattish has no `subflow` node. The TOML exporter maps legacy workflow and subflow calls to
this contract.

## Rattish Schema Profile 1

Rattish Schema Profile 1 supports these JSON Schema concepts for statically connected data:

- primitive types `object`, `array`, `string`, `number`, `integer`, `boolean`, and `null`;
- `properties`, `required`, and `additionalProperties`;
- homogeneous array `items`;
- `enum` and `const`;
- numeric bounds;
- string and array length bounds;
- `pattern` and `format` as runtime constraints;
- `anyOf` only when it expresses a value type plus `null`.

Rattish 1 does not accept conditional schemas, recursive schemas, `not`, or general
`oneOf`, `allOf`, and `anyOf` combinations in a statically connected contract. A future
schema profile may add them with defined compatibility rules.

External schema files must exist during compilation and declare a schema within this
profile. Project-local `$ref` values are allowed. Every reference must resolve within the
workflow project folder and becomes a compiler dependency. Remote `$ref` values are
forbidden. The compiler embeds the fully resolved schema in IR.

## Binding compatibility

For each `with` binding, the compiler proves that every value allowed by the source schema
is accepted by the destination schema. In type terms, the source must be a subtype of the
destination.

At minimum, the proof checks:

- primitive type inclusion, including integer as a subtype of number;
- required and optional object properties;
- additional-property rules;
- array item compatibility;
- enum and const inclusion;
- nullable source values;
- numeric and length bounds;
- the type of an explicit binding default.

The compiler rejects a binding when it finds an incompatibility or cannot prove
compatibility within Schema Profile 1. It does not defer a statically visible mismatch to
deployment preflight or runtime.

## JSON member selection

Rattish names and JSON member names occupy different casing domains. In this reference:

```rattish
node.Review.output.pullRequestId
```

`node`, `Review`, and `output` resolve as case-insensitive Rattish names. `pullRequestId`
selects a case-sensitive JSON member using its source spelling.

Bracket selection handles keys that are not Rattish identifiers or contain dots:

```rattish
node.review.output["pull_request_id"]
node.review.output["Display Name"]
node.review.output["result.value"]
node.review.output.items[0]
```

The compiler validates each selector against the producer schema. The lexer and parser
must not lowercase JSON member selectors.
