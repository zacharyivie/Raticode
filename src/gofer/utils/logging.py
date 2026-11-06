from __future__ import annotations

import json
import logging
import os
import sys
import threading
from types import TracebackType
from typing import Any

_configured = False

# The desktop app reads lines with this prefix from the backend's stderr and shows
# them in Settings > Developer with their level. Every record is one JSON line.
DESKTOP_LOG_PREFIX = "GOFER_LOG "
# A traceback is useful; a runaway repr of a large object is not.
MAX_DESKTOP_MESSAGE = 16_000
_LEVEL_NAMES = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warn"}
_desktop_handler: logging.Handler | None = None


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def desktop_level_name(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    return _LEVEL_NAMES.get(levelno, "debug" if levelno < logging.INFO else "warn")


class DesktopLogFormatter(logging.Formatter):
    """One JSON line per record, so multi-line tracebacks stay one log entry."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not lose the record
            message = str(record.msg)
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        elif record.exc_text:
            message = f"{message}\n{record.exc_text}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        if len(message) > MAX_DESKTOP_MESSAGE:
            message = message[:MAX_DESKTOP_MESSAGE] + " […truncated]"
        payload = {
            "level": desktop_level_name(record.levelno),
            "logger": record.name,
            "message": message,
        }
        return DESKTOP_LOG_PREFIX + json.dumps(payload, ensure_ascii=False)


def _thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "unknown"
    logging.getLogger("gofer.crash").error(
        "Uncaught error in background thread %s",
        name,
        exc_info=(args.exc_type, args.exc_value or args.exc_type(), args.exc_traceback),
    )


def _main_exception(
    kind: type[BaseException], value: BaseException, traceback: TracebackType | None
) -> None:
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, value, traceback)
        return
    logging.getLogger("gofer.crash").critical(
        "Uncaught error stopped the backend", exc_info=(kind, value, traceback)
    )


def configure_backend_logging(env: dict[str, str] | None = None) -> logging.Handler | None:
    """Send backend log records to stderr for the desktop app or a terminal.

    The desktop app sets ``GOFER_LOG_FORMAT=desktop`` and gets JSON lines it can
    file by level. A terminal gets plain lines. ``GOFER_LOG_LEVEL`` picks how much of
    Raticode's own logging is kept (default ``info``); other libraries only report
    warnings and errors, so their routine chatter does not crowd the log.
    """
    global _desktop_handler
    source: Any = os.environ if env is None else env
    if _desktop_handler is not None:
        return _desktop_handler
    structured = source.get("GOFER_LOG_FORMAT", "").strip().lower() == "desktop"
    wanted = source.get("GOFER_LOG_LEVEL", "info").strip().upper()
    if wanted == "WARN":
        wanted = "WARNING"
    level = getattr(logging, wanted, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        DesktopLogFormatter()
        if structured
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    logging.getLogger("gofer").setLevel(level)
    logging.captureWarnings(True)
    threading.excepthook = _thread_exception
    sys.excepthook = _main_exception
    _desktop_handler = handler
    return handler


def reset_backend_logging() -> None:
    """Remove the handler installed by ``configure_backend_logging`` (tests)."""
    global _desktop_handler
    if _desktop_handler is not None:
        logging.getLogger().removeHandler(_desktop_handler)
        _desktop_handler = None
    logging.getLogger("gofer").setLevel(logging.NOTSET)
    logging.captureWarnings(False)
    threading.excepthook = threading.__excepthook__
    sys.excepthook = sys.__excepthook__
