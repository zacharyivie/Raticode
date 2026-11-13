---
title: agent
description: Ask a coding provider to do a task, with an explicit prompt, model, effort and resources.
pageClass: docs-layout
---

# agent

Ask a coding provider to do a task, with an explicit prompt, model, effort and resources.

## When to use it

Use it for open-ended analysis or implementation. The provider can use its own tools within its own permissions, so review the task and the deployment profile before you run it.

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

- **Provider IDs** come from the Rattish provider contracts. Write `claude-code`, not the legacy name `claude_code`.
- **Model and effort** default to the provider contract's choice when you leave them out. A deployment profile adds runtime settings but never overrides the provider, model or effort you wrote.
- **Prompt.** Use inline `prompt` or `prompt-path`, not both. Bind values through `with` and use them in the prompt as `{{name}}`.
- **Memory** is `"none"`, `run` or `all`. Quote `"none"` so it reads as a string, not the absence literal. It does not turn on Second Brain.
- **Output** is text by default. For typed output, declare one schema source: `output-schema` or `output-schema-path`. `repair-attempts` (0 to 3) retries invalid structured output, and each retry uses the provider again.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

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

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output is a string, or the value that passed the declared output schema. Select fields downstream only when the schema declares them.

## Preflight and limits

Preflight checks that the provider is installed and signed in where needed, the working directory exists, and the prompt and profile resources are valid. It can't promise that your account has allowance left or that the provider will finish the task.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
