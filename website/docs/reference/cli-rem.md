---
title: Rem, tickets and self-work from the CLI
description: Script Rem's chat, tasks, memories, alerts and settings, the global Kanban board and self-work with the Raticode CLI.
pageClass: docs-layout
---

# Rem, tickets and self-work from the CLI

::: info New in 0.5.3
These commands arrived in Raticode 0.5.3. Earlier versions don't have them.
:::

`Raticode rem`, `Raticode tickets` and `Raticode selfwork` let a script or a remote box use Rem, the [global board](../rem/board.md) and self-work without the desktop window. They're meant for scripts: there are no prompts, menus or screens.

Every command sends a request to a running Raticode backend, the same API the desktop window and the phone use. The CLI never opens Rem's database, so the app's rules apply exactly as they do in the window. The CLI finds a [headless backend](./headless.md) on its own. It can't find the desktop app's backend yet, because the app doesn't publish its address or token, so these commands can't reach it while only the desktop app is running. See the [CLI reference](./cli.md) for how the CLI finds the backend and its token. The token is never accepted on the command line.

## Output and exit codes

These commands follow the [backend client's script contract](./cli.md#output-and-exit-codes): one JSON object on stdout, `{"ok", "command", "data", "error", "warnings"}`, plus `request_id` when the command sent one; messages for people on stderr; and exit codes 0 to 8. `--output text` prints a short summary instead, and `--output ndjson` prints one object per line. Each command also takes `--endpoint`, `--token-file` and `--data-dir` to find the backend.

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

`--request-id` makes a message, task, ticket or comment safe to retry: sending the same ID again doesn't add it twice. Without it, each command makes one and reports it in `request_id`. Changes to tickets, tasks and self-work settings take `--expected-revision`, the `revision` from the matching `show`, `list` or `status` command. If someone changed the record since, the command exits 4 and changes nothing.

`rem follow` and `rem notices --watch` print `{"kind": "event", "data": ...}` for each message or alert and end with the result object, marked `"kind": "result"`. With `--timeout` they end with exit 8 when the time runs out, as `Raticode api --follow` does; Ctrl+C ends them with exit 0.

## Rem's chat

```bash
Raticode rem say "Summarize last night's failures" --wait --timeout 5m
Raticode rem messages --tail 20
Raticode rem messages --after 1532
Raticode rem search "release notes"
Raticode rem follow --timeout 30m
```

- `say` sends a message as if typed in the app. `--wait` waits for Rem's reply and prints it; without it, the command returns once Rem has the message. `-` reads the message from stdin, and `--refers-to ID` replies to an earlier message.
- `messages` reads the chat. Each message has a position, `seq`; pass the answer's `last` as `--after` next time to read only what's new.
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
Raticode rem runs cancel RUN_ID
Raticode rem thread send THREAD_ID "Use the staging database"
```

A task with no `--schedule` runs once, now. `--after TASK_OR_RUN_ID`, repeated for several, makes a one-time task [wait for other tasks](../rem/home.md#start-a-task-after-another) and start once they all succeed; `tasks edit --after` changes what a waiting task waits for and `--clear-after` starts it as scheduled. A waiting task's `dependency` says whether it is `waiting` or `held` and why, and `tasks list --output text` shows it. `tasks run` on a waiting task starts it now. `--spec FILE` adds other task fields from a JSON file, such as `provider`, `model` or `acceptance_criteria`. `run --wait` and `wait` exit 0 when the run succeeds, 7 when it needs you, 1 when it fails or is stopped, and 8 when time runs out; time running out doesn't stop the run. `runs acknowledge` and `runs retry-review` match the app's buttons, and `thread show` reads a task's work thread.

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
Raticode rem notices --cursor c1.1532.40.12
Raticode rem notices --watch --include needs_you
```

`notices` returns what needs you or finished since a cursor: Rem tasks that need your answer or finished, self-work proposals waiting for approval, and organization work. The first read returns no alerts, only the cursor to start from, so a new script doesn't get the whole history; save the answer's `cursor` and pass it next time. `--include` picks from `needs_you`, `finished` and `proposals`. For what is waiting right now, use `rem tasks list --needs-you` or `tickets list --proposals`.

## Rem's settings

```bash
Raticode rem settings show
Raticode rem settings export --file rem.json
Raticode rem settings apply --file rem.json
```

The file holds Rem's providers, models, efforts, projects, resources, browser, swarm access and board setting, in the form `export` writes. `apply` replaces all of them. While the desktop window is open it sends its own Rem settings every minute and replaces what you applied, so change them in **Settings > Rem** there instead. The skill and MCP library has its own commands; see [Tools, skills and MCP](../rem/resources.md#add-skills-and-mcp-servers-from-the-cli).

## Tickets

```bash
Raticode tickets list --status todo --status in_progress
Raticode tickets list --proposals
Raticode tickets show REM-12
Raticode tickets create --title "Fix flaky test" --project ~/code/app --status backlog --request-id t-123
Raticode tickets create --title "Write the launch post" --org ORG_ID --assignee WORKER_ID
Raticode tickets edit REM-12 --expected-revision 2 --priority high
Raticode tickets move REM-12 todo --expected-revision 3
Raticode tickets assign REM-12 --expected-revision 4 --to-org ORG_ID
Raticode tickets comment REM-12 "Seen on CI twice"
Raticode tickets comments REM-12
Raticode tickets approve REM-14 --expected-revision 1
Raticode tickets decline REM-15 --expected-revision 1 --reason "Not now"
Raticode tickets projects ~/code/app ~/code/site --expected-revision 2
```

Tickets are named by key, such as `REM-12`, or by ID. An organization's ticket needs `--org ORG_ID` when it isn't on the board's first page. `list` also returns the board's organizations, participants and projects, with the revisions `projects` needs. Moving a ticket to `in_progress` starts Rem on it now, as on the board.

## Self-work

```bash
Raticode selfwork status
Raticode selfwork report --mark-seen
Raticode selfwork set --expected-revision 3 --enabled --max-threads 2 --no-auto-pull-backlog
Raticode selfwork export > self-work.json
Raticode selfwork apply --file self-work.json
```

`status` shows whether self-work is on, what it's doing and its settings, and `report` is the running report described in [Self-work](../rem/board.md#self-work). `apply` takes the file `export` writes; it checks the current revision unless the file has `expected_revision`. Self-work spends provider allowance while nobody is watching, so turn it on deliberately on a remote box.

## What only you can do

The app keeps some decisions for you: approving or declining Rem's proposals, marking a ticket done by hand, choosing which projects Rem and workers may take tickets from, and changing Rem's and self-work's settings. The backend applies its usual rules to these commands: a proposal still has to be approved before it moves, and declined tickets stay declined.

When one of these commands runs inside a Rem worker session (`RATICODE_AGENT_SESSION` is set) or another provider turn that Raticode started, the CLI refuses it with exit 5 and sends nothing. This keeps Rem from using the CLI to approve its own proposals or finish its own tickets. It's a guard, not a boundary: the backend can't yet tell whether a request with your token came from you or an agent. Don't give agents a token that can act as you.
