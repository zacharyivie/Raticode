"""Unexpected failures in API handlers are answered and logged; user errors stay quiet."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from gofer.ui.server import GoferUiRequestHandler
from tests.unit.test_ui_server import _request


def _break(monkeypatch: pytest.MonkeyPatch, error: BaseException, *, after_response: bool = False):
    def dispatch(self: GoferUiRequestHandler) -> None:
        if after_response:
            self._send_json({"partial": True})
        raise error

    monkeypatch.setattr(GoferUiRequestHandler, "_dispatch_GET", dispatch)


def test_an_unexpected_error_gets_a_500_and_a_logged_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG, logger="gofer")
    _break(monkeypatch, KeyError("missing-key"))
    result = _request(tmp_path, "GET", "/api/workflows?token=query-secret")
    assert result.status == 500
    assert "Settings > Developer" in json.loads(result.body)["error"]
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert record.getMessage() == "Unexpected error answering GET /api/workflows"
    assert record.exc_info and record.exc_info[0] is KeyError
    assert "query-secret" not in record.getMessage(), "the query string is not logged"


def test_a_rejected_request_is_a_quiet_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG, logger="gofer")
    _break(monkeypatch, ValueError("title 'my private plans' is too long"))
    result = _request(tmp_path, "GET", "/api/workflows")
    assert result.status == 400
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "gofer.ui.server"] == [
        (logging.DEBUG, "Rejected GET /api/workflows with 400 (ValueError)")
    ]


def test_an_error_after_the_response_started_is_logged_without_a_second_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG, logger="gofer")
    _break(monkeypatch, RuntimeError("late failure"), after_response=True)
    result = _request(tmp_path, "GET", "/api/workflows")
    assert result.status == 200
    assert json.loads(result.body) == {"partial": True}
    assert [r.levelno for r in caplog.records if r.name == "gofer.ui.server"] == [logging.ERROR]


def test_a_client_that_disconnects_is_not_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG, logger="gofer")
    _break(monkeypatch, BrokenPipeError())
    with pytest.raises(BrokenPipeError):  # handle() ends these quietly in the real server.
        _request(tmp_path, "GET", "/api/workflows")
    assert not [r for r in caplog.records if r.name == "gofer.ui.server"]


@pytest.mark.parametrize(
    ("error", "level"),
    [
        (PermissionError(13, "Permission denied"), logging.WARNING),
        (ValueError("bad model"), logging.DEBUG),
    ],
)
def test_failed_settings_saves_are_logged_only_when_not_the_requests_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    level: int,
):
    from gofer.ui import server as server_module

    def save(_provider: str, _body: dict[str, object]) -> None:
        raise error

    monkeypatch.setattr(server_module, "save_provider_preference", save)
    caplog.set_level(logging.DEBUG, logger="gofer")
    result = _request(tmp_path, "POST", "/api/provider/settings", body={"provider": "codex"})
    assert result.status == 400
    [record] = [r for r in caplog.records if r.name == "gofer.ui.server"]
    assert record.levelno == level
    assert record.getMessage().startswith("Saving provider settings")
    assert (record.exc_info is not None) == (level == logging.WARNING)
