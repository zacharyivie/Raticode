---
title: Providers, models and effort
description: Choose coding providers and model settings while preserving Rem conversations.
pageClass: docs-layout
---

# Providers, models and effort

Choose the coding tool for a task without moving the conversation into another app.

## Provider, model, and effort

| Selection | Meaning |
| --- | --- |
| Provider | The installed coding CLI or transport Raticode invokes |
| Model | The model requested through that provider |
| Effort | A provider-supported reasoning setting for that model |
| Permissions | The provider's allowed actions and approval behavior |

The model and effort catalogs come from provider discovery. They can differ by CLI version, account, and access policy. An unavailable saved choice remains selected so you can diagnose it; Raticode does not silently substitute another provider.

## Choose a model

Open the model picker in Rem. Select a model the provider exposes. Providers advertising custom model support also offer a custom model ID field. Enter the ID and commit it on blur or Enter; check spelling against that provider's catalog.

Some models have no effort selector. A CLI-default model delegates selection to the coding tool. That choice is not evidence of a particular underlying model or reasoning level.

## Save defaults

**Settings > Providers** manages enabled providers, executable paths, allowed or denied models, and provider-specific defaults. **Settings > Rem** chooses the default provider for new conversations. Existing explicit thread selections remain their own settings.

Refresh discovery after signing in, changing a binary, or receiving a new model entitlement. Repeated refreshes are not a substitute for authenticating the CLI itself.

## Conversation continuity

Provider and model changes preserve the Raticode thread. Same-provider model or effort changes keep that provider's native session. Each provider stores its own session for the thread, so returning to a previous provider restores it with the intervening visible conversation.

For a large handoff, Raticode can summarize imported history for the destination. The complete Rem history remains saved; it is not replaced by that handoff summary. If a native session is unavailable, Rem reports the error instead of silently replaying everything in a fresh session.

## Workflow providers

Agent nodes select their provider and model in `workflow.rattish`, not from the currently visible Rem picker. Workflow API profiles and coding CLI subscriptions are separate configuration paths. Check [Agent](../nodes/agent.md) and [Provider setup](../setup.md) before assuming an API profile works in Rem chat.

## Usage and billing

Authentication, subscriptions, rate limits, and billing belong to the provider. Keeping a thread or restoring a native session does not make prior context free. Use [Usage and allowances](./usage.md) to separate observed Raticode activity from provider account totals.

