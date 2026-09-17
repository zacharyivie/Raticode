# Provider transport supplement

M1b, research owner. Access date for every external source: 2026-09-16. Starting revision: `486c4ab2773f473a6619d7e225e2f1485e83b74d`. This supplement answers the Grok stream and five-provider MCP questions left by M1. It changes only this file. Findings describe observed documentation/source; paragraphs marked Recommendation are Raticode design proposals. No provider was installed or executed, no dependencies changed, and no external AI implementation was acquired or copied.

## Local baseline and scope

Read root AGENTS.md; `rg --files -g AGENTS.md` found no nested instructions. Starting Git status was clean. Inspected `pyproject.toml`, `subscriptions/base.py`, `ui/chat.py`, existing provider research and test file inventory. Rem command construction currently has separate Codex/Claude branches. Its trusted Swarm grant checks the actual server URL, and its Second Brain grant checks the executable and argument prefix. Preserve those identity checks for new adapters. MCP settings must reach both Rem/Swarms and subscription execution; adding argv to only one path is insufficient.

Existing `TemporaryDirectory` ownership in the process/chat paths is the natural place to retain generated configuration through process shutdown. Existing test areas are `test_ui_chat.py`, `test_rem_swarms.py`, `test_subscriptions.py` and provider capability/contract tests. No product baseline suite was rerun for this document. M2 and the board retain baseline evidence; user-reported host results do not establish this attempt's product test status. One exploratory lookup of `core/assistant_resources.py` failed because that path does not exist; no architecture claim depends on it.

M1b criteria are owned by research, depend on accepted M1, and gate affected M3 claims. The lead owns adoption decisions; backend owns implementation; test owns independent behavioral checks. Exclusions: redesigning orchestration, installing providers, modifying user configuration, changing dependencies, running workflows and importing external core AI code.

## Grok stream contract resolved through primary source

