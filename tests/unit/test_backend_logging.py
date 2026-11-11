"""The backend's log lines that the desktop app files by level."""

from __future__ import annotations

import json
import logging
import sys
import threading
import warnings

import pytest

from gofer.utils import logging as backend_logging
from gofer.utils.logging import (
    DESKTOP_LOG_PREFIX,
    DesktopLogFormatter,
    configure_backend_logging,
    reset_backend_logging,
)


def _record(level: int, message: str, *args: object, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        "gofer.devices.service",
        level,
        __file__,
        1,
        message,
        args,
        exc_info,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("level", "name"),
    [
        (logging.DEBUG, "debug"),
        (logging.INFO, "info"),
        (logging.WARNING, "warn"),
        (logging.ERROR, "error"),
        (logging.CRITICAL, "error"),
    ],
)
def test_each_record_is_one_prefixed_json_line(level, name):
    line = DesktopLogFormatter().format(_record(level, "Phone %s connected", "A"))
    assert line.startswith(DESKTOP_LOG_PREFIX)
    assert "\n" not in line
    payload = json.loads(line[len(DESKTOP_LOG_PREFIX) :])
    assert payload == {
        "level": name,
        "logger": "gofer.devices.service",
        "message": "Phone A connected",
    }


def test_a_traceback_stays_inside_its_entry_and_huge_messages_are_cut():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = _record(logging.ERROR, "Worker failed", exc_info=sys.exc_info())
    line = DesktopLogFormatter().format(record)
    message = json.loads(line[len(DESKTOP_LOG_PREFIX) :])["message"]
    assert message.startswith("Worker failed\nTraceback")
    assert "RuntimeError: boom" in message
    big = DesktopLogFormatter().format(_record(logging.INFO, "x" * 50_000))
    assert json.loads(big[len(DESKTOP_LOG_PREFIX) :])["message"].endswith("[…truncated]")


def test_a_bad_format_string_still_produces_an_entry():
    line = DesktopLogFormatter().format(_record(logging.WARNING, "%d phones", "two"))
    assert json.loads(line[len(DESKTOP_LOG_PREFIX) :])["message"] == "%d phones"


class _Stderr:
    """The test's captured stderr. pytest swaps sys.stderr per phase, so use capsys."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys
        self.text = ""

    def getvalue(self) -> str:
        self.text += self.capsys.readouterr().err
        return self.text


@pytest.fixture
def stderr(capsys):
    reset_backend_logging()
    yield _Stderr(capsys)
    reset_backend_logging()


def _lines(stream: _Stderr) -> list[dict[str, str]]:
    return [
        json.loads(line[len(DESKTOP_LOG_PREFIX) :])
        for line in stream.getvalue().splitlines()
        if line.startswith(DESKTOP_LOG_PREFIX)
    ]


def test_desktop_format_keeps_raticode_detail_and_quiets_libraries(stderr):
    configure_backend_logging({"GOFER_LOG_FORMAT": "desktop", "GOFER_LOG_LEVEL": "debug"})
    logging.getLogger("gofer.devices.service").debug("debug detail")
    logging.getLogger("gofer.ui.server").info("info detail")
    logging.getLogger("urllib3").info("library chatter")
    logging.getLogger("asyncio").warning("library warning")
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("deprecated thing", DeprecationWarning, stacklevel=1)
    entries = _lines(stderr)
    assert [(e["level"], e["logger"]) for e in entries[:3]] == [
        ("debug", "gofer.devices.service"),
        ("info", "gofer.ui.server"),
        ("warn", "asyncio"),
    ]
    assert entries[3]["logger"] == "py.warnings"
    assert "deprecated thing" in entries[3]["message"]
    assert "library chatter" not in stderr.getvalue()


def test_default_level_is_info_and_terminal_format_is_plain(stderr):
    configure_backend_logging({})
    logging.getLogger("gofer.x").debug("hidden")
    logging.getLogger("gofer.x").info("shown")
    output = stderr.getvalue()
    assert "hidden" not in output
    assert "INFO gofer.x: shown" in output
    assert DESKTOP_LOG_PREFIX not in output


def test_configuring_twice_adds_one_handler_and_reset_removes_it(stderr):
    root = logging.getLogger()
    before = list(root.handlers)
    first = configure_backend_logging({"GOFER_LOG_FORMAT": "desktop"})
    assert configure_backend_logging({"GOFER_LOG_FORMAT": "desktop"}) is first
    assert root.handlers == [*before, first]
    reset_backend_logging()
    assert root.handlers == before
    assert threading.excepthook is threading.__excepthook__
    assert sys.excepthook is sys.__excepthook__


def test_an_uncaught_thread_error_becomes_an_error_entry(stderr):
    configure_backend_logging({"GOFER_LOG_FORMAT": "desktop"})

    def fail() -> None:
        raise ValueError("thread broke")

    worker = threading.Thread(target=fail, name="device-service")
    worker.start()
    worker.join()
    [entry] = [e for e in _lines(stderr) if e["logger"] == "gofer.crash"]
    assert entry["level"] == "error"
    assert entry["message"].startswith("Uncaught error in background thread device-service")
    assert "ValueError: thread broke" in entry["message"]


def test_an_uncaught_main_thread_error_is_logged_as_an_error(stderr):
    configure_backend_logging({"GOFER_LOG_FORMAT": "desktop"})
    try:
        raise KeyError("lost")
    except KeyError:
        kind, value, traceback = sys.exc_info()
    assert kind is not None and value is not None
    backend_logging._main_exception(kind, value, traceback)
    [entry] = [e for e in _lines(stderr) if e["logger"] == "gofer.crash"]
    assert entry["level"] == "error"
    assert "KeyError: 'lost'" in entry["message"]


def test_a_repeating_failure_is_logged_once_and_its_recovery_once(caplog):
    from gofer.utils.logging import RepeatingFailure

    logger = logging.getLogger("gofer.test.loop")
    caplog.set_level(logging.DEBUG, logger="gofer")
    failure = RepeatingFailure(logger, "Background check")
    for _ in range(3):
        failure.failed(OSError("disk is locked"))
    failure.failed(ValueError("bad row"))
    failure.succeeded()
    failure.succeeded()
    failure.failed(OSError("disk is locked"))
    records = [(r.levelno, r.getMessage(), bool(r.exc_info)) for r in caplog.records]
    assert records == [
        (logging.ERROR, "Background check failed", True),
        (logging.DEBUG, "Background check still failing (OSError)", False),
        (logging.DEBUG, "Background check still failing (OSError)", False),
        (logging.ERROR, "Background check failed", True),
        (logging.INFO, "Background check recovered", False),
        (logging.ERROR, "Background check failed", True),
    ]
