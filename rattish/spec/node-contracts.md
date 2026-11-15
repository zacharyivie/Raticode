# Rattish 1 built-in node contracts

## Status

The built-in type set in this document is accepted. It preserves the capabilities of the
current runtime except its `start`, `pass`, `fail`, and `subflow` operation records. Rattish
expresses the first three concepts through `start` and `finish` properties. Legacy
`subflow` and `workflow` calls both lower to the single Rattish `workflow` node.

The field tables are a compatibility baseline, not yet the final machine-readable
contracts. They record current capability using Rattish kebab-case names. Fields labeled
"translation" replace TOML-era structure and need conformance examples before their
contracts freeze.

## Built-in type names

Rattish 1 currently names these 25 built-in node types:

```text
agent                 approval-gate         bash-command
break                 common-llm-task       copy-file
delete-file           file                  folder
http-request          jev                   local-search
local-vectorize       loop                  move-file
notification          open-resource         prompt-file
python-script         read-file             rem-prompt
rem-task              shell-script          workflow
write-file
```

All type names compare case-insensitively and lower to the spelling shown above.

Configuration schemas may mark a String property with `x-rattish-role: provider_id` or
`x-rattish-role: identifier`. The compiler lowercases values only for those identifier roles.
Opaque strings such as model names, prompts, paths, URLs, commands, and JSON values remain
case-sensitive. A closed String enum may use `x-rattish-case-insensitive: true` when its values
are language-like identifiers rather than preserved data.

## Common node fields

Every node contract includes these execution fields:

| Field | Type | Default | Rule |
|---|---|---:|---|
| `type` | identifier | required | Selects one built-in or qualified plugin contract. |
| `needs` | node ID or list | `[]` | One-time readiness requirements. |
| `to` | route or list | none | Outgoing routes after success or allowed failure. |
| `with` | local binding map | `{}` | Typed locals, resolved and snapshotted at node start; same-named input ports also receive them. |
| `start` | Boolean | `false` | Optional assertion and styling hint. |
| `finish` | `pass`, `fail`, or `none` | `none` | Optional terminal outcome and styling hint. |
| `allow-fail` | Boolean | `false` | Allows operation failure to route without failing the workflow. |
| `timeout` | duration or `none` | `none` | Limits active processing time for one execution. |
| `max-runs` | positive integer or `none` | `none` | Fails before an execution would exceed the limit. |
| `max-concurrency` | positive integer | `1` | Limits overlapping executions of this node. |
| `retry-count` | nonnegative integer | `0` | Adds operation attempts inside one routed node run. |
| `retry-delay` | duration | `1s` | Delay between attempts; counts toward workflow timeout only. |

`finish: pass` and `finish: fail` prohibit `to`. `finish: fail` also prohibits
`allow-fail: true`. `start: true` prohibits `needs`.

The old common fields map as follows:

| Current field | Rattish treatment |
|---|---|
| `allow_failure` | Renamed `allow-fail`. |
| `timeout_seconds` | Replaced by the duration-valued `timeout`. |
| `inputs` | Replaced by `with`. |
| `pipe_output` | Replaced by an explicit `with` binding. |
| `await_all_inputs` | Replaced by `needs` plus the readiness rules. |
| `on_failure` | Replaced by `allow-fail` and conditional `to` routes. |
| `label` | Editor presentation belongs in `workflow.metadata.json`. |
| `for_each` | Requires a Rattish loop and binding translation before freezing. |
| `fail_fast` | Belongs to the loop or fan-out contract, not every node. |

## Script and command nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `bash-command` | `command: String` | `working-dir: Path = none`, `env: StringMap = {}` |
| `python-script` | `script-path: Path` | `args: StringList = []`, `env: StringMap = {}` |
| `shell-script` | `script-path: Path` | `args: StringList = []`, `env: StringMap = {}` |

For all three command and script types, `with.stdin` supplies standard input. Every other
`with` name becomes an environment variable by replacing hyphens with underscores and
converting ASCII letters to uppercase while remaining available as a local. For example,
`build-mode` becomes `BUILD_MODE` and can also appear as `{{build-mode}}` in configuration.
String values are passed unchanged. Structured values use canonical compact JSON.

