---
title: jev
description: Ask Jev one yes/no or multiple-choice question about some text and route on the label it returns.
pageClass: docs-layout
---

# jev

Ask TypeSafe's Jev classifier one yes/no or multiple-choice question about some text, and route on the answer.

::: info New in 0.5.5
The jev node is new in Raticode 0.5.5.
:::

## When to use it

Use it to sort, flag or route text by what it says: is this a refund request, is this log line an error, which team should get this message. It answers with a label and a score, never with written text. For a written answer, use [agent](./agent.md) or [common-llm-task](./common-llm-task.md).

Save a Jev key first. [Jev classifier](../workflows/jev.md) covers the key, what's sent to TypeSafe, cost and limits. For a ready-made workflow to try, copy the [Jev demo](../starters/jev-demo.md) starter (from 0.5.5).

## Example

```rattish
Rattish: 1

Workflow:
  name: Refund triage
  inputs:
    message:
      schema: {"type": "string"}
      required: true

Node triage:
  type: jev
  question: Does the customer ask for their money back?
  criteria:
    yes: The writer asks for a refund, chargeback or reversed payment.
    no: Complaints, questions or praise that don't ask for money back.
  threshold: 0.6
  with:
    state: input.message
  to:
    - route-topic when node.triage.output.label == "no"
    - refund-flow otherwise

Node route-topic:
  type: jev
  needs: triage
  question-type: choice
  question: Which team should handle this message?
  options:
    billing: Payments, invoices and receipts
    bugs: Something in the product is broken
    other: Anything else
  with:
    state: input.message
  to:
    - bugs-flow when node.route-topic.output.label == "bugs" and node.route-topic.output.score >= 0.7
    - by-hand otherwise

Node refund-flow:
  type: bash-command
  needs: triage
  command: echo "refund"
  finish: pass

Node bugs-flow:
  type: bash-command
  needs: route-topic
  command: echo "bugs"
  finish: pass

Node by-hand:
  type: bash-command
  needs: route-topic
  command: echo "review by hand"
  finish: pass
```

- **Yes/no.** `score` is Jev's probability of yes. `label` is `"yes"` when the score is at least `threshold`, otherwise `"no"`. Treat the score as a signal to compare against a threshold, not as exact odds.
- **Choice.** `label` is the option Jev picked, and `score` is its confidence. Describe each option, because Jev decides from the descriptions.
- **Criteria.** Say what counts as yes and what counts as no. Jev is more reliable with them, so Raticode warns (`RATTISH_JEV_CRITERIA_MISSING`) when a yes/no node has none.
- **State.** Bind it with `with: state:`, usually to another node's output or a workflow input. It can be text or JSON. An empty state fails the node before anything is sent.
- **Model.** Nodes use `jev-1.14.0` unless they set `model`. The version is fixed when the workflow is checked, so a new Jev release can't change your thresholds without you choosing it.

## Configuration

Fields for this node only. The [shared node fields](./index.md#shared-node-fields) also apply.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `question` | string | Required | The question Jev answers about the state. |
| `question-type` | `yes-no`, `choice` | `"yes-no"` | A yes/no question or a pick among options. |
| `criteria` | object with `yes` and `no` | `{}` | What counts as yes and as no. Yes/no only. |
| `options` | object | `{}` | 2 to 255 option names, each mapped to a description. Choice only. |
| `threshold` | number from 0 to 1 | `0.5` | Lowest score that counts as yes. Yes/no only. |
| `state` | string | `""` | The text the question is about. A `with` binding replaces it. |
| `model` | string | `"jev-1.14.0"` | Jev model version. |
| `long-context` | boolean | `false` | Allow up to 262k tokens instead of 64k. Slower and less accurate, per TypeSafe. |
| `request-timeout` | string | `"15s"` | Limit for one call to Jev, at most `60s`. The shared `timeout` limits the whole node. |
| `cache` | duration or null | Not set | Reuse a saved answer for this long, such as `12h` or `7d`, at most `365d`. See [Result cache](#result-cache). |

## Result cache

New in 0.5.5. With `cache: 12h`, a run reuses an earlier answer to the same question about the same state instead of calling Jev again, while the answer is younger than the duration. The key covers the model, the question and state after bindings, the criteria or options, the threshold and the rest of the node's compiled settings. A [file input](../workflows/inputs.md#file-inputs) counts by its contents and file name, not by the path of the run's copy, and a run [in a worktree](../workflows/rattish.md#run-in-a-worktree) counts by the project the worktree was made from, not by its own worktree folder. A cached step shows as **Cached** in the Run Timeline, makes no call and records 0 tokens and $0. The output is the saved one, including its original `input_tokens`, `output_tokens` and `latency_ms`.

Saving, limits, clearing and test sets work as described for [common-llm-task](./common-llm-task.md#result-cache).

## Input ports

| Port | Type | Meaning |
| --- | --- | --- |
| `state` | any JSON value | The text or data to classify. Replaces the authored `state`. |
| `question` | string | Replaces the authored `question`. |

## Output

| Field | Type | Meaning |
| --- | --- | --- |
| `label` | string | `"yes"` or `"no"`, or the chosen option's name. |
| `score` | number from 0 to 1 | Yes/no: probability of yes. Choice: confidence in the label. |
| `probabilities` | object | Yes/no: `{"yes": p, "no": 1 - p}`. Choice: a probability per option. |
| `question_type` | string | `yes-no` or `choice`. |
| `threshold` | number or null | The threshold used, or null for a choice question. |
| `model` | string | The model that answered. |
| `requested_model` | string | The model the node asked for. |
| `input_tokens` | integer or null | Billed input tokens Jev reported. |
| `output_tokens` | integer or null | Output tokens Jev reported (currently free). |
| `latency_ms` | integer | How long the call took. |

Routes compare `label` or `score`, as in `when node.triage.output.label == "yes"` or `when node.triage.output.score >= 0.8`.

## Preflight

Checking a workflow fails with `RATTISH_PREFLIGHT_JEV_NOT_CONFIGURED` when no Jev key is saved, and `RATTISH_PREFLIGHT_JEV_DISABLED` when Jev is turned off in **Settings > Providers**. Preflight doesn't call Jev.

## Failures and retries

Errors use the codes in [Jev classifier > Errors](../workflows/jev.md#errors). `retry-count` retries only the errors another attempt could fix, such as a timeout, and waits out a rate limit before trying again. Use `allow-fail: true` with a `when failed` route to send text to a person when Jev can't answer.

The node sends text to TypeSafe and is billed per input token, so its contract lists the `network` and `provider` effects.
