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
Raticode rem tasks create --title "Find the slowdown" --type research --project . \
  --prompt "Why did startup slow down?" --computer DESKTOP_ID
Raticode rem tasks show TASK_ID
Raticode rem tasks edit TASK_ID --expected-revision 3 --prompt "Check the build and tests"
Raticode rem tasks run TASK_ID --wait --timeout 1h
Raticode rem tasks wait TASK_ID --timeout 30m
Raticode rem tasks pause TASK_ID --cancel-current
Raticode rem tasks resume TASK_ID --expected-revision 4
Raticode rem tasks delete TASK_ID --yes
Raticode rem tasks make-workflow TASK_ID
Raticode rem tasks switch-over TASK_ID --yes
Raticode rem runs show RUN_ID
Raticode rem runs pause RUN_ID
Raticode rem runs resume RUN_ID
Raticode rem runs cancel RUN_ID
Raticode rem runs acknowledge-all
Raticode rem tasks wake TASK_ID
Raticode rem thread send THREAD_ID "Use the staging database"
```

- A task with no `--schedule` runs once, now.
- `tasks list` leaves each task's prompt out, so it stays quick with many tasks. `tasks show` prints one task with its prompt.
- `--spec FILE` adds other task fields from a JSON file, such as `provider`, `model` or `acceptance_criteria`.
- `--provider`, `--model` and `--account` set the task thread's provider, model and account, like the thread picker's [Account menu](../rem/providers.md#choose-a-thread-s-account). The account wins over Rem's account setting; `Raticode provider account list` names the accounts with their sign-in state; with `--format json` each one also has `last_reading`, its newest weekly and 5-hour percentages.
- `--computer ID` pins the task to one computer from `Raticode computers list`. On a paired desktop it runs as that desktop's own Rem task, for research and conversation tasks only; this computer keeps a stand-in run with its answer and report path, and `runs cancel` stops it there ([Run a task on a paired desktop](../rem/home.md#run-a-task-on-a-paired-desktop), experimental).
- `runs pause` and `runs resume` [pause a run and resume it](../rem/home.md#pause-and-resume-a-task) in the same thread. `tasks pause` and `tasks resume` are for a repeating task's schedule.
- `runs acknowledge` and `runs retry-review` match the app's buttons. `runs acknowledge-all` is [Dismiss all](../rem/home.md#dismiss-all-updates): it dismisses every task update at once and leaves tasks that still need your answer, printing how many it dismissed and how many still need you. `thread show` reads a task's work thread.
- `tasks make-workflow` starts the [Make it a workflow](../rem/home.md#make-it-a-workflow) task for a repeating task that passed twice. `tasks switch-over` prints the drafted workflow's files and the cron trigger it would add; with `--yes` it adds the trigger, approves exactly that version and pauses the task's schedule (`--unseen` also approves files the diff couldn't show in full). It's refused inside an agent turn. `tasks resume` undoes it.
- A task whose worker is [sleeping](../rem/home.md#long-waits-and-time-limits) shows as `sleeping` in `tasks list --output text`, with what it waits for and its next check. Its run's `sleep` field has the details. `tasks wake TASK_ID` (or `runs wake RUN_ID`) wakes it now, like **Wake now** in the app.
- `runs show` names the provider app and version each worker and reviewer turn started, such as *Provider: Claude Code 2.3.1 (worker, reviewer)*. JSON output has them in the run's `turns`, each with `role` (`execute` for the worker, `verify` for the reviewer), `provider`, `provider_version` (`null` when it couldn't be read) and `started_at`. A failed or blocked run whose task last passed with another version of that app adds a line such as *Claude Code changed from 2.3.0 to 2.3.1 since the last passing run.*, and its JSON has `provider_version_changes`. Runs saved before this show neither. This isn't in a release yet.
- A research or conversation task that waits for room while a paired desktop has room for it carries a `suggestion` on its run, and `runs show RUN_ID --output text` prints it, such as *Desktop B has room: Run there*. `runs run-there RUN_ID` is **Run there** in the app: it pins the run to that desktop, where it runs as that desktop's own Rem task, and counts as your own start. Nothing goes there until you run it. See [Run waiting work on a paired desktop](../rem/home.md#run-waiting-work-on-a-paired-desktop).

### What a thread sends

```bash
Raticode rem context THREAD_ID
Raticode rem context THREAD_ID --turns 10 --output text
```

`rem context` shows how big each part of a thread's prompt was, newest turn first: its characters, an estimated token count (characters divided by 4) and whether the turn sent it, a resumed session [kept it](../rem/usage.md#what-each-turn-sends), or the worker reads it from a file (`in a file`). It works for any thread, a Rem task's or your own. Only sizes are recorded, never the text. This isn't in a release yet.

```bash
Raticode rem context
Raticode rem context --call chat_reply --output text
Raticode rem context --run RUN_ID
```

Without a thread ID, `rem context` lists [Rem's own model calls](../rem/usage.md#rem-s-own-calls), newest first, with the same sizes for each part. `--call` keeps one kind: `chat_reply`, `worker_answer`, `conversation`, `task_update`, `self_work_plan`, `self_work_fit`, `self_work_stop` or `organization`. Calls recorded before self-work's three checks were split keep the older `self_work` kind and still show in the list and counts; `--call self_work` lists only those. `--run` keeps the calls about one task run. JSON output has `calls`, each with its `call` kind, `parts`, `attempt` (`first`, `reformat`, `repair` or `read`) and `failed`, and `call_count`. `by_kind` counts every kept call that matches the filters, per kind: `calls`, `reasked` (reformats and repairs: asked again because the answer couldn't be used), `failed`, and `attempts` with a count for each attempt. Each kind's `versions` splits those calls by the [version of the fixed rules](../rem/usage.md#rem-s-own-calls) they were sent with, newest rules first: `version` (`null` for calls with no fixed rules or recorded before versions were kept), `calls`, `re_asked`, `failed`, `first_seen`, `last_seen`, and `current` on the version of the kind's newest call. Each call in `calls` has its `rules_version`. Text output ends with the same counts, one line per rules version under each kind. See [what re-asked and failed mean](../rem/usage.md#rem-s-own-calls).

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
Raticode rem memories add "Release notes go in CHANGELOG.md" --project /path/to/project
Raticode rem memories edit MEMORY_ID --project /path/to/project
Raticode rem memories edit MEMORY_ID --global
Raticode rem memories add "In Oslo for the summit" --until 2026-06-30
Raticode rem memories edit MEMORY_ID --until 2026-07-01
Raticode rem memories edit MEMORY_ID --keep
Raticode rem memories forget MEMORY_ID
Raticode rem memories mode paused
Raticode rem memories undo MESSAGE_ID
```

