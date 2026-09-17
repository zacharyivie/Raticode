# Per-run MCP injection addendum

Access date: 2026-09-16. Research-only follow-up requested by Rem for Gemini and official Grok. Starting checkout `e58ed5b2c8fb243d06cd5310d0a37196224f1a4a`, clean before this artifact. No product edits, dependency changes, installation, credential/config copying, model execution or upstream code reuse.

## Local fit and decision

Read root AGENTS.md, existing transport follow-up, `pyproject.toml`, subscription adapters, `ui/chat.py`, `ui/codex_steering.py`, and subprocess code. No nested AGENTS.md found under docs/src/tests. Existing `Subscription.execute` owns subprocess lifetime and temporary prompt files. Rem prepares tool permissions separately. `stream_subprocess` closes stdin after its initial write, so ACP requires a bidirectional transport. Raticode's own Codex transport already demonstrates request correlation, bounded output and process teardown, but its methods are not ACP methods.

Recommendation: implement a small Raticode-owned ACP client for Gemini. Grok's documented session-local plugins provide another ACP route. These are alternatives to the previously rejected system-settings and soft-overlay shortcuts. They do not establish a new one-shot CLI config flag. Keep normal authentication locations and the actual project cwd. Do not use a temporary cwd as an undocumented configuration selector.

No baseline product suite was rerun for this documentation-only task. M3's prior integrated test results remain separate. Source inspection is evidence of supported mechanisms, not an executed authenticated transport test.

## Gemini: source-backed per-session MCP

Observed in [v0.60.0 argument parsing](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/config.ts): `--acp` selects ACP; `--experimental-acp` is deprecated. `--extensions` selects extensions; it does not advertise a directory override. The [extension manager](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/extension-manager.ts) enumerates installed user extensions and uses CLI overrides for enablement. Its install/link path creates user extension storage. Therefore installing/linking a temporary extension does not meet the no-user-config-mutation requirement.

Observed in [ACP session manager, lines 53-102 and 265-310](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/acp/acpSessionManager.ts): `session/new` and `session/load` accept `mcpServers`. HTTP definitions map `url` to `httpUrl`, SSE to `url`, and header arrays to a dictionary. Request definitions replace same-name entries in a copied MCP map passed to `loadCliConfig`. New sessions use the loaded authentication selection and `refreshAuth`; load also refreshes auth before initialization. This avoids relocating user storage. Merge is additive, not ambient isolation.

Recommended request shape, using synthetic values only:

```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/absolute/project","mcpServers":[{"type":"http","name":"raticode-run-unique","url":"http://127.0.0.1:12345/mcp","headers":[{"name":"Authorization","value":"Bearer TEST_ONLY"}]}]}}
```

The [dispatcher](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/acp/acpRpcDispatcher.ts) advertises HTTP/SSE capabilities and session loading. Its explicit `authenticate` method can clear credentials when switching methods and writes the selected method to user settings. Recommendation: use existing auth through session creation; fail with an auth-required message when unavailable. Do not automatically switch auth or send `authenticate` merely to inject MCP.

The [stdio transport](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/acp/acpStdioTransport.ts) uses newline-delimited JSON and performs cleanup on connection close. Implement framing and bidirectional requests locally, without importing its SDK or source.

## Grok: documented plugin injection in agent mode

Observed in the official [plugin guide, lines 339-361](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/docs/user-guide/09-plugins.md): a plugin can contain `.mcp.json`; its manifest is optional. `_meta.pluginDirs` on `session/new` and `session/load` supplies automatically trusted, session-only directories. The repeatable process equivalent is `grok agent --no-leader --plugin-dir /absolute/private-plugin stdio`. The flag is ignored in leader mode. Plugins remain subject to native managed MCP policy.

Recommendation: create only Raticode-owned MCP metadata in a unique private directory. Prefer session `_meta.pluginDirs` for explicit scope, or a dedicated non-leader process with the flag. Keep the directory alive through session teardown, including cancellation. Do not install a marketplace plugin or change `GROK_HOME`. Authentication preservation is an inference from leaving normal discovery intact, not a live-login test result. The [official runtime README](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-shell/README.md) documents normal browser credentials under `~/.grok/auth.json`.

The [agent-mode guide](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/docs/user-guide/15-agent-mode.md) documents JSON-RPC initialization, session creation, prompts, updates and permission requests. Agent flags precede `stdio`; `--no-leader` forces a local agent. Do not infer that the plugin flag works with `grok -p`: that one-shot combination was not established. Do not enable always-approve as a shortcut for implementing permission callbacks.

