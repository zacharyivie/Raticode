---
title: common-llm-task
description: Use a ready-made prompt to review, summarize, explain, extract, rewrite or classify text.
pageClass: docs-layout
---

# common-llm-task

Use a ready-made prompt to review, summarize, explain, extract, rewrite or classify text.

## When to use it

Use it when the job fits one of the six task types and a short declaration is enough. For a custom prompt or the extra fields, use [agent](./agent.md).

## Example

```rattish
Rattish: 1

Workflow:
  name: Use a common LLM task

Node step:
  type: common-llm-task
  provider: codex
  task: explain
  target: Raticode workflows use explicit routes between nodes.
  instructions: Explain this sentence in plain language.
  finish: pass
```

- `task` is `review`, `summarize`, `explain`, `extract`, `rewrite` or `classify`. The default is `summarize`.
- `target` is the text to work on. `instructions` adds your criteria. You can bind upstream content and use prompt locals.
- Model and effort default to the provider contract's choice.
- Output is text unless you declare one output schema. `repair-attempts` works as on [agent](./agent.md).

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `task` | `review`, `summarize`, `explain`, `extract`, `rewrite`, `classify` | `"summarize"` | Common prompt task family. |
| `target` | string | `""` | Text for the chosen task. |
| `instructions` | string | `""` | Additional task criteria. |
| `provider` | string | Required | Rattish provider ID. |
| `profile` | string or null | Not set | Optional compatible deployment profile. |
| `model` | string | Provider default | Model ID supported by the selected provider. |
| `effort` | string | Provider default | Effort choice supported by that model. |
| `working-dir` | string | `"."` | Working directory resolved from the workflow project. |
| `memory` | `none`, `run`, `all` | `"none"` | Provider memory scope. |
| `output-schema` | object or null | Not set | Inline successful-output JSON Schema. |
| `output-schema-path` | string or null | Not set | File containing the successful-output schema. |
| `repair-attempts` | integer (min 0, max 3) | `0` | Bounded structured-output repair attempts. |

## Inputs and output

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output is a string, or the value that passed the declared output schema. Select fields downstream only when the schema declares them.

## Preflight and limits

The provider and working directory must be available. The task type doesn't stop the provider from changing files with its own tools, so set permissions to match.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
