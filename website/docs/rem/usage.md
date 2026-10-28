---
title: Usage and allowances
description: Compare provider account allowances with the token activity Raticode records.
pageClass: docs-layout
---

# Usage and allowances

Check what the provider says remains in your account, and separately inspect the activity recorded by Raticode.

## Open Usage

Go to **Settings > Usage**. The dashboard has two views:

| View | What it measures |
| --- | --- |
| Remaining allowance | Provider account quota, credits, or reset windows when available |
| Tokens in Raticode | Recorded invocation activity across projects on this device |

Use **Refresh usage** to request a new observation. A timestamp shows when account data was checked. A failed refresh can leave the last loaded result visible, so read the status and freshness together.

## Account allowances

Filter accounts to coding apps, API profiles, or all accounts. Each provider keeps its own units and reset periods. A subscription percentage does not reveal how many tokens remain, and two accounts with similar percentages may have different limits.

Some providers cannot expose a usable allowance. Missing, stale, or unauthenticated data is not a zero balance. Follow the account link or fix authentication when the dashboard cannot obtain a current observation.

## Raticode activity

Select the last 24 hours, 7 days, 30 days, or 90 days in the token view. Recorded usage comes from the provider's reported values or supported estimates. Tracking starts when records become available; it does not reconstruct all work performed before then.

Provider totals can include activity outside Raticode. Raticode's local activity can omit unreported cost or usage. These views answer different questions and need not match.

## Interpret costs carefully

Unknown cost remains unknown. Native conversation restoration avoids unnecessary transcript replay but does not promise zero-cost context. Provider subscriptions, caching, and API pricing determine billing.

Organization budgets and swarm progress have additional rules. Read [Organizations](../advanced/organizations.md) before treating a reported-dollar budget as a guarantee that no in-flight work can exceed it.

