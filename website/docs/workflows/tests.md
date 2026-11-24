---
title: Test sets
description: Run a workflow against saved input cases, check each result, see the expected cost first, and compare a changed prompt with the last revision.
pageClass: docs-layout
---

# Test sets

A test set is a list of input cases for one workflow. Each case runs the workflow once with its inputs, then checks what came out. Use it when you change a prompt and want to know whether the workflow still does its job.

::: info New in 0.5.5
Test sets are new in Raticode 0.5.5.
:::

## Where the file lives

The test set sits beside the workflow, named after it. For a registered workflow that is `workflow.tests.json` in the workflow's folder, so it travels with the workflow when you export it. Results don't: they go to the workflow's `logs/tests/` folder, which is already ignored. For a workflow found elsewhere in the project, outside `.raticode`, results go to Raticode's data folder instead, so they never land in your source tree (from 0.5.5).

```json
{
  "testSetVersion": 1,
  "cases": [
    {
      "id": "cats",
      "name": "Short summary about cats",
      "inputs": { "topic": "cats" },
      "checks": [
        { "type": "text", "target": "outputs.summary", "contains": "cat", "ignoreCase": true },
        { "type": "json", "target": "nodes.score.grade", "oneOf": ["A", "B"] },
        { "type": "command", "target": "outputs.summary", "command": "python3 checks/short.py" },
        { "type": "judge", "target": "outputs.summary", "criteria": "Two sentences at most and no made-up facts." }
      ]
    }
  ]
}
```

`Raticode rattish docs --format json` prints the path of the file's JSON schema as `testSetSchema`.

## What a check can look at

`target` says where a check looks. It starts with one of these and continues with `.key` and `[index]` steps:

| Target | Value |
| --- | --- |
| `outputs` | The workflow's public outputs (the default) |
| `outputs.NAME` | One public output |
| `nodes.NODE-ID` | The last output of one node, such as `nodes.build.stdout` |
| `status` | The run status: `passed` or `failed` |
| `error` | The run's error, or `null` |

## Checks

| Type | Passes when | Fields |
| --- | --- | --- |
| `text` | The value, as text, matches | One of `equals`, `contains`, `notContains`, `matches` (a regular expression), plus optional `ignoreCase` |
| `json` | The field has the expected JSON value | One of `equals`, `notEquals`, `oneOf` (a list), or `exists: true/false` |
| `command` | A command exits with the expected code | `command`, `exitCode` (default 0), `timeoutSeconds` (default 60), `shell` (default `auto`) |
| `judge` | A model says the value meets the criteria | `criteria`, optional `provider`, `model`, `profile`, `effort` |

