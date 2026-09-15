# Raticode

Raticode is a desktop studio and Python CLI for building and running AI coding workflows. Workflows use the Rattish language in `workflow.rattish` and connect shell commands, scripts, HTTP requests, and coding agents through explicit routes, joins, branches, and cycles.

For current workflow authoring, start with the [Rattish guide](rattish/spec/README.md).
The TOML examples below document the legacy workflow interface.

The installed command is `gof`.

## What It Can Do
    
- Run workflow nodes from start nodes through conditional edges, including recursive loops for improve/review or retry-until-output workflows.
- Execute `bash_command`, `shell_script`, `python_script`, `http_request`, and `agent` nodes.
- Use Claude Code or Codex through local CLIs, or direct OpenAI/Anthropic-compatible API profiles.
- Validate workflow structure while allowing cycles and self-loops.
- Show workflow structure in the terminal.
- Create workflow scaffolds and build workflows through an interactive wizard.
- Edit agents and workflows in an interactive terminal editor.
- Store named workflows and agents in the user data directory.
- Run a single named agent outside a workflow.
- Schedule workflows with cron expressions through APScheduler and a SQLite job store.
- Fan out agent nodes across a fixed count, rows in tabular files, or files in a directory.
- Pass outputs between nodes through prompt interpolation, explicit input mappings, or piped stdin.
- Retry nodes, set timeouts, and conditionally traverse edges based on success, failure, or output regex matches.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer if you want to run the React workflow studio
- One or both local agent CLIs if you want to run CLI-backed agent nodes:
  - `claude` for `claude_code` subscriptions
  - `codex` for `codex` subscriptions
- API credentials if you want to run direct API-backed agent nodes:
  - `OPENAI_API_KEY` or a configured Raticode secret for `openai_api`
  - `ANTHROPIC_API_KEY` or a configured Raticode secret for `anthropic_api`

Script and command nodes do not require an LLM provider CLI.

## Setup

Install the project in editable mode with development dependencies:

```bash
pip install -e ".[dev]"
```

Install optional Excel support if you want tabular fan-out from `.xlsx` files:

```bash
pip install -e ".[dev,xlsx]"
```

Confirm the CLI is available:

```bash
gof --help
```

Run the React workflow studio:

```bash
gof ui serve

# In another shell:
source "$HOME/.nvm/nvm.sh"
nvm use
cd frontend
npm ci --include=dev
npm run dev
```

For Electron development, run this from `frontend` after installing dependencies:

```bash
XDG_CONFIG_HOME="$HOME/.local/state/raticode-dev/config" \
XDG_DATA_HOME="$HOME/.local/state/raticode-dev/data" \
npm run electron:dev
```

If startup reports `concurrently: command not found`, run `nvm use` and
`npm ci --include=dev` in `frontend`. The launcher needs the local development
dependencies, including `concurrently`, `wait-on`, Vite, and Electron. XDG paths
select app storage and do not install npm dependencies.

For browser development, set `GOFER_UI_API_TOKEN` to a fresh random secret in the
backend shell before starting the server. Open the frontend with the same secret
in `http://127.0.0.1:5173/#gofer-token=<secret>`. The frontend consumes the fragment
and removes it from browser history. The API never supplies an unauthenticated
session token. Desktop startup supplies the capability through private IPC and
requires no pairing step.

UI requests allow 30 seconds to finish sending headers or a body, with a
15-second socket idle timeout. Up to 32 handlers and 8 expensive requests run at
once. Async UI jobs have a 24-hour deadline; set `GOFER_UI_JOB_TIMEOUT_SECONDS` on
the backend process to adjust it. These limits do not alter CLI workflow runs.

## Desktop App

Linux and macOS desktop packages are built from the Electron app in
`frontend/release`.
Arch users will be able to install Raticode from AUR after publication:

```bash
yay -S gofer-flow
```

The AUR packaging files live in `packaging/arch`.

On Windows, the normal installer includes the bundled `gof.exe` backend. During
setup, the "Add gof CLI to my user PATH" option is checked by default so users
can run `gof` from new PowerShell or Command Prompt sessions after installation.

On macOS, release builds publish a `.dmg`. Until the app is signed and notarized
with an Apple Developer account, users may need to approve the app in System
Settings > Privacy & Security after first launch. Unsigned macOS builds use manual
update downloads. Windows unsigned installers may show Unknown publisher or
SmartScreen prompts.

