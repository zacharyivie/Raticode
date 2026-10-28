---
title: Node reference
description: Choose among the 22 built-in Rattish node types and understand shared execution fields.
pageClass: docs-layout
---

# Node reference

Choose a node by what it does, then make its inputs, routes, and side effects explicit.

Each reference page contains a complete source example, the node's configuration fields and defaults, its input ports and output fields, and its preflight requirements. Tables follow the installed Rattish machine contracts.

## Commands and agents

| Node | Purpose |
| --- | --- |
| [bash-command](./bash-command.md) | Run one command and capture its result |
| [shell-script](./shell-script.md) | Run a maintained Bash script |
| [python-script](./python-script.md) | Run a maintained Python script |
| [agent](./agent.md) | Ask a provider to perform an authored task |
| [common-llm-task](./common-llm-task.md) | Use a review, summary, extraction, or other common prompt pattern |

## Files and resources

| Node | Purpose |
| --- | --- |
| [read-file](./read-file.md) | Read text and expose its metadata |
| [write-file](./write-file.md) | Create, replace, or append text |
| [copy-file](./copy-file.md) | Copy a file, folder, or symlink |
| [move-file](./move-file.md) | Move or rename a resource |
| [delete-file](./delete-file.md) | Remove a resource with trash and recursion policies |
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
| [approval-gate](./approval-gate.md) | Obtain a human decision |
| [notification](./notification.md) | Notify a desktop or external channel |
| [http-request](./http-request.md) | Call a service and expose its response |

## Local retrieval

| Node | Purpose |
| --- | --- |
| [local-vectorize](./local-vectorize.md) | Build or validate an on-disk text index |
| [local-search](./local-search.md) | Retrieve ranked chunks from that index |

These nodes are distinct from Rem's conversational tools. Enabling Second Brain in a thread does not add a node to a workflow, and declaring an HTTP node does not enable Rem web search.

## Shared node fields

Every node declares `type`. The following execution and routing fields are shared:

| Field | Default | Meaning |
| --- | --- | --- |
| `needs` | `[]` | One-time readiness dependencies |
| `to` | No routes | Explicit outgoing routes, including conditional ones |
| `with` | `{}` | Input-port and local bindings |
| `start` | `false` | Start assertion/presentation hint; cannot have `needs` |
| `finish` | None | `pass` or `fail` endpoint; cannot have outgoing routes |
| `allow-fail` | `false` | Allow an operation failure to participate in recovery routing |
| `timeout` | None | Authored duration limit |
| `max-runs` | None | Maximum runs of the node |
| `max-concurrency` | `1` | Maximum overlapping activations of the node |
| `retry-count` | `0` | Additional operation retries |
| `retry-delay` | `1s` | Delay between retries |

`finish: fail` cannot also declare `allow-fail`. Node contracts can add restrictions: for example, a break node cannot have outgoing routes. A loop's source concurrency is separate from the shared activation limit.

## Source spelling and selectors

Rattish configuration uses hyphens, such as `working-dir` and `create-dirs`. Output selectors use the schema's exact names, often underscores, such as `node.check.output.exit_code`.

Do not invent node types named start, pass, fail, branch, or join. Use shared fields and [routes/readiness](../workflows/routes.md) for those behaviors.

## Check an example

Copy a complete example into `workflow.rattish` in a disposable project and create any prerequisite files mentioned on its page. Check and preflight do not execute nodes:

```bash
gof rattish format workflow.rattish
gof rattish check workflow.rattish --format json
gof rattish preflight workflow.rattish --format json
```

An environment-dependent preflight diagnostic can mean a provider, notifier, interpreter, index, or opener is missing. Resolve that requirement before execution rather than bypassing the check.

See [Validation and preflight](../workflows/validation.md) and [Inputs and bindings](../workflows/inputs.md).
