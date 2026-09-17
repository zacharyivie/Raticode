# Baseline and independent test plan

M2 owner: test engineer. Attempt: `da69ab7fb92940efbd72c40c0aac1198`.
Baseline revision: `3ff12123e8f41cfe61fca1f99806bdbba76525e2`.
Recorded 2026-09-16. The assignment worktree was clean at intake. Only this document is owned by M2. No production code, tests, workflows, dependency manifests or user checkout files were changed.

## Environment and evidence

Worktree: `/home/doonk/Projects/Gofer Flow Workflows/workspaces/696586fb3e414109ab4c07a8b7d7b804/da69ab7fb92940efbd72c40c0aac1198`.
Root `AGENTS.md` applies; no nested tracked AGENTS files were found. Python baseline commands run from this worktree. Local `.venv/bin/python` is CPython 3.14.5. `gofer.__file__` resolves to this worktree's `src/gofer/__init__.py`, recorded in `/tmp/raticode-m2-baseline/imports.log`.

The worktree initially had no Python or Node dependencies. `uv venv .venv` failed with read-only `/home/doonk/.cache/uv`, underlying exit not preserved; the original wrapper ended 0 because it tailed the log, so its exit is not evidence of successful setup. `UV_CACHE_DIR=/tmp/raticode-m2-uv uv venv .venv` succeeded. `UV_CACHE_DIR=/tmp/raticode-m2-uv uv pip install --python .venv/bin/python --offline -e '.[dev]'` exited 1 because the fresh cache lacked supported dependency versions. `npm ci --offline --ignore-scripts` exited 226 because `/home/doonk/.npm/_cacache` is read-only. Logs: `environment.log`, `environment-retry.log`, `npm-ci.log` under the log directory above.

To proceed without network or manifest changes, copied installed dependency files from `/home/doonk/Projects/Raticode/Raticode/.venv/lib/python3.14/site-packages/` into the local venv, replaced local `_editable_impl_gofer_flow.pth` with the absolute assignment `src` path, and copied its Ruff binary to local `.venv/bin/ruff`. Copied that project's `frontend/node_modules/` into the assignment's ignored dependency directory. These are local copies, not symlinks to source code. Both copy commands exited 0. This is an installed-environment baseline, not a reproducible clean-install result. Lockfile installation remains unverified.

Before every npm command group: `source /home/doonk/.nvm/nvm.sh && nvm use`, selecting root `.nvmrc` Node v22.12.0 and npm 10.9.0. `node-env.log` records versions. No live LLM CLI was called. Existing pytest test doubles and temporary fixture workflows are authorized test inputs, not user workflow execution.

## Requirement-derived checks

These cases precede implementation. They are planned acceptance checks, not passing claims. Backend owns production code and neighboring Python tests. Frontend owns UI code and interaction tests. M5 test engineer owns `tests/unit/test_provider_steering_acceptance.py` after integrated backend/frontend dependencies are accepted. Reviewer owns independent final diff assessment; coordinator owns verification and integration evidence.

The provider matrix is Claude Code, Codex, Cursor, Copilot, Gemini, xAI and OpenCode. Each provider runs with its documented default and at least one explicit model fixture. Exact executable/argv/JSON fixture contracts depend on accepted research, not guessed flags. xAI research identifies official `grok`; require cited contract verification before encoding it.