`python-script.args` and `shell-script.args` are ordered authored arguments. Bindings do
not append implicit positional arguments.

The runtime constructs the command environment in this order, with later sources taking
precedence:

1. inherited process environment;
2. the node's `env` map;
3. environment variables generated from `with` bindings.

An existing variable is therefore replaced for that node execution. Its presence in the
runtime environment never affects compilation.

```rattish
Node test:
  type: bash-command
  command: run-tests
  env:
    "BUILD_MODE": local
  with:
    build-mode: input.mode
```

Here `input.mode` supplies `BUILD_MODE` for this execution. It replaces both the authored
`env.BUILD_MODE` value and any inherited process value.

Both script nodes return a closed successful output object with `stdout`, `stderr`,
`exit_code`, and the resolved `script_path`. Python scripts use `python` from `PATH`.
Shell scripts use `bash`. Deployment preflight requires the selected interpreter and a
readable script file. Missing deployment resources do not invalidate compiled IR.

## File and resource nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `read-file` | `path: Path` | `encoding: String = "utf-8"`, `errors: String = "strict"` |
| `write-file` | `path: Path` | `content: String = ""`, `encoding: String = "utf-8"`, `create-dirs: Boolean = true`, `overwrite: Boolean = true`, `append: Boolean = false` |
| `copy-file` | `source-path: Path`, `destination-path: Path` | `create-dirs: Boolean = true`, `overwrite: Boolean = false` |
| `move-file` | `source-path: Path`, `destination-path: Path` | `create-dirs: Boolean = true`, `overwrite: Boolean = false` |
| `delete-file` | `path: Path` | `use-trash: Boolean = true`, `recursive: Boolean = false`, `missing-ok: Boolean = false` |
| `file` | `path: Path` | none |
| `folder` | `path: Path` | none |
| `open-resource` | `target: String` | `resource-type: auto \| file \| folder \| url \| app = auto`, `args: StringList = []` |
| `prompt-file` | `output-path: Path` | `template: String = ""`, `template-path: Path = none`, `variables: StringMap = {}`, `encoding: String = "utf-8"`, `create-dirs: Boolean = true`, `overwrite: Boolean = true` |

`prompt-file.template` and `prompt-file.template-path` are mutually exclusive when both are
nonempty. Supplying both is a compile error. Named `with` bindings replace same-named
entries from `variables` for that execution.

`read-file` returns file content and resolved path metadata. `file` and `folder` return
resolved metadata without reading file content. `prompt-file` returns the rendered prompt,
the resolved output path, and the interpolation inputs. `open-resource` returns the resolved
target and concrete resource type after `auto` detection.

`read-file` output is a closed object with required `content`, `path`, `file_name`, `file_stem`,
`file_extension`, and `directory` String fields. `path` and `directory` are resolved
runtime paths; the remaining name fields are derived from that path. Deployment
preflight verifies that the file is readable and that the selected encoding and error
mode are supported. Resource availability does not affect compilation.

Relative filesystem paths resolve from the project root. Authored absolute paths,
home-relative paths, upward `..` traversal, and intermediate symlinks are allowed;
filesystem reads use OS permissions. `copy-file`,
`move-file`, and `delete-file` support files, directories, and symlink objects. They do not
follow the final symlink. `write-file` refuses to write through a symlink. Non-append writes
replace atomically; append mode creates a missing file or appends to an existing file and
does not consult `overwrite`. When present, `with.stdin` replaces authored `content` for
that execution.

## Agent nodes

The `agent` node absorbs the current `AgentConfig` and `AgentOperation` records. Rattish has
no separate agent declaration and no `agent-id` field.

