---
title: rem-prompt
description: Send a prompt to Rem and pass its reply to the next steps.
pageClass: docs-layout
---

# rem-prompt

Send a prompt to Rem and pass its reply to the next steps.

## When to use it

Use it when a step needs Rem's answer: a summary, a decision in words, or a question about your tasks and projects. The prompt goes to Rem's chat, marked as coming from the workflow, so Rem answers with its memories and context.

## Example

```rattish
Rattish: 1

Workflow:
  name: Release digest

Node notes:
  type: read-file
  path: CHANGELOG.md
  to: ask

Node ask:
  type: rem-prompt
  prompt: "Summarize these release notes in three bullets: {{notes}}"
  with:
    notes: node.notes.output.content
  needs: notes
  to: save

Node save:
  type: write-file
  path: digest.md
  with:
    stdin: node.ask.output.reply
  needs: ask
```

`prompt` is required and may use `{{name}}` placeholders filled from `with` bindings. A bound `prompt` port replaces the authored text.

## What Rem may do

By default Rem only answers. Workflow prompts often carry text from files, web pages or commands, and this keeps that text from starting work.

- If the answer would start, change, cancel or delete a task, save or change a memory, or send a file, that part is dropped. The reply says so, and `actions_dropped` is `true`.
- `allow-actions: true` lets Rem act on the prompt as it would on a message you typed. Tasks it starts still go through Rem's own checks, such as your approved projects and folders.
- `wait-for-tasks: true` also waits for the work Rem started, and fails if any of it doesn't succeed.

::: warning Prompts from triggers
Be careful with `allow-actions: true` in a workflow started by a [webhook or file trigger](../workflows/triggers.md). Someone else can write the webhook body or the watched file, and that text can reach the prompt.

- Raticode tells Rem when a webhook or file trigger started the run. Rem then does only what the prompt plainly asks, and describes rather than acts on any work the outside text asks for.
- The backend log records a warning for each such prompt.
- Keep these prompts answer-only when you can, and pass outside text through a step that checks it first.
:::

## Waiting and stopping

The node waits until Rem has answered.

- If Rem is replying to something else, the prompt waits in line like a message you send.
- Rem must be running. With no Raticode backend answering, the node fails at once with `RATTISH_REM_UNAVAILABLE`. It fails the same way if Rem stops for more than a minute while the node waits.
- On a timeout or a stopped workflow, a prompt Rem hasn't started is taken back. One Rem is already answering still gets its answer in Rem's chat.
- Work Rem started for an `allow-actions` prompt keeps running. It's Rem's work now, as if you had asked.

## Configuration

Configuration fields use hyphens. These are the node-specific fields; [shared execution fields](./index.md#shared-node-fields) apply separately.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `prompt` | string | Required | The message Rem receives. |
| `allow-actions` | boolean | `false` | Let Rem start or change tasks, memories and files for this prompt. |
| `wait-for-tasks` | boolean | `false` | Wait for any work Rem started and include its result. |

## Inputs and output

Bind this declared port through `with`:

| Port | Value type |
| --- | --- |
| `prompt` | string |

Successful output fields use these exact selector names:

| Field | Value type |
| --- | --- |
| `reply` | string, Rem's answer |
| `details` | string, the longer explanation Rem shows under "Details", or empty |
| `request_id` | string, the chat message the workflow sent |
| `message_id` | string, Rem's reply message |
| `actions_dropped` | boolean |
| `tasks` | list of `{task_id, run_id, title, state, succeeded, summary, result, artifacts, thread_id}` for work Rem started or continued |

For example, refer to `node.ask.output.reply` from a downstream binding.

## Errors

| Code | Meaning |
| --- | --- |
| `RATTISH_REM_UNAVAILABLE` | No Raticode backend is running Rem, or Rem stopped while the node waited. |
| `RATTISH_REM_BUSY` | Rem already has five messages waiting. |
| `RATTISH_REM_PROMPT_FAILED` | Rem couldn't answer; the message is Rem's explanation. |
| `RATTISH_REM_PROMPT_LOST` | Rem's chat history was cleared before it answered. |
| `RATTISH_REM_TASK_*` | With `wait-for-tasks`, work Rem started failed, was stopped or ended uncertainly. |

## Preflight and limits

Rem must have been set up in this Raticode data folder. The node uses Rem's chat model, so each run costs a Rem reply. Rem's reply is at most 4,000 characters; longer material is in `details`.

Continue with [rem-task](./rem-task.md) or the [Node reference](./index.md).