### Desktop Trust Model

The Electron renderer runs with `contextIsolation: true`, `nodeIntegration:
false`, and Electron sandboxing enabled. The preload bridge exposes only IPC
methods backed by main-process validation. Main-process IPC handlers reject
messages that do not come from the current main window main frame. Development
mode allows the configured local Vite origin for that frame, while packaged
builds allow only the bundled `frontend/dist` app entry and the bundled backend
error page.

Desktop file operations are confined to the active Raticode data directory unless
the user explicitly selects another file or folder through the native picker.
Selected paths are represented by short-lived session grants tracked by the
preload bridge and checked by the main process. Deletes use the operating system
trash instead of recursive removal, and external URL opening is restricted to
`https:` links.

## CLI-Only Installs

Release builds also publish standalone CLI binaries:

- Linux: `gof-linux-x64`
- Windows: `gof-windows-x64.exe`
- macOS: `gof-macos-<arch>`
- Debian/Ubuntu: `gofer-flow-cli_<version>_amd64.deb`
- Red Hat/Fedora: `gofer-flow-cli-<version>-1.<dist>.x86_64.rpm`
- Arch/AUR: `gofer-flow-cli`

These artifacts are intended for servers, CI, containers, and enterprise
automation where the Electron frontend is unnecessary. A Linux container can
install only the CLI with a pattern like:

```dockerfile
ADD gof-linux-x64 /usr/local/bin/gof
RUN chmod +x /usr/local/bin/gof
```

Arch users can install the CLI-only package after AUR publication:

```bash
yay -S gofer-flow-cli
```

## Release Builds

Pushes to `main` validate source and build unsigned Linux, Windows and macOS
packages, and stage a verified draft through `.github/workflows/release-candidate.yml`.
Review that candidate before pushing a `v*` tag at its exact commit. The tag workflow
publishes the staged files without rebuilding. Pull requests run source validation.

See [the release guide](docs/releasing.md) for signing setup, candidate review,
tagging and retry behavior. Signing is optional. Leave `RELEASE_SIGNING` unset for
unsigned releases; set the repository variable to `true` only after configuring
Windows and Apple signing. Signed preparation fails if credentials are missing.

Use the version bump script before tagging a release:

```bash
node scripts/bump-version.cjs 0.1.1
```

Release and packaging script behavior is covered by focused regression tests:

```bash
python -m pytest tests/unit/test_release_scripts.py
```

After building the release AppImage, update the Arch package checksum with:

```bash
node scripts/bump-version.cjs 0.1.1 --appimage-sha256 <appimage-sha256> --cli-sha256 <cli-sha256>
```

## Data Directory

By default, Raticode stores workflows, agent files, prompts, scheduler state, and scheduler PID files in the OS user data directory:

- Linux: `$XDG_DATA_HOME/gofer` or `~/.local/share/gofer`
- macOS: `~/Library/Application Support/gofer`
- Windows: `%APPDATA%\gofer`

Many commands also include a hidden `--data-dir` option used by tests and automation.

## License

Raticode is licensed under the GNU Affero General Public License, version 3.0
only. See `LICENSE` for the full terms.

## Workflow Commands

```bash
# Create a minimal workflow TOML scaffold
gof workflow create --name "Daily Analysis"

# Build a workflow interactively
gof workflow build

# Save an interactively built workflow somewhere specific
gof workflow build --output ./daily-analysis.toml

# List stored workflows
gof workflow list

# Validate a stored workflow ID or TOML path
gof workflow validate daily-analysis
gof workflow validate ./daily-analysis.toml

# Explain where references come from and when they resolve
gof workflow validate daily-analysis --explain-bindings
gof workflow validate daily-analysis --explain-bindings --json

# Show the workflow graph
gof workflow show daily-analysis

# Run a workflow
gof workflow run daily-analysis
gof workflow run ./daily-analysis.toml

# Simulate a workflow without executing nodes
gof workflow run daily-analysis --dry-run

# Print node output while running
gof workflow run daily-analysis --verbose

# Edit a workflow interactively
gof workflow edit daily-analysis

# Delete a stored workflow
gof workflow rm daily-analysis
gof workflow rm daily-analysis --yes
```

Workflows can be resolved by stored workflow ID or by a direct `.toml` path.

## Agent Commands

