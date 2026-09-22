# Additional CLI providers

The backend IDs are `cursor`, `copilot` and `opencode`. They work through the existing Raticode transcript and subprocess runtime. Native session IDs are reported where the wire contract supplies them, but are never passed to another invocation. Rem steering restarts with Raticode's conversation, persona and resource context. Swarm native steering remains Codex-only.

## Catalog and profiles

Each provider catalog entry includes:

```json
{
  "id": "cursor",
  "displayName": "Cursor",
  "permissionModes": [{"id": "default", "displayName": "CLI default"}],
  "defaultPermissionMode": "default",
  "supportsCustomModel": true
}
```

Copilot's label is `GitHub Copilot`; OpenCode's is `OpenCode`. Existing catalog availability, version, status, models and errors retain their meaning. Codex publishes its three sandbox modes, defaulting to `workspace-write`. Claude publishes its existing approval modes, defaulting to `dontAsk` for Rem.

The three new adapters accept only omitted or `default` permission mode. Shell/web selections are separate resource controls. Do not send Codex's `workspace-write` to them. Profiles preserve exact model strings and reject unsupported permission modes, legacy tool/MCP string flags and arbitrary extra arguments. OpenCode rejects separate effort settings. Copilot passes advertised effort values through `--reasoning-effort`. Cursor combines the selected model and effort into its native model ID. `cli-default` omits model and effort options. Rattish provider contracts use that sentinel as a portable default. No static model list constrains portable compilation.

OpenCode reads `opencode models` as full `provider/model` IDs and allows custom IDs. Cursor parses `cursor-agent models` rows as `ID - label`, stripping terminal colors and trailing current/default markers. It groups recognized trailing effort tokens under their base model. For example, `cursor-grok-4.5-low-fast` becomes model `cursor-grok-4.5` and effort `low-fast`. Speed and thinking suffixes remain part of the effort choice so execution reconstructs the exact advertised ID. Prefixes such as `claude-opus-thinking` remain separate model families. Both `xhigh` and `extra-high` retain their native spelling. Unknown suffixes remain independent model IDs.

Cursor preserves catalog order and removes duplicate IDs. Each family defaults to its explicitly marked default, its unsuffixed entry if available, or its first advertised variant. An unsuffixed entry in a family with variants appears as the `cli-default` effort. Models without variants have no effort choices. Manual model inputs are removed. Copilot reads its SDK `models.list` catalog, including supported reasoning efforts. Discovery uses an empty temporary directory with explicit `--add-dir` trust. A catalog status of `unsupported_cli_version` means enumeration is unavailable.

## Commands and output

| Provider | Headless arguments | Output contract |
| --- | --- | --- |
| Cursor | `--print --output-format stream-json -p PROMPT`, optional `--model MODEL` | Append complete assistant text segments; do not repeat aggregate result text. Require successful result and zero process exit. Read `session_id`. |
| Copilot | `--output-format text --silent -p PROMPT`, optional `--model MODEL` | Preserve stdout as one final answer, never as thought fragments; nonzero exit/cancellation fails. No guessed JSON events, usage or native session ID. |
| OpenCode | `run --format json --model PROVIDER/MODEL PROMPT` | Append `text.part.text`; deduplicate part IDs. `step_finish` is not terminal. Process exit completes the run; error records fail even with zero exit. Read `sessionID`. |

Prompt and model are individual argv values, never interpolated shell commands. Agent execution supplies the complete prompt, without relying on a provider tool to read a temporary prompt file. Malformed Cursor/OpenCode JSON fails the run. Cancellation retains partial output and drains the subprocess before deleting invocation files. Images fail explicitly. Cursor effort choices use `--model`, never a separate `--effort` flag.

These tests use synthetic fixtures from the cited contracts, not captured authenticated responses. Copilot JSONL/session metadata compatibility remains unverified; using text is an explicit compatibility choice. A live Copilot smoke test on 2026-09-16 returned one complete answer with shell and web disabled.

## Resources and configuration lifetime

Every invocation uses a private temporary directory and unpredictable MCP names. The caller's trusted Swarm URL must exactly match the selected `swarm` resource before receiving a `swarm_action` grant. The Second Brain executable and argument prefix must match the installed tool before its four tools receive grants. Other selected MCP servers receive their requested server grant. Generated files live until process cleanup, including cancellation and spawn failure. Concurrent invocations do not share files or names.

