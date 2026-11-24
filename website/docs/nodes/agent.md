---
title: agent
description: Ask a coding provider to do a task, with an explicit prompt, model, effort and resources.
pageClass: docs-layout
---

# agent

Ask a coding provider to do a task, with an explicit prompt, model, effort and resources.

## When to use it

Use it for open-ended analysis or implementation. The provider can use its own tools within its own permissions, so review the task and the deployment profile before you run it. On Claude Code and Codex the step's shell runs in an [operating-system sandbox](#sandbox-and-network).

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
| `profile` | string or null | Not set | Optional compatible deployment profile, or one of the provider's [accounts](../rem/providers.md#several-accounts). Preflight checks that an account is [signed in](#accounts-that-need-sign-in). |
| `model` | string | Provider default | Model ID supported by the selected provider. |
| `effort` | string | Provider default | Effort choice supported by that model. |
| `working-dir` | string | `"."` | Working directory resolved from the workflow project. |
| `prompt` | string | `""` | Inline task prompt. |
| `prompt-path` | string or null | Not set | Readable prompt source file. |
| `skill` | string or null | Not set | Selected skill reference. |
| `memory` | `none`, `run`, `all` | `"none"` | Provider memory scope. |
| `tools` | list of string | `[]` | Selected provider tools. |
| `mcp-servers` | list of string | `[]` | Names of MCP servers the provider already has set up. On Claude Code each name allows that server's tools (`--allowedTools mcp__<name>`). |
| `env` | object | `{}` | Additional string environment values. |
| `extra-paths` | list of string | `[]` | Additional authored resource paths. |
| `output-schema` | object or null | Not set | Inline successful-output JSON Schema. |
| `output-schema-path` | string or null | Not set | File containing the successful-output schema. |
| `repair-attempts` | integer (min 0, max 3) | `0` | Bounded structured-output repair attempts. |
| `fallback` | list of up to 4 entries | `[]` | Providers to try next when this one can't serve the step. See [Fallback providers](#fallback-providers). |
| `network` | list of hosts, or `any` | No hosts | Hosts the step's shell may reach. See [Sandbox and network](#sandbox-and-network). |
| `sandbox` | `on` or `off` | `on` | `off` runs the step without its operating-system sandbox. |

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
      provider: anthropic-api