```bash
# Create an agent interactively or with flags
gof agent create
gof agent create \
  --name "Reviewer" \
  --subscription codex \
  --working-dir . \
  --prompt "Review the current repository changes."

# List all agents
gof agent list

# List agents for one workflow
gof agent list --workflow daily-analysis

# Run a named agent directly
gof agent run reviewer

# Edit an agent interactively
gof agent edit reviewer

# Edit an agent with flags
gof agent edit reviewer --subscription claude_code --tools "Read,Write"

# Remove an agent from its workflow
gof agent rm reviewer
gof agent rm reviewer --yes
```

Agent subscriptions currently support:

- `claude_code`, which runs `claude --print -p <prompt>`
- `codex`, which runs `codex --quiet -p <prompt>`
- `openai_api`, which calls an OpenAI-compatible Responses or chat API
- `anthropic_api`, which calls an Anthropic-compatible Messages API

Direct API subscriptions are selected with the same `subscription`, `profile`, and
`model` fields as CLI-backed agents. Store API credentials outside workflow TOML:
use the default `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` environment variables, or
create a provider profile with `--api-key-secret` so Raticode resolves
`GOFER_SECRET_<NAME>` at runtime.

```bash
gof provider profile create ci-openai \
  --subscription openai_api \
  --model gpt-5-mini \
  --api-key-secret OPENAI_CI_KEY
```

Agent prompts can be inline text or a path to a Markdown file. Inline prompts are written to the managed `prompts/` directory.

## Schedule Commands

Workflows can include schedule metadata:

```toml
[workflow.schedule]
cron_expression = "0 9 * * 1-5"
timezone = "UTC"
```

Schedule management:

```bash
# Add a workflow TOML file to the scheduler database
gof schedule add ./daily-analysis.toml

# List scheduled workflows
gof schedule list

# Remove a scheduled workflow
gof schedule remove daily-analysis

# Start the scheduler in the background
gof schedule start

# Start the scheduler in the foreground
gof schedule start --foreground

# Stop the background scheduler
gof schedule stop
```

The default scheduler database is `schedules.db` in the Raticode data directory. You can override it with `--db`.

## Webhook and API Triggers

Workflows can opt in to local webhook/API triggers. Triggers are disabled unless a workflow declares an enabled entry under `workflow.webhooks`:

```toml
[workflow.webhooks.github]
enabled = true
source = "github"
token_env = "GOFER_GITHUB_WEBHOOK_TOKEN"
fanout_path = "payload.items"
concurrency_policy = "reject_if_running"
sensitive_payload_fields = ["payload.installation.token"]
```

The trigger ID is the table key (`github` above). Enabled webhook triggers fail closed by default: every enabled trigger must configure `token_env`, configure an inline `token`, or explicitly opt in to unauthenticated local testing. `token_env` is preferred for production because the secret stays outside workflow TOML. `token` can be set directly for local testing. Authenticated HTTP requests must send the configured token with `X-Gofer-Webhook-Token` or `Authorization: Bearer <token>`.

For short-lived local testing only, a trigger can opt in to unauthenticated requests:

```toml
[workflow.webhooks.local]
enabled = true
allow_unauthenticated = true
```

Raticode marks this as high risk in validation, plan previews, Studio payloads, and bundle import previews. Do not use it for workflows exposed outside loopback, through tunnels, or to other machines.

Trigger payloads are available to nodes through the `trigger` namespace:

```toml
[[nodes]]
id = "triage"
type = "bash_command"
command = 'printf "%s\n" "$ISSUE_NUMBER"'

[nodes.inputs]
"env.ISSUE_NUMBER" = "{{trigger.payload.issue.number}}"
"env.EVENT" = "{{trigger.headers.x_github_event}}"
"env.SOURCE" = "{{trigger.source}}"
```

Start the UI/API server, then post JSON to the trigger endpoint:

```bash
curl -X POST "http://127.0.0.1:8765/api/workflows/incident-response/webhooks/github/trigger" \
  -H "Content-Type: application/json" \
  -H "X-Gofer-Webhook-Token: $GOFER_GITHUB_WEBHOOK_TOKEN" \
  -H "X-GitHub-Event: issues" \
  -d '{"issue":{"number":42},"items":[{"title":"first"}]}'
```

The request payload, normalized headers, source, received timestamp, request ID, and fan-out events are recorded with the run. Webhook payloads are sanitized by default before they are written to run logs or `.trigger.json` replay sidecars. Common sensitive field names such as `token`, `secret`, `password`, authorization fields, API keys, and entries listed in `sensitive_payload_fields` are masked recursively. Replay uses the saved sanitized payload, so masked fields replay as masked values.

