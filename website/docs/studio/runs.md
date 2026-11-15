---
title: Runs and approvals
description: Validate workflows, inspect run timelines, review outputs, and resolve approval steps.
pageClass: docs-layout
---

# Runs and approvals

A run records the steps that actually happened: branch choices, node failures and repeated activations.

## Before starting

1. Save the workflow.
2. Review its commands, destinations, agent prompts and overwrite settings.
3. Validate it: the source compiles.
4. Check preflight: this machine can run it.

Neither check runs the workflow.

In the desktop app, run Rattish workflows with **Local**. Run preview's **Remote** target queues the run for the legacy runner queue, which loads workflows through the legacy loader; see [Runner queue](../workflows/runner-queue.md).

## Start and inspect

Choose **Run**. **Run preview** opens first and lists:

- each node's side effects
- the provider CLIs its agent nodes need
- the order the nodes run in

If the workflow declares inputs, fill in values that match their schemas. Then choose **Run workflow**.

<AppScreenshot name="run-preview" caption="Run preview for a four-node workflow. Nothing runs until you choose Run workflow." />

Open the bottom panel:

| View | Use it for |
| --- | --- |
| Problems | Source errors and readiness diagnostics |
| Run Timeline | The sequence of node activations and their outcomes |
| Runs | Current and saved run records |
| Tests | The workflow's [test set](../workflows/tests.md): expected cost, pass or fail per case, and changes since the last revision |
| Terminal | Commands you run manually |

Select a run to see each node's output and errors. A failed node isn't always a failed run: with `allow-fail: true`, a failure can route to a recovery branch on purpose. Check the workflow's final outcome separately from any one node's result.

<AppScreenshot name="studio-workflow-run" caption="A finished run. run-tests shows its allowed failure, which routed to fix; the other nodes succeeded, and Run Timeline lists every activation." />

## Every workflow's runs

::: info New in 0.5.5
The Workflow runs view and `rattish runs --all` are new in Raticode 0.5.5. The phone's **All runs** list needs Raticode Mobile 0.35.0.
:::

**Runs** in the bottom panel belongs to the open workflow. To see what ran across all of them, such as overnight, choose **All workflow runs** under **New Workflow** in the Workflows sidebar.

The **Workflow runs** view lists runs from every registered workflow, newest first:

