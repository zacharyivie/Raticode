# Provider CLI and Rem steering research

Research milestone M1. Owner: research. Starting revision: `3ff12123e8f41cfe61fca1f99806bdbba76525e2`. Sources accessed 2026-09-16. This is a design recommendation for AC1-3, not implementation or acceptance evidence. Only this document is changed. No dependencies were installed, no provider sessions or workflows were executed, and no external implementation was copied.

## Repository evidence

Read root `AGENTS.md`; the checkout contains no nested `AGENTS.md`. Initial `git status --short` was empty. `pyproject.toml` declares Python 3.11+, asyncio-compatible process utilities, Pydantic and pytest tooling. No new library is needed for the proposed adapters or state machine.

* `src/gofer/subscriptions/base.py` owns process execution, cancellation, temporary prompt files and output parsing. Its prompt is currently an instruction to read a file; providers without access to that file cannot receive the actual task through this path.
* `src/gofer/ui/chat.py` separately builds Rem commands and streams results. `ProviderName`, validation and binary selection admit only Codex and Claude. It already builds a Raticode-owned transcript and compacts it. Swarm turns reuse this path.
* `src/gofer/ui/codex_steering.py` already has native steering, expected-turn matching, explicit rejection and `SteeringDeliveryUncertain`. Preserve these distinctions. `src/gofer/ui/swarms.py` currently advertises steering only for Codex.
* `src/gofer/core/provider_capabilities.py` discovers account-dependent models, caches by executable path and mtime, and distinguishes missing, authentication, version, timeout and invalid-response failures. Its provider ID type, registry and binary resolver require extension.
* `provider_profiles.py`, `provider_permissions.py`, `rattish/provider_runtime.py`, provider JSON contracts and frontend selectors are additional integration points. A subscription class alone cannot fulfill AC2.
* Relevant existing tests include `test_subscriptions.py`, `test_ui_chat.py`, `test_ui_server.py`, `test_codex_steering.py`, `test_rem_swarms.py`, `test_provider_capabilities.py`, `test_provider_profiles.py`, `test_rattish_provider_contracts.py` and `test_workflow_provider_discovery.py`.

Baseline test execution belongs to M2. This research attempt has no local `.venv` and makes no claim that baseline tests pass. Inspection commands exited successfully except an exploratory reference to nonexistent `ui/assistant.py`; the actual module is `ui/chat.py`.

## Verified CLI contracts

The examples below describe argv tokens, not shell strings. `PROMPT`, `MODEL` and `ID` are placeholders. They establish documented syntax, not a tested minimum version. Record the installed executable/version and maintain representative fixtures before claiming compatibility with it. Omit the model flag for `cli-default`; never send that UI sentinel to a CLI.

| Provider | Documented invocation and continuation | Integration implications |
| --- | --- | --- |
| Cursor | `cursor-agent -p PROMPT --output-format stream-json --model MODEL`; explicit `--resume ID`. Current primary command is `agent`, with `cursor-agent` retained as an alias. | Prefer the identifiable alias when available. A generic `agent` binary requires identity verification. `agent models` and `--list-models` are documented discovery paths. |
| Copilot | `copilot -p PROMPT --output-format json --model MODEL`; `--resume=ID`. | JSON means JSONL. Use standalone Copilot CLI, not the older `gh copilot` extension. Never use a bare resume picker in headless mode. |
| Gemini | `gemini -p PROMPT --output-format stream-json --model MODEL`; `--resume ID`. | Parse `init`, assistant `message`, tool, error and final `result` separately. Headless exits include 1 for general failure, 42 for invalid input and 53 for turn limits. |
| xAI | `grok -p PROMPT --model MODEL --output-format streaming-json`; `--resume ID`. | Official product identity is Grok Build CLI, executable `grok`. The long prompt flag is `--single`. Do not substitute a community Grok CLI. |
| OpenCode | `opencode run PROMPT --format json --model PROVIDER/MODEL`; `--session ID`. | JSON is an event stream. Preserve the full provider/model string. `opencode models` lists configured-provider models. |