These work as in [What Rem remembers](../rem/home.md#what-rem-remembers). Text that looks like a password or key is refused. `--project` takes the root folder of an open project and makes the memory apply only to work there; `--global` makes it apply everywhere again. See [Project memories](../rem/home.md#project-memories). `--until` sets the last day a memory applies (a date, or a date and time with its timezone); after it, Rem stops using the memory but keeps it listed. `--keep` removes the end date. See [Memories with an end date](../rem/home.md#memories-with-an-end-date).

## Decisions

```bash
Raticode rem decisions list
Raticode rem decisions list --project /path/to/project
Raticode rem decisions show D-3
Raticode rem decisions use D-3
Raticode rem decisions change D-3 "pytest -q"
Raticode rem decisions edit D-3 --why "Faster than the full suite"
Raticode rem decisions reject D-4
Raticode rem decisions reopen D-3
Raticode rem decisions forget D-3
Raticode rem decisions retire D-7
Raticode rem decisions add branch.integration main --project /path/to/project
Raticode rem decisions add adr:workflow-format "Workflows are written in Rattish, never TOML" \
  --project /path/to/project --record --applies-to "src/rattish/**" --forbids "**/*.toml"
Raticode rem decisions edit D-7 --consequences "Old TOML files need converting" --note "clarified"
Raticode rem decisions replace D-7 "Workflows are Rattish; TOML only for imports" --note "import support"
Raticode rem decisions confirm-edit E-2
Raticode rem decisions cancel-edit E-2
Raticode rem decisions scan /path/to/project
Raticode rem decisions scan /path/to/project --preview
Raticode rem decisions paths --id D-7 --applies-to "src/workflows/**" --forbids "**/*.toml"
Raticode rem decisions paths --project /path/to/project --applies-to "docs/adr/**"
```

These work as in [Rem's decisions](../rem/home.md#rem-s-decisions). `use` takes Rem's recommended answer and `change` gives your own; work that stopped for the question continues with your answer. `reject` turns a question down, `reopen` asks a decided question again as a new card while the decision stays in force until you answer it, and `forget` deletes the entry. A [decision record](../rem/home.md#retire-a-record) whose file is already in the repository can't be deleted: `forget` is refused for it, and `retire` keeps it turned down, stops workers getting it, and rewrites its file with `status: rejected`, committed on the landing branch. `retire` is refused for a decision with no file; `forget` that one. `add` records a decision yourself under a topic key; with `--record` (and `--project`) it is a [decision record](../rem/home.md#decision-records) written into the project's repository, with `--context`, `--options`, `--consequences`, `--check`, and repeatable `--applies-to`, `--forbids` and `--signal`. `edit` takes the same record parts and `--note`; `replace` writes a new decision that replaces one in force, keeping a record's parts unless you give new ones. `scan` reads the project's files and git history for its check command, landing branch and decision folder (see [How Rem finds the answers](../rem/home.md#how-rem-finds-the-answers)) and files what it finds; `--preview` only shows it. `confirm-edit` and `cancel-edit` press **OK** or **Cancel** on a change you [asked Rem for in chat](../rem/home.md#ask-rem-to-change-a-decision); `list` shows the changes still waiting, each with its ID (`E-2`) and the before and after of each part. Under each waiting question `list` prints who asked it, when, and what waits on it, such as `Asked by REM-249 · 21 Nov · REM-249 waits for this` (in JSON, the item's `asked` block). New in 0.6.0. `confirm-edit` is refused when the decision changed after Rem showed the change. `show` includes Raticode's last run of a decided check command, and for a record it prints next to each Applies to and Never path how many tracked files it matches on the landing branch, such as `docs/releasing.md (1 file)`, `github/workflows/** (matches no files)` or `secrets/** (matches 3 files that exist now)`. `paths` counts paths before you save them: `--id` counts in a decision's project (for `edit` or `replace`) and `--project` in a folder (for `add`). It changes nothing, and a count never stops a save, since a path can be planned. Path counts are new in 0.6.0. Each other change takes `--expected-revision` to refuse it when the list changed since you read it. Agent tokens can read decisions but can't change them.

## Alerts

```bash
Raticode rem notices
Raticode rem notices --cursor c1.1532.40.12.7
Raticode rem notices --watch --include needs_you
```

`notices` returns what needs you or finished since a cursor: Rem tasks that need your answer or finished, self-work proposals waiting for approval, organization work, and workflow messages, the notices a [notification](../nodes/notification.md) node with `channel: phone` stored for your phone.

- The first read returns no alerts, only the cursor to start from, so a new script doesn't get the whole history. Save the answer's `cursor` and pass it next time.
- `--include` picks from `needs_you`, `finished`, `proposals` and `workflow`. A `workflow` alert names its `workflow_id` and `run_id`; read that run with `Raticode rattish logs WORKFLOW_ID RUN_ID`. After a run's first 20 notices, its one "And N more from this run" alert has `more_from_run` set to N. `dropped` counts workflow notices deleted before this read reached them (the computer keeps the newest 500); `--watch` prints them as one alert with `"kind": "dropped"`. A question of Rem's that waits for you while no task stops for it ([auto mode](../rem/home.md#auto-mode-decides-for-you)) is one `needs_you` alert with `"kind": "decision"`, its `project_name` and a `decision` object with `id`, `question` and `recommended`.
- For what's waiting right now, use `rem tasks list --needs-you` or `tickets list --proposals`.

## Rem's settings

```bash
Raticode rem settings show
Raticode rem settings export --file rem.json
Raticode rem settings apply --file rem.json
```

- The file holds Rem's providers, models, efforts, accounts, projects, resources, browser, swarm access and board setting, in the form `export` writes. `apply` replaces all of them.
- `self_work_accounts` is [which account self-work uses](../rem/board.md#which-account-self-work-uses) with each provider, such as `{"claude_code": "spare"}`. A provider it leaves out follows Rem's setting. The desktop window doesn't send it, so its once-a-minute resend leaves what you applied in place.
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
- Moving a ticket to `in_progress` starts Rem on it now, as on the board. A ticket that contradicts one of the project's decisions is blocked instead, and the command fails with the reason.
- A ticket that [violates decisions](../rem/board.md#self-work-keeps-to-decisions) is marked `(violates D-4)` in `list`, and `show` prints the reason. In JSON each ticket has `violates_decisions` (the decision IDs, empty when none) and `violation_reason`. New in 0.6.0.
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
Raticode selfwork stop-when "REM-205 to REM-212 are done" --no-later-than 00:00
Raticode selfwork stop-when --no-later-than 23:00 --day today
Raticode selfwork stop-when --clear both
Raticode selfwork report --period
Raticode selfwork set --expected-revision 3 --enabled --max-threads 2 --no-auto-pull-backlog
Raticode selfwork set --expected-revision 4 --session-token-limit 500000 --rate-limit-percent 80
Raticode selfwork set --expected-revision 5 --daily-allowance-percent 11
Raticode selfwork set --expected-revision 5 --auto-approve --confirm-auto-approve
Raticode selfwork set --expected-revision 6 --no-auto-approve
Raticode selfwork set --expected-revision 7 --account claude_code=spare --account codex=auto
Raticode selfwork export > self-work.json
Raticode selfwork apply --file self-work.json
Raticode selfwork objectives
Raticode selfwork objectives add --text "Raise UX quality of the workflow studio"
Raticode selfwork objectives add --text "Make every starter run on Windows" --done-when "All starters pass on the Windows CI job"
Raticode selfwork objectives check --id OBJECTIVE_ID
Raticode selfwork objectives move --id OBJECTIVE_ID --index 0
```

- `status` shows whether self-work is on, what it's doing, how many of its tasks are paused, and its settings.
- `on` and `off` flip only the switch, like telling Rem "turn on self-work". `set` changes several settings at once and needs `--expected-revision`.
- `--session-token-limit`, `--daily-token-limit` and `--rate-limit-percent` set the [self-work limits](../rem/board.md#self-work-limits); 0 turns one off. `status` shows them, and why self-work stopped starting new work when a limit is reached.
- `--daily-allowance-percent` sets the [daily cap on the weekly allowance](../rem/board.md#daily-cap-on-the-weekly-allowance) (new in 0.6.0); a percent above 0 chooses Daily cap and 0 turns it off. It's refused while Pace to reset is chosen.
- `--allowance-mode off|daily|pace` chooses how self-work uses a weekly allowance, and `--pace-aim N` (1 to 100, such as 50 for half the week) and `--pace-lead H` (0 to 24) set [Pace to reset](../rem/board.md#pace-to-reset)'s aim and finish (new in 0.6.0). `status` adds the pace line, such as "Pacing Codex's weekly allowance: 40% used, line 48.5%. Up to 2 tasks can start." `status` adds a line with today's use, naming the [account](../rem/providers.md#self-work-on-accounts) self-work runs on when it uses one.
- `--auto-approve` turns on [auto-approval](../rem/board.md#let-rem-approve-its-own-tickets): self-work's new ideas go to Backlog approved until self-work ends. It also needs `--confirm-auto-approve`; without it the command explains what the setting allows and changes nothing. `apply` needs the same flag when its file turns auto-approval on. `--no-auto-approve` turns it off. `status` shows how many tickets self-work approved this session.
- `--account PROVIDER=ACCOUNT` sets [which account self-work uses](../rem/board.md#which-account-self-work-uses) with that provider (new in 0.6.0): an account name, `auto` to choose by usage, `@default` for the default sign-in, or nothing after `=` for Rem's setting. Repeat it for several providers. `status` lists the choices, `export` writes them as `provider_accounts` and `apply` replaces them all.
- `objectives` lists the [self-work objectives](../rem/board.md#objectives) with their IDs (new in 0.6.0). `add`, `edit`, `remove`, `move`, `check` and `uncheck` change them as you, like the side pane; `--done-when` sets the doneness criteria, and `--expected-revision` refuses the change if the list moved on (exit 4).
- `report` is the running report described in [Self-work](../rem/board.md#self-work). `report --period` prints the [report of the last self-work period](../rem/board.md#the-report-when-self-work-stops).
- `window` sets a [timed window](../rem/board.md#timed-self-work), like telling Rem. `--date YYYY-MM-DD` picks the day, `--timezone` names a zone (the computer's by default) and `--then restore|on|off` sets the switch afterwards. `status` shows the windows with their IDs, and `--cancel` ends or drops one.
- `stop-when` sets [when the self-work running now stops](../rem/board.md#stop-condition-and-latest-time), like telling Rem: a condition in your words, which Rem checks after each self-work task finishes, and `--no-later-than HH:MM` (with `--day today|tonight|tomorrow|YYYY-MM-DD` and `--timezone`) as the latest time. An empty condition (`""`) clears it; `--clear condition|no-later-than|both` clears either or both, and `--revision` refuses the change if the stop moved on. Self-work must be on. `status` shows the condition, the latest time and Rem's last check. Not in a released version yet.
- `apply` takes the file `export` writes. It checks the current revision unless the file has `expected_revision`.
- Self-work spends provider allowance while nobody is watching, so turn it on deliberately on a remote box.

## Restore Rem's database

`Raticode rem restore-db` works on the files directly, without the backend, so quit Raticode first. It refuses while a running Raticode holds Rem, and inside a Rem or worker turn only `--list` works.

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
