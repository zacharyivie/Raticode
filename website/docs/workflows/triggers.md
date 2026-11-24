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

A trigger runs only the version of the workflow you approved. If the workflow or a file it runs changes, the trigger skips its starts until you look at the change and approve it. See [Approved versions](#approved-versions).

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

Each new trigger has a [Time limit](#time-limit) of 6 hours. Add `--max-run-time 30m`, `--max-run-time 1h30m` or `--max-run-time none` to change it.

### From a repeating Rem task

*New in Raticode 0.6.0.*

A repeating Rem task can become a workflow with **Make it a workflow** in its menu (see [Make it a workflow](../rem/home.md#make-it-a-workflow)). Its finished card's **Switch over…** adds a cron trigger for you:

- The schedule and time zone are the task's own. **Daily** and **Weekly** become a cron expression such as `30 7 * * *` or `0 9 * * mon,fri`. A **Custom** cron schedule keeps its fields, with numbered weekdays written as day names, because Rem's own schedules count weekdays from Monday = 0 and triggers follow crontab, where 0 is Sunday. The trigger fires on the days the task did.
- It is labelled "Switched over from Rem task “…”" and uses the default overlap and missed-run settings.
- It runs only the version you reviewed in **Switch over…**, the same as approving it in [Approved versions](#approved-versions). If the workflow changed since you opened it, nothing is added.
- The task's schedule is paused at the same time. **Resume schedule** on the task turns this trigger off, and switching over again turns the same trigger back on.

### Unsandboxed steps

::: info New in 0.6.0
The sandbox for triggered agent steps and these warnings are new in Raticode 0.6.0.
:::

Triggered runs happen with nobody watching, so agent steps run in an [operating-system sandbox](../nodes/agent.md#sandbox-and-network). When a step can't, because its provider has no sandbox, Raticode runs on native Windows, or the step sets `sandbox: off`, the Triggers section shows **Runs unsandboxed** with each step, provider and reason. **Add trigger** then asks once more: press **Add anyway** to save it. `Raticode rattish triggers add` prints the same list after it saves, and its JSON output has an `unsandboxed` list.

A triggered run also refuses a Codex profile that gives Codex full access, unless the step sets `sandbox: off`. Runs you start yourself allow it, with a **Runs unsandboxed** warning.

From 0.6.0, a sandboxed step whose workflow file widens its own sandbox is listed too, under **Widens its sandbox**: each `extra-paths` entry outside the project, which its shell may write, and `network: any`. **Add trigger** asks once more for these as well, `Raticode rattish triggers add` prints a `Widens its sandbox:` line for each, and its JSON output has a `widened` list. An extra path that is or holds a credential folder isn't a warning: the run stops at preflight.

### Retiring models

::: info New in 0.6.0
Retiring-model warnings are new in Raticode 0.6.0.
:::

A schedule can keep firing long after you last looked at a workflow, so the Triggers section warns before a model it uses stops working. When an `anthropic-api` or `openai-api` step, or one of its fallback entries, uses a deprecated model, it shows **Model retiring** with the step, the provider, the retirement date and the model to move to. Once that date has passed it shows **Model retired**, and [preflight](../nodes/agent.md#retired-and-retiring-models) stops the run until you change the model. `Raticode rattish triggers add` prints the same lines after it saves, and its JSON output has a `models` list.

### Cron schedules

- The expression has five fields: minute, hour, day of month, month, weekday. For example, `0 9 * * 1-5` runs at 9:00 on weekdays and `*/15 * * * *` every 15 minutes.
- Weekdays follow crontab, so `0` and `7` are both Sunday.
- Set a day of the month or a weekday, not both.
- A schedule no date can match, such as `0 9 31 2 *` (February 31), is refused.
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

Each run saves its trigger events in its run folder, readable only by your user account (from 0.5.5). That lets you [resume a triggered run or run it again](../studio/runs.md#resume-and-run-again) with the same changed files or webhook body, on the computer, on the phone or with `Raticode rattish runs rerun RUN_ID`. The saved events are deleted with the run.

### Webhooks

A webhook trigger gets a URL on `127.0.0.1` and a token. Raticode shows the token once and stores only its hash. If you lose it, replace it with **New token** or `rotate-token`.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"ref":"main"}' http://127.0.0.1:8767/rattish-hooks/TRIGGER_ID
```

- The call returns `202` with `started`, `queued` or `skipped`.
- Requests from a web page are refused.
- A `loop` with a `trigger-events` source receives the JSON body as `payload`. Treat it as untrusted input.
- The body must be strict JSON in UTF-8, or empty (the payload is then `null`). A body the run record couldn't store gets `400` with the reason and starts nothing: `NaN` or `Infinity`, whole numbers beyond 9,007,199,254,740,991 (send those as strings), text with a lone surrogate escape, or nesting thousands of levels deep.
- On a computer with other user accounts, keep the token off the command line, where other accounts can read it. `curl -H @header-file` reads the header from a private file.
- The listener opens only while an enabled webhook trigger exists. If port 8767 is taken, the Triggers section says so, and `Raticode rattish triggers webhook-port PORT` changes it.

## Approved versions

*New in Raticode 0.6.0.*

A trigger starts the workflow with nobody watching, so it runs only the version you approved, and an approval covers only what you were shown:

- **Adding a trigger** approves the workflow as it is at that moment.
- **Saving the workflow in the Studio window**, by editing its source or the graph, approves the workflow file you saved and nothing else. If a prompt file, script or child workflow changed since your last approval, the trigger still says **Workflow changed since you approved it** and **Review changes** lists that file.
- **Changing or turning on a trigger** approves nothing. If changes are waiting for review, they keep waiting: the Triggers section and the phone open **Review changes** right after you save or turn it on, and `Raticode rattish triggers update` and `enable` print the diff and ask, or approve with `--yes`.

The approval covers the workflow's text and these files:

- prompt files (an agent's `prompt-path` and a `prompt-file` node's `template-path`)
- scripts (`script-path` of `python-script` and `shell-script` nodes)
- child workflows called by a `workflow` node, with their own prompt files, scripts and child workflows
- the definition of each MCP server an [`mcp-tool`](../nodes/mcp-tool.md) step calls, from Rem's MCP server list: its command, arguments and the names of the environment variables it gets (never their values), or its URL. **Review changes** shows it as an **MCP server** entry. It doesn't cover the package a runner such as `npx` or `uvx` downloads, so a server whose package has no exact version is marked **Package version not pinned: each run fetches the newest release** (see [Pin the server's package](../nodes/mcp-tool.md#pin-the-server-s-package)). New in 0.6.0.
- each file inside the project folder that such a local server's command or arguments name, such as `tools/server.py` in `python3 tools/server.py`. A relative path counts from the step's `working-dir`. **Review changes** shows it as a **Script**. New in 0.6.0.

Other files the workflow reads or writes don't count, and neither does code those files load themselves: modules a Python script imports, files a shell script sources, or anything an MCP server's script loads. A change to one of those doesn't stop the trigger, so keep code you want reviewed in a covered file. A path that comes from a run-time value, such as a `with` binding or a <span v-pre>`{{...}}`</span> template, isn't known until the run, so it isn't covered.

Before each triggered start, Raticode compares those files with the approved version. If anything changed:

- The start is skipped and the trigger's history shows **Workflow changed since you approved it.** More starts skipped for the same reason add to that one entry's count.
- The paired phone gets one Workflow message for the change, however many starts it stops. A later, different change sends another.
- The trigger's card in the Triggers section says **Workflow changed since you approved it** with a **Review changes** button.

**Review changes** lists each changed file with a diff, in the same view as the test sets' **Show source change**: lines added in green, removed in red. A file the workflow now runs for the first time shows as **Now used**, one it no longer runs as **No longer used**. **Approve this version** approves exactly what you reviewed. If the workflow changed again in the meantime, nothing is approved and the new diff loads.

Some changes can't be shown in full. Review changes shows up to 2,000 diff lines on the computer and 60,000 characters on the phone, across all files, and it can't show a binary file or one over 200,000 characters at all. When any file's change was cut or couldn't be shown, **Approve this version** asks a second time before approving. It names those files and offers to open them, so you can read the rest first. **Approve without seeing all** then approves them. A file the workflow no longer runs, or one that's missing, doesn't need the second question.

An MCP server entry, new or changed, whose package has no exact version asks the same second question, on the computer, the phone and with `Raticode rattish triggers approve`: its approved command can run a release you never saw. Its button says **Approve anyway** when nothing else is waiting for the second yes. Pin the version and the question goes away. New in 0.6.0.

### Checked again during the run

A step could still change a later step's file after the run starts, or another program could. So a triggered run checks each covered file once more right before it uses it, comparing its SHA-256 with the approved version:

- A Python script is checked immediately before Python starts.
- A shell script runs from a checked copy in a folder for that run under Raticode's data folder, because Bash reads a script while it runs and would pick up later edits. A sandboxed agent shell can't change that folder (see [Sandbox for workflow agent steps](../advanced/security.md#sandbox-for-workflow-agent-steps)), and it's removed when the run ends. The script still starts in the project folder, but `$0` names the copy, so find the script's own files from the project folder rather than from `$0`.
- An agent's prompt file and a `prompt-file` template are checked as they're read, so the text checked is the text used.
- A child workflow is checked when its `workflow` step starts it.
- An MCP server's definition is checked when its `mcp-tool` step starts the server or connects to it. If it changed, the step fails with **This MCP server's definition changed since you approved the workflow, so it wasn't started.**
- A project file an MCP server's command or arguments name is checked right before its `mcp-tool` step starts the server, so a changed one fails the step with the message below and the server doesn't start. So does such a file that wasn't there when you approved the workflow. Like a Python script, it's checked in place, so a server that reads it again later isn't checked again.

If a file changed:

- That step fails with **This file changed during the run, so it wasn't run.** The rest of the run goes on as it would after any failed step.
- The trigger counts as changed until you approve it again, even if the file changes back. **Review changes** names the file.
- The paired phone gets one Workflow message, **A file changed during a triggered run**, through the same approval notice. Starts skipped afterwards don't send another.

A trigger with **Run edits without approval** on skips these checks. A path known only at run time isn't covered, as above.

Manual runs are never held back or checked, whether started from the Studio, the phone, Rem or `Raticode rattish run`.

### Only you can approve

Approving takes the same user-only path as accepting a ticket's work, so an agent can't approve its own edit:

- Rem, its workers and organization workers can still add or change triggers through `Raticode rattish triggers`, but from inside an agent turn that approves nothing. A trigger an agent adds waits with **Waiting for you to approve this workflow version.**, and its first review shows every file as new.
- `Raticode rattish triggers approve` refuses to run inside an agent turn (exit code 5), and limited agent tokens can't reach the approve route.
- Inside an agent turn, `rotate-token`, `webhook-port PORT`, setting a `budget` and `resume --all` are refused too (exit code 5), so an agent can't take a webhook's token, move the webhook listener, loosen the daily limit or turn every trigger back on. Reading the port and the budget, and `pause --all`, still work.
- The paired phone approves as you do. Its **Approve this version** passes the phone's [suspicious-request check](../mobile/pairing.md#suspicious-requests-lock-the-phone) first, so a locked phone can't approve.

Turning a trigger off approves nothing.

### Run edits without approval

Each trigger has a **Run edits without approval** switch, off by default. With it on, the trigger runs the workflow even after it changes, without asking. Only you can turn it on: from the Triggers section, or with `--run-edits-without-approval` on `add` or `update` outside an agent turn. `update --require-approval` turns it off again.

### Headless servers

On a computer with no desktop window, review and approve from a terminal:

```bash
Raticode rattish triggers list                 # approval=changed marks a trigger waiting for you
Raticode rattish triggers changes TRIGGER_ID   # what changed, file by file
Raticode rattish triggers approve TRIGGER_ID   # prints the diff, then asks
```

`approve` prints the diff before asking. `--yes` approves without the question. With `--format json`, `--yes` is required, and `--fingerprint` approves only the version a previous `changes --format json` returned; if the workflow changed since, it exits with code 4.

When a file's diff was cut, the file can't be shown or an MCP server's package has no exact version, `approve` lists those entries and asks a second time. With `--yes` it also needs `--unseen`; without it, nothing is approved and it exits with code 2. With `--format json` the refusal lists the files under `unseen`.

`update` and `enable` save your change either way. If the trigger has changes waiting for review, they print the diff and ask at a terminal. `--yes` approves without asking, plus `--unseen` when part of the change can't be shown. Without a terminal and without `--yes`, the changes keep waiting.

### Triggers from before this update

The first time the scheduler or the Triggers section looks at a trigger saved before approvals existed, it approves the workflow as it is then, so updating Raticode doesn't stop triggers that were running.

## If the workflow is already running

Each trigger has an overlap policy, set in the Triggers section or with `--overlap skip|queue|parallel`. Runs you started by hand count too.

| Policy | CLI value | What happens |
| --- | --- | --- |
| Skip this run | `skip` | The start is dropped and recorded as skipped. This is the default. |
| Run once after it finishes | `queue` | One run waits. Later starts fold into it: a file trigger's waiting run gets every file that changed while it waited (up to 256), and a webhook's gets the latest call's body. |
| Run at the same time | `parallel` | Another run starts, up to 16 triggered runs at once. |

From 0.6.0, the history line says which run was in the way and how long it had been going, for example **Skipped because run 20261123T101500123456Z-3f2a91c0b7d4 has been running for 7 h 12 min.** A waiting `queue` start says **Waiting for run … to finish. It has run for 12 min.** A long time there usually means a run is stuck: open it under Runs, or stop it with `Raticode rattish stop WORKFLOW --run RUN_ID`. A [Time limit](#time-limit) on the trigger stops its own stuck runs for you.

## Time limit

::: info New in 0.6.0
Time limits for triggered runs are new in Raticode 0.6.0.
:::

A triggered run that hangs, such as a step waiting on a server that never answers, would otherwise keep a `skip` trigger from ever starting again. So each trigger has a **Time limit**: when a triggered run has gone that long, the scheduler stops it.

- The default is **6 hours**. Triggers saved before 0.6.0 get it too. Set any length, or none, in the trigger's form in **Workflow settings › Triggers** (leave **Time limit** empty for none), or from the CLI:

  ```bash
  Raticode rattish triggers update TRIGGER_ID --max-run-time 2h
  Raticode rattish triggers update TRIGGER_ID --max-run-time none   # no limit
  ```

  Lengths take `s`, `m`, `h` and `d`, such as `90s`, `30m` or `1h30m`, up to 30 days.
- The run is stopped the same way as **Stop**, so it ends as **Stopped** under Runs. The trigger's history entry shows it as **Failed**: **Stopped after 6 h, this trigger's time limit. Raise the limit or set a timeout in the workflow.**
- The workflow's own [`timeout`](./rattish.md#workflow-limits) still applies. Whichever is shorter ends the run: at the workflow's `timeout`, the run fails with its timeout error as before.
- Only triggered runs have it. Runs you start yourself in the app or a terminal, from the phone, or by Rem don't.
- `Raticode rattish triggers list` shows it as `time-limit=6h`, and `list --format json` includes it as `maxRunSeconds` (seconds, or `null` for none). `triggers export` and `apply` carry it as `maxRunSeconds`; a file without it gets 6 hours.
- The phone shows each trigger's Time limit on the workflow's **Triggers** tab. It is set on the computer.

## Usage limits

::: info New in 0.6.0
Waiting for a usage limit to reset is new in Raticode 0.6.0.
:::

When a triggered run fails because one of its agent steps hit the provider's usage limit, such as Claude Code's 5-hour or weekly limit, retrying right away only fails again. So by default the trigger waits for the limit to reset and then resumes the run.

- The history entry shows **Waiting for usage limit** instead of **Failed**, with a line such as **Waiting for Claude Code's usage limit to reset at 05:00; resumes then.** When the provider names no reset time, it says **Claude Code's usage limit was reached; trying again at 05:30.**
- Two minutes after the reset the provider named, or every 30 minutes when it named none, the scheduler [resumes](../studio/runs.md#resume-and-run-again) that run: steps that passed aren't run again, and the run continues from the step that hit the limit. The entry then shows **Resumed run … after Claude Code's usage limit reset.** and ends as the resumed run ends.
- A run waits at most **6 times**, the same as Rem's tasks. After the sixth wait the entry ends **Failed** with the last error, such as **Still at Claude Code's usage limit after 6 waits for it to reset.**
- Only a provider's usage limit counts. A credit balance that is too low, an organization's spend cap (`enforced_spend_limit_reached`) and the workflow's own [`max-cost-usd` or `max-tokens`](./rattish.md#workflow-limits) end the run failed at once, because waiting doesn't fix them.
- The resume is a triggered start like any other: it needs the [approved version](#approved-versions), it is skipped while [all triggers are paused](#pause-all-triggers) or the trigger is off, and it counts toward the [daily limit](#daily-limit). It waits for any other run of the workflow to finish first.
- A waiting run counts as running for the [overlap policy](#if-the-workflow-is-already-running): a `skip` start says **Skipped because run … is waiting for Claude Code's usage limit to reset.**, and a `queue` start waits for the resumed run to finish.
- Waits are saved with the triggers on this computer, so a run still resumes after Raticode or its scheduler restarts. Nothing resumes while no scheduler is running; the next one resumes any run whose reset has passed.
- **Workflow runs**, a workflow's **Runs** list and the phone show the failed run as **Waiting for usage limit**, with the time it resumes, until the scheduler resumes it.
- Turn it off for a trigger with **Wait for a usage limit to reset** in its form in **Workflow settings › Triggers**, or from the CLI. Off, such a run ends **Failed** as before:

  ```bash
  Raticode rattish triggers update TRIGGER_ID --usage-limit fail
  Raticode rattish triggers update TRIGGER_ID --usage-limit wait   # the default
  ```

  `add` takes `--usage-limit` too. `triggers list` shows `usage-limit=wait` or `usage-limit=fail`, and `list --format json`, `export` and `apply` carry it as `waitForUsageLimit`. Triggers saved before 0.6.0 wait.

## Missed and failed runs

The Triggers section and `Raticode rattish triggers history` list every occurrence. `--problems` shows only failed, missed, skipped and interrupted ones. A failed run shows the reason, and its full record is under Runs as usual.

- **Time limit reached.** A run the scheduler stopped at the trigger's [Time limit](#time-limit) is a failed entry that says so, such as **Stopped after 6 h, this trigger's time limit.** Its run record shows **Stopped**.
- **Waiting for usage limit.** A run that hit its provider's usage limit waits for the reset and resumes. See [Usage limits](#usage-limits).
- **Skipped while busy.** A start dropped because the workflow was already running names that run and how long it had been running. See [If the workflow is already running](#if-the-workflow-is-already-running).

- **Cron.** A scheduled time more than a minute old when the scheduler reaches it counts as missed, whether the computer was asleep or nothing was running. By default Raticode only records it. Choose **Show as missed and run once** (`--missed run_once`) to also run once to catch up.
- **File and webhook.** These can't be replayed. When the scheduler comes back after more than two minutes away, they record a missed entry with the length of the gap.
- **Paused.** Times that fall while [all triggers are paused](#pause-all-triggers) are recorded as skipped, not missed, and never caught up.
- **Interrupted runs.** A queued run that never started and a run cut off by a crash are recorded too. A run whose process ended without saving its result, for example when the computer restarted, shows as disconnected rather than still running, so it doesn't make `skip` or `queue` triggers wait. On Linux and Windows this holds even when another program has since been given the old process ID. On macOS Raticode checks the process ID alone, so in the rare case another program has taken it, the run keeps showing as running until you stop it or that program ends.

## Pause all triggers

::: info New in 0.5.5
Pausing every trigger at once, on the computer, in the CLI and on the phone (Raticode Mobile 0.35.0), is new in Raticode 0.5.5.
:::

One switch pauses every trigger on this computer, for example before travel or while you change several workflows. Turn on **Pause all triggers** at the top of the [Workflow runs](../studio/runs.md#pause-all-triggers) view, or run:

```bash
Raticode rattish triggers pause --all
Raticode rattish triggers resume --all
```

The paired phone has the same switch under **More › Workflows**; see [Workflows on the phone](../mobile/workspace.md#triggers).

- Pausing doesn't change any trigger's own on/off setting. Resuming brings back exactly the triggers that were on, and the ones that were off stay off. You can still turn single triggers on or off while paused; they take effect when you resume.
- While paused, nothing starts on its own. Cron times are recorded as **skipped** and are never caught up, even with **Show as missed and run once**. File changes aren't watched. Webhook calls get `409` and are recorded as skipped. A queued run that was waiting is dropped and recorded as skipped.
- The pause is saved, so it holds across restarts and for the background scheduler. A cron time that fell inside a pause still counts as skipped if nothing was running at the time.
- The Triggers section of each workflow shows **All triggers are paused**, and `Raticode rattish triggers list` and `status` say so too.
- Without `--all`, `pause` and `resume` change nothing and point to `disable ID` and `enable ID` for a single trigger.

## Daily limit

::: info New in 0.6.0
Daily limits for triggered runs are new in Raticode 0.6.0.
:::

A trigger can have a **Daily limit** on estimated cost, tokens or both, so a schedule or a busy folder can't run up a bill while you're away. Set it in the trigger's form in the Triggers section (leave a field empty for no limit), or from the CLI:

```bash
Raticode rattish triggers update TRIGGER_ID --daily-max-cost-usd 2.50 --daily-max-tokens 200000
Raticode rattish triggers update TRIGGER_ID --daily-max-cost-usd 0   # remove the cost limit
```

There is also an optional limit for the whole computer, over every triggered run of every workflow. Set it next to **Pause all triggers** in the [Workflow runs](../studio/runs.md#pause-all-triggers) view (**Set daily limit**), or:

```bash
Raticode rattish triggers budget                                     # show it and today's use
Raticode rattish triggers budget --max-cost-usd 10 --max-tokens 1000000
Raticode rattish triggers budget --max-cost-usd 0 --max-tokens 0     # remove it
```

How it works:

- **Each run is capped at what is left.** A triggered run starts with its run limit set to the smaller of the workflow's own [`max-cost-usd` or `max-tokens`](./rattish.md#workflow-limits) and what is left of the day, under the trigger's limit and the computer's. The run then stops the same way as at `max-cost-usd`, including Claude Code's own spending cap. Its error says it reached its **remaining daily** limit.
- **Once a limit is used up**, new triggered starts are recorded as **Skipped** with the reason **Daily limit reached** and what was used, until midnight. Queued runs that come due are skipped too. A webhook call gets `202` with `skipped`.
- **The day** ends at midnight in the trigger's time zone: a cron trigger's own time zone, or this computer's for file and webhook triggers. The computer-wide limit uses this computer's time zone. A run counts toward the day it started.
- **The phone** gets one Workflow message the first time each limit is reached that day, under the **Workflow messages** alert switch.
- **Only triggered runs count.** Runs you start yourself in the app or a terminal, from the phone, or by Rem don't count toward a daily limit and are never blocked by one.
- **Unknown cost** follows the [`max-cost-usd` rule](./rattish.md#workflow-limits): with a cost limit, a call that doesn't report its cost ends the run, and what it cost isn't counted. Saving a trigger with a daily cost limit warns when the workflow uses a provider that may not report cost, such as Codex or Rem. For those, use a token limit. A [local model server](../setup.md#local-model-servers) reports its calls as $0, so they never use up a cost limit.
- While a triggered run is still running, what was left when it started counts as used for any other start under the same limit. A run that comes due beside it is skipped with **Daily limit reached**, so runs that overlap can't together pass the limit. Once the running run finishes, only what it really used counts, and the next start gets the rest. A trigger whose overlap is `queue` waits for its own running run instead of being skipped. Screens and `triggers list` show what was really used.
- `Raticode rattish triggers list` shows each trigger's use today (`daily: $0.40 of $2.50 used today`), and `list --format json` includes it as `dailyLimit`. `triggers export` and `apply` carry each trigger's limits as `dailyMaxCostUsd` and `dailyMaxTokens`.

On the phone, each trigger in a workflow's **Triggers** tab and the **Pause all triggers** card show the limit and today's use; see [Workflows on the phone](../mobile/workspace.md#triggers). Limits are set on the computer.

## Stop a triggered run

Stop a triggered run the same way as any other (from 0.5.5): **Stop** in the app's Runs, **Stop** on the phone, or `Raticode rattish stop WORKFLOW --run RUN_ID`. This works whichever process started the run, including the background scheduler from `rattish triggers start` or **Start at login**, on Linux, macOS and Windows.

- The request goes to the process running the workflow, which ends it within about a second as **Stopped**, the same as a run stopped in the app. A node in the middle of a call is cancelled, so its work may be partly done.
- Until then, Runs shows **Stop requested**, and so does the phone.
- `rattish stop` works with no backend running. Without `--run` it stops every run of that workflow on this computer.

## Start at login

The **Start at login** switch, or `Raticode rattish triggers login on`, adds a per-user login entry that runs `Raticode rattish triggers start`. It needs no Raticode window, but the computer must be on and logged in.

| System | Login entry |
| --- | --- |
| Linux | `~/.config/autostart/raticode-triggers-*.desktop` |
| macOS | `~/Library/LaunchAgents/com.raticode.triggers.*.plist` |
| Windows | A script in your Startup folder |

- The entry keeps the `PATH` of the process that switched it on, so providers are found at login. Other environment variables aren't copied, so an API key exported in your shell profile isn't there. Save it as a [workflow secret](./inputs.md#secrets) instead (from 0.5.5): `secret["NAME"]` reads the system keychain, which most desktops unlock when you log in. If the keychain is still locked when a trigger fires, preflight stops that run and says so.
- Only one process runs a data directory's triggers at a time. If you open the desktop app while the background scheduler holds them, the app waits on standby and takes over if the scheduler exits.

## Commands

| Command | Purpose |
| --- | --- |
| `Raticode rattish triggers list [PATH]` | Show triggers, next runs, and scheduler state |
| `Raticode rattish triggers update ID ...` | Change a trigger |
| `Raticode rattish triggers update ID --max-run-time 2h\|none` | Set the trigger's [Time limit](#time-limit), or remove it with `none`. `add` takes it too; the default is 6 hours |
| `Raticode rattish triggers update ID --usage-limit wait\|fail` | Wait for a provider's [usage limit](#usage-limits) to reset and resume the run (the default), or end it failed. `add` takes it too |
| `Raticode rattish triggers enable ID` / `disable ID` | Turn a trigger on or off. Turning it on approves nothing; changes waiting for review print and ask, or `--yes` approves them |
| `Raticode rattish triggers changes ID` | Show what changed since the trigger's workflow version was approved |
| `Raticode rattish triggers approve ID` | Print the changes, then approve this version for the trigger. `--unseen` also approves changes that can't be shown in full |
| `Raticode rattish triggers pause --all` / `resume --all` | Pause or resume every trigger, keeping each one's own setting |
| `Raticode rattish triggers update ID --daily-max-cost-usd N --daily-max-tokens N` | Set or remove (`0`) a trigger's [daily limit](#daily-limit) |
| `Raticode rattish triggers budget [--max-cost-usd N] [--max-tokens N]` | Show or set the daily limit for every triggered run on this computer |
| `Raticode rattish triggers remove ID` | Delete a trigger and its history |
| `Raticode rattish triggers rotate-token ID` | Replace a webhook token |
| `Raticode rattish triggers history [ID]` | Show recent runs |
| `Raticode rattish triggers status` | Show whether anything is running triggers. When nothing is, it says how to start them |
| `Raticode rattish triggers start` | Run triggers in the foreground |
| `Raticode rattish triggers login on\|off\|status` | Manage Start at login |
| `Raticode rattish triggers webhook-port [PORT]` | Show or change the webhook port |

Options and limits:

- `add` and `update` also take `--label`, `--disabled` (create it turned off), `--recursive`/`--no-recursive` for folders, `--overlap`, `--run-edits-without-approval` and `--format json`. `update` takes `--require-approval` to turn that switch off.
- Debounce must be between 0.1 and 3,600 seconds.
- `history --limit N` shows up to 1,000 events. Raticode keeps the latest 200 per trigger.

See also: [Rattish language](./rattish.md), [Validation and preflight](./validation.md), [Schedules](./schedules.md) and [File watchers](./watchers.md) for the older services, [Import and export](../studio/sharing.md) for what an exported workflow leaves behind, and the [CLI command index](../reference/cli-commands.md).
