"""Trigger defaults the CLI shows in its options.

They live apart from ``gofer.rattish.triggers`` so building the command line
does not load the cron parser and the Rattish compiler.
"""

from __future__ import annotations

DEFAULT_OVERLAP = "skip"
DEFAULT_MISSED = "record"
DEFAULT_DEBOUNCE_SECONDS = 2.0
# A triggered run's Time limit. Triggers saved before it existed get it too.
DEFAULT_MAX_RUN_SECONDS = 6 * 3600
MAX_RUN_SECONDS_CEILING = 30 * 86400
_RUN_TIME_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}
NO_RUN_TIME_WORDS = frozenset({"none", "off", "no", "never", "0"})


def parse_run_time(text: str) -> int | None:
    """Read a Time limit such as ``2h``, ``30m``, ``1h30m`` or ``none`` as whole seconds.

    None means no limit. A bare number is refused, because ``30`` could mean seconds or
    minutes. Raises ValueError with a sentence for the user.
    """
    import re

    value = text.strip().lower()
    if value in NO_RUN_TIME_WORDS:
        return None
    pattern = r"(\d+(?:\.\d+)?)\s*([a-z]+)"
    parts = re.findall(pattern, value)
    if not parts or re.sub(pattern, "", value).strip():
        raise ValueError(
            f"Time limit {text!r} isn't a duration. Use forms like 90s, 30m, 2h or 1h30m, "
            "or none for no limit."
        )
    seconds = 0.0
    for number, unit in parts:
        if unit not in _RUN_TIME_UNITS:
            raise ValueError(f"Time limit {text!r} has an unknown unit {unit!r}. Use s, m, h or d.")
        seconds += float(number) * _RUN_TIME_UNITS[unit]
    return round(seconds) if seconds else None


def format_run_time(seconds: float) -> str:
    """A length of time as people read it: ``45 s``, ``12 min``, ``6 h`` or ``7 h 12 min``."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole} s"
    if whole < 3600:
        minutes, rest = divmod(whole, 60)
        return f"{minutes} min" + (f" {rest} s" if rest else "")
    hours, rest = divmod(whole, 3600)
    return f"{hours} h" + (f" {rest // 60} min" if rest // 60 else "")