```

- **Each entry** takes `provider` (required), `profile`, `model` and `effort`. Left-out `model` and `effort` come from that entry's own provider contract, not the step's. The compiler rejects a provider with no contract and fields other than these four, and warns when an entry repeats a choice the step already tries.
- **When it switches.** The step first uses its provider with its full `retry-count`. Only when the last of those attempts fails with a provider availability error does it move to the next entry, which gets the same `retry-count`, and so on down the list. The availability errors are:
  - a rate limit, or a used-up plan allowance or quota;
  - an overloaded or temporarily unavailable service;
  - an expired sign-in or rejected credentials;
  - a provider CLI that isn't installed. Retrying can't install it, so the step skips its retries and switches straight away.
- **Never another account at a usage limit.** Once a sign-in reaches its rate limit or allowance in a step, the step doesn't move to another account of that provider, or to its default sign-in. It passes over those entries, and the run record lists each one as skipped: "skipped work-account: Raticode doesn't move work to another account of the same provider at a usage limit". Another provider, an API-key profile, and the same account with another model are still used. Another account is still used for the other three errors: an expired sign-in, an overloaded service or a CLI that isn't installed.
- **When it never switches.** A failed task, structured output that is still invalid after `repair-attempts`, a timeout, and a run stopped by its `max-tokens` or `max-cost-usd` limit end the step as they would without a list.
- **Limits cover every attempt.** The run's `max-tokens` and `max-cost-usd` count the calls of every provider the step tried. A fallback never gets a fresh budget.
- **Preflight** checks every entry's profile and credentials. The step can run when any of its providers is installed: a missing one is a warning (`RATTISH_PREFLIGHT_FALLBACK_PROVIDER_UNAVAILABLE`), and the step skips it at run time. Only when none is installed does preflight stop the run. An entry that is another account of a provider an earlier entry uses is a warning too (`RATTISH_PREFLIGHT_FALLBACK_SAME_PROVIDER_ACCOUNT`): it will only be used for sign-in or outage errors. So is an account that [needs sign-in](#accounts-that-need-sign-in) while the step has another entry to use.
- **Which provider answered.** The run record names the provider, profile, model and effort of each node's last attempt, and lists each switch with its reason and the error that caused it, and each account it skipped. The Studio's node details show **Answered by** and the switches, the Run Timeline has a line for each switch, the [Workflow runs](../studio/runs.md#every-workflow-s-runs) list marks the run **switched provider**, and the phone's run view says which provider answered and why the step switched.

::: warning A switch mid-session works like retry-count
A fallback provider starts the step again from its prompt. It doesn't continue the failed provider's session, and whatever that session already did stays done: files it edited, commands it ran. That is the same as a `retry-count` attempt, so give a fallback list only to steps that are safe to retry.
:::

## Sandbox and network

::: info New in 0.6.0
The agent step sandbox, `network` and `sandbox` are new in Raticode 0.6.0.
:::

Agent steps often run with nobody watching, from a [trigger](../workflows/triggers.md) at night, so their shell runs in an operating-system sandbox by default:

| Provider | What the sandbox does |
| --- | --- |
| `claude-code` | Claude Code's own sandbox, with the settings Rem uses for unattended work. Shell commands can write only to `working-dir`, the `extra-paths` and a scratch folder for the run, which is removed when the run ends. Credential folders such as `~/.ssh`, `~/.aws`, `~/.config/gh` and Claude Code's own sign-in are hidden from the shell and denied to the Read and Edit tools. The shell can't see Raticode's data folder either. The shell reaches only the hosts in `network`. The project's own Claude Code settings aren't loaded, since their hooks would run outside the sandbox; its `CLAUDE.md` files are passed to the step as instructions instead. |
| `codex` | Codex's own sandbox with the reach of `workspace-write`: writes stay in the working folder and `extra-paths`, and the shell has no network unless the step says `network: any`. From 0.6.0 the same home credential folders as Claude Code steps and Raticode's [credential folders](../advanced/security.md#credential-folders-no-agent-can-reach) are denied to it. A run started by a trigger refuses a profile that gives Codex full access (`danger-full-access` or the bypass flag). |
| `cursor`, `copilot`, `opencode`, `antigravity`, `grok` | No sandbox Raticode can turn on. The step runs unsandboxed. From 0.6.0, on Linux with bubblewrap, Raticode's credential folders are hidden from it. |
| `anthropic-api`, `openai-api` | No shell or file tools, so there is nothing to sandbox. |

On native Windows, Raticode counts every agent step as unsandboxed: Claude Code has no sandbox there, and Codex still starts in `workspace-write`, but Raticode doesn't rely on its Windows sandbox. Run Raticode in WSL2 for sandboxed agent steps on Windows.

In a triggered run, a step's shell also can't swap out a script a later step runs: the run checks each approved file right before using it, and runs shell scripts from a checked copy under Raticode's data folder. See [Checked again during the run](../workflows/triggers.md#checked-again-during-the-run).

The sandbox covers what the shell can do. It doesn't decide whether the shell may run: the step's `tools` and the profile's permission mode still do that, as before. In a repository worktree, commits also need the repository's shared `.git` folder, so the shell can write that too. In a run with [`workspace: worktree`](../workflows/rattish.md#run-in-a-worktree), it can write only the parts the run's own branch needs: new objects, the run's worktree folder and the `raticode/run/` branches. Commits on the run's branch work; every other branch and tag stays read-only.

**Network.** A step's shell reaches no hosts unless you list them:

```yaml
Node update-deps:
  type: agent
  provider: claude-code
  prompt: Update the npm dependencies and run the tests.
  tools:
    - Bash(npm install:*)
    - Bash(npm test:*)
  network:
    - registry.npmjs.org
    - "*.github.com"
