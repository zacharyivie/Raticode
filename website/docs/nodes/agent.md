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

To run the node on a model server on your own computer, such as Ollama, use `provider: openai-api` with a [local model server profile](../setup.md#local-model-servers) and the model ID the server lists (from 0.5.5):

```rattish
Node summarize:
  type: agent
  provider: openai-api
  profile: ollama
  model: qwen3:8b
  prompt: Summarize the release notes in three bullets.
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
| `fallback` | list of up to 4 entries | `[]` | Providers to try next when this one can't serve the step. See [Fallback providers](#fallback-providers). |

## Fallback providers

::: info New in 0.5.5
Fallback providers are new in Raticode 0.5.5.
:::

A `fallback` list names other providers the step may switch to when its provider can't take the call, for example when your Claude plan's allowance runs out partway through a night of runs:

```yaml
Node review:
  type: agent
  provider: claude-code
  prompt: Review the current change.
  retry-count: 1
  fallback:
    -
      provider: codex
      effort: medium
    -
      provider: claude-code
      profile: work-account
```

- **Each entry** takes `provider` (required), `profile`, `model` and `effort`. Left-out `model` and `effort` come from that entry's own provider contract, not the step's. The compiler rejects a provider with no contract and fields other than these four, and warns when an entry repeats a choice the step already tries.
- **When it switches.** The step first uses its provider with its full `retry-count`. Only when the last of those attempts fails with a provider availability error does it move to the next entry, which gets the same `retry-count`, and so on down the list. The availability errors are:
  - a rate limit, or a used-up plan allowance or quota;
  - an overloaded or temporarily unavailable service;
  - an expired sign-in or rejected credentials;
  - a provider CLI that isn't installed. Retrying can't install it, so the step skips its retries and switches straight away.
- **When it never switches.** A failed task, structured output that is still invalid after `repair-attempts`, a timeout, and a run stopped by its `max-tokens` or `max-cost-usd` limit end the step as they would without a list.
- **Limits cover every attempt.** The run's `max-tokens` and `max-cost-usd` count the calls of every provider the step tried. A fallback never gets a fresh budget.
- **Preflight** checks every entry's profile and credentials. The step can run when any of its providers is installed: a missing one is a warning (`RATTISH_PREFLIGHT_FALLBACK_PROVIDER_UNAVAILABLE`), and the step skips it at run time. Only when none is installed does preflight stop the run.
- **Which provider answered.** The run record names the provider, profile, model and effort of each node's last attempt, and lists each switch with its reason and the error that caused it. The Studio's node details show **Answered by** and the switches, the Run Timeline has a line for each switch, the [Workflow runs](../studio/runs.md#every-workflow-s-runs) list marks the run **switched provider**, and the phone's run view says which provider answered and why the step switched.

::: warning A switch mid-session works like retry-count
A fallback provider starts the step again from its prompt. It doesn't continue the failed provider's session, and whatever that session already did stays done: files it edited, commands it ran. That is the same as a `retry-count` attempt, so give a fallback list only to steps that are safe to retry.
:::

## Inputs and output

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output is a string, or the value that passed the declared output schema. Select fields downstream only when the schema declares them.

## Preflight and limits

Preflight checks that the provider, and each [fallback provider](#fallback-providers), is installed and signed in where needed, the working directory exists, and the prompt and profile resources are valid. With a local model server profile, it also checks that the server answers and lists the node's model. It can't promise that your account has allowance left or that the provider will finish the task.

A workflow's `max-tokens` and `max-cost-usd` also apply inside an agent node's session: Claude Code starts with a spending cap from the run's remaining budget, and some providers are stopped mid-session at a token limit. See [What a limit covers inside an agent node](../workflows/rattish.md#what-a-limit-covers-inside-an-agent-node).

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