| Stable ID | Input/action | Observable expected result | Executable verification | Owner/dependency |
| --- | --- | --- | --- | --- |
| AC1-S1 | Start a blocked fake Rem response, submit distinct steering text for each provider/model | Text reaches native turn or replacement turn once; replacement sees original request, earlier transcript, partial context and new steering; response reflects steering | Parametrized async fake stream test with events controlling process lifecycle; assert captured prompts, turn IDs, transcript and completion count | Backend + M5; accepted research/backend |
| AC1-S2 | Submit steering before process start, during stream, immediately before completion and after completion | Every accepted request has one observable delivery or explicit retained pending state; stale requests get accurate rejection; no disappearance or duplicate replay | Deterministic barriers around acceptance/finish, bounded waits, delivery ledger keyed by request ID | Backend + M5; steering contract |
| AC1-S3 | Repeat a request ID and send two distinct requests concurrently | Retry does not duplicate execution; distinct requests preserve accepted order and text; no overlapping uncontrolled replacement processes | Event-controlled tasks; count starts, kills, deliveries and accepted IDs | Backend + M5 |
| AC1-S4 | Race steering with Stop, thread switch and thread deletion | Stop reaps process and prevents restart; old thread response cannot mutate new thread; deletion cannot revive thread or lose unrelated drafts | Async cancel tests plus rendered Rem interaction; inspect state and subprocess lifetime | Backend/frontend + M5 |
| AC1-S5 | Provider rejects native steering or disconnects after delivery but before acknowledgement | Documented fallback is used only where delivery is known absent; uncertain delivery cannot silently repeat side effects | Fake transport rejects, hangs or closes at exact protocol boundary; assert errors and invocation counts | Backend + M5 |
| AC1-UI1 | During active response, focus composer, clear, type Unicode/multiline steering, submit by supported button/keyboard | Editable draft survives updates; busy state permits steering; clear occurs only after acceptance; errors preserve retryable text; acceptance visibly belongs to correct thread | Rendered browser/Electron test with fake HTTP stream, not source-text assertions | Frontend; integrated API |
| AC2-P1 | Select each new provider with explicit model in Rem, Swarm agent and Agent node, save and reload | Provider/model persist without coercion to Claude/Codex; displayed selection matches invocation | API persistence tests plus browser select/save/reload in all three contexts | Backend/frontend + M5 |
| AC2-P2 | Submit prompt with quotes, newlines, Unicode, paths with spaces and explicit model | Correct executable receives intact prompt/model/context using argv or documented stdin; no shell interpolation or accidental default provider | `_build_command`/subprocess capture fixtures per CLI and stream/session fixtures | Backend + M5; research CLI contracts |
| AC2-P3 | Compile/preflight/run fixture `workflow.rattish` Agent node for every provider | Contract/compiler/defaults agree; available fake provider runs, result/lineage propagate; unavailable executable fails preflight | Provider contract, compiler, agent runtime and conformance pytest groups using doubles | Backend + M5 |
| AC2-P4 | Start Swarm turn for each provider and exercise its temporary board connection | Per-turn MCP config reaches CLI; tool call binds correct agent and attempt, never another identity; connection closes with turn | Fake CLI inspects config and invokes fake board transport; assert prompt identity and lifecycle | Backend + M5; research MCP contract |
| AC2-P5 | Restrict shell/web/MCP/path permissions, then request corresponding operation | Supported restrictions passed exactly; unsupported restriction fails closed with explanation; no permissive substitute | Permission argv/config assertions and denial-path tests across Rem/Swarm/Agent nodes | Backend + M5 |
| AC3-F1 | Remove executable, request unsupported option/model or return auth failure | Correct actionable error names actual provider/cause; no silent fallback, phantom success or retry loop | Parametrized missing binary/validation/nonzero process fixtures; assert no extra spawn | Backend + M5 |
| AC3-F2 | Stream split JSON, malformed JSON, diagnostic stderr, empty output or terminal error | Parser follows documented framing; diagnostics do not become fabricated assistant answers; valid trailing chunks retained; failure state accurate | Byte/chunk stream fixtures per protocol, bounded-output and exit-status checks | Backend + M5 |
| AC3-F3 | Cancel before spawn, during initialization, midstream and while restarting | Child is reaped, terminal state emitted once, no late messages or leaked native session | Controlled fake subprocess tests with process returncode and task cleanup assertions | Backend + M5 |
| AC3-F4 | Switch provider or model on existing conversation with persona, resources and prior context | Persona/thread/resources preserved; incompatible native session not reused; correct model on next command | Persistence, prompt capture and UI switch/reload tests; independent session IDs for each provider | Backend/frontend + M5 |
| AC3-C1 | Run existing Claude/Codex default/model/permission/native-steering paths | Prior defaults, attachments, streaming traces and uncertain-delivery guarantees remain valid | Existing subscription, capabilities, profiles, chat, Codex steering, Rem/Swarm and execution tests | Backend + M5 |
| AC4-V1 | Verify submitted commit, integrate accepted milestones, run combined revision | Required checks exit 0 at recorded final SHA; no stale branch/test substitution; unresolved baseline failures remain blockers | Swarm verify for each milestone, integrate records and final combined verify; full suite partition manifest if needed | Coordinator + M5/reviewer |
| AC4-V2 | Review final diff and dependency manifests | Core orchestration/context/steering implementation remains Raticode-owned; docs and machine contracts match behavior | Independent reviewer examines actual diff plus dependency changes and research adoption record | Reviewer/coordinator |

