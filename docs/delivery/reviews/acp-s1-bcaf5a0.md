# ACP-S1 repair review

Independent bounded recheck of bcaf5a0a87b30051153b857e0027b14ea2b84a3d. Reviewer assignment starts clean at c6a4fd562b6c9545358296224ffee8597b826295. Read root AGENTS.md; no nested instructions in the pinned tree. Backend worktree unchanged by this review.

ACP-S1 is closed at this pinned revision. No blocking findings in the session repair. At src/gofer/subscriptions/acp_session.py:107 the completed-response drain remembers the first invalid update and continues collecting valid chunks. At line 125 the recovery drain ignores further schema/session failures while preserving the original error. The new params type guard converts malformed parameters into the same structured failure handling.

Verification used a read-only Git archive snapshot in /tmp/acp-review-bcaf5a0/source. Python came from original M3b assignment 67c040ae2a284130906627e93c690557/.venv/bin/python, with PYTHONPATH=/tmp/acp-review-bcaf5a0/source/src. Both independent scripts assert that imported helpers resolve to this pinned snapshot, preventing accidental review of another checkout.

Commands and results, all exit 0:

- `python -m pytest -q tests/unit/test_acp_session.py tests/unit/test_acp_transport.py tests/unit/test_acp_config.py`: 73 passed in 6.16s. Log /tmp/acp-review-bcaf5a0/pytest.log.
- `python /tmp/acp-review-bcaf5a0/repro.py`: original subprocess reproduction with fixed-behavior assertions passes repeated malformed updates, EOF and malformed framing. Accepted body remains `first last`, one terminal error, no raw exception, child reaped. Log in adjacent repro.log.
- `python /tmp/acp-review-bcaf5a0/batch_repro.py`: six deterministic independent cases, pending/completed request crossed with first invalid text/foreign session/null params. Each queues further mixed invalid updates and valid text. Expected thought and terminal body `first second third`, original error retained, exactly one error terminal, no foreign text, request finished or cancelled. All pass; adjacent batch_repro.log.

The 803b2eb baseline reproduction previously exposed ACP-S1 despite 58 passing helper tests. The repaired tests now cover both response timing paths. No new dependencies or external core AI implementation reuse appears in the bounded session repair.

This is helper closure only. It does not accept M3b, provider permission enforcement, unwired Gemini/Grok adapters, or final integrated behavior. Required full-suite evidence and final swarm integration/review remain outstanding. No product code changed; Ruff/mypy/full suite were not rerun for this documentation-only review. No live provider calls or user workflows executed.
