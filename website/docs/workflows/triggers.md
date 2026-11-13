---
title: Triggers
description: Start a Rattish workflow on a cron schedule, a file change, or an authenticated local webhook.
pageClass: docs-layout
---

# Triggers

A trigger starts a `workflow.rattish` without anyone pressing Run. There are three kinds:

| Kind | Fires when |
| --- | --- |
| Cron | A schedule comes due |
| File | Files change in a folder |
| Webhook | A program on this computer calls a local URL |

Every triggered run takes the same path as `Raticode rattish run`, with the same compile, preflight and run record.

::: warning Triggers only fire while something is running them
Triggers run only while Raticode or its background scheduler is running. Nothing wakes a sleeping computer. Turn on **Start at login** if triggers should still fire after a reboot.
:::

Triggers belong to the computer, not the workflow file. They aren't stored in `workflow.rattish`, and exporting a workflow leaves its triggers and tokens behind.

## Add a trigger

In the Studio, open the workflow, choose **Workflow settings** from the graph's **More** menu, and scroll to **Triggers**.

<AppScreenshot name="workflow-triggers" caption="Workflow settings, scrolled to Triggers: a weekday schedule and a file trigger, each with Edit and Delete." />

From the CLI:

```bash
Raticode rattish triggers add cron PATH/workflow.rattish --cron "0 9 * * 1-5" --timezone America/New_York
Raticode rattish triggers add file PATH/workflow.rattish --path inbox --debounce 2 --glob "*.csv"
Raticode rattish triggers add webhook PATH/workflow.rattish
```

Raticode refuses a trigger the workflow couldn't run, for example when a required input isn't set. Pass inputs with `--input NAME=JSON`, or fill in **Workflow inputs** in the Studio.

### Cron schedules

- The expression has five fields: minute, hour, day of month, month, weekday.
- Weekdays follow crontab, so `0` and `7` are both Sunday.
- Set a day of the month or a weekday, not both.
- The schedule follows the time zone you choose, including daylight saving changes.

### File changes

- Point a trigger at a file or a folder. Relative paths start at the workflow's project.
- The workflow starts once the files stay quiet for the debounce time.
- A folder that never goes quiet still gets a run after 60 seconds or ten debounce periods, whichever is longer.
- Raticode ignores changes inside `.git`, `.raticode`, `.venv`, `__pycache__` and `node_modules`.

A workflow that writes into its own watched folder would start itself forever. After 10 runs in a row started by changes made during a run, Raticode turns the trigger off and records why.

### Trigger events

Bind events with the `trigger.events` reference. A `loop` with a `trigger-events` source gets one item per event:

| Trigger | Items per run | Fields |
| --- | --- | --- |
| File | One per changed file, up to 256 | `type`, `kind` (`created`, `modified`, `deleted` or `moved`), `path`, `name`, `directory`, `trigger_id` |
| Webhook | One | `type`, `trigger_id`, `received_at`, and the JSON `payload` (bodies up to 1 MiB) |
| Cron | None | |

The loop also provides `index` and `event_json`, plus `file_content` when the loop sets `include-content`.

### Webhooks

A webhook trigger gets a URL on `127.0.0.1` and a token. Raticode shows the token once and stores only its hash. If you lose it, replace it with **New token** or `rotate-token`.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"ref":"main"}' http://127.0.0.1:8767/rattish-hooks/TRIGGER_ID
```

- The call returns `202` with `started`, `queued` or `skipped`.
- Requests from a web page are refused.
- A `loop` with a `trigger-events` source receives the JSON body as `payload`. Treat it as untrusted input.
- On a computer with other user accounts, keep the token off the command line, where other accounts can read it. `curl -H @header-file` reads the header from a private file.
- The listener opens only while an enabled webhook trigger exists. If port 8767 is taken, the Triggers section says so, and `Raticode rattish triggers webhook-port PORT` changes it.

## If the workflow is already running

Each trigger has an overlap policy, set in the Triggers section or with `--overlap skip|queue|parallel`. Runs you started by hand count too.

| Policy | CLI value | What happens |
| --- | --- | --- |
| Skip this run | `skip` | The start is dropped and recorded as skipped. This is the default. |
| Run once after it finishes | `queue` | One run waits. Later starts fold into it. |
| Run at the same time | `parallel` | Another run starts, up to 16 triggered runs at once. |

## Missed and failed runs

The Triggers section and `Raticode rattish triggers history` list every occurrence. `--problems` shows only failed, missed, skipped and interrupted ones. A failed run shows the reason, and its full record is under Runs as usual.

- **Cron.** A scheduled time more than a minute old when the scheduler reaches it counts as missed, whether the computer was asleep or nothing was running. By default Raticode only records it. Choose **Show as missed and run once** (`--missed run_once`) to also run once to catch up.
- **File and webhook.** These can't be replayed. When the scheduler comes back after more than two minutes away, they record a missed entry with the length of the gap.
- **Interrupted runs.** A queued run that never started and a run cut off by a crash are recorded too.

## Start at login

The **Start at login** switch, or `Raticode rattish triggers login on`, adds a per-user login entry that runs `Raticode rattish triggers start`. It needs no Raticode window, but the computer must be on and logged in.

| System | Login entry |
| --- | --- |
| Linux | `~/.config/autostart/raticode-triggers-*.desktop` |
| macOS | `~/Library/LaunchAgents/com.raticode.triggers.*.plist` |
| Windows | A script in your Startup folder |

- The entry keeps the `PATH` of the process that switched it on, so providers are found at login. Other environment variables aren't copied, so a workflow that needs an API key from your shell profile should read it another way.
- Only one process runs a data directory's triggers at a time. If you open the desktop app while the background scheduler holds them, the app waits on standby and takes over if the scheduler exits.
- The app's Stop button can't reach a run the background scheduler started.

## Commands

| Command | Purpose |
| --- | --- |
| `Raticode rattish triggers list [PATH]` | Show triggers, next runs, and scheduler state |
| `Raticode rattish triggers update ID ...` | Change a trigger |
| `Raticode rattish triggers enable ID` / `disable ID` | Turn a trigger on or off |
| `Raticode rattish triggers remove ID` | Delete a trigger and its history |
| `Raticode rattish triggers rotate-token ID` | Replace a webhook token |
| `Raticode rattish triggers history [ID]` | Show recent runs |
| `Raticode rattish triggers status` | Show whether anything is running triggers |
| `Raticode rattish triggers start` | Run triggers in the foreground |
| `Raticode rattish triggers login on\|off\|status` | Manage Start at login |
| `Raticode rattish triggers webhook-port [PORT]` | Show or change the webhook port |

Options and limits:

- `add` and `update` also take `--label`, `--disabled` (create it turned off), `--recursive`/`--no-recursive` for folders, `--overlap` and `--format json`.
- Debounce must be between 0.1 and 3,600 seconds.
- `history --limit N` shows up to 1,000 events. Raticode keeps the latest 200 per trigger.

See also: [Rattish language](./rattish.md), [Validation and preflight](./validation.md), [Schedules](./schedules.md) and [File watchers](./watchers.md) for the older services, [Import and export](../studio/sharing.md) for what an exported workflow leaves behind, and the [CLI command index](../reference/cli-commands.md).
