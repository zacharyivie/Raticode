# Developer and design notes

The [Raticode website](../website/docs/index.md) is the single source of truth for how Raticode works for its users, desktop and mobile. The files here are developer material: designs, data formats, API contracts, release procedures and security reviews. When a user-visible feature changes, update the website first; update these notes for implementation details. See `AGENTS.md` for the rule and `scripts/check-website-docs.py` for the check.

| File | What it covers | User guide on the website |
| --- | --- | --- |
| [rem.md](rem.md) | Rem threads, storage, provider sessions | [Chat and threads](../website/docs/rem/chat.md) |
| [rem-home.md](rem-home.md) | Rem's home: API, task model, review and recovery | [Rem's home and tasks](../website/docs/rem/home.md) |
| [rem-kanban.md](rem-kanban.md) | Global Kanban and self-work engine | [Global board and self-work](../website/docs/rem/board.md) |
| [rem-provider-sessions.md](rem-provider-sessions.md) | Native provider sessions | [Providers, models and effort](../website/docs/rem/providers.md) |
| [rem-steering-api.md](rem-steering-api.md), [rem-steering-ui.md](rem-steering-ui.md) | Steering a running reply | [Chat and threads](../website/docs/rem/chat.md#steer-a-running-response) |
| [browser.md](browser.md) | Integrated browser internals | [Integrated browser](../website/docs/studio/browser.md) |
| [second-brain-workspace.md](second-brain-workspace.md) | Second Brain internals | [Second Brain](../website/docs/knowledge/second-brain.md) |
| [mobile-desktop-parity.md](mobile-desktop-parity.md) | Desktop side of phone features | [Raticode Mobile](../website/docs/mobile/index.md) |
| [cli-provider-adapters.md](cli-provider-adapters.md) | Adding CLI providers | [Provider setup](../website/docs/setup.md) |
| [branding.md](branding.md) | Naming and brand rules | — |
| [releasing.md](releasing.md) | Desktop release procedure | [Installation](../website/docs/installation.md) |
| [security/](security/) | Device pairing, messaging, protocol v2, release verification | [Pair and connect](../website/docs/mobile/pairing.md), [Permissions and local data](../website/docs/advanced/security.md) |
| [delivery/](delivery/) | Provider research and delivery plans | — |
| [features/](features/) | Organizations design demo | [Organizations](../website/docs/advanced/organizations.md) |

Phone app developer notes are in [`mobile/android/docs/`](../mobile/android/docs/README.md).
