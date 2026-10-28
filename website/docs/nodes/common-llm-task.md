---
title: common-llm-task
description: Use a task-specific prompt pattern for reviewing, summarizing, explaining, extracting, rewriting, or classifying.
pageClass: docs-layout
---

# common-llm-task

Use a task-specific prompt pattern for reviewing, summarizing, explaining, extracting, rewriting, or classifying.

## When to use it

Choose this node when the task family is clear and you want a compact declaration. Choose an agent node when you need a bespoke prompt and its broader configuration fields.

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

`task` accepts `review`, `summarize`, `explain`, `extract`, `rewrite`, and `classify`, defaulting to `summarize`. `target` is the text to work on; `instructions` adds the criteria. Bind upstream content and use prompt locals where appropriate.

Model and effort follow the selected provider contract when omitted. The output is text unless you declare one compatible output schema. Structured-output repair has the same bounded-attempt policy as agent nodes.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

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

Additional local bindings are supported. See [Inputs and bindings](../workflows/inputs.md) for delivery and type rules.

Successful output is a string by default, or the value validated by the declared output schema. Select downstream fields only when the schema declares them.

## Preflight and limits

The chosen provider and working directory must be available. A task label is not an assurance that the provider cannot change files through its own tools; use compatible permissions.

Use `gof rattish check` and `gof rattish preflight` before choosing to run the example. The installed contract is available through `gof rattish docs --format json`.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
