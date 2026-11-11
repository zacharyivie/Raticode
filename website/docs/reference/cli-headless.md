---
title: Headless CLI for workflows, Organizations and more
description: Script workflow runs, Organizations, swarms, Second Brain, providers, usage and live threads through a running Raticode backend, and keep their settings in files.
pageClass: docs-layout
---

# Headless CLI for workflows, Organizations and more

::: info New in 0.5.3
These commands arrived in Raticode 0.5.3. Earlier versions don't have them.
:::

These commands let a script, a CI job or a remote box use Raticode without the desktop window. Each one sends a request to a running backend, the same API the desktop window and the phone use, so a run you start here appears in the app's Runs and the app's Stop button reaches it. The CLI never opens the backend's databases itself. The CLI finds a [headless backend](./headless.md) on its own. It can't find the desktop app's backend yet, because the app doesn't publish its address or token, so these commands can't reach it while only the desktop app is running.

There are no menus, prompts or screens. A command makes one request, or a few, and prints the result. When something needs a decision from you, such as starting an organization, the command doesn't offer it and the app keeps it.

[Rem, tickets and self-work from the CLI](./cli-rem.md) covers `Raticode rem`, `Raticode tickets` and `Raticode selfwork`. The [CLI reference](./cli.md#talk-to-a-running-backend) covers `Raticode api`, `Raticode wait`, how the CLI finds the backend and its token, and the exit codes.

## Before you start

On a server or in a container, start a [headless backend](./headless.md). It writes its address and token where these commands find them with no setup:

```bash
Raticode serve --headless &
Raticode org list
```

Every command here takes the same connection options as `Raticode api`: `--endpoint`, `--token-file` and `--data-dir`. None of them accepts the token itself on the command line. Inside a Rem worker session the CLI doesn't pick up the owner token, so give agents a limited [agent token](./headless.md#agent-tokens) instead.

Output is one JSON envelope on stdout by default: `{"ok", "command", "data", "error", "warnings"}`. Messages for people go to stderr. Add `--output text` for a readable table. Exit codes are the [backend client's codes](./cli.md#output-and-exit-codes): 0 done, 1 failed, 2 invalid input, 3 not found, 4 conflict or stale revision, 5 not allowed, 6 no backend, 7 waiting on you, 8 timed out.

## Workflows

Most `Raticode rattish` commands work on `workflow.rattish` files, and `rattish run` runs a workflow inside the CLI process. The commands below ask the backend instead. Their help starts with "(Backend)".

```bash
Raticode rattish list --format json
Raticode rattish show nightly-report --source
Raticode rattish start nightly-report --input day='"2026-11-10"' --wait --timeout 30m
Raticode rattish runs nightly-report --status error
Raticode rattish logs nightly-report RUN_ID
Raticode rattish events nightly-report RUN_ID
Raticode rattish stop nightly-report --run RUN_ID
Raticode rattish resume nightly-report RUN_ID --from-node build --wait
```

`rattish list` reads the workflow registry from the data folder; the others need the backend. `start` starts the run and returns its ID straight away. Its nodes really run: agent nodes call providers, command nodes run commands. `--dry-run` only shows the plan. With `--wait`, the exit code is the run's: 0 it succeeded, 1 it failed or was stopped, 7 it stopped at an approval gate, 8 the timeout passed first. The run keeps going after a timeout. `--follow` waits too and prints each run event as it happens, one `{"kind": "event", "data": ...}` line each, then the result line. `Raticode rattish wait WORKFLOW RUN_ID` waits for a run you started earlier, with the same exit codes.

Approval gates:

```bash
Raticode rattish approvals nightly-report --pending
Raticode rattish approve nightly-report RUN_ID approve --notes "Checked the diff"
Raticode rattish reject nightly-report RUN_ID approve
```

The third argument is the approval node's ID. Inside a Rem or worker turn the CLI refuses `approve` and `reject` (exit 5, error code `user_only`), so a workflow can't approve its own gate.

[Triggers](../workflows/triggers.md) keep their `Raticode rattish triggers` commands. Two more move a workflow's triggers in and out of a file through the backend:

```bash
Raticode rattish triggers export nightly-report -f triggers.json
Raticode rattish triggers apply nightly-report -f triggers.json
```

`apply` matches triggers by kind and label. It creates the missing ones and updates the ones that differ, so applying the same file twice changes nothing. `--prune` also removes triggers the file doesn't list. Trigger files never hold IDs or webhook tokens. A new webhook trigger gets a fresh token, which `apply` prints once.

`Raticode rattish export WORKFLOW -f nightly.raticode` writes a bundle, and `Raticode rattish import nightly.raticode --project PATH` adds it to a project. The backend only writes into folders it trusts: its own data folder or a project the desktop approved. A different folder is refused with exit 5. Export needs the backend on the same computer, because the backend writes the bundle into its data folder first and the CLI moves it to `-f`.

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
```

`release` refuses a worker that runs without a sandbox (`danger-full-access` or `bypassPermissions`), just as the phone does. Release those in the app, where you can see their access.

Tickets and knowledge:

```bash
Raticode org tickets list ORG_ID --status todo
Raticode org tickets create ORG_ID --title "Fix flaky test" --request-id t-123
Raticode org tickets update ORG_ID TICKET_ID --expected-revision 3 --set status=todo
Raticode org tickets comment ORG_ID TICKET_ID --body "Seen again in CI"
Raticode org tickets changes ORG_ID TICKET_ID
Raticode org knowledge search ORG_ID "release checklist"
Raticode org knowledge read ORG_ID ENTRY_ID
Raticode org knowledge save ORG_ID -f decision.json
```

A new ticket starts in backlog, so a running organization doesn't pick it up until you move it to `todo`. `--request-id` makes `create` and `comment` safe to retry. `update` takes the ticket's `revision` and exits 4 if the ticket changed since.

The work schedule:

```bash
Raticode org schedule show ORG_ID > schedule.json
Raticode org schedule apply ORG_ID -f schedule.json
```

The file holds the blocks, the time zone and the revision it was read at. If the organization changed in between, `apply` exits 4 and changes nothing.

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

`apply` updates the swarm named in the file, or creates one when the file has no `swarmId`. A running swarm has to be paused in the app before its settings change. `delete` removes the swarm and its run records; its git worktrees and branches stay.

## Second Brain

```bash
Raticode brain list
Raticode brain documents --brain BRAIN_ID
Raticode brain search "release checklist" --brain BRAIN_ID --mode hybrid
Raticode brain read DOCUMENT_ID
Raticode brain save --brain BRAIN_ID --title "Deploy notes" -f notes.md
Raticode brain projects --brain BRAIN_ID
```

`save` writes the text into the brain's `Inbox` folder as a new note and waits until it's indexed. `--url` records where the text came from; the backend doesn't fetch it. Adding a brain folder needs the desktop's folder approval, so do that in the app. Note files are plain files, so a script on the same computer can also read them directly.

## Providers, models and usage

```bash
Raticode provider list
Raticode provider models claude_code
Raticode provider status codex
Raticode provider set codex defaultModel=MODEL_ID 'deniedModels=["OTHER_MODEL_ID"]'
Raticode usage show --days 7
Raticode usage refresh
```

`provider status` shows whether a provider is signed in. Signing in opens a browser, so do it on that computer with the provider's own CLI. `usage show` reads what the backend has recorded and asks no provider. `usage refresh` asks each signed-in provider for current numbers, so it runs their CLIs. The [provider profile](./cli.md#provider-profiles-and-health) commands are unchanged and still edit the profile store directly.

## Projects, devices and live threads

```bash
Raticode projects list
Raticode devices list
Raticode threads follow CONVERSATION_ID TURN_ID
Raticode threads steering CONVERSATION_ID
Raticode threads steer CONVERSATION_ID TURN_ID --text "Use the staging database"
Raticode threads stop CONVERSATION_ID TURN_ID
```

`projects list` lists the project folders that hold registered workflows. `devices list` shows paired phones and computers; pairing stays in Settings. `threads follow` prints a turn's events as they arrive and ends when the turn does.

The list of threads lives in the desktop window, not the backend, so the CLI can't list threads or start one yet. Moving that list into the backend is a planned follow-up. Rem's own work threads are covered by `Raticode rem thread`.

## Settings as files

Each kind of setting has a pair of commands. The file from the first one can go in version control, be edited, and go back in with the second:

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

`org config` records the revision it read. `org apply` sends it back, so applying an old file after someone else changed the organization fails with exit 4 instead of undoing their change. A file without an `organizationId` creates a new organization, paused. Applying a configuration never starts anything. Without `-f`, the export commands put the document in the envelope's `data`, and the apply commands accept either the plain document or a saved envelope.

## Decisions that stay with you

The backend treats every request that carries your token as you. So when one of these commands runs inside a Rem or worker turn, the CLI refuses it with exit 5 (error code `user_only`) and sends nothing: `rattish approve` and `reject`, the legacy `workflow approve` and `reject`, `org apply`, `org employees release`, `swarm apply` and `delete`, and `provider set`.

`Raticode api` follows the same rule inside a Rem or worker turn. It sends only what a limited [agent token](./headless.md#agent-tokens) may do, and never an organization's start, pause, resume, consent decision, launch, review or credential action, whichever token it holds. Everything else is refused with exit 5 (`user_only`) before anything is sent. Run those from your own terminal or in the app.

Inside such a turn the CLI also refuses your owner token, whether it comes from the data folder, `RATICODE_TOKEN`, `RATICODE_TOKEN_FILE` or `--token-file`. These are guards, not a boundary: a worker runs as your account. Don't give agents a token that can act as you.

## What the CLI doesn't do

Some of the app's endpoints have no CLI command on purpose. Each has a reason:

| Reason | What it covers |
| --- | --- |
| `org-run-control` | Starting, pausing or resuming an organization or its heartbeats, and deciding consent requests. This needs your separate authorization, so it stays in the Organizations tab. |
| `consent-gated` | Changes the app asks your consent for: archiving an organization, restoring an earlier revision of one, routines, webhooks, GitHub imports and execution decisions. |
| `user-decision` | Your decisions on an organization's tickets: approving a plan, accepting or sending back a review, and reopening a ticket. Each one lets agents start work, so make them in the Organizations tab. |
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
| `phone-only` | The phone's own screens: overview cards, drafts, its file browser, media and developer log. |
| `device-link` | The encrypted link between paired devices: syncing, file transfer, cancelling a phone's request and remote jobs sent to another desktop. A script on this computer talks to the backend directly. |
| `protocol-reply` | Replies, snapshots and progress updates the desktop sends back to a phone. |
| `browser` | Raticode's browser tabs, which need a display. |
| `source-control` | Git. Use git directly. |

Anything else the backend serves that has no command of its own is still reachable with `Raticode api`, such as workflow revision history (`GET /workflows/ID/history`) or log retention (`GET /retention`).

## Every backend command

| Command | Guide |
| --- | --- |
| `Raticode api`, `Raticode wait` | [CLI reference](./cli.md#talk-to-a-running-backend) |
| `Raticode rattish show`, `Raticode rattish start`, `Raticode rattish stop`, `Raticode rattish resume`, `Raticode rattish wait` | [Workflows](#workflows) |
| `Raticode rattish runs`, `Raticode rattish logs`, `Raticode rattish events` | [Workflows](#workflows) |
| `Raticode rattish approvals`, `Raticode rattish approve`, `Raticode rattish reject` | [Workflows](#workflows) |
| `Raticode rattish export`, `Raticode rattish import`, `Raticode rattish triggers export`, `Raticode rattish triggers apply` | [Workflows](#workflows) |
| `Raticode org list`, `Raticode org show`, `Raticode org history`, `Raticode org events`, `Raticode org projects` | [Organizations](#organizations) |
| `Raticode org config`, `Raticode org apply`, `Raticode org export`, `Raticode org import` | [Settings as files](#settings-as-files) |
| `Raticode org employees list`, `Raticode org employees hold`, `Raticode org employees release` | [Organizations](#organizations) |
| `Raticode org schedule show`, `Raticode org schedule apply` | [Organizations](#organizations) |
| `Raticode org tickets list`, `Raticode org tickets create`, `Raticode org tickets update`, `Raticode org tickets comment`, `Raticode org tickets changes` | [Organizations](#organizations) |
| `Raticode org knowledge search`, `Raticode org knowledge read`, `Raticode org knowledge save` | [Organizations](#organizations) |
| `Raticode swarm list`, `Raticode swarm show`, `Raticode swarm history`, `Raticode swarm export`, `Raticode swarm apply`, `Raticode swarm delete` | [Swarms](#swarms) |
| `Raticode brain list`, `Raticode brain documents`, `Raticode brain search`, `Raticode brain read`, `Raticode brain save`, `Raticode brain projects` | [Second Brain](#second-brain) |
| `Raticode provider list`, `Raticode provider models`, `Raticode provider status`, `Raticode provider set`, `Raticode usage show`, `Raticode usage refresh` | [Providers, models and usage](#providers-models-and-usage) |
| `Raticode projects list`, `Raticode devices list` | [Projects, devices and live threads](#projects-devices-and-live-threads) |
| `Raticode threads follow`, `Raticode threads steering`, `Raticode threads steer`, `Raticode threads stop` | [Projects, devices and live threads](#projects-devices-and-live-threads) |
| `Raticode rem say`, `Raticode rem messages`, `Raticode rem search`, `Raticode rem follow`, `Raticode rem notices` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem settings show`, `Raticode rem settings export`, `Raticode rem settings apply` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem memories list`, `Raticode rem memories add`, `Raticode rem memories edit`, `Raticode rem memories forget`, `Raticode rem memories mode`, `Raticode rem memories undo` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem tasks list`, `Raticode rem tasks show`, `Raticode rem tasks create`, `Raticode rem tasks edit`, `Raticode rem tasks run`, `Raticode rem tasks pause`, `Raticode rem tasks resume`, `Raticode rem tasks delete`, `Raticode rem tasks wait` | [Rem from the CLI](./cli-rem.md) |
| `Raticode rem runs show`, `Raticode rem runs cancel`, `Raticode rem runs acknowledge`, `Raticode rem runs retry-review`, `Raticode rem thread show`, `Raticode rem thread send` | [Rem from the CLI](./cli-rem.md) |
| `Raticode tickets list`, `Raticode tickets show`, `Raticode tickets create`, `Raticode tickets edit`, `Raticode tickets move`, `Raticode tickets assign`, `Raticode tickets comment`, `Raticode tickets comments`, `Raticode tickets approve`, `Raticode tickets decline`, `Raticode tickets projects` | [Tickets from the CLI](./cli-rem.md#tickets) |
| `Raticode selfwork status`, `Raticode selfwork report`, `Raticode selfwork set`, `Raticode selfwork export`, `Raticode selfwork apply` | [Self-work from the CLI](./cli-rem.md) |

## Keeping the CLI in step with the app

`docs/cli-parity.json` in the repository lists what the app can do and names the CLI command that covers each part, or the reason above that it has none:

- every backend endpoint, with one entry per action where a field in the request picks the action, such as each ticket change, each paired-device setting and each swarm control;
- every operation and message type a phone can send, and every reply the desktop sends back;
- every request the desktop window sends to its own Electron process, so features that live only in the window are listed too.

A test reads the backend's routing code, the phone protocol and the window's request table, and fails when something new has neither a command nor a reason, when the file names a command that doesn't exist, or when a backend command is missing from this page. Adding a feature to the app means adding its command or its reason in the same change.

The [CLI command index](./cli-commands.md) is generated from the CLI itself, and the same test fails when it no longer matches the commands, their options or their help.
