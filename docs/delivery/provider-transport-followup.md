# Bounded transport follow-up

Research follow-up requested by backend and coordinator. Access date: 2026-09-16. Starting revision: `8c244e1be9cd03d51c5c5ec6ed600244080ae261`. The accepted M1b artifact remains unchanged. Observed facts and recommendations are distinguished below. No packages installed, product code edited, models executed, persistent provider configuration changed or credentials read/copied.

## Local evidence and scope

Read root AGENTS.md and mandatory writing skill. `rg --files -g AGENTS.md` found only root instructions. Starting `git status --short` was empty. Inspected `pyproject.toml`, accepted research, subscription boundaries, test inventory and `ui/chat.py` command construction. Existing Rem grants distinguish shell/web tools and verify the Swarm URL before granting its tool. They are not an OS sandbox guarantee. Recommendations below concern required tool identity and configured permissions; blanket ambient configuration isolation is excluded.

`command -v cursor-agent agent gemini grok copilot opencode` found no executable. Read-only file discovery under `~/.local/bin`, `~/.local/share/cursor-agent`, `/usr/local/bin`, `/opt`, `~/.cursor` and `~/.nvm` found no Cursor agent executable. `ls -l ~/.local/bin` confirmed the documented default `agent` path absent. This establishes discovery failure in this worker environment, not that the host has no installation. No installed CLI help/version test was possible. The next host input is an exact executable path, followed by bounded `PATH_TO_AGENT --version` and `PATH_TO_AGENT --help` checks. No provider invocation is reported as tested.

No product baseline suite was rerun for this documentation-only task. Existing M2 evidence remains separate. Failed source-path guesses are research lookup failures, not passing checks or product failures.

## Wire contracts

### Cursor

