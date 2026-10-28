---
title: Provider setup
description: Connect local coding CLIs and configure Raticode's provider and model defaults.
pageClass: docs-layout
---

# Provider setup

Raticode uses your installed coding tools and their existing authentication. Choose one working provider before adding more.

## Install and authenticate the coding CLI

Use the provider's own instructions to install its command-line tool and sign in. Check that it works in a terminal before debugging Raticode discovery.

| Provider in Rem | Expected executable | Setup reference |
| --- | --- | --- |
| Codex | `codex` | [Codex CLI](https://developers.openai.com/codex/cli) |
| Claude Code | `claude` | [Claude Code setup](https://code.claude.com/docs/en/setup) |
| Cursor | `cursor-agent` | [Cursor CLI](https://cursor.com/docs/cli/overview) |
| GitHub Copilot | `copilot` | [Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli) |
| OpenCode | `opencode` | [OpenCode](https://opencode.ai/docs/) |
| Grok Build | `grok` | Your Grok Build CLI distribution |
| Google Antigravity | `agy` | [Antigravity CLI](https://antigravity.google/docs/cli) |

Provider versions, accounts, and policies determine which models are available. A provider may be installed while its model discovery is unauthenticated or denied. A working binary alone does not establish a usable subscription.

## Discover providers in Raticode

1. Open **Settings > Providers**.
2. Enable your chosen provider.
3. Check the executable path. Select an explicit binary if the desktop's PATH differs from your terminal's.
4. Refresh discovery after installation or sign-in.
5. Select default model and effort values from the returned catalog, if you want provider-specific defaults.

Do not move provider configuration into a new XDG directory just to isolate Raticode. Provider authentication may depend on that directory. An isolated Raticode data directory and a relocated provider login are different changes.

## Choose defaults for Rem

Open **Settings > Rem** to choose the provider used by new threads, the default scope, and the resources copied into them. The composer selects the current thread's provider, model, effort, and permissions. Changing defaults does not rewrite explicit choices in an existing thread.

Try a read-only request first:

```text
Explain the files in this project. Do not modify anything or run a workflow.
```

Check the response and tool activity. If the provider cannot start, Rem displays the error rather than silently selecting another model.

## Permissions

Choose a permission mode before requesting edits or commands. Available modes come from the provider catalog. Some providers expose read-only or plan modes; Grok and Antigravity use CLI-managed permissions. Their native approval rules still apply.

See [Permissions and local data](./advanced/security.md) before enabling broader access. Raticode resource toggles and provider permission modes control different parts of the invocation.

## API profiles for workflow agents

Workflow agent nodes can also use direct OpenAI or Anthropic API profiles. These profiles are distinct from a coding CLI subscription and are not ordinary Rem chat providers. Configure them using `gof provider create` and the options shown by `--help`; keep API credentials in environment variables or supported secret references.

Rattish chooses the provider, model, and effort in source. A profile supplies deployment configuration such as environment values and secret references. It does not override those compiled model choices.

Continue with [Providers, models and effort](./rem/providers.md) and [Agent nodes](./nodes/agent.md).