| Field | Type | Default |
|---|---|---:|
| `provider` | provider identifier | required |
| `profile` | profile identifier or `none` | `none` |
| `model` | String | provider default |
| `effort` | String | provider default |
| `working-dir` | Path | `.` |
| `prompt` | String | `""` |
| `prompt-path` | Path or `none` | `none` |
| `skill` | String or `none` | `none` |
| `memory` | `none`, `run`, or `all` | `none` |
| `tools` | StringList | `[]` |
| `mcp-servers` | StringList | `[]` |
| `env` | StringMap | `{}` |
| `extra-paths` | PathList | `[]` |
| `output-schema` | JSON object or `none` | `none` |
| `output-schema-path` | Path or `none` | `none` |
| `repair-attempts` | integer from 0 through 3 | `0` |
| `fallback` | list of at most 4 maps of `provider`, `profile`, `model`, `effort` | `[]` |

`prompt` and `prompt-path` are mutually exclusive. Supplying both is a compile error.
Supplying neither lowers to `prompt: ""` and produces a warning only.
`output-schema` and `output-schema-path` are mutually exclusive.

The compiler resolves omitted `model` and `effort` values from the selected provider's
versioned contract and writes the resolved values into IR. Provider installation and
credentials remain deployment-preflight concerns.

### Fallback providers

Each `fallback` entry requires `provider` and may set `profile`, `model` and `effort`; no
other key is allowed. The compiler resolves the entry's provider contract on its own
(`RATTISH_PROVIDER_CONTRACT_UNRESOLVED` with `details.fallback` when none exists), fills an
omitted `model` and `effort` from that entry's contract, never the step's, and freezes the
entry fully in `configuration.fallback` and in `resolutions.provider.fallback`, each with
its provider contract identity. Every fallback provider also becomes a `provider_contract`
dependency. An entry identical to the primary choice or an earlier entry warns with
`RATTISH_DUPLICATE_PROVIDER_FALLBACK`.

At run time the step runs its primary provider with its full `retry-count`. When the last
attempt fails with a `provider` error whose `details.availability` is `rate_limit`,
`overloaded`, `signed_out` or `not_installed`, the runtime emits a `node_fell_back` event
and runs the next entry with the same `retry-count`. `not_installed` is non-retryable
(`details.retryable: false`), so that provider's retries are skipped. Other error kinds,
including `output_validation` after structured-output repair, `timeout` and `budget`, end
the step, and no switch happens once the run's usage limits request a stop. The run's
`max-tokens` and `max-cost-usd` count every provider's calls. A fallback provider starts
from the step's prompt: it doesn't resume the failed session, whose side effects remain,
exactly as with a `retry-count` attempt.

Each node run record carries `provider` (the provider, profile, model and effort of its
last attempt) and, after a switch, `fallbacks` with `from`, `to`, `reason` and the
error `message`.

Preflight check `provider-available` fails only when no provider in the chain is
installed. `fallback-provider-available` warns about each missing one, and
`profile-compatible` checks every entry's profile and credentials.

`profile` selects deployment-only environment and secret configuration. It does not
select or override `provider`, `model`, or `effort`. A profile need not exist on the
compiling machine. Preflight checks that it exists and is compatible with the selected
provider.

Plaintext values are legal in `env` and other credential-shaped String fields. The
compiler emits a warning when a field name or value pattern looks sensitive. Secret
references remain the recommended form, but suspected plaintext never becomes a language
error.

The current `dynamic-count` and `fan-source` Agent fields do not enter Rattish. Authors use
a `loop` node. The current numeric Agent timeout folds into the common duration-valued
`timeout` field.

`common-llm-task` keeps the current tasks `review`, `summarize`, `explain`, `extract`,
`rewrite`, and `classify`. It also absorbs its agent provider configuration instead of
using `agent-id`.

| Field | Type | Default |
|---|---|---:|
| `task` | task identifier | `summarize` |
| `target` | String | `""` |
| `instructions` | String | `""` |
| `provider` | provider identifier | required |
| `profile` | profile identifier or `none` | `none` |
| `model`, `effort` | Agent provider fields | provider defaults |
| `working-dir` | Path | `.` |
| `memory` | `none`, `run`, or `all` | `none` |
| `output-schema` | JSON object or `none` | `none` |
| `output-schema-path` | Path or `none` | `none` |
| `repair-attempts` | integer from 0 through 3 | `0` |
| `fallback` | as on `agent` | `[]` |
| `cache` | cache duration or `none` | `none` |

