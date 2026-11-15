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
- `fallback` lists providers to switch to when this one is rate-limited, out of allowance, overloaded, signed out or not installed. It works as on agent: see [Fallback providers](./agent.md#fallback-providers) (from 0.5.5).
- `cache: 7d` reuses a saved answer instead of calling the model again. See [Result cache](#result-cache) (from 0.5.5).

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
| `fallback` | list of up to 4 entries | `[]` | Providers to try next, each with `provider` and optional `profile`, `model` and `effort`. See [Fallback providers](./agent.md#fallback-providers). |
| `cache` | duration or null | Not set | Reuse a saved answer for this long, such as `30m`, `12h`, `7d` or `2w`, at most `365d`. See [Result cache](#result-cache). |

## Result cache

New in 0.5.5. Add `cache` with a duration to reuse a successful answer instead of calling the model again:

```rattish
Node summarize:
  type: common-llm-task
  provider: claude-code
  task: summarize
  cache: 7d
  with:
    target: input.notes
```

- A run reuses a saved answer while it is younger than the duration and nothing that shapes the answer changed. Each answer keeps the expiry it was saved with, so raising the duration later doesn't extend answers already saved. The saved answer is keyed on the provider, profile, model and effort that would answer, the rendered prompt and bound inputs, the output schema and the node's compiled settings. Change any of them and the step calls the model again. Moving the node in the file, editing other nodes or changing the duration keeps its saved answers.
- Each fallback provider has its own saved answers. A step that switches to a fallback looks for that provider's answer before calling it.
- A cached step shows as **Cached** in the [Run Timeline](../studio/runs.md#cached-steps). It makes no model call, so the run's usage records 0 tokens and $0 for it, and it doesn't count toward `max-tokens` or `max-cost-usd`.
- Only successful answers are saved. Failures, and steps that read a `secret["NAME"]`, are never saved.
- `cache` can't be combined with `memory: run` or `memory: all`, because remembered conversation changes each answer.
- Files the model would read with its own tools aren't part of the key. If the answer depends on files that change, use a shorter duration or clear the cache.
- [Test sets](../workflows/tests.md) always skip the cache, so a test case checks fresh answers.
- Agent nodes can't use `cache`, because an agent can change files. [jev](./jev.md#result-cache) nodes can.

Saved answers live in the data folder under `rattish-cache/<workflow-id>/`, one folder per workflow, readable only by your user account. Each workflow keeps up to 32 MB; past that, the answers that expire first are removed. An answer over 2 MB is used for its run but not saved.

To clear a workflow's saved answers, open **Workflow settings** and choose **Clear cache** under **Result cache**, or run `Raticode rattish cache clear path/to/workflow.rattish` (`--workflow-id ID` names it by ID, `--all` clears every workflow). `Raticode rattish cache show` prints how many answers a workflow has saved.

## Inputs and output

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output is a string, or the value that passed the declared output schema. Select fields downstream only when the schema declares them.

## Preflight and limits

The provider and working directory must be available. With a `fallback` list, the step can run when any of its providers is installed, and each entry's profile is checked. The task type doesn't stop the provider from changing files with its own tools, so set permissions to match.

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
