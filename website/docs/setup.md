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

1. Open **Settings > Providers**.
2. Turn on your provider.
3. Check the executable path. If the desktop app's PATH differs from your terminal's, select the binary yourself.
4. Refresh discovery after you install or sign in.
5. Optionally pick a default model and effort from the list it returns.

::: warning Keep the provider's own config folder
Don't move a provider's configuration into a new XDG folder just to isolate Raticode. Its sign-in may depend on that folder. Giving Raticode its own data folder is a separate change from moving a provider's login.
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

Next: [Providers, models and effort](./rem/providers.md) and [Agent nodes](./nodes/agent.md).