```

- Write a host name, or `*.` and a domain for every host under it (quote that form). `*.github.com` doesn't include `github.com` itself. `localhost` is accepted too. Web addresses, ports and paths aren't accepted.
- `network: any` lets the shell reach every host. On Claude Code it does this with a `WebFetch(domain:*)` permission rule, so the WebFetch tool can also fetch any page without asking.
- Codex can only turn its shell's network on or off. Use `network: any` with Codex; a list of hosts fails preflight.
- `WebFetch(domain:…)` allow rules in your own Claude Code settings add to the hosts a sandboxed step can reach.

**Preflight finds network needs before the run.** When a sandboxed step's own settings show it needs hosts that `network` doesn't list, preflight stops the run with the exact `network:` lines to add. Two signs count:

- a tool rule that allows a command that uses the network, such as `Bash(curl:*)`, `Bash(git push:*)` or `Bash(npm install:*)`, while `network` lists no hosts;
- a web address in the prompt or prompt file, when the step can't use the WebFetch tool.

Preflight can't see every command an agent might decide to run. A command that tries a host you didn't list fails inside the run, and the agent's output says so.

**Turning it off.** `sandbox: off` runs the step without Raticode's sandbox settings. Claude Code then runs with no sandbox, as agent steps did up to 0.5.5, and a triggered run lets a Codex profile use full access. From 0.6.0, on Linux with bubblewrap, such a step still can't see Raticode's [credential folders](../advanced/security.md#credential-folders-no-agent-can-reach). Codex keeps whatever mode its profile sets, `workspace-write` by default. The Studio shows the field as a warning (`RATTISH_AGENT_SANDBOX_OFF`), and preflight lists the step under **Runs unsandboxed**:

```yaml
Node release:
  type: agent
  provider: claude-code
  prompt: Sign and upload the release with the tools in ~/release-kit.
  sandbox: off