A **command** check runs in the project folder with the target value on standard input. `shell` works like the [bash-command field](../nodes/bash-command.md#which-shell-runs-the-command): `auto` uses PowerShell on Windows and bash elsewhere, and `bash` uses Git for Windows' bash on Windows. Set `"shell": "bash"` when the check is a bash command and the test set should pass on Windows. The editor's **Shell** menu sets it; `shell` is new in Raticode 0.6.0. It also gets `RATICODE_TEST_CASE`, `RATICODE_TEST_RUN_ID` and `RATICODE_TEST_RUN_FILE`, the path of the full run record, if your script needs more than one value.

A **judge** check is optional and costs one model call per case. It uses the check's `provider`, then the test set's `judge.provider`, then the workflow's first agent provider, then Claude Code. The model answers pass or fail with a one-line reason, and the panel shows that reason.

A case for a workflow started by a webhook or file trigger can list `triggerEvents`, the events the run starts with, in the same shape the trigger supplies them in `trigger.events` (from 0.5.5). The case form has no field for them, so edit the file; editing the case in the Studio keeps them.

```json
{
  "id": "approved",
  "inputs": {},
  "triggerEvents": [
    {"type": "webhook", "trigger_id": "test", "received_at": "2026-01-01T09:00:00Z", "payload": {"action": "publish-docs"}}
  ]
}
```

Every case also checks that the run passed. Set `"expectStatus": "failed"` on a case that should fail, or `"any"` to skip that check. A case whose inputs don't match the workflow's input schema, or whose provider is missing, shows **Could not run** instead of a failure.

## Expected cost

Before anything runs, Raticode shows how many model calls the selected cases will make and what that should cost:

- A case that ran before uses its own measured cost from the last test run.
- A new case uses the average cost per model call from earlier test runs.
- Before the first run there is nothing to measure, so the dollar figure is unknown. Codex and other subscription CLIs never report dollars, so their cost stays unknown.

The call count is approximate when the workflow has loops, conditional routes or structured-output repair attempts. Rem nodes are counted separately and not priced.

## Run from the Studio

Open the workflow and choose **Tests** in the bottom panel.

- The header shows the expected cost, and the **Run all** button repeats it.
- Turn on **Skip model judgment** to run without judge checks. They cost nothing then.
- The case list marks each case **Passed**, **Failed** or **Could not run**. Select a case to see each check and why it failed.
- **Add case** and **Edit** open a form: name, inputs as a JSON object, what the run should do, and the checks. JSON values stay as you typed them until you save.
- **Run case** runs only the selected case. **Stop** stops a run between cases and cancels the case in progress.

Tests always run the saved workflow. If the editor has unsaved changes, the panel says so.

## Compare with the last revision

Every result records the workflow revision it tested: a fingerprint of the source. When you change the source and run the tests again, the panel compares the new result with the newest result of a different revision:

- Each case is marked **Fixed**, **Broke**, **Changed** or **New**.
- **Show source change** shows the diff between the two revisions, so you can see which prompt edit caused it.

Results from an older revision stay visible until you run the tests again, with a note that they don't match the current source.

## Try another model

::: info New in 0.6.0
Try another model is new in Raticode 0.6.0, and on the phone in Raticode Mobile 0.36.0.
:::

To see whether a cheaper or newer model does the job, try it on the test set before you change the workflow. In the **Tests** panel choose **Try another model**, pick the step, the provider and the model, then **Check cost**.

- The panel shows the expected cost for both models first: the current model and the candidate. **Run both** stays off until it has.
- Each case runs with the current model, then with the candidate. If a finished result of the same revision and test set already covers the cases, the current model's side reuses it and costs nothing.
- The override applies only to that test run. The workflow file, its triggers and their approvals don't change. Each case's run record lists the tried model under `model_overrides`.
- Judge checks keep their own model on both sides, so the grading doesn't change with the candidate.
- A table then shows each case side by side: pass or fail, cost and time for the current model against the candidate, marked **Fixed**, **Broke** or **Changed** where they differ, with totals. Cost here is the workflow's own model calls; judge checks are left out because they are the same on both sides.
- **Use this model** writes `provider:` and `model:` into the step through the editor, like a graph edit: a change that would stop the workflow compiling isn't saved, and every trigger on the workflow asks for approval again before it runs the new version. The button is off when the workflow changed since the try or has unsaved edits.

The same action is on the phone, under **Tests · Try another model** on a workflow's page; see [Raticode Mobile](../mobile/workspace.md#try-another-model).

The candidate's result is saved with the models it tried. **Compare with the last revision** and the expected cost only use results with the same models, so a tried model never counts as the workflow's own. The candidate's expected cost is unknown until a try of that model has measured it.

From the command line, `--model NODE=PROVIDER:MODEL` does the same, and you can repeat it for several steps. The model may contain colons, as in `opencode:llama3:8b`:

```bash
Raticode rattish test WORKFLOW_PATH --model write=claude-code:claude-sonnet-5-5 --estimate
Raticode rattish test WORKFLOW_PATH --model write=claude-code:claude-sonnet-5-5
Raticode rattish test WORKFLOW_PATH --use-model write=claude-code:claude-sonnet-5-5
```

The run prints both expected costs, then one row per case with the current model's and the candidate's result, cost and time. The exit code follows the candidate. `--history` marks each tried result with `[tried NODE: PROVIDER MODEL]`. `--use-model` writes the model into the step and runs nothing. `--max-cost` counts both models.

## From the command line

```bash
Raticode rattish test WORKFLOW_PATH --estimate          # expected cost only
Raticode rattish test WORKFLOW_PATH                     # run every case
Raticode rattish test WORKFLOW_PATH --case cats --skip-judge
Raticode rattish test WORKFLOW_PATH --max-cost 0.50     # refuse when over or unknown
Raticode rattish test WORKFLOW_PATH --history           # saved results and the last comparison
```

The command prints the expected cost first, then one line per case. Exit code 0 means every case passed, 1 that a case failed or couldn't run, and 2 that the test set or the workflow couldn't be used. `--format json` returns the full result with its comparison. `--tests FILE` uses another test set file.

Each case is a real run. It shows up in **Runs** like any other, and it does whatever the workflow does: commands, file writes, provider calls.