`fallback` behaves as on `agent` (see Fallback providers above). `cache` is described in
Result cache below; it can't be combined with `memory` other than `none`.

### Result cache

`common-llm-task` and `jev` accept `cache`, a duration matching
`^[1-9][0-9]*(s|m|h|d|w)$` and at most `365d`. `agent` does not, because an agent can change
files; authoring it there is `RATTISH_UNKNOWN_FIELD`. The compiler materializes `cache: none`
when it is absent.

At run time, after bindings resolve and configuration renders, a node with `cache` and no
secret-derived binding computes a key: SHA-256 of the canonical JSON of the provider, profile,
model and effort of the provider choice about to run, the rendered prompt (for `jev`, the
question and state), the bound locals, the node's output schema, the project root, and the
compiled node without `source_span`, `routes`, `readiness` and `configuration.cache`. An entry
younger than both its saved expiry and the node's current `cache` duration is used as the
node's output: no handler runs, the output is still validated against the output schema, the
node run record carries `cached_at`, its `node_completed` event carries `cached: true`, and its
usage records `cached_results: 1` with 0 tokens and $0 instead of a call. Only successful,
schema-valid outputs are saved.

Entries live in `<data>/rattish-cache/<workflow-id>/<key>.json` with owner-only permissions,
at most 2 MiB each and 32 MiB per workflow; a write past the cap removes the entries expiring
first. Runs started by a test set never read or write the cache.

The common `with` block replaces the current `input-mapping` field.

## Loops and branch control

`loop` requires a `source` map. Its `source.type` selects one of these contracts:

| Source type | Required fields | Optional fields and defaults |
|---|---|---|
| `count` | none | `count: Integer = 1`, `max-concurrency: Integer = 1`, `fail-fast: Boolean = false` |
| `tabular` | `path: Path` | `max-concurrency: Integer = 1`, `fail-fast: Boolean = false` |
| `directory` | `path: Path` | `glob: String = "*"`, `include-content: Boolean = false`, `max-concurrency: Integer = 1`, `fail-fast: Boolean = false` |
| `trigger-events` | none | `include-content: Boolean = false`, `max-concurrency: Integer = 1`, `fail-fast: Boolean = false` |
| `infinite` | none | `max-concurrency: Integer = 1`, `fail-fast: Boolean = false` |

Directory and tabular sources are opened when the loop activates, so upstream nodes
may create them. An empty directory or a glob with no matching files succeeds with
zero iterations and no body activations. Missing or unreadable directories fail at
activation with a filesystem error.

The `break` node requires `loop: NodeID` and has `message: String = ""`. It cannot have
`to`. The target must name a loop whose activation lineage can reach the Break node.

When Break completes, it succeeds and closes only the named loop activation lineage. The
executor discards iterations and loop-owned activations that have not started. Executions
already running finish their current branches. The loop's completion routes wait for those
branches to settle. Repeated Break executions for the same loop lineage are idempotent.

Rattish 1 does not provide an option to cancel running iterations.

## Local retrieval nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `local-vectorize` | `source-path: Path`, `index-path: Path` | `glob: String = "**/*"`, `recursive: Boolean = true`, `chunk-size: Integer = 1200`, `chunk-overlap: Integer = 120`, `encoding: String = "utf-8"`, `mode: incremental \| full \| validate = incremental`, `embedding-strategy: String = "hash_token_v1"`, `search-strategy: String = "cosine_v1"` |
| `local-search` | `index-path: Path`, `query: String` | `top-k: Integer = 5`, `score-threshold: Number = 0.0`, `include-snippets: Boolean = true`, `include-file-metadata: Boolean = true`, `embedding-strategy: String = "hash_token_v1"`, `search-strategy: String = "cosine_v1"` |

The current runtime spells the default search strategy `cosine_v1`. Strategy names are
opaque strings, so Rattish preserves this spelling. The compiler normalizes only values
whose field contract declares a case-insensitive closed enum.

