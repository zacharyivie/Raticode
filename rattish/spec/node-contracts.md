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

Rattish 1 currently names these 26 built-in node types:

```text
agent                 approval-gate         bash-command
break                 common-llm-task       copy-file
delete-file           file                  folder
http-request          jev                   local-search
local-vectorize       loop                  mcp-tool
move-file             notification          open-resource
prompt-file           python-script         read-file
read-web-page         rem-prompt            rem-task
shell-script          workflow              write-file
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
| `bash-command` | `command: String` | `working-dir: Path = none`, `env: StringMap = {}`, `shell: auto, bash, or powershell = auto` |
| `python-script` | `script-path: Path` | `args: StringList = []`, `env: StringMap = {}` |
| `shell-script` | `script-path: Path` | `args: StringList = []`, `env: StringMap = {}`, `shell: auto, bash, or powershell = auto` |

`shell` picks the program that runs a `bash-command` command or a `shell-script` file:

| `shell` | `bash-command` | `shell-script` |
|---|---|---|
| `auto` | `powershell.exe -Command` on Windows, `bash -c` elsewhere | `bash` from `PATH` |
| `bash` | bash; on Windows, Git for Windows' `bin\bash.exe` | the same bash |
| `powershell` | `powershell.exe -Command` on Windows, `pwsh -Command` elsewhere | `-File` with the same program |

On Windows, `shell: bash` finds Git for Windows next to `git.exe` on `PATH`, in the folder
Git for Windows records in the registry, or in its default install folders, then accepts
another `bash.exe` on `PATH`. It never runs a `bash.exe` inside the Windows folder or
`WindowsApps`: those are the WSL launcher. When no bash is found, preflight fails with
`RATTISH_PREFLIGHT_RESOURCE_MISSING`, whose details carry `install_url`, the Git for Windows
download page. A run that skips preflight fails the node with the configuration error
`RATTISH_RUNTIME_SHELL_UNAVAILABLE`.

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
| `sandbox` | `on` or `off` | `on` |
| `network` | `any`, `none`, or list of host names (`*.` prefix allowed) | `[]` |

`prompt` and `prompt-path` are mutually exclusive. Supplying both is a compile error.
Supplying neither lowers to `prompt: ""` and produces a warning only.
`output-schema` and `output-schema-path` are mutually exclusive.

The compiler resolves omitted `model` and `effort` values from the selected provider's
versioned contract and writes the resolved values into IR. Provider installation and
credentials remain deployment-preflight concerns.

### Sandbox and network

With `sandbox: on`, the runtime starts `claude-code` with Claude Code's sandbox settings
(the settings Rem uses for unattended turns): shell writes are limited to `working-dir`,
`extra-paths`, a per-run scratch folder under the data folder (removed when the run ends)
and, for a git worktree, part of the repository's shared `.git` folder; credential paths are
hidden from the shell and denied to the Read and Edit tools; Bash is not approved
automatically; and the shell's network allowlist is `network`. In a `workspace: worktree`
run, a step whose `working-dir` is in the run's worktree may write only `objects/`, the
run's own `worktrees/<name>/` folder, `refs/heads/raticode/run/` and
`logs/refs/heads/raticode/run/`, and its environment adds `gc.auto=0` and
`maintenance.auto=false` through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`
after any entries already there. `git add` and `git commit` on the run's branch work; every
other branch, tag and the repository's config stay read-only. Any other git worktree used as
a step's `working-dir` gets the whole shared `.git` folder. `network: any` adds the permission rule
`WebFetch(domain:*)`, which allows every host. `codex` keeps the reach of `workspace-write`
through a Codex permission profile that also denies the home credential paths and Raticode's
credential folders; `network: any` turns on its workspace network, and a list of hosts is a
preflight error (`RATTISH_PREFLIGHT_NETWORK_UNSUPPORTED`). A run started by a trigger refuses a Codex
profile with full access (`RATTISH_PREFLIGHT_SANDBOX_REQUIRED`). The sandbox applies only to
the built-in Claude Code and Codex adapters.

