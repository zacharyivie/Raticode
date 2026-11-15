---
title: Rem, tickets and self-work from the CLI
description: Script Rem's chat, tasks, memories, alerts and settings, the global Kanban board and self-work with the Raticode CLI.
pageClass: docs-layout
---

# Rem, tickets and self-work from the CLI

::: info New in 0.5.3
These commands arrived in Raticode 0.5.3. Earlier versions don't have them.
:::

`Raticode rem`, `Raticode tickets` and `Raticode selfwork` let a script or a remote box use Rem, the [global board](../rem/board.md) and self-work without the desktop window. They're built for scripts, with no prompts, menus or screens.

- Every command sends a request to a running Raticode backend, through the same API the desktop window and the phone use.
- The CLI never opens Rem's database, so the app's rules apply exactly as in the window.
- The CLI finds a [headless backend](./headless.md) on its own. It can't find the desktop app's backend yet, because the app doesn't publish its address or token. With only the desktop app running, these commands can't reach it.
- The [CLI reference](./cli.md) explains how the CLI finds the backend and its token. The token is never accepted on the command line.

## Output and exit codes

These commands follow the [backend client's script contract](./cli.md#output-and-exit-codes):

- One JSON object on stdout: `{"ok", "command", "data", "error", "warnings"}`, plus `request_id` when the command sent one.
- Messages for people on stderr.
- Exit codes 0 to 8.
- `--output text` prints a short summary instead, and `--output ndjson` prints one object per line.
- Each command takes `--endpoint`, `--token-file` and `--data-dir` to find the backend.

The exit codes that matter most here:

| Exit | Meaning |
| --- | --- |
| 0 | Done |
| 1 | Failed, or a task run ended without succeeding |
| 2 | Invalid input, or the app refused the change, such as moving a proposal you haven't approved or a ticket for an organization while Organizations is off |
| 3 | Not found |
| 4 | The record changed since `--expected-revision` |
| 5 | Not allowed: a wrong token, or a decision only you can make (error code `user_only`) |
| 6 | The backend isn't reachable |
| 7 | A task run is waiting on you |
| 8 | Timed out, or the outcome is unknown |

Safe retries and stale edits:

- `--request-id` makes a message, task, ticket or comment safe to retry. Sending the same ID again doesn't add it twice. Without it, each command makes one and reports it in `request_id`.
- Changes to tickets, tasks and self-work settings take `--expected-revision`, the `revision` from the matching `show`, `list` or `status` command. If someone changed the record since, the command exits 4 and changes nothing.

Streams:

- `rem follow` and `rem notices --watch` print `{"kind": "event", "data": ...}` for each message or alert, and end with the result object marked `"kind": "result"`.
- With `--timeout` they exit 8 when time runs out, like `Raticode api --follow`. Ctrl+C ends them with exit 0.

## Rem's chat

```bash
Raticode rem say "Summarize last night's failures" --wait --timeout 5m
Raticode rem messages --tail 20
Raticode rem messages --after 1532
Raticode rem search "release notes"
Raticode rem follow --timeout 30m
```

- `say` sends a message as if typed in the app.
  - `--wait` waits for Rem's reply and prints it. Without it, the command returns once Rem has the message.
  - `-` reads the message from stdin, and `--refers-to ID` replies to an earlier message.
- `messages` reads the chat. Each message has a position, `seq`. Pass the answer's `last` as `--after` next time to read only what's new.
- `search` finds words in the whole saved chat, like the app's search. No model reads the query.
- `follow` prints each new message as it arrives until it is stopped or `--timeout` passes.

## Tasks and runs

```bash
Raticode rem tasks list --needs-you
Raticode rem tasks create --title "Nightly check" --type research --prompt "Check the build" \
  --schedule '{"kind": "daily", "local_time": "06:00", "timezone": "UTC"}'
Raticode rem tasks create --title "Fix the audit findings" --type develop --project . \
  --prompt "Fix what the audit found" --after AUDIT_TASK_ID
Raticode rem tasks show TASK_ID
Raticode rem tasks edit TASK_ID --expected-revision 3 --prompt "Check the build and tests"
Raticode rem tasks run TASK_ID --wait --timeout 1h
Raticode rem tasks wait TASK_ID --timeout 30m
Raticode rem tasks pause TASK_ID --cancel-current
Raticode rem tasks resume TASK_ID --expected-revision 4
Raticode rem tasks delete TASK_ID --yes
Raticode rem runs show RUN_ID
Raticode rem runs pause RUN_ID
Raticode rem runs resume RUN_ID
Raticode rem runs cancel RUN_ID
Raticode rem tasks wake TASK_ID
Raticode rem thread send THREAD_ID "Use the staging database"
```

- A task with no `--schedule` runs once, now.
- `--spec FILE` adds other task fields from a JSON file, such as `provider`, `model` or `acceptance_criteria`.
- `runs pause` and `runs resume` [pause a run and resume it](../rem/home.md#pause-and-resume-a-task) in the same thread. `tasks pause` and `tasks resume` are for a repeating task's schedule.
- `runs acknowledge` and `runs retry-review` match the app's buttons. `thread show` reads a task's work thread.
- A task whose worker is [sleeping](../rem/home.md#long-waits-and-time-limits) shows as `sleeping` in `tasks list --output text`, with what it waits for and its next check. Its run's `sleep` field has the details. `tasks wake TASK_ID` (or `runs wake RUN_ID`) wakes it now, like **Wake now** in the app.

### Waiting on other tasks

- `--after TASK_OR_RUN_ID`, repeated for several, makes a one-time task [wait for other tasks](../rem/home.md#start-a-task-after-another) and start once they all succeed.
- `tasks edit --after` changes what a waiting task waits for. `--clear-after` starts it as scheduled.
- A waiting task's `dependency` says whether it's `waiting` or `held`, and why. `tasks list --output text` shows it.
- `tasks run` on a waiting task starts it now.
- Other [start conditions](../rem/home.md#start-at-a-time-or-after-what-is-running) are set by asking Rem. Each one is an entry in `dependency.prerequisites` with a `kind`: `task`, `run`, `time`, `idle` or `self_work`.

### Exit codes for `run --wait` and `wait`

| Exit | When |
| --- | --- |
| 0 | The run succeeded |
| 7 | The run needs you |
| 1 | The run failed or was stopped |
| 8 | Time ran out. The run keeps going |

## Memories

```bash
Raticode rem memories list
Raticode rem memories add "Deploys happen on Tuesdays"
Raticode rem memories edit MEMORY_ID "Deploys happen on Wednesdays"
Raticode rem memories forget MEMORY_ID
Raticode rem memories mode paused
Raticode rem memories undo MESSAGE_ID
```

These work as in [What Rem remembers](../rem/home.md#what-rem-remembers). Text that looks like a password or key is refused.

## Alerts

```bash
Raticode rem notices
Raticode rem notices --cursor c1.1532.40.12.7
Raticode rem notices --watch --include needs_you
```

`notices` returns what needs you or finished since a cursor: Rem tasks that need your answer or finished, self-work proposals waiting for approval, organization work, and workflow messages, the notices a [notification](../nodes/notification.md) node with `channel: phone` stored for your phone.

- The first read returns no alerts, only the cursor to start from, so a new script doesn't get the whole history. Save the answer's `cursor` and pass it next time.
- `--include` picks from `needs_you`, `finished`, `proposals` and `workflow`. A `workflow` alert names its `workflow_id` and `run_id`; read that run with `Raticode rattish logs WORKFLOW_ID RUN_ID`. After a run's first 20 notices, its one "And N more from this run" alert has `more_from_run` set to N. `dropped` counts workflow notices deleted before this read reached them (the computer keeps the newest 500); `--watch` prints them as one alert with `"kind": "dropped"`.
- For what's waiting right now, use `rem tasks list --needs-you` or `tickets list --proposals`.

## Rem's settings

```bash
Raticode rem settings show
Raticode rem settings export --file rem.json
Raticode rem settings apply --file rem.json
```

- The file holds Rem's providers, models, efforts, projects, resources, browser, swarm access and board setting, in the form `export` writes. `apply` replaces all of them.
- While the desktop window is open, it sends its own Rem settings every minute and replaces what you applied. Change them in **Settings > Rem** there instead.
- The skill and MCP library has its own commands. See [Tools, skills and MCP](../rem/resources.md#add-skills-and-mcp-servers-from-the-cli).

## Tickets

```bash
Raticode tickets list --status todo --status in_progress
Raticode tickets list --proposals
Raticode tickets show REM-12
Raticode tickets create --title "Fix flaky test" --project ~/code/app --status backlog --request-id t-123
Raticode tickets create --title "Write the launch post" --org ORG_ID --assignee WORKER_ID
Raticode tickets edit REM-12 --expected-revision 2 --priority high
Raticode tickets create --title "Starter workflows" --waits-for REM-3 --waits-for REM-4
Raticode tickets edit REM-12 --expected-revision 3 --waits-for REM-9
Raticode tickets edit REM-12 --expected-revision 4 --clear-waits
Raticode tickets move REM-12 todo --expected-revision 3
Raticode tickets assign REM-12 --expected-revision 4 --to-org ORG_ID
Raticode tickets comment REM-12 "Seen on CI twice"
Raticode tickets comments REM-12
Raticode tickets approve REM-14 --expected-revision 1
Raticode tickets decline REM-15 --expected-revision 1 --reason "Not now"
Raticode tickets projects ~/code/app ~/code/site --expected-revision 2
```

- Name a ticket by key, such as `REM-12`, or by ID.
- An organization's ticket needs `--org ORG_ID` when it isn't on the board's first page.
- `list` also returns the board's organizations, participants and projects, with the revisions `projects` needs.
- `show` prints the ticket's [usage](../rem/board.md#usage-on-each-ticket): its runs' tokens, and how many calls a provider didn't report. Each ticket in `list` has the same `usage` object.
- Moving a ticket to `in_progress` starts Rem on it now, as on the board.
- `--waits-for` lists the tickets that must be Done first, by key or ID; repeat it for several. On `edit` it replaces the whole list, and `--clear-waits` empties it. A loop or a ticket from another board is refused. `list` marks a waiting ticket with `(waits for REM-3)`, and `show` prints the list and any notice about a cancelled one. See [Tickets that wait for other tickets](../rem/board.md#tickets-that-wait-for-other-tickets).

## Self-work

```bash
Raticode selfwork status
Raticode selfwork report --mark-seen
Raticode selfwork on
Raticode selfwork off
Raticode selfwork window --until 19:00
Raticode selfwork window --from 23:00 --until 07:00 --timezone Asia/Irkutsk
Raticode selfwork window --until-running-done --then off
Raticode selfwork window --cancel WINDOW_ID
Raticode selfwork report --period
Raticode selfwork set --expected-revision 3 --enabled --max-threads 2 --no-auto-pull-backlog
Raticode selfwork set --expected-revision 4 --session-token-limit 500000 --rate-limit-percent 80
Raticode selfwork set --expected-revision 5 --auto-approve --confirm-auto-approve
Raticode selfwork set --expected-revision 6 --no-auto-approve
Raticode selfwork export > self-work.json
Raticode selfwork apply --file self-work.json
```

- `status` shows whether self-work is on, what it's doing, how many of its tasks are paused, and its settings.
- `on` and `off` flip only the switch, like telling Rem "turn on self-work". `set` changes several settings at once and needs `--expected-revision`.
- `--session-token-limit`, `--daily-token-limit` and `--rate-limit-percent` set the [self-work limits](../rem/board.md#self-work-limits); 0 turns one off. `status` shows them, and why self-work stopped starting new work when a limit is reached.
- `--auto-approve` turns on [auto-approval](../rem/board.md#let-rem-approve-its-own-tickets): self-work's new ideas go to Backlog approved until self-work ends. It also needs `--confirm-auto-approve`; without it the command explains what the setting allows and changes nothing. `apply` needs the same flag when its file turns auto-approval on. `--no-auto-approve` turns it off. `status` shows how many tickets self-work approved this session.
- `report` is the running report described in [Self-work](../rem/board.md#self-work). `report --period` prints the [report of the last self-work period](../rem/board.md#the-report-when-self-work-stops).
- `window` sets a [timed window](../rem/board.md#timed-self-work), like telling Rem. `--date YYYY-MM-DD` picks the day, `--timezone` names a zone (the computer's by default) and `--then restore|on|off` sets the switch afterwards. `status` shows the windows with their IDs, and `--cancel` ends or drops one.
- `apply` takes the file `export` writes. It checks the current revision unless the file has `expected_revision`.
- Self-work spends provider allowance while nobody is watching, so turn it on deliberately on a remote box.

## Restore Rem's database

`Raticode rem restore-db` works on the files directly, without the backend, so quit Raticode first. It refuses while a running Raticode holds Rem.

```bash
Raticode rem restore-db --list --format json
Raticode rem restore-db
Raticode rem restore-db --snapshot rem-home-20261114-030000Z.sqlite3
```

Without `--snapshot` it restores the newest copy that still passes SQLite's check. The current files are moved aside, never deleted, and Rem posts what was restored in its chat on the next start. Raticode does the same on its own when it finds the database damaged at startup. See [Rem's database backups](../knowledge/second-brain.md#rem-s-database-backups).

## What only you can do

The app keeps these decisions for you:

- approving or declining Rem's proposals
- marking a ticket done by hand
- choosing which projects Rem and workers may take tickets from
- changing Rem's and self-work's settings, including letting self-work approve its own tickets

The backend applies its usual rules to these commands. A proposal still has to be approved before it moves, and declined tickets stay declined.

Inside a Rem worker session (`RATICODE_AGENT_SESSION` is set) or another provider turn that Raticode started, the CLI refuses these commands with exit 5 and sends nothing. That keeps Rem from approving its own proposals or finishing its own tickets through the CLI.

::: warning A guard, not a boundary
The backend can't yet tell whether a request with your token came from you or an agent. Don't give agents a token that can act as you.
:::