## HTTP and interaction nodes

`http-request` has these operation fields:

| Field | Type | Default |
|---|---|---:|
| `method` | String | `"GET"` |
| `url` | String | required |
| `headers` | StringMap | `{}` |
| `params` | StringMap | `{}` |
| `json` | JSON value or `none` | `none` |
| `body` | String or `none` | `none` |
| `request-timeout` | duration | `30s` |
| `retry` | HTTP retry map | attempts `1`, backoff `0s`, no retry statuses |
| `expected-statuses` | IntegerList | `[200]` |
| `response-mode` | `auto`, `json`, `text`, or `none` | `auto` |
| `output-mapping` | StringMap | `{}` |
| `secret-fields` | StringList | `[]` |
| `network-allowlist` | StringList | `[]` |

`request-timeout` is distinct from the common node `timeout`. It limits one HTTP attempt.
The `with` block may replace `url`, `headers`, `params`, `json`, or `body` for one node
execution. A bound `json` value suppresses authored `body`, and a bound `body` suppresses
authored `json`. Binding both is a runtime configuration error. `json` and `body` are also
mutually exclusive when both are authored.

`approval-gate` has these operation fields:

| Field | Type | Default |
|---|---|---:|
| `message` | String | required |
| `decision-timeout` | duration or `none` | `none` |
| `timeout-decision` | `reject` or `timeout` | `timeout` |
| `approvers` | StringList | `[]` |
| `notify` | Boolean | `false` |
| `notification-title` | String | `"Raticode approval needed"` |
| `subject` | String or `none` | `none` |

`notification` supports the channels `desktop`, `phone`, `slack`, `teams`, `webhook`, and
`email`:

| Field | Type | Default |
|---|---|---:|
| `title` | String | `"Raticode notification"` |
| `body` | String | `""` |
| `channel` | `desktop`, `phone`, `slack`, `teams`, `webhook`, or `email` | `desktop` |
| `urgency` | `low`, `normal`, or `critical` | `normal` |
| `webhook-url` | String or `none` | `none` |
| `headers` | StringMap | `{}` |
| `payload` | JSON value or `none` | `none` |
| `email-from` | String or `none` | `none` |
| `email-to` | StringList | `[]` |
| `smtp-host` | String or `none` | `none` |
| `smtp-port` | Integer | `587` |
| `smtp-username`, `smtp-password` | String or `none` | `none` |
| `smtp-starttls` | Boolean | `true` |
| `request-timeout` | duration | `30s` |
| `retry` | HTTP retry map | attempts `1`, backoff `0s`, no retry statuses |
| `expected-statuses` | IntegerList | `[200, 201, 202, 204]` |
| `network-allowlist` | StringList | `[]` |

The `phone` channel stores the title, body, workflow ID and run ID in the computer's alert
store (`phone-alerts.sqlite3` in the data folder); the paired phone reads it at its next sync.
Its success output means the notice was stored. One run keeps at most 20 notices; past that
the store keeps a single "And N more from this run" notice for the run, and the node still
succeeds. The store keeps the newest 500 notices. Its `phone-paired` preflight check is a
warning when no phone is paired. Slack, Teams, and generic webhook channels require
`webhook-url`. Email requires
`email-from`, at least one `email-to` value, and `smtp-host`. The `with` block may replace
the message fields, destination fields, headers, payload, and SMTP connection values for
one execution. Runtime credentials do not appear in the success output.

## Composition nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `workflow` | exactly one of `workflow-id: String` or `workflow-path: Path` | `version: positive Integer = none`; the common `with` block supplies public inputs. |

`workflow-id` resolves an installed workflow through the destination registry.
`workflow-path` resolves an authored `.rattish` source path, relative to the project root
unless absolute or home-relative. The compiler must load the
referenced workflow's public interface to validate `with` bindings and outputs. The child
runs as an isolated workflow invocation. Its internal node outputs are private.