Runs created from webhook triggers can be replayed from the CLI or UI run history:

```bash
gof workflow trigger incident-response \
  --trigger-id github \
  --payload-file event.json \
  --header "X-GitHub-Event: issues" \
  --token "$GOFER_GITHUB_WEBHOOK_TOKEN"

gof workflow trigger incident-response --trigger-id github --replay-run-id run-20260626.log
```

If a workflow needs exact replay of sensitive payload fields, it must opt in explicitly:

```toml
[workflow.webhooks.github]
enabled = true
token_env = "GOFER_GITHUB_WEBHOOK_TOKEN"
store_raw_payload = true
```

`store_raw_payload = true` keeps the original payload in the replay sidecar for exact replay and is shown as a high-risk setting in plan previews. Run logs and latest-run API responses still mask sensitive trigger payload values.

For `trigger_events` fan-out nodes, `fanout_path` can point at a JSON array such as `payload.items`. Each array object becomes one trigger event.

## Workflow TOML

A minimal command-only workflow:

```toml
[workflow]
id = "hello"
name = "Hello Workflow"

[[nodes]]
id = "step-one"
type = "bash_command"
command = "echo hello"

[[nodes]]
id = "step-two"
type = "bash_command"
command = "echo world"

[[edges]]
from = "step-one"
to = "step-two"
```

A workflow with an agent node:

```toml
[workflow]
id = "analysis"
name = "Analysis"

[agents.reviewer]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/reviewer.md"
tools = []
mcp_servers = []
extra_paths = []
env = {}

[[nodes]]
id = "collect"
type = "bash_command"
command = "git diff --stat"
pipe_output = true

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
prompt_path = "prompts/reviewer.md"
working_dir = "."

[[edges]]
from = "collect"
to = "review"
```

Prompt files support `{{var}}` interpolation from the context passed to an agent. When a predecessor node has `pipe_output = true`, its output is prepended to downstream agent prompts and sent to downstream script or command nodes as stdin.

### Workflow inputs and run-scoped variables

Declare immutable invocation values under `workflow.inputs` and mutable state under
`workflow.variables`. Each workflow or subflow invocation receives a fresh scope;
values cross a call boundary only through `input_bindings` and declared outputs.
Legacy `workflow.parameters`, `parameter_bindings`, `--param`, and `{{params.*}}`
remain supported aliases.

```toml
[workflow]
id = "development-checks"
name = "Development checks"

[workflow.inputs.project_dir]
type = "path"
required = true

[workflow.variables]
attempt = 1

[[nodes]]
id = "test"
type = "bash_command"
command = "python -m pytest"
working_dir = "{{inputs.project_dir}}"

[[nodes]]
id = "report"
type = "bash_command"
command = "echo {{nodes.test.exit_code}}"
```

Pass inputs with `gof workflow run development-checks --input project_dir=/work/repo`
or `--inputs-json`. Exact references preserve booleans, numbers, arrays, and objects;
embedded references are converted to text. Use `set`, `update`, or `clear` for ordered
mutations. Concurrent writers must all use `merge`, which stores values by invocation
ID, or validation rejects the workflow as nondeterministic.

#### Resume, checkpoint, and replay semantics

- Inputs and variables belong to one invocation. A fresh run starts from supplied/default
  inputs and declared variable initializers; it never inherits mutable state from another run.
- Resume and approval checkpoints save the invocation's current inputs and variables. Resume
  loads that snapshot before validating required inputs, then applies any newly supplied input
  values as explicit overrides.
- Non-secret values are stored directly in the local checkpoint. Declared secret inputs,
  declared secret variables, and secret-bearing node outputs are shown as `***` in the public
  checkpoint and stored in an authenticated encrypted scope using a local key. If that key is
  unavailable, explicitly resupply every redacted secret input. A normal resume then rebuilds
  redacted variable/output state by rerunning the affected nodes and their descendants. An
  approval checkpoint that contains additional unrebuildable secret state instead asks you to
  restart the workflow from the beginning.
- Logs, plans, API/UI run metadata, and cache records contain redacted public values. Execution
  keeps an internal raw value so a downstream node can consume a token without receiving `***`.
