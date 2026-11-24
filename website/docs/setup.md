---
title: Provider setup
description: Connect local coding CLIs and configure Raticode's provider and model defaults.
pageClass: docs-layout
---

# Provider setup

Raticode runs the coding tools you already have installed, signed in with your own accounts. Get one provider working before adding more.

## Install and authenticate the coding CLI

Install the provider's command-line tool and sign in, following its own instructions. Check that it works in a terminal before you look at Raticode's settings.

| Provider in Rem | Executable | Setup reference |
| --- | --- | --- |
| Codex | `codex` | [Codex CLI](https://developers.openai.com/codex/cli) |
| Claude Code | `claude` | [Claude Code setup](https://code.claude.com/docs/en/setup) |
| Cursor | `cursor-agent` | [Cursor CLI](https://cursor.com/docs/cli/overview) |
| GitHub Copilot | `copilot` | [Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli) |
| OpenCode | `opencode` | [OpenCode](https://opencode.ai/docs/) |
| Grok Build | `grok` | Your Grok Build CLI distribution |
| Google Antigravity | `agy` | [Antigravity CLI](https://antigravity.google/docs/cli) |

Your provider's version, account and policies decide which models you get. A tool can be installed and still fail to list models because it isn't signed in or access is denied. A working binary doesn't prove you have a usable subscription.

## Discover providers in Raticode

1. Open **Settings > Providers**. Each app Raticode found has a row with its status, such as **Ready** or **Needs sign-in**.
2. Turn on your provider's **Enabled** switch. Choose **Sign in** on its row if it needs sign-in.
3. Open the provider's details with the arrow next to its name and check the executable path. If the desktop app's PATH differs from your terminal's, select the binary yourself with **Choose executable...**. An app Raticode didn't find is under **Not installed** at the end of the list.
4. Refresh discovery after you install or sign in.
5. Optionally pick a default model and effort in the details, from the list the provider returns.

::: warning Keep the provider's own config folder
Don't move a provider's configuration into a new XDG folder just to isolate Raticode. Its sign-in may depend on that folder. Giving Raticode its own data folder is a separate change from moving a provider's login.

To use more than one subscription of Claude Code, Codex or Grok, add [accounts](./rem/providers.md#several-accounts) instead. Raticode makes each account's folder for you.
:::

## Choose defaults for Rem

**Settings > Rem** sets what new threads get: the provider, the default scope and the resources copied in.

The composer sets the current thread's provider, model, effort and permissions. Changing the defaults doesn't change choices already made in an existing thread.

Try a read-only request first:

```text
Explain the files in this project. Do not modify anything or run a workflow.
```

Check the reply and its tool activity. If the provider can't start, Rem shows the error. It doesn't quietly switch to another model.

## Permissions

Pick a permission mode before you ask for edits or commands.

- The modes come from the provider's catalog.
- Some providers offer read-only or plan modes.
- Grok and Antigravity manage permissions in their own CLI, and their approval rules still apply.

Raticode's resource toggles and the provider's permission mode control different things. Read [Permissions and local data](./advanced/security.md) before you allow broader access.

## API profiles for workflow agents

Workflow agent nodes can also call the OpenAI or Anthropic API directly through a profile.

- A profile is separate from a coding CLI subscription, and it isn't a Rem chat provider.
- Create one with `Raticode provider profile create`. `Raticode provider profile --help` lists the options.
- Keep API keys in environment variables or supported secret references.
- The Rattish source picks the provider, model and effort. A profile supplies deployment settings such as environment values and secret references, and doesn't override the model choices in the source.

## Local model servers

New in 0.5.5. Workflow agent nodes can use a model server you run yourself, such as [Ollama](https://ollama.com/), llama.cpp's `llama-server` or LM Studio, through its OpenAI-compatible API. Raticode doesn't bundle or install a model server and doesn't download models: install and start the server, and load or pull a model in it, first.

Add the server in **Settings > Providers** under **Local model servers** (from 0.6.0, open it under **For workflows only**): give the profile a name, enter the server's address and choose **Add server**. **Check server** lists the models it has. Or use the command line:

```bash
Raticode provider profile create ollama --local --api-base-url http://127.0.0.1:11434/v1
Raticode provider profile check ollama
```

`--api-base-url` defaults to Ollama's `http://127.0.0.1:11434/v1`. llama.cpp's `llama-server` usually listens on `http://127.0.0.1:8080/v1` and LM Studio on `http://127.0.0.1:1234/v1`. The address ends in the API's `/v1` path.

- **On this computer** (`localhost` or a `127.x.x.x` or `::1` address), the server needs no API key, and Raticode never sends it your OpenAI key.
- **On another computer**, Raticode still asks for a key and shows a warning: prompts, any file text in them and the model's replies travel over the network. In Settings the key goes into the system keychain as a [workflow secret](./workflows/inputs.md#secrets); on the command line, save it with `Raticode rattish secrets set NAME` and pass `--api-key-secret NAME`, or name an environment variable with `--api-key-env`. Use HTTPS or a network you trust.
- Raticode's network rules normally block local and private addresses. A local model server profile lets Raticode reach the one host it names, and nothing else.

In a workflow, an [agent node](./nodes/agent.md) uses the `openai-api` provider, the profile, and a model ID exactly as the server lists it:

```rattish
Rattish: 1

Workflow:
  name: Local summary
  max-cost-usd: 0.01

Node summarize:
  type: agent
  provider: openai-api
  profile: ollama
  model: qwen3:8b
  prompt: Summarize in one sentence why local models keep data on this computer.
```

Always set `model`. Without it the node uses the `openai-api` default, `gpt-5-mini`, which a local server doesn't have.

What to expect:

- **Preflight** asks the server for its model list (`GET /v1/models`) before the run. It fails if the server doesn't answer or doesn't list the model, and names the models it does list. With Ollama, `llama3.2` and `llama3.2:latest` are the same model, but `qwen3` isn't `qwen3:8b`.
- **The call** goes to the server's chat completions API (`POST /v1/chat/completions`). The model answers the prompt only: like other direct API profiles it has no tools, so it can't read files or run commands. A call may take up to 10 minutes before it times out, since a model on a CPU can be slow to load. A node's `timeout` can only shorten that; to allow longer, raise the profile's own timeout.
- **Cost** is recorded as a known $0, so a workflow's `max-cost-usd` keeps working and never ends the run for a local call.
- **Tokens** come from the server's response when it reports them. Ollama does: tested with Ollama 0.34.2 and `qwen3:8b`, preflight accepted `qwen3:8b` as listed and each call reported its input and output tokens, so `max-tokens` works. A thinking model's reasoning counts as output tokens.
- **If a server doesn't report tokens**, the run's token total reads as unknown, and a workflow with `max-tokens` ends after that server's first call with a "didn't report how many tokens" `budget` error (`RATTISH_TOKEN_LIMIT_UNCHECKABLE`). Use `max-cost-usd` alone, or leave `max-tokens` out, for such a server. See [Workflow limits](./workflows/rattish.md#workflow-limits).

## Jev key for classifier nodes

The [jev node](./nodes/jev.md) asks TypeSafe's Jev classifier yes/no and multiple-choice questions (from 0.5.5). It needs its own TypeSafe API key:

1. Open **Settings > Providers** and find **Classification models**. From 0.6.0 it's under **For workflows only**; choose it to open the form.
2. Paste the key under **Jev (TypeSafe)**, choose **Save**, then **Check key**.

Or run `Raticode provider jev set-key`, which asks for the key without showing it. The key goes into your system keychain, never an environment variable. See [Jev classifier](./workflows/jev.md).

Next: [Providers, models and effort](./rem/providers.md) and [Agent nodes](./nodes/agent.md).