Providers without a sandbox, every provider on native Windows, and `sandbox: off` run
unsandboxed: preflight check `runs-unsandboxed` warns with `RATTISH_PREFLIGHT_RUNS_UNSANDBOXED`
for each provider in the chain. On a sandboxed step, `sandbox-widened` warns with
`RATTISH_PREFLIGHT_SANDBOX_WIDENED` for each `extra-paths` entry outside the project root and
for `network: any`. `extra-paths-allowed` fails with `RATTISH_PREFLIGHT_CREDENTIAL_PATH` when an
entry is, holds or sits in a credential folder (Raticode's account, device, browser-grant or
secrets folders, or a home path such as `~/.ssh`), for every provider and sandbox setting; the
runtime refuses the step again with `RATTISH_SANDBOX_CREDENTIAL_PATH`. `sandbox: off` also warns at compile time with
`RATTISH_AGENT_SANDBOX_OFF`. `sandbox-available` fails when Claude Code's sandbox can't
start or a profile passes its own `--settings`. `network-declared` fails with
`RATTISH_PREFLIGHT_NETWORK_UNDECLARED` when a sandboxed step's tool rules allow a network
command while `network` lists no hosts, or its static prompt names a web address that
`network` doesn't cover and its tools don't include WebFetch.

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

A step never moves to another account of a provider at a usage limit. Each entry's sign-in
is the profile's `account_id`, the provider's default sign-in (no profile, or a profile
with no sign-in of its own), or the profile's own token or config folder; a profile whose
environment or secret references set an API key (such as `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY` or `XAI_API_KEY`), and every `anthropic-api` and `openai-api` entry, pays
with an API key instead. Once an entry fails with `rate_limit`, the runtime passes over
every later entry of that provider whose sign-in is a different subscription sign-in than
the ones that hit the limit, whatever error ends the entries in between. Each one passed
over is appended to `fallbacks` with `skipped: true`, `reason: rate_limit`, `from` the
entry that failed last, and a `message` such as `skipped work-account: Raticode doesn't
move work to another account of the same provider at a usage limit`; no `node_fell_back`
event is emitted for it. Another provider, an API-key profile and the same sign-in with
another model are still used, and `signed_out`, `overloaded` and `not_installed` still
switch accounts. If every remaining entry is skipped, the step ends with the failure of
the last entry that ran.

Each node run record carries `provider` (the provider, profile, model and effort of its
last attempt) and, after a switch or a skip, `fallbacks` with `from`, `to`, `reason`, the
error `message` and, for a skipped entry, `skipped: true`.

Preflight check `provider-available` fails only when no provider in the chain is
installed. `fallback-provider-available` warns about each missing one, and
`profile-compatible` checks every entry's profile and credentials, and warns with
`RATTISH_PREFLIGHT_FALLBACK_SAME_PROVIDER_ACCOUNT` when an entry is another subscription
account, or the default sign-in, of an earlier entry's provider: it will only be used for
sign-in or outage errors. For `anthropic-api` and `openai-api` it also checks each entry's
model: a retired model, or one missing from the
provider's model list, is an error, and a deprecated model is a warning with its retirement
date. An error in any entry is reported ahead of a warning.

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

`read-web-page` has these operation fields:

| Field | Type | Default |
|---|---|---:|
| `url` | String | required |
| `max-chars` | Integer 1..1000000 | `20000` |
| `format` | `markdown` or `text` | `markdown` |
| `request-timeout` | duration (`ms`, `s` or `m`) | `30s` |
| `render` | `never`, `auto` or `always` | `never` |
| `network-allowlist` | StringList | `[]` |

`with.url` replaces the authored `url` for one execution. The runtime sends one GET with no
cookies or credentials, follows at most 5 redirects, and applies `request-timeout` to the
whole fetch. Before each connection it resolves the host and refuses the hop unless every
address is public or `network-allowlist` names the host, a `*.domain` pattern, the address
or a network containing it; the connection is pinned to the checked address. It reads at
most 5 MiB and accepts `text/html`, `application/xhtml+xml` and `text/plain` (an untyped
body is sniffed); any other type fails with `RATTISH_WEB_PAGE_UNSUPPORTED_TYPE` naming it.
No JavaScript runs unless `render` asks for it.