- Webhook replay saves the sanitized trigger payload plus the explicit invocation inputs used by
  the original request. Secret explicit inputs use the same encrypted local scope. Configured
  webhook `input_bindings` are evaluated again against the saved replay payload.
- Subflows and workflow calls create new scope frames. Only explicit `input_bindings` enter the
  child, and only declared outputs return to the caller.

### Runtime bindings and shell expansion

`gof workflow plan` and `gof workflow validate --explain-bindings` show each explicit
Raticode reference with its producer, source and destination types, resolution phase,
status, secret readiness, and any string coercion. Exact references, including
`workflow.*`, `run.*`, and `secret.*` inputs, resolve without becoming templates.
They retain the producer's native value when the destination supports it; process
environment, argument, and standard-input boundaries report their string coercion.
References embedded in a larger string, such as `"item={{loop.current.value}}"`, are
converted to text.

Raticode resolves `{{...}}` references before starting an operation. Node input mappings
then provide typed operation inputs, while `env.*` mappings and `operation.env` become
process environment variables. Expressions such as `${FILE_NAME}` belong to the shell;
Raticode deliberately does not parse or validate arbitrary shell expansion.

### HTTP request nodes

Use `http_request` when a workflow needs a structured API call without shelling out to `curl`. URL, headers, query params, JSON body fields, raw body, and output mappings support `{{node.data.path}}`, `{{previous.output}}`, `{{trigger.value}}`, and loop interpolation. Secret references use `{{secret.NAME}}` or `secret:NAME`; at runtime Raticode reads `GOFER_SECRET_NAME` or `NAME` from the environment and masks configured secret fields in logs.

API polling:

```toml
[[nodes]]
id = "poll-status"
type = "http_request"
method = "GET"
url = "https://api.example.com/jobs/{{trigger.job_id}}"
expected_statuses = [200]
response_mode = "json"

[nodes.headers]
Authorization = "{{secret.API_TOKEN}}"

[nodes.output_mapping]
state = "json.state"
```

Issue creation:

```toml
[[nodes]]
id = "create-issue"
type = "http_request"
method = "POST"
url = "https://api.example.com/issues"
expected_statuses = [201]
response_mode = "json"
secret_fields = ["Authorization"]

[nodes.headers]
Authorization = "{{secret.API_TOKEN}}"

[nodes.json]
title = "{{previous.data.title}}"
body = "{{previous.output}}"

[nodes.output_mapping]
issue_id = "json.id"
issue_url = "json.url"
```

Slack-style message posting:

```toml
[[nodes]]
id = "notify"
type = "http_request"
method = "POST"
url = "https://hooks.slack.com/services/{{secret.SLACK_WEBHOOK_PATH}}"
expected_statuses = [200]
body = '{"text":"Workflow {{trigger.workflow_id}} finished"}'
secret_fields = ["url"]
```

Webhook callback with retry:

```toml
[[nodes]]
id = "callback"
type = "http_request"
method = "POST"
url = "{{trigger.callback_url}}"
expected_statuses = [200, 202]

[nodes.json]
status = "{{previous.terminal_status}}"
message = "{{previous.output}}"

[nodes.retry]
attempts = 3
backoff_seconds = 1.5
retry_on_statuses = [429, 500, 502, 503, 504]
```

### Approval gates and notifications

Use `approval_gate` when a workflow should pause before continuing. The node writes a pending approval request under the Raticode data directory, records the run ID and node ID in the run log, and resumes when a user approves or rejects it from the CLI. Approval messages support the same `{{node.output}}`, `{{previous.output}}`, `{{trigger.value}}`, and loop interpolation used by other nodes.

```toml
[[nodes]]
id = "review-deploy"
type = "approval_gate"
message = "Deploy {{previous.output}} to production?"
timeout_seconds = 3600
approvers = ["ops"]
notify = true
notification_title = "Deployment approval needed"

[[nodes]]
id = "deploy"
type = "bash_command"
command = "./deploy.sh"

[[nodes]]
id = "record-rejection"
type = "notification"
title = "Deployment rejected"
body = "Run {{review-deploy.data.runId}} was rejected: {{review-deploy.data.notes}}"

[[edges]]
from = "review-deploy"
to = "deploy"
condition = "on_success"

[[edges]]
from = "review-deploy"
to = "record-rejection"
condition = "on_failure"
```

CLI approval workflow:

