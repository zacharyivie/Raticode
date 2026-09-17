# Grok session plugin contract

M1e research, accessed 2026-09-16. Starting Raticode revision `7e1dc3e478447a1d25cc8331716e774e3039accf`, clean worktree. Owner research; backend implements M3b, test engineer independently checks behavior, reviewer checks permissions, lead records adoption and acceptance. This artifact closes the source-schema/name question left by M1d. Installed-binary compatibility and permission callback identity remain separate gates.

Read root AGENTS.md, accepted injection addendum, `pyproject.toml`, `subscriptions/cli_providers.py`, and provider-permission references before external research. No nested docs AGENTS.md found. Existing adapters already own temporary configuration through process drain and use unique MCP aliases. Extend those boundaries with Raticode-owned ACP code. No dependencies, upstream implementation code, product changes, installed-runtime changes, workflow execution or model calls are part of this assignment. No product baseline suite ran for this documentation-only change. The board reports the M3a host checkpoint at `24da30a` passed 1,998 tests; that is prior evidence, not a check performed here.

## Fixed source and confidence

Use public commit [`37949780c144e37df692e3d669051a21fec24f20`](https://github.com/xai-org/grok-build/commit/37949780c144e37df692e3d669051a21fec24f20.patch), dated 2026-09-09, whose patch header records monorepo source revision `c4ea71cfdbcdb21e32e41bc25a0043d7d4836714`. All implementation links below use that public commit. Its [shell crate manifest](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/Cargo.toml) says `1.0.24`. This does not identify an installed executable or prove release 1.0.24/1.0.32 compatibility.

Unpinned `main/SOURCE_REV` returned `be7ce6e8cffe46d20bef9834b211616082ee866b`, while `commit/main` resolved the public commit above. Treat these as different cached snapshots. Do not combine them into one revision claim. GitHub API/tree retrieval failed; the commit patch and full-SHA raw files worked. Shell curl failed DNS with exit 6. Several guessed loader paths returned 404 before the actual paths were resolved. No source build or live binary probe ran.

## Minimal metadata and loader trace

Create an owner-private, unique directory with only this synthetic `.mcp.json`. The example token is fake. Use a real per-run token supplied by Raticode at runtime.

```json
{
  "mcpServers": {
    "raticode-a1b2c3-swarm": {
      "type": "http",
      "url": "http://127.0.0.1:12345/mcp",
      "headers": {"Authorization": "Bearer TEST_ONLY"}
    }
  }
}
```

The pinned [plugin guide](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager/docs/user-guide/09-plugins.md) documents optional manifests and session-local `_meta.pluginDirs` on `session/new` and `session/load`. Those directories are automatically trusted for plugin loading. The process alternative is `grok agent --no-leader --plugin-dir /absolute/private-directory stdio`; `--plugin-dir` is repeatable and ignored in leader mode. Prefer session metadata for explicit isolation. No evidence here establishes `grok -p --plugin-dir`.

```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/absolute/project","mcpServers":[],"_meta":{"pluginDirs":["/absolute/private-directory"]}}}
```

Observed trace:

1. [PluginRegistry::build_for_cwd](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-agent/src/plugins/registry.rs), lines 291-325, clones discovery configuration and adds session directories as CLI overrides. The registry retains these directories for rebuilding. Active plugins require enabled and trusted status. `mcp_owners` maps the original JSON server key to its owning plugin; it does not prepend the plugin name. A directory-derived or manifest plugin name is therefore a separate identity. Recommend a unique directory basename as well as a unique server key.
2. [Plugin loading and merge](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/managed_mcp.rs), lines 424-460 and 560-596, loads file entries before inline entries, deduplicates by server name and passes plugin metadata for substitutions/diagnostics. TOML-claimed names beat plugin entries, including disabled TOML definitions. Managed policy evaluates merged servers and can reject them despite plugin trust. Policy blocks carry reasons; config disables and project pins can omit entries. Unique aliases reduce collisions but cannot bypass policy.
3. [JSON loader and conversion](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/util/config/mcp.rs), lines 1121-1165 and 1393-1442, reads `mcpServers`, deserializes each entry independently, then passes the original key to `to_acp_mcp_server`. Malformed entries are warned and skipped; malformed files yield no config. Successful session creation cannot prove successful injection. Plugin/environment substitutions occur before ACP conversion. Generate literal URL/token values without substitution syntax and validate generated metadata locally.
4. [McpServerTransportConfig and to_acp_mcp_server](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-config-types/src/mcp.rs), lines 28-61 and 372-446, accept a string `url`, optional `type`, and `headers` as a string-to-string map. HTTP headers become ACP name/value objects internally. Missing URL fails deserialization; empty URL is omitted during conversion. An explicit SSE type or URL ending `/sse` selects SSE. Avoid `/sse` for streamable HTTP, combined command/URL definitions, non-string headers and simultaneous bearer-token mechanisms. These are recommendation constraints, not claims that upstream rejects every ambiguous form.
5. [MCP tool runtime](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-mcp/src/servers.rs), lines 45-47 and 1481-1488, qualifies tool calls as `server__tool`. The example becomes `raticode-a1b2c3-swarm__swarm_action`, never `plugin-...`. Keep aliases short, letter-leading and without double underscores. Its initialization state also distinguishes an early finish from completed background handshakes, so an initialization acknowledgement is insufficient readiness evidence.

## Readiness and permissions

Observed in [MCP extension source](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/extensions/mcp.rs), lines 42-160, 610-660 and 1142-1153: `x.ai/mcp/list` accepts `sessionId` and `cache`. It returns named servers with optional session state: `enabled`, `status`, `tools`, `authRequired`, `setupRequired`, `blockedReason`. Status values include `ready`, `initializing`, `setuprequired`, `unavailable`. Local tools in that server's list have unqualified names and an enabled flag. Plugin ownership is a source label, not a tool-name prefix. Recommended gate: bounded polling with the exact session ID until the expected URL/server and enabled `swarm_action` are ready. Missing session state or a wrong URL must not count. Reject blocked/auth/setup/unavailable results and unknown methods explicitly; a missing tool cannot silently pass. Never use an unscoped catalog response as session proof.

```json
{"jsonrpc":"2.0","id":3,"method":"x.ai/mcp/list","params":{"sessionId":"SESSION_FROM_NEW","cache":true}}
```

The response has an extra application envelope. [ExtMethodResult](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/result.rs) serializes `{result: T|null, error?: ...}` inside the JSON-RPC result. Thus inspect the successful RPC result's `result.servers`, and handle both RPC errors and the inner error. Synthetic shape:

```json
{"jsonrpc":"2.0","id":3,"result":{"result":{"servers":[{"name":"raticode-a1b2c3-swarm","source":"local","type":"http","url":"http://127.0.0.1:12345/mcp","session":{"enabled":true,"status":"ready","tools":[{"name":"swarm_action","enabled":true}]}}]}}}
```

The pinned [agent guide](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager/docs/user-guide/15-agent-mode.md) documents ACP permission callbacks and recommends extension discovery from initialization. This research does not establish a machine-authoritative MCP identity field in those permission callbacks. Do not authorize by display title alone, or assume callbacks occur for every operation under ambient permissions. Backend/reviewer must ground the actual callback and native permission-mode behavior before permission acceptance. Deny unknown requests; required-tool availability must not imply shell/web approval.

## Maturity, alternatives and checks

The [official repository](https://github.com/xai-org/grok-build) describes periodic monorepo syncs and released binaries, with 45 public commits visible at access. The pinned patch includes MCP startup ownership repairs and test changes. Registry tests cover inline/file MCP deduplication and inactive ownership; managed-MCP tests cover policy rejection and disabled plugins. This is maintenance and regression evidence, not executed validation here. No independent production adoption measurement or issue discussion was verified. Prefer the exact contract and executable checks over popularity as a compatibility signal.

Session plugins preserve explicit lifetime and avoid Raticode writing persistent provider settings. Process plugins are reasonable for one dedicated non-leader process but broaden scope to its sessions. Reject persistent settings swaps, auth-store copying, unknown print-mode combinations and SDK/core implementation reuse. Keeping normal HOME/cwd preserves normal auth discovery by design; it does not prove the CLI never writes its own caches. Use the existing Raticode adapter/config lifetime and ACP transport, with local schema validation and bounded readiness checks.

The following are proposed exit criteria, not passing feature tests. Backend owns implementation; test engineer maps each ID to executable fake-process tests, reviewer checks pinned compatibility evidence. No model request is required for the proposed compatibility probes.

| ID | Concrete action | Expected result and verification |
| --- | --- | --- |
| G1-schema | Fake process reads generated HTTP metadata with a synthetic header; repeat with malformed JSON, array headers and missing URL. | Exact map/endpoint/token reaches the fake; bad metadata fails before prompt. Validate both JSON snippets locally and check captured ACP inputs. |
| G2-isolation | Start two sessions with distinct directories, aliases, endpoints and tokens; cancel one during handshake and repeat cancellation during drain. | No cross-session values; remaining session works; temporary path survives until its complete process tree exits. Assert filesystem and process state, including steering replacement. |
| G3-ready | Fake list returns initializing, then ready with correct tool; repeat ready with missing/disabled tool, wrong URL, missing session, inner error and method-not-found. | No prompt before exact readiness; success sends one prompt; other cases fail within configured deadline with useful redacted diagnostics. Cover late status notifications and cancellation. |
| G4-policy | Fake list returns blockedReason, authRequired, setupRequired, or absent server after a policy/config drop. | Distinct actionable error where evidence exists; absent server remains unavailable rather than invented policy diagnosis. Zero prompt calls; no fallback config write. |
| G5-permissions | Inject expected tool identity alongside lookalike prefix, foreign session, shell/web request and title-only forged match. | Only a source-grounded identity authorized by current Raticode policy can pass. Unknown/ambiguous identities deny. Verify no blanket approval and test native modes that bypass callbacks. Callback evidence is still required. |
| G6-binary | On an already installed official binary, record path/hash/version/help. With fake local MCP endpoint and existing authorized auth, initialize, create session, list status/tools, then exit without session/prompt. | Record exact schema, header receipt, names, nested envelope and process drain for that binary. Repeat concurrent isolation, a synthetic policy rejection in an isolated test environment, and missing tool. No user config/policy mutation or model calls. If non-model startup/auth is unavailable, report blocker rather than claiming compatibility. |

Artifact checks are `git diff --check` and `test -s docs/delivery/grok-plugin-contract.md`; swarm verification must bind this file to its committed revision. No accepted research file is modified. M1e artifact verification does not accept M3b or waive final integration checks.