`render: never` only fetches. `render: always` renders every HTML page, and `render: auto`
renders an HTML page only when the fetched page's `likely_needs_javascript` is true. Plain
text is never rendered. Rendering runs after the plain fetch, on its final address, with an
installed Chrome, Chromium or Edge started with `--headless=new --dump-dom --timeout` and a
new, empty profile in the run's scratch folder that is deleted when the render ends. It
gets its own `request-timeout`; the browser's `--timeout` stops loading a little earlier
(up to 5 seconds) so the loaded page is still printed. Every browser request, loopback
included, goes through a local proxy that lives only for that render and applies the same
address check and `network-allowlist` to each host, connecting to the checked address.
Refused requests get HTTP 403 inside the page. The rendered HTML, at most 5 MiB, goes
through the same text extraction. With no browser installed, `always` fails with
`RATTISH_WEB_PAGE_NO_BROWSER` (`configuration`) and `auto` keeps the fetched page; preflight
reports the missing browser as an error for `always` and a warning for `auto`. A browser
that fails or runs out of time fails `always` with `RATTISH_WEB_PAGE_BROWSER_FAILED`
(`network`) or `RATTISH_WEB_PAGE_TIMEOUT`, and `auto` keeps the fetched page.

The output is a closed object with `title`, `description`, `text`, `links`, `final_url`,
`truncated`, `likely_needs_javascript` and `rendered`, which is true when the fields come
from the rendered page. `text` is taken from `<article>` elements when
present, else `<main>`, else the body, leaving out `nav`, `aside`, `footer`, the page
`header` (an article's own header stays), buttons, frames, scripts and hidden elements.
`format: markdown` keeps headings, lists, links, emphasis, code, quotes and tables as
Markdown; `text` drops the marks. `text` is cut to `max-chars` at a nearby space or line
break. `links` holds up to 200 unique absolute `http` and `https` links from the whole page
without fragments. `truncated` is true when the body (or the rendered HTML) passed 5 MiB or
`text` passed `max-chars`. `likely_needs_javascript` is true when the readable text is under 200
characters and the page has an app root element or a noscript JavaScript notice, or under
50 characters and any script; after a render it describes the rendered page. Every output field is untrusted page content. Failures use
`RATTISH_WEB_PAGE_*` codes with kind `network`, except `RATTISH_WEB_PAGE_INVALID_URL`
(`configuration`) and `RATTISH_WEB_PAGE_TIMEOUT` (`timeout`).

`mcp-tool` has these operation fields:

| Field | Type | Default |
|---|---|---:|
| `server` | String, a name in Rem's MCP server list | required |
| `tool` | String | required |
| `arguments` | JSON object, or a String that renders to one | `{}` |
| `output-schema` | Object or `none` | `none` |
| `output-schema-path` | Path or `none` | `none` |
| `working-dir` | Path | `.` |
| `call-timeout` | duration (`ms`, `s`, `m` or `h`) | `60s` |
| `sandbox` | `on` or `off` | `on` |
| `network` | host list, `any` or `none` | `[]` |

The runtime resolves `server` in Rem's MCP server list (`rem-resources.json` in the data folder)
when the node starts, opens a fresh session (a `stdio` server is started for the call and stopped
after it; an `http` server gets one Streamable HTTP session), sends `initialize`, lists the tools,
checks the rendered arguments against the tool's `inputSchema` and sends `tools/call`. `with.arguments`
replaces the authored value; a String value must render to JSON text holding an object. Unquoted
block-map keys lower-case like every Rattish map key, so the compiler warns with
`RATTISH_MCP_ARGUMENT_KEY_CHANGED` when one changes spelling.

The output is the result's `structuredContent` when present, else its `text` content items joined
with line breaks. With an output schema whose `type` isn't `string`, text is parsed as JSON. A result
with `isError: true` fails with `RATTISH_MCP_TOOL_ERROR` (kind `command`).

A `stdio` server runs in an operating-system sandbox unless `sandbox: off` or the host is native
Windows: writes are limited to `working-dir`, the run's scratch folder and the server's package
cache, the same credential folders as agent steps and the rest of the data folder are hidden, and
the network is limited to `network` through a host-filtering proxy (bubblewrap with a private
network namespace on Linux, `sandbox-exec` on macOS). `call-timeout` covers the start, the listing
and the call.

The package cache is `mcp-cache/<definition digest>/` in the data folder, one per server
definition, shared by preflight, the tool listing and runs. `npm_config_cache` is its `npm` folder,
`XDG_CACHE_HOME` its `xdg` folder and `UV_CACHE_DIR` `xdg/uv`; `TMPDIR`, `TMP` and `TEMP` stay in
the run's scratch folder. Changing or removing the server's library entry deletes its cache, and a
cache unused for 30 days is pruned. Only list-only starts (preflight, the tool listing) write it.
A tool call reads it through a throwaway layer: a bubblewrap overlay (0.11 or later) on Linux, an
APFS clone on macOS, or, on Linux without overlays, a copy (`cp -a --reflink=auto`) in the run's
scratch folder of a cache a list-only start has filled and that holds at most 500 MB, with the
lasting folder shown read-only. When none of these works, the call gets an empty cache.

A `stdio` server's environment is not the host process's. It holds a base set (`HOME`, `LOGNAME`,
`PATH`, `SHELL`, `TERM`, `USER`, `LANG`, `LANGUAGE`, `LC_*`, `TMPDIR`, `TMP`, `TEMP`, and on
native Windows the system variables such as `SYSTEMROOT`, `USERPROFILE` and `PATHEXT`, matched
without case), then the variables the server's library entry names in `env` (names only), then
the sandbox's own temporary, cache and proxy variables, which win. `GOFER_*` variables are never
passed. A named variable that isn't set is left out.

Preflight checks `server-registered`, `sandbox-available`, `tool-arguments-valid` (lists the tools,
checks the tool name and the literal arguments; values with templates are checked at run time) and
warns with `runs-unsandboxed`, `package-pinned`, `env-set` and `cache-readable`. `package-pinned` reports
`RATTISH_PREFLIGHT_MCP_PACKAGE_UNPINNED` when the server's command starts a package runner (`npx`,
`bunx`, `pnpm dlx`, `uvx`, `pipx run` and their aliases) whose package has no exact version
(`pkg@1.2.3`, `pkg==1.2.3`); pinning covers the top-level package only. `env-set` reports
`RATTISH_PREFLIGHT_MCP_ENV_MISSING` when a variable the server names isn't set. `cache-readable`
reports `RATTISH_PREFLIGHT_MCP_CACHE_UNREADABLE` with severity `info` when the server starts a
package runner, the step's `network` doesn't list its registry host (`registry.npmjs.org`, or
`pypi.org` and `files.pythonhosted.org`), and this computer's tool calls would get an empty cache. `tool-arguments-valid` never starts a `stdio` server that would run
unsandboxed: it reports `RATTISH_PREFLIGHT_MCP_TOOL_CHECK_SKIPPED` with severity `info` instead,
and the run checks the tool and arguments. A trigger approval covers each server's definition,
including its variable names but never their values, under the key
`mcp-server:NAME`, and a triggered run checks it again before it starts or connects to the server.
Each regular file inside the project that a `stdio` server's command or arguments name, a relative
value resolved from `working-dir`, is covered too, with role `script` under its absolute path. A
triggered run checks each one right before the server starts and fails the step with
`RATTISH_APPROVED_FILE_CHANGED` when it differs or wasn't there at approval.
When an unpinned server's definition is new or changed, Review changes marks the entry with
`unpinned` and `warning`, and it is listed in `unseen` with reason `unpinned`, so approving it needs
`accept_unseen`.

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
| `options` | StringList of 2 to 8 labels, or `none` | `none` |
| `reject-options` | StringList | `[]` |
| `reply` | `none`, `optional`, or `required` | `none` |

With `options`, the person answers with one of the labels instead of Approve or Reject.
Labels are 1 to 60 characters with no leading or trailing space and must differ by more than
capital letters. The output's `choice` holds the label picked, and `decision` is `rejected`
when that label is in `reject-options` and `approved` otherwise, so `approved` keeps working
in routes. `reject-options` must name listed options. `reply` asks for typed text, returned
as the output's `reply`. A timeout picks no option: `choice` is null and `reply` is empty.
The compiler narrows `choice` to the listed options plus null, and a route that compares
`node.<id>.output.choice` with `==` or `!=` against a String that isn't a listed option is
rejected with `RATTISH_APPROVAL_CHOICE_UNKNOWN`.

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
