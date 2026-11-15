---
title: "Starter: Jev demo"
description: A starter workflow that asks Jev a yes/no and a multiple-choice question about a message and routes on each label and score.
pageClass: docs-layout
---

# Jev demo

Shows how the [jev node](../nodes/jev.md) works. It asks the [Jev classifier](../workflows/jev.md) whether a message asks for a refund. If it doesn't, it asks which team should answer it, then takes a different branch for each answer and prints what Jev returned. It makes at most two Jev calls per run and doesn't change any files.

::: info New in 0.5.5
Starter workflows are new in Raticode 0.5.5.
:::

```bash
Raticode rattish create --from jev-demo --project .
```

Save a Jev key in **Settings > Providers** first. Without one, the run stops at preflight with `RATTISH_PREFLIGHT_JEV_NOT_CONFIGURED`, or `RATTISH_PREFLIGHT_JEV_DISABLED` when Jev is turned off.

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `message` | string, up to 4,000 characters | A customer asking for their money back for a cracked screen | The text Jev reads |
| `sure-score` | number from 0 to 1 | `0.8` | Answers that score at least this are acted on. Lower scores go to a person |

## How it works

1. `wants-refund` is a yes/no jev node: "Does the writer ask for their money back?", with criteria for yes and no and `threshold: 0.5`. Its `label` is `yes` when the score is at least the threshold.
2. Its routes check the label and the score:
   - `refund` when the label is `yes` and the score is at least `sure-score`.
   - `check-refund-by-hand` when the label is `yes` but the score is lower.
   - `pick-team` otherwise.
3. `pick-team` is a choice jev node: "Which team should answer this message?", with the options `billing`, `shipping`, `bugs` and `other`, each described. Its `label` is the option Jev picked and its `score` is Jev's confidence in it.
4. Its routes send the message to `bug-report` when Jev picks `bugs` with a score of at least `sure-score`, to `send-to-team` for any other option at that score, and to `pick-team-by-hand` otherwise. `pick-team-by-hand` also prints each option's probability.

Every route whose condition holds fires, so each node's conditions are written not to overlap. The last steps only print a line, such as `Refund request (label yes, score 0.97)`. Replace them with the steps you want.

To reuse answers to the same message instead of asking Jev again, add `cache: 7d` to either jev node. See [Result cache](../nodes/jev.md#result-cache).

## Test set

| Case | What it checks |
| --- | --- |
| `refund` | A clear refund request is labelled `yes` and takes the refund branch without asking which team |
| `bug-report` | A crash report is labelled `no`, then `bugs`, with a probability for each option |
| `shipping` | A message about a late parcel goes to the shipping team |
| `not-sure` | With `sure-score` set to 1, nothing is acted on and a person picks the team |

In the app, the test set calls the real Jev: seven calls for the four cases, billed to your TypeSafe key. Jev's real scores can differ from run to run, so a case can land on a by-hand branch now and then. Raticode's own CI runs the cases against a stand-in for Jev.

See [Starter workflows](./index.md), the [jev node](../nodes/jev.md) and the [Jev classifier](../workflows/jev.md).
