"""`Raticode rattish start --wait` waits for a run that isn't in the run list yet.

A background run can take a moment to show up in the backend's run list. The CLI used
to look it up once, right after starting it, and report its own new run as not found.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from gofer.cli.client import CliError, ExitCode
from gofer.cli.commands import workflows


class ListedLater:
    """A backend whose run list shows the run only from the ``after``-th request on."""

    def __init__(self, after: int, status: str = "success") -> None:
        self.after = after
        self.status = status
        self.requests = 0

    def request(self, method: str, path: str, **_: Any) -> dict[str, Any]:
        assert method == "GET" and path == "/workflows/wf/logs"
        self.requests += 1
        if self.requests < self.after:
            return {"runs": []}
        return {"runs": [{"id": "other"}, {"id": "run-1", "status": self.status}]}


def test_a_run_that_shows_up_late_is_waited_for() -> None:
    backend = ListedLater(after=3)
    state = workflows._wait_for_run(cast(Any, backend), None, "wf", "run-1", timeout=60, interval=0)
    assert state["status"] == "success"
    assert backend.requests == 3


def test_a_run_that_never_shows_up_is_not_found_after_the_grace_period() -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 5
        return now[0]

    backend = ListedLater(after=10**6)
    with pytest.raises(CliError) as raised:
        workflows._wait_for_run(
            cast(Any, backend), None, "wf", "run-1", timeout=600, interval=0, clock=clock
        )
    assert raised.value.exit_code == ExitCode.NOT_FOUND
    assert "Run 'run-1' was not found" in str(raised.value)


def test_a_failed_run_still_ends_with_its_exit_code() -> None:
    with pytest.raises(CliError) as raised:
        workflows._wait_for_run(
            cast(Any, ListedLater(after=2, status="error")),
            None,
            "wf",
            "run-1",
            timeout=60,
            interval=0,
        )
    assert raised.value.exit_code == ExitCode.FAILED


def test_waiting_on_an_existing_run_id_that_isnt_listed_fails_at_once() -> None:
    backend = ListedLater(after=10**6)
    with pytest.raises(CliError) as raised:
        workflows._wait_for_run(
            cast(Any, backend), None, "wf", "run-1", timeout=600, interval=0, grace=0
        )
    assert raised.value.exit_code == ExitCode.NOT_FOUND
    assert backend.requests == 1
