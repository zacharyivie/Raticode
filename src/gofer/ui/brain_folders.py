"""Folder rules: search a brain folder normally, list it after other results, or leave it out.

The rules live in the brain's settings (``folderRules``, a map from a folder path to
its rule), so they travel with the brain folder's ``.raticode/brain.json`` and with
packages. A rule covers the folder and its subfolders; a subfolder's own rule wins.
Files are never moved or changed.

"Leave out of search" uses the per-document ``excluded`` flag: the document gets
``excluded`` plus ``excludedBy`` naming the folder, so it isn't indexed or given to
Rem. A document the user left out in Edit metadata has no ``excludedBy`` and stays
left out when the folder rule changes.

"List after other results" changes only ranking: search moves those documents after
current ones, beside reports marked as replaced, and Rem sees status ``lowered``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

NORMAL = "normal"
LIST_AFTER = "list-after"
LEAVE_OUT = "leave-out"
RULES = (NORMAL, LIST_AFTER, LEAVE_OUT)
# One rule per folder; far more than any brain needs.
MAX_RULES = 1000
# Rem's search status for a document in a folder listed after other results.
LOWERED = "lowered"


def _folder(value: Any) -> str:
    text = value.strip().rstrip("/") if isinstance(value, str) else ""
    path = PurePosixPath(text)
    if (
        not text
        or len(text) > 1000
        or "\\" in text
        or "\x00" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in text.split("/"))
    ):
        raise ValueError("A folder rule needs a folder path inside the brain")
    return path.as_posix()


def checked_rules(value: Any) -> dict[str, str]:
    """Validate folder rules and return them with tidy folder paths."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_RULES:
        raise ValueError("Folder rules must map up to 1,000 folders to a rule")
    rules: dict[str, str] = {}
    for folder, rule in value.items():
        if rule not in RULES:
            raise ValueError(
                "Choose Search normally, List after other results or Leave out of search"
            )
        rules[_folder(folder)] = rule
    return dict(sorted(rules.items()))


def rule_for_folder(folder: str, rules: Mapping[str, str] | None) -> tuple[str, str] | None:
    """The nearest rule for a folder: its own, else its closest parent's, as (folder, rule)."""
    if not rules:
        return None
    parts = [part for part in str(folder).split("/") if part]
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end])
        if candidate in rules:
            return candidate, rules[candidate]
    return None


def rule_for(path: str, rules: Mapping[str, str] | None) -> tuple[str, str] | None:
    """The folder rule a document at ``path`` follows, or None when it is searched normally."""
    found = rule_for_folder(PurePosixPath(str(path)).parent.as_posix(), rules)
    return found if found and found[1] != NORMAL else None


def lowered_folder(path: str, rules: Mapping[str, str] | None) -> str:
    """The folder whose rule lists this document after other results, or ''."""
    found = rule_for(path, rules)
    return found[0] if found and found[1] == LIST_AFTER else ""


def left_out_folder(path: str, rules: Mapping[str, str] | None) -> str:
    """The folder whose rule leaves this document out of search, or ''."""
    found = rule_for(path, rules)
    return found[0] if found and found[1] == LEAVE_OUT else ""


def apply_rule(doc: dict[str, Any], rules: Mapping[str, str] | None) -> bool:
    """Set or clear a document's folder exclusion. Returns whether it changed.

    Missing documents are left as they are: they are already out of search.
    """
    if doc.get("missing"):
        return False
    before = (bool(doc.get("excluded")), doc.get("excludedBy"))
    folder = left_out_folder(str(doc.get("path", "")), rules)
    if folder:
        if not doc.get("excluded"):
            doc["excluded"] = True
            doc["excludedBy"] = folder
        elif doc.get("excludedBy"):
            # Still out by a folder rule, possibly a nearer one now.
            doc["excludedBy"] = folder
    elif doc.get("excludedBy"):
        doc["excluded"] = False
        doc.pop("excludedBy", None)
    return before != (bool(doc.get("excluded")), doc.get("excludedBy"))


def is_left_out(doc: Mapping[str, Any]) -> bool:
    """Left out of search (by a folder rule or in Edit metadata), not just missing."""
    return bool(doc.get("excluded")) and not doc.get("missing")
