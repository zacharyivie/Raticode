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

## Accounts at a glance

*New in Raticode 0.6.0.* The top of the **Remaining allowance** view has one card per provider account. **Tokens in Raticode** doesn't repeat them; it keeps the token chart, the cost estimate and the price list. Each card shows that account's own readings. Raticode never adds accounts into one percentage, because two accounts can have different limits.

| On a card | Shows |
| --- | --- |
| Name | The provider and the account: a [Claude Code, Codex or Grok account](./providers.md#several-accounts) by its name, otherwise **Default sign-in** or **Default account** |
| Sign-in state | For Claude Code, and for each Codex or Grok account: **Signed in**, **Needs sign-in**, **Claude Code not found** (or **Codex not found**, **Grok not found**) or **Sign-in not checked**. A Codex account reads it from Codex's `account/read`, a Grok account from whether Grok's `_x.ai/billing` answers. Other rows show their usage status. |
| Meters | Each Claude Code card has its **5-hour** and **Weekly** windows side by side, with the percentage left. A window with no reading yet still has its place and says **No reading yet**. Other windows the account reports, such as a weekly Opus limit, follow them. |
| Reset countdown | Under each meter, for example **resets in 2h 14m**, or **resets in 3d 4h** when it's days away. **reset time passed** means the reading is out of date; refresh it. |
| Last checked | When Raticode last read the account, such as **Checked 4 min ago** |

A card that needs sign-in keeps its **Sign in** button. For a Claude Code, Codex or Grok account it starts the same one-click sign-in as **Settings > Providers**, and the card updates when you've approved it in your browser. If the provider refuses, the card shows the provider's own message. Rem skips that account until then.

A Codex account's card shows the windows Codex reports for it, such as `codex · primary · 5-hour` and `codex · secondary · 7-day`. A window Codex reports as null isn't shown; it isn't counted as empty. A Grok account's card shows what Grok's `_x.ai/billing` reports for its folder: credits used this period, and on-demand spend or prepaid balance when the account has them.

Providers that aren't installed, and metered API rows that have no allowance to report, get no card; they stay in the list under **Remaining allowance**.

### Weekly pace

*New in Raticode 0.6.0.* Under its meters, a card with a weekly window charts the current week: the percentage left, from the window's start to its reset, as the provider reported it. A dashed line runs from full at the start to empty at the reset; that's an even pace. Above the dashed line, the account is using its allowance more slowly than an even pace; below it, faster.

Under the chart, one line says how the week is going:

| Line | Means |
| --- | --- |
| **On pace to last until reset** | At the rate the readings show, the allowance lasts past the reset. |
| **At this pace, runs out around Thu 14:00** | At that rate, the allowance runs out before the reset, at about that day and time on this computer's clock. |
| **Not enough readings yet** | This week has fewer than two readings, for example right after the window reset. |
| **Used up until reset** | The provider reports the window as full. |

The rate runs from the week's first reading to the newest one, and counts the time since the newest reading as long as the provider still reports the same percentage. The pace comes only from the provider's own readings. Raticode never estimates an allowance from token counts, because a provider's limit isn't a number of tokens Raticode can know.

For a Claude Code account the chart follows the **Weekly** window; for Codex, the weekly (7-day) limit. Readings from an earlier week never count toward this week's pace.

Self-work can follow a pace of its own from the same readings: with [Pace to reset](./board.md#pace-to-reset) it starts new work only while the account is under a line that reaches 97% a few hours before the reset. *New in Raticode 0.6.0.*

Raticode keeps each reading in its usage history on this computer, but only when it differs from the window's previous reading: a new percentage or a new reset time. Readings come from the background check every 10 minutes, from **Refresh usage**, and from Claude Code's reports during turns. Out of date readings, and a failed refresh that keeps an older reading on screen, add nothing.

### Rem uses next

When **Settings > Rem > Accounts > Claude Code account** (or **Codex account**, **Grok account**) is **Choose by usage**, the card for the account of that provider Rem would pick for its next thread or task has a **Rem uses next** badge, with Rem's reason, for example "Most room left: 12% of its weekly allowance used." It's the same choice Rem makes when work starts, described under [Several accounts](./providers.md#several-accounts). If no account is signed in with room left, the badge goes on the default sign-in card. With the default sign-in or one named account chosen, no card has the badge.

The badge shows what Rem would pick at the moment the page was read. A thread or task that has already started stays on its own account.

The phone's **Usage** page shows the same cards, with the same weekly chart and pace line. A Codex or Grok account that needs sign-in has **Sign in** on its card, which [signs it in from the phone](./providers.md#sign-an-account-in-from-the-phone). A Claude Code account or a default sign-in tells you to sign in on the computer, because that sign-in opens a browser there.

## Refresh

**Refresh usage** asks providers again. A timestamp shows when account data was last checked. A failed refresh can leave the previous result on screen, so read the status and the time together.

Some providers report allowances only with a usage-reporting key. **Configure usage reporting** stores that key in the system's secret store.

## Account allowances

Filter by coding apps, API profiles or all accounts.

- Each provider uses its own units and reset periods.
- A subscription percentage doesn't tell you how many tokens are left, and two accounts at the same percentage can have different limits.
- Missing, stale or signed-out data isn't a zero balance. Follow the account link or fix sign-in when the dashboard can't get a current reading.
- Each [account](./providers.md#several-accounts), such as a Claude Code, Codex or Grok account, has its own row with its own weekly and 5-hour allowance, and its own tokens and estimated cost under **Raticode activity**. An account that needs sign-in says so, and its card under [Accounts at a glance](#accounts-at-a-glance) has the **Sign in** button. Accounts report their limits only through the provider's own app (Claude Code's `/usage`, Codex's app-server `account/rateLimits/read` run with the account's `CODEX_HOME`, Grok's `_x.ai/billing` run with the account's `GROK_HOME`); Raticode reads no token for them.

## Limit alerts

::: info New in 0.6.0
Limit alerts are new in Raticode 0.6.0.
:::

Raticode can warn you before an account runs out. When a new allowance reading shows an account at or past an alert level of its **5-hour** or **weekly** window, you get one desktop notification that names the account, the window and when it resets. For example: "Claude Code (work) is at 82% of its 5-hour limit", with "It resets today at 3:40 PM" in the text.

- **Settings > Usage > Remaining allowance > Limit alerts** has the switch and the two levels. Alerts are on by default, at **80%** and **95%**. Levels are whole percentages from 1 to 100, and the second must be higher than the first.
- Each level alerts **once per window**. A reading that jumps past both levels at once sends a single alert for the higher one. The levels re-arm when the window resets, so the next 5-hour or weekly window can alert again.
- Raticode remembers which alerts it sent for each account and window, so restarting the app doesn't repeat them.
- Every account counts on its own, including each [Claude Code account](./providers.md#several-accounts) and each provider profile. An account that needs sign-in never alerts, and neither does an old reading or one past its reset time.
- Readings come from the background check every 10 minutes, from **Refresh usage**, and from Claude Code's own reports during turns. An alert can arrive a few minutes after the account crosses the level.
- Only 5-hour and weekly windows alert. Weekly windows for one model, such as Claude's weekly Opus allowance, count too. Monthly plans, credits and spend limits don't.
- **On the phone:** a paired [Raticode Mobile](../mobile/rem.md#alerts-and-notifications) phone gets the same alert under **Also alert me about › Workflow messages**. Unless **Show Rem's words** is on, it reads only "A workflow sent a message".
- When the computer has no desktop session, such as a [headless backend](../reference/headless.md), the desktop notification is skipped and the phone still gets the alert.
- From a script: [`Raticode usage alerts`](../reference/cli-headless.md#providers-models-and-usage).

## Raticode activity

::: info Changed in 0.6.0
The chart, the 6mo, 1Y, 5Y, All and Custom periods, and the saved period are new in Raticode 0.6.0. Raticode 0.5.5 offers the last 24 hours, 7, 30 or 90 days and shows only the table.
:::

Choose a period above the totals:

| Period | Covers |
| --- | --- |
| 24h, 7d, 30d | The last 24 hours, 7 days or 30 days |
| 3mo | The last 90 days (called "Last 90 days" before) |
| 6mo | The last 180 days |
| 1Y | The last 365 days |
| 5Y | The last 5 years |
| All | Everything since the first call recorded on this computer |
| Custom | From a start date to an end date you pick, both included |

The choice is saved on this computer and comes back the next time you open **Settings > Usage**. A period you had saved as 90 days opens as **3mo**. A custom range needs a start date on or before the end date, no earlier than 2000 and not in the future; **Show range** tells you what to fix.

Raticode keeps every recorded call, so longer periods show all the history this computer has. Nothing is deleted after 90 days.

Figures come from what providers report, or from supported estimates. Tracking starts when records exist; earlier work isn't rebuilt.

A task you [ran on a paired desktop](./home.md#run-a-task-on-a-paired-desktop) (experimental, new in 0.6.0) is counted on that desktop, under its own accounts. This computer keeps one row for the whole run, with the provider, the model and that desktop's computer ID, so the task's own usage includes it. It's left out of this activity, the cost estimate and every account here, so nothing is counted twice.

### Tokens over time

Under the total, a stacked bar chart shows reported tokens per time bucket. Each bar is one bucket, and each piece of a bar is one provider, sized by that provider's tokens in the bucket. The bucket size follows the period:

| Period length | One bar is |
| --- | --- |
| Up to 2 days | An hour |
| Up to 35 days | A day |
| Up to about 13 months | A week, starting Monday |
| Up to 6 years | A month |
| Longer | A year |

Buckets follow your computer's calendar and time zone. Weeks or days with no calls show as empty slots, so gaps stay visible.

- **Hover a bar** to see its date, total tokens, and each provider's tokens and share of that bar.
- **With the keyboard**, Tab to the chart and use the left and right arrow keys; a screen reader reads each bar.
- **The legend** names each provider with its total and share of the period. Each provider has its own color and pattern, so the chart doesn't rely on color alone.
- **Show as a table** lists every bucket with each provider's tokens.

#### By provider or by account

*New in Raticode 0.6.0.* A **By provider** / **By account** switch sits beside the chart's title. **By provider** stacks one piece per provider. **By account** splits each provider into its accounts, the same accounts the provider table below lists: each [provider account](./providers.md#several-accounts) and each provider profile is its own piece, and a provider's default sign-in is **Default sign-in** for Claude Code and **Default account** for the others.

- The legend, a bar's details, what a screen reader reads with the arrow keys, and **Show as a table** name each account with its provider, such as **Claude Code · work** or **Codex · Default account**.
- A provider's accounts sit next to each other in every bar, largest first.
- Each account is a shade of its provider's color (see below) with a pattern of its own, so two accounts of one provider stay apart without relying on color. The default sign-in keeps the provider's own color and pattern. Named accounts follow in alphabetical order, so an account keeps its shade and pattern whatever period you choose. After four accounts the shades repeat; patterns repeat only after ten.
- The choice is saved on this computer, like the period.

#### Provider colors

*New in Raticode 0.6.0.* Each provider is drawn in a color from its own branding, the same on the computer and the phone. Light themes use a deeper shade of the same color so every piece stays visible. Providers from one company differ by shade and pattern.

| Provider | Color | Pattern |
| --- | --- | --- |
| Claude Code | Anthropic's clay orange | Solid |
| Anthropic API | A lighter clay on dark themes, a deeper one on light themes | Diagonal lines, falling |
| Codex | OpenAI green | Diagonal lines, rising |
| OpenAI API and local model servers | Mint on dark themes, deep green on light themes | Checks |
| GitHub Copilot | GitHub purple | Horizontal lines |
| Cursor | Cream on dark themes, near-black on light themes, from Cursor's black and white logo | Dots |
| OpenCode | Light gray on dark themes, mid gray on light themes, from OpenCode's gray logo | Vertical lines |
| Grok | Mid gray on dark themes, dark gray on light themes, from xAI's black and white logo | Crossed lines |
| Antigravity | Antigravity's logo blue | Grid |
| Jev (TypeSafe) | TypeSafe's pink | Rings |
| Any other provider | Olive | Diagonal lines, rising |

Local model servers are recorded as the OpenAI API, so they share its color.

A provider that made calls but reported no tokens appears in the legend as **Tokens not reported** and adds nothing to the bars. Estimated usage is left out of the chart, as it is from the reported total. A bar's details count calls without token data.

### On the phone and in the CLI

The phone's **Usage** page has the same periods, including **Custom** with a date range picker, the same chart with its own **By provider** / **By account** chips (saved on the phone), and a tap-to-read panel with **Earlier** and **Later** buttons. See [Desktop settings, Usage and the Developer log](../mobile/workspace.md#desktop-settings-usage-and-the-developer-log).

`Raticode usage show` has an **ACCOUNT** column, so each account is its own line. It takes `--range` with `24h`, `7d`, `30d`, `3mo`, `6mo`, `1y`, `5y` or `all`, or `--from` and `--to` dates for a custom range. Its text output lists tokens per bucket and provider; add `--by account` to list them per account instead, such as `claude_code (work)`. JSON output always carries both: `series.providers` with `providers` in each bucket, and `series.accounts` with `accounts` in each bucket. `--days 90` still works and means 3mo. See [Providers, models and usage](../reference/cli-headless.md#providers-models-and-usage).

From 0.5.5, every [jev node](../nodes/jev.md) call adds a **Jev (TypeSafe)** row, including calls that failed. TypeSafe bills the input tokens shown; output tokens are free.

## Cost at API prices

*New in Raticode 0.6.0.* Under the provider table, **Cost at API prices** estimates what the period's tokens would have cost if you had paid each provider's public API list price instead of a subscription. It's an estimate, not a bill, and the page labels it that way.

- **Estimated API cost** is the period's total. Each provider row splits it into uncached input, cache reads, cache writes and output.
- Only work done in Raticode is counted. Use of the same subscriptions in other apps isn't on this page.
- **Price list** shows every model's price per million tokens, its source page and the date it was checked.

### How the estimate is worked out

- **Cache reads, cache writes and uncached input are priced separately** when the provider reported them. Cached input costs much less than uncached input; with Claude Code, most of the input in a long session is cache reads.
- **Codex and OpenAI count cached input inside their input total.** Raticode subtracts the cached reads and cache writes from it, so only the rest is priced as uncached input. A Codex call that reused little of the cache shows most of its input as uncached, and that's what the provider reported, not a counting error. For example, a Codex call that sends a long new prompt each time, rather than continuing one session, often reuses only Codex's own fixed instructions.
- **A call without a cache breakdown** is priced as if all its input were uncached, which overstates its cost when it used the cache. The page says how many calls that applies to. Claude Code turns stopped before this release are the exception: they saved only the uncached part of their input, so their cost is understated, and they count among the calls that reported only part of their tokens.
- **Cache writes** cost more than uncached input, by an amount that depends on how long the entry is kept. Claude Code reports which writes are kept for one hour; Raticode records that from this release. Older Claude Code writes are priced at the one-hour rate, which is what Claude Code writes on subscription accounts. Other providers' writes use their short-lifetime rate.
- **Rem's one-shot calls write the five-minute cache.** *New in Raticode 0.6.0.* Rem's chat replies, answers to workers, decisions, summaries and Second Brain reports each send a prompt once and never again, so with Claude Code they ask for the five-minute cache. Those writes cost 1.25 times the input price instead of 2 times, and show with no one-hour part. Worker and reviewer sessions keep Claude Code's own choice (one hour on a subscription), because they reread their prompt on every request.
- **Which model ran.** When Raticode lets a provider's app choose its default model (**cli-default**), it records the model the app reports instead, from this release. Claude Code names it; Codex doesn't, so a Codex call on its default model stays unpriced. Calls recorded before this release keep **cli-default** or **model not recorded**.
- **Unknown stays unknown, never $0.** A model with no list price, such as a provider's default model when Raticode didn't record which one ran, or GitHub Copilot and Cursor's Auto, which aren't sold per token here, shows **Unknown**. A total that leaves some calls out reads **at least** that amount.
- **Left out:** calls with no token data, estimated calls, long-context surcharges (they apply per request, and Raticode records one sum per turn), Google's hourly cache storage and tool fees such as web search.

### Which providers report cache use

| Provider | Reports | Before this release |
| --- | --- | --- |
| Claude Code, Anthropic API | Cache reads, cache writes, and how many writes are kept for one hour | Reads and writes were recorded, but not the one-hour part. Stopped Claude Code turns recorded uncached input only, so they're priced as uncached. |
| Codex, OpenAI API | Cached input (reads) and cache writes, both counted inside the input total. The OpenAI API also says which writes are kept for 12 hours. | Reads were recorded. Cache writes from Codex's app server and the OpenAI API's write fields weren't read, so writes were priced as uncached input. No Codex call on this kind of account has reported a cache write so far. |
| Cursor, OpenCode | Cache reads and writes | Recorded |
| Antigravity | Cache reads | Recorded |
| GitHub Copilot, Grok | Whatever their CLI reports; not checked with real calls | No priced calls recorded |
| Jev (TypeSafe) | Input and output only; there's no cache | Recorded |

### API list prices

US dollars per million tokens, standard tier, checked 2026-11-18. Raticode keeps them in one table, `src/gofer/core/api_prices.py`, with a source and date on each row.

| Model | Input | Cache read | Cache write (short / long) | Output | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| claude-opus-5-5 (`opus`) | 4 | 0.20 | 5 / 8 | 20 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-5-5 (`sonnet`) | 2 | 0.10 | 2.50 / 4 | 10 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-haiku-5-5 (`haiku`) | 0.10 | 0.01 | 0.125 / 0.20 | 0.50 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-haiku-4-5 | 1 | 0.10 | 1.25 / 2 | 5 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-5 | 5 | 0.50 | 6.25 / 10 | 25 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-opus-4-8, 4-7, 4-6, 4-5 | 5 | 0.50 | 6.25 / 10 | 25 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-5 | 2 | 0.20 | 2.50 / 4 | 10 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-sonnet-4-6, 4-5 | 3 | 0.30 | 3.75 / 6 | 15 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-fable-5-2 | 8 | 0.20 | 10 / 16 | 40 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-fable-5-1 | 10 | 0.25 | 12.50 / 20 | 50 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| claude-fable-5 | 10 | 1 | 12.50 / 20 | 50 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-6.2-astra | 8 | 0.40 | 10 / 20 | 40 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6.2-sol, gpt-6.1-sol | 2 | 0.10 | 2.50 / 5 | 10 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6.1-terra | 1 | 0.05 | 1.25 / 2.50 | 5 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6-astra | 10 | 1 | 12.50 / 25 | 50 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6-luna | 0.10 | 0.01 | 0.125 / 0.25 | 0.50 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-6-sol | 2 | 0.20 | 2.50 / 5 | 10 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-5.6-sol | 4 | 0.40 | 5 / 10 | 20 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-5.6-terra | 2 | 0.20 | 2.50 / 5 | 12 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-5.6-luna | 0.20 | 0.02 | 0.25 / 0.50 | 1.20 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-5.5 | 5 | 0.50 | as input | 30 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-5.4 | 2.50 | 0.25 | as input | 15 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gemini-3.8-flash | 0.75 | 0.075 | as input | 3.75 | [Gemini API pricing](https://aistudio.google.com/docs/pricing) |
| gemini-3.1-pro-preview | 2 | 0.20 | as input | 12 | [Gemini API pricing](https://aistudio.google.com/docs/pricing) |
| gemini-4-carbon | 2 | 0.10 | as input | 10 | [Gemini API pricing](https://aistudio.google.com/docs/pricing) |
| grok-build-0.1 (`grok-code-fast-1`) | 1 | 0.20 | as input | 2 | [xAI pricing](https://docs.x.ai/developers/pricing) |
| grok-4.7, grok-4.6 | 2 | 0.50 | as input | 6 | [xAI pricing](https://docs.x.ai/developers/pricing) |
| grok-4.5 | 2 | 0.30 | as input | 6 | [xAI pricing](https://docs.x.ai/developers/pricing) |
| grok-4.3 | 1.25 | 0.20 | as input | 2.50 | [xAI pricing](https://docs.x.ai/developers/pricing) |
| composer-2.5 (Cursor) | 0.50 | 0.20 | as input | 2.50 | [Cursor models and pricing](https://cursor.com/docs/models-and-pricing) |
| opencode/big-pickle | free | free | free | free | [OpenCode Zen](https://opencode.ai/docs/zen) |
| jev-1.14.0 | 0.042 | none | none | free | [TypeSafe models](https://docs.typesafe.ai/models) |

Short and long cache writes are 5 minutes and 1 hour for Anthropic, and 30 minutes and 12 hours for OpenAI. Google and xAI charge no write premium, and neither do OpenAI's GPT-5.5 and earlier. Model names are matched without a `[1m]` context marker, a date suffix or an effort suffix such as `-high`, so `claude-opus-5-5[1m]` uses the Opus price and `claude-haiku-4-5-20251001` the Haiku 4.5 price. Some prices change soon: gemini-4-carbon doubles on 2026-12-01 and gemini-3.8-flash on 2027-01-01. Prompts above a size limit cost more on some models (Haiku 5.5 above 100K tokens, OpenAI above 272K, Gemini Pro above 200K, Grok from 200K); the estimate uses the standard rate.

On the phone, the **Usage** page shows the same estimate. In the CLI, `Raticode usage show` includes it; see [Providers, models and usage](../reference/cli-headless.md#providers-models-and-usage).

## Verified work

*New in 0.6.0.* Under **Tokens in Raticode**, **Verified work** shows how well Rem's own work passed on its own in the period you picked. It counts Rem's [Kanban tickets](./board.md) that reached **Done** or **Blocked** in that period:

- **First-pass acceptance**, such as **31 of 40**: accepted tickets that passed review the first time, with no repair and without you stepping in. Landing a ticket's work yourself still counts as first pass; marking it done by hand doesn't.
- **Repairs per ticket**: how many times a review, or Raticode's [own run of the project's check](./home.md#raticode-runs-the-project-s-check-itself), sent an accepted ticket's work back before it passed.
- **Blocked**: tickets that ended blocked, out of all tickets that finished, and why: review checks failed, the project check kept failing, the work broke a project decision, it waited for your answer, it failed or was stopped. A ticket that only sleeps, waits for an event or is paused isn't counted. A failed task is listed by what its message says:
  - **No activity for 20 minutes**: the worker or reviewer showed no activity for 20 minutes, so Rem stopped its turn.
  - **Reached the 120-minute turn limit**: one turn ran for 2 hours, even while active.
  - **Usage limit waits used up**: the provider's usage limit was still reached after every wait for it to reset.
  - **Provider error**: the provider app's stream or process ended with an error, including temporary failures that kept failing after every retry.
  - **Couldn't start**: the provider app or the turn couldn't be launched, such as a CLI that isn't installed or a prompt over the launch limit.
  - **Other failure**: any other failure, and failures recorded before Raticode named these causes.

  *New in 0.6.0.*
- **Project check failed first try**: of the tickets with a recorded check run, how many failed Raticode's check on the first try.
- **Flaky check runs**: check runs that failed, then passed when Raticode [ran them again on the same commit](./home.md#raticode-runs-the-project-s-check-itself). They aren't counted as first-try failures or repairs. *Not in a released version yet.*
- **Tokens per accepted ticket**: the tokens all of an accepted ticket's runs used, from the same token history as the chart.
- **You stepped in**: your **Request changes** and the tickets you moved by hand.

A small chart below shows first-pass, repaired and blocked tickets per day, week or month. The numbers come from records Raticode already keeps (each ticket's timeline, the runs still stored and the token history), so they reach back over everything stored: pick **All** to compare with earlier work. Runs of finished one-time tasks are removed after 30 days, but their tickets' timelines stay, so older periods still count. Organization tickets aren't counted.

These numbers are only shown to you. Rem and its workers never see them in their instructions, so Rem can't aim for the numbers instead of the work.

The [self-work report](./board.md#the-report-when-self-work-stops) card shows one line for its own period, such as **31 of 40 tickets passed review first time.** The phone's **Usage** page shows the same section, and `Raticode usage verified` prints it; see [Providers, models and usage](../reference/cli-headless.md#providers-models-and-usage).

## What each turn sends

This isn't in a release yet. When Rem builds a turn's prompt, it records how big each part was: its length in characters and an estimated token count. The estimate is the characters divided by 4. It's a rough guide, not what the provider counts. Raticode stores the sizes only, never the text, in the usage history on this computer, and removes turns older than 90 days.

| Part | What it is |
| --- | --- |
| Identity and task instructions | Who Rem is, or for a Rem task, the worker's instructions: the task, its worktree, sandbox and landing rules |
| Tool settings and guidance | The provider, model and tool switches, with the browser, web and test-slot guidance that goes with them |
| Commit message format | Your commit message template |
| CLI and docs context | Where the Raticode CLI and the installed Rattish documentation are |
| Skill index | The workflow-builder skill's path |
| Resources | The skills and MCP servers chosen in the thread's tools |
| Second Brain rules | How to search and save to your Second Brain, when it's on |
| Report theme | The report format and design direction, or a one-line pointer to the rules tool that returns them |
| Delegation background | For a Rem task: the original request and what Rem recalled from its chat and linked threads |
| Delegation background file | For a Rem task whose recalled chat and threads run past 12,000 characters: the whole background, in a private file the worker opens when it needs it. The instructions keep the original request, memories, decisions and a short index of what the file holds. This part isn't sent in the prompt, so it's never counted as sent. Antigravity workers can only open the project, so they keep the background in their instructions |
| User memories | For a Rem task: the facts Rem was asked to remember that apply to this work |
| Standing rules | The fixed rules every prompt ends with |
| Editor context | The project root, open files and workflow paths |
| Conversation | The messages this turn sends |

Every part except the conversation is a standing part: it repeats from turn to turn. A provider session that Raticode resumes already holds the instructions and editor context it saw last time, so when they haven't changed Raticode doesn't send them again. Those parts are then marked **kept**: they still count toward the session's context, but this turn didn't resend them.

### Rem's own calls

Rem also calls its chat model without tools, outside any thread: to write a chat reply, answer a worker's question, decide what to tell a worker next, write a task update, review an organization's request, and for self-work's own three checks: writing its objectives, checking tickets against them, and checking the stop condition. Each of the three self-work checks is its own kind (**Self-work objective planning**, **Self-work objective check** and **Self-work stop check**); calls recorded before they were split show as **Self-work checks (before they were split)**. Each of these calls records its sizes too, with no change to what it sends or answers. Their parts are:

| Part | What it is |
| --- | --- |
| Fixed rules | The rules that are the same on every call of that kind, sent as the system prompt |
| Rules for this call | Rules that change with the call, such as auto mode or a phone request |
| One part per record | Each record of the call's data under its own name, such as Current request, Recent chat history, Open tasks, Board tickets, Memories, Decisions, Delegation background or Tool receipts. The desktop's records and recalled context are split one level further, so **Desktop thread list**, **Second Brain notes**, **Linked threads** and **Recalled messages** each show on their own |
| Lookups, reads and repairs | What a lookup, a read of earlier messages or a repair note added before Rem asked again |

Each request is recorded, so a reply that needed a lookup shows up twice: once as first sent and once with what the lookup added. These calls have no session, so every part is sent each time. Their data is JSON full of IDs, paths and times, which tokenizes more densely than prose, so the provider usually counts more tokens than the estimate.

Each request also records why Rem sent it, and whether its answer could be used. This isn't in a release yet.

| Attempt | When Rem sends it |
| --- | --- |
| First ask | The call's first request |
| Reformat | The reply wasn't the requested JSON, so Rem asked once more for only the JSON |
| Repair | The JSON had fields that didn't validate, such as a chat reply or task update with a wrong field, or a self-work check's answer that couldn't be used at all, so Rem asked once more with what was wrong |
| Read | Rem asked again after reading earlier messages or running a lookup the reply asked for |

A **re-asked** call is a reformat or a repair: Rem asked again because the answer couldn't be used. A read is a planned next step, so it isn't counted as re-asked. A **failed** call is one whose answer still couldn't be used: a reply that still wasn't JSON after the reformat, or fields that still didn't validate after the repair. When a chat reply fails, Rem says in the chat that it couldn't finish and keeps your request, and a task update that fails is posted as a plain update instead. Self-work's checks read their answers one entry at a time: an entry with a field that doesn't validate is left out, a reason or evidence that runs long is cut after a sentence, and a list that runs long keeps its first entries, so one bad entry no longer costs the rest. Only an answer with nothing usable, such as one that isn't a JSON object or a stop check whose `met` isn't true or false, is repaired, and when that fails self-work carries on as after an outage and checks again later. A kind with many re-asks has a prompt the model often gets wrong, which costs a second request each time. Only the attempt and the failed mark are stored, with the sizes, never the answer.

Each call also records the version of its fixed rules: the first 12 hex characters of the rules' SHA-256 hash, such as `3f2a9c0b71de`. Any change to the rules, even one word, starts a new version, so re-asks and failures can be compared before and after a rules change without reading logs. Only the version is stored, never the rules' text. A call with no fixed rules has no version, and calls recorded before versions were kept count under none. *New in 0.6.0.*

### Context sent

- **In a thread:** the **Thread info** button (the **i** at the top right of the thread) shows the thread's name and scope and, under **Context sent**, the newest turn's parts, biggest first, each with a bar and its estimated tokens. Hover a size to see its exact characters. It reads again when a turn ends.
- **On a Rem task:** the task's page has a **Context sent** section above its updates. Open it to see the newest worker turn of the task's current or last run.
- **In the CLI:** `Raticode rem context THREAD_ID` prints the same breakdown for any thread, with `--turns N` for the newest N turns. JSON output has each part's `chars`, `tokens_estimate` and `sent`, and `file` for a part the turn reads from a file. `Raticode rem context` with no thread ID prints Rem's own newest calls instead, each marked when it was a re-ask or failed, then each kind's total calls, re-asked and failed, with the same counts for each rules version below it. `--call KIND` keeps one kind. See [Rem from the CLI](../reference/cli-rem.md#what-a-thread-sends).

### Biggest standing parts, last 7 days

Under **Tokens in Raticode**, **Biggest standing parts, last 7 days** ranks the standing parts by how much text they sent across every Rem turn on this computer in the last week. Each row shows the estimated tokens sent, the average size per turn, and in how many turns it was sent. Use it to find which part to trim first. It always covers the last 7 days, whatever period the page shows. `Raticode usage show` prints the same list, and its JSON has it as `standing_parts`.

Parts of Rem's own calls rank in the same list, named with their kind, such as **Chat reply: Desktop thread list**, and counted in calls instead of turns. For those calls, everything except the message being answered, the chat history and what lookups added is a standing part. Below the list, **Rem's own calls** gives each kind's number of calls, average and largest size, the total sent, and how many of its calls were re-asked or failed in the last 7 days. `Raticode usage show --format text` adds the same **RE-ASKED** and **FAILED** columns, and its JSON has `reasked` and `failed` on each entry of `standing_parts.rem_calls`.

When the kind's newest call had a rules version, one more line counts only the calls sent with those rules, such as **Current rules since Nov 20: 1 re-asked · 0 failed of 40 calls**. It counts every call kept with that version, not only the last 7 days, and starts again from zero when the rules change. The JSON has it as `current_version` on each entry of `standing_parts.rem_calls`, with `version`, `calls`, `re_asked`, `failed`, `first_seen` and `last_seen`, or `null` when the newest call had no version. *New in 0.6.0.*

Each kind in **Rem's own calls** also shows the tokens its providers reported for those calls, such as **Reported: 1.1k tokens input · 900 tokens read from cache · 100 tokens written to cache**. Input counts everything the model read, cache reads and writes included, so a large share read from cache means the call's repeating parts were cheap to send again. These are the provider's own counts, not estimates, and a count no call reported is left out rather than shown as 0. A provider that reports no cache use shows only input. `Raticode usage show --format text` adds **INPUT**, **CACHE READ** and **CACHE WRITE** columns, with `-` for a count none of the calls reported. Its JSON has `input_tokens`, `cache_read_tokens` and `cache_write_tokens` on each entry of `standing_parts.rem_calls` (`null` when none was reported), and `reported_calls`, how many of the kind's calls reported tokens. Calls recorded before this was added have no tokens tied to them. *New in 0.6.0.*

Self-work research runs get the same numbers to start from. Each research turn writes this 7-day `standing_parts` record, sizes and part names only and no prompt text, to `context-usage.json` in the run's private folder under Raticode's data folder, and the worker's instructions name the file. The file is removed when the turn ends. Antigravity workers can only open the project, so they don't get it. *New in 0.6.0.*

The phone doesn't show these sizes yet.

## Costs

- Unknown cost stays unknown.
- Restoring a provider's native session avoids replaying the transcript but doesn't make earlier context free.
- Subscriptions, caching and API pricing decide what you pay.

Organization budgets and swarm progress have their own rules. Read [Organizations](../advanced/organizations.md#budgets-and-destinations) before treating a dollar budget as a hard cap: work already running can go past it.
