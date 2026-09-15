---
name: gofer-flow-workflow-builder
description: Create, edit, validate, and preflight Raticode workflows written in the Rattish language. Use for workflow authoring, graph changes, node configuration, routes, inputs, or execution readiness. Do not use the legacy TOML authoring commands for these tasks.
---

# Raticode workflow builder

Author Raticode workflows as Rattish source. A registered workflow lives at
`<project>/.raticode/<workflow-id>/workflow.rattish`. Do not create or edit workflow TOML.

If the assistant prompt provides an exact Raticode CLI path, use it instead of bare
`gof`. Run `gof rattish docs --format json` to locate the installed Rattish specification
and machine-readable node contracts. This command works in desktop and wheel installs and
does not require a Raticode source checkout.

## Authoring workflow

1. Inspect registered workflows with `gof rattish list --format json`.
2. For a new workflow, run:

   ```bash
   gof rattish create "Workflow name" --project <project-folder> --format json
   ```

   Read `workflow.sourcePath` from the JSON response. Edit that `workflow.rattish` file.
3. For an existing workflow, edit the `sourcePath` supplied in the assistant's workflow
   context or returned by `gof rattish list --format json`.
4. Consult only the documentation needed for the change:
   - `README.md` for the language overview and CLI.
   - `grammar.ebnf` and `lexical-spec.md` for source spelling.
   - `static-semantics.md` for bindings, routes, inputs, and path rules.
   - `node-contracts.md` for shared execution fields and node behavior.
   - The installed `contracts/` directory for the exact fields, defaults, and schemas of
     each node type. Machine contracts and compiler diagnostics override prose examples.
5. Validate every edit:

   ```bash
   gof rattish format <workflow.rattish>
   gof rattish check <workflow.rattish> --format json
   gof rattish preflight <workflow.rattish> --format json
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
the graph. Set explicit workflow or node limits for intentional cycles.

Provider profiles and secrets are global Raticode configuration. Reference them from
Rattish, but never write resolved credentials into `workflow.rattish`, prompts, logs, or
generated files.

## Boundaries

- Preserve the user's project and requested trigger behavior.
- Reuse project scripts and prompts when suitable.
- Do not edit compiled JSON IR. The compiler owns IR and stores it internally.
- Do not use `gof workflow create`, `gof workflow add-node`, or other legacy TOML mutation
  commands for Rattish workflows.
- If the user requests a trigger or feature that Rattish 1 does not support, report that
  limitation. Do not create a legacy TOML workflow as a workaround unless the user asks.
- If the CLI or filesystem is unavailable, provide a Rattish patch and say which validation
  commands could not run.