## Existing coverage and required additions

`tests/unit/test_codex_steering.py` already exercises native Codex requests, cancellation during initialization and safe fallback boundaries. `test_ui_chat.py` covers prompt context, model flags, resolved binaries, permissions, traces, output/resource limits and cancellation. `test_provider_capabilities.py`, `test_provider_profiles.py`, `test_subscriptions.py`, `test_rattish_provider_contracts.py`, `test_rattish_agent_runtime.py`, `test_swarm_execution.py` and `test_rem_swarms.py` are relevant neighboring suites. `tests/conftest.py` provides `FakeSubscription` and isolated per-test data directories.

Existing `ProviderId` and Rem dispatch admit only Codex/Claude. A dropdown or successful argv snapshot alone cannot prove new provider support. Add per-provider stream/error fixtures, real runtime dispatch through doubles, MCP connection tests and portable steering race coverage. Browser source-string tests cannot establish rendered composer interaction. Existing Codex tests must survive rather than being rewritten to weaker generic expectations.

Scope excludes live provider authentication, paid calls, running user workflows, releases, new external core AI dependencies and changing user checkout. No claim is made about undocumented versions or unsupported flags. Permission limitations must be documented and observable, not hidden behind fallback. New controlled inputs require focus -> clear -> type -> blur coverage, plus Enter commit where supported.

## Commands and baseline results

All log paths below are under `/tmp/raticode-m2-baseline/`. Logs stay outside owned repository files; selected result summaries and hashes are recorded here for durable review. The baseline only measures existing code, not the requested feature.

| Command from worktree unless stated | Exit | Result | Log |
| --- | --- | --- | --- |
| `.venv/bin/python -c 'import sys,gofer,pytest,mypy,ruff; print(sys.executable); print(gofer.__file__); print(pytest.__version__)'` | 0 | Local executable/import proven; pytest 9.0.3 | `imports.log` |
| `.venv/bin/python -m ruff check src tests`, before copying Ruff executable | 1 | Setup failure, `RuffNotFound`; repaired by copying binary | `ruff.log` |
| `.venv/bin/python -m ruff check src tests`, after copying binary | 0 | All checks passed | `ruff-retry.log` |
| `.venv/bin/python -m mypy src tests` | 0 | No issues in 196 source files | `mypy.log` |
| `cd frontend && npm run test`, after nvm selection | 1 | 462 tests, 454 pass, 8 fail | `frontend-test.log` |
| `cd frontend && npm run lint`, after nvm selection | 0 | ESLint passed | `frontend-lint.log` |
| `cd frontend && npm run check:build`, after nvm selection | 0 | Vite build passed, chunk-size warning only | `frontend-check-build.log` |
| `cd frontend && npm run test:browser`, after nvm selection | 1 | Build passed; first Electron test exited SIGTRAP, sandbox shutdown operation not permitted | `frontend-test-browser.log` |

Frontend failing cases: Git status reuse/external branch changes; branch deletion safety; binary/merge history; unchanged-baseline cache invalidation; upstream/detached/subdirectory headers; parallel search symlink rejection; desktop grant renewal through Python HTTP; desktop Git/files outside agent roots. Seven report `spawnSync git EPERM`; grant renewal reports `SyntaxError: "undefined" is not valid JSON`. These are baseline blockers. Browser execution is also blocked; lint/build success does not waive rendered-flow verification.