```bash
# List pending approvals
gof workflow approvals

# Approve or reject a pending gate by run ID and node ID
gof workflow approve 2026-06-24T10-15-300000-0400.log review-deploy \
  --workflow deploy-flow \
  --by alice \
  --notes "Change reviewed"
gof workflow reject 2026-06-24T10-15-300000-0400.log review-deploy \
  --workflow deploy-flow \
  --by bob \
  --notes "Rollback plan missing"
```

Approval nodes return success only for explicit approval. Rejection and timeout return failure, so `on_failure` routes can handle both. Use `output_matches` against `approved`, `rejected`, or `timeout` when those outcomes need separate paths.

Notifications currently support the `desktop` channel and use an adapter boundary so future email, Slack, Teams, or webhook providers can share the same TOML shape:

```toml
[[nodes]]
id = "notify-ops"
type = "notification"
title = "Workflow needs attention"
body = "Workflow {{trigger.workflow_id}} run {{trigger.run_id}} needs review."
channel = "desktop"
urgency = "normal"
```

### Resource limits

Workflows use conservative default resource ceilings for local execution: fan-out item counts, files scanned, bytes read per file and per run, vector index size, subprocess output capture, node/run log bytes, UI request and log response bytes, chat prompt size, watcher queue depth, and watcher/continuous-run concurrency are bounded. A workflow that exceeds a limit fails closed with an error that includes the configured limit.

Default limits:

```toml
[workflow.resource_limits]
max_fanout_items = 1000
max_files_scanned = 5000
max_file_read_bytes = 1048576
max_aggregate_read_bytes = 32000000
max_vector_index_bytes = 50000000
max_bundle_entries = 1000
max_bundle_entry_bytes = 10000000
max_bundle_total_uncompressed_bytes = 64000000
max_bundle_compressed_bytes = 64000000
max_bundle_metadata_bytes = 1048576
max_bundle_compression_ratio = 100.0
max_log_bytes_per_node = 1048576
max_log_bytes_per_run = 20000000
max_api_request_body_bytes = 1048576
max_api_log_response_bytes = 1048576
max_chat_prompt_bytes = 128000
max_subprocess_output_bytes = 2000000
max_watcher_queue_depth = 1000
max_watcher_concurrency = 4
max_fanout_concurrency = 16
```

Workflow bundle import is validated before the bundled workflow TOML is trusted, so
CLI/UI import and preview use host-side bundle limits. Configure those limits with
environment variables on the `gof` or UI server process:

```bash
GOFER_BUNDLE_MAX_ENTRIES=1000
GOFER_BUNDLE_MAX_ENTRY_BYTES=10000000
GOFER_BUNDLE_MAX_TOTAL_UNCOMPRESSED_BYTES=64000000
GOFER_BUNDLE_MAX_COMPRESSED_BYTES=64000000
GOFER_BUNDLE_MAX_METADATA_BYTES=1048576
GOFER_BUNDLE_MAX_COMPRESSION_RATIO=100
```

Advanced local batch workflows can opt in to larger limits in TOML:

```toml
[workflow.resource_limits]
max_fanout_items = 2000
max_files_scanned = 10000
max_file_read_bytes = 2097152
max_aggregate_read_bytes = 67108864
max_vector_index_bytes = 104857600
max_log_bytes_per_node = 2097152
max_log_bytes_per_run = 41943040
max_api_request_body_bytes = 2097152
max_api_log_response_bytes = 2097152
max_chat_prompt_bytes = 262144
max_subprocess_output_bytes = 4000000
max_watcher_queue_depth = 2000
max_watcher_concurrency = 4
max_fanout_concurrency = 16
```

File watcher queues are bounded by `max_watcher_queue_depth`. When a hot watcher produces more events than fit, Raticode keeps the newest queued batches/events and drops the oldest overflow before starting more runs. `max_watcher_concurrency` is also capped by the trusted server-wide limit, so a workflow override cannot raise host-wide watcher or continuous-run concurrency.

## Node Types

### `bash_command`

Runs a command through `bash -c`.

```toml
[[nodes]]
id = "list-files"
type = "bash_command"
command = "find . -maxdepth 2 -type f"
working_dir = "."
env = { EXAMPLE = "1" }
```

### `python_script`

Runs a Python script with optional arguments.

```toml
[[nodes]]
id = "transform"
type = "python_script"
script_path = "scripts/transform.py"
args = ["--format", "json"]
env = {}
```

### `shell_script`

