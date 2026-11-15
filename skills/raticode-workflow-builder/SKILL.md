---
name: raticode-workflow-builder
description: Create, edit, validate, and preflight Raticode workflows written in the Rattish language. Use for workflow authoring, graph changes, node configuration, routes, inputs, or execution readiness. Do not use the legacy TOML authoring commands for these tasks.
---

# Raticode workflow builder

Author Raticode workflows as Rattish source. A registered workflow lives at
`<project>/.raticode/<workflow-id>/workflow.rattish`. Do not create or edit workflow TOML.

If the assistant prompt provides an exact Raticode CLI path, use it instead of bare
`Raticode`. Run `Raticode rattish docs --format json` to locate the installed Rattish specification
and machine-readable node contracts. This command works in desktop and wheel installs and
does not require a Raticode source checkout.

## Authoring workflow

1. Inspect registered workflows with `Raticode rattish list --format json`.
2. For a new workflow, run:

   ```bash
   Raticode rattish create "Workflow name" --project <project-folder> --format json
   ```

   Read `workflow.sourcePath` from the JSON response. Edit that `workflow.rattish` file.
3. For an existing workflow, edit the `sourcePath` supplied in the assistant's workflow
   context or returned by `Raticode rattish list --format json`.
4. Consult only the documentation needed for the change:
   - `README.md` for the language overview and CLI.
   - `grammar.ebnf` and `lexical-spec.md` for source spelling.
   - `static-semantics.md` for bindings, routes, inputs, and path rules.
   - `node-contracts.md` for shared execution fields and node behavior.
   - The installed `contracts/` directory for the exact fields, defaults, and schemas of
     each node type. Machine contracts and compiler diagnostics override prose examples.
5. Validate every edit:

   ```bash
   Raticode rattish format <workflow.rattish>
   Raticode rattish check <workflow.rattish> --format json
   Raticode rattish preflight <workflow.rattish> --format json
   ```

   Fix error diagnostics and re-run the failed command. Report warnings instead of hiding
   them. Do not run the workflow unless the user authorizes real execution.

## Rattish essentials

A minimal workflow is:

```rattish
Rattish: 1

Workflow:
  name: Daily review

Node collect:
  type: bash-command
  command: git status --short
  to: review

Node review:
  type: agent
  provider: codex
  prompt: Review the repository status.
  needs: collect
```

Use kebab-case node IDs and field names. `needs` declares readiness. `to` declares routes.
Use `with` for typed input bindings, such as `report: node.collect.output.stdout`. Workflow
inputs use `input.<name>`. Paths in Rattish are project-relative and cannot escape the
project root.

Use a `loop` node for iteration or fan-out. Agent nodes do not own fan-out. Use
`allow-fail: true` plus a conditional route when an operation failure should be handled by
the graph. Set explicit workflow or node limits for intentional cycles. To cap what a run
spends on models, set workflow `max-tokens` or `max-cost-usd`; they are checked after each
call, and `max-cost-usd` ends the run at the first call whose provider reports no cost.
Inside an agent node, Claude Code gets a spending cap from the run's remaining budget, and
tokens that Claude Code, OpenCode or Copilot report mid-session stop the node at the limit.

An `agent` or `common-llm-task` node may list up to four `fallback` entries, each a map of
`provider` and optional `profile`, `model` and `effort`. After `retry-count`, the step moves
to the next entry only on a provider availability error (rate limit or used-up allowance,
overloaded service, expired sign-in, CLI not installed), never on a task failure, invalid
structured output or a usage-limit stop. The fallback starts the step over, like a retry,
so suggest one only for steps that are safe to rerun.

A `common-llm-task` or `jev` node may set `cache: DURATION` (`30m`, `12h`, `7d`, `2w`, at most
`365d`) to reuse a saved successful answer while the provider, model, effort, rendered
prompt, bound inputs, output schema and node settings are unchanged. A cached step makes no
call and records 0 tokens and $0. Agent nodes can't cache, `cache` can't be combined with
`memory: run` or `all`, steps that read a secret never cache, and test sets skip the cache.
Suggest it for repeated questions about the same input; files the model reads aren't part
of the key.

Provider profiles and secrets are global Raticode configuration. Reference them from
Rattish, but never write resolved credentials into `workflow.rattish`, prompts, logs, or
generated files. `secret["NAME"]` reads a value saved in the system keychain, then the
environment. `Raticode rattish secrets list` shows saved names; only the user saves or
removes values (Workflow settings > Secrets or `Raticode rattish secrets set NAME`), so when
preflight reports `RATTISH_PREFLIGHT_SECRET_UNAVAILABLE`, tell the user which name to add.

## Boundaries

- Preserve the user's project and requested trigger behavior.
- Reuse project scripts and prompts when suitable.
- Do not edit compiled JSON IR. The compiler owns IR and stores it internally.
- Do not use `Raticode workflow create`, `Raticode workflow add-node`, or other legacy TOML mutation
  commands for Rattish workflows.
- Schedules, file watches and local webhooks are not Rattish syntax. Set them with
  `Raticode rattish triggers add cron|file|webhook` after the workflow validates, and tell the
  user they fire only while Raticode or its background scheduler is running.
- Test cases for a workflow go in `<name>.tests.json` beside it (`workflow.tests.json` for a
  registered workflow), validated by the `testSetSchema` path from `Raticode rattish docs`.
  `Raticode rattish test <workflow.rattish> --estimate` prints the expected cost without
  running anything. Running the tests executes the workflow once per case, so it needs the
  same authorization as a real run.
- If the user requests a trigger or feature that Rattish 1 does not support, report that
  limitation. Do not create a legacy TOML workflow as a workaround unless the user asks.
- If the CLI or filesystem is unavailable, provide a Rattish patch and say which validation
  commands could not run.
