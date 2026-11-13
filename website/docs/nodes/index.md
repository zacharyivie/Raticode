---
title: Node reference
description: Pick one of the 24 built-in Rattish node types, and see the fields every node shares.
pageClass: docs-layout
---

# Node reference

Pick a node by what it does. Each node's page has a complete example, its fields and defaults, its input ports and output fields, and what preflight checks. The tables follow the installed Rattish contracts.

## Commands and agents

| Node | Purpose |
| --- | --- |
| [bash-command](./bash-command.md) | Run one command and capture its result |
| [shell-script](./shell-script.md) | Run a maintained Bash script |
| [python-script](./python-script.md) | Run a maintained Python script |
| [agent](./agent.md) | Ask a provider to do a task you wrote |
| [common-llm-task](./common-llm-task.md) | Use a ready-made prompt to review, summarize, extract and more |

## Files and resources

| Node | Purpose |
| --- | --- |
| [read-file](./read-file.md) | Read text and expose its metadata |
| [write-file](./write-file.md) | Create, replace, or append text |
| [copy-file](./copy-file.md) | Copy a file, folder, or symlink |
| [move-file](./move-file.md) | Move or rename a resource |
| [delete-file](./delete-file.md) | Remove a resource, with trash and recursive options |
| [file](./file.md) | Describe a file without reading its content |
| [folder](./folder.md) | Describe a directory without enumerating it |
| [open-resource](./open-resource.md) | Open a file, folder, URL, or application |
| [prompt-file](./prompt-file.md) | Render a prompt template to a file |

## Control and interaction

| Node | Purpose |
| --- | --- |
| [loop](./loop.md) | Fan out runtime work items |
| [break](./break.md) | Stop a loop's pending iterations |
| [workflow](./workflow.md) | Call a child workflow's public interface |
| [approval-gate](./approval-gate.md) | Wait for a person's decision |
| [notification](./notification.md) | Notify a desktop or external channel |
| [rem-prompt](./rem-prompt.md) | Ask Rem and use its reply |
| [rem-task](./rem-task.md) | Run one of Rem's tasks and wait for its result |
| [http-request](./http-request.md) | Call a service and expose its response |

## Local retrieval

| Node | Purpose |
| --- | --- |
| [local-vectorize](./local-vectorize.md) | Build or validate an on-disk text index |
| [local-search](./local-search.md) | Retrieve ranked chunks from that index |

These nodes are separate from Rem's chat tools. Turning on Second Brain in a thread adds no workflow node, and an HTTP node doesn't turn on Rem's web search.

## Shared node fields

Every node declares `type`. These execution and routing fields work on every node:

| Field | Default | Meaning |
| --- | --- | --- |
| `needs` | `[]` | One-time readiness dependencies |
| `to` | No routes | Explicit outgoing routes, including conditional ones |
| `with` | `{}` | Input-port and local bindings |
| `start` | `false` | Marks a start node, for checks and display. Can't have `needs` |
| `finish` | None | `pass` or `fail` endpoint. Can't have outgoing routes |
| `allow-fail` | `false` | Allow an operation failure to participate in recovery routing |
| `timeout` | None | Authored duration limit |
| `max-runs` | None | Maximum runs of the node |
| `max-concurrency` | `1` | Maximum overlapping activations of the node |
| `retry-count` | `0` | Additional operation retries |
| `retry-delay` | `1s` | Delay between retries |

- `finish: fail` can't also set `allow-fail`.
- Some nodes add their own rules. A break node, for example, can't have outgoing routes.
- A loop's source concurrency is separate from this shared `max-concurrency`.

## Source spelling and selectors

- Configuration fields use hyphens, such as `working-dir` and `create-dirs`.
- Output selectors use the schema's exact names, often with underscores, such as `node.check.output.exit_code`.
- There are no start, pass, fail, branch or join node types. Use the shared fields and [routes and readiness](../workflows/routes.md) instead.

## Check an example

Copy an example into `workflow.rattish` in a scratch project, and create any files its page mentions. Check and preflight never run nodes:

```bash
Raticode rattish format workflow.rattish
Raticode rattish check workflow.rattish --format json
Raticode rattish preflight workflow.rattish --format json
```

A preflight error about the environment usually means a provider, notifier, interpreter, index or opener is missing. Fix it before you run the workflow rather than working around the check.

See [Validation and preflight](../workflows/validation.md) and [Inputs and bindings](../workflows/inputs.md).