Copilot receives `--add-dir` for the selected working directory on each invocation, without changing persistent trust settings or granting blanket tool access. It receives `--additional-mcp-config @FILE` and exact `--allow-tool ALIAS(swarm_action)` grants. Shell and URL denials use documented deny flags. OpenCode receives additive `OPENCODE_CONFIG_CONTENT`; pre-existing inline settings from the caller or environment survive. Remote entries set `oauth: false`. `OPENCODE_PERMISSION` supplies a default deny and selected native/MCP grants. Explicit extra filesystem roots use `external_directory` permission patterns. Neither path relocates credentials or copies authentication stores.

These are additive MCP mechanisms. Other provider-configured servers or plugins can still load. Tool policy is not an OS or network sandbox. This implementation does not claim isolation of every ambient provider setting. Real authentication and MCP handshake failures remain provider errors; synthetic tests establish Raticode's config construction and process ownership, not a successful remote handshake.

### Cursor resource compatibility

Cursor uses a temporary plugin containing `.cursor-plugin/plugin.json` and `.mcp.json`, supplied through `--plugin-dir`. Its loader maps entries to `plugin-PLUGIN-SERVER` identifiers and supports HTTP, stdio and `enabledTools`.

A private `CURSOR_CONFIG_DIR/cli-config.json` supplies permission allow/deny arrays. `--disable-project-configs` prevents `.cursor/cli.json` from replacing those arrays. The source-backed private `--allowed-tools` option selects protocol tool names, excluding shell and web tool kinds when disabled. Unlike `--exclude-tools`, this option is not guarded by the inspected exclude-tools feature flag. It also excludes provider subagent tools from this selected set. The allowlist includes `get_mcp_tools_tool_call`, which Cursor requires for deferred MCP discovery even with no selected servers. Omitting it causes `Required tool GET_MCP_TOOLS not found in allTools`; individual MCP execution grants remain unchanged. Exact plugin tool grants use `Mcp(plugin-PLUGIN-SERVER:TOOL)`. No broad `--force` or `--approve-mcps` grant is used.

Cursor accepts any build version without a date allowlist or version probe. Resource startup checks help for Cursor identity and the required `--plugin-dir` capability. Private options remain version-sensitive; actual CLI failures are reported by the adapter. Every Cursor invocation passes `--trust` for the working directory selected in Raticode, including project and Global Rem threads. This does not enable `--yolo` or override selected tool permissions. Normal authenticated Cursor mode supports user-local plugins; local/Bedrock mode does not, and the documented local-mode environment switches are rejected. The private config does not relocate the credential backend: Linux credentials use XDG configuration, macOS uses the home directory/keychain. Raticode does not read or copy those credentials. Existing account-specific soft CLI settings are not copied into the private config.

## Evidence and limits

Primary references accessed 2026-09-16:

- [Cursor output](https://cursor.com/docs/cli/reference/output-format), [parameters](https://cursor.com/docs/cli/reference/parameters), [permissions](https://cursor.com/docs/cli/reference/permissions).
- Host-supplied official Cursor `2026.09.10` package at `/tmp/rem-cursor-agent-2026.09.10`, help at `/tmp/rem-cursor-help.txt`. Bounded source inspection confirmed plugin manifest/config discovery, MCP identifiers, config/credential paths, project override disabling and protocol tool filtering. No external core implementation was copied into Raticode.
- Installed Cursor `2026.09.15-d2fe57e` package inspected on 2026-09-16. Confirmed private tool allowlist parsing and application, project-config suppression, isolated config lookup, and plugin MCP `enabledTools` propagation. The previous single-date check rejected this compatible CLI before starting any model.
- [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference), observed release `1.0.85`. The SDK event schema is not treated as a CLI contract.
- [OpenCode v1.18.31 run command](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/cli/cmd/run.ts), [configuration](https://opencode.ai/docs/config/), [permissions](https://opencode.ai/docs/permissions/), [MCP configuration](https://opencode.ai/docs/mcp-servers/).

Acceptance checks live in `tests/unit/test_cli_providers.py` and neighboring catalog/profile/Rattish tests. They cover fragmented streams, duplicates, malformed/fatal output, missing completion, session identity changes, cancellation, model/prompt preservation, compile/preflight/runtime selection, config lifetime and trusted endpoint mismatch. Independent integration review and real-provider compatibility evidence remain separate from these tests.

## xAI Grok

The canonical provider ID `grok` has registered Rem, Swarm and Agent-node
adapters using Raticode's ACP subprocess transport. Official Grok Build runs
`grok agent --no-leader stdio` after help/version family checks. Custom models use `session/set_model`; unsupported
selection fails explicitly. Every invocation creates a fresh session and receives
the Raticode conversation. Provider switching never reuses another session.
Portable Rem steering interrupts, drains and continues with the same thread context.

Grok defaults to CLI-managed permissions; an explicitly selected strict mode
rejects execution before a prompt. These CLI interfaces do not establish
Raticode's exact native shell, web and MCP restrictions. Select **CLI-managed permissions** explicitly in Rem or swarm member
settings to use the CLI's existing policies, configured tools and ambient MCP
servers. The UI explains that Raticode shell/web switches cannot restrict native
tools in this mode. Existing native prohibitions stay in place. Requests requiring
interactive permission are denied; Raticode does not enable YOLO or bypass modes.
No existing provider or resource selection is changed automatically.

For Agent nodes, select a provider profile with `approval_mode: "cli-managed"`.
Preflight rejects a default/strict profile with the same explanation. Grok
preserves exact custom model IDs, accepts the `cli-default` sentinel and rejects
image attachments and legacy tool/MCP string flags. Grok reads `grok models` for
IDs and defaults, then queries the same CLI through ACP `_x.ai/models/list` for labels and advertised reasoning efforts.
This metadata probe sends neither a prompt nor a session creation request. If
metadata is unavailable, the text catalog remains usable without invented effort
choices. Canonical effort values go into `session/set_model` metadata as
`reasoningEffort`, or session creation metadata when no model was selected.

Selected structured MCP definitions use a private invocation configuration.
Grok receives a temporary plugin through `_meta.pluginDirs`; HTTP readiness checks validate the expected endpoint
and required enabled tools. Configuration survives until the process drains and
is then deleted. Authentication stores and persistent CLI configuration stay in
place. Native mode does not claim to exclude ambient MCP servers or sibling tools.

The protocol follows the official Grok Build source snapshot
`37949780c144e37df692e3d669051a21fec24f20` documented in the delivery research.
Tests use synthetic ACP processes and fake providers, including actual Rattish
compile/preflight/runtime dispatch. No authenticated live Grok model
session was run. CLI family probes do not certify every installed version's
protocol compatibility; unsupported initialization, model selection and tool
readiness report errors rather than falling back to another provider.


## Google Antigravity

Google's [migration guide](https://antigravity.google/docs/cli/gcli-migration)
identifies Antigravity CLI as the successor for Gemini CLI users. Raticode's main
provider list shows `antigravity`, backed by `agy`. Gemini CLI is unsupported;
its provider contract and runtime adapter have been removed. Update saved workflows
and profiles to Antigravity and review model IDs, authentication settings, and
executable overrides. Existing selections are not silently redirected.

The installed native CLI is 1.2.4. Discovery runs `agy models`, which lists native
slug and display-name columns. Trailing low, medium and high variants group into
one model family with a separate effort picker. Each family retains its first
advertised native ID and default effort; only advertised efforts are selectable. Refresh never sends a model prompt or starts browser login.
Run `agy` in a terminal for Google's interactive migration and browser sign-in,
then refresh providers. See [installation](https://antigravity.google/docs/cli/install).

Execution uses [headless NDJSON](https://antigravity.google/docs/cli/headless),
`--input-format stream-json --output-format stream-json`, with one user event on
stdin. Closing stdin ends the process after the result. A fresh process receives
the existing Rem persona, conversation, and project references each turn. Reply
deltas are not thought entries; completed tool steps appear once and the terminal
result supplies the answer. Non-success terminal states, malformed streams,
missing completion, and nonzero exits remain errors.

Antigravity requires explicit CLI-managed
permissions. Raticode never adds `--dangerously-skip-permissions`. Native CLI
policies and ambient tools remain active. For grouped models, the selected effort
replaces the native model ID suffix, so Flash plus Low passes the exact advertised
`gemini-3.8-flash-low` ID. Saved native variant selections retain their effort.
Profiles without a variant ID use the native `--effort` option. Image attachments
are not supported.

Selected MCP servers are staged in a temporary `.agents/mcp_config.json` using
Antigravity's `serverUrl` and stdio schemas. The actual project is added with
`--add-dir`, and the prompt explicitly identifies it as the user's working
directory. With no selected MCP servers, the process runs directly in the project.
This avoids changing project/global MCP files or relocating authentication.
The temporary configuration is removed only after the process drains. Native
headless permission policies may deny MCP calls requiring interactive approval.

Live verification confirmed the installed binary/version and the unauthenticated
model-discovery result. Authenticated model execution and MCP readiness remain
unverified. Regression tests use synthetic subprocesses and published model rows.
