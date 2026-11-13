---
title: Usage and allowances
description: Compare provider account allowances with the token activity Raticode records.
pageClass: docs-layout
---

# Usage and allowances

**Settings > Usage** has two views that answer different questions:

| View | Shows |
| --- | --- |
| Remaining allowance | What the provider says is left: quota, credits or reset windows, when available |
| Tokens in Raticode | Activity Raticode recorded across projects on this computer |

The two don't need to match. Provider totals include work done outside Raticode, and Raticode can't count usage a provider doesn't report.

## Refresh

**Refresh usage** asks providers again. A timestamp shows when account data was last checked. A failed refresh can leave the previous result on screen, so read the status and the time together.

Some providers report allowances only with a usage-reporting key. **Configure usage reporting** stores that key in the system's secret store.

## Account allowances

Filter by coding apps, API profiles or all accounts.

- Each provider uses its own units and reset periods.
- A subscription percentage doesn't tell you how many tokens are left, and two accounts at the same percentage can have different limits.
- Missing, stale or signed-out data isn't a zero balance. Follow the account link or fix sign-in when the dashboard can't get a current reading.

## Raticode activity

Pick the last 24 hours, 7, 30 or 90 days. Figures come from what providers report, or from supported estimates. Tracking starts when records exist; earlier work isn't rebuilt.

## Costs

- Unknown cost stays unknown.
- Restoring a provider's native session avoids replaying the transcript but doesn't make earlier context free.
- Subscriptions, caching and API pricing decide what you pay.

Organization budgets and swarm progress have their own rules. Read [Organizations](../advanced/organizations.md#budgets-and-destinations) before treating a dollar budget as a hard cap: work already running can go past it.
