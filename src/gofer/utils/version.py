"""Installed Raticode version lookup."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from gofer.utils.brand_compat import LEGACY_DISTRIBUTIONS

DISTRIBUTION = "raticode"
CLI_NAME = "Raticode"


def installed_version() -> str | None:
    """Return the installed distribution version, or None when running from source."""
    for name in (DISTRIBUTION, *LEGACY_DISTRIBUTIONS):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return None