| Column | Shows |
| --- | --- |
| Workflow | The workflow's name and its project |
| Started by | **Manual** (the Studio or a terminal), **Trigger** (a schedule, file watch, webhook or polling trigger), **Phone** (Raticode Mobile) or **Rem** (Rem, its workers or organization agents) |
| Status | Passed, failed, running, stopped or interrupted, whether it waits on an approval, and **switched provider** when a model step moved to a [fallback provider](../nodes/agent.md#fallback-providers) (hover for which and why) |
| Started | How long ago it started; hover for the exact time |
| Duration | How long it ran, or has been running |
| Tokens and Est. cost | The run's [model usage](#model-usage-and-cost); runs saved before usage tracking show a dash |

The line under the title totals the runs that match, such as "6 workflow runs: 5 passed, 1 failed · est. $0.42". Filter by status, workflow and time range (last 24 hours, 7 days, 30 days or all time). Select a run to open its workflow in the Studio with that run's timeline and outputs.

The list reads a small summary of each run, not its full record, so it stays quick with many projects and runs. Runs saved before this release count as **Trigger** when they had trigger events and **Manual** otherwise.

### Pause all triggers

The **Pause all triggers** switch in the view's header pauses every [trigger](../workflows/triggers.md#pause-all-triggers) on this computer at once (from 0.5.5). The line beside it counts how many triggers are on, or says when the pause began. While paused, scheduled times are skipped rather than caught up. Turning the switch off resumes exactly the triggers that were on before.

In a terminal, `Raticode rattish runs --all` prints the same list. Add `--status`, `--since` (an ISO time, or a span such as `30m`, `24h` or `7d`), `--workflow WORKFLOW_ID` and `--output text`. JSON output includes `totals` with the same summary line. See the [CLI reference](../reference/cli-commands.md#raticode-rattish-runs-list).

## Model usage and cost

::: info New in 0.5.5
Usage per run and node, and the `max-tokens` and `max-cost-usd` limits, are new in Raticode 0.5.5.
:::

Every model call a run makes is counted for that run and the node that made it, including retries, structured-output repairs and calls made by a [workflow node](../nodes/workflow.md)'s child workflow. [rem-prompt](../nodes/rem-prompt.md) and [rem-task](../nodes/rem-task.md) nodes hand their work to Rem, so Rem's model calls count toward Rem's own task, not the run, and the run's limits don't cover them.

- **Runs** shows the tokens and estimated cost of each run under its name.
- Above the timeline of a selected run, **Model usage** gives the run's calls, tokens and estimated cost, any limits it ran with, and a line per node.
- Select a node to see its model calls, tokens and estimated cost for that run, and the provider and model that answered. When the step switched to a [fallback provider](../nodes/agent.md#fallback-providers), the node lists each switch and its reason, and Run Timeline has a line for it. The run's limits cover the calls of every provider the step tried.
- In a terminal, `Raticode rattish runs WORKFLOW_ID --output text` adds **TOKENS** and **EST. COST** columns, and `Raticode rattish logs WORKFLOW_ID [RUN_ID] --output text` starts with the run's usage and a line per node. JSON output has the same numbers under `usage`.

The estimated cost is what the provider reported for each call, such as Claude Code's cost estimate; Raticode doesn't price tokens itself. When a provider doesn't report a value, it reads **unknown**, never $0. When only some calls reported it, the total reads **at least** that amount and says how many calls are missing. Runs saved before usage tracking show no usage.

The usage of each call also goes to the [usage ledger](../rem/usage.md) on this computer, tagged with the run and node.

To cap a run, set `max-tokens` or `max-cost-usd` in the workflow (in Studio: the workflow's **General** settings). See [Workflow limits](../workflows/rattish.md#workflow-limits).

An agent node's whole provider session used to count as one call, checked only when the session ended. Now Claude Code starts with a spending cap set from the run's remaining budget, and when it stops there, the run fails with `RATTISH_COST_LIMIT_EXCEEDED`. Providers that report usage while they run (Claude Code, OpenCode and Copilot for tokens) are counted as they go, and Raticode stops the node once the run passes its limit. The node then shows the same budget error as the run. Providers that report usage only at the end are still checked after each call, so one call can go past a limit. [What a limit covers inside an agent node](../workflows/rattish.md#what-a-limit-covers-inside-an-agent-node) lists each provider.

### Cached steps

New in 0.5.5. A [common-llm-task](../nodes/common-llm-task.md#result-cache) or [jev](../nodes/jev.md#result-cache) node with `cache: DURATION` can reuse an answer an earlier run saved. Its Run Timeline line shows **Cached** and says it made no model call, and the node shows **Cached** with when the answer was saved. Its usage is 0 tokens and $0, **Model usage** counts it under **cached**, and it adds nothing to the usage ledger or to the run's limits. `Raticode rattish run` prints a line for each cached step.

Clear a workflow's saved answers from **Workflow settings > Result cache** or with `Raticode rattish cache clear`.

## Repeated nodes

Cycles and loops can run a node more than once. Each activation records which branch or loop iteration started it. One box on the graph doesn't mean the node ran once.

## Resume and run again

::: info New in 0.5.5
Resuming runs that were started with inputs or by a trigger, and **Run again with the same inputs**, are new in Raticode 0.5.5. In 0.5.4 and earlier, a run started with inputs or by a file or webhook trigger can't be resumed.
:::

Pick a run in the run menu (**More graph actions › Select workflow run**):

- **Resume** starts a new run that reuses the steps that finished and runs the failed or cut-off step again, then the steps after it. It works for failed, stopped and interrupted runs of the workflow as it was then. If the workflow changed since, start a new run instead.
- **Run again with the same inputs** starts a new run of the workflow as it is now, with the inputs and trigger events the earlier run used. It works for a run with any outcome. The new run's record names the run it repeated (`rerun_of`).

When a button is off, hover it to see why.

### Saved inputs and trigger events

Each run saves the inputs it was started with and the [trigger events](../workflows/triggers.md#trigger-events) that started it, so Resume and Run again can use them:

- They are saved beside the run's record, in the workflow's run folder, as `RUN_ID.inputs`. Only your user account can read the file.
- Up to 256 file events are saved, and a webhook body up to 1 MiB, the most a trigger sends. A run started with more saves none of its values, and Resume and Run again say why.
- An input whose schema says `"format": "password"` is never saved. When you resume or run again, the app asks you to enter it again. Workflow [secrets](../workflows/inputs.md#secrets) are read from the keychain while a run goes, so they are never saved either.
- A run whose inputs were invalid saves nothing.
- The saved values are deleted with the run: by the automatic clean-up of old runs, by **Prune** in the Runs panel's retention settings or `Raticode rattish prune`, and when you delete the workflow. If a run's record is removed some other way, such as by hand, its saved values are deleted when the workflow's next run ends, once they are 10 minutes old.

Runs from before this was added have no saved values. Start a new run for those.

From a terminal, `Raticode rattish runs rerun RUN_ID` runs a run again and `Raticode rattish resume WORKFLOW_ID RUN_ID` resumes one. Give password inputs with `--input name=value`. See the [CLI reference](../reference/cli-headless.md#workflows). On the phone, see [Workflows](../mobile/workspace.md#resume-and-run-again).

## Stop and side effects

**Stop** on the selected run asks it to cancel. Cancelling doesn't undo a file write, command or network request that already finished, so check the workspace before you restart.

## Approval steps

An `approval-gate` node asks you for a decision.

- Read its message and subject before you approve or reject.
- Routes can branch on the decision, including a timeout.
- Provider tool approvals are separate. The coding CLI's own permissions control those.

Fields are in the [approval-gate reference](../nodes/approval-gate.md); conditional routes are in [Routes](../workflows/routes.md).

Run files for a workflow in a project are kept in its workflow folder. Standalone sources keep them in app storage.

## Old runs

::: info New in 0.5.5
Automatic clean-up of Rattish runs and `Raticode rattish prune` are new in Raticode 0.5.5. Version 0.5.4 and earlier clean up only legacy TOML workflow runs on their own.
:::

Each workflow keeps its runs under three retention settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| Keep latest runs | 100 | The newest runs always stay, however old they are |
| Keep runs for days | 14 | Older runs stay until they are this many days old |
| Keep failed runs for days | 30 | The same, for runs that failed, failed preflight or got invalid inputs |

A run's age counts from when it started. Stopped and interrupted runs use **Keep runs for days**.

In the Runs panel's retention settings, a changed number is saved when you leave the field or press Enter. Clearing a field and leaving it keeps the saved number.

After every run ends, Raticode deletes that workflow's runs these settings no longer keep. It does this however the run started: from Studio, a trigger or schedule, your phone, a resume or the CLI. The run that just ended always stays until the next clean-up.

A clean-up never removes a run that:

- is still running
- is waiting on an approval step
- is being resumed, or was resumed by a run that is still going

To change the settings or clean up now, choose the trash can button (**Run retention settings**) above the run log. **Preview** counts the runs that would go and deletes nothing. **Prune** deletes them. Changes apply to that workflow only.

From a terminal, `Raticode rattish prune PATH` lists the runs the saved settings no longer keep, and `--apply` deletes them. `--keep-last`, `--keep-days` and `--keep-failed-days` replace the saved settings for that one command. See the [CLI command index](../reference/cli-commands.md#raticode-rattish-prune).

A run record that can't be read, such as a damaged file, is left alone and doesn't stop the **Runs** list from loading. The CLI and the backend's run list report how many records they skipped.

