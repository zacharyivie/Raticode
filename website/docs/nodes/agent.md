---
title: agent
description: Ask a coding provider to perform a task with explicit prompt, model, effort, and resource choices.
pageClass: docs-layout
---

# agent

Ask a coding provider to perform a task with explicit prompt, model, effort, and resource choices.

## When to use it

Use an agent node for open-ended analysis or implementation. The provider can use its native tools according to its own permissions, so review both the task and the deployment profile before execution.

## Example

```rattish
Rattish: 1

Workflow:
  name: Call an agent

Node step:
  type: agent
  provider: codex
  prompt: Explain the project entry points without changing files.
  working-dir: .
  memory: "none"
  finish: pass
```

Provider IDs come from the Rattish provider contracts; for example, use `claude-code` rather than the legacy subscription name `claude_code`. When model or effort is omitted, compilation resolves the provider contract default. A deployment profile supplies compatible runtime settings without overriding authored provider/model/effort choices.

Use inline `prompt` or a `prompt-path`; they are mutually exclusive. Bind values through `with` and refer to locals in prompt text as `{{name}}`. `memory` selects `"none"`, `run`, or `all`; quote `"none"` so it is a string rather than an absence literal. This field does not turn on Second Brain.

The default successful output is text. Declare `output-schema` or `output-schema-path` for typed structured output, and use only one schema source. `repair-attempts` allows zero to three attempts to repair invalid structured output, which can incur additional provider usage.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `provider` | string | Required | Rattish provider ID. |
| `profile` | string or null | Not set | Optional compatible deployment profile. |
| `model` | string | Provider default | Model ID supported by the selected provider. |
| `effort` | string | Provider default | Effort choice supported by that model. |
| `working-dir` | string | `"."` | Working directory resolved from the workflow project. |
| `prompt` | string | `""` | Inline task prompt. |
| `prompt-path` | string or null | Not set | Readable prompt source file. |
| `skill` | string or null | Not set | Selected skill reference. |
| `memory` | `none`, `run`, `all` | `"none"` | Provider memory scope. |
| `tools` | list of string | `[]` | Selected provider tools. |
| `mcp-servers` | list of string | `[]` | Selected MCP server references. |
| `env` | object | `{}` | Additional string environment values. |
| `extra-paths` | list of string | `[]` | Additional authored resource paths. |
| `output-schema` | object or null | Not set | Inline successful-output JSON Schema. |
| `output-schema-path` | string or null | Not set | File containing the successful-output schema. |
| `repair-attempts` | integer (min 0, max 3) | `0` | Bounded structured-output repair attempts. |

## Inputs and output

Additional local bindings are supported. See [Inputs and bindings](../workflows/inputs.md) for delivery and type rules.

Successful output is a string by default, or the value validated by the declared output schema. Select downstream fields only when the schema declares them.

## Preflight and limits

The provider must be installed/authenticated as needed, the working directory must exist, and selected prompt/profile resources must be valid. Preflight does not guarantee account allowance or that a provider will complete the task.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
