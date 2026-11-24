"""Checks for dates and times that people or models hand to Raticode."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def aware_instant(value: Any, label: str, *, future: bool = False) -> datetime:
    """[value] as an ISO 8601 date and time that names its timezone.

    [label] names the field in errors ("Memory expiry" gives "Memory expiry needs a
    timezone"). With [future], an instant that has already passed is refused too.
    Organization memories and Rem's memory end dates both use this check.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO 8601 date and time") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} needs a timezone")
    if future and parsed <= datetime.now(UTC):
        raise ValueError(f"Choose a {label.lower()} in the future")
    return parsed