Legacy TOML `parameter_bindings` and `input_bindings` both migrate to `with`. A duplicate
name is a migration error. Legacy component output selectors migrate to public outputs on
the referenced workflow rather than remaining at each call site.

See [output-contracts.md](output-contracts.md) for the public workflow interface and
built-in output contract format.

## Rem nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `rem-prompt` | `prompt: String` | `allow-actions: Boolean = false`, `wait-for-tasks: Boolean = false` |
| `rem-task` | `task-id: String` | `instructions: String = ""`, `wait: Boolean = true` |

Both nodes reach Rem through its durable store in the runtime's data directory, so the
desktop, the CLI and schedules behave alike. The Raticode backend holding Rem's lease answers
the prompt or starts the run and applies Rem's own project and folder authorization before
any worker starts. Neither node runs when no backend holds the lease
(`RATTISH_REM_UNAVAILABLE`).

`rem-prompt` posts the prompt to Rem's chat, marked as sent by the workflow, and succeeds with
Rem's reply. Unless `allow-actions` is true, the runtime discards any tasks, task controls,
memories, memory changes or files Rem proposes for that prompt and sets
`actions_dropped`. Stopping the node withdraws a prompt Rem has not started answering.

`rem-task` queues a run of an existing, undeleted task, as Rem's Run action does.
`instructions` apply to that run only. With `wait`, the node succeeds only when the run
ends `succeeded`; any other final state fails the node with error kind `rem`, or
`cancelled` when the run was stopped. Stopping or timing out the node cancels the run it
started. A task already running for anything other than the same workflow node fails with
`RATTISH_REM_BUSY`. Deployment preflight requires the task to exist.

## Classifier nodes

| Type | Required fields | Optional fields and defaults |
|---|---|---|
| `jev` | `question: String` | `question-type: yes-no \| choice = yes-no`, `criteria: {yes, no} = {}`, `options: StringMap = {}`, `threshold: Number 0..1 = 0.5`, `state: String = ""`, `model: String = "jev-1.14.0"`, `long-context: Boolean = false`, `request-timeout: duration = 15s`, `cache: cache duration \| none = none` |

`jev` asks TypeSafe's hosted Jev classifier one question about `state` and returns a closed
object with `label`, `score`, `probabilities`, `question_type`, `threshold`, `model`,
`requested_model`, `input_tokens`, `output_tokens` and `latency_ms`. `with.state` and
`with.question` replace the authored values for one execution; `state` may be any JSON
value.

- `yes-no` sends a `noul` question. `score` is the probability of yes and `label` is
  `"yes"` when `score >= threshold`, else `"no"`. `criteria.yes` and `criteria.no` are sent
  as Jev's `true` and `false` criteria; leaving them out is the warning
  `RATTISH_JEV_CRITERIA_MISSING`.
- `choice` sends a `choice` question whose criteria are `options` (2 to 255 names mapped to
  descriptions). `label` is the option Jev picked and `score` its confidence. A choice node
  can't set `criteria` or `threshold`, and a yes-no node can't set `options`.
- The model is frozen into IR, never resolved through an alias at run time.
  `request-timeout` limits one HTTP attempt and is at most `60s`.
- `cache` reuses a saved answer as described in Result cache above.

The node is not a provider-backed agent: it has no `provider`, `profile` or `effort`. The
runtime calls `https://api.typesafe.ai/v1/systemone` with the key saved in the OS keychain;
workflow source can't change the host or supply the key. Redirects are refused. Each
runtime process allows four Jev calls in flight and shares one rate-limit cooldown (60 to
300 seconds, from `Retry-After`). Errors carry `details.retryable`; the workflow runtime
skips `retry-count` for non-retryable errors and waits at least `details.retry_after_ms`
before retrying a rate limit. Deployment preflight check `jev-configured` requires a saved
key with Jev turned on. An empty `state` is a runtime configuration error, as is a state
over 512 KiB.

## Required contract work

All built-in node tables have machine-readable contracts. Secret bindings use exact
environment names and may feed compatible HTTP and notification input ports through
`with`. Each built-in contract declares its successful output schema.