Cursor facts come from its [headless documentation](https://docs.cursor.com/en/cli/headless), [usage documentation](https://docs.cursor.com/en/cli/using) and [2026-01-08 changelog](https://cursor.com/changelog/cli-jan-08-2026). Copilot syntax, model and resume options come from the [command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference). Gemini formats and exits come from the [headless reference](https://geminicli.com/docs/cli/headless/), with model and resume options in [configuration](https://geminicli.com/docs/reference/configuration/). xAI syntax comes from [headless scripting](https://docs.x.ai/build/cli/headless-scripting), updated June 10, and [CLI reference](https://docs.x.ai/build/cli/reference), updated July 21. OpenCode options come from its [CLI reference](https://opencode.ai/docs/cli/).

Discovery must not invent JSON output flags for human-readable model listings. xAI documents `grok models` and `grok version`. A missing machine-readable catalog is different from a missing executable. Keep custom model entry available where the installed CLI cannot enumerate models. Preserve the existing separation between portable workflow validation and host-dependent availability checks.

## Permissions, tools and context

Provider modes are not interchangeable. Preserve the selected provider's vocabulary and reject unsupported combinations before spawn. Never repair an unsupported restrictive mode by enabling unrestricted execution.

* Cursor documents `--force` for headless file modification. Older usage documentation describes broad noninteractive write access. This inconsistency requires version-specific fixtures; neither omission of `--force` nor prompt text proves an OS read-only boundary. Do not expose an unverified read-only guarantee. See [headless mode](https://docs.cursor.com/en/cli/headless).
* Copilot documents `--allow-tool`, `--deny-tool`, and session-scoped `--additional-mcp-config`, which augments other configuration. Deny rules take precedence. Broad tool approval is not the same as path approval. See the [command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference). Test actual headless allow/deny behavior before mapping a Raticode mode.
* Gemini exposes `default`, `auto_edit`, `yolo` and `plan` approval modes, but its reference qualifies plan support as experimental. `--allowed-tools` bypasses confirmation; it is not a general sandbox. See [configuration](https://geminicli.com/docs/reference/configuration/).
* Grok distinguishes tool permissions from filesystem/network sandboxing. `--always-approve`, `--allow` and `--deny` are documented; deny rules win. See [permissions](https://docs.x.ai/build/features/permissions). Global and project MCP configuration may also load Claude/Cursor compatibility files. See [MCP servers](https://docs.x.ai/build/features/mcp-servers). An adapter must not assume only Raticode-supplied servers are present.
* OpenCode documents permission configuration and `OPENCODE_PERMISSION`; its [permission documentation](https://opencode.ai/docs/permissions/) is the policy reference. The [v1.18.31 run command source](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.31/packages/opencode/src/cli/cmd/run.ts) auto-rejects pending permission requests unless auto approval is selected, and matches session events before consuming them. Do not treat an absent interactive dialog as permission success.

Recommendation: add explicit adapter capabilities for model selection, effort, images, prompt transport, MCP transport, permissions and session continuation. Keep per-provider argv construction and stream parsing small and Raticode-owned. Reuse process-tree termination and resource limits. Buffer JSONL across arbitrary byte/chunk boundaries, retain stderr separately, and only render recognized assistant content. A tool result containing `text` must not become the final answer. Unknown event types may be ignored; malformed required records and provider-declared fatal errors must produce a failure even if process exit is zero.

Rem/Swarms need their existing trusted MCP tools to remain usable. Registering a provider while dropping `trusted_swarm_url`, Second Brain, or selected resources is incomplete. Use temporary per-run configuration only through verified provider mechanisms, retain cleanup through cancellation, and do not mutate user-global configuration. Where an isolated MCP transport is unverified, report that capability gap and keep AC2 open. Generic prompt-file reading also needs an explicit readable path grant or another supported transport. Never quietly discard images, effort or resource restrictions.

## Steering design and alternatives

Recommendation R1: implement portable steering in Rem's own turn coordinator, using interrupt-and-resume where native steering is unavailable. The logical Rem task may span multiple CLI processes. Preserve the Raticode transcript, current working tree and selected resources. Resume a verified provider session only under the same provider and context identity; otherwise start a fresh process with the Raticode transcript and an explicit interrupted-task continuation prompt. The latter preserves visible context, not hidden provider reasoning. It costs another startup and may repeat analysis.

The [ACP v1 prompt-turn specification](https://agentclientprotocol.com/protocol/v1/prompt-turn) gives a useful lifecycle rule: cancellation is asynchronous, updates may arrive before the cancelled completion, and another prompt follows turn completion. Raticode should adopt that ordering in its own coordinator without acquiring an ACP implementation. Grok documents `grok agent stdio` for ACP, but this alone does not prove native in-flight steering across all providers.

| Alternative | Benefit | Cost and decision recommendation |
| --- | --- | --- |
| Native steering | Keeps a live provider turn and avoids restart. | Retain proven Codex support behind capabilities. Do not infer support from interactive terminal follow-up input. |
| Interrupt, wait, continue | Works with headless processes and Raticode's transcript. | Recommended portable baseline. Cannot roll back completed tools or promise exactly-once external side effects. |
| Queue until natural completion | Simplest delivery implementation. | Does not satisfy an instruction that must redirect active work. Reserve for a documented completion race, with a visible receipt. |
| PTY keystroke automation | Can reach interactive-only functions. | Reject for this milestone: terminal versions, focus and prompts make reliable delivery difficult to verify. |
| Replace orchestration with another project's engine | Would reuse someone else's lifecycle. | Reject. Raticode owns orchestration, context and delivery; no import, wrapping, vendoring or copying of an external core AI implementation. |

Recommendation R2: define acceptance as Raticode taking durable responsibility for a message, not proof that a model obeyed it. Proposed request fields are `conversationId`, `turnId`, `requestId`, `text`. These are a proposed contract, not existing API names. Under a per-conversation lock, validate the expected turn, persist a unique message and bind its provider/model snapshot before acknowledging. Repeated request IDs return the same receipt; conflicting text under an existing ID is rejected. Proposed receipts distinguish `queued`, `interrupting`, `delivered`, `rejected`, `uncertain`, with delivery mode `native` or `restart`.

Recommendation R3: use separate cancellation reasons for user stop and steering restart. Wait for the old process tree to exit, drain its output, record partial content as interrupted, then launch one successor. Tag callbacks with a generation ID so a late final/error cannot overwrite the successor. A user stop cancels any restart and retains pending text visibly. Two steering messages accepted during shutdown retain order and reach the successor once. If completion wins before acceptance, reject the stale turn with the draft intact or explicitly accept a next-turn receipt. Never show success for dropped text.

Recommendation R4: preserve `SteeringDeliveryUncertain` for native requests with a lost acknowledgement. Do not automatically replay them through restart. Display the unresolved receipt. For restart, process creation is an observable handoff, not proof of provider consumption; a startup failure leaves the message retryable without duplicating history. On server restart, persisted accepted-but-undelivered records must remain visible and must not launch autonomous work without a recovery policy.

Recommendation R5: keep one Rem conversation independent of provider-specific session IDs. Provider switching retains persona, conversation and resource selections. Namespace provider session identity by provider, workspace and conversation; invalidate or segregate it when switching providers. Preserve the actual requested model and report explicit errors rather than substituting another provider/model. Steer controls must stay enabled across supported models, show pending/delivered/error status, and retain text on a rejected submission. Stop remains distinct from Steer.

## Maturity and operational evidence

Gemini and OpenCode are useful open-source case studies for process and event contracts, not components to acquire. [Gemini releases](https://github.com/google-gemini/gemini-cli/releases) show stable v0.60.0 plus daily nightly and preview builds at access time. Its [v0.60.0 noninteractive tests](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.60.0/packages/cli/src/nonInteractiveCli.test.ts) exercise streaming and tool execution through doubles. [Issue 18593](https://github.com/google-gemini/gemini-cli/issues/18593) reports incorrect session selection while resuming. This is reported operational experience, not proof that the current stable version still fails. It motivates exact-ID fixtures and checking reported session identity.

[OpenCode releases](https://github.com/anomalyco/opencode/releases) show v1.18.31 and multiple adjacent patch releases. Its version-pinned run source exposes session-filtered event handling and permission rejection. [Issue 2095](https://github.com/anomalyco/opencode/issues/2095) reports hanging execution. The report establishes a real failure mode to test with a bounded timeout, not its prevalence or present status. Its test-directory page could not be fetched, so this review does not claim test-suite coverage or test results for OpenCode.

[Copilot releases](https://github.com/github/copilot-cli/releases) show 1.0.85 and detailed maintenance notes, including MCP identity fixes. This is release/support evidence, not a public audit of implementation tests. Cursor's January changelog establishes maintained CLI command compatibility. xAI's dated official CLI, MCP and permissions documentation establishes product identity and operational contracts. Neither establishes publicly audited implementation coverage in this review. No independently verified deployment counts were found; issue reports and release activity are adoption/maintenance signals, not reliability scores. Current version numbers above are observed releases, not minimum supported versions or installed versions.

## Observable acceptance and executable checks

The lead owns adoption/rejection decisions; backend owns adapter and lifecycle implementation, frontend owns user interactions, test owns independent regression evidence. The following are proposed additions to the tracker, not claims of passing tests.

| ID / objective | Concrete input or action | Expected result and verification |
| --- | --- | --- |
| R-AC1a / AC1 | For each of seven CLI providers, block a fake active turn, submit text with request ID, release shutdown. | One successor receives original history and steering exactly once; old process exits first; same selected model/resources. Barrier-controlled async test. |
| R-AC1b / AC1 | Race steering against natural completion, explicit Stop, a second steer and duplicate HTTP retry. | Deterministic receipts; no lost accepted text, duplicate transcript entry or concurrent successor. Assert every lock-order outcome without sleeps. |
| R-AC1c / AC1,3 | Lose native acknowledgement after fake acceptance; emit stale final after restart; crash after accepted persistence. | No automatic uncertain replay; stale final cannot finish successor; restart exposes unresolved text. Fake transport and persistence reload tests. |
| R-AC1d / AC1 | In rendered Rem, type while streaming, Steer, receive rejection, retry, then Stop. | Draft survives rejection; status is visible; successful receipt clears only submitted draft; Stop prevents restart. Test each provider and keyboard path. |
| R-AC2a / AC2 | Save/reload each provider with custom model in Rem, Swarm roster and Rattish Agent node. | Exact values survive; compiler/preflight/runtime agree; argv receives correct CLI and model. Parameterized tests plus frontend persistence checks. |
| R-AC2b / AC2,3 | Supply quoted/multiline prompt, MCP resources, restrictive permission and fragmented JSONL. | Prompt reaches provider intact; tools remain scoped; no shell interpolation; one correct final answer and session ID. Fixture tests inspect argv/env/config and parsed events. |
| R-AC2c / AC2,3 | Switch providers during a thread, then continue. | Raticode persona/history/resources persist; old provider session is not passed to new CLI. Inspect second invocation and rendered selector. |
| R-AC3a / AC3 | Missing binary, wrong `agent` identity, unsupported flag, auth error, fatal JSON error with exit zero, truncated JSON and stalled process. | Specific failure, bounded termination, pending message retained; no fallback or false success. Fake executables, no live LLM calls. |
| R-AC3b / AC3 | Existing Claude/Codex requests and native steering fixtures. | Existing permission, model, thought, result, session and cancellation behavior remains covered. Run existing suites. |

Recommended existing-area command, after selecting the worktree environment and checking `gofer.__file__` resolves into this checkout:

```bash
python -m pytest tests/unit/test_subscriptions.py tests/unit/test_ui_chat.py tests/unit/test_ui_server.py tests/unit/test_codex_steering.py tests/unit/test_rem_swarms.py tests/unit/test_provider_capabilities.py tests/unit/test_provider_profiles.py tests/unit/test_rattish_provider_contracts.py tests/unit/test_workflow_provider_discovery.py
```

The test engineer's assigned `tests/unit/test_provider_steering_acceptance.py` should implement independent boundary cases above. Full `python -m pytest`, `ruff check src tests --fix` with fix review, and `mypy src tests` remain required after shared execution/CLI changes. Frontend checks must use the repository scripts after sourcing nvm and selecting root `.nvmrc`; add a browser/Electron Rem interaction check. These commands were not run by research. Final integration must record actual exit codes and the tested revision.

## Gaps and scope limits

No installed-provider version/help outputs or authenticated streams were collected. Documentation does not establish exact Grok streaming event fields, all providers' isolated MCP configuration paths, or uniform native steering support. Engineers must verify those affected details before claiming full support; fake fixtures invented from assumptions cannot close them. Cursor's current parameter page did not expose useful text to the reader, its old permission URL redirected to the documentation root, and an old CLI-reference URL failed. The obsolete xAI permissions URL failed; following the official reference resolved the current page. These gaps do not justify invented flags.

Research excludes product implementation, dependency acquisition, provider installation, workflow execution and live account changes. Only specific unresolved design/review questions should reopen research. Acceptance of this artifact requires swarm verification of the committed file; implementation acceptance requires the separate code and combined integration checks.
