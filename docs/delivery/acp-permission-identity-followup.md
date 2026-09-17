# ACP permission identity follow-up

Bounded answer to backend request `2cdc517fd891410c805a216dfa30c1c8`, accessed 2026-09-16. Starting revision `7e1dc3e478447a1d25cc8331716e774e3039accf`, clean assignment `7aa722e8b7b9414fbf83679785265b22`. This is research for M3b, not feature acceptance. Prior research artifacts remain unchanged.

Read root AGENTS.md, existing delivery research, `pyproject.toml`, subscription adapter permission/configuration boundaries, Rem resource plumbing and relevant test inventory. No nested docs instructions found. Existing Raticode adapters own per-run configuration and process lifetime; Rem supplies shell/web permissions and a validated Swarm endpoint separately. Keep that ownership and the reviewed ACP transport. No product edits, packages, dependencies, upstream core code, model calls or workflow execution. No product baseline suite rerun for this documentation-only follow-up. `command -v grok gemini` found neither executable and exited 1; installed-binary checks remain unavailable here.

## Findings and decision

Gemini callback-only trusted-tool authorization is unsupported at v0.60.0. Grok has a source-grounded canonical callback identity, but that does not force every tool through a callback. Native permission enforcement remains a separate acceptance requirement. Neither provider should pass restricted-mode preflight merely because a fake callback test passes.

### Gemini

Observed in [v0.60.0 acpSession.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/acp/acpSession.ts), lines 701-716: the permission request contains `sessionId`, `options`, and `toolCall` with `toolCallId`, `status`, `title`, `content`, `locations`, `kind`. It omits canonical tool name, MCP server, raw input and identity metadata. The call ID can come from the model function call. Display text, kind and ID prefixes cannot identify the trusted Swarm tool. Internal policy checks happen before requesting permission, so callbacks do not cover every execution.

Observed in [policy-engine.ts](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/core/src/policy/policy-engine.ts), lines 136-179: `mcpName` matches the actual server identity separately from tool-name matching. Exact server plus short tool name can match the corresponding qualified tool. This is the appropriate identity boundary for generated grants. Recommendation: deny ambiguous callbacks; grant approved MCP operations through native policy after validating injection, instead of inventing callback identity fields.

Candidate native policy entry, synthetic alias only:

```toml
[[rule]]
mcpName = "raticode-run-unique"
toolName = "swarm_action"
decision = "allow"
priority = 900
```

This example alone does not restrict other tools. Generate unconditional shell/web denials when disabled and exact grants for required tools; avoid broad server grants. Backend must ground every tool spelling and test its actual rule selection.

Observed in [config.ts argument parsing and effective settings](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/config/config.ts), lines 328-357 and 768-799: candidate argv is `gemini --acp --approval-mode default --admin-policy /ABSOLUTE/PRIVATE/policy.toml --allowed-mcp-server-names raticode-run-unique`. The CLI passes policy paths and MCP allowlist into policy configuration. These flags do not prove successful policy loading.

Observed in [core policy config](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/core/src/policy/config.ts): supplemental admin paths are not subject to system-file ownership checks, but are ignored if the system policy directory contains TOML policies. Admin tier outranks user, extension and default tiers. User `--policy` paths replace the default user-policy directory, so using that flag can remove existing restrictions. MCP allowlists also produce server-wide permission allows, making exact stronger policy essential. Parse errors emit feedback rather than establishing the requested policy. Recommendation: do not silently replace user policies or bypass centrally managed policy. Without evidence that required denials and grants are effective, report a capability/policy conflict before a model prompt. This research has not established a machine-readable effective-policy acknowledgement over ACP.

### Grok

All following Grok links pin public commit `37949780c144e37df692e3d669051a21fec24f20`, the same source snapshot used by M1e. Its crate version is 1.0.24; that does not establish installed release compatibility.

Observed in [tool taxonomy](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-tools/src/tool_taxonomy.rs): canonical identity lives in `toolCall._meta["x.ai/tool"]`, with `version: 1`, `name`, `namespace`, `kind`, `label`, `read_only`, and optional `input`. MCP namespace serializes as `mcp`. Recommendation: require supported version, namespace and exact name equal to the trusted generated `server__tool`, plus matching session. Do not authorize by label, title, prefix or `read_only` alone.

Observed in [tool preparation](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs), lines 1566-1573: permission ToolCallUpdate receives the canonical metadata stamp. [Normalization](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-tools/src/normalization.rs) resolves identity from the live toolset; unknown tools leave metadata unchanged. Missing metadata must deny, not fall back to presentation strings. [Prompter](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-workspace/src/permission/prompter.rs), lines 725-736, passes this update into the ACP request. Its separate request-level `_meta` describes permission UI/hook details, not the canonical identity object. Select only an offered one-time option; never enable always-approve or remembered grants.

