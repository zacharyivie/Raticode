"""Checks for what an open-resource node hands to the operating system's opener.

Targets are often templated from trigger payloads, loop rows or earlier node output, so
someone other than the workflow's author may have written them. ``xdg-open`` and
``open`` read a leading ``-`` as an option, a URL can name any registered protocol
handler, and ``cmd /c start`` parses ``&`` and ``|`` as more commands (CWE-88).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

URL_SCHEMES = frozenset({"http", "https", "mailto"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def checked_open_target(target: str, resource_type: str) -> str:
    """Return ``target`` if it is safe to pass to the platform opener, else raise."""
    if resource_type == "app":
        return target
    if _CONTROL.search(target):
        raise ValueError("Open target can't contain control characters")
    if target.startswith("-"):
        raise ValueError("Open target can't start with '-'")
    if resource_type == "url":
        scheme = urlsplit(target).scheme.lower()
        if scheme not in URL_SCHEMES:
            raise ValueError(
                "Only http, https and mailto addresses open as URLs; "
                "open a local file with resource-type file or folder"
            )
    return target
