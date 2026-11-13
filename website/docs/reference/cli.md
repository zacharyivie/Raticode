---
title: CLI reference
description: Use the Raticode CLI to author, validate, inspect, and deliberately run Rattish workflows.
pageClass: docs-layout
---

# CLI reference

The `Raticode` CLI uses the same workflow contracts as the desktop app. Use it for reviewable commands, machine-readable diagnostics, or to start automation from somewhere else.

## Find commands

```bash
Raticode --help
Raticode rattish --help
Raticode rattish docs --format json
```

- The [CLI command index](./cli-commands.md) lists every command and option, generated from the CLI itself.
- `Raticode rattish docs` prints where the installed language docs, grammar, node contracts and schemas are. They describe the installed compiler, so you don't need a source checkout. `Raticode schema` prints the same paths.

## Scripting conventions

- Commands that print records, such as lists and status, accept `--format json`. Older `--json` flags still work as a short form, and the `rem` lists also accept `--format table`.
- There are two JSON shapes:
  - Commands that work on files in the data folder, such as `rattish list` or `runner list`, print the bare data.
  - Commands that talk to a running backend print an envelope with the data under `data`. See [Output and exit codes](#output-and-exit-codes).
- Data goes to stdout. Errors and warnings go to stderr, so `Raticode … --format json | jq` never gets an error message as input.
- Exit codes: 0 is success and 1 is failure. 2 means the command was used wrongly: an unknown option or format, invalid input, or a Rattish workflow given to a command that only reads [legacy TOML workflows](#legacy-toml-commands).
- The data folder is `GOFER_DATA_DIR` when set (the desktop app sets it when you pick a custom folder). Otherwise it's the standard folder, such as `~/.local/share/gofer` on Linux. Most commands also take `--data-dir`.

## Rattish commands

| Command | Purpose | Executes nodes? |
| --- | --- | --- |
| `Raticode rattish create "Name" --project PATH --format json` | Register a workflow and return its ID and source path | No |
| `Raticode rattish list --format json` | List registered Rattish workflows | No |
| `Raticode rattish format PATH` | Format a source file in place | No |
| `Raticode rattish check PATH --format json` | Parse and run static checks | No |
| `Raticode rattish compile PATH --format json` | Compile validated source to JSON IR | No |
| `Raticode rattish inspect-ir PATH --pretty` | Print the compiled representation | No |
| `Raticode rattish preflight PATH --format json` | Check required runtime resources | No |
| `Raticode rattish run PATH --format json` | Execute the workflow | **Yes** |
| `Raticode rattish triggers add cron\|file\|webhook PATH ...` | Start the workflow on a schedule, file change, or local webhook. See [Triggers](../workflows/triggers.md) | Later, when it fires |

- `PATH` is the source file. For a registered workflow, use the exact path `create` returned.
- Run `--help` on a command for its current options. Most take `--data-dir`.
- Only `format` edits its target, and only `run` executes nodes. A run can write files, call providers and contact services, as the source says.

## Inputs and limits

```bash
Raticode rattish run WORKFLOW_PATH --input 'message="Hello"' --format json
```

- Values after `=` are JSON. Repeat `--input` for each public input.
- Check the input schema and workflow limits before running. The [inputs guide](../workflows/inputs.md) has a full example.
- In shell scripts, check the exit status and the JSON result. A printed message doesn't prove every node succeeded.
- A failed request whose external effect is uncertain may need checking before you retry.

## Provider profiles and health

```bash
Raticode provider --help
Raticode provider profile list --format json
Raticode provider profile create NAME --subscription codex --model MODEL
Raticode provider profile edit NAME --model MODEL
Raticode provider profile rm NAME
Raticode doctor --json
```

- Provider profiles are reusable deployment settings. `Raticode provider profile create --help` lists the supported subscription and permission options.
- `edit` replaces only the values you pass. `rm` asks first unless you add `--yes`.
- Sign in to native provider CLIs separately.
- A profile never overrides a Rattish node's explicit provider, model or effort.
- `doctor` checks installation health and reports whether the [Organizations Feature Preview](./headless.md#turn-on-organizations) is on in this environment. Its legacy workflow option doesn't replace `Raticode rattish preflight`.

## Other command families

| Family | Guide |
| --- | --- |
| `Raticode rattish triggers` | [Triggers](../workflows/triggers.md) |
| `Raticode schedule` | [Schedules](../workflows/schedules.md) |
| `Raticode watch` | [File watchers](../workflows/watchers.md) |
| `Raticode runner` | [Runner queue](../workflows/runner-queue.md) |
| `Raticode workflow run PATH` | Runs a `.rattish` file, a workflow folder or a registered ID. `--dry-run` runs preflight only. `Raticode workflow resume --run-id ID` resumes a run |
| `Raticode rem` | [Rem from the CLI](./cli-rem.md) for chat, tasks, memories, alerts and settings. [Tools, skills and MCP](../rem/resources.md#add-skills-and-mcp-servers-from-the-cli) for the skill and MCP library |
| `Raticode tickets`, `Raticode selfwork` | [Tickets and self-work from the CLI](./cli-rem.md#tickets) |
| `Raticode rattish start`, `stop`, `resume`, `runs`, `logs`, `approve` and the other "(Backend)" commands; `Raticode org`, `Raticode swarm`, `Raticode brain`, `Raticode usage`, `Raticode projects`, `Raticode devices`, `Raticode threads`; `Raticode provider list`, `models`, `status` and `set` | [Workflows, Organizations and more from the CLI](./cli-headless.md): runs, approvals and trigger files through the backend, Organizations, swarms, Second Brain, providers, usage and live threads, with settings as files |
| `Raticode schema` | Prints where the Rattish docs, contracts and schemas are installed. `--legacy-toml` prints the old TOML authoring contract |
| `Raticode serve --headless` | Runs the backend with no desktop window. See [Headless backend](./headless.md) |
| `Raticode tokens` | Creates, lists and revokes limited agent tokens. See [Agent tokens](./headless.md#agent-tokens) |
| `Raticode licenses --output DIR` | Exports the third-party notices bundled with this build |
| `Raticode ui serve` | Runs the backend the desktop app starts (port 8765 by default), the same as `Raticode serve`. It writes no token file and Rem waits for a window, so use `Raticode serve --headless` for scripts and servers |

## Legacy TOML commands

Raticode workflows used to be TOML files. Some commands still read only that format, and their help starts with `[Legacy TOML]`. New workflows belong in `workflow.rattish`.

### `workflow` commands that accept Rattish

These take a Rattish workflow ID, folder or `.rattish` file:

| Command | With a Rattish workflow |
| --- | --- |
| `run`, `resume` | Runs or resumes it, like `Raticode rattish run` |
| `list` | Lists Rattish and TOML workflows |
| `validate` | Runs `Raticode rattish check` |
| `plan` | Runs `Raticode rattish preflight` |
| `logs list`, `logs show`, `logs latest`, `logs prune`, `usage` | Reads its run history |
| `export` | Writes a `.raticode` archive. `import FILE.raticode --project PATH` installs one |
| `rm`, `rename`, `duplicate` | Works as in the app |

### Approval gates

`approvals`, `approve` and `reject` work with approval gates of legacy TOML workflows. For a Rattish workflow's gates, use `Raticode rattish approvals`, `approve` and `reject` through the [backend](./cli-headless.md#workflows).

- Only you can decide a gate. Inside a Rem or worker turn, `workflow approve` and `reject` refuse with exit 5 and change nothing, so a workflow can't approve its own gate.
- `--by` isn't your name. It picks which of the gate's listed approvers you decide as. It's refused when the gate lists no approvers or the name isn't on the list.
- Without `--by`, the decision is recorded as made from the CLI.

### Remove, rename and duplicate

`rm`, `rename` and `duplicate` also work with TOML workflows.

- When a [headless backend](./headless.md) is running for the same data folder, they ask it to make the change. Only the backend writes the workflow files, and it updates schedules and continuous runs right away.
- Inside a Rem or worker turn, that request needs an [agent token](./headless.md#agent-tokens). Without one it fails with exit 5 and the files stay unchanged.
- With no headless backend running, they change the files themselves, as before. The desktop app's own backend doesn't publish its address, so with only the app open they also change the files themselves.

### TOML-only commands

These `workflow` commands work only with TOML workflows: `show`, `edit`, `build`, `create`, `recipe`, `set-info`, `add-node`, `add-edge`, `add-agent`, `rm-node`, `rm-edge`, `set-schedule`, `clear-schedule`, `set-watch`, `clear-watch`, `trigger`, `secrets`, `stop`, `history`, `diff`, `restore`, `preview-import`.

Given a Rattish workflow, they change nothing, exit with code 2 and print the Rattish command to use instead, such as `Raticode rattish inspect-ir` for `show` or `Raticode rattish triggers add cron` for `set-schedule`.

The `Raticode agent`, `Raticode schedule`, `Raticode watch` and `Raticode runner` groups are legacy too. Use [triggers](../workflows/triggers.md) for schedules and file watching, and read the guides above before using the others.

| Given a Rattish workflow | Result |
| --- | --- |
| `agent list --workflow` | Exit 2, points to `Raticode rattish inspect-ir` |
| `runner queue` | Exit 2, points to `Raticode rattish run` or `Raticode rattish start` |
| `schedule add` | Exit 2, points to `Raticode rattish triggers add cron` |
| `watch` | Reads only TOML workflows in the data folder and ignores Rattish ones |

`agent list`, `watch list`, `runner list` and `workflow approvals` accept `--format json`.

### Secrets on the command line

Other accounts can read command lines, so:

- `Raticode workflow trigger` reads a webhook token from `--token-file PATH` (a file only you can read) or `RATICODE_WEBHOOK_TOKEN`. The old `--token` option still works but is deprecated and prints a warning.
- `workflow add-node --notification-smtp-password` expects a secret reference such as `secret:SMTP_PASSWORD`, and warns when given a password.

## Talk to a running backend

::: info New in 0.5.3
`Raticode api`, `Raticode wait` and `Raticode completion` arrived in Raticode 0.5.3. So did the resource commands built on them: [Rem, tickets and self-work](./cli-rem.md) and [workflows, Organizations and more](./cli-headless.md).
:::

The commands above work on files. Everything stateful lives in the backend: Rem, tickets and the global board, self-work, Organizations, swarms and run control. Scripts reach it through the backend's local API, the same one the desktop window and the phone use. The CLI never opens those stores itself.

The CLI finds a [headless backend](./headless.md) on its own. It can't find the desktop app's backend yet, because the app doesn't publish its address or token. With only the desktop app running, these commands can't reach it.

`Raticode api METHOD PATH` calls any endpoint, like `gh api`. Paths are relative to `/api`:

```bash
Raticode api GET /health
Raticode api GET /rem/kanban | jq '.data.self_work'
Raticode api POST /rem/kanban/ticket -f action=create -f title="Fix flaky test" --request-id t-123
Raticode api PUT /rem/kanban/settings -F max_threads=3 --expected-revision 0
Raticode api POST /organizations --data @configure.json --expected-revision 7
```

| Option | What it does |
| --- | --- |
| `-d, --data JSON` | Request body. `@file` reads a file and `@-` reads stdin. |
| `-f, --raw-field key=value` | A string field. A query parameter for `GET` and `DELETE`, otherwise a JSON body field. Dots nest: `params.taskId=t1`. |
| `-F, --field key=value` | Like `-f`, but `true`, `3`, `null` or `[...]` are sent as JSON values, and `@file` reads the value from a file. |
| `--request-id ID` | Idempotency key, sent as `request_id`. Only [some endpoints](#output-and-exit-codes) deduplicate on it: retrying with the same ID returns the first result instead of doing the work again. Others ignore it. `auto` makes one and prints it in the result. |
| `--expected-revision REV` | Refuse the change (exit 4) if the record changed since you read it. Sent as `expected_revision` on `/rem/...` paths and `expectedRevision` elsewhere (inside `params` for Organizations). |
| `--follow` | Stream an NDJSON endpoint such as `/chat/events`, or keep polling a `GET` that returns an `events` list such as `/rem/events`. |
| `--timeout 30s` | Give up after this long. With `--follow` it limits the whole follow. |
| `--raw` | Print the response body as it came, without the envelope. |

### Finding the backend and its token

The CLI only talks to a backend on this computer: `127.0.0.1`, `::1` or `localhost`. It refuses any other address. Raticode's backend listens only on TCP and can't listen on a Unix socket. To reach a backend on another machine, forward its port with `ssh -L` and point the CLI at the local end.

`api` and `wait` have no `--token` option, because other accounts can read command lines. They look for the backend and token in this order:

| | Looked up in order |
| --- | --- |
| Address | `--endpoint http://127.0.0.1:PORT`, then `RATICODE_URL`, then `<data folder>/headless/endpoint.json`, then `http://127.0.0.1:8765` |
| Token | `--token-file PATH`, then `RATICODE_TOKEN`, then `RATICODE_TOKEN_FILE`, then the token file named in `endpoint.json`, then `<data folder>/headless/owner-token` |
| Data folder | `--data-dir`, then `RATICODE_DATA_DIR`, then `GOFER_DATA_DIR`, then the standard folder |

File safety:

- A token file must belong to you and be readable only by you (`chmod 600`).
- `endpoint.json` must not be writable by anyone else, since it decides where the token goes.
- The CLI refuses either file otherwise. On Windows the CLI doesn't check these permissions; see [Headless backend](./headless.md#find-the-endpoint-and-token).

Inside a Rem worker or another provider turn that Raticode started (where `RATICODE_AGENT_SESSION` is set):

- The CLI never picks up the owner token from the data folder.
- It accepts only a limited [agent token](./headless.md#agent-tokens), from any source.
- An owner token in `RATICODE_TOKEN`, `RATICODE_TOKEN_FILE` or `--token-file` is refused with exit 5 (error code `owner_token_in_agent_session`).

Which backend you're talking to:

- [`Raticode serve --headless`](./headless.md) publishes `headless/endpoint.json` and `headless/owner-token` in the data folder, so the CLI finds it with no setup.
- For scripts that shouldn't act as you, pass a limited [agent token](./headless.md#agent-tokens) with `--token-file`.
- `Raticode ui serve` takes its token from `GOFER_UI_API_TOKEN`, or makes a new random one at each start and writes it nowhere. Start it with a token you keep in a private file, and give the CLI the same file:

```bash
umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/raticode-token
GOFER_UI_API_TOKEN="$(cat ~/.config/raticode-token)" Raticode ui serve &
Raticode api GET /rem/tasks --token-file ~/.config/raticode-token
```

You can export `RATICODE_TOKEN_FILE` in your shell instead of passing `--token-file`. Raticode removes `RATICODE_TOKEN` and `RATICODE_TOKEN_FILE` from everything it starts (Rem's workers, providers and workflow commands), so they don't inherit your token even if you start the app from that shell.

### Output and exit codes

Each command prints one JSON object on stdout. Messages for people go to stderr:

```json
{"ok": false, "command": "api", "data": null,
 "error": {"code": "conflict", "message": "This ticket changed. Check it and try again.", "http": 409},
 "warnings": [], "request_id": "t-123"}
```

- `request_id` appears only when the command sent one.
- With `--follow`, output is one JSON object per line: `{"kind": "event", "data": ...}` for each event, then the result object with `"kind": "result"` as the last line.
- `--output ndjson` gives that line format for any command, and `--output text` prints readable output. `--format` is another name for `--output`.

| Exit | Meaning |
| --- | --- |
| 0 | Done |
| 1 | The operation ran and failed, or the backend had an internal error |
| 2 | Invalid arguments or input (HTTP 400, 422) |
| 3 | Not found (HTTP 404) |
| 4 | Conflict or stale `--expected-revision` (HTTP 409) |
| 5 | Missing, rejected or unsafe token, or forbidden (HTTP 401, 403) |
| 6 | No backend answered |
| 7 | Waiting on you: an approval, consent or answer is pending |
| 8 | Timed out, or the request may or may not have taken effect |

Existing `rattish` commands keep their own codes, where 2 also means a source file couldn't be read.

These endpoints deduplicate on `request_id`:

- Rem: `POST /rem/messages`, `POST /rem/tasks`, `PATCH` and `DELETE /rem/tasks/ID`, `POST /rem/tasks/ID/run` and `/pause`, `POST /rem/runs/ID/cancel`, `/acknowledge` and `/retry_review`, and `POST /rem/threads/ID/messages`
- The board: `POST /rem/kanban/ticket` with `action` `create` or `comment`

Retries:

- A request that never reached the backend is always retried.
- After a timeout, a dropped connection or a busy backend, the CLI resends a sent request only if it's a read, or carries a request ID to one of the endpoints above.
- Everything else, such as starting a workflow run, is sent once. If the answer is lost, the command exits 8. Check whether it happened before trying again.
- On exit 8 from one of the endpoints above, retry with the same `--request-id`, not a new one.

### Waiting for a state

`Raticode wait PATH` polls a `GET` endpoint until a field reaches a final state:

```bash
Raticode wait /rem/runs/RUN_ID --field run.state \
  --success succeeded --failure failed,cancelled,outcome_unknown --needs-user blocked \
  --timeout 30m
```

| Exit | When |
| --- | --- |
| 0 | A `--success` state |
| 1 | A `--failure` state |
| 7 | A `--needs-user` state |
| 8 | The timeout passed first |

The last response is in `data`. If the backend restarts while you wait, `wait` keeps trying until the timeout.

### Scripts and shell completion

The CLI never prompts when:

- you pass `--non-interactive` (`Raticode --non-interactive ...`),
- `RATICODE_NON_INTERACTIVE=1` or `CI=true` is set, or
- stdin isn't a terminal.

A command that would ask for confirmation then fails with exit 2 unless you pass `--yes`.

`Raticode completion bash`, `zsh`, `fish` or `powershell` prints a completion script. It completes commands and options, and for `Raticode api` the method and the API paths with CLI coverage:

```bash
Raticode completion bash > ~/.local/share/bash-completion/completions/Raticode
Raticode completion zsh > "${fpath[1]}/_Raticode"
Raticode completion fish > ~/.config/fish/completions/Raticode.fish
```

`Raticode --install-completion` installs it for the current shell instead.
