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
| `command` | A command exits with the expected code | `command`, `exitCode` (default 0), `timeoutSeconds` (default 60) |
| `judge` | A model says the value meets the criteria | `criteria`, optional `provider`, `model`, `profile`, `effort` |

A **command** check runs in the project folder with the target value on standard input. It also gets `RATICODE_TEST_CASE`, `RATICODE_TEST_RUN_ID` and `RATICODE_TEST_RUN_FILE`, the path of the full run record, if your script needs more than one value.

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
