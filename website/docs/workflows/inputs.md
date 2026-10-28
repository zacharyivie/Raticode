---
title: Inputs, outputs and bindings
description: Pass typed values between nodes and expose a reusable workflow interface.
pageClass: docs-layout
---

# Inputs, outputs and bindings

Give a workflow an explicit interface instead of copying values into prompts by hand.

## Declare an input

Workflow inputs have a schema and can be required. Public outputs declare a source reference and schema:

```rattish
Rattish: 1

Workflow:
  name: Save a message
  interface-version: 1
  inputs:
    message:
      schema: {"type": "string"}
      required: true
  outputs:
    saved-path:
      from: node.save.output.path
      schema: {"type": "string"}

Node save:
  type: write-file
  path: reports/message.txt
  with:
    stdin: input.message
  finish: pass
```

The `stdin` port replaces the writer's authored content. On execution, supply JSON for the input:

```bash
gof rattish run WORKFLOW_PATH --input 'message="Hello from an input"' --format json
```

Quoting matters: the value after `=` is JSON, so a string needs JSON quotes inside the shell argument. Repeat `--input` for multiple values. Invalid inputs fail before execution.

## Bind another node's result

A `with` block binds a reference or literal to a local name. Node output selectors use the exact schema field names, which may contain underscores even though Rattish configuration fields use hyphens.

```text
with:
  report: node.collect.output.stdout
```

Use that local in a prompt as `{{report}}`. If the binding name matches a node input port, the value also goes to that port. Command and script nodes expose non-stdin bindings as uppercase environment variables; `build-mode` becomes `BUILD_MODE`. Structured environment values use compact JSON.

## Missing values and defaults

Some branches can reach a consumer before a producer succeeds. An allowed failure does not create a successful output. When a reference is optional, provide a compatible default in an expanded binding or redesign the readiness conditions.

```text
with:
  report:
    from: node.collect.output.stdout
    default: No report was available.
```

The compiler checks the binding's type and whether its source can be available. Runtime selection respects activation lineage and the latest successful value available at the consumer's start.

## Secrets

Use exact bracket references such as `secret["SERVICE_TOKEN"]` for environment-backed secret values in compatible ports. Compilation records the reference, not the credential. Secret values cannot be route predicates or public workflow outputs.

The application does not turn arbitrary environment variables into an encrypted secret vault. See [Permissions and local data](../advanced/security.md).

## Reuse a child workflow

A `workflow` node calls another workflow through its public inputs and outputs. The child's internal node results stay private. The compiler reads the child's interface and freezes the selected version. See [Workflow nodes](../nodes/workflow.md) for the call fields.