```

**Preflight checks.**

| Code | Severity | When |
| --- | --- | --- |
| `RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE` | error | Claude Code's sandbox can't start, for example when Linux has no `bwrap` or `socat`, or the profile passes its own `--settings`. |
| `RATTISH_PREFLIGHT_NETWORK_UNDECLARED` | error | The step needs hosts that `network` doesn't list. The message says which lines to add. |
| `RATTISH_PREFLIGHT_NETWORK_UNSUPPORTED` | error | A Codex step lists hosts. Write `network: any` instead. |
| `RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE` (Codex) | error | From 0.6.0. The Codex profile passes `-c sandbox_mode`, `default_permissions` or `permissions.*`, which would replace the step's sandbox. |
| `RATTISH_PREFLIGHT_SANDBOX_REQUIRED` | error | A triggered run would give Codex full access. Change the profile or add `sandbox: off`. |
| `RATTISH_PREFLIGHT_RUNS_UNSANDBOXED` | warning | The step, or one of its [fallback providers](#fallback-providers), runs unsandboxed. The message starts with **Runs unsandboxed** and names each provider and why. |
| `RATTISH_PREFLIGHT_SANDBOX_WIDENED` | warning | From 0.6.0. The step's own settings let its shell reach beyond the project: an `extra-paths` entry outside it, which the shell may write, or `network: any`. The message starts with **Widens its sandbox**. |
| `RATTISH_PREFLIGHT_CREDENTIAL_PATH` | error | From 0.6.0. An `extra-paths` entry is, holds or sits in a credential folder: Raticode's own or one in your home folder such as `~/.ssh`. Any provider, sandbox on or off. |

The workflow's [Triggers](../workflows/triggers.md#unsandboxed-steps) section shows the same **Runs unsandboxed** and **Widens its sandbox** lists, and adding a trigger asks you to confirm while either isn't empty. See [Permissions and local data](../advanced/security.md#sandbox-for-workflow-agent-steps) for how this fits with Raticode's other permission layers.

## Inputs and output

You can bind other locals too. [Inputs and bindings](../workflows/inputs.md) covers delivery and types.

Output is a string, or the value that passed the declared output schema. Select fields downstream only when the schema declares them.

## Preflight and limits

Preflight checks that the provider, and each [fallback provider](#fallback-providers), is installed and signed in where needed, the working directory exists, the prompt and profile resources are valid, and the step's [sandbox](#sandbox-and-network) can start with the hosts it needs. With a local model server profile, it also checks that the server answers and lists the node's model. It can't promise that your account has allowance left or that the provider will finish the task.

### Accounts that need sign-in

New in 0.6.0.

When the step's `profile`, or a fallback entry's, is one of the provider's [accounts](../rem/providers.md#several-accounts), preflight checks that the account is signed in:

| Code | Severity | When |
|---|---|---|
| `RATTISH_PREFLIGHT_ACCOUNT_NEEDS_SIGN_IN` | error | The account is the step's only choice, or every entry in the step's fallback list is an account that needs sign-in. The run doesn't start. |
| `RATTISH_PREFLIGHT_ACCOUNT_NEEDS_SIGN_IN` | warning | The step has another entry to use. For a fallback entry, the message starts with **Fallback entry 1 (claude-code)**, numbered as in the list. The run goes ahead. |

- **Where the state comes from.** Preflight uses the account's last sign-in check when it's under two minutes old, the same check **Settings > Providers** shows. Otherwise it runs the provider's own status check, such as `claude auth status`, and waits up to 5 seconds for it.
- **An unknown state passes.** An account whose check doesn't answer in time, or whose CLI isn't installed, isn't flagged. A slow check keeps running, so the next preflight uses its answer. If the sign-in really has expired, the run reports it, and the step moves on to its next fallback entry.
- **Only accounts.** API-key profiles, other profiles and the provider's default sign-in aren't checked here.
- **Signing in.** Choose **Sign in** next to the account in **Settings > Providers**, or run `Raticode provider account login <name>`, then check the workflow again. See [When an account needs sign-in](../rem/providers.md#when-an-account-needs-sign-in).

In the Studio, the step's **Profile** menu and each **Fallback entry** profile menu list an account with its provider and sign-in state, such as `work · Claude Code account · Needs sign-in`. The state is **Signed in**, **Needs sign-in**, **CLI not found** or **Not checked**, as in **Settings > Providers**. API-key and other profiles show their name and model as before.

### Retired and retiring models

::: info New in 0.6.0
Model retirement checks are new in Raticode 0.6.0.
:::

With `anthropic-api` or `openai-api`, preflight also checks the step's model, and each fallback entry's, against what the provider has announced:

| Code | Severity | When |
|---|---|---|
| `RATTISH_PREFLIGHT_MODEL_RETIRED` | error | The provider has retired the model, so a call to it fails. The message gives the retirement date and the model to use instead. |
| `RATTISH_PREFLIGHT_MODEL_UNAVAILABLE` | error | The provider's model list for your API key doesn't have the model, for example a typo or a model your account can't use. The message names the closest model it does list. |
| `RATTISH_PREFLIGHT_MODEL_DEPRECATED` | warning | The model still runs but has a retirement date. The message gives the date and the replacement. The run goes ahead. |

- **Where the dates come from.** Raticode ships a list of each provider's announced retirements, so retired and retiring models are caught without a network connection. The workflow's [Triggers](../workflows/triggers.md#retiring-models) section shows the same warnings.
- **The provider's model list.** Preflight asks the provider for its models (`GET /v1/models`) with the profile's API key and keeps the answer for a day in Raticode's data folder, saved under a hash of the key rather than the key itself. When the list can't be fetched, because you're offline, the key is refused or the service is down, that part of the check is skipped and the run reports any real problem itself.
- **Other hosts.** A profile whose API address points somewhere other than `api.anthropic.com` or `api.openai.com`, such as a proxy, skips these checks: model names there may differ.
- When you leave `model` out, `anthropic-api` uses `claude-sonnet-5-5`. Up to 0.5.5 it used `claude-3-5-sonnet-latest`, which Anthropic retired on October 28, 2025.

A workflow's `max-tokens` and `max-cost-usd` also apply inside an agent node's session: Claude Code starts with a spending cap from the run's remaining budget, and some providers are stopped mid-session at a token limit. See [What a limit covers inside an agent node](../workflows/rattish.md#what-a-limit-covers-inside-an-agent-node).

Check the example with `Raticode rattish check` and `Raticode rattish preflight` before you run it. `Raticode rattish docs --format json` prints the installed contract.

Continue with [Node reference](./index.md), [Routes and joins](../workflows/routes.md), or [Runs and approvals](../studio/runs.md).
