---
title: CLI command index
description: Every Raticode CLI command with its arguments and options, generated from the CLI itself.
pageClass: docs-layout
---

# CLI command index

::: info Generated page
`scripts/generate-cli-reference.py` writes this page from the CLI's command tree, and a test fails when the two differ. It describes the development branch: commands marked Backend need Raticode 0.5.3 or later. Run `Raticode <command> --help` to see what your installed version offers.
:::

Each command is labelled with its kind:

- **Backend** commands send requests to a running backend. See [Headless CLI](./cli-headless.md), [Rem and tickets from the CLI](./cli-rem.md) and [talking to a running backend](./cli.md#talk-to-a-running-backend).
- **Offline** commands work on files and local stores without a backend.
- **Legacy** commands work only with older TOML workflows. For Rattish workflows use `Raticode rattish`.

The [CLI reference](./cli.md) explains how the commands fit together.

## Top-level commands

### Raticode api

*Backend: needs a running Raticode backend.* Call any endpoint of the local Raticode backend, like `gh api`.

- `METHOD` (required): GET, POST, PUT, PATCH or DELETE.
- `PATH` (required): API path such as /rem/kanban; the /api prefix is optional.
- `--data` / `-d` `STR`: JSON body, @file, or @- to read stdin.
- `--raw-field` / `-f` `STR` (repeatable): key=value string. Query parameter for GET and DELETE, else a body field. Dots nest: params.taskId=t1.
- `--field` / `-F` `STR` (repeatable): key=value parsed as JSON when it is valid JSON (true, 3, null, &#91;..&#93;); @file reads the value from a file.
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--expected-revision` `STR`: Fail with exit 4 instead of overwriting if the record changed since this revision. Sent as expected_revision on /rem/ paths and expectedRevision elsewhere (inside params for organizations); a number when it is all digits.
- `--follow`: Stream an NDJSON endpoint, or poll a GET that returns an events list, writing one JSON object per line.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--raw`: Print the response body as-is instead of the envelope.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.
- `--non-interactive`: Never prompt. Also set by RATICODE_NON_INTERACTIVE=1, CI=true or no terminal.

### Raticode completion

*Offline: works without a backend.* Print the shell completion script for Raticode.

- `SHELL` (required): bash, zsh, fish or powershell.

### Raticode doctor

*Offline: works without a backend.* Inspect local Raticode readiness without making network calls.

- `--workflow` / `-w` `STR`: Workflow ID, file stem, or TOML path to include in diagnostics.
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode licenses

*Offline: works without a backend.* Export the third-party notices bundled with this build.

- `--output` `PATH` (required): New directory for licenses and source archives

### Raticode schema

*Offline: works without a backend.* Print where the machine-readable Rattish language contracts are installed.

- `--format` `STR`: Output format: json.
- `--legacy-toml`: Print the legacy TOML authoring contract instead of the Rattish pointers.
- `--operation` `STR`: Legacy TOML: the contract for one operation type.
- `--capability` `STR`: Legacy TOML: one model capability schema.
- `--command` `STR`: Legacy TOML: structured help for a mutation command, e.g. workflow.add-node.

### Raticode serve

*Offline: works without a backend.* Run the Raticode backend API. Add --headless for servers and containers.

- `--host` `STR`: API bind host. Headless mode accepts only loopback addresses.
- `--port` `INT`: API bind port. Use 0 to let the OS choose a free port.
- `--data-dir` `PATH`: Raticode app data directory for global settings, schedules, registries, and app state. Defaults to GOFER_DATA_DIR or the standard data folder.
- `--headless`: Run with no desktop window: listen on loopback only, write the owner token and endpoint to owner-only files in &#60;data&#62;/headless/, and load Rem's settings from a file.
- `--token-file` `PATH`: Headless: read the owner token from this owner-only file (mode 600) instead.
- `--rem-settings` `PATH`: Headless: Rem settings JSON. Defaults to &#60;data&#62;/headless/rem-settings.json.

### Raticode wait

*Backend: needs a running Raticode backend.* Poll a GET endpoint until a field reaches a terminal state.

- `PATH` (required): GET path to poll, such as /rem/runs/RUN_ID.
- `--field` `STR`: Dotted path of the state in the response, such as run.state.
- `--success` `STR` (repeatable): State(s) that mean done (comma-separated or repeated).
- `--failure` `STR` (repeatable): State(s) that mean failed: exit 1.
- `--needs-user` `STR` (repeatable): State(s) that wait on you: exit 7.
- `--raw-field` / `-f` `STR` (repeatable): key=value query parameter.
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode agent

&#91;Legacy TOML&#93; Manage and run agents in TOML workflows. Rattish declares agents as nodes.

### Raticode agent create

*Legacy: older TOML workflows.* Create a new agent and save it to the data directory.

- `--name` `STR`: Human-readable agent name
- `--subscription` `STR`: Subscription (claude_code, codex, cursor, copilot, opencode, antigravity, grok, openai_api, anthropic_api)
- `--profile` `STR`: Provider profile name
- `--model` `STR`: Provider model override
- `--working-dir` `PATH`: Agent working directory
- `--prompt` `STR`: Prompt text or path to a prompt file
- `--tools` `STR`: Comma-separated tool names
- `--mcp-servers` `STR`: Comma-separated MCP server names
- `--extra-path` `PATH` (repeatable): Additional path to grant the provider sandbox (repeatable)
- `--env` `STR` (repeatable): Environment variables as KEY=VALUE (repeatable)

### Raticode agent edit

*Legacy: older TOML workflows.* Edit an existing agent's configuration.

- `AGENT_ID` (required): Agent ID to edit
- `--subscription` `STR`: Subscription (claude_code, codex, cursor, copilot, opencode, antigravity, grok, openai_api, anthropic_api)
- `--profile` `STR`: Provider profile name
- `--model` `STR`: Provider model override
- `--working-dir` `PATH`: Agent working directory
- `--prompt` `STR`: Prompt text or path to a prompt file
- `--tools` `STR`: Comma-separated tool names
- `--mcp-servers` `STR`: Comma-separated MCP server names
- `--extra-path` `PATH` (repeatable): Replace additional provider sandbox paths (repeatable)
- `--env` `STR` (repeatable): Environment variables as KEY=VALUE (repeatable)

### Raticode agent list

*Legacy: older TOML workflows.* List agents. Without --workflow, lists all agents in the data directory.

- `--workflow` `STR`: Filter by workflow ID
- `--format` `STR`: Output format: text or json.

### Raticode agent rm

*Legacy: older TOML workflows.* Remove an agent from its workflow.

- `AGENT_ID` (required): Agent ID to remove
- `--yes` / `-y`: Skip confirmation

### Raticode agent run

*Legacy: older TOML workflows.* Run a named agent.

- `AGENT_ID` (required): Agent ID (e.g. TradeAgent)

## Raticode brain

Second Brain through the running backend: list, search, read, save and projects.

### Raticode brain documents

*Backend: needs a running Raticode backend.* List a brain's indexed documents.

- `--brain` `STR` (required): Brain ID from `brain list`.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode brain list

*Backend: needs a running Raticode backend.* List registered brains and whether their folders are reachable.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode brain projects

*Backend: needs a running Raticode backend.* List the projects a brain's notes and reports belong to.

- `--brain` `STR` (required): Brain ID from `brain list`.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode brain read

*Backend: needs a running Raticode backend.* Print a document's original text. PDFs come back base64-encoded in JSON.

- `DOCUMENT_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode brain save

*Backend: needs a running Raticode backend.* Save text into a brain's Inbox as a new note and index it.

- `--brain` `STR` (required): Brain ID from `brain list`.
- `--title` `STR` (required)
- `--file` / `-f` `PATH`: Text to save; - for stdin. Defaults to --text.
- `--text` `STR`: Text to save.
- `--url` `STR`: Where the text came from; recorded, not fetched.
- `--wait` / `--no-wait`: Wait until it's indexed.
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode brain search

*Backend: needs a running Raticode backend.* Search notes and documents. Each hit names a document and a block.

- `QUERY` (required)
- `--brain` `STR` (required) (repeatable): Brain ID; repeat to search several.
- `--mode` `STR`: exact (words), semantic (meaning) or hybrid.
- `--limit` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode devices

Paired devices (read only; pairing stays in the app).

### Raticode devices list

*Backend: needs a running Raticode backend.* List paired phones and computers and when each was last seen.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode org

Organizations through the running backend: read, configure, employees, work schedule, tickets, projects and knowledge. Start, pause and consent decisions stay in the desktop app.

### Raticode org apply

*Backend: needs a running Raticode backend.* Replace an organization's configuration with a file, or create one.

- `--file` / `-f` `PATH` (required): File from `org config`; - for stdin.
- `--reason` `STR`
- `--expected-revision` `INT`: Overrides the revision recorded in the file.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org config

*Backend: needs a running Raticode backend.* Write an organization's configuration file for editing and `org apply`.

- `ORGANIZATION_ID` (required)
- `--file` / `-f` `PATH`: File to write; default the envelope's data.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org employees hold

*Backend: needs a running Raticode backend.* Hold a worker so it takes no new tickets.

- `ORGANIZATION_ID` (required)
- `EMPLOYEE_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org employees list

*Backend: needs a running Raticode backend.* List workers with their role, provider and whether they're held.

- `ORGANIZATION_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org employees release

*Backend: needs a running Raticode backend.* Release a worker's hold. Workers without a sandbox are released in the app.

- `ORGANIZATION_ID` (required)
- `EMPLOYEE_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org events

*Backend: needs a running Raticode backend.* Activity entries. Text output is one JSON entry per line.

- `ORGANIZATION_ID` (required)
- `--after` `INT`: Only entries after this sequence.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org export

*Backend: needs a running Raticode backend.* Export the portable organization package (Agent Companies format) as a ZIP.

- `ORGANIZATION_ID` (required)
- `--file` / `-f` `PATH` (required): ZIP file to write.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org history

*Backend: needs a running Raticode backend.* Configuration revisions, newest first.

- `ORGANIZATION_ID` (required)
- `--limit` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org import

*Backend: needs a running Raticode backend.* Import an organization package. It arrives paused, with every worker held.

- `PACKAGE` (required): Package ZIP from `org export`.
- `--preview`: Check it without creating anything.
- `--reason` `STR`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org knowledge read

*Backend: needs a running Raticode backend.* Print one knowledge entry.

- `ORGANIZATION_ID` (required)
- `ENTRY_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org knowledge save

*Backend: needs a running Raticode backend.* Create or update a knowledge entry.

- `ORGANIZATION_ID` (required)
- `--file` / `-f` `PATH` (required): JSON entry: &#123;id?, kind, title, summary, body, topic, projectId, links&#125;; - for stdin.
- `--expected-version` `INT`: When updating.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org knowledge search

*Backend: needs a running Raticode backend.* Search shared organization knowledge.

- `ORGANIZATION_ID` (required)
- `QUERY` (optional)
- `--limit` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org list

*Backend: needs a running Raticode backend.* List organizations with their state and open tickets.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org projects

*Backend: needs a running Raticode backend.* List the organization's project folders and its projects (initiatives).

- `ORGANIZATION_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org schedule apply

*Backend: needs a running Raticode backend.* Replace the work schedule and its time zone. Stale revisions exit 4.

- `ORGANIZATION_ID` (required)
- `--file` / `-f` `PATH` (required): JSON from `org schedule show --format json` (its data).
- `--reason` `STR`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org schedule show

*Backend: needs a running Raticode backend.* Show the work schedule blocks, time zone and the revision to apply against.

- `ORGANIZATION_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org show

*Backend: needs a running Raticode backend.* Show an organization's configuration, state and runtime.

- `ORGANIZATION_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org tickets changes

*Backend: needs a running Raticode backend.* Show the code changes a ticket's runs made.

- `ORGANIZATION_ID` (required)
- `TICKET_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org tickets comment

*Backend: needs a running Raticode backend.* Add a comment to a ticket.

- `ORGANIZATION_ID` (required)
- `TICKET_ID` (required)
- `--body` `STR` (required)
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org tickets create

*Backend: needs a running Raticode backend.* Create a ticket. It starts in backlog unless you pass --status todo.

- `ORGANIZATION_ID` (required)
- `--title` `STR` (required)
- `--description` `STR`
- `--assignee` `STR`: Worker ID.
- `--project` `STR`: Project (initiative) ID.
- `--status` `STR`: backlog (default) stays parked; todo lets a running organization pick it up.
- `--priority` `STR`
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org tickets list

*Backend: needs a running Raticode backend.* List and search tickets.

- `ORGANIZATION_ID` (required)
- `--status` `STR`: backlog, todo, in_progress, ...
- `--assignee` `STR`: Worker ID.
- `--project` `STR`: Project (initiative) ID.
- `--query` `STR`: Words in the title or description.
- `--limit` `INT`
- `--offset` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode org tickets update

*Backend: needs a running Raticode backend.* Change ticket fields: title, description, assignee, status, priority and more.

- `ORGANIZATION_ID` (required)
- `TICKET_ID` (required)
- `--expected-revision` `INT` (required): The ticket revision you read; stale ones exit 4.
- `--set` `STR` (required) (repeatable): Field change as name=value, such as status=todo. Repeatable.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode projects

Projects the backend knows about (read only).

### Raticode projects list

*Backend: needs a running Raticode backend.* List project folders that hold registered workflows.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode provider

Manage provider settings

### Raticode provider list

*Backend: needs a running Raticode backend.* List providers: installed, enabled, version and default model.

- `--refresh`: Look for provider CLIs again.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode provider models

*Backend: needs a running Raticode backend.* List a provider's models and effort levels, and which models are blocked.

- `PROVIDER` (required): Provider ID from `provider list`.
- `--refresh`: Ask the provider CLI again.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode provider profile create

*Offline: works without a backend.* Create a provider profile in the data folder.

- `NAME` (required): Profile name
- `--subscription` `STR`: CLI or API subscription, such as codex, antigravity, grok, or openai_api
- `--model` `STR`: Provider model
- `--timeout` `FLOAT`: Default timeout in seconds
- `--effort` / `--reasoning` `STR`: Model effort setting (--reasoning is deprecated)
- `--approval-mode` `STR`: Provider approval mode
- `--sandbox-mode` `STR`: Provider sandbox mode
- `--extra-arg` `STR` (repeatable): Additional provider CLI argument (repeatable)
- `--tool` `STR` (repeatable): Default tool (repeatable)
- `--mcp-server` `STR` (repeatable): Default MCP server (repeatable)
- `--env` `STR` (repeatable): KEY=VALUE env var (repeatable)
- `--unsafe-env` `STR` (repeatable): Explicit local-only plaintext sensitive env var (repeatable)
- `--secret-ref` `STR` (repeatable): KEY=SECRET_NAME secret env reference (repeatable)
- `--api-base-url` `STR`: Direct API base URL
- `--api-key-env` `STR`: Direct API key env var
- `--api-key-secret` `STR`: Direct API key secret name
- `--organization` `STR`: Provider organization

### Raticode provider profile edit

*Offline: works without a backend.* Change a provider profile. Options you give replace the stored values.

- `NAME` (required): Profile name
- `--model` `STR`: Provider model
- `--timeout` `FLOAT`: Default timeout in seconds
- `--effort` / `--reasoning` `STR`: Model effort setting (--reasoning is deprecated)
- `--approval-mode` `STR`: Provider approval mode
- `--sandbox-mode` `STR`: Provider sandbox mode
- `--extra-arg` `STR` (repeatable): Replace extra args
- `--tool` `STR` (repeatable): Replace default tools
- `--mcp-server` `STR` (repeatable): Replace MCP servers
- `--env` `STR` (repeatable): Replace env vars
- `--unsafe-env` `STR` (repeatable): Replace or add explicit local-only plaintext sensitive env vars
- `--secret-ref` `STR` (repeatable): Replace secret env references
- `--api-base-url` `STR`: Direct API base URL
- `--api-key-env` `STR`: Direct API key env var
- `--api-key-secret` `STR`: Direct API key secret name
- `--organization` `STR`: Provider organization

### Raticode provider profile list

*Offline: works without a backend.* List provider profiles. JSON names secrets and env variables, never their values.

- `--format` `STR`: Output format: text or json.

### Raticode provider profile rm

*Offline: works without a backend.* Remove a provider profile. Asks first unless --yes is given.

- `NAME` (required): Profile name
- `--yes` / `-y`: Skip confirmation

### Raticode provider set

*Backend: needs a running Raticode backend.* Change a provider's settings, such as its default model.

- `PROVIDER` (required)
- `VALUES` (required, repeatable): name=value pairs: enabled, executable, defaultModel, defaultEffort, deniedModels (a JSON list).
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode provider status

*Backend: needs a running Raticode backend.* Show whether a provider is signed in. Signing in happens in the app.

- `PROVIDER` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode rattish

Write, check, compile and run Rattish workflows. Commands marked (Backend) start, stop, inspect and approve runs through a running backend.

### Raticode rattish approvals

*Backend: needs a running Raticode backend.* (Backend) List a workflow's approval-gate requests.

- `WORKFLOW_ID` (required)
- `--pending`: Only approvals still waiting.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish approve

*Backend: needs a running Raticode backend.* (Backend) Approve an approval-gate node so the run continues.

- `WORKFLOW_ID` (required)
- `RUN_ID` (required)
- `NODE_ID` (required): The approval node's ID.
- `--notes` `STR`: Recorded with the decision.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish check

*Offline: works without a backend.* Compile a Rattish file and report language errors without running it.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory used for internal artifacts.

### Raticode rattish compile

*Offline: works without a backend.* Compile and publish validated IR to Raticode's internal artifact cache.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory used for internal artifacts.

### Raticode rattish create

*Offline: works without a backend.* Create and register a workflow in PROJECT/.raticode/.

- `NAME` (required): Human-readable workflow name.
- `--project` `PATH`: Repository project folder that will own the workflow.
- `--format` `STR`: Output format: text or json.
- `--registry-dir` `PATH`: Override the app directory used for workflow registration.

### Raticode rattish docs

*Offline: works without a backend.* Print paths to the installed Rattish documentation and node contracts.

- `--format` `STR`: Output format: text or json.

### Raticode rattish events

*Backend: needs a running Raticode backend.* (Backend) Print a run's node events. Text output is one JSON event per line.

- `WORKFLOW_ID` (required)
- `RUN_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish export

*Backend: needs a running Raticode backend.* (Backend) Export a workflow as a .raticode bundle file.

- `WORKFLOW_ID` (required)
- `--file` / `-f` `PATH` (required): Where to write the .raticode bundle.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish format

*Offline: works without a backend.* Canonicalize Rattish spelling and layout while preserving comments.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--check`: Report whether formatting is needed without writing the file.
- `--stdout`: Write formatted source to stdout.

### Raticode rattish import

*Backend: needs a running Raticode backend.* (Backend) Import (apply) a .raticode bundle into a project and register it.

- `BUNDLE` (required): A .raticode bundle file.
- `--project` `PATH` (required): Project folder for the workflow.
- `--preview`: Check the bundle without importing.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish inspect-ir

*Offline: works without a backend.* Print validated internal IR without creating a source-side IR file.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--pretty`: Indent the emitted JSON.
- `--data-dir` `PATH`: Override the app data directory used for internal artifacts.

### Raticode rattish list

*Offline: works without a backend.* List registered Rattish workflows grouped by repository project.

- `--format` `STR`: Output format: text or json.
- `--registry-dir` `PATH`: Override the app directory used for workflow registration.

### Raticode rattish logs

*Backend: needs a running Raticode backend.* (Backend) Print one run's log.

- `WORKFLOW_ID` (required)
- `RUN_ID` (optional): Run ID; defaults to the latest run.
- `--tail-bytes` `INT`: Only the end of the log.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish preflight

*Offline: works without a backend.* Compile a Rattish file and check whether this environment can run it.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory used for internal artifacts.

### Raticode rattish reject

*Backend: needs a running Raticode backend.* (Backend) Reject an approval-gate node.

- `WORKFLOW_ID` (required)
- `RUN_ID` (required)
- `NODE_ID` (required): The approval node's ID.
- `--notes` `STR`: Recorded with the decision.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish resume

*Backend: needs a running Raticode backend.* (Backend) Resume a stopped or failed run. It runs nodes again.

- `WORKFLOW_ID` (required)
- `RUN_ID` (required): The run to resume.
- `--from-node` `STR`
- `--only-node` `STR`
- `--force`: Resume even if the source changed.
- `--wait`: Wait for the resumed run to finish.
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish run

*Offline: works without a backend.* Compile, preflight, run, and persist a Rattish workflow.

- `SOURCE` (required): Path to a .rattish workflow file.
- `--input` `STR` (repeatable): Workflow input as NAME=JSON. Repeat for multiple inputs.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory used for runs and artifacts.

### Raticode rattish runs

*Backend: needs a running Raticode backend.* (Backend) List a workflow's runs, newest first.

- `WORKFLOW_ID` (required)
- `--status` `STR`: success, error, running or stopped.
- `--limit` `INT`
- `--offset` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish show

*Backend: needs a running Raticode backend.* (Backend) Show one workflow: name, status, inputs and where its source is.

- `WORKFLOW_ID` (required): Workflow ID from `Raticode rattish list`.
- `--source`: Include the workflow.rattish source.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish start

*Backend: needs a running Raticode backend.* (Backend) Start a run in the backend; `rattish run` runs one in this process instead.

- `WORKFLOW_ID` (required): Workflow ID from `Raticode rattish list`.
- `--input` `STR` (repeatable): Workflow input as name=value. Values that parse as JSON keep their type.
- `--inputs-file` `PATH`: JSON object of inputs; - reads standard input.
- `--dry-run`: Show the plan without running anything.
- `--wait`: Wait for the run to finish.
- `--follow`: Wait, printing run events as NDJSON lines while it runs.
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--expected-revision` `STR`: Refuse to run if the source changed since this revision.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish stop

*Backend: needs a running Raticode backend.* (Backend) Stop a workflow's active run.

- `WORKFLOW_ID` (required)
- `--run` `STR`: Stop this run only.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish triggers add cron

*Offline: works without a backend.* Run a workflow on a cron schedule.

- `SOURCE` (required): Path to a workflow.rattish file.
- `--cron` `STR` (required): Five fields: minute hour day month weekday.
- `--timezone` `STR`: IANA time zone. Defaults to this computer's zone.
- `--overlap` `STR`: If the workflow is already running: skip, queue (one run after it), or parallel.
- `--missed` `STR`: When a run is missed: record it, or run_once to also catch up one run.
- `--label` `STR`: A short name for this trigger.
- `--input` `STR` (repeatable): Workflow input as NAME=JSON. Repeat for multiple inputs.
- `--disabled`: Save the trigger turned off.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers add file

*Offline: works without a backend.* Run a workflow when files change, after they go quiet.

- `SOURCE` (required): Path to a workflow.rattish file.
- `--path` `STR` (required): File or folder to watch. Relative paths start at the project.
- `--debounce` `FLOAT`: Seconds the files must stay quiet before the workflow starts.
- `--glob` `STR`: Only react to files matching this pattern.
- `--recursive` / `--no-recursive`: Include subfolders of a watched folder.
- `--overlap` `STR`: If the workflow is already running: skip, queue (one run after it), or parallel.
- `--label` `STR`: A short name for this trigger.
- `--input` `STR` (repeatable): Workflow input as NAME=JSON. Repeat for multiple inputs.
- `--disabled`: Save the trigger turned off.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers add webhook

*Offline: works without a backend.* Run a workflow from an authenticated webhook on this computer.

- `SOURCE` (required): Path to a workflow.rattish file.
- `--overlap` `STR`: If the workflow is already running: skip, queue (one run after it), or parallel.
- `--label` `STR`: A short name for this trigger.
- `--input` `STR` (repeatable): Workflow input as NAME=JSON. Repeat for multiple inputs.
- `--disabled`: Save the trigger turned off.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers apply

*Backend: needs a running Raticode backend.* (Backend) Make a workflow's triggers match a file. Triggers match by kind and label.

- `WORKFLOW_ID` (required)
- `--file` / `-f` `PATH` (required): Trigger file from `triggers export`; - for stdin.
- `--prune`: Also remove triggers that aren't in the file.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish triggers disable

*Offline: works without a backend.* Turn a trigger off without deleting it.

- `TRIGGER_ID` (required): Trigger ID.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers enable

*Offline: works without a backend.* Turn a trigger on.

- `TRIGGER_ID` (required): Trigger ID.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers export

*Backend: needs a running Raticode backend.* (Backend) Write a workflow's triggers as a JSON file, without IDs or webhook tokens.

- `WORKFLOW_ID` (required)
- `--file` / `-f` `PATH`: File to write; default the envelope's data.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rattish triggers history

*Offline: works without a backend.* Show recent runs, including failed, missed and skipped ones.

- `TRIGGER_ID` (optional): Only this trigger.
- `--limit` `INT RANGE`: How many entries to show.
- `--problems`: Only failed, missed, skipped or interrupted runs.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers list

*Offline: works without a backend.* List triggers and whether anything is running them.

- `SOURCE` (optional): Only this workflow.rattish file.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers login

*Offline: works without a backend.* Start the scheduler at login, so triggers still fire after a reboot.

- `STATE` (optional): on, off, or status.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers remove

*Offline: works without a backend.* Delete a trigger and its history.

- `TRIGGER_ID` (required): Trigger ID.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers rotate-token

*Offline: works without a backend.* Replace a webhook's token. The old token stops working at once.

- `TRIGGER_ID` (required): Webhook trigger ID.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers start

*Offline: works without a backend.* Run triggers in this process until it's stopped.

- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers status

*Offline: works without a backend.* Show whether anything is running triggers on this computer.

- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers update

*Offline: works without a backend.* Change a trigger. Options you leave out stay the same.

- `TRIGGER_ID` (required): Trigger ID.
- `--cron` `STR`: New cron expression.
- `--timezone` `STR`: New IANA time zone.
- `--path` `STR`: New file or folder to watch.
- `--debounce` `FLOAT`: New quiet time in seconds.
- `--glob` `STR`: New file pattern.
- `--recursive` / `--no-recursive`: Include subfolders.
- `--overlap` `STR`: skip, queue, or parallel.
- `--missed` `STR`: record or run_once.
- `--label` `STR`: New name.
- `--input` `STR` (repeatable): Workflow input as NAME=JSON. Repeat for multiple inputs.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish triggers webhook-port

*Offline: works without a backend.* Show or change the local port webhooks listen on.

- `PORT` (optional): New port. Leave out to show the current one.
- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Override the app data directory.

### Raticode rattish wait

*Backend: needs a running Raticode backend.* (Backend) Wait for a run to finish or stop at an approval.

- `WORKFLOW_ID` (required)
- `RUN_ID` (required)
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--follow`: Print run events as NDJSON lines.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode rem

Rem from the command line. Chat, tasks, memories, alerts and settings go through the running backend; skill and mcp manage the library Rem threads can use.

### Raticode rem follow

*Backend: needs a running Raticode backend.* Print new chat messages as they arrive, one JSON line each, until stopped.

- `--after` `INT RANGE`: Start after this position; default is now
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem mcp add

*Offline: works without a backend.* Add an MCP server that any provider can use in Rem threads.

- `NAME` (required): Letters, digits, hyphens, or underscores
- `COMMAND` (optional, repeatable): Local program and arguments, after --
- `--url` `STR`: HTTP or HTTPS MCP endpoint
- `--description` / `-d` `STR`: What the server provides
- `--replace`: Update an existing server

### Raticode rem mcp list

*Offline: works without a backend.* List MCP servers in the Rem library.

- `--format` `STR`: Output format: table (or text) or json

### Raticode rem mcp remove

*Offline: works without a backend.* Remove an MCP server from the Rem library.

- `NAME` (required)

### Raticode rem memories add

*Backend: needs a running Raticode backend.* Remember a fact. Text that looks like a password or key is refused.

- `TEXT` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem memories edit

*Backend: needs a running Raticode backend.* Correct one remembered fact.

- `MEMORY_ID` (required)
- `TEXT` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem memories forget

*Backend: needs a running Raticode backend.* Forget one remembered fact, including in the notes on replies that saved it.

- `MEMORY_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem memories list

*Backend: needs a running Raticode backend.* List what Rem remembers, newest first, and whether memory is on.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem memories mode

*Backend: needs a running Raticode backend.* Turn memory on, pause new saves, or turn it off.

- `MODE` (required): on, paused or off
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem memories undo

*Backend: needs a running Raticode backend.* Reverse the memory changes one Rem reply made.

- `MESSAGE_ID` (required): The Rem reply whose memory changes to undo
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem messages

*Backend: needs a running Raticode backend.* Read Rem's chat. Pass the answer's last position as --after to read only new messages.

- `--tail` / `-n` `INT RANGE`: The newest N messages
- `--after` `INT RANGE`: Messages after this position (seq) instead
- `--limit` `INT RANGE`: At most this many, with --after
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem notices

*Backend: needs a running Raticode backend.* Alerts for anything that needs you: stuck or finished tasks, proposals, organizations.

- `--cursor` `STR`: The cursor from the last read; without it, only the cursor
- `--include` `STR`: Comma list of needs_you, finished, proposals (default: all)
- `--watch`: Keep reading and print each alert
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem resources

*Offline: works without a backend.* List every skill and MCP server available to Rem threads.

- `--format` `STR`: Output format: table (or text) or json

### Raticode rem runs acknowledge

*Backend: needs a running Raticode backend.* Mark a finished or blocked run as seen.

- `RUN_ID` (required)
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem runs cancel

*Backend: needs a running Raticode backend.* Stop a run that is going.

- `RUN_ID` (required)
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem runs retry-review

*Backend: needs a running Raticode backend.* Retry a run's interrupted independent review.

- `RUN_ID` (required)
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem runs show

*Backend: needs a running Raticode backend.* Show one run: state, result, criteria and whether it needs you.

- `RUN_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem say

*Backend: needs a running Raticode backend.* Send Rem a chat message, as if typed in the app.

- `TEXT` (optional): The message, or - to read it from stdin
- `--file` `PATH`: Read the text from this file
- `--refers-to` `STR`: ID of an earlier chat message this one replies to
- `--wait`: Wait for Rem's reply and print it
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem search

*Backend: needs a running Raticode backend.* Search Rem's chat for words. No model reads the query.

- `QUERY` (required): Words to find in the whole saved chat
- `--before` `INT`: Continue from this position
- `--limit` `INT RANGE`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem settings apply

*Backend: needs a running Raticode backend.* Replace Rem's settings with a file from `rem settings export`.

- `--file` / `-f` `PATH` (required): JSON settings file, or - for stdin
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem settings export

*Backend: needs a running Raticode backend.* Write Rem's settings as a JSON file that `rem settings apply` accepts.

- `--file` / `-f` `PATH`: Write here instead of stdout
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem settings show

*Backend: needs a running Raticode backend.* Show Rem's settings: providers, models, projects, resources and board.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem skill add

*Offline: works without a backend.* Register an existing skill folder from anywhere on this computer.

- `PATH` (required): Folder containing SKILL.md

### Raticode rem skill list

*Offline: works without a backend.* List registered and discovered skills.

- `--format` `STR`: Output format: table (or text) or json

### Raticode rem skill new

*Offline: works without a backend.* Create a skill in Raticode's skills folder.

- `NAME` (required): Folder and skill name, such as release-notes
- `--description` / `-d` `STR` (required): When Rem should use this skill
- `--body-file` `PATH`: Markdown file with the skill instructions

### Raticode rem skill path

*Offline: works without a backend.* Print Raticode's skills folder. Each child folder with a SKILL.md is a skill.

### Raticode rem skill remove

*Offline: works without a backend.* Unregister a skill folder. Files are not deleted.

- `PATH` (required): Registered skill folder

### Raticode rem tasks create

*Backend: needs a running Raticode backend.* Create a Rem task. With no schedule it runs once, now; with --after, once those succeed.

- `--title` `STR` (required)
- `--type` `STR` (required): research, maintain, develop, errand or reminder
- `--prompt` `STR`: What Rem should do, or - for stdin
- `--prompt-file` `PATH`
- `--project` `PATH`: Project folder the work happens in
- `--schedule` `STR`: JSON schedule, such as &#123;"kind": "daily", "local_time": "09:00", "timezone": "UTC"&#125;
- `--after` `STR` (repeatable): Start only after this task or run succeeds. Repeat for several; one-time tasks only
- `--spec` `PATH`: JSON task fields (provider, model, acceptance_criteria...)
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks delete

*Backend: needs a running Raticode backend.* Delete a task and its schedule, stopping a run that is going. Needs --yes.

- `TASK_ID` (required)
- `--yes` / `-y`: Confirm destructive actions.
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks edit

*Backend: needs a running Raticode backend.* Change a task. Refused with exit 4 if it changed since --expected-revision.

- `TASK_ID` (required)
- `--expected-revision` `INT` (required): The task's revision from `show`
- `--title` `STR`
- `--type` `STR`: research, maintain, develop, errand or reminder
- `--prompt` `STR`: What Rem should do, or - for stdin
- `--prompt-file` `PATH`
- `--project` `PATH`: Project folder the work happens in
- `--schedule` `STR`: JSON schedule, such as &#123;"kind": "daily", "local_time": "09:00", "timezone": "UTC"&#125;
- `--after` `STR` (repeatable): Start only after this task or run succeeds. Repeat for several; one-time tasks only
- `--clear-after`: Stop waiting for other tasks and start as scheduled
- `--future-only`: Leave a run already going as it is
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks list

*Backend: needs a running Raticode backend.* List Rem's tasks with their latest run.

- `--needs-you`: Only tasks waiting on you
- `--active`: Only tasks with a run going
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks pause

*Backend: needs a running Raticode backend.* Pause a task's schedule.

- `TASK_ID` (required)
- `--cancel-current`: Also stop a run going
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks resume

*Backend: needs a running Raticode backend.* Resume a paused task, as the app's Resume button does.

- `TASK_ID` (required)
- `--expected-revision` `INT` (required): The task's revision from `show`
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks run

*Backend: needs a running Raticode backend.* Start a task now. With --wait, exits 0 succeeded, 7 needs you, 1 failed, 8 timeout.

- `TASK_ID` (required)
- `--wait`: Wait for the run to finish
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks show

*Backend: needs a running Raticode backend.* Show one task, its revision and recent runs.

- `TASK_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem tasks wait

*Backend: needs a running Raticode backend.* Wait for a task's current run. Exits 0 succeeded, 7 needs you, 1 failed, 8 timeout.

- `TASK_ID` (required)
- `--timeout` `STR`: Longest to wait, such as 30s, 5m or 1h30m.
- `--interval` `FLOAT RANGE`: Seconds between polls.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem thread send

*Backend: needs a running Raticode backend.* Give a task's worker more instructions, as the app's thread input does.

- `THREAD_ID` (required)
- `TEXT` (optional): Instructions, or - for stdin
- `--file` `PATH`: Read the text from this file
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode rem thread show

*Backend: needs a running Raticode backend.* Show a Rem task's work thread: messages, results and status.

- `THREAD_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode runner

&#91;Legacy TOML&#93; Manage remote runners and queued workflow runs. The queue loads TOML workflows only.

### Raticode runner cancel

*Legacy: older TOML workflows.* Cancel queued or running work.

- `RUN_ID` (required): Queued run ID

### Raticode runner list

*Legacy: older TOML workflows.* List known runners.

- `--format` `STR`: Output format: text or json.

### Raticode runner queue

*Legacy: older TOML workflows.* Queue a workflow run for a separate runner process.

- `WORKFLOW` (required): Workflow ID or TOML path
- `--priority` `INT`: Higher priority runs first
- `--trigger` `STR`: Requested trigger name
- `--label` `STR` (repeatable): Target runner label
- `--param` `STR` (repeatable): Run parameter as KEY=JSON_VALUE or KEY=VALUE

### Raticode runner register

*Legacy: older TOML workflows.* Register or update a local runner record.

- `--name` `STR`: Runner display name
- `--id` `STR`: Stable runner ID
- `--label` `STR` (repeatable): Runner label
- `--provider-cli` `STR` (repeatable): Available provider CLI name
- `--workspace-root` `PATH` (repeatable): Workspace root available to the runner

### Raticode runner start

*Legacy: older TOML workflows.* Start a local runner process that executes queued workflows.

- `--name` `STR`: Runner display name
- `--id` `STR`: Stable runner ID
- `--label` `STR` (repeatable): Runner label
- `--provider-cli` `STR` (repeatable): Available provider CLI name
- `--once`: Process at most one queued run
- `--poll-seconds` `FLOAT RANGE`

### Raticode runner status

*Legacy: older TOML workflows.* Inspect queued and running work. One run always prints as JSON.

- `RUN_ID` (optional): Queued run ID
- `--limit` `INT RANGE`: Maximum runs to list
- `--format` `STR`: Output format: text or json.

## Raticode schedule

&#91;Legacy TOML&#93; Manage workflow schedules. For Rattish, use Raticode rattish triggers add cron.

### Raticode schedule add

*Legacy: older TOML workflows.* Add a workflow to the schedule.

- `WORKFLOW_FILE` (required): Workflow TOML file
- `--db` `PATH`

### Raticode schedule list

*Legacy: older TOML workflows.* List all scheduled workflows.

- `--db` `PATH`
- `--format` `STR`: Output format: text or json.

### Raticode schedule remove

*Legacy: older TOML workflows.* Remove a workflow from the schedule.

- `WORKFLOW_ID` (required)
- `--db` `PATH`

### Raticode schedule start

*Legacy: older TOML workflows.* Start the scheduler (background by default).

- `--foreground` / `-f`: Run in the foreground
- `--db` `PATH`

### Raticode schedule stop

*Legacy: older TOML workflows.* Stop the background scheduler.

## Raticode selfwork

Rem's self-work: status, report and settings. Needs a running backend.

### Raticode selfwork apply

*Backend: needs a running Raticode backend.* Apply self-work settings from a file. Without --expected-revision it replaces them.

- `--file` / `-f` `PATH` (required): JSON from `selfwork export`, or -
- `--expected-revision` `INT`: Refuse if the settings changed since this revision
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode selfwork export

*Backend: needs a running Raticode backend.* Print the self-work settings as a JSON file for `selfwork apply`.

- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode selfwork report

*Backend: needs a running Raticode backend.* Show the running report: tickets added and work done since you last looked.

- `--mark-seen`: Mark it read on every device
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode selfwork set

*Backend: needs a running Raticode backend.* Change self-work settings. Self-work spends provider allowance while nobody watches.

- `--expected-revision` `INT` (required): The settings revision from `selfwork status`
- `--enabled` / `--disabled`
- `--max-threads` `INT RANGE`
- `--auto-pull-backlog` / `--no-auto-pull-backlog`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode selfwork status

*Backend: needs a running Raticode backend.* Show whether self-work is on, what it's doing and its settings.

- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode swarm

Swarms through the running backend: list, show, history, and configuration as files. Starting and controlling swarms stays in the desktop app.

### Raticode swarm apply

*Backend: needs a running Raticode backend.* Update a swarm from a file, or create it when the file has no swarmId.

- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--file` / `-f` `PATH` (required): File from `swarm export`; - for stdin.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode swarm delete

*Backend: needs a running Raticode backend.* Delete a stopped swarm and its run records. Its git worktrees and branches stay.

- `SWARM_ID` (required)
- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--yes` / `-y`: Confirm destructive actions.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode swarm export

*Backend: needs a running Raticode backend.* Write a swarm's configuration as a JSON file for `swarm apply`.

- `SWARM_ID` (required)
- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--file` / `-f` `PATH`: File to write; default the envelope's data.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode swarm history

*Backend: needs a running Raticode backend.* List a swarm's past runs (20 at a time), or show one.

- `SWARM_ID` (required)
- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--run` `STR`: Show one past run in full.
- `--offset` `INT`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode swarm list

*Backend: needs a running Raticode backend.* List a project's swarms.

- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode swarm show

*Backend: needs a running Raticode backend.* Show a swarm's configuration and current run.

- `SWARM_ID` (required)
- `--project` `PATH` (required): The project folder the swarm belongs to.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode threads

Follow, steer or stop a live thread turn by its IDs. The thread list itself lives in the desktop window, so listing and starting threads isn't available.

### Raticode threads follow

*Backend: needs a running Raticode backend.* Stream a turn's events until it ends: one NDJSON event per line, then the result.

- `CONVERSATION_ID` (required): The thread's conversation ID.
- `TURN_ID` (required): The turn to follow.
- `--after` `INT`: Skip events up to this sequence number.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode threads steer

*Backend: needs a running Raticode backend.* Send a message to a turn that is still running. Rem's own tasks use `rem` commands.

- `CONVERSATION_ID` (required)
- `TURN_ID` (required)
- `--text` `STR` (required): What to tell the running turn.
- `--request-id` `STR`: Retrying with the same ID never sends it twice.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode threads steering

*Backend: needs a running Raticode backend.* List the steering messages sent to a thread and whether each was delivered.

- `CONVERSATION_ID` (required)
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode threads stop

*Backend: needs a running Raticode backend.* Stop a thread's running turn.

- `CONVERSATION_ID` (required)
- `TURN_ID` (required): The running turn.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode tickets

Global Kanban tickets: Rem's and every organization's. Needs a running backend.

### Raticode tickets approve

*Backend: needs a running Raticode backend.* Approve a Rem proposal, which moves it to To do.

- `TICKET` (required)
- `--expected-revision` `INT` (required): The ticket's revision from `show`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets assign

*Backend: needs a running Raticode backend.* Assign an organization's ticket to a worker, or hand a Rem ticket to an organization.

- `TICKET` (required)
- `--expected-revision` `INT` (required): The ticket's revision from `show`
- `--assignee` `STR`: A worker ID in the ticket's organization, or rem
- `--to-org` `STR`: Hand a Rem ticket to this organization
- `--org` `STR`: Organization ID, for an organization's ticket
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets comment

*Backend: needs a running Raticode backend.* Add a comment to a ticket's timeline.

- `TICKET` (required)
- `TEXT` (optional): The comment, or - for stdin
- `--file` `PATH`
- `--org` `STR`: Organization ID, for an organization's ticket
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets comments

*Backend: needs a running Raticode backend.* List a ticket's comments and status changes, oldest first.

- `TICKET` (required)
- `--org` `STR`: Organization ID, for an organization's ticket
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets create

*Backend: needs a running Raticode backend.* Create a ticket for Rem, or with --org for an organization.

- `--title` `STR` (required)
- `--description` `STR`: Text, or - for stdin
- `--description-file` `PATH`
- `--priority` `STR`: low, medium, high or critical
- `--status` `STR`: backlog or todo
- `--type` `STR`: research, maintain, develop or errand
- `--project` `PATH`: Open project folder
- `--done-when` `STR`: How to tell it's finished
- `--org` `STR`: Organization ID, for an organization's ticket
- `--assignee` `STR`: rem, or a worker ID with --org
- `--request-id` `STR`: Idempotency key sent as request_id. Only Rem messages, tasks, run controls and thread messages, and board ticket create and comment, deduplicate on it: reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates one and prints it in the result.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets decline

*Backend: needs a running Raticode backend.* Decline a Rem proposal, which cancels it.

- `TICKET` (required)
- `--expected-revision` `INT` (required): The ticket's revision from `show`
- `--reason` `STR`: Why, so Rem doesn't propose it again
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets edit

*Backend: needs a running Raticode backend.* Change a ticket's title, description, priority, type, project or finish line.

- `TICKET` (required)
- `--expected-revision` `INT` (required): The ticket's revision from `show`
- `--title` `STR`
- `--description` `STR`: Text, or - for stdin
- `--description-file` `PATH`
- `--priority` `STR`
- `--type` `STR`
- `--project` `PATH`
- `--done-when` `STR`
- `--org` `STR`: Organization ID, for an organization's ticket
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets list

*Backend: needs a running Raticode backend.* List tickets: open ones first, then the most recently closed.

- `--status` `STR` (repeatable): Repeat for several
- `--scope` `STR`: rem or organization
- `--org` `STR`: Organization ID, for an organization's ticket
- `--assignee` `STR`: rem or a worker ID
- `--project` `PATH`: Only this project folder
- `--proposals`: Only proposals awaiting you
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets move

*Backend: needs a running Raticode backend.* Move a ticket. in_progress starts Rem on it now; done is for you to decide.

- `TICKET` (required)
- `COLUMN` (required): backlog, todo, in_progress, done or cancelled
- `--expected-revision` `INT` (required): The ticket's revision from `show`
- `--org` `STR`: Organization ID, for an organization's ticket
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets projects

*Backend: needs a running Raticode backend.* Set the projects a participant may take tickets from in self-work.

- `PATHS` (required, repeatable): Open project folders
- `--expected-revision` `INT` (required): The participant's revision from `tickets list`
- `--participant` `STR`: rem, or agent:&#60;org&#62;:&#60;worker&#62; from `tickets list`
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode tickets show

*Backend: needs a running Raticode backend.* Show a ticket with its comments and status history.

- `TICKET` (required): Ticket ID or key, such as REM-12
- `--org` `STR`: Organization ID, for an organization's ticket
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode tokens

Manage agent tokens: limited API tokens for scripts and agents. They can't approve consent requests or proposals, change Rem's settings, pair devices or read the owner token.

### Raticode tokens create

*Offline: works without a backend.* Create an agent token and write it to a new owner-only file. It is never printed.

- `NAME` (required): Token name, such as ci or nightly-script
- `--output` / `-o` `PATH` (required): New file that receives the token, readable only by you (mode 600).
- `--expires-days` `INT RANGE`: Stop accepting the token after this many days.
- `--data-dir` `PATH`: Raticode data directory

### Raticode tokens list

*Offline: works without a backend.* List agent tokens by name. Token values are never stored or shown.

- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Raticode data directory

### Raticode tokens revoke

*Offline: works without a backend.* Revoke an agent token. A running backend stops accepting it immediately.

- `NAME` (required): Token name
- `--data-dir` `PATH`: Raticode data directory

## Raticode ui

Run the workflow studio API

### Raticode ui serve

*Offline: works without a backend.* Serve JSON endpoints used by the React workflow studio (same as Raticode serve).

- `--host` `STR`: API bind host. Headless mode accepts only loopback addresses.
- `--port` `INT`: API bind port. Use 0 to let the OS choose a free port.
- `--data-dir` `PATH`: Raticode app data directory for global settings, schedules, registries, and app state. Defaults to GOFER_DATA_DIR or the standard data folder.
- `--headless`: Run with no desktop window: listen on loopback only, write the owner token and endpoint to owner-only files in &#60;data&#62;/headless/, and load Rem's settings from a file.
- `--token-file` `PATH`: Headless: read the owner token from this owner-only file (mode 600) instead.
- `--rem-settings` `PATH`: Headless: Rem settings JSON. Defaults to &#60;data&#62;/headless/rem-settings.json.

## Raticode usage

Provider usage and limits the backend has recorded.

### Raticode usage refresh

*Backend: needs a running Raticode backend.* Ask each signed-in provider for current usage, then show it.

- `--days` `INT`: 1, 7, 30 or 90.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

### Raticode usage show

*Backend: needs a running Raticode backend.* Show recorded usage and limits per provider account. Asks no provider.

- `--days` `INT`: 1, 7, 30 or 90.
- `--output` / `-o` / `--format` `json` / `ndjson` / `text`: json: one envelope on stdout. ndjson: one object per line. text: for people.
- `--endpoint` `STR`: Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. Defaults to RATICODE_URL, RATICODE_SOCKET, then &#60;data&#62;/headless/endpoint.json.
- `--token-file` `PATH`: File holding the API token (mode 600). Defaults to RATICODE_TOKEN, RATICODE_TOKEN_FILE, then &#60;data&#62;/headless/owner-token (not in Rem worker sessions).
- `--data-dir` `PATH`: Raticode data folder to discover the backend from. Defaults to RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.

## Raticode watch

&#91;Legacy TOML&#93; Run workflow file/folder watchers. For Rattish, use Raticode rattish triggers add file.

### Raticode watch list

*Legacy: older TOML workflows.* List workflows in the data directory that have file/folder watchers.

- `--format` `STR`: Output format: text or json.
- `--data-dir` `PATH`: Raticode data directory

### Raticode watch start

*Legacy: older TOML workflows.* Run file/folder watchers in the foreground.

- `--data-dir` `PATH`: Raticode data directory
- `--poll-interval` `FLOAT RANGE`: Seconds between filesystem scans.

## Raticode workflow

Run and inspect workflows. run, resume, list, plan, validate, logs, usage, export, import, rm, rename and duplicate accept Rattish workflows; commands marked &#91;Legacy TOML&#93; work only with TOML workflows. Author Rattish with Raticode rattish.

### Raticode workflow add-agent

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Add or replace an agent config in a workflow.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--id` `STR` (required): Agent ID
- `--subscription` `STR` (required): codex, claude_code, openai_api, or anthropic_api
- `--working-dir` `PATH` (required): Agent working directory
- `--prompt-path` `PATH` (required): Prompt markdown path
- `--tool` `STR` (repeatable): Allowed tool name
- `--mcp-server` `STR` (repeatable): MCP server name
- `--extra-path` `PATH` (repeatable): Additional path to grant the provider sandbox (repeatable)
- `--env` `STR` (repeatable): Environment KEY=VALUE

### Raticode workflow add-edge

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Add or replace a workflow edge.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--from` `STR` (required): Source node ID
- `--to` `STR` (required): Target node ID
- `--condition` `STR`: Edge condition
- `--output-pattern` `STR`: Regex for output_matches condition
- `--field` `STR`: Structured result field for output_field condition
- `--operator` `STR`: Typed predicate operator for output_field condition
- `--value` `STR`: JSON comparison value for output_field condition

### Raticode workflow add-node

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Add or replace a workflow node.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--id` `STR` (required): Node ID
- `--type` `STR` (required): Node operation type
- `--command` `STR`: Command for command nodes
- `--script-path` `PATH`: Script path for script nodes
- `--path` `PATH`: Path for read/write/delete/file/folder nodes
- `--source-path` `PATH`: Source path for copy/move nodes
- `--destination-path` `PATH`: Destination path for copy/move nodes
- `--target` `STR`: Target for open_resource
- `--resource-type` `STR`: auto, file, folder, url, or app
- `--content` `STR`: Content for write_file nodes
- `--message` `STR`: Message for pass/fail nodes
- `--template` `STR`: Inline prompt template
- `--template-path` `PATH`: Prompt template file path
- `--output-path` `PATH`: Output path for prompt/index nodes
- `--working-dir` `PATH`: Working directory for command/agent nodes
- `--agent-id` `STR`: Agent ID for agent nodes
- `--prompt-path` `PATH`: Prompt path for agent nodes
- `--skill-name` `STR`: Skill name for agent skill invocation
- `--profile` `STR`: Provider profile for agent/common LLM nodes
- `--model` `STR`: Provider model override for agent/common LLM nodes
- `--provider-timeout` `FLOAT RANGE`: Provider CLI timeout override for agent/common LLM nodes
- `--task` `STR`: Common LLM task preset
- `--task-target` / `--query` `STR`: Task target text or search query
- `--instructions` `STR`: Task instructions
- `--index-path` `PATH`: Local vector index path
- `--vector-mode` `STR`: local_vectorize mode: incremental, full, validate, or compact
- `--top-k` `INT RANGE`: local_search result count
- `--score-threshold` `FLOAT`: Minimum local_search score to include
- `--include-snippets` / `--no-include-snippets`: Include snippets in local_search results
- `--include-file-metadata` / `--no-include-file-metadata`: Include file metadata in local_search results
- `--dynamic-count` `STR`: Agent dynamic count
- `--memory` `STR`: Agent memory mode: none, run, or all
- `--input-map` `STR` (repeatable): Agent input mapping KEY=CTX_PATH
- `--input` `STR` (repeatable): Common node input KEY=CTX_PATH. Use stdin=SOURCE or env.NAME=SOURCE for command nodes.
- `--input-mapping-json` `STR`: Agent input mapping JSON object
- `--output-schema` `STR`: Named output schema or inline JSON Schema object for agent/common LLM nodes
- `--repair-attempts` `INT RANGE`: Structured-output repair attempts for agent/common LLM nodes
- `--variable` `STR` (repeatable): Prompt template variable KEY=CTX_PATH_OR_VALUE
- `--env` `STR` (repeatable): Environment KEY=VALUE
- `--arg` `STR` (repeatable): Script/open-resource arg
- `--create-dirs` / `--no-create-dirs`
- `--overwrite` / `--no-overwrite`: Override file overwrite behavior for write/copy/move nodes
- `--append`: Append in write_file nodes
- `--trash` / `--no-trash`
- `--recursive`
- `--missing-ok`
- `--encoding` `STR`
- `--errors` `STR`
- `--pipe-output`
- `--allow-failure`: Allow this node to fail without failing the overall workflow
- `--await-all-inputs` / `--no-await-all-inputs`: Wait for all upstream nodes before this node runs
- `--retry-count` `INT RANGE`
- `--retry-delay-seconds` `FLOAT RANGE`
- `--timeout-seconds` `FLOAT RANGE`
- `--fan-source` `STR`: loop source: count, tabular, directory, trigger-events, or infinite
- `--fan-count` `STR`
- `--fan-path` `PATH`
- `--fan-glob` `STR`
- `--fan-include-content`
- `--fan-max-concurrency` `INT RANGE`
- `--fan-fail-fast`
- `--method` `STR`: HTTP method for http_request
- `--url` `STR`: URL for http_request
- `--header` `STR` (repeatable): HTTP header KEY=VALUE
- `--param` `STR` (repeatable): HTTP query parameter KEY=VALUE
- `--json-body` `STR`: HTTP JSON request body
- `--body` `STR`: HTTP raw request body
- `--http-timeout` `FLOAT RANGE`
- `--http-retry-attempts` `INT RANGE`
- `--http-retry-backoff` `FLOAT RANGE`
- `--http-retry-status` `INT` (repeatable): HTTP status that should be retried
- `--expected-status` `INT` (repeatable): Expected successful HTTP status
- `--response-mode` `STR`: auto, json, text, or none
- `--output-map` `STR` (repeatable): HTTP output mapping KEY=response.path
- `--secret-field` `STR` (repeatable): HTTP field/path to mask in logs and UI
- `--approval-timeout` `FLOAT`: Seconds before an approval gate times out
- `--timeout-decision` `STR`: Approval timeout output: timeout or reject
- `--approver` `STR` (repeatable): Allowed approver identity for approval_gate
- `--notify`: Send a desktop notification for approval_gate
- `--title` `STR`: Notification or approval title
- `--notification-body` `STR`: Notification body
- `--channel` `STR`: Notification channel
- `--urgency` `STR`: Notification urgency
- `--notification-webhook-url` `STR`: Slack, Teams, or generic webhook URL
- `--notification-header` `STR` (repeatable): Notification webhook header KEY=VALUE
- `--notification-payload-json` `STR`: Generic webhook JSON payload
- `--notification-email-from` `STR`: Email sender address
- `--notification-email-to` `STR` (repeatable): Email recipient address
- `--notification-smtp-host` `STR`: SMTP host for email notifications
- `--notification-smtp-port` `INT`: SMTP port for email notifications
- `--notification-smtp-username` `STR`: SMTP username or secret ref
- `--notification-smtp-password` `STR`: SMTP password as a secret reference such as &#123;&#123;secret.SMTP_PASSWORD&#125;&#125;, never the password itself
- `--notification-smtp-starttls` / `--no-notification-smtp-starttls`
- `--notification-timeout` `FLOAT`: Notification delivery timeout in seconds
- `--notification-retry-attempts` `INT`: Notification delivery attempts
- `--notification-retry-backoff` `FLOAT`: Seconds between notification retries
- `--notification-retry-status` `INT` (repeatable): Webhook HTTP status that should be retried
- `--notification-expected-status` `INT` (repeatable): Expected webhook success status
- `--notification-network-allowlist` `STR` (repeatable): Allowed webhook host or CIDR

### Raticode workflow approvals

*Legacy: older TOML workflows.* List approval gates.

- `--workflow` / `-w` `STR`: Only show pending approvals for this workflow ID
- `--all`: Show pending and completed approval decisions
- `--format` `STR`: Output format: text or json.

### Raticode workflow approve

*Legacy: older TOML workflows.* Approve a pending approval gate. Refused inside Rem and worker turns (exit 5).

- `RUN_ID` (required): Run ID from the pending approval
- `NODE_ID` (required): Approval node ID
- `--workflow` / `-w` `STR`: Workflow ID when run/node IDs are not unique
- `--by` `STR`: Decide as this approver when the gate names approvers. Must be one of the names the gate lists; it records which approver role decided, not who you are.
- `--notes` `STR`: Decision notes

### Raticode workflow build

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Interactively build a workflow via a guided wizard.

- `--output` `PATH`: Output path for workflow TOML

### Raticode workflow clear-schedule

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Remove a workflow cron schedule.

- `WORKFLOW` (required): Workflow ID or path to TOML file

### Raticode workflow clear-watch

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Remove workflow file/folder watcher settings.

- `WORKFLOW` (required): Workflow ID or path to TOML file

### Raticode workflow create

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Create a new workflow scaffold in the data directory.

- `--name` `STR`: Workflow name
- `--template` `STR`: Create from a built-in workflow template
- `--list-templates`: List built-in workflow templates
- `--preview-template`: Preview the selected template without creating files
- `--output` `PATH`: Output directory (default: data dir)

### Raticode workflow diff

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Show a revision diff against the current workflow.

- `WORKFLOW` (required): Workflow ID
- `REVISION_ID` (required): Revision ID to compare with current
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow duplicate

*Legacy: older TOML workflows.* Duplicate a workflow with a new ID and name.

- `WORKFLOW` (required): Workflow ID
- `--name` `STR`: Name for duplicate

### Raticode workflow edit

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Interactively edit a workflow's fields in the terminal.

- `WORKFLOW` (required): Workflow ID or path to TOML file

### Raticode workflow export

*Legacy: older TOML workflows.* Export a workflow and related local files into a portable bundle.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--output` / `-o` `PATH` (required): Bundle archive to create
- `--notes` `STR`: Optional bundle notes

### Raticode workflow history

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; List workflow revisions.

- `WORKFLOW` (required): Workflow ID
- `--limit` `INT RANGE`: Maximum revisions to show
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow import

*Legacy: older TOML workflows.* Import a workflow TOML file, a legacy bundle, or a Rattish .raticode archive.

- `SOURCE` (required): Workflow TOML file, legacy workflow bundle, or Rattish .raticode archive
- `--project` `PATH`: Project folder that receives a .raticode archive
- `--replace`: Replace an existing workflow
- `--dry-run`: Preview a bundle import without writing files

### Raticode workflow list

*Legacy: older TOML workflows.* List all workflows in the data directory.

- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow logs latest

*Legacy: older TOML workflows.* Print the latest run log for a workflow.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--path-only`: Print only the log path

### Raticode workflow logs list

*Legacy: older TOML workflows.* List run logs for a workflow.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--offset` `INT RANGE`: Number of runs to skip
- `--limit` `INT RANGE`: Maximum runs to list
- `--status` `STR`: Filter by status
- `--trigger` `STR`: Filter by trigger type
- `--search` `STR`: Search run metadata and log text
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow logs prune

*Legacy: older TOML workflows.* Preview or prune old workflow run logs and sidecar files.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--keep-last` `INT RANGE`: Always keep newest N runs
- `--keep-days` `INT RANGE`: Keep runs newer than N days
- `--keep-failed-days` `INT RANGE`: Keep failed runs newer than N days
- `--apply`: Delete matching runs instead of previewing
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow logs show

*Legacy: older TOML workflows.* Print a specific workflow run log.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `RUN_ID` (required): Run log file name from workflow logs list
- `--offset` `INT RANGE`: Read from byte offset
- `--limit` `INT RANGE`: Maximum bytes to read
- `--tail-bytes` `INT RANGE`: Read last N bytes

### Raticode workflow plan

*Legacy: older TOML workflows.* Preview execution order, fan-out, provider calls, and side effects.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.
- `--trigger-json` `STR`: Trigger context JSON object for trigger-event fan-out estimates
- `--param` `STR` (repeatable): Workflow parameter as KEY=VALUE. May be repeated.
- `--params-json` `STR`: Workflow parameters as a JSON object
- `--input` `STR` (repeatable): Workflow input as KEY=VALUE. May be repeated.
- `--inputs-json` `STR`: Workflow inputs as a JSON object

### Raticode workflow preview-import

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Preview a workflow bundle import without writing files.

- `SOURCE` (required): Workflow bundle to preview

### Raticode workflow recipe watch-folder-summarize

*Legacy: older TOML workflows.* Create a folder watcher workflow that fans changed files into an agent summarizer.

- `--name` `STR` (required): Workflow name
- `--watch-path` `PATH` (required): Folder to watch
- `--glob` `STR`: Changed file glob
- `--recursive`: Watch subdirectories
- `--provider` `STR`: codex, claude_code, openai_api, or anthropic_api
- `--working-dir` `PATH`: Agent working dir
- `--max-concurrency` `INT RANGE`: Agent fan-out concurrency
- `--debounce-seconds` `FLOAT RANGE`: Watcher debounce seconds
- `--output` `PATH`: Workflow TOML output path
- `--prompt-path` `PATH`: Prompt markdown path to create or reuse
- `--overwrite`: Overwrite existing files

### Raticode workflow reject

*Legacy: older TOML workflows.* Reject a pending approval gate. Refused inside Rem and worker turns (exit 5).

- `RUN_ID` (required): Run ID from the pending approval
- `NODE_ID` (required): Approval node ID
- `--workflow` / `-w` `STR`: Workflow ID when run/node IDs are not unique
- `--by` `STR`: Decide as this approver when the gate names approvers. Must be one of the names the gate lists; it records which approver role decided, not who you are.
- `--notes` `STR`: Decision notes

### Raticode workflow rename

*Legacy: older TOML workflows.* Rename a workflow and update its ID to match the new name.

- `WORKFLOW` (required): Workflow ID
- `--name` `STR` (required): New workflow name

### Raticode workflow restore

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Restore a workflow revision, validating before replacement.

- `WORKFLOW` (required): Workflow ID
- `REVISION_ID` (required): Revision ID to restore
- `--as-copy`: Restore as a new workflow

### Raticode workflow resume

*Legacy: older TOML workflows.* Resume a failed or stopped workflow run.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--run-id` `STR` (required): Run log file name to resume from
- `--from-node` `STR`: Rerun this node and downstream nodes, reusing successful upstream outputs
- `--only-node` `STR`: Rerun only this node after restoring successful dependency outputs
- `--skip-cache`: Ignore reusable node cache entries while resuming
- `--force`: Ignore saved outputs and run from the beginning
- `--verbose` / `-v`: Show each node's output

### Raticode workflow rm

*Legacy: older TOML workflows.* Delete a workflow. A Rattish workflow is unregistered and its folder removed.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--yes` / `-y`: Skip confirmation

### Raticode workflow rm-edge

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Remove a workflow edge.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--from` `STR` (required): Source node ID
- `--to` `STR` (required): Target node ID

### Raticode workflow rm-node

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Remove a workflow node and its edges.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--id` `STR` (required): Node ID

### Raticode workflow run

*Legacy: older TOML workflows.* Execute a workflow by name or file path.

- `WORKFLOW` (required): Workflow ID, .rattish file, workflow folder, or TOML file
- `--dry-run`: Simulate without executing
- `--verbose` / `-v`: Show each node's output
- `--trigger-json` `STR`: Trigger context JSON object used for dry-run planning
- `--param` `STR` (repeatable): Workflow parameter as KEY=VALUE. May be repeated.
- `--params-json` `STR`: Workflow parameters as a JSON object
- `--input` `STR` (repeatable): Workflow input as KEY=VALUE. May be repeated.
- `--inputs-json` `STR`: Workflow inputs as a JSON object

### Raticode workflow secrets

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Show required workflow secrets and whether they are available locally.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.

### Raticode workflow set-info

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Update top-level workflow settings without opening an editor.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--name` `STR`: Workflow display name
- `--max-total-node-runs` `INT RANGE`: Maximum node executions allowed in one workflow run
- `--run-continuously` / `--no-run-continuously`: Keep one workflow run active and ignore schedule/watch starts

### Raticode workflow set-schedule

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Add or replace a workflow cron schedule.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--cron` `STR` (required): Cron expression
- `--timezone` `STR`: Schedule timezone

### Raticode workflow set-watch

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Add or replace workflow file/folder watcher settings.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--path` `PATH` (required): File or folder to watch
- `--glob` `STR`: Glob pattern for watched paths
- `--recursive`: Watch subdirectories
- `--debounce-seconds` `FLOAT RANGE`: Seconds to debounce filesystem events
- `--mode` `STR`: batch, queue, or fanout
- `--max-concurrency` `INT RANGE`: Maximum concurrent queued watcher runs

### Raticode workflow show

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Display the graph structure of a workflow.

- `WORKFLOW` (required): Workflow ID or path to TOML file

### Raticode workflow stop

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Request that a running legacy TOML workflow stop.

- `WORKFLOW` (required): Workflow ID or path to TOML file

### Raticode workflow trigger

*Legacy: older TOML workflows.* &#91;Legacy TOML&#93; Trigger a workflow through its configured webhook/API trigger.

- `WORKFLOW` (required): Workflow ID to trigger
- `--trigger-id` `STR`: Webhook trigger ID
- `--payload-json` `STR`: JSON payload to pass as trigger.payload
- `--payload-file` `PATH`: File containing JSON payload to pass as trigger.payload
- `--header` / `-H` `STR` (repeatable): Trigger header as 'Name: value'; may be repeated
- `--source` `STR`: Trigger source metadata
- `--token-file` `PATH`: File holding the webhook token (mode 600), for workflows that require one. Without it, RATICODE_WEBHOOK_TOKEN is used.
- `--replay-run-id` `STR`: Replay the saved trigger payload from a prior run log id

### Raticode workflow usage

*Legacy: older TOML workflows.* Show LLM usage summaries for recent workflow runs.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.
- `--limit` `INT RANGE`: Number of recent runs to inspect

### Raticode workflow validate

*Legacy: older TOML workflows.* Validate a workflow by name or file path.

- `WORKFLOW` (required): Workflow ID or path to TOML file
- `--format` `STR`: Output format: text or json.
- `--json`: Same as --format json.
- `--explain-bindings`: Explain reference producers, types, coercion, and resolution phases
