# Rattish node contracts

These JSON files define the built-in Rattish node contract format. Each file
validates against [`../schemas/node-contract.schema.json`](../schemas/node-contract.schema.json).
Machine keys use snake_case. Rattish source fields remain kebab-case.

The first representative set covers the contracts that force the most reusable behavior:

- `agent.json` has provider-owned computed defaults, an author-selected output schema,
  open prompt bindings, and deployment checks.
- `bash-command.json` has a fixed output, a reserved `stdin` binding, dynamic environment
  bindings, and explicit environment precedence.
- `python-script.json` and `shell-script.json` apply the same binding rules to versioned
  script contracts with explicit interpreter and file preflight checks.
- `write-file.json`, `copy-file.json`, `move-file.json`, and `delete-file.json` define
  project-confined mutations for files, folders, and symlink objects.
- `file.json` and `folder.json` publish resolved path metadata after checking the selected
  resource kind.
- `prompt-file.json` renders an inline or project-local template and writes it atomically.
- `open-resource.json` dispatches a project resource, URL, or application through the
  platform opener.
- `http-request.json` defines request policy, retries, typed request bindings, structured
  response output, and plaintext credential warnings.
- `notification.json` covers desktop, webhook, and email delivery without publishing
  connection credentials in node output.
- `approval-gate.json` persists an externally actionable decision request and optionally
  sends a best-effort notification.
- `common-llm-task.json` provides the six versioned convenience prompts while retaining
  provider-owned model and effort defaults and structured output.
- `local-vectorize.json` and `local-search.json` define a project-local, versioned retrieval
  index and deterministic search output.
- `loop.json` freezes defaults for count, tabular, directory, trigger-event, and infinite
  sources. Each item receives its own activation lineage and bounded concurrency.
- `break.json` closes one explicitly named loop lineage without cancelling work already
  running in that lineage.
- `workflow.json` derives its inputs and output from a versioned child interface.

Filesystem mutation contracts never follow an authored path outside the project root.
Copy, move, and delete operate on directories as well as files. A final symlink is treated
as the object being copied, moved, or deleted; a symlink in an intermediate path may not
escape the project root. Non-append writes use a same-directory temporary file and atomic
replacement. A `with.stdin` binding on `write-file` overrides authored `content`.

JSON Schema checks the contract document shape. The contract loader must also enforce
cross-field invariants that the meta-schema cannot express cleanly:

- validate every embedded schema against JSON Schema Draft 2020-12 and Rattish Schema
  Profile 1;
- reject defaults and computed-default fields absent from `configuration_schema`;
- validate each literal default against its field schema;
- reject duplicate node type and contract version pairs;
- reject input names whose runtime name transformation collides;
- verify that the named runtime handler implements the declared contract version during
  deployment preflight.

These files do not expand common node fields such as `needs`, `to`, `timeout`, or
`allow-fail`. The language contract owns those fields.

Validate the current set with:

```bash
python -m gofer.rattish.contract_validator \
  --schema rattish/schemas/node-contract.schema.json \
  rattish/contracts/*.json
```
