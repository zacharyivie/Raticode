---
title: Inputs, outputs and bindings
description: Pass typed values between nodes and expose a reusable workflow interface.
pageClass: docs-layout
---

# Inputs, outputs and bindings

Give a workflow a typed interface instead of pasting values into prompts by hand.

## Declare an input

Each workflow input has a schema and can be required. Each public output names its source and schema:

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

The `stdin` port replaces the writer's authored content. Pass the input as JSON when you run it:

```bash
Raticode rattish run WORKFLOW_PATH --input 'message="Hello from an input"' --format json
```

- The value after `=` is JSON, so a string needs JSON quotes inside the shell argument.
- Repeat `--input` for more values.
- Invalid inputs fail before anything runs.

## Bind another node's result

A `with` block binds a reference or a literal to a local name:

```text
with:
  report: node.collect.output.stdout
```

- Use the name in a prompt as `{{report}}`.
- If the name matches one of the node's input ports, the value also goes to that port.
- Command and script nodes get other bindings as uppercase environment variables: `build-mode` becomes `BUILD_MODE`. Structured values arrive as compact JSON.
- Output selectors use the schema's exact field names. Those can contain underscores, even though Rattish fields use hyphens.

## Missing values and defaults

A branch can reach a consumer before the producer succeeds, and an allowed failure produces no output. For an optional reference, give a compatible default in an expanded binding, or change the readiness conditions:

```text
with:
  report:
    from: node.collect.output.stdout
    default: No report was available.
```

The compiler checks the binding's type and whether its source can be available. At run time, the consumer gets the latest successful value from its own activation lineage at the moment it starts.

## Secrets

Reference an environment-backed secret with exact brackets, such as `secret["SERVICE_TOKEN"]`, in a compatible port.

- Compilation records the reference, never the credential.
- Secrets can't be route predicates or public workflow outputs.
- Raticode doesn't turn environment variables into an encrypted vault. See [Permissions and local data](../advanced/security.md).

## Reuse a child workflow

A `workflow` node calls another workflow through its public inputs and outputs. The child's internal node results stay private. The compiler reads the child's interface and freezes the selected version. See [Workflow nodes](../nodes/workflow.md) for the fields.
