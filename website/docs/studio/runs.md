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

## Start and inspect

Choose **Run**. **Run preview** opens first and lists:

- each node's side effects
- the provider CLIs its agent nodes need
- the order the nodes run in

If the workflow declares inputs, fill in values that match their schemas. Then choose **Run workflow**.

### File inputs

*New in Raticode 0.6.0.*

A [file input](../workflows/inputs.md#file-inputs) shows **Choose file** and a drop zone, with the kinds of file it takes and its size limit below. Choose a file, drop one from your file manager or the file tree, or type its path in the box.

- A file you choose or drop that is too large, or not a kind the input takes, is marked before anything starts. A typed path is checked when the run starts.
- When the run starts, Raticode copies the file into the run's folder, readable only by your account, and the workflow reads that copy. **Resume**, **Run again** and **Run from here** give the new run its own copy of the earlier run's copy, so changing or deleting the original doesn't matter.

To start from a file instead, right-click it in the file tree and choose **Run workflow with this file**. It lists the workflows in the project that have a file input; pick one and the Run dialog opens with the file filled in. A workflow with more than one file input is listed once for each.

<AppScreenshot name="run-preview" caption="Run preview for a four-node workflow. Nothing runs until you choose Run workflow." />

Open the bottom panel:

| View | Use it for |
| --- | --- |
| Problems | Source errors and readiness diagnostics |
| Run Timeline | The sequence of node activations and their outcomes |
| Runs | Current and saved run records |
| Tests | The workflow's [test set](../workflows/tests.md): expected cost, pass or fail per case, and changes since the last revision |
| Changes | What a run of a [`workspace: worktree`](../workflows/rattish.md#run-in-a-worktree) workflow changed, with **Land** and **Discard**. Shown only for those runs |
| Terminal | Commands you run manually |

Select a run to see each node's output and errors. The Studio's run menu and **All runs** list name each run's status, such as **Failed** or **Succeeded**, and show the first line of a failed or interrupted run's error under its date (new in 0.6.1). A failed node isn't always a failed run: with `allow-fail: true`, a failure can route to a recovery branch on purpose. Check the workflow's final outcome separately from any one node's result.

<AppScreenshot name="studio-workflow-run" caption="A finished run. run-tests shows its allowed failure, which routed to fix; the other nodes succeeded, and Run Timeline lists every activation." />

### A node's output

Select a node after a run and open its **Run** tab. **Latest activation** gives the status and duration of the node's last activation, then its text in tabs:

| View | Shows |
| --- | --- |
| Output | What the node returned. An agent node's answer reads as plain text with its own line breaks; any other result is indented JSON |
| Stdout | What a bash-command or script node printed |
| Stderr | What it printed to its error stream, in red |
| Error | Why the activation failed, in red. A failed activation opens on this view |

Only the views that have text appear. **Copy** copies the view you're looking at. Drag the bottom edge of the text to make it taller. **Raw data** opens the node's whole result as JSON.

When a run's output is too large to send to the app in full, the tab says some of it was cut.

**Activation history** lists each activation of the node, newest first, with its lineage ID under it. Select a finished activation to open its own output and error in the same views. That is how you see what an earlier pass of a loop printed, or what a failed try said before a later one passed.

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
| Status | Passed, failed, running, stopped or interrupted, whether it waits on an approval, and **switched provider** when a model step moved to a [fallback provider](../nodes/agent.md#fallback-providers) (hover for which and why). A failed or interrupted run shows the first line of its error underneath; **Show full error** opens the whole message (new in 0.6.1) |
| Started | How long ago it started; hover for the exact time |
| Duration | How long it ran, or has been running |
| Tokens and Est. cost | The run's [model usage](#model-usage-and-cost); runs saved before usage tracking show a dash |

The line under the title totals the runs that match, such as "6 workflow runs: 5 passed, 1 failed · est. $0.42". Filter by status, workflow and time range (last 24 hours, 7 days, 30 days or all time). Select a run to open its workflow in the Studio with that run's timeline and outputs.

The list reads a small summary of each run, not its full record, so it stays quick with many projects and runs. Runs saved before this release count as **Trigger** when they had trigger events and **Manual** otherwise.

### Pause all triggers

The **Pause all triggers** switch in the view's header pauses every [trigger](../workflows/triggers.md#pause-all-triggers) on this computer at once (from 0.5.5). The line beside it counts how many triggers are on, or says when the pause began. While paused, scheduled times are skipped rather than caught up. Turning the switch off resumes exactly the triggers that were on before.

Under the switch, **Set daily limit** sets an optional [daily limit](../workflows/triggers.md#daily-limit) on the estimated cost, tokens or both of every triggered run on this computer (new in 0.6.0). The line shows how much of it today has used. Once it is used up, triggered starts are skipped until midnight; runs you start yourself aren't affected. **Change** edits it and **Remove limit** clears it.

In a terminal, `Raticode rattish runs --all` prints the same list. Add `--status`, `--since` (an ISO time, or a span such as `30m`, `24h` or `7d`), `--workflow WORKFLOW_ID` and `--output text`. JSON output includes `totals` with the same summary line. See the [CLI reference](../reference/cli-commands.md#raticode-rattish-runs-list).

## Model usage and cost

::: info New in 0.5.5
Usage per run and node, and the `max-tokens` and `max-cost-usd` limits, are new in Raticode 0.5.5.
:::

Every model call a run makes is counted for that run and the node that made it, including retries, structured-output repairs and calls made by a [workflow node](../nodes/workflow.md)'s child workflow. [rem-prompt](../nodes/rem-prompt.md) and [rem-task](../nodes/rem-task.md) nodes hand their work to Rem, so Rem's model calls count toward Rem's own task, not the run, and the run's limits don't cover them.

- **Runs** shows the tokens and estimated cost of each run under its name.
- Above the timeline of a selected run, **Model usage** gives the run's calls, tokens and estimated cost, any limits it ran with, and a line per node.
- Select a node to see its model calls, tokens and estimated cost for that run, and the provider and model that answered. **Provider app** names the version of the provider's app the step started, such as *Claude Code 2.3.1* (new in 0.6.0; runs saved before it show none). When the step switched to a [fallback provider](../nodes/agent.md#fallback-providers), the node lists each switch and its reason, and Run Timeline has a line for it. The run's limits cover the calls of every provider the step tried.
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
- A [file input](../workflows/inputs.md#file-inputs) saves the path of the run's own copy of the file, in `RUN_ID.files` beside the record. Resume and Run again give the new run a copy of that copy, never the original file.
- The saved values are deleted with the run: by the automatic clean-up of old runs, by **Prune** in the Runs panel's retention settings or `Raticode rattish prune`, and when you delete the workflow. If a run's record is removed some other way, such as by hand, its saved values are deleted when the workflow's next run ends, once they are 10 minutes old.

Runs from before this was added have no saved values. Start a new run for those.

From a terminal, `Raticode rattish runs rerun RUN_ID` runs a run again and `Raticode rattish resume WORKFLOW_ID RUN_ID` resumes one. Give password inputs with `--input name=value`. See the [CLI reference](../reference/cli-headless.md#workflows). On the phone, see [Workflows](../mobile/workspace.md#resume-and-run-again).

## Ask Rem about a failure

::: info New in 0.6.0
New in Raticode 0.6.0. On the phone it needs Raticode Mobile 0.36.0 as well.
:::

**Ask Rem** on a failure puts a short summary of it in Rem's message box as a text file, the way **Ask Rem about this entry** does in the [Application log](../reference/settings.md#select-entries). Nothing is sent: write your question, then send it yourself. When the Rem plugin is off, the file goes to the open thread in the side pane instead.

The button is on:

- each failed row in **Run Timeline**: a failed node, an allowed failure, a stopped step, and the run's own failure
- failed and interrupted runs in **Runs** and in the **Workflow runs** view
- each entry in **Problems**

The file holds:

- the workflow file's path and the run ID
- the node's ID and type, its status, exit code and error
- for a model step, its provider app and version, such as `Provider: Claude Code 2.3.1, model claude-sonnet-4-5`. When the workflow's last passing run used another version of that app, a line says so: *Claude Code changed from 2.3.0 to 2.3.1 since the last passing run.* New in 0.6.0; runs saved before it show the provider and model only.
- the end of the node's output: up to its last 200 lines, cut to 8 KB
- the run's preflight problems
- the run's input names and values. A password input (`"format": "password"`) shows as a mask; Raticode never saved its value.

Without a node to pick, such as from **Runs**, the summary describes the node that ended the run. A **Problems** entry from the workflow source, with no run behind it, names the file, the line and the problem, and lists the other preflight problems.

Everything in the file came from the run or the workflow, so it is marked as untrusted: it sits between BEGIN and END lines with a random tag, and the file tells Rem to treat it as data, not instructions. A command's output can't end the block early, because it can't know the tag.

In a terminal, `Raticode rattish runs failure RUN_ID` prints the same text; add `--node NODE` to pick a node. See the [CLI reference](../reference/cli-headless.md#workflows). On the phone, see [Ask Rem about a failed run](../mobile/workspace.md#ask-rem-about-a-failed-run).

## Run from here

::: info New in 0.6.0
Run from here and `--from` are new in Raticode 0.6.0.
:::

After you edit one step, you often don't need to run the steps before it again. Right-click the step on the graph and choose **Run from here**. Raticode saves the workflow, then starts a new run that:

1. Picks the newest run of this workflow that reached the step and in which every step before it is unchanged.
2. Reuses those earlier steps' saved results instead of running them.
3. Runs the step you chose, and everything after it, from the workflow as it is now.

"The steps before it" are every step it depends on, directly or through other steps: a `needs` entry, a route into it, or a binding or route condition that reads that step's output. Each step has a fingerprint covering its definition and everything it depends on, so a change anywhere up the chain counts. Moving a step around the file doesn't. Steps that aren't before it, such as a parallel branch beside it, run again.

What you see and what it costs:

- Run Timeline marks each reused step **Reused**, with the run it came from, and the graph shows it the same way.
- A reused step makes no model call and runs no command. It costs $0 and counts toward no usage limit: not `max-tokens`, not `max-cost-usd`, not a trigger's daily limit. Like every step of the run, it still counts toward `max-runs`.
- The new run's record names the run it reused, the step it started from and the reused steps, under `replayed_from`. It doesn't count as resuming that run, so you can still resume it.
- The run uses the reused run's inputs and trigger events. Password inputs were never saved, so Raticode asks for them again, as **Resume** does.
- Approval steps are never reused. An approval step before the step you chose asks again, with the same message, and the steps after it run from the new answer, even when the earlier run's answer was a rejection. Steps before the approval step are still reused. Before it starts, **Run from here** lists the approval steps the earlier run reached, before or after the step, since each asks again when the run gets to it, and asks you to confirm.

When it can't start, it says why instead:

- **A step before it changed** since every run that reached it. The message names that step. Run the whole workflow instead, or **Run from here** on the changed step.
- **No run reached the step yet.** Run the whole workflow first.
- **The step is inside a loop body.** One loop iteration can't be rerun on its own yet; run from the loop node instead.
- **A step before it is an agent with `memory: run`**, whose earlier turns aren't saved.

Raticode compares the workflow source. Files a step reads while it runs, such as an agent's `prompt-path` file, aren't compared, so after editing one of those, run the whole workflow or run from that step.

From a terminal:

```bash
# In the CLI process, from a workflow file
Raticode rattish run path/to/workflow.rattish --from summarize
Raticode rattish run path/to/workflow.rattish --from summarize --reuse RUN_ID --input token='"new-token"'
# Through the backend, so the run shows up in the app
Raticode rattish start nightly-report --from summarize --dry-run
Raticode rattish start nightly-report --from summarize --wait
```

`--reuse RUN_ID` reuses that run instead of the newest one that fits, and refuses with the reason when it doesn't fit. `--input` gives only the password inputs the reused run didn't save. With `--dry-run`, `rattish start` shows the run and steps it would reuse, the approval steps that will ask again and the password inputs it needs. See the [CLI command index](../reference/cli-commands.md#raticode-rattish-run).

## Review a worktree run's changes

::: info New in 0.6.0
Worktree runs and the Changes tab are new in Raticode 0.6.0.
:::

A workflow with **Run in a worktree** on (`workspace: worktree`) runs each time in a git worktree of its own, and Raticode commits what the run changed on the branch `raticode/run/<workflow>-<run>`. See [Run in a worktree](../workflows/rattish.md#run-in-a-worktree).

Select such a run and open **Changes** in the bottom panel. It reads **to review** while the changes wait. The tab shows:

- the run's branch and the branch it lands in: the one the project was on when the run started;
- how many files changed, and whether the branch lands as a fast-forward, merges cleanly, or conflicts, and in which files;
- what the project's [worktree setup](./worktree-setup.md) did, with **Review setup** when a version waits for your approval;
- the full patch in the same diff view as source control's commit view.

Then decide:

- **Land in BRANCH** asks you to confirm, then lands the changes there: a fast-forward, or a merge commit after a check that the two merge without conflicts. It refuses while that branch's folder has uncommitted changes to tracked files, and it never pushes. It lands only the commit you reviewed, so if the branch moved since you opened the tab, review it again. Afterwards Raticode removes the run's worktree and branch.
- **Discard** asks you to confirm, then removes the run's worktree and branch, and with them its changes.

A conflict changes nothing. The tab lists the conflicting files; merge the run's branch yourself, or discard it and run the workflow again on the current code. A run that changed nothing says so, and its worktree and branch are already gone.

A workflow keeps at most 10 runs whose changes wait. Past that, triggered runs are skipped with that reason in the trigger's history, and runs you start yourself fail before any step runs.

From a terminal:

```bash
Raticode rattish runs changes RUN_ID --output text --diff
Raticode rattish runs land RUN_ID
Raticode rattish runs discard RUN_ID
```

`changes` prints JSON by default, with each file's text before and after, the branch tip (`tip`) and the merge check (`merge`). `land --expected-tip COMMIT` lands only if the branch is still that commit, and exits 4 on a conflict. Land and discard are yours to do: they refuse inside a Raticode agent turn. See the [CLI command index](../reference/cli-commands.md#raticode-rattish-runs-land).

## Stop and side effects

**Stop** on the selected run asks it to cancel. Cancelling doesn't undo a file write, command or network request that already finished, so check the workspace before you restart.

## Approval steps

An `approval-gate` node asks you for a decision.

- Read its message and subject before you approve or reject.
- A gate with `options` shows one button per option instead of Approve and Reject, and a gate with `reply` adds a box for a typed reply. Options that count as a rejection are shown in red. *New in Raticode 0.6.0.* See [Choices and typed replies](../nodes/approval-gate.md#choices-and-typed-replies).
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
- has [worktree changes](#review-a-worktree-run-s-changes) waiting to be landed or discarded

To change the settings or clean up now, choose the trash can button (**Run retention settings**) above the run log. **Preview** counts the runs that would go and deletes nothing. **Prune** deletes them. Changes apply to that workflow only.

From a terminal, `Raticode rattish prune PATH` lists the runs the saved settings no longer keep, and `--apply` deletes them. `--keep-last`, `--keep-days` and `--keep-failed-days` replace the saved settings for that one command. See the [CLI command index](../reference/cli-commands.md#raticode-rattish-prune).

A run record that can't be read, such as a damaged file, is left alone and doesn't stop the **Runs** list from loading. The CLI and the backend's run list report how many records they skipped.