Remaining Grok gap: the exact plugin HTTP JSON schema, effective server/tool names and startup confirmation require a version-pinned loader or help/handshake check. The plugin guide establishes discovery and lifetime but does not give the complete remote `.mcp.json` example. Do not mechanically substitute the native TOML schema. The [MCP guide](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-pager/docs/user-guide/07-mcp-servers.md) confirms native HTTP headers, native `server__tool` names, managed-policy rejection and first-prompt startup races. Use letter-leading hyphenated aliases without double underscores; derive grants from observed effective names.

## Maturity, versions and uncertainty

Gemini [v0.60.0](https://github.com/google-gemini/gemini-cli/releases/tag/v0.60.0) was released September 15 at commit prefix `733edcb`. Release notes include MCP OAuth and extension-boundary hardening. [Session-manager tests](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/acp/acpSessionManager.test.ts) cover session creation, MCP stdio mapping, authentication failure and client filesystem capability handling. The inspected test file does not prove remote HTTP end-to-end behavior. None of these upstream tests ran here.

Grok's public tree reports [runtime crate version 1.0.24](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-shell/Cargo.toml) and [monorepo SOURCE_REV](https://raw.githubusercontent.com/xai-org/grok-build/main/SOURCE_REV) `be7ce6e8cffe46d20bef9834b211616082ee866b`. This identifies the inspected snapshot, not a verified installed binary. The [repository](https://github.com/xai-org/grok-build) describes periodic source syncs; its public history showed 45 commits. [Plugin source tests](https://raw.githubusercontent.com/xai-org/grok-build/main/crates/codegen/xai-grok-shell/src/plugin/mod.rs) cover config updates and reload decisions, not the proposed session injection. [Official changelog](https://x.ai/build/changelog) records fixes for headless MCP readiness, plugin inheritance and concurrent startup hangs. That is operational maintenance evidence and a reason to test those failures. The agent-mode guide lists Zed, Neovim and Emacs integrations, evidence of intended client adoption, not independently measured deployment scale.

Shell HTTP retrieval failed with DNS resolution denial. Web retrieval worked for cited sources, but GitHub recursive-tree API and several guessed source paths returned errors. The Grok loader path was not resolved in this bounded pass. No executable/help test or authenticated no-model handshake ran here. Cursor findings remain Rem's separately supplied pinned-distribution evidence; this artifact does not reverify them.

## Implementation boundaries and proposed exit checks

Backend owns the adapter; test engineer independently implements these checks. Coordinator records adoption and verifies the artifact. No external AI implementation, SDK or dependency is proposed.

| ID | Input/action | Expected result and verification |
| --- | --- | --- |
| I1 | Fake Gemini ACP process advertises HTTP; create and reload session with a unique endpoint/header. | Recorded JSON-RPC contains exact endpoint/header, cwd and alias; no HOME/GEMINI_CLI_HOME override, auth copying or authenticate call. Inspect fake process input and config-write spies. |
| I2 | Fake agent lacks HTTP capability, rejects injection, returns auth-required, or never acknowledges creation. | Fail before session/prompt with a specific capability/auth/timeout error. No fallback prompt can silently omit required tools. |
| I3 | Two simultaneous Grok sessions with different private plugin paths, endpoints and tokens; cancel one. | No cross-session path/token reuse. Remaining session works; cancelled session/process exits before its directory is removed. Fake process and filesystem assertions. |
| I4 | Grok process mode would use leader with a process plugin flag. | Adapter chooses --no-leader or documented session metadata; rejects unsupported mode. Command tests plus installed-version help and no-model loader evidence before compatibility acceptance. |
| I5 | Fragment JSON across reads; interleave notifications, RPC errors and permission requests. | Correlate IDs, preserve text exactly once, enforce output limits and terminal prompt result. Permission requests cannot deadlock; unsupported callbacks fail closed. Fake bidirectional transport tests. |
| I6 | Denied shell/web request alongside the required Swarm tool and hostile ambient grants. | Existing Raticode permission contract remains enforced; injection alone never grants every tool. Test decisions and exact effective tool identity, not only generated argv. |
| I7 | Resume after steering/cancellation with regenerated injection metadata. | Required tool points at current run, no stale token/path reuse, and no duplicate assistant history in Rem. Backend state tests plus independent Rem interaction regression. |
| I8 | Malformed plugin JSON, policy-blocked server, handshake timeout, or missing required tool. | Explicit failure before declaring provider ready; preserve diagnostics without token exposure. Fixture tests and version-pinned non-model probe evidence. |

Candidate executable test command after implementation: `.venv/bin/python -m pytest tests/unit/test_provider_steering_acceptance.py -q`. Test owner must map these IDs to actual test names and record exit codes; this is a proposed check, not a passing result. Existing full-suite, Ruff, mypy and frontend gates still apply to product changes.

Artifact checks: `git diff --check` and `test -s docs/delivery/provider-injection-addendum.md`. Commit and swarm verification remain pending through Rem's requested host bridge. This follow-up has no assigned milestone ID in the supplied inbox, so the lead must attach it before recorded acceptance.