The initial `.venv/bin/python -m pytest` collected 1,927 items and progressed to 68%, then stopped making progress in `test_swarm_execution.py` for more than two minutes. Interrupted with Ctrl-C; execution tool returned 130, without a completed pytest summary. This is incomplete coverage, never a passing full suite. A bounded isolated verbose run and partitions were started to localize the hang and preserve coverage of later files.

`.venv/bin/python -m pytest tests/unit/test_resource_security.py -q` exited 1 in 0.15s: 18 passed, 3 failed. Both parametrizations of `test_http_total_deadline_stops_trickles` and `test_http_cancellation_closes_active_socket` fail because socket construction raises `PermissionError: [Errno 1] Operation not permitted`. This reproduces the baseline failures independently of requested changes. Log: `resource-security.log`.

## Submission and verification limits

`git add docs/delivery/baseline-and-test-plan.md` exited 128 because `/home/doonk/Projects/Raticode/Raticode/.git/worktrees/da69ab7fb92940efbd72c40c0aac1198/index.lock` cannot be created on a read-only filesystem. The current tool permissions do not allow escalation. The artifact remains in the app-managed assignment worktree, uncommitted. No submitted or integrated revision exists for M2 yet. Coordinator has been notified to arrange an app-side commit or writable assignment. Do not represent this document as application-verified or integrated until those actions succeed.

The coordinator also identified that M2 originally had no configured verification check; committed artifacts require one. The coordinator must configure an artifact check on the follow-up assignment before swarm verification. An appropriate check reads this file, verifies the baseline revision, AC1–4 matrix and result sections, and records its hash. A document check does not waive failed product checks.

For fresh integration candidates, do not depend on another checkout's editable install. Prepare a local environment or set `PYTHONPATH="$PWD/src"` with an explicit assertion that `Path(gofer.__file__).resolve()` lies under `Path.cwd() / 'src'`. The dependency-copy method above can prepare local environments without fetching new dependencies. Frontend commands need locally available node_modules and nvm selection.

The application check runner allows 120 seconds per command and at most 20 command arrays. Use separate setup/import, Ruff, mypy, Python partitions, frontend test/lint/build and rendered-flow commands. The final partition manifest must enumerate every collected test exactly once, including new test files; all partitions must complete. An interrupted partition or timeout is a blocker. Run `ruff check src tests --fix` and review its diff after code changes; this documentation-only baseline uses read-only Ruff to preserve source.

`timeout 45 .venv/bin/python -m pytest tests/unit/test_swarm_execution.py -vv --durations=10` exited 124. Eleven cases passed before `test_non_git_writers_are_serial_and_elapsed_limit_cancels` stopped progressing. This isolated attempt localizes the stall without treating the unexecuted remainder as passing. Log: `swarm-execution.log`.

The coordinator confirmed the shared Git-metadata blocker and instructed no further implementation, commit retries or permission bypass. Resume in an app-managed assignment with writable Git metadata and supported local socket/Electron subprocess permissions. Then commit and swarm-verify the preserved artifacts, rerun all required baseline checks, and proceed with implementation only after dependencies are accepted.

## Final bounded diagnostics

The coordinator requested stopping all remaining diagnostics. No new groups or retries were launched after that request.

| Command/group | Final exit | Coverage/result | Log |
| --- | --- | --- | --- |
| `timeout 110 .venv/bin/python -` running pytest on `tests/integration`, `tests/regression` and sorted `tests/unit/test_*.py` filenames before `test_swarm_execution.py`, with `--durations=15` | 1 | 1,319 collected; 1,311 passed, 6 failed, 2 skipped; 88.60s | `python-prefix.log` |
| `timeout 110 .venv/bin/python -m pytest tests/unit/test_swarm_performance.py tests/unit/test_swarm_tools.py tests/unit/test_swarms.py tests/unit/test_thoughts.py tests/unit/test_tui_editor.py tests/unit/test_ui_api.py tests/unit/test_ui_chat.py tests/unit/test_ui_chat_media.py tests/unit/test_ui_server.py tests/unit/test_usage.py tests/unit/test_watcher.py tests/unit/test_webhook_triggers.py tests/unit/test_workflow_*.py --durations=15` | 124 | 589 collected; timed out after two results in swarm performance; incomplete | `python-remainder.log` |
| `timeout 110 .venv/bin/python -` running pytest on sorted `tests/unit/test_*.py` filenames after `test_swarm_performance.py`, with `--durations=15` | 130 | 585 collected; interrupted as requested; four swarm-tools setup errors and three swarm failures visible; incomplete | `python-after-swarm.log` |

