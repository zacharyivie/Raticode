---
title: Providers, models and effort
description: Choose coding providers and model settings while preserving Rem conversations.
pageClass: docs-layout
---

# Providers, models and effort

Raticode works with the Codex, Claude Code, Cursor, GitHub Copilot, OpenCode, Antigravity and Grok CLIs you have installed and signed in to. You can change the coding tool for a task without leaving the conversation.

| Choice | Meaning |
| --- | --- |
| Provider | The installed coding CLI or transport Raticode runs |
| Model | The model requested through that provider |
| Effort | A reasoning setting the provider supports for that model |
| Permissions | What the provider may do and when it asks for approval |

## Choose a model

Open the model picker in Rem and pick from the provider's catalog.

- Model and effort lists come from the provider itself. They can differ by CLI version, account and access policy.
- There's no free-text model field. If a model is missing, refresh providers or check the CLI's own access.
- Some models have no effort setting.
- A CLI-default model leaves the choice to the coding tool. It doesn't tell you which model or reasoning level actually runs.
- A saved choice that's no longer available stays selected so you can see the problem. Raticode never quietly swaps in another provider.

## Save defaults

| Where | What it sets |
| --- | --- |
| **Settings > Providers** | Enabled providers, executable paths, allowed or denied models, provider-specific defaults |
| **Settings > Rem** | Default provider, model and effort for new tasks and threads. Threads with their own choices keep them. |
| **Settings > Rem > Rem chat provider** | Who writes replies in Rem's home chat. Leave it on **Same as tasks and threads**, or pick a faster model or lower effort so chat answers quickly while tasks keep a stronger model. |

With Claude Code as the chat provider, Rem's home chat reply appears as it's written. Other providers show what Rem is doing, such as "Reading earlier messages", then the finished reply. Hover a reply (or open its ⋯ menu on the phone) to see which model wrote it and how long it took.

After signing in, changing a binary or getting access to a new model, choose **Refresh providers** in **Settings > Providers**. Refreshing doesn't sign the CLI in; do that in the CLI itself.

## Conversation continuity

Changing provider or model keeps the Raticode thread.

- Changing model or effort within one provider keeps that provider's native session.
- Each provider keeps its own session for the thread. Going back to a provider restores its session along with the conversation you had elsewhere.
- For a large handoff, Raticode can summarize earlier history for the new provider. The full history stays saved.
- If a native session is unavailable, Rem reports the error instead of quietly replaying everything into a fresh session.

## Workflow providers

[Agent](../nodes/agent.md) nodes set their provider and model in `workflow.rattish`, not from Rem's picker. Workflow API profiles and coding CLI subscriptions are configured separately, so an API profile doesn't automatically work in Rem chat. See [Provider setup](../setup.md).

## Usage and billing

Sign-in, subscriptions, rate limits and billing belong to the provider. Keeping a thread or restoring a session doesn't make earlier context free. [Usage and allowances](./usage.md) separates what Raticode recorded from your provider account totals.
