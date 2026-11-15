"""Trigger defaults the CLI shows in its options.

They live apart from ``gofer.rattish.triggers`` so building the command line
does not load the cron parser and the Rattish compiler.
"""

from __future__ import annotations

DEFAULT_OVERLAP = "skip"
DEFAULT_MISSED = "record"
DEFAULT_DEBOUNCE_SECONDS = 2.0
