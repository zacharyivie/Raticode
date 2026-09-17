# Bounded ACP helper review

Reviewed revision: `803b2eb5ed13e3b77049a7cb126122f75df299bd`, against its parent. Reviewer assignment started clean at `7e1dc3e478447a1d25cc8331716e774e3039accf`. Applicable root AGENTS.md read; no nested AGENTS.md present in pinned tree.

Scope: six committed ACP transport/session/config and matching test files. Backend's uncommitted catalog/profile and subsequent permission work excluded. This is an early independent review, not M3b/M6 verification or final integrated acceptance. Product files and backend worktree were not changed.

## Blocking finding ACP-S1

`src/gofer/subscriptions/acp_session.py:112-125`, particularly line 115: failure recovery parses queued session updates with `session_text()` without containing another `AcpTransportError`. A second invalid or foreign update can therefore abort recovery before its terminal error event is yielded.

Reproduction against pinned source: fake subprocess reads session/prompt, emits text chunks `first` and ` last`, emits two agent_message_chunk updates whose content is `{type: text, text: null}`, then waits without responding. Consume prompt_session with a one-second request timeout. The first malformed update enters the exception handler; parsing the next malformed queued update raises from line 115. Observed stream contains only two thought events, followed by raised `AcpTransportError('ACP assistant text is malformed')`. No error event contains accumulated body `first last`. Child is reaped with return code -15. The defect is failure-envelope/partial-result loss, not a child leak.

Repair acceptance: invalid or foreign queued updates encountered during error recovery must not prevent exactly one terminal error event containing previously accepted assistant text and exitCode 1. Preserve original failure, never include foreign-session text, and reap the child. Add deterministic regressions for multiple invalid/foreign updates while the prompt is pending and during final queued draining. Session schema failures and framing/EOF failures both need coverage.

Reproduction: `/tmp/acp-review-803b2eb/repro.py`, output `/tmp/acp-review-803b2eb/repro.log`. Final reproduction does not send a response in the invalid-updates case, so it needs no artificial request-completion delay to reproduce ACP-S1. Separate EOF and malformed-framing cases force the reader to observe terminal state before returning the successful prompt response.

## Passing evidence

- `git diff eb5f656 803b2eb -- src/gofer/subscriptions/acp_transport.py tests/unit/test_acp_transport.py` exits 0 with no changes. Transport and tests match the previously independently reviewed R1/R2/R3 repair.
- Exported pinned source with `git archive 803b2eb | tar -x -C /tmp/acp-review-803b2eb/source`. No Git worktree was created. Existing checkpoint Python supplies dependencies; explicit PYTHONPATH points to this export, and reproduction asserts all three helper imports resolve within its src directory. No dependencies installed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/acp-review-803b2eb/source/src /tmp/rem-checkpoint-eb5f656/.venv/bin/python -m pytest /tmp/acp-review-803b2eb/source/tests/unit/test_acp_transport.py /tmp/acp-review-803b2eb/source/tests/unit/test_acp_session.py /tmp/acp-review-803b2eb/source/tests/unit/test_acp_config.py -q -p no:cacheprovider`: exit 0, **58 passed in 6.05s**. Log `/tmp/acp-review-803b2eb/pytest.log`.
- Suite covers repeated cancellation/concurrent close, spawn recovery, deep JSON with unbounded request, concurrent notification waiters, SIGTERM-resistant child cleanup, config lifetime, isolated aliases/private directory permissions, unchanged auth/environment and deny-by-default callbacks.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/acp-review-803b2eb/source/src /tmp/rem-checkpoint-eb5f656/.venv/bin/python /tmp/acp-review-803b2eb/repro.py`: exit 0. Independent fake-process EOF and malformed-framing cases retain ordered `first last` text and emit error with that body; subprocesses are reaped. Third case asserts ACP-S1 remains reproducible. An initial timing-dependent attempt failed to reproduce ACP-S1; the final pending-request case above replaces that assumption.
- Config inspection found no ambient authentication copying, persistent config swapping, external core AI implementation imports or dependency additions in these six files. Private directory cleanup relies on documented caller nesting outside the transport context; its test exercises that order. Permission callback defaults to denial. Provider native-policy enforcement remains a separate acknowledged blocker and is not established by this review.

No full suite, live provider calls, workflow execution, milestone verify, integration or final feature acceptance was performed. Required integrated validation remains with the lead/test milestones. ACP-S1 was sent to backend for repair; independently recheck the exact repaired SHA before closing it.