Runs a shell script with `bash`.

```toml
[[nodes]]
id = "deploy"
type = "shell_script"
script_path = "scripts/deploy.sh"
args = ["staging"]
```

### `agent`

Runs a configured LLM agent.

```toml
[[nodes]]
id = "summarize"
type = "agent"
agent_id = "summarizer"
prompt_path = "prompts/summarize.md"
working_dir = "."
input_mapping = { diff = "collect" }
```

Agent prompt context may contain file paths from triggers, fan-out items, or upstream
outputs. Those path strings are treated as data and do not expand the provider
sandbox. To intentionally grant access outside `working_dir`, add exact paths to the
agent config:

```toml
[agents.summarizer]
subscription = "codex"
working_dir = "."
prompt_path = "prompts/summarize.md"
extra_paths = ["/path/to/shared/context"]
```

## Node Controls

All node types can use these graph-level controls:

```toml
[[nodes]]
id = "fragile-step"
type = "bash_command"
command = "curl -f https://example.com"
retry_count = 2
retry_delay_seconds = 3
timeout_seconds = 30
pipe_output = true
```

- `retry_count`: number of retries after the first failed attempt.
- `retry_delay_seconds`: delay between retry attempts.
- `timeout_seconds`: subprocess timeout. When a subprocess node is stopped or
  times out, Raticode terminates the subprocess tree: POSIX platforms signal
  the started process group, and Windows uses `taskkill /T` with a forced
  fallback. Descendants that deliberately detach into a separate session,
  process group, service, or job may outlive the workflow and should clean up
  their own background work.
- `pipe_output`: makes the node output available as stdin or prepended prompt text for downstream nodes.

## Conditional Edges

Edges default to `always`. They can also run only on success, only on failure, or when output matches a regex.

```toml
[[edges]]
from = "test"
to = "deploy"
condition = "on_success"

[[edges]]
from = "test"
to = "notify"
condition = "on_failure"

[[edges]]
from = "scan"
to = "investigate"
condition = "output_matches"
output_pattern = "CRITICAL|HIGH"
```

Supported conditions:

- `always`
- `on_success`
- `on_failure`
- `output_matches`

## Agent Fan-Out

Agent nodes can run multiple instances concurrently.

Fixed count:

```toml
[[nodes]]
id = "parallel-review"
type = "agent"
agent_id = "reviewer"
prompt_path = "prompts/review.md"
working_dir = "."

[nodes.fan_source]
type = "count"
count = 4
max_concurrency = 2
fail_fast = false
```

Rows from `.csv`, `.jsonl`, or `.xlsx`:

```toml
[[nodes]]
id = "process-rows"
type = "agent"
agent_id = "row-worker"
prompt_path = "prompts/row.md"
working_dir = "."

[nodes.fan_source]
type = "tabular"
path = "data/items.csv"
max_concurrency = 8
fail_fast = true
```

Files in a directory:

```toml
[[nodes]]
id = "process-files"
type = "agent"
agent_id = "file-worker"
prompt_path = "prompts/file.md"
working_dir = "."

[nodes.fan_source]
type = "directory"
path = "docs"
glob = "*.md"
include_content = true
max_concurrency = 4
fail_fast = false
```

Fan-out context includes:

- Count fan-out: `{{index}}`
- Tabular fan-out: each column by name, plus `_row` containing the whole row as JSON
- Directory fan-out: `{{file_path}}`, `{{file_name}}`, and optionally `{{file_content}}`

## Development

Run checks after code changes:

```bash
ruff check src tests --fix
mypy src tests
python -m pytest
cd frontend && npm test && npm run check:build
```

Frontend checks are split by runtime:

- `npm test` runs the fast Node-based React/helper tests.
- `npm run check:build` verifies the production Vite bundle.
- `npm run test:browser` builds the Vite app, serves the built files with
  mocked API responses, and drives the workflow studio in Electron/Chromium.
  Use it for browser-level regressions around canvas pointer events, dialogs,
  panels, responsive layout, and save payload serialization. In headless Linux
  CI, run it under `xvfb-run -a`.

Run focused tests while developing:

```bash
python -m pytest tests/unit/
python -m pytest tests/integration/
python -m pytest tests/regression/
python -m pytest tests/unit/test_executor.py::test_name -v
```

The test suite uses `FakeSubscription` from `tests/conftest.py`, so tests do not require real Claude Code or Codex CLI access.