Observed [output documentation](https://cursor.com/docs/cli/reference/output-format): `--print --output-format stream-json` emits JSONL. Assistant text is `message.content[].text`; identity is `session_id`. Without partial streaming, assistant events are complete segments. Terminal `type=result`, `subtype=success`, `is_error=false` repeats the aggregate in `result`. Failure can terminate without result, with nonzero exit and stderr. With `--stream-partial-output`, only assistant events having `timestamp_ms` and lacking `model_call_id` contain new text; other flushes duplicate it.

Recommendation: initially omit partial streaming or implement its documented filter. Never append terminal aggregate to already emitted text. Test both modes, result-only success, early EOF and nonzero exit with partial text.

### OpenCode

Observed [v1.18.31 run command](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.31/packages/opencode/src/cli/cmd/run.ts): JSONL envelopes contain `type`, `timestamp`, `sessionID`. Completed text uses `type=text`, `part.text`; completed or failed tools use `tool_use` with `part`; step events use `step_start`/`step_finish` with `part`. Errors use `type=error`, `error.name`, optionally `error.data.message`. Internal session idle breaks the event loop but is not emitted as a JSON terminal event. A model step ending in tool use is not final completion.

Recommendation: combine process exit, parsed errors and step/text state. Do not require a nonexistent `result` event, or mark success at the first `step_finish`. Reject error events even if exit status is zero. Keep partial output when cancelled. Session identifiers use capital `ID` here.

### Copilot

Observed [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference): `--output-format=json` emits JSONL. It does not specify the complete stdout event schema. [Official issue 4107](https://github.com/github/copilot-cli/issues/4107) reports a terminal `result` with `usage`, but lacks a complete captured result/error/session envelope. It also reports missing token counts; do not manufacture usage.

[SDK event documentation](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/streaming-events) supplies `event.type` plus `event.data`: `assistant.message_delta` has `messageId`/`deltaContent`; `assistant.message` has `messageId`/`content`; `session.error` has `errorType`/`message`; `session.idle` may have `aborted`. Envelope `agentId` distinguishes subagents. These are documented SDK events, not proof that every event, especially ephemeral idle/deltas, is forwarded unchanged by CLI stdout. SDK idle and CLI terminal result must not be conflated.

Recommendation: these fields support explicitly labeled provisional fixtures, but the complete CLI terminal/error/session schema remains an evidence gap. Obtain a version-specific official CLI schema or scrubbed already-existing capture before claiming full compatibility. Do not import the SDK or another adapter. Avoid guessing Claude-style `is_error`, `subtype` or `session_id`. Deltas plus complete messages must not duplicate text. Do not parse session identity from localized stderr hints.

## Required MCP injection and permissions

### Cursor

[Parameters](https://cursor.com/docs/cli/reference/parameters) still document persistent project/global MCP discovery and approval, not an extra per-run MCP file. [Support reply](https://forum.cursor.com/t/cursor-agent-not-picking-up-mcp-json-from-project/157026/5) states `CURSOR_CONFIG_DIR` does not fix the reported project-MCP loading issue. This is version-specific failure evidence, not proof every later version fails.

[ACP documentation](https://cursor.com/docs/cli/acp) demonstrates `session/new` with an empty `mcpServers` list, while its MCP section describes project/user files. It does not establish nonempty per-session HTTP injection. ACP is therefore a candidate requiring a handshake probe, not a proven fix. Its permission and extension callbacks add implementation obligations.

[Permission documentation](https://cursor.com/docs/cli/reference/permissions) supports `Shell(...)`, `Read(...)`, `Write(...)`, `WebFetch(...)`, `Mcp(server:tool)` and deny precedence. Recommendation: these can express grants after server loading is proven. Do not replace the private Swarm endpoint with prompt instructions or treat `--approve-mcps` as injection. Required-tool injection remains blocked on executable/version evidence or a documented transport path. Normal unsupported combinations should fail explicitly while other providers proceed.

### Gemini

[Configuration](https://geminicli.com/docs/reference/configuration/) documents `--allowed-mcp-server-names` as a session allowlist. It selects existing definitions; it does not supply them. `GEMINI_CLI_HOME` relocates user storage. The [v0.60.0 loader](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/settings.ts) sends BOTH `GEMINI_CLI_SYSTEM_SETTINGS_PATH` and `GEMINI_CLI_SYSTEM_DEFAULTS_PATH` through `isFileAndDirectorySecureSync`; insecure files are skipped. User settings use `Storage.getGlobalSettingsPath()`.

Recommendation: use unpredictable hyphenated aliases and the allowlist when a supported injection path exists. This prevents accidental/pre-existing alias collision; it is not protection from a process that can read the alias and mutate configuration concurrently. Neither random names nor relocating home solves existing browser-login preservation. No ordinary-user per-run settings override preserving that login was established here. Do not claim temporary system-default files are a workaround. API-key authentication in an intentionally owned profile is a separate supported-auth design, not transparent browser-auth preservation.

[Policy engine](https://geminicli.com/docs/reference/policy-engine/) supports per-tool allow/deny/ask decisions and priority tiers. Supplemental `--admin-policy` differs from system settings: it is documented as exempt from strict ownership checks, but ignored when central system policy files exist. Recommendation: preserve central policy and report conflicts. Policy can constrain tools, but cannot inject MCP definitions.

### Grok

The [official changelog](https://x.ai/build/changelog) introduced `GROK_CONFIG` and `GROK_CONFIG_PATH` in 1.0.5, August 15. The [configuration guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/05-configuration.md) resolves their scope: JSON inline or JSON/TOML file overlays merge above user settings, below enforced requirements. They accept an allowlist of soft settings and discard other tables. They cannot add discovery sources, commands or redirected network traffic. Therefore these variables do not establish `mcp_servers` injection while retaining existing login.

[Permissions](https://docs.x.ai/build/features/permissions) documents per-invocation `--allow`/`--deny` with Bash, MCPTool, WebFetch and WebSearch filters; deny wins. Recommendation: assess these separately from MCP transport. Existing M1b `GROK_HOME` injection remains an owned-profile option with an authentication limitation, not transparent auth preservation. No config-copying workaround is recommended.

### Copilot and OpenCode

Copilot's cited CLI reference supports additive `--additional-mcp-config=@ABSOLUTE_FILE`, keeping its normal credential location. It supports `--allow-tool='ALIAS(swarm_action)'`, `--deny-tool=shell`, and `url` permission patterns; denials take precedence. Recommendation: use an unguessable alias, exact Raticode-validated URL and private config. Restrict approval to required tools. Ambient merging is documented; do not describe it as isolation or assume every external MCP tool is covered by shell/web categories.

[OpenCode permissions](https://opencode.ai/docs/permissions/) supports allow/ask/deny and ordered pattern matching, last matching rule winning. Existing M1b inline config injection preserves normal credentials. Recommendation: use a catch-all deny then exact approved tool patterns where required, and explicit `bash`, `webfetch`/`websearch` decisions. Verify final merge ordering; ambient more-specific rules must not defeat claimed restrictions. Generated remote entries should disable OAuth for Raticode token-auth endpoints. Tool denial is not a network sandbox.

## Maturity and bounded acceptance

This follow-up supplements M1/M1b maturity evidence. OpenCode and Gemini have inspectable versioned loaders/tests, but no upstream tests were executed here. [Copilot changelog](https://raw.githubusercontent.com/github/copilot-cli/main/changelog.md) lists 1.0.85 on September 16 and continued fixes to MCP startup, cancellation and streaming. This demonstrates maintenance and operational defects, not completeness of public stdout contracts. Cursor support provides a reproducible reported failure and official response. Grok has public config documentation and releases, but the installed/source version mapping remains untested. No popularity-based quality claim or adoption count is asserted.

Proposed checks below belong to backend/test; the lead owns adoption and records unresolved compatibility limits.

| ID | Concrete input/action | Expected result and verification |
| --- | --- | --- |
| F1 | Cursor segment events followed by aggregate result; repeat using partial-stream duplicate flushes. | Text exactly once, correct session ID; parser unit tests. |
| F2 | OpenCode step_finish with tool-use continuation, later text, then exit; repeat with error plus zero exit. | No premature completion; error remains failure; fake process tests. |
| F3 | Concurrent generated MCP configs with distinct URLs/tokens; cancel one. | Endpoint/tool mapping stays per-run; no credential copying or persistent config writes; inspect subprocess env/files and cleanup after exit. |
| F4 | Gemini user-owned system/default override files plus an alias allowlist. | Never claim successful injection merely from argv; require version-specific config-load/handshake evidence without model calls. Unsupported path reports capability error. |
| F5 | Grok overlay containing an MCP entry. | Adapter does not advertise this unsupported injection mechanism. Contract review against official allowlist. |
| F6 | Copilot SDK-shaped events without established CLI terminal/session fields. | Fixtures labeled provisional; exact CLI compatibility stays unverified until primary capture/schema exists. No false acceptance from synthetic tests. |
| F7 | Denied shell/web and a selected trusted MCP tool, plus conflicting ambient grants. | Supported adapter enforces explicit restrictions without dropping required tool; otherwise fails with a specific capability error. Fake command tests plus version-pinned config evidence. |

Submission checks: `git diff --check` and `test -s docs/delivery/provider-transport-followup.md`. No follow-up milestone is assigned in tracker revision 26; lead must attach this artifact to a verification assignment before acceptance. Accepted M1b is not reopened. The worker cannot write Git metadata outside its writable roots; commit through Rem's authorized host bridge if required. No commit, swarm verification or integration is claimed by this document.
