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

## Settings > Providers

*Changed in Raticode 0.6.0.*

The page has two parts:

- **Coding apps**: the CLIs Rem, threads and Organizations run. They come first.
- **For workflows only**: [local model servers](#workflow-providers) and [classification models](#classification-models), which only workflow nodes use. Each is folded into one closed line with a short summary, such as **Local model servers** "1 server" or **Classification models** "Jev: key saved". Choose a line to open its form.

Searching Settings for an app's name, such as "Claude", or for words such as "sign in", "accounts", "default model", "effort", "executable", "app location" or "version" opens this page.

### Coding apps

Each coding app Raticode found gets one row:

- its name and version,
- its status, in words with a symbol: **Ready**, **Needs sign-in**, **Checking** or **Error**,
- **Signed in** when the app is ready, or a **Sign in** button when it needs sign-in,
- how many [accounts](#several-accounts) it has, such as "2 accounts, 1 needs sign-in",
- an **Enabled** switch. Turn it off and Rem stops offering that app. Found apps start switched on.

**Sign in** on the row runs the app's own browser sign-in (Claude Code, Codex, Cursor, GitHub Copilot and Grok). An app without one, such as Antigravity, opens its details to say how to sign in. A row with **Error** shows the app's message under it, such as "Model discovery timed out."

Choose the arrow next to the name, or the name itself, to open the details. They list, in order:

- **Show in model menus**: a checkbox for each model the app reported. See [Hide models from menus](#hide-models-from-menus).
- **Default model and effort**: what Raticode picks when you choose this app. These appear once the app lists its models.
- The app's accounts, with **Add account**, when the app supports accounts.
- **Sign-in**: **Sign in again** runs the app's own sign-in once more, for example to switch its default login.
- **App location**: one closed line saying where the app runs from, such as "Automatic · /usr/bin/claude" or "Override · /opt/claude/bin/claude". Choose it to see the path Raticode found, an **Executable override** field and **Choose executable...**. If you saved an override and the app isn't there, the line ends with "not found" and opens by itself.

Apps Raticode didn't find are folded into one closed group after the rows, **Not installed (n)**. Open it to type an executable path or use **Choose executable...** for an app installed somewhere Raticode doesn't look.

An app whose saved **Executable override** points somewhere it isn't keeps its own row instead. The row shows **Error** and "wasn't found at its saved location", and when you open its details, **App location** is already open so you can fix the path or clear it.

## Hide models from menus

Each provider's details in **Settings > Providers** list the models it reported, under **Show in model menus**. Every model has a checkbox, with its name and its model ID under the name.

- Uncheck a model to keep it out of the model menus in Rem, threads and the default model list. Check it to show it again. Each change saves at once.
- The line above the list says how many are shown, such as "12 of 14 shown". **Show all** and **Hide all** change every model in the list.
- A provider with more than 8 models gets a search field. It matches the name or the ID and only filters the list; the count and **Show all** and **Hide all** still cover every model.
- Raticode doesn't list a provider's short aliases, such as Claude Code's `haiku`, unless you hid one before. A hidden default model stops being the default; Raticode uses a shown model until you choose another.

*New in Raticode 0.6.0.* Raticode 0.5.5 shows the same choice as two lists, **Allowed** and **Denied**, with arrow buttons between them. Both save the same setting, so what you hid carries over.

## Save defaults

| Where | What it sets |
| --- | --- |
| **Settings > Providers** | Enabled providers, executable paths, [which models show in model menus](#hide-models-from-menus), provider-specific defaults |
| **Settings > Rem** | Default provider, model and effort for new tasks and threads. Threads with their own choices keep them. |
| **Settings > Rem > Accounts** | Which [account](#several-accounts) each provider's work uses, such as your Claude Code, Codex or Grok account |
| **Settings > Rem > Rem chat provider** | Who writes replies in Rem's home chat. Leave it on **Same as tasks and threads**, or pick a faster model or lower effort so chat answers quickly while tasks keep a stronger model. |

With Claude Code as the chat provider, Rem's home chat reply appears as it's written. Other providers show what Rem is doing, such as "Reading earlier messages", then the finished reply. Hover a reply (or open its ⋯ menu on the phone) to see which model wrote it and how long it took.

Opening Settings reuses what Raticode learned about each provider in the last 5 minutes, so it doesn't start every CLI again. Older or missing information is checked in the background, and a small spinner shows next to each provider's status while it updates. The line next to **Coding apps** in **Settings > Providers** says when the providers were last checked, for example "Checked 3 min ago".

After signing in, changing a binary or getting access to a new model, choose **Refresh providers** on that line. It checks every provider again straight away. Refreshing doesn't sign the CLI in. Use **Sign in** on the app's row, or sign in with the CLI itself. **Sign in** runs the app's own browser sign-in for Claude Code, Codex, Cursor, GitHub Copilot and, from 0.6.0, Grok (`grok login`).

## Several accounts

*New in Raticode 0.6.0.*

If you have more than one subscription for a provider, add each one as an **account**. Every account is its own configuration folder of that provider's own app, so each keeps its own sign-in, settings and session history, and all of them stay signed in at the same time.

Claude Code, Codex and Grok have accounts. A Claude Code account's folder is Claude Code's documented `CLAUDE_CONFIG_DIR`; a Codex account's folder is its `CODEX_HOME`; a Grok account's folder is its `GROK_HOME`. Other providers get accounts as Raticode learns how each one keeps its sign-in in a folder. The screens, CLI commands and Rem settings below are the same for every provider.

| | Claude Code | Codex | Grok |
| --- | --- | --- | --- |
| Folder | `<data>/claude-accounts/<id>`, used as `CLAUDE_CONFIG_DIR` | `<data>/codex-accounts/<id>`, used as `CODEX_HOME` | `<data>/grok-accounts/<id>`, used as `GROK_HOME` |
| Sign in | `claude auth login` | `codex login`, or `codex login --device-auth` [from the phone](#sign-an-account-in-from-the-phone) or the CLI | `grok login`, or `grok login --device-auth` [from the phone](#sign-an-account-in-from-the-phone) or the CLI |
| Signed in | `claude auth status`, with email and plan | Codex's app-server `account/read`, with email and plan | Whether Grok's `_x.ai/billing` answers for the folder |
| Limits | Claude Code's `/usage` | Codex's app-server `account/rateLimits/read` | Grok's `_x.ai/billing` (credits, on-demand spend, prepaid balance) |
| Sign out | `claude auth logout` | `codex logout` | `grok logout` |
| Variables removed from its runs | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` and the Bedrock, Vertex and Foundry switches | `OPENAI_API_KEY`, `CODEX_ACCESS_TOKEN`, `CODEX_API_KEY` | `XAI_API_KEY`, `GROK_CODE_XAI_API_KEY`, `GROK_AUTH`, `GROK_AUTH_PATH` |
| Copy my setup | `CLAUDE.md`, skills, commands, agents and output styles from `~/.claude` | `AGENTS.md`, skills and `config.toml` without its sign-in settings, from `~/.codex` (or `CODEX_HOME`) | Not offered: a new Grok account starts empty |

A Codex account's folder is the `CODEX_HOME` for everything Codex does as that account: `codex exec` turns, resuming and forking them, the steerable app-server turns, Rem's replies and the usage check. Codex keeps the account's sessions there too.

*New in Raticode 0.6.0.* A Grok account's folder is the `GROK_HOME` for everything Grok does as that account: `grok agent --no-leader stdio` turns, Rem's replies, the sign-in check and the usage check. Grok keeps the account's sign-in (`auth.json`), settings and sessions there. `--no-leader` keeps accounts from sharing one background Grok process.

### Add an account and sign in once

1. Open **Settings > Providers** and open the provider's details, such as **Claude Code**'s. Its accounts are listed there, under **Claude Code accounts**. The row itself shows how many accounts it has. There's no separate accounts section.
2. Choose **Add account**. The account's options appear:
   - **Account name**: a short name, such as `work`.
   - **Import my ...**: start the account with your shared setup, listed in the table above. It's copied from the provider's own folder (`~/.claude` or `~/.codex`) unless you type another folder or pick one with **Choose folder...**, such as a team's shared skills folder. Nothing else is copied: no sign-in, history or sessions. For Codex, `config.toml` is copied without the settings that choose or hold a sign-in (such as `forced_login_method`, `cli_auth_credentials_store`, `model_provider` and `model_providers`) and without any token, key, header or `env` value, and `auth.json` is never copied.
   - **Sign in now**: on by default. Turn it off to add the account now and choose **Sign in** on its row later.
3. Choose **Add and sign in**. The provider's own sign-in opens your browser (`claude auth login`, `codex login` or `grok login`). Approve the sign-in there. The account changes to **Signed in** by itself.

Raticode makes each account's folder for you, named by a random ID inside its data folder, so you don't pick where the sign-in lives. A provider that doesn't support accounts yet, such as Cursor, has no **Add account** button. Grok has no shared setup to import, so a new Grok account starts empty and has no **Import** option.

### Change an account

Each account's row shows its name, its status (such as **Signed in** or **Needs sign-in**), its plan and the email it's signed in with. The row has a button only when the account needs something: **Sign in** for an account that needs sign-in, or **Cancel sign-in** while a sign-in is waiting in your browser.

Everything else is in the account's **⋯** menu (its label names the account, such as "Actions for work"). With the keyboard, press Enter or Space on **⋯** to open it, move with the arrow keys, and press Escape to close it and go back to the button.

- **Sign in again**: runs the provider's own sign-in for an account that's already signed in, for example to switch it to another login.
- **Rename**: opens a name field on the row. The folder and sign-in stay as they are. If **Settings > Rem** used the old name, it follows the new one.
- **Import setup**: opens a form on the row that copies your shared setup into an account you already have, from the provider's own folder or one you choose. Anything the account already has, such as its own `CLAUDE.md`, stays as it is. Raticode says what it copied.
- **Copy my setup again**: not in the menu. It appears in a short line under the account, such as "Differs from your setup in CLAUDE.md and skills.", when the shared setup the account has no longer matches the provider's own folder. The line lists only the items that differ, and the button copies only those again. Files the account has that the source doesn't, such as a skill of its own, stay. For Codex, `config.toml` goes through the same cleaner as when the account was added: only the source's settings without sign-in settings count and are copied in, and settings only the account has, including its own sign-in settings, stay. Sign-in files (`.credentials.json`, `auth.json`), sessions and history are never read or changed. Items the account never had are **Import setup**'s job, so they don't show here.
- **Remove**: last in the menu, set apart in red. Raticode asks you to confirm first. It signs the account out with the provider's app, then deletes its folder. If **Settings > Rem** used it, Rem goes back to the default sign-in.

Signing in once per account is the only manual step. The provider's app keeps that sign-in in the account's folder (Claude Code on macOS: in a Keychain entry tied to the folder) and renews it itself. Switching a thread or task to another account, whether you do it or Rem does, never asks you to sign in again.

If no browser opens, use **open the sign-in page** under the account, or run `Raticode provider account login work` in a terminal and follow the provider's prompts there. On a computer without a browser, `Raticode provider account login work --provider grok --device-auth` runs `grok login --device-auth`, which prints a page and a code to enter on any other device.

If the provider refuses the sign-in, the account shows the provider's own message instead of a general "try again". For example, OpenAI limits how many paid ChatGPT accounts can sign in to Codex on one device; Codex's message about that limit appears under the account and on its Usage card.

### See which account runs

*New in Raticode 0.6.0.* Each provider's accounts in **Settings > Providers** show which account its work uses, read from the choices you already saved:

- **Threads, tasks and Rem's replies** sits on the account chosen in [Settings > Rem > Accounts](#choose-which-account-runs).
- **Self-work** sits on the account [self-work's runs](#choose-self-work-s-account) use. When self-work follows Rem's setting, it sits on the same account.

When the choice isn't one account, a line above the accounts says so instead, and no account is labelled:

| Choice | What the line says |
| --- | --- |
| No account chosen | "Threads, tasks and Rem's replies: Default sign-in" |
| **Choose by usage** | "Self-work: Choose by usage" |
| An account that was removed | "Threads, tasks and Rem's replies: work (removed)" |

A chosen account that's signed out keeps its label, followed by "(needs sign-in)", because Rem skips it until it's signed in again. A provider with no accounts and nothing chosen shows only its "No extra accounts" note.

**Change** next to each label opens **Settings > Rem** on the provider's picker: **Accounts** for threads, tasks and Rem's replies, or self-work's own **Accounts**. Nothing is stored on the Providers page itself.

### Sign an account in from the phone

*New in Raticode 0.6.0 and Raticode Mobile 0.36.0.* A Codex or Grok account that needs sign-in has **Sign in** on the phone too: in a thread's **Account** menu and on the account's card on the phone's **Usage** page.

1. Tap **Sign in**. The computer runs the provider's device code sign-in for that account, in the account's own folder: `codex login --device-auth` with its `CODEX_HOME`, or `grok login --device-auth` with its `GROK_HOME`. No browser opens on the computer.
2. The phone shows **Open sign-in page** and a one-time code with a **Copy** button. Open the page and sign in to the account you want for this Raticode account. Codex's page is `https://auth.openai.com/codex/device`; Grok's comes from xAI and may already have the code filled in.
3. Enter the code there, or check that it matches the one the page shows. Only enter it on the page you opened from the phone. The phone shows how long is left: Codex's code expires after 15 minutes, and Grok waits for its code for at least 10 minutes.
4. When the provider's app finishes, the account changes to **Signed in**, on the phone and on the computer.

Quitting Raticode stops a sign-in that is still waiting, and its code stops working; start a new one after reopening. Starting a second sign-in for the same account stops the first.

If the code runs out first, the phone says the sign-in wasn't finished in time. **Cancel** stops the sign-in on the computer, and **Try again** starts a new one with a new code.

If the provider refuses a device code sign-in, the phone shows the provider's own message and what to do instead:

- Codex: device code sign-in has to be turned on for your ChatGPT account first, in ChatGPT's **Settings > Security** (`chatgpt.com/settings/security`), or, for a workspace account, by an admin in the workspace's permissions.
- Grok: xAI can answer that device code sign-in isn't available. The phone can't change that, so it says to sign the account in on the computer instead, from **Settings > Providers > Grok**.

From a terminal, `Raticode provider account login work --device-auth` runs the same sign-in and prints the link and code. Claude Code accounts don't have a device code sign-in, so for those the phone still says to sign in on the computer.

### When an account needs sign-in

If the provider reports an account as signed out or its sign-in as expired, the account shows **Needs sign-in**:

- on the desktop in **Settings > Providers** and on the **Usage** page,
- on the phone's **Usage** page, where a Codex or Grok account's card has **Sign in** ([from the phone](#sign-an-account-in-from-the-phone)),
- in the CLI with `Raticode provider account list` and `Raticode usage show`.

Rem skips that account when it chooses one. Choose **Sign in** next to it to run the provider's sign-in again. **Check sign-ins**, next to **Add account**, asks the providers about every account now.

If two accounts of one provider are signed in to the same subscription, such as the same Claude account or the same ChatGPT account, Raticode warns you. Sign one of them in again with a different account, or remove it. Codex accounts show the email and plan Codex's `account/read` reports.

Grok reports no email or account ID, so a Grok account shows no email or plan, and two Grok accounts signed in to the same Grok account aren't flagged. A Grok account counts as signed in when Grok's `_x.ai/billing` answers for its folder; Grok's "Authentication required" answer means **Needs sign-in**, and any other failure leaves the state unknown rather than signed out. Grok picks up a changed `auth.json` by itself, so a sign-in made in a terminal shows up at the next check without restarting anything.

### Choose which account runs

**Settings > Rem > Accounts** has one choice per provider with accounts: **Claude Code account**, **Codex account** and **Grok account**. It applies to that provider's threads, Rem tasks, self-work and Rem's own replies. A thread's own choice and [self-work's own choice](#choose-self-work-s-account) win over it. The phone has the same choices under **Desktop settings > Rem**.

| Choice | What runs |
| --- | --- |
| **Default sign-in** | The provider's usual sign-in (`~/.claude`, `~/.codex` for Codex, `~/.grok` for Grok), as before |
| An account | That account |
| **Choose by usage** | Rem picks the provider's signed-in account with the most room left when a thread or task starts |

If you picked a Claude Code account before this setting covered other providers, that choice carries over as the Claude Code choice.

With **Choose by usage**, Rem looks at each account's last weekly and 5-hour readings from the last 30 minutes. It skips accounts that need sign-in or have a full window, prefers accounts it has readings for, then picks the lowest weekly use and then the lowest 5-hour use. For Codex, the 5-hour and weekly windows are found by their length. A window Codex reports as null has no reading: it counts as unknown, never as empty. When Codex says the account's included usage isn't allowed right now, that account counts as full; when Codex doesn't say, Raticode assumes nothing either way. Grok reports a billing period's credits rather than weekly and 5-hour windows, so Rem compares Grok accounts by their credits: it skips accounts whose credits are used up, then picks the lowest credit use, then the name. The run's reason says so, such as "Most room left: 40% of its monthly credits used." Each run records which account it chose and why. A thread or task stays on the account it started on; if none can run, Rem uses the default sign-in.

Your own choice wins: a task created with an account (`provider_profile` in the task, or `Raticode rem tasks create --account NAME`) runs on it whatever the setting says.

### Choose self-work's account

*New in Raticode 0.6.0.* **Settings > Rem > Self-work** has its own **Accounts** choice per provider, so the work Rem starts on its own can run on a different account from your chat and threads: **Rem's setting** (the default), **Default sign-in**, **Choose by usage** or one account.

A run self-work started uses the task's own account first, then this choice, then **Settings > Rem > Accounts**. Rem's chat, threads and tasks you create keep using Rem's setting. The phone's **Desktop settings › Rem self-work** and `Raticode selfwork set --account PROVIDER=ACCOUNT` change the same choice. See [Which account self-work uses](./board.md#which-account-self-work-uses).

### Self-work on accounts

*New in Raticode 0.6.0.* [Self-work's limits](./board.md#self-work-limits) watch the account each run self-work starts uses, not the provider's default sign-in:

- **Daily cap.** **Weekly allowance per day** counts how much of that account's own weekly allowance rose while self-work ran on it, separately for each account. A run on `work` counts against `work` only. A Grok account has no weekly allowance, so the cap counts its credits instead, scaled to the credits period: 10% of a week is about 2.3% of a 30-day month's credits a day.
- **Rate limit use** reads that account's own stored windows.
- **Before starting a task**, self-work checks the account Rem would run it on: [self-work's own choice](#choose-self-work-s-account) for the provider, else Rem's setting. With one account chosen, self-work starts nothing new while that account is at a limit. With **Choose by usage**, self-work skips accounts at the daily cap or at the rate-limit percent and starts the task on the one with the most room left of those still open. It starts nothing when every signed-in account is at a limit; only when no account is signed in at all do its runs use the default sign-in. The run records which accounts it skipped and why, such as "home reached the daily cap of 15%".
- **Default sign-in.** With the default sign-in chosen, or no accounts, the limits read the default sign-in as before.

The status line and **Settings > Rem > Self-work** name the account, such as "Today self-work used about 12% of work's weekly allowance; its daily cap is 15%." Other accounts self-work used that day follow, such as "Also today: about 3% of home's."

### Choose a thread's account

*New in Raticode 0.6.0.* When the selected provider has accounts, the model picker in a thread shows an **Account** menu under the provider, model and effort. The phone's thread picker has the same menu.

| Choice | What runs |
| --- | --- |
| **Rem's setting** | Whatever **Settings > Rem > Accounts** says for the provider. The menu shows it, such as **Choose by usage**. This is where every thread starts |
| **Default sign-in** | The provider's usual sign-in, even when Rem's setting names an account |
| An account | That account. The menu shows whether it's signed in and its last weekly and 5-hour reading, such as "Week 42% · 5h 10% used, 2h ago" |

- The choice is saved on the thread, once per provider, and sent with every turn, including turns you send from the phone. It wins over Rem's setting. Switching to another provider and back keeps it.
- Accounts that need sign-in are listed but can't be picked. On the desktop, **Sign in** next to one starts that account's sign-in, as in **Settings > Providers**. On the phone, **Sign in** for a Codex or Grok account [signs it in from the phone](#sign-an-account-in-from-the-phone) with a link and a one-time code; for a Claude Code account it says to sign in on the computer: Settings › Usage › **Sign in** on the account's card.
- Changing the account after a thread has started warns that your next message starts a new provider session on that account, as switching providers does. Rem gives the new session the conversation so far, and the divider above your message names the account, such as `switched to Claude Code - fable - high - work`.
- A thread that runs on another paired desktop has no Account menu: that computer uses its own accounts and its own Rem setting.
- On the phone the menu appears only when the computer's Raticode has it.

Raticode doesn't move work to another account when one reaches its usage limit. Rem waits for the limit to reset, as it does with one account. Anthropic's and OpenAI's terms don't say whether switching accounts automatically at a limit is allowed, so this is not offered.

The same rule holds in workflows. A step's [fallback providers](../nodes/agent.md#fallback-providers) can list another account of the same provider, but after a usage limit the step passes over it, and over the provider's default sign-in, and the run lists it as skipped. It moves on to another provider or an API-key profile if the list has one. Another account is still used when the first one's sign-in expired, the service is overloaded or the CLI isn't installed, and the workflow's checks warn that it will only be used for those errors.

When Settings > Rem names one Codex account, Rem's library lists the skills in that account's `skills` folder under **Codex**, and a Codex turn switches off the MCP servers in that account's own `config.toml` rather than in `~/.codex`.

### What Raticode does and doesn't touch

- Raticode never reads, copies, stores or sends an account's sign-in or credential files, including Codex's and Grok's `auth.json`. It only runs the provider's unmodified app with the account's folder, using the commands in the table above. No proxy runs and Raticode opens no port; the browser sign-in returns to the provider's app itself.
- An account's runs never get the provider's other sign-in variables, even when Raticode itself was started with them, so an `OPENAI_API_KEY` in your shell can't stand in for a Codex account's ChatGPT sign-in, and an `XAI_API_KEY` can't stand in for a Grok account's sign-in. The default Grok sign-in keeps `XAI_API_KEY` as Grok's fallback, as before.
- Each provider's account folders live in its own `<provider>-accounts` folder in Raticode's data folder (`claude-accounts`, `codex-accounts`, `grok-accounts`), readable only by you. Each account's folder is named by an internal ID, so renaming an account keeps its sign-in.
- Claude Code's Read and Edit tools are denied every provider's account folders in every Claude Code turn, and sandboxed shell commands can't see them. From 0.6.0, on Linux with bubblewrap, a thread's shell can't see them either, except the account the thread itself uses. In that one, Claude Code's settings and plugins, Codex's `config.toml`, hooks and rules, and Grok's `config.toml`, hooks, folder trust and plugins stay read-only, and Claude Code runs on the account ignore MCP servers in its `.claude.json`, which the turn can still write. See [Credential folders no agent can reach](../advanced/security.md#credential-folders-no-agent-can-reach) for what the turn can still change. Without bubblewrap, a shell command in a thread that isn't sandboxed can still read any file your user account can, including these folders.
- From 0.6.0, Codex turns can't read or write any account folder either, and Cursor, Copilot, OpenCode, Antigravity and Grok don't see them on Linux. See [What Codex's sandbox can reach](#what-codex-s-sandbox-can-reach), [What Grok's shell can reach](#what-grok-s-shell-can-reach) and [Credential folders no agent can reach](../advanced/security.md#credential-folders-no-agent-can-reach).
- **Remove** signs the account out with the provider's app first, then deletes its folder and its history.
- Each account has its own sessions. Moving a thread to another account starts a new session there, as switching providers does.

### What Codex's sandbox can reach

Checked with Codex 0.156.1 on Linux, by having Codex print the sandbox rules it gives the model (`codex debug prompt-input`) for the sandbox settings Raticode passes:

| Codex sandbox | Can read | Can write |
| --- | --- | --- |
| Up to 0.5.5: `read-only` | The whole disk, including every `<provider>-accounts` folder | Nothing |
| Up to 0.5.5: `workspace-write` | The whole disk, including every `<provider>-accounts` folder | The project, temp folders and each `--add-dir` folder. A thread trusted with Raticode's data folder gets it as a writable folder, which includes `claude-accounts` and `codex-accounts`. |
| From 0.6.0: the `raticode` permission profile | The whole disk except each `<provider>-accounts` folder, `devices`, `browser-grants` and a headless secrets folder | As before, except those folders |

From 0.6.0, Raticode turns the sandbox mode it used to pass (`--sandbox workspace-write` or `read-only`, plus `--add-dir`) into a Codex permission profile named `raticode` with the same reach, and denies each credential folder in it. Codex applies a denied folder to reading and writing alike, and to folders that sit inside a writable one. Checked with Codex 0.156.1 on Linux: a command can't list, read or write a denied folder, can't create one that doesn't exist, and `.git`, `.codex` and `.agents` in writable folders stay read-only, as before; Codex's app-server reports the `raticode` profile as active.

Codex ignores permission profiles when a loaded `config.toml` sets `sandbox_mode`. Raticode then keeps the older settings and logs why, so remove `sandbox_mode` from your Codex config to get the profile. Native Windows keeps the older settings too. See [Credential folders no agent can reach](../advanced/security.md#credential-folders-no-agent-can-reach) for every kind of run.

### What Grok's shell can reach

Grok has a sandbox of its own (`grok --sandbox`), but it's off by default and Raticode doesn't turn it on. Even its `workspace` and `read-only` profiles let commands read the whole disk, so they wouldn't keep a turn out of another account's folder (Grok's bundled user guide, *Sandbox Mode*, Grok 1.0.89). What keeps Grok out is Raticode's own mask:

| Where | Can a Grok turn's shell read other Grok accounts' folders? |
| --- | --- |
| Linux with bubblewrap, from 0.6.0 | No. Every Grok process Raticode starts (turns, Rem's replies, the sign-in and usage checks) runs inside bubblewrap, where the whole `grok-accounts` folder is empty and read-only. Only the turn's own account folder is put back, so Grok can use its sign-in. The other accounts' folders, and `claude-accounts`, `codex-accounts`, `devices` and `browser-grants`, stay empty. |
| macOS, Windows, or Linux without bubblewrap | Yes. Grok's shell runs with your full access and can read any account's folder, including its `auth.json`. |

In the turn's own folder, the files that start programs or loosen what Grok asks first stay read-only: `config.toml`, `hooks`, `hooks-paths`, `managed_config.toml`, `requirements.toml`, `sandbox.toml`, `trusted_folders.toml`, the plugin folders, `workflows`, `lsp.json`, `pager.toml`, `statusline.sh` and `bin`. If `config.toml` is missing, Raticode creates it empty first, so the turn can't create one that a later run reads. The turn can still read its own `auth.json`, as a turn on the default sign-in can read `~/.grok/auth.json`.

How this was checked: Raticode's tests start a stand-in `grok` through the same code path and check the bubblewrap arguments: the `grok-accounts` folder is covered, only the turn's account is bound back, and the files above are read-only. Bubblewrap itself couldn't run where those tests ran, so the mask wasn't exercised against the real `grok` there. Grok 1.0.89 itself was run with an empty `GROK_HOME`: it answered `_x.ai/billing` with error -32000 "Authentication required" and wrote `config.toml` into the folder.

Not built yet: accounts for other providers, an account per organization employee, and starting a Claude Code account's sign-in from the phone.

## Conversation continuity

Changing provider or model keeps the Raticode thread.

- Changing model or effort within one provider keeps that provider's native session.
- Each provider keeps its own session for the thread. Going back to a provider restores its session along with the conversation you had elsewhere.
- For a large handoff, Raticode can summarize earlier history for the new provider. The full history stays saved.
- If a native session is unavailable, Rem reports the error instead of quietly replaying everything into a fresh session.

## Workflow providers

[Agent](../nodes/agent.md) nodes set their provider and model in `workflow.rattish`, not from Rem's picker. Workflow API profiles and coding CLI subscriptions are configured separately, so an API profile doesn't automatically work in Rem chat. The same goes for [local model servers](../setup.md#local-model-servers) such as Ollama: they're for workflow agent nodes, not Rem chat. See [Provider setup](../setup.md). In **Settings > Providers** they're under **For workflows only > Local model servers**.

## Classification models

Jev, TypeSafe's yes/no and multiple-choice classifier, has its own card under **Classification models** in **Settings > Providers** (from 0.5.5). From 0.6.0 it sits under **For workflows only**, folded until you open it. It isn't a coding provider: it answers a question with a label and a score instead of writing text, so it never appears in Rem's model picker, Organizations or agent nodes. Workflows use it through the [jev node](../nodes/jev.md). See [Jev classifier](../workflows/jev.md).

## Usage and billing

Sign-in, subscriptions, rate limits and billing belong to the provider. Keeping a thread or restoring a session doesn't make earlier context free. [Usage and allowances](./usage.md) separates what Raticode recorded from your provider account totals.
