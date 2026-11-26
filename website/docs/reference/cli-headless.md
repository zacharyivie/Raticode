---
title: Headless CLI for workflows, Organizations and more
description: Script workflow runs, Organizations, swarms, Second Brain, providers, usage and live threads through a running Raticode backend, and keep their settings in files.
pageClass: docs-layout
---

# Headless CLI for workflows, Organizations and more

::: info New in 0.5.3
These commands arrived in Raticode 0.5.3. Earlier versions don't have them.
:::

These commands let a script, a CI job or a remote box use Raticode without the desktop window.

- Each one sends a request to a running backend, through the same API the desktop window and the phone use. A run you start here shows in the app's Runs, and the app's Stop button reaches it.
- The CLI never opens the backend's databases itself.
- The CLI finds a [headless backend](./headless.md) on its own. It can't find the desktop app's backend yet, because the app doesn't publish its address or token. With only the desktop app running, these commands can't reach it.
- There are no menus, prompts or screens. A command makes one request, or a few, and prints the result.
- Decisions that need you, such as starting an organization, have no command. They stay in the app.

Related pages:

- [Rem, tickets and self-work from the CLI](./cli-rem.md) covers `Raticode rem`, `Raticode tickets` and `Raticode selfwork`.
- The [CLI reference](./cli.md#talk-to-a-running-backend) covers `Raticode api`, `Raticode wait`, how the CLI finds the backend and its token, and the exit codes.

## Before you start

On a server or in a container, start a [headless backend](./headless.md). It writes its address and token where these commands find them with no setup:

```bash
Raticode serve --headless &
Raticode org list
```

- Every command takes the same connection options as `Raticode api`: `--endpoint`, `--token-file` and `--data-dir`. None accepts the token itself on the command line.
- Inside a Rem worker session the CLI doesn't pick up the owner token. Give agents a limited [agent token](./headless.md#agent-tokens) instead.
- A server usually has no system keychain, so the Jev key, Organizations and webhook secrets, usage reporting credentials and workflow secrets can't be saved there. Give the backend a read-only [secrets folder](./headless.md#secrets-without-a-keychain) instead (from 0.5.5): `Raticode serve --headless --secrets-dir PATH`, or systemd's `LoadCredentialEncrypted=` with no option. `Raticode doctor` lists which secret-backed features can't work.
- Output is one JSON envelope on stdout: `{"ok", "command", "data", "error", "warnings"}`. Messages for people go to stderr. `--output text` prints a readable table.
- Exit codes are the [backend client's codes](./cli.md#output-and-exit-codes): 0 done, 1 failed, 2 invalid input, 3 not found, 4 conflict or stale revision, 5 not allowed, 6 no backend, 7 waiting on you, 8 timed out.

## Workflows

Most `Raticode rattish` commands work on `workflow.rattish` files, and `rattish run` runs a workflow inside the CLI process. The commands below ask the backend instead. Their help starts with "(Backend)". `rattish list` reads the workflow registry from the data folder, and the others need the backend.

```bash
Raticode rattish list --format json
Raticode rattish show nightly-report --source
Raticode rattish start nightly-report --input day='"2026-11-10"' --wait --timeout 30m
Raticode rattish runs nightly-report --status error
Raticode rattish runs --all --since 24h --output text
Raticode rattish logs nightly-report RUN_ID
Raticode rattish events nightly-report RUN_ID
Raticode rattish stop nightly-report --run RUN_ID
Raticode rattish resume nightly-report RUN_ID --wait
Raticode rattish runs rerun RUN_ID --wait
Raticode rattish start nightly-report --from summarize --wait
Raticode rattish runs changes RUN_ID --output text --diff
Raticode rattish runs land RUN_ID
Raticode rattish runs discard RUN_ID
Raticode rattish runs failure RUN_ID --output text
```

`rattish runs WORKFLOW_ID` and `rattish runs --all` are short for `Raticode rattish runs list`. `Raticode rattish runs rerun RUN_ID` runs a workflow again with the inputs and trigger events an earlier run saved, using the workflow as it is now; it finds the workflow from the run ID. Password inputs are never saved, so give each one again with `--input name=value`, on `rattish resume` too. See [Run again with the same inputs](../studio/runs.md#resume-and-run-again) (from 0.5.5).

`Raticode rattish start WORKFLOW_ID --from NODE` starts a run from one node, reusing the saved results of the unchanged steps before it from the newest run that fits; `--reuse RUN_ID` names the run, and `--dry-run` shows which run and steps it would reuse. `Raticode rattish run PATH --from NODE` does the same in the CLI process. See [Run from here](../studio/runs.md#run-from-here).

`Raticode rattish runs changes RUN_ID` shows what a run of a [`workspace: worktree`](../workflows/rattish.md#run-in-a-worktree) workflow changed: its branch, each file, and whether it lands cleanly in the branch the project was on (`--diff` with `--output text` prints the diff). `Raticode rattish runs land RUN_ID` lands it there, as a fast-forward or a merge checked for conflicts first, and removes the run's worktree and branch. It refuses while that branch's folder has uncommitted changes, never pushes, and exits 4 on a conflict, naming the files. `--expected-tip COMMIT` lands only if the branch is still the commit you reviewed (`tip` in the JSON from `changes`). `Raticode rattish runs discard RUN_ID` removes the worktree and branch instead. Land and discard refuse to run inside a Raticode agent turn. New in 0.6.0.

`Raticode rattish runs failure RUN_ID` prints what [Ask Rem about this failure](../studio/runs.md#ask-rem-about-a-failure) attaches: the workflow file, the run, the failed node and its type, the error, the end of that node's output, preflight problems and the run's inputs, with password inputs masked, all marked as untrusted run output. `--node NODE` picks another node, and `--node workflow` describes the run itself. It finds the workflow from the run ID. New in 0.6.0.

`stop` reaches runs whichever process started them, including [triggered runs](../workflows/triggers.md#stop-a-triggered-run) from the background scheduler. With no backend running it still works: it asks runs on this computer to stop by themselves (from 0.5.5). Without `--run` it stops every run of the workflow.

`start` starts the run and returns its ID straight away. Its nodes really run: agent nodes call providers and command nodes run commands. `--dry-run` only shows the plan.

| Option | What it does |
| --- | --- |
| `--wait` | Waits and exits with the run's result: 0 succeeded, 1 failed or stopped, 7 stopped at an approval gate, 8 the timeout passed first. The run keeps going after a timeout |
| `--follow` | Waits too, and prints each run event as it happens, one `{"kind": "event", "data": ...}` line each, then the result line |

`Raticode rattish wait WORKFLOW RUN_ID` waits for a run you started earlier, with the same exit codes.

Approval gates:

```bash
Raticode rattish approvals nightly-report --pending
Raticode rattish approve nightly-report RUN_ID approve --notes "Checked the diff"
Raticode rattish reject nightly-report RUN_ID approve
Raticode rattish approve release RUN_ID channel --choice Beta --reply "Internal testers only"
```

The third argument is the approval node's ID. A gate with [options](../nodes/approval-gate.md#choices-and-typed-replies) is answered with `approve --choice LABEL` instead of approve or reject, and `--reply TEXT` sends the typed reply a gate asks for. Inside a Rem or worker turn the CLI refuses `approve` and `reject` (exit 5, error code `user_only`), so a workflow can't approve its own gate.

[Triggers](../workflows/triggers.md) keep their `Raticode rattish triggers` commands. Two more move a workflow's triggers in and out of a file through the backend:

```bash
Raticode rattish triggers export nightly-report -f triggers.json
Raticode rattish triggers apply nightly-report -f triggers.json
```

- `apply` matches triggers by kind and label. It creates missing ones and updates ones that differ, so applying the same file twice changes nothing.
- `--prune` also removes triggers the file doesn't list.
- Trigger files never hold IDs or webhook tokens. A new webhook trigger gets a fresh token, which `apply` prints once.
- Each trigger `apply` creates or changes runs the workflow as it is then, the same as saving it in the Triggers section. To review a changed workflow on a headless server, use `Raticode rattish triggers changes ID` and `approve ID`; see [Approved versions](../workflows/triggers.md#headless-servers). New in 0.6.0.

[Workflow shortcuts](./shortcuts.md#run-a-workflow-from-any-app), the keys that run a workflow from any app, are listed, approved and removed without a backend: `Raticode rattish shortcuts list`, `Raticode rattish shortcuts approve SHORTCUT_ID` (refused inside an agent turn) and `Raticode rattish shortcuts remove SHORTCUT_ID`. Adding one needs the desktop app, which records the key. New in 0.6.0.

[MCP tool steps](../nodes/mcp-tool.md) call servers from Rem's MCP server list. `Raticode rem mcp tools NAME` starts one the way a step would, in its sandbox, and lists its tools with their arguments; it is what the Studio's tool picker shows. Add `--network HOST` (repeatable, or `any`) and `--no-sandbox` to match the step's `network` and `sandbox` fields. It needs no backend. New in 0.6.0.

Bundles:

- `Raticode rattish export WORKFLOW -f nightly.raticode` writes a bundle. `Raticode rattish import nightly.raticode --project PATH` adds it to a project.
- The backend only writes into folders it trusts: its own data folder or a project the desktop approved. Any other folder is refused with exit 5.
- Export needs the backend on the same computer. The backend writes the bundle into its data folder first, and the CLI moves it to `-f`.

## Organizations

::: info Feature Preview
Organizations are experimental. The backend serves them only when it runs with `RATICODE_EXPERIMENTAL_ORGANIZATIONS=1`; otherwise these commands exit 5.
:::

`Raticode org` reads and configures organizations. It can't start, pause or resume one, decide consent requests, or approve, review or reopen a ticket's work. Those stay in the [Organizations tab](../advanced/organizations.md), where you can review what you're approving.

```bash
Raticode org list
Raticode org show ORG_ID
Raticode org history ORG_ID
Raticode org events ORG_ID --after 120
Raticode org projects ORG_ID
Raticode org employees list ORG_ID
Raticode org employees hold ORG_ID builder
Raticode org employees release ORG_ID builder
Raticode org employees account ORG_ID builder work      # or: default
```

`release` refuses a worker that runs without a sandbox (`danger-full-access` or `bypassPermissions`), just as the phone does. Release those in the app, where you can see their access.

Tickets and knowledge:

```bash
Raticode org tickets list ORG_ID --status todo
Raticode org tickets create ORG_ID --title "Fix flaky test" --request-id t-123
Raticode org tickets update ORG_ID TICKET_ID --expected-revision 3 --set status=todo
Raticode org tickets comment ORG_ID TICKET_ID --body "Seen again in CI"
Raticode org tickets changes ORG_ID TICKET_ID
Raticode org tickets run-there ORG_ID TICKET_ID
Raticode org knowledge search ORG_ID "release checklist"
Raticode org knowledge read ORG_ID ENTRY_ID
Raticode org knowledge save ORG_ID -f decision.json
```

- `org tickets run-there` is **Run there** on a ticket that waits for room while a paired desktop has room for it: `org show --output text` lists each suggestion. It only points the ticket at the fleet execution target you already made on that desktop for the ticket's worker. Without one it refuses with what to grant. See [Run waiting work on a paired desktop](../rem/home.md#run-waiting-work-on-a-paired-desktop).
- A new ticket starts in backlog. It runs once it reaches `todo`: you move it, or a free worker [pulls it](../advanced/organizations.md#pull-from-backlog) when backlog pulls are on.
- `--request-id` makes `create` and `comment` safe to retry.
- `update` takes the ticket's `revision` and exits 4 if the ticket changed since.

The work schedule:

```bash
Raticode org schedule show ORG_ID > schedule.json
Raticode org schedule apply ORG_ID -f schedule.json
```

The file holds the blocks, the time zone and the revision it was read at. If the organization changed in between, `apply` exits 4 and changes nothing.

Whether free workers pull accepted Backlog tickets into To do:

```bash
Raticode org backlog-pulls ORG_ID          # show the setting
Raticode org backlog-pulls ORG_ID --off    # leave Backlog for you to move
Raticode org backlog-pulls ORG_ID --on
```

[Auto mode](../advanced/organizations.md#auto-mode), where Rem decides the organization's
requests. Only you can turn it on or off: the command refuses inside a Raticode agent turn, and
`--on` needs `--yes` to confirm what it allows.

```bash
Raticode org auto-mode ORG_ID              # show it, why it stopped, and Rem's recent decisions
Raticode org auto-mode ORG_ID --on --yes
Raticode org auto-mode ORG_ID --off
```

Credentials for workers and webhooks live in the system keychain. On a server without one, put each in an `org-secret-ACCOUNT` file in the backend's [secrets folder](./headless.md#secrets-without-a-keychain) and add a secret reference with that account (from 0.5.5):

```bash
Raticode api POST /organizations -f action=secret_reference -f organizationId=ORG_ID \
  -f params.id=github -f params.account=github-token
```

## Swarms

`Raticode swarm` reads swarms and changes their settings. It never starts one, sends it messages or changes a running one. Swarms belong to a project folder, so every command takes `--project`.

```bash
Raticode swarm list --project ~/code/app
Raticode swarm show SWARM_ID --project ~/code/app
Raticode swarm history SWARM_ID --project ~/code/app
Raticode swarm export SWARM_ID --project ~/code/app -f reviewers.json
Raticode swarm apply --project ~/code/app -f reviewers.json
Raticode swarm delete SWARM_ID --project ~/code/app --yes
```

- `apply` updates the swarm named in the file, or creates one when the file has no `swarmId`.
- Pause a running swarm in the app before changing its settings.
- `delete` removes the swarm and its run records. Its git worktrees and branches stay.

## Second Brain

```bash
Raticode brain list
Raticode brain documents --brain BRAIN_ID
Raticode brain search "release checklist" --brain BRAIN_ID --mode hybrid
Raticode brain read DOCUMENT_ID
Raticode brain save --brain BRAIN_ID --title "Deploy notes" -f notes.md
Raticode brain projects --brain BRAIN_ID
```

- `save` writes the text into the brain's `Inbox` folder as a new note and waits until it's indexed.
- `--url` records where the text came from. The backend doesn't fetch it.
- Adding a brain folder needs the desktop's folder approval, so do it in the app.
- Notes are plain files, so a script on the same computer can also read them directly.

## Providers, models and usage

```bash
Raticode provider list
Raticode provider models claude_code
Raticode provider status codex
Raticode provider set codex defaultModel=MODEL_ID 'deniedModels=["OTHER_MODEL_ID"]'
Raticode usage show --range 7d
Raticode usage show --range 1y --output text
Raticode usage show --range 30d --by account --output text
Raticode usage show --from 2026-01-01 --to 2026-03-31
Raticode usage refresh
Raticode usage alerts
Raticode usage alerts --off
Raticode usage alerts --on --first 75 --second 90
Raticode usage verified --range all --output text
```

- `provider status` shows whether a provider is signed in. Signing in opens a browser, so do it on that computer with the provider's own CLI, or, for a Codex or Grok [account](#provider-accounts), run `Raticode provider account login NAME --device-auth` and finish it from any device.
- `usage show` reads what the backend has recorded and asks no provider. `--range` takes `24h`, `7d`, `30d` (the default), `3mo`, `6mo`, `1y`, `5y` or `all`; `--from` and `--to` give a custom range, both dates included. The JSON has `range`, the `activity` totals and `series`, the reported tokens per hour, day, week, month or year and provider, as in [Tokens over time](../rem/usage.md#tokens-over-time). Buckets follow the backend computer's time zone. An account with a weekly window also has `weekly_burndown`: the window's `starts_at` and `resets_at`, this week's `readings` (each `observed_at` and `used_percent`, saved only when the reading changed), `reading_count`, and `pace` with `state` (`on_pace`, `runs_out`, `not_enough` or `used_up`), `runs_out_at` and `text`, as in [Weekly pace](../rem/usage.md#weekly-pace). The text output adds a **Weekly allowance pace** table. The older `--days 1|7|30|90` still works; 90 means `3mo`. The period options and the pace are new in 0.6.0.
- `usage show --by account` splits the text output's tokens per bucket by [account](../rem/usage.md#by-provider-or-by-account), such as `claude_code (work)`, instead of by provider (`--by provider`, the default). The JSON always has both: `series.providers` with each bucket's `providers`, and `series.accounts` (each with `key`, `provider`, `profile`, `name`, `total_tokens`, `calls` and its chart `style`) with each bucket's `accounts`, keyed by `key`. *New in 0.6.0.*
- `usage show` also includes `api_cost`, the period's [estimated cost at API list prices](../rem/usage.md#cost-at-api-prices) per provider and in total. A cost Raticode can't work out is `null`, never 0, and `lower_bound` marks a total that leaves some calls out. Its text output ends with the same estimate. *New in 0.6.0.*
- `usage refresh` asks each signed-in provider for current numbers, so it runs their CLIs.
- `usage verified` shows [Verified work](../rem/usage.md#verified-work) for a period: how many of Rem's tickets passed review first time, repairs per ticket, blocked tickets by cause, how often Raticode's project check failed on the first try, how many check runs were flaky (failed, then passed on a rerun of the same commit; `project_check.flaky`), tokens per accepted ticket and how often you stepped in. It takes the same `--range`, `--from` and `--to` as `usage show`, reads `GET /api/usage/verified-work`, and its JSON has `accepted`, `first_pass`, `first_pass_percent`, `repairs`, `repairs_per_ticket`, `finished`, `blocked`, `blocked_causes`, `project_check`, `tokens`, `step_ins`, `line` and `trend`. Each `blocked_causes` entry has a `cause`, its `label` and `tickets`; a failed task's cause is `no_activity`, `turn_limit`, `usage_limit`, `provider_error`, `not_started` or `failed` (other failure). `usage show` carries the same numbers as `verified_work`. *New in 0.6.0.*
- `usage alerts` shows or changes the [limit alerts](../rem/usage.md#limit-alerts): `--on` or `--off`, and `--first` and `--second` for the two levels in percent (80 and 95 by default; the second must be higher). A level you leave out keeps its saved value. Changing them is refused inside an agent turn. *New in 0.6.0.*
- Without a keychain, usage reporting credentials come from `usage-PROVIDER-FIELD` files in the backend's [secrets folder](./headless.md#secrets-without-a-keychain), such as `usage-openai_api-admin_api_key` (from 0.5.5).
- The [provider profile](./cli.md#provider-profiles-and-health) commands are unchanged and still edit the profile store directly.

### Provider accounts

*New in 0.6.0.* Each [account](../rem/providers.md#several-accounts) is its own configuration folder of a provider's app. Claude Code (`claude_code`), Codex (`codex`) and Grok (`grok`) have accounts; Claude Code is the default for `--provider` when adding one.

```bash
Raticode provider account add work --copy-setup
Raticode provider account login work
Raticode provider account login personal --device-auth
Raticode provider account list
Raticode provider account add personal --provider codex --copy-setup
Raticode provider account list --provider claude_code --format json
Raticode provider account status work
Raticode provider account rename work office
Raticode provider account import-setup office --from ~/team-claude-setup
Raticode provider account copy-setup office --check
Raticode provider account copy-setup office
Raticode provider account rm office
```

- `add` makes the account's folder and runs the provider's own sign-in for it (`claude auth login` for Claude Code, `codex login` for Codex, `grok login` for Grok); `--no-sign-in` skips that. `--copy-setup` copies only the provider's shared setup, never a sign-in: for Codex, `AGENTS.md`, skills and `config.toml` without its sign-in settings. `--copy-setup-from FOLDER` copies the same items from another folder instead of the provider's own. `--provider` picks the provider and defaults to `claude_code`. Approve in the browser the provider opens. That first sign-in is the only manual step for each account.
- `login` signs an account in again, for example after **needs sign-in**. If the provider asks for a code, paste it into its prompt in that terminal. If the provider refuses the sign-in, its own message is printed there. `login --device-auth` (Codex and Grok) prints a link and a one-time code instead of opening a browser, so you can finish the sign-in on any device, as the phone's [Sign in](../rem/providers.md#sign-an-account-in-from-the-phone) does. If ChatGPT has device code sign-in turned off, it also says where to turn it on.
- `list` shows every provider's accounts, or one provider's with `--provider`. `list` and `status` show **signed in** or **needs sign-in** from the provider's own status command (`claude auth status`, Codex's app-server `account/read`, or whether Grok's `_x.ai/billing` answers), the email and plan, and a warning when two accounts of one provider are the same subscription. `list --refresh` asks again instead of using the last two minutes' answer.
- `login`, `status`, `rename` and `rm` find the account by name; with `--provider` they refuse an account of another provider.
- `import-setup` copies the shared setup into an account you already have, from the provider's own folder or `--from FOLDER`. It keeps anything the account already has and prints what it copied (`--format json` gives `{"account", "copied"}`).
- `copy-setup` is **Copy my setup again**: it compares the setup items the account has with the provider's own folder (or `--from FOLDER`) and copies again only the ones that differ, keeping files and settings only the account has. Codex's `config.toml` goes through the same sign-in cleaner as `--copy-setup`. Sign-ins, sessions and history are never touched. `--check` only lists the items that differ (`--format json` gives `{"account", "changed"}`; without `--check`, `{"account", "copied"}`).
- `rm` signs the account out with the provider's app, then deletes its folder. `provider profile rm` refuses an account.
- The backend's `/api/provider/accounts` takes the same actions, with `provider` in the request: `create` (with `copySetup` and an optional `setupFrom` folder), `sign_in` (with `device: true` for the link-and-code sign-in, whose state carries `url`, `code`, `refused` and `help`), `cancel_sign_in`, `check`, `rename`, `import_setup` (optional `setupFrom`; the reply lists `copied`), `copy_setup` (the same, copying again only the items that differ) and `remove`. Each account in the reply has `setup_changes`, the items that differ, when its provider has a shared setup. `/api/provider/claude-accounts` still works for Claude Code accounts.

### Jev classifier

```bash
Raticode provider jev status
Raticode provider jev set-key
Raticode provider jev check
Raticode provider jev disable
Raticode provider jev enable
Raticode provider jev remove-key
```

- [Jev](../workflows/jev.md) is the classifier behind the [jev node](../nodes/jev.md). It isn't a coding provider, so `provider list` doesn't show it.
- `set-key` asks for the key without showing it, or reads one line from standard input with `--stdin`. The key never goes on the command line, where other accounts on the computer could see it. The backend keeps it in the system keychain.
- On a server with no keychain, `set-key` fails with a message naming `--secrets-dir`. Put the key in a file named `jev-key` in the backend's [secrets folder](./headless.md#secrets-without-a-keychain) instead (from 0.5.5). `status` then says `Key: read from the secrets folder (jev-key)` and the JSON has `"keySource": "secrets folder"`. `remove-key` can't delete that file; replace or remove it yourself.
- `check` makes one call to Jev to test the key and refresh the model list. `status` makes none.
- `disable` stops every jev node from calling Jev and keeps the key. `remove-key` deletes it from the keychain.

### Workflow secrets

```bash
Raticode rattish secrets set SERVICE_TOKEN
printf '%s' "$VALUE" | Raticode rattish secrets set SERVICE_TOKEN --stdin
Raticode rattish secrets list --format json
Raticode rattish secrets remove SERVICE_TOKEN
```

- These save the values `secret["NAME"]` reads in the system keychain. They work without a running backend, the same as the Studio's **Workflow settings > Secrets** (from 0.5.5). See [Secrets](../workflows/inputs.md#secrets).
- `set` asks for the value without showing it, or reads all of standard input with `--stdin` (one trailing newline is dropped). The value never goes on the command line.
- `list` shows names, when each was saved, and whether an environment variable with the same name is also set. No command prints a value.
- On a server with no keychain, `set` fails with a message naming `--secrets-dir`. Put the value in a file named `workflow-secret-NAME` in the backend's [secrets folder](./headless.md#secrets-without-a-keychain) instead (from 0.5.5). `secret["NAME"]` reads it when the keychain has no saved value, before the environment variable. `list` shows only keychain names; `Raticode api GET /rattish/secrets` also lists the folder's names under `folder`.
- `set` and `remove` refuse inside a Rem or worker turn (exit code 5). `list` works there.

## Social posts

[Social posts](../advanced/posts.md) is an experimental Feature Preview, new in 0.6.0. It is untested and may change. The CLI can propose posts and read the queue, never approve or publish:

```bash
Raticode social propose --file post.json   # propose a post for your review
Raticode social list                       # the queue, newest first
Raticode social show POST_ID               # one post with its variants and activity
Raticode social accounts                   # connected accounts, without logins
Raticode social templates                  # your post templates and the starters, with their blanks
Raticode social times --for x,linkedin     # the next good times to post, in this computer's time zone
```

- `propose` reads the post as JSON from `--file`, or from standard input with `--file -`. [How Rem, tasks and workflows propose](../advanced/posts.md#how-rem-tasks-and-workflows-propose) describes the fields. Inside a Rem or worker turn the proposal is recorded as coming from that turn, not from you. To fill in a [template](../advanced/posts.md#templates), send `template` (its id) and `fields` (a value for each blank) instead of `variants`.
- `times` takes account ids or platform names in `--for` and a time zone name in `--tz`. It skips times within 3 hours of other posts planned for the same accounts. The times are [general guidance](../advanced/posts.md#best-times), not your own audience's data.
- Approving, publishing, sending again and connecting accounts happen in the Posts plugin or on your phone's Posts screen (`social-approval`), so a script or agent can never put a public post out. Saving, changing and deleting templates also happen in the Posts plugin.

## Projects, devices, computers and live threads

```bash
Raticode projects list
Raticode projects setup PATH
Raticode devices list
Raticode computers list
Raticode computers set 4
Raticode computers placement
Raticode computers place org:ORGANIZATION_ID manual --computer ID --shared ID
Raticode computers order ID ID
Raticode threads follow CONVERSATION_ID TURN_ID
Raticode threads steering CONVERSATION_ID
Raticode threads steer CONVERSATION_ID TURN_ID --text "Use the staging database"
Raticode threads stop CONVERSATION_ID TURN_ID
```

- `projects list` lists the project folders that hold registered workflows.
- `projects setup PATH` shows a project's [worktree setup](../studio/worktree-setup.md) and whether you approved it. Without a path it lists setups waiting for approval.
- `devices list` shows paired phones and computers. Pairing stays in Settings.
- `computers list` shows each computer Raticode can run work on (this one and each paired desktop) with its state, worker limit, running workers, CPU and memory pressure, and room for new workers. With `--output text`, a **Running workers** list below the table gives each computer's workers by kind, such as `2 organization workers, 1 Rem task, 1 swarm turn`, and names each of this computer's workers. JSON has the same as `running_by_kind` and `running_work`. [Computers and their load](../rem/home.md#computers-and-their-load) explains the numbers.
- `computers set N` changes this computer's worker limit, the same number as Settings > Computers > **Worker limit**: 0 to 16, where 0 picks one from the cores and memory. Change a paired desktop's limit on that desktop.
- `computers placement` shows the strategy (overflow, balanced or manual) and assigned computers for Rem and each organization. `computers place rem|org:ID STRATEGY` changes one; manual takes one or more `--computer` IDs, and `--shared` marks a computer that another manual placement may also have. [Where work runs](../rem/home.md#where-work-runs) explains the rules.
- `computers order ID...` sets your order of computers, first to fill first, the same order as the arrows in Settings > Computers. Computers you leave out come after the ones you name. An ID that isn't one of your computers is refused.
- `threads follow` prints a turn's events as they arrive and ends when the turn does.
- The thread list lives in the desktop window, not the backend, so the CLI can't list or start threads yet. Moving that list into the backend is a planned follow-up.
- For Rem's own work threads, use `Raticode rem thread`.

## Settings as files

Each kind of setting has a pair of commands. Write a file with the first, keep it in version control or edit it, and apply it with the second:

| Settings | Write a file | Apply a file |
| --- | --- | --- |
| Organization configuration | `Raticode org config ORG_ID -f org.json` | `Raticode org apply -f org.json` |
| Organization package (Agent Companies format) | `Raticode org export ORG_ID -f org.zip` | `Raticode org import org.zip` |
| Work schedule | `Raticode org schedule show ORG_ID` | `Raticode org schedule apply ORG_ID -f schedule.json` |
| Swarm | `Raticode swarm export SWARM_ID --project P -f swarm.json` | `Raticode swarm apply --project P -f swarm.json` |
| Workflow triggers | `Raticode rattish triggers export WORKFLOW -f triggers.json` | `Raticode rattish triggers apply WORKFLOW -f triggers.json` |
| Workflow | `Raticode rattish export WORKFLOW -f w.raticode` | `Raticode rattish import w.raticode --project P` |
| Rem's settings | `Raticode rem settings export` | `Raticode rem settings apply -f rem.json` |
| Self-work | `Raticode selfwork export` | `Raticode selfwork apply -f self-work.json` |

- `org config` records the revision it read, and `org apply` sends it back. Applying an old file after someone else changed the organization fails with exit 4 instead of undoing their change.
- A file without an `organizationId` creates a new, paused organization. Applying a configuration never starts anything.
- Without `-f`, the export commands put the document in the envelope's `data`. The apply commands accept the plain document or a saved envelope.

## Decisions that stay with you

The backend treats every request that carries your token as you. So inside a Rem or worker turn, the CLI refuses these commands with exit 5 (error code `user_only`) and sends nothing:

- `rattish approve` and `reject`, and `workflow approve` and `reject`
- `rattish runs land` and `discard`
- `rattish triggers approve`, `rotate-token`, `webhook-port PORT`, `budget` with a limit, `resume --all`, `--run-edits-without-approval` on `add` or `update`, and a trigger's daily limits or Time limit raised or removed with `add` or `update` (lowering them still works)
- `rattish secrets set` and `remove`
- `org apply`, `org employees release`, Auto mode changes and sending a ticket to another computer
- `swarm apply` and `delete`
- `provider set`
- `provider jev set-key`, `check`, `remove-key`, `enable` and `disable`
- Rem's settings, a ticket marked done by hand, approving or declining proposals, project choices and self-work settings
- `rem mcp add --replace`, `rem mcp remove`, `rem skill remove` and `rem restore-db` (adding a new MCP server or skill still works; it stays off until you turn it on in Thread tools)
- usage alerts and computer settings
- `tokens create` and `revoke`

`Raticode api` follows the same rule inside a Rem or worker turn:

- It sends only what a limited [agent token](./headless.md#agent-tokens) may do.
- It never sends an organization's start, pause, resume, consent decision, launch, review or credential action, whichever token it holds.
- Everything else is refused with exit 5 (`user_only`) before anything is sent. Run those from your own terminal or in the app.

Inside such a turn the CLI also refuses your owner token, whether it comes from the data folder, `RATICODE_TOKEN`, `RATICODE_TOKEN_FILE` or `--token-file`.

::: warning Guards, not a boundary
A worker runs as your account. Don't give agents a token that can act as you.
:::

## What the CLI doesn't do

Some of the app's endpoints have no CLI command on purpose. Each has a reason:

| Reason | What it covers |
| --- | --- |
| `org-run-control` | Starting, pausing or resuming an organization or its heartbeats, and deciding consent requests. This needs your separate authorization, so it stays in the Organizations tab. |
| `consent-gated` | Changes the app asks your consent for: archiving an organization, restoring an earlier revision of one, routines, webhooks, GitHub imports and execution decisions. |
| `user-decision` | Your decisions on tickets: approving a plan, accepting or sending back a review, landing a ticket's branch, requesting changes from its patch, and reopening a ticket. Each one lets agents start work or changes your branches, so make them in the ticket's sheet on the Kanban board or in the Organizations tab. |
| `runs-agents` | Starting or steering agent work outside a workflow run: starting, controlling or messaging a swarm, and an organization's wake, launches, checks and retries. |
| `worker-tools` | Tools an organization worker uses during its own run, such as claims, artifacts and memory. |
| `thread-list` | Starting, listing, searching, archiving or deleting threads, from the app or a phone. The thread list lives in the desktop window; moving it into the backend is a planned follow-up. |
| `folder-approval` | Opening a project or a Second Brain folder. The backend accepts these only with the desktop's folder approval. |
| `desktop-auth` | The desktop's own security handshakes (folder grants, trusted folders, the window's session token). |
| `pairing` | Pairing and paired-device actions. They need the Settings screen. |
| `provider-sign-in` | Provider sign-in, which opens a browser. |
| `credentials` | Saving account credentials or secrets. Credentials never go through command-line arguments. |
| `webhook-ingress` | Webhook endpoints that outside services call. |
| `window-ui` | The desktop window's own screens: attachments, previews, voice input, the studio editor, themes, its terminal, clipboard, logs and developer tools. Scripts edit `workflow.rattish` and use `Raticode rattish` instead. |
| `file-explorer` | The desktop window's file explorer, editor and project search. Scripts read and write project files directly. |
| `window-settings` | App settings the desktop window owns, such as voice tidy, commit messages, report format, the conversation archive folder, the data folder and Feature Preview choices. |
| `app-updates` | Updating the desktop app. Update the CLI with your package manager or a new release download. |
| `setup-approval` | Approving or stopping a project's worktree setup, which then runs commands as you in every new worktree. Approve it in the app, where its commands are shown in full. |
| `social-approval` | Approving, publishing, sending again or rescheduling a [social post](../advanced/posts.md), and connecting, changing or disconnecting its accounts. A post is public, so each one is your click in the Posts plugin. |
| `phone-only` | The phone's own screens: overview cards, drafts, its file browser, media and developer log. |
| `device-link` | The encrypted link between paired devices: syncing, file transfer, cancelling a phone's request and remote jobs sent to another desktop. A script on this computer talks to the backend directly. |
| `protocol-reply` | Replies, snapshots and progress updates the desktop sends back to a phone. |
| `browser` | Raticode's browser tabs, which need a display. |
| `source-control` | Git. Use git directly. |

Anything else the backend serves that has no command of its own is still reachable with `Raticode api`, such as log retention (`GET /retention`).

## Every backend command

| Command | Guide |
| --- | --- |
| `Raticode api`, `Raticode wait` | [CLI reference](./cli.md#talk-to-a-running-backend) |
| `Raticode rattish show`, `Raticode rattish start`, `Raticode rattish stop`, `Raticode rattish resume`, `Raticode rattish wait` | [Workflows](#workflows) |
| `Raticode rattish runs list`, `Raticode rattish runs rerun`, `Raticode rattish runs changes`, `Raticode rattish runs land`, `Raticode rattish runs discard`, `Raticode rattish runs failure`, `Raticode rattish logs`, `Raticode rattish events` | [Workflows](#workflows) |
| `Raticode rattish approvals`, `Raticode rattish approve`, `Raticode rattish reject` | [Workflows](#workflows) |
| `Raticode rattish export`, `Raticode rattish import`, `Raticode rattish triggers export`, `Raticode rattish triggers apply` | [Workflows](#workflows) |
| `Raticode org list`, `Raticode org show`, `Raticode org history`, `Raticode org events`, `Raticode org projects` | [Organizations](#organizations) |
| `Raticode org config`, `Raticode org apply`, `Raticode org export`, `Raticode org import` | [Settings as files](#settings-as-files) |
| `Raticode org employees list`, `Raticode org employees hold`, `Raticode org employees release`, `Raticode org employees account` | [Organizations](#organizations) |
| `Raticode org schedule show`, `Raticode org schedule apply`, `Raticode org backlog-pulls`, `Raticode org auto-mode` | [Organizations](#organizations) |
| `Raticode org tickets list`, `Raticode org tickets create`, `Raticode org tickets update`, `Raticode org tickets comment`, `Raticode org tickets changes`, `Raticode org tickets run-there` | [Organizations](#organizations) |
| `Raticode org knowledge search`, `Raticode org knowledge read`, `Raticode org knowledge save` | [Organizations](#organizations) |
| `Raticode swarm list`, `Raticode swarm show`, `Raticode swarm history`, `Raticode swarm export`, `Raticode swarm apply`, `Raticode swarm delete` | [Swarms](#swarms) |
| `Raticode brain list`, `Raticode brain documents`, `Raticode brain search`, `Raticode brain read`, `Raticode brain save`, `Raticode brain projects` | [Second Brain](#second-brain) |
| `Raticode provider list`, `Raticode provider models`, `Raticode provider status`, `Raticode provider set`, `Raticode usage show`, `Raticode usage refresh`, `Raticode usage alerts`, `Raticode usage verified` | [Providers, models and usage](#providers-models-and-usage) |
| `Raticode provider jev status`, `Raticode provider jev set-key`, `Raticode provider jev check`, `Raticode provider jev remove-key`, `Raticode provider jev enable`, `Raticode provider jev disable` | [Jev classifier](#jev-classifier) |
| `Raticode social propose`, `Raticode social list`, `Raticode social show`, `Raticode social accounts`, `Raticode social templates`, `Raticode social times` | [Social posts](#social-posts) |
| `Raticode projects list`, `Raticode projects setup`, `Raticode devices list`, `Raticode computers list`, `Raticode computers set`, `Raticode computers placement`, `Raticode computers place`, `Raticode computers order` | [Projects, devices, computers and live threads](#projects-devices-computers-and-live-threads) |
| `Raticode threads follow`, `Raticode threads steering`, `Raticode threads steer`, `Raticode threads stop` | [Projects, devices, computers and live threads](#projects-devices-computers-and-live-threads) |
| `Raticode rem say`, `Raticode rem messages`, `Raticode rem search`, `Raticode rem follow`, `Raticode rem notices` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem settings show`, `Raticode rem settings export`, `Raticode rem settings apply` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem memories list`, `Raticode rem memories add`, `Raticode rem memories edit`, `Raticode rem memories forget`, `Raticode rem memories mode`, `Raticode rem memories undo` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem decisions list`, `Raticode rem decisions show`, `Raticode rem decisions use`, `Raticode rem decisions change`, `Raticode rem decisions edit`, `Raticode rem decisions replace`, `Raticode rem decisions reject`, `Raticode rem decisions reopen`, `Raticode rem decisions forget`, `Raticode rem decisions retire`, `Raticode rem decisions add`, `Raticode rem decisions scan`, `Raticode rem decisions paths`, `Raticode rem decisions confirm-edit`, `Raticode rem decisions cancel-edit` | [Decisions from the CLI](./cli-rem.md#decisions) |
| `Raticode rem tasks list`, `Raticode rem tasks show`, `Raticode rem tasks create`, `Raticode rem tasks edit`, `Raticode rem tasks run`, `Raticode rem tasks pause`, `Raticode rem tasks resume`, `Raticode rem tasks delete`, `Raticode rem tasks wait`, `Raticode rem tasks wake`, `Raticode rem tasks make-workflow`, `Raticode rem tasks switch-over` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem runs show`, `Raticode rem runs pause`, `Raticode rem runs resume`, `Raticode rem runs cancel`, `Raticode rem runs acknowledge`, `Raticode rem runs acknowledge-all`, `Raticode rem runs retry-review`, `Raticode rem runs wake`, `Raticode rem runs run-there`, `Raticode rem thread show`, `Raticode rem thread send`, `Raticode rem context` | [Rem from the CLI](./cli-rem.md) |
| `Raticode tickets list`, `Raticode tickets show`, `Raticode tickets create`, `Raticode tickets edit`, `Raticode tickets move`, `Raticode tickets assign`, `Raticode tickets comment`, `Raticode tickets comments`, `Raticode tickets approve`, `Raticode tickets decline`, `Raticode tickets projects` | [Tickets from the CLI](./cli-rem.md#tickets) |
| `Raticode selfwork status`, `Raticode selfwork report`, `Raticode selfwork start`, `Raticode selfwork on`, `Raticode selfwork off`, `Raticode selfwork window`, `Raticode selfwork stop-when`, `Raticode selfwork objectives`, `Raticode selfwork set`, `Raticode selfwork export`, `Raticode selfwork apply` | [Self-work from the CLI](./cli-rem.md) |

## Keeping the CLI in step with the app

`docs/cli-parity.json` in the repository lists what the app can do and names the CLI command that covers each part, or the reason above that it has none:

- every backend endpoint, with one entry per action where a field in the request picks the action, such as each ticket change, each paired-device setting and each swarm control;
- every operation and message type a phone can send, and every reply the desktop sends back;
- every request the desktop window sends to its own Electron process, so features that live only in the window are listed too.

A test reads the backend's routing code, the phone protocol and the window's request table. It fails when:

- something new has neither a command nor a reason,
- the file names a command that doesn't exist, or
- a backend command is missing from this page.

So a new app feature adds its command or its reason in the same change.

The [CLI command index](./cli-commands.md) is generated from the CLI itself, and the same test fails when it no longer matches the commands, their options or their help.