Prefix failures are `test_installed_entrypoint_discovers_schema_without_repository_cwd`, `test_successor_nodes_from_same_parent_run_concurrently`, `test_bash_successor_nodes_from_same_parent_overlap`, both `test_http_total_deadline_stops_trickles` cases and `test_http_cancellation_closes_active_socket`. They are baseline failures, not regressions introduced by this documentation. Exact traces remain in the log. No waiver was granted. Remainder traces are incomplete because execution was stopped; do not infer root causes from progress markers.

No required full Python pass, rendered-browser pass, commit, swarm verification or integration was achieved. M2 is blocked with its document and logs preserved. Required next action is environment repair, followed by completed checks and application-recorded submission/integration.

### Log hashes

SHA-256 hashes identify the preserved diagnostic artifacts. Absolute log directory: `/tmp/raticode-m2-baseline`.

| Log | SHA-256 |
| --- | --- |
| `environment-retry.log` | `28553f437dd6c88fce7ded30c0f03a5ce94c279309893665f55036e7b276304e` |
| `environment.log` | `6a3e796494e56612e977a028ecfd1b486f8ae29e20be3212f09c66be4430d414` |
| `frontend-check-build.log` | `c3e160ccbc02697988fc799d782cd86cfc54bb67d099af955875c2d5819a3638` |
| `frontend-exits.log` | `a310751f26408f7595d8956ab3333de8e013b2069e7b1c8302cd4a022370ff23` |
| `frontend-lint.log` | `94f873c1b2477cce10b742816c2392ce76ec8f61ec0c9cc4301f965f73919011` |
| `frontend-test-browser.log` | `b8311baa59773c3a60e64a2e2e3c1bdd105d1727892c8b9962b837eb35919851` |
| `frontend-test.log` | `51e7f2c00a01f9740c506cba5774142b598a0218665c2df31ab8ddff10ac91c6` |
| `frontend.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `imports.log` | `51bee0c4297773fc4ce2825009a619256312a910330281bbc1a6fdd99e12c0eb` |
| `mypy.log` | `e68384225475b8ffaf453fcfc08c86c711fade2401116583391b6e152aaa49f3` |
| `node-env.log` | `6b2a7502a642dde35d16b2819f30bfebc85ff0f58136fa8b8dc6393fbdf5f839` |
| `npm-ci.log` | `1c9b72faf5a6b934f968214608dcc610675f0b7d28fa562837d5673e4fc3f467` |
| `pytest.log` | `b024b0dea72751dd249c39f6a8fbcffa1981948a3af8af086c646cb2b022e8b5` |
| `python-after-swarm.log` | `a8613ed6a327794a6487f803a5234fe195d5ffd0b7a68fe98934b090a72ef8a4` |
| `python-prefix.log` | `ee8a9e03bdf5d69e28a27c5a13070809280c14ec52c5368e1e8eb1105f1755b7` |
| `python-remainder.log` | `a7340b206f1cc13c371708812338139825ebc641fd5c7869ad885d4d1fa43577` |
| `resource-security.log` | `59ea85f2ea41c7ee6eadd8e9353c8f146ad5c7127e298e96f7e352efdaae61f6` |
| `ruff-retry.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `ruff.log` | `9fa3a21a9669691737019388fc72f34514f8c970e6d2d725b1f44054d1d0f0f6` |
| `swarm-execution.log` | `0aa8efcafcf126f8908177fc88c652edb3f8f57d36478c5aac354e6ff37e844f` |

The installed-entrypoint failure is an environment limitation: `.venv/bin/gof` is absent in the copied dependency environment. The two executor failures assert `ExecutionResult.success` is false; their underlying cause was not established within this run. Neither is waived.