The official [headless page](https://docs.x.ai/build/cli/headless-scripting) documents `grok -p PROMPT --output-format streaming-json`, newline-delimited output, explicit `--resume ID`, and `--no-auto-update`. Its ACP example is a different protocol. It does not specify the headless event schema.

The official [grok-build repository](https://github.com/xai-org/grok-build) supplies that missing source. Its `main` is a periodically exported monorepo tree. [SOURCE_REV](https://raw.githubusercontent.com/xai-org/grok-build/main/SOURCE_REV) reported `be7ce6e8cffe46d20bef9834b211616082ee866b`. This is an observed monorepo revision, not a verified public Git commit URL or installed binary version. Mutable `main` URLs below are access-date evidence; they do not establish a minimum supported release.

The [native stream reducer](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/src/headless/reducer/acp.rs) defines these wire fields:

| `type` | Fields and meaning |
| --- | --- |
| `text`, `thought` | String `data`; assistant output and reasoning respectively. |
| `tool_call` | `toolCallId`, `title`, `kind`, `status`, `toolName`, `rawInput`, `content`, `locations`. |
| `tool_call_update` | `toolCallId`, `status`, `rawOutput`, `content`, `locations`. |
| `usage` | Optional `messageId`, `stopReason`, `usage`, `signature`; response boundary, not turn completion. |
| `plan`, `available_commands` | `entries`; or `tools` and `commands`. |
| `end` | `stopReason`, `sessionId`, `requestId`, with optional usage/structured output additions. |
| `error` | `message`, potentially usage additions. The error method emits an error line without requiring an `end`. |
| `max_turns_reached` | Limit marker. Lifecycle events also include compaction, image compression and memory flush. |

The [emitter](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/src/headless.rs) serializes compact JSON plus newline. `stop_reason_wire` maps `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`. Its JSON-only result has `text`, `stopReason`, `sessionId`, `requestId`; optional `thought` is separate. Some startup failures can occur before a session exists. Its MCP initialization metadata is built from configuration, with status hardcoded to connected; that metadata cannot prove an actual handshake.

The [headless guide](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md) lists exit 0 success, 1 error, 130 SIGINT and 143 SIGTERM. Its statement that end is always last should be read with the error implementation above. The older [shell README](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-shell/README.md) shows `EndTurn`, whereas the current emitter uses snake_case. This documentation inconsistency requires version-specific compatibility evidence.

Recommendation S1: implement a small native JSONL parser in Raticode, dispatching on `type`. Append only text `data` to the answer. Keep thought/tool/lifecycle output separate. Read session identity from terminal `sessionId`, never `requestId` or snake_case assumptions. Treat `error` as fatal even with exit zero; retain partial text separately. Handle EOF without completion as incomplete. Do not require `end` after error, and do not interpret per-response usage as completion. Record installed version/help before enabling legacy stop-reason aliases. Fixtures may now use these sourced field names with synthetic values, explicitly labeled synthetic rather than captured output.

## Per-run MCP configuration and isolation

Here, injection means adding the selected servers without persistent writes. Isolation means unselected servers cannot connect, including same-name replacement attacks. Tool approval is a third property. A temporary file alone proves neither isolation nor approval. Complete process isolation is outside this research scope.

### Cursor

Observed: the [CLI parameters](https://cursor.com/docs/cli/reference/parameters) list MCP discovery/login and `--approve-mcps`, which approves all servers. They do not document a per-run MCP file argument. [Configuration](https://prod.cursor.com/docs/cli/reference/configuration) places `cli-config.json` under `CURSOR_CONFIG_DIR`; it does not establish relocation of MCP config. [MCP documentation](https://cursor.com/docs/mcp) uses `.cursor/mcp.json` and `~/.cursor/mcp.json`, with `mcpServers` entries. Stdio uses `command`, `args`, `env`; remote HTTP/SSE uses `url`, `headers`. OAuth and `${env:NAME}` interpolation are documented. The general MCP page covers more than the CLI, so identical behavior across surfaces needs confirmation.

Operational evidence: [official support discussion](https://forum.cursor.com/t/cursor-agent-not-picking-up-mcp-json-from-project/157026) records project MCP ignored on `2026.03.30-a5d3e17`. Support confirmed the issue April 8 and requested a retest on `2026.04.17` April 28. This does not establish whether today's release is fixed. It contradicts treating a config-directory override as proven MCP injection.

Recommendation S2: leave isolated per-run MCP unverified for Cursor. Do not temporarily overwrite workspace/global config or claim `CURSOR_CONFIG_DIR/mcp.json` works from the CLI config documentation alone. A version-pinned disposable-profile probe is the next evidence needed. Broad `--approve-mcps` is inappropriate while ambient loading remains possible. Required Swarm tools cannot be silently dropped while presenting full support. Cleanup must never remove existing `.cursor` files.

### Copilot

Observed from the [command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference): `--additional-mcp-config=@ABSOLUTE_FILE` adds session configuration with top-level `mcpServers`. It wins same-name conflicts but merges other servers from plugins, trusted workspace files and user config. Remote entries require `type: "http"` or `"sse"`, `url`, `tools`; optional `headers` and OAuth settings are supported. Local entries use `command`, `args`, `tools`, optional `env`. `--disable-builtin-mcps` disables built-ins; repeatable `--disable-mcp-server=NAME` disables named servers. `COPILOT_HOME` relocates user state. Tool availability, approvals and enterprise allowlists are separate controls.

Recommendation S3: use a private generated JSON file and explicit tool lists, with a unique server name tied to the run. Injection is documented; strict isolation is not established by this flag. A temporary COPILOT_HOME also changes login/session storage and does not eliminate workspace or plugin sources. Do not copy OAuth stores wholesale. Enumerating and disabling ambient servers requires complete source discovery and a race policy; otherwise record merged behavior and keep strict-resource acceptance open. Delete the generated file only after the child exits. Retain any deliberately managed session store across steering restarts.

### Gemini

Observed from [configuration](https://geminicli.com/docs/reference/configuration/): `GEMINI_CLI_HOME=RUN_ROOT` puts user settings/storage in `RUN_ROOT/.gemini`. `--allowed-mcp-server-names` accepts a comma-separated session allowlist. System settings override user/workspace settings. `GEMINI_CLI_SYSTEM_SETTINGS_PATH` changes the system settings path. [MCP documentation](https://geminicli.com/docs/tools/mcp-server/) defines `mcpServers`, stdio `command/args/env`, SSE `url`, Streamable HTTP `httpUrl`, `headers`, OAuth, `includeTools`, `excludeTools` and `trust`. `trust: true` bypasses tool confirmations; server selection is independent.

The [v0.60.0 loader](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/settings.ts) merges trusted workspace settings after user settings, before system settings. It security-checks system files before loading them. The [settings tests](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/settings.test.ts) cover override precedence, environment path selection and rejecting insecure system files, including a not-root-owned example.

Recommendation S4: generate `RUN_ROOT/.gemini/settings.json` and use a session server-name allowlist. Verify selected endpoint identity after all configuration merges; a same-name project override must not inherit Raticode's trusted approval. Do not use a temporary system-settings override to bypass enterprise policy. A user-owned temporary system file may be ignored by the observed version, so it is not a reliable shortcut. Preserve auth/session continuity explicitly when relocating user state. Prefer a hyphenated generated server alias; the configuration reference warns that underscores can confuse policy name parsing. Remove only owned run files after exit; retain state required for continuation until the conversation ends.

### Grok

Observed: [settings reference](https://docs.x.ai/build/settings/reference) makes `GROK_HOME` the root for config, auth, sessions, plugins and logs. `GROK_CURSOR_MCPS_ENABLED=false` and `GROK_CLAUDE_MCPS_ENABLED=false` disable vendor scanners. [MCP documentation](https://docs.x.ai/build/features/mcp-servers) defines `[mcp_servers.NAME]` in `config.toml`, stdio `command/args/env`, remote `url/headers`, environment interpolation and OAuth. Project `.grok/config.toml` files load from cwd toward Git root and replace same-name user servers entirely.

The [source MCP guide](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/docs/user-guide/07-mcp-servers.md) also documents HTTP/SSE, `enabled`, native managed allowlists and OAuth credential persistence. The [loader](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-shell/src/util/config/mcp.rs) independently reads project `.mcp.json`. Its skip condition is a Claude-import marker; the vendor toggles alone are insufficient. Native project configuration remains active. Managed allowlist semantics are not a per-run strict-config flag.

Recommendation S5: temporary `GROK_HOME/config.toml` is a supported injection location, but full isolation remains unresolved. Do not forge import markers, replace enterprise requirements or claim two vendor toggles exclude every ambient server. Same-name project replacement can redirect a trusted endpoint. A dedicated profile changes authentication and session lookup; preserving browser login and continuation requires deliberate state ownership. Keep the real task cwd. ACP `session/new.mcpServers` is a possible alternative, but neither its merge semantics nor complete permissions lifecycle is verified here; it is not an approved replacement implementation. Cleanup must wait for process termination and respect sessions required by restart.

### OpenCode

Observed from [configuration](https://opencode.ai/docs/config/): `OPENCODE_CONFIG` names an extra file and `OPENCODE_CONFIG_CONTENT` supplies inline JSON at higher priority. Both merge; unrelated ambient keys survive. [MCP documentation](https://opencode.ai/docs/mcp-servers/) uses top-level `mcp`. Local entries have `type: "local"`, command arrays and `environment`; remote entries have `type: "remote"`, `url`, optional `headers`. `oauth: false` disables OAuth auto-detection for token-auth servers. Otherwise remote OAuth can persist credentials. `enabled: false` disables a server; tool controls are separate.

The [v1.18.31 loader](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.31/packages/opencode/src/config/config.ts) merges inline content late and supports `OPENCODE_DISABLE_PROJECT_CONFIG` for project file discovery. It still has other config sources and directory loading; that flag alone does not prove isolation. The [config tests](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.31/packages/opencode/test/config/config.test.ts) provide a maintained test target for config behavior. No upstream tests were run here.

Recommendation S6: inject a unique per-run MCP entry through inline JSON or an owned file, preserving existing provider credentials. For static-token tools, disable OAuth explicitly and pass headers through the child environment/config without logging them. Strict server isolation needs evidence for the effective merged set, including remote organizational config and plugins; an empty `mcp` object is not a documented deletion operator. Deleting the temporary config does not delete persistent OAuth tokens or sessions. Do not claim cleanup does so.

## Maturity, alternatives and remaining uncertainty

Gemini v0.60.0 and OpenCode v1.18.31 have inspectable config loaders and tests. M1 records their releases and operational issue evidence; this bounded supplement does not repeat a broad project survey. Their value here is showing how merge order, trust and persistence interact, not supplying implementation code.

Grok's public repository exposes native stream types and configuration loading. Its page reported 45 commits and periodic monorepo exports, while [GitHub releases](https://github.com/xai-org/grok-build/releases) listed no releases. That is limited public maintenance history, not evidence of an unmaintained product. It does not identify the binary release matching the observed source. Cursor's support thread supplies version-specific operational failure evidence; Copilot's reference supplies detailed configuration and policy contracts. This supplement does not establish public implementation-test coverage for Cursor/Copilot, independently verified adoption counts, or reliability from popularity.

Unavailable sources: xAI's web CLI reference returned reader errors twice. Guessed flat Grok reducer/config paths failed; actual module directories resolved those questions. Guessed Grok headless test paths also failed, so no Grok test-coverage claim is made. Current provider binaries, live authentication and real MCP handshakes were not tested. Strict MCP isolation remains an explicit capability gap, especially Cursor and Grok. Research completion does not close implementation AC2 while required resources are unavailable.

Recommendation S7: prefer existing Raticode subprocess/config ownership over persistent config edits or new proxy dependencies. Per-run additive config has low integration cost but admits ambient servers. Relocating provider state reduces user-file writes but affects authentication, sessions and plugins. ACP could provide session-scoped inputs but adds a bidirectional lifecycle and does not automatically solve ambient merging. Reject global/workspace file swapping, assumed strict flags, automatic credential copying and any import, wrapping, vendoring or copying of another AI project's core implementation. Raticode should own its adapter, transcript, cancellation and steering logic.

## Measurable adapter and acceptance checks

These are proposed checks, not executed product tests. Backend should add cases within its owned suites; test should independently exercise them in `tests/unit/test_provider_steering_acceptance.py`. Lead must record adopted/rejected recommendations before adapter acceptance.

| ID | Input/action | Expected result and verification |
| --- | --- | --- |
| S-AC1 | Feed sourced Grok text, thought, tool, usage, end and error shapes through arbitrary byte splits, including split UTF-8 and multiple lines per chunk. | Exact answer text once; separate tool/reasoning output; correct session ID; no completion on usage. Deterministic parser tests. |
| S-AC2 | Emit error with exit zero; emit partial text then EOF; terminate during a tool event; return cancelled/max-turn stop reason. | Fatal/incomplete/cancelled/limited state accurately distinguished; partial answer retained; no false success. Fake process tests. |
| S-AC3 | Run two fake turns concurrently with distinct URLs and tokens, then cancel one. | Each receives its own config, cwd and credentials; cancelling one preserves the other's files; all owned temporary files removed after final exit. Inspect argv/env/files, never log tokens. |
| S-AC4 | Put unselected and same-name hostile servers in user, project, parent-project and plugin sources. | A claimed isolated adapter starts none of the unselected servers and cannot redirect the trusted URL. Without a proven mechanism, preflight reports the capability gap and keeps AC2 open. Version-pinned loader/handshake evidence is required in addition to fake tests. |
| S-AC5 | Configure local stdio and remote HTTP resources with auth headers; deny an MCP tool; make required Swarm endpoint return 401 or fail handshake. | Correct transport and exact headers, denial honored, bounded actionable failure, no secret output or silently missing required tool. Use local doubles; no live model call. |
| S-AC6 | Steer during an active turn using a relocated profile; restart in same conversation; switch provider afterward. | Config lives until old process exits; successor retains intended session/auth context; provider switch never reuses another provider's session. Barrier-controlled process tests. |
| S-AC7 | Snapshot user/workspace config, deny config creation, force spawn failure and cancel during startup. | Original file bytes unchanged; no fallback to persistent mutation or broad permission; generated artifacts cleaned. Filesystem failure tests. |
| S-AC8 | Select each new provider in Rem/Swarm/Agent with required resources and an unsupported transport/permission combination. | Exact selection persists; explicit error preserves draft; no silent drop or unrestricted fallback. API tests plus rendered frontend flow. |

Suggested targeted command after confirming local imports: `python -m pytest tests/unit/test_subscriptions.py tests/unit/test_ui_chat.py tests/unit/test_rem_swarms.py tests/unit/test_provider_steering_acceptance.py`. Required broader checks remain owned by M3/M5 and integration. Fake tests validate Raticode behavior, not undocumented external CLI behavior. Any provider probe needs a recorded executable version, scrubbed evidence and no live LLM call unless separately authorized.

Artifact submission checks are `git diff --check` and `test -s docs/delivery/provider-transport-supplement.md`. Commit through the authorized Rem bridge, then run swarm verification on the committed artifact. This document does not claim a commit, verification or integration that has not occurred.