The [pinned permission guide](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager/docs/user-guide/22-permissions-and-safety.md) says ask mode permits read-only operations, ambient grants can authorize tools, and deny rules beat ask and allow rules. Therefore setting ask mode and rejecting callbacks does not enforce a no-shell policy. Exact MCP native rule syntax is `MCPTool(server__tool)`; broad deny rules also defeat exact allows. Do not assume every documented print-mode flag applies to agent mode.

Observed in [CLI definitions](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager/src/app/cli.rs): allow/deny rules belong to top-level PagerArgs. AgentArgs contains model, always-approve, agent-profile, plugin-dir and leader options, but no allow/deny fields. Root help identifies `Grok Build TUI`. Agent help should expose `--no-leader`, `--plugin-dir`, and `stdio`. These distinguish the supported CLI family from an unrelated program named grok; they are not cryptographic provenance.

Observed in [binary dispatch](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-pager-bin/src/main.rs), lines 2167-2188: agent startup forwards permission mode and disable-web-search, but does not forward top-level allow/deny vectors. Thus `grok --deny Bash agent --no-leader stdio` cannot be recommended as an effective shell restriction. The agent path uses the supplied mode for launch yolo/auto resolution; web disabling reaches runtime resolution. Neither proves all-operation callback coverage. Candidate startup is `grok --permission-mode default --disable-web-search agent --no-leader --model MODEL stdio`, subject to supported mode parsing and compatibility checks. Shell denial against ambient grants remains unresolved. The same file prints `grok <version-with-commit/channel>` for `--version`; `version --json` returns `currentVersion` and `channel`. Requiring xAI/Build text in version output rejects valid binaries. Pair version with root/agent help checks and a pinned compatibility probe.

Recommendation: retain an explicit unsupported restricted-shell combination until an effective per-session native control is established. Do not compensate with persistent config swaps, auth copying, blanket approval or a fabricated CLI flag. A Raticode-owned native-policy encoder is preferable to borrowing an upstream agent implementation, but must first have a verified transport contract.

## Maturity and limits

This supplements M1d/M1e maturity evidence rather than reopening it. Grok [acp_session.rs](https://raw.githubusercontent.com/xai-org/grok-build/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/acp_session.rs) contains `tool_meta_stamp_tests`, including permission-request identity assertions for read_file. Taxonomy tests check schema consistency and wire round trips. These tests support the identity design; they were not executed here and do not prove an installed MCP callback. Gemini's pinned session/policy files show maintained explicit policy paths and failure handling. Previously recorded releases and operational evidence remain in the accepted research. No new adoption metric or independent production guarantee is claimed.

GitHub recursive-tree API retrieval failed. Several guessed source paths returned 404; module declarations resolved the cited implementations. No binary, auth, effective-policy, or live callback probe ran. Public Grok source and installed release mapping remain a gap. Ordinary docs can differ from the pinned source; the agent allow/deny dispatch gap is a concrete example.

## Proposed acceptance checks

Backend owns implementation, test engineer independently verifies behavior, reviewer checks pinned contracts, lead records adoption/rejection. These are proposed checks, not passing results.

| ID | Input/action | Expected result and verification |
| --- | --- | --- |
| P1 | Feed Gemini source-shaped callbacks with trusted-looking title/ID, then shell kind and foreign session. | No identity-based MCP approval; one-time deny/cancel response, no remembered grants. Fake ACP regression. |
| P2 | Feed Grok canonical version-1 MCP identity, then missing metadata, version 2, wrong namespace, lookalike name and wrong session. | Only exact authorized session/server/tool can select offered allow-once. Every mismatch denies. Assert response option comes from request. |
| P3 | Start Gemini with native grants plus ambient shell/web allows, trusted-server settings and central system TOML. | Disabled operations remain blocked; if effective policy cannot be established, stop before prompt with explicit error. Fake startup tests plus pinned non-model policy compatibility evidence. |
| P4 | Start pinned Grok agent using top-level allow/deny flags and ambient shell grants. | No claim that parsed flags enforce denial. Restricted-shell startup remains blocked until a source-backed alternative passes independent policy tests. |
| P5 | Return official-shaped grok version, root help and agent help; repeat unrelated same-name executable, missing flags, timeout and malformed JSON. | Accept only compatible family/version combination; actionable bounded failure otherwise. No auth/model request during identity probes. |
| P6 | Steer/reload while permissions/config are pending. | New turn receives current grants; no stale token/session approval; old process drains before config removal. Barrier-controlled adapter tests. |

Executable product checks belong to M3b/M5, using worktree-local Python and fakes. Artifact checks: `git diff --check` and `test -s docs/delivery/acp-permission-identity-followup.md`. This conversational follow-up has no new assigned milestone/attempt; lead must bind the committed artifact to an application verification assignment before acceptance. Existing verified M1e is unchanged.
